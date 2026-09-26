"""Ableton Live Set (.als) export.

Builds a Live 12 set from Live's own DefaultLiveSet.als template:
  - tempo, time signature, and a locator at every section
  - one MIDI track per part (drums as one Drum Rack-mapped track), a clip spanning the song
  - one audio track per stem, bar-1 aligned, with warp markers on every bar taken from the
    beat grid, so reference audio follows the grid even when the recording drifts
Tracks are cloned from the template's MIDI/audio tracks; every "pointee" id (automation and
modulation targets) is renumbered so the document stays consistent.
"""
import copy
import gzip
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import system
from ..config import DATA, SR

CACHED_TEMPLATE = DATA / "DefaultLiveSet.als"

POINTEE_TAGS = {"AutomationTarget", "ModulationTarget", "Pointee", "VolumeModulationTarget",
                "TranspositionModulationTarget", "TransientEnvelopeModulationTarget", "GrainSizeModulationTarget",
                "FluxModulationTarget", "SampleOffsetModulationTarget", "ComplexProFormantsModulationTarget",
                "ComplexProEnvelopeModulationTarget"}
# Live colour-palette indices, roughly matching the app's group colours
GROUP_COLOR = {"drums": 13, "bass": 2, "harmony": 18, "melody": 4, "vocals": 0, "fx": 20}


def _valid_template(p: Path) -> bool:
    try:
        root = ET.fromstring(gzip.decompress(p.read_bytes()))
        return root.find("LiveSet/Tracks/MidiTrack") is not None and root.find("LiveSet/Tracks/AudioTrack") is not None
    except Exception:
        return False


def template_path() -> Path | None:
    """Live's DefaultLiveSet.als: TRACK_ANATOMY_ABLETON_TEMPLATE, else the newest installed Live,
    copied into the data folder on first use (Live's files are never redistributed)."""
    env = os.environ.get("TRACK_ANATOMY_ABLETON_TEMPLATE")
    if env and _valid_template(Path(env)):
        return Path(env)
    if CACHED_TEMPLATE.exists():
        return CACHED_TEMPLATE
    for cand in reversed(system.ableton_template_candidates()):     # newest version first
        p = Path(cand)
        if _valid_template(p):
            CACHED_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, CACHED_TEMPLATE)
            return CACHED_TEMPLATE
    return None


def available() -> bool:
    return template_path() is not None


def _fmt(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _sub(parent: ET.Element, tag: str, value=None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if value is not None:
        el.set("Value", str(value))
    return el


class _Ids:
    def __init__(self, start: int):
        self.n = start

    def next(self) -> int:
        self.n += 1
        return self.n


def _renumber(el: ET.Element, ids: _Ids) -> None:
    for e in el.iter():
        if e.tag in POINTEE_TAGS or e.tag.startswith("ControllerTargets."):
            if "Id" in e.attrib:
                e.set("Id", str(ids.next()))


def _set_name(track: ET.Element, name: str) -> None:
    n = track.find("Name")
    n.find("EffectiveName").set("Value", name)
    n.find("UserName").set("Value", name)


def _clip_common(clip: ET.Element, start: float, length: float, name: str, color: int, meter: int, warped: bool) -> None:
    _sub(clip, "LomId", 0)
    _sub(clip, "LomIdView", 0)
    _sub(clip, "CurrentStart", _fmt(start))
    _sub(clip, "CurrentEnd", _fmt(start + length))
    loop = _sub(clip, "Loop")
    _sub(loop, "LoopStart", 0)
    _sub(loop, "LoopEnd", _fmt(length))
    _sub(loop, "StartRelative", 0)
    _sub(loop, "LoopOn", "false")
    _sub(loop, "OutMarker", _fmt(length))
    _sub(loop, "HiddenLoopStart", 0)
    _sub(loop, "HiddenLoopEnd", _fmt(length))
    _sub(clip, "Name", name)
    _sub(clip, "Annotation", "")
    _sub(clip, "Color", color)
    _sub(clip, "LaunchMode", 0)
    _sub(clip, "LaunchQuantisation", 0)
    ts = _sub(_sub(clip, "TimeSignature"), "TimeSignatures")
    rts = _sub(ts, "RemoteableTimeSignature", Id=0)
    _sub(rts, "Numerator", meter)
    _sub(rts, "Denominator", 4)
    _sub(rts, "Time", 0)
    _sub(_sub(clip, "Envelopes"), "Envelopes")
    stp = _sub(clip, "ScrollerTimePreserver")
    _sub(stp, "LeftTime", 0)
    _sub(stp, "RightTime", 0)
    tsel = _sub(clip, "TimeSelection")
    _sub(tsel, "AnchorTime", 0)
    _sub(tsel, "OtherTime", 0)
    _sub(clip, "Legato", "false")
    _sub(clip, "Ram", "false")
    _sub(_sub(clip, "GrooveSettings"), "GrooveId", -1)
    _sub(clip, "Disabled", "false")
    _sub(clip, "VelocityAmount", 0)
    fa = _sub(clip, "FollowAction")
    for k, v in (("FollowTime", 4), ("IsLinked", "true"), ("LoopIterations", 1), ("FollowActionA", 4),
                 ("FollowActionB", 0), ("FollowChanceA", 100), ("FollowChanceB", 0), ("JumpIndexA", 1),
                 ("JumpIndexB", 1), ("FollowActionEnabled", "false")):
        _sub(fa, k, v)
    grid = _sub(clip, "Grid")
    for k, v in (("FixedNumerator", 1), ("FixedDenominator", 16), ("GridIntervalPixel", 20), ("Ntoles", 2),
                 ("SnapToGrid", "true"), ("Fixed", "false")):
        _sub(grid, k, v)
    _sub(clip, "FreezeStart", 0)
    _sub(clip, "FreezeEnd", 0)
    _sub(clip, "IsWarped", "true" if warped else "false")
    _sub(clip, "TakeId", 1)
    _sub(clip, "IsInKey", "false")
    si = _sub(clip, "ScaleInformation")
    _sub(si, "Root", 0)
    _sub(si, "Name", 0)
    ET.SubElement(clip, "AutomationEnvelopesListWrapper", {"LomId": "0"})


def midi_clip(notes: list[tuple[float, float, int, int]], length: float, name: str, color: int, meter: int) -> ET.Element:
    """notes: (start_beat, duration_beats, pitch, velocity)."""
    clip = ET.Element("MidiClip", {"Id": "0", "Time": "0"})
    _clip_common(clip, 0.0, length, name, color, meter, True)
    nel = _sub(clip, "Notes")
    kts = _sub(nel, "KeyTracks")
    by_key: dict[int, list] = {}
    for n in notes:
        by_key.setdefault(int(n[2]), []).append(n)
    nid = 0
    for i, key in enumerate(sorted(by_key)):
        kt = _sub(kts, "KeyTrack", Id=i)
        ns = _sub(kt, "Notes")
        for s, d, _p, v in sorted(by_key[key]):
            nid += 1
            ET.SubElement(ns, "MidiNoteEvent", {"Time": _fmt(s), "Duration": _fmt(max(d, 1 / 64)),
                                                "Velocity": str(int(np.clip(v, 1, 127))), "OffVelocity": "64",
                                                "NoteId": str(nid)})
        _sub(kt, "MidiKey", key)
    _sub(_sub(nel, "PerNoteEventStore"), "EventLists")
    _sub(nel, "NoteProbabilityGroups")
    _sub(_sub(nel, "ProbabilityGroupIdGenerator"), "NextId", 1)
    _sub(_sub(nel, "NoteIdGenerator"), "NextId", nid + 1)
    for k, v in (("BankSelectCoarse", -1), ("BankSelectFine", -1), ("ProgramChange", -1),
                 ("NoteEditorFoldInZoom", -1), ("NoteEditorFoldInScroll", 0), ("NoteEditorFoldOutZoom", -1),
                 ("NoteEditorFoldOutScroll", 0), ("NoteEditorFoldScaleZoom", -1), ("NoteEditorFoldScaleScroll", 0),
                 ("NoteSpellingPreference", 0), ("AccidentalSpellingPreference", 3), ("PreferFlatRootNote", "false")):
        _sub(clip, k, v)
    eg = _sub(clip, "ExpressionGrid")
    for k, v in (("FixedNumerator", 1), ("FixedDenominator", 16), ("GridIntervalPixel", 20), ("Ntoles", 2),
                 ("SnapToGrid", "false"), ("Fixed", "false")):
        _sub(eg, k, v)
    return clip


def audio_clip(rel_path: str, abs_path: str, n_samples: int, file_size: int, length: float, name: str, color: int,
               meter: int, markers: list[tuple[float, float]], warp_mode: int, ids: _Ids) -> ET.Element:
    clip = ET.Element("AudioClip", {"Id": "0", "Time": "0"})
    _clip_common(clip, 0.0, length, name, color, meter, True)
    sr = _sub(clip, "SampleRef")
    fr = _sub(sr, "FileRef")
    _sub(fr, "RelativePathType", 3)
    _sub(fr, "RelativePath", rel_path)
    _sub(fr, "Path", abs_path)
    _sub(fr, "Type", 1)
    _sub(fr, "LivePackName", "")
    _sub(fr, "LivePackId", "")
    _sub(fr, "OriginalFileSize", file_size)
    _sub(fr, "OriginalCrc", 0)
    _sub(fr, "SourceHint", "")
    _sub(sr, "LastModDate", 0)
    _sub(sr, "SourceContext")
    _sub(sr, "SampleUsageHint", 0)
    _sub(sr, "DefaultDuration", n_samples)
    _sub(sr, "DefaultSampleRate", SR)
    _sub(sr, "SamplesToAutoWarp", 0)
    on = _sub(clip, "Onsets")
    _sub(on, "UserOnsets")
    _sub(on, "HasUserOnsets", "false")
    for k, v in (("WarpMode", warp_mode), ("GranularityTones", 30), ("GranularityTexture", 65),
                 ("FluctuationTexture", 25), ("TransientResolution", 6), ("TransientLoopMode", 2),
                 ("TransientEnvelope", 100), ("ComplexProFormants", 100), ("ComplexProEnvelope", 128),
                 ("Sync", "true"), ("HiQ", "true"), ("Fade", "false")):
        _sub(clip, k, v)
    fades = _sub(clip, "Fades")
    for k, v in (("FadeInLength", 0), ("FadeOutLength", 0), ("ClipFadesAreInitialized", "true"),
                 ("CrossfadeInState", 0), ("FadeInCurveSkew", 0), ("FadeInCurveSlope", 0), ("FadeOutCurveSkew", 0),
                 ("FadeOutCurveSlope", 0), ("IsDefaultFadeIn", "false"), ("IsDefaultFadeOut", "false")):
        _sub(fades, k, v)
    _sub(clip, "PitchCoarse", 0)
    _sub(clip, "PitchFine", 0)
    _sub(clip, "SampleVolume", 1)
    wm = _sub(clip, "WarpMarkers")
    for sec, beat in markers:
        ET.SubElement(wm, "WarpMarker", {"Id": str(ids.next()), "SecTime": _fmt(sec), "BeatTime": _fmt(beat)})
    _sub(clip, "SavedWarpMarkersForStretched")
    _sub(clip, "MarkersGenerated", "false")
    _sub(clip, "IsSongTempoLeader", "false")
    return clip


def _add_groove(ls: ET.Element, A: dict) -> None:
    """Add the track's measured groove to the Groove Pool (cloned from the template's groove)."""
    g = (A.get("drums") or {}).get("groove")
    grooves = ls.find("GroovePool/Grooves")
    if not g or grooves is None or len(grooves) == 0:
        return
    new = copy.deepcopy(grooves[0])
    new.set("Id", str(max(int(x.get("Id", 0)) for x in grooves) + 1))
    sw = (A.get("drums") or {}).get("swing") or {}
    name = f"Track Anatomy groove ({round(sw['percent'])}% {sw['grid']})" if sw.get("percent") else "Track Anatomy groove"
    new.find("Name").set("Value", name)
    clip = new.find("Clip/Value/MidiClip")
    clip.find("Name").set("Value", name)
    meter = A["global"]["meter"]
    for tag in ("CurrentEnd",):
        clip.find(tag).set("Value", _fmt(meter))
    loop = clip.find("Loop")
    for tag in ("LoopEnd", "OutMarker"):
        loop.find(tag).set("Value", _fmt(meter))
    kts = clip.find("Notes/KeyTracks")
    for c in list(kts):
        kts.remove(c)
    kt = _sub(kts, "KeyTrack", Id=0)
    ns = _sub(kt, "Notes")
    S = g["steps_per_beat"]
    for i in range(g["steps"]):
        t = i / S + g["offsets"][i]
        ET.SubElement(ns, "MidiNoteEvent", {"Time": _fmt(max(0.0, t)), "Duration": _fmt(0.5 / S),
                                            "Velocity": str(g["velocities"][i]), "OffVelocity": "64",
                                            "NoteId": str(i + 1)})
    _sub(kt, "MidiKey", 60)
    nid = clip.find("Notes/NoteIdGenerator/NextId")
    if nid is not None:
        nid.set("Value", str(g["steps"] + 1))
    grid_el = new.find("Grid")
    if grid_el is not None:
        grid_el.set("Value", {4: "3", 3: "2", 6: "4"}.get(S, "3"))   # groove base: 1/16, 1/8T, 1/16T
    grooves.append(new)


def warp_markers(A: dict, n_samples: int) -> list[tuple[float, float]]:
    """(seconds in the aligned file, beat) pairs: every bar for drifting grids, ends for steady ones."""
    beats = np.array(A["grid"]["beats"])
    first = A["grid"]["first_beat"]
    origin = A["global"]["origin_time"]
    meter = A["global"]["meter"]
    dur = n_samples / SR
    pts = []
    for i, t in enumerate(beats):
        b = i + first
        if b < 0 or b % meter:
            continue
        sec = float(t - origin)          # aligned files start at bar 1, so bar 1 is 0 s
        if 0 <= sec <= dur:
            pts.append((sec, float(b)))
    if A["global"]["steady"] and len(pts) > 2:
        pts = [pts[0], pts[-1]]
    if pts and pts[0][1] == 0.0:
        pts[0] = (0.0, 0.0)
    else:
        pts.insert(0, (0.0, 0.0))
    return pts


def build(A: dict, track_dir: Path, out_dir: Path, abs_root: str = "") -> Path:
    """Write <out_dir>/<title>.als plus Samples/Imported/*.wav and return the .als path.

    abs_root: absolute (Windows) path of out_dir if known, for exact sample paths.
    """
    from .export import aligned_stem
    tpl = template_path()
    if tpl is None:
        raise RuntimeError("Ableton Live's DefaultLiveSet.als template was not found.")
    root = ET.fromstring(gzip.decompress(tpl.read_bytes()))
    ls = root.find("LiveSet")
    tracks = ls.find("Tracks")
    proto_midi = copy.deepcopy(tracks.find("MidiTrack"))
    proto_audio = copy.deepcopy(tracks.find("AudioTrack"))
    for t in list(tracks):
        if t.tag in ("MidiTrack", "AudioTrack"):
            tracks.remove(t)
    returns = [t for t in tracks]
    for t in returns:
        tracks.remove(t)

    ids = _Ids(max(int(e.get("Id")) for e in root.iter() if e.tag in POINTEE_TAGS and e.get("Id", "").isdigit()) + 1000)
    g = A["global"]
    meter, bpm = g["meter"], g["bpm"]
    song_beats = float(g["n_bars"] * meter)
    track_id = 1000
    new_tracks = []

    # ---------------------------------------------------------------- MIDI tracks
    drum_notes = []
    for e in A["elements"]:
        if e.get("hits"):
            drum_notes += [(h[0], 0.25, h[2], h[1]) for h in e["hits"] if h[0] >= 0]
    midi_parts = []
    if drum_notes:
        midi_parts.append(("Drums (MIDI)", "drums", drum_notes))
    for e in A["elements"]:
        if e.get("notes"):
            midi_parts.append((f"{e['name']} (MIDI)", e["group"], [(n[0], n[1] - n[0], n[2], n[3]) for n in e["notes"] if n[0] >= 0]))
    ch_notes = []
    for c in A.get("chords", []):
        if c.get("root") is None:
            continue
        from .harmony import QUAL
        base = 60 + c["root"] - (12 if c["root"] > 5 else 0)
        for iv in QUAL[c["quality"]][0]:
            ch_notes.append((c["b0"], c["b1"] - c["b0"], base + iv, 80))
    if ch_notes:
        midi_parts.append(("Chords (MIDI)", "harmony", ch_notes))
    for name, group, notes in midi_parts:
        t = copy.deepcopy(proto_midi)
        track_id += 1
        t.set("Id", str(track_id))
        _set_name(t, name)
        t.find("Color").set("Value", str(GROUP_COLOR.get(group, 1)))
        _renumber(t, ids)
        ev = t.find("DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events")
        ev.append(midi_clip(notes, song_beats, name.replace(" (MIDI)", ""), GROUP_COLOR.get(group, 1), meter))
        new_tracks.append(t)

    # ---------------------------------------------------------------- audio (reference stems)
    samples = out_dir / "Samples" / "Imported"
    samples.mkdir(parents=True, exist_ok=True)
    for e in A["elements"]:
        src = track_dir / e["audio"]
        if not src.exists():
            continue
        fname = f"{e['id']}.wav"
        dst = samples / fname
        aligned_stem(src, dst, g["origin_time"], A["track"]["gain"])
        n = sf.info(str(dst)).frames
        rel = f"Samples/Imported/{fname}"
        absp = f"{abs_root.rstrip('/')}/{rel}" if abs_root else ""
        t = copy.deepcopy(proto_audio)
        track_id += 1
        t.set("Id", str(track_id))
        name = f"{e['name']} (stem)"
        _set_name(t, name)
        color = GROUP_COLOR.get(e["group"], 1)
        t.find("Color").set("Value", str(color))
        _renumber(t, ids)
        ev = t.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        length = min(song_beats, float(np.floor(float(np.interp(n / SR + g["origin_time"], A["grid"]["beats"],
                                                                     np.arange(len(A["grid"]["beats"])) + A["grid"]["first_beat"])))))
        clip = audio_clip(rel, absp, n, dst.stat().st_size, max(length, 1.0), e["name"], color, meter,
                          warp_markers(A, n), 0 if e["group"] == "drums" else 4, ids)
        ev.append(clip)
        new_tracks.append(t)

    for t in new_tracks + returns:
        tracks.append(t)

    _add_groove(ls, A)

    # ---------------------------------------------------------------- tempo, meter, locators
    main = ls.find("MainTrack")
    main.find("DeviceChain/Mixer/Tempo/Manual").set("Value", _fmt(bpm))
    ts = main.find("DeviceChain/Mixer/TimeSignature/Manual")
    if ts is not None:
        ts.set("Value", str((meter - 1) + 99 * 2))
    locs = ls.find("Locators/Locators")
    for c in list(locs):
        locs.remove(c)
    for i, s in enumerate(A["sections"]):
        loc = _sub(locs, "Locator", Id=i)
        _sub(loc, "LomId", 0)
        _sub(loc, "Time", _fmt(s["b0"] * meter))
        _sub(loc, "Name", s["label"])
        _sub(loc, "Annotation", f"bars {s['b0'] + 1}-{s['b1']}")
        _sub(loc, "IsSongStart", "false")
    ls.find("NextPointeeId").set("Value", str(ids.next() + 1))

    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    from .export import safe_name
    out = out_dir / f"{safe_name(A['track']['title'])} (Track Anatomy).als"
    out.write_bytes(gzip.compress(xml))
    return out
