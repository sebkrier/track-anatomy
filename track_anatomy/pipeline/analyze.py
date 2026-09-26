"""Grid-dependent analysis: turns cached stems, beats and notes into analysis.json + MIDI.

Memory: stems are read from disk when needed and released straight after, so a long track
never has more than a couple of stems in RAM at once.
"""
import time
from pathlib import Path

import numpy as np

from ..config import DRUM_PIECES, PIPELINE_VERSION, SR
from ..jsonio import read_json, write_json
from . import audio
from . import drums as DR
from . import export as EX
from . import fx as FX
from . import grid as G
from . import harmony as H
from . import notes as N
from . import roles as R
from . import sound as S
from . import structure as ST
from . import suggest as SG

PITCHED = ["bass", "vocals", "piano", "guitar", "other"]
GROUP_ORDER = ["drums", "bass", "harmony", "melody", "vocals", "fx"]
NAMES = {
    "other": {"pad": "Synth pad / chords", "stab": "Synth stabs", "lead": "Synth lead", "arp": "Arp / sequence"},
    "piano": {"pad": "Keys - chords", "stab": "Keys - stabs", "lead": "Keys - melody", "arp": "Keys - arpeggio"},
    "guitar": {"pad": "Guitar - chords", "stab": "Guitar - stabs", "lead": "Guitar - lead", "arp": "Guitar - picking"},
}
ROLE_GROUP = {"pad": "harmony", "stab": "harmony", "lead": "melody", "arp": "melody"}


class Stems:
    """Reads stems/*.flac on demand; remembers only their levels."""

    def __init__(self, d: Path):
        self.d = d
        self._db: dict[str, float] = {}

    def load(self, rel: str, mono: bool = False) -> np.ndarray:
        return audio.load(self.d / rel, mono=mono)

    def stem(self, name: str, mono: bool = False) -> np.ndarray:
        return self.load(f"stems/{name}.flac", mono)

    def db(self, name: str) -> float:
        if name not in self._db:
            self._db[name] = audio.rms_db(self.stem(name, mono=True))
        return self._db[name]


def analyze(d: Path, st) -> dict:
    meta = read_json(d / "meta.json")
    opts = read_json(d / "options.json", {}) or {}
    cache = d / "cache"
    gain = meta["gain"]
    store = Stems(d)

    mixm = store.stem("mix", mono=True)
    duration = len(mixm) / SR
    mix_db = audio.rms_db(mixm)

    st.detail("Building the beat grid", 0.01)
    beats = read_json(cache / "beats.json")
    grid = G.build(beats["beats"], beats["downbeats"], duration, G.first_onset(mixm, SR),
                   tempo_factor=float(opts.get("tempo_factor", 1.0)), downbeat_shift=int(opts.get("downbeat_shift", 0)),
                   meter=opts.get("meter"))
    allin1 = read_json(cache / "allin1.json", {}) or {}
    elements: list[dict] = []

    # ---------------------------------------------------------------- drums
    st.detail("Drum hits, patterns and swing", 0.05)
    drum_db = store.db("drums")
    names = [p for p in DRUM_PIECES if drum_db > -60 and store.db(p) >= max(drum_db - 24, mix_db - 40)]
    hits = DR.detect_all(lambda p: store.stem(p, mono=True), names) if names else {}
    # the kick defines the beat in most styles; fall back to kick + snare when it is sparse
    anchor = [h["t"] for h in hits.get("kick", []) if h["rel"] > -6]
    if len(anchor) < 32:
        anchor = [h["t"] for p in ("kick", "snare") for h in hits.get(p, []) if h["rel"] > -6]
    shift = G.refine(grid, np.array(anchor)) if anchor else 0.0
    if abs(shift) > 0.002:
        grid.notes.append(f"Grid nudged {shift * 1000:+.0f} ms onto the kick/snare transients.")
    n_bars = grid.n_bars(duration)            # after the nudge, so every per-bar array agrees
    D = DR.analyze(hits, grid, duration) if hits else {"present": False, "hits": {}, "patterns": []}
    for p in DRUM_PIECES:
        if p in D.get("hits", {}):
            elements.append({"id": p, "name": DR.PIECES[p][0], "group": "drums", "kind": "drums", "role": p,
                             "audio": f"stems/{p}.flac", "hits": D["hits"][p]})
    kick_times = np.array([h["t"] for h in D.get("hits", {}).get("kick", [])])

    # ---------------------------------------------------------------- notes
    notes = {k: read_json(cache / f"notes_{k}.json", []) or [] for k in PITCHED}
    notes["bass"] = N.repair_octave_splits(notes["bass"])
    for k in ("other", "piano", "guitar"):
        notes[k] = N.drop_bass_bleed(notes[k], notes["bass"])
    timing = {}
    for k in notes:
        N.add_beats(notes[k], grid)
        off = N.timing_offset(notes[k], 4)
        if off:
            N.shift_notes(notes[k], grid, off)
            timing[k] = round(off * grid.period * 1000, 1)

    if len(notes["bass"]) >= 8 or store.db("bass") > mix_db - 30:
        elements.append({"id": "bass", "name": "Bass", "group": "bass", "kind": "bass", "role": "bass",
                         "audio": "stems/bass.flac", "notes": notes["bass"]})

    # ---------------------------------------------------------------- harmonic stems -> roles
    for i, k in enumerate(("other", "piano", "guitar")):
        nts = notes[k]
        if store.db(k) < mix_db - 32 and len(nts) < 12:
            continue
        if len(nts) < 12:
            if k == "other":
                elements.append({"id": "other_texture", "name": "Atmos / FX / noise", "group": "fx", "kind": k,
                                 "role": "texture", "audio": f"stems/{k}.flac", "notes": []})
            continue
        roles = R.split(nts, k)
        if k == "other" or len(roles) > 1:
            st.detail(f"Splitting the {k} stem into {', '.join(roles)}", 0.12 + 0.08 * i)
            residual = "texture" if k == "other" else None
            parts = list(roles) + ([residual] if residual else [])
            R.render_to_files(store.stem(k), roles, residual, {r: d / f"stems/{k}_{r}.flac" for r in parts})
            for r in parts:
                rel = f"stems/{k}_{r}.flac"
                if r == "texture":
                    if audio.rms_db(store.load(rel, mono=True)) > mix_db - 36:
                        elements.append({"id": f"{k}_texture", "name": "Atmos / FX / noise", "group": "fx",
                                         "kind": k, "role": "texture", "audio": rel, "notes": []})
                    continue
                elements.append(_role_element(f"{k}_{r}", k, r, rel, roles[r]))
        else:
            r = next(iter(roles))
            elements.append(_role_element(k, k, r, f"stems/{k}.flac", roles[r]))

    if store.db("vocals") > mix_db - 30 or len(notes["vocals"]) >= 24:
        elements.append({"id": "vocals", "name": "Vocals", "group": "vocals", "kind": "vocals", "role": "vocals",
                         "audio": "stems/vocals.flac", "notes": notes["vocals"]})
    elements.sort(key=lambda e: (GROUP_ORDER.index(e["group"]), _role_rank(e)))

    # ---------------------------------------------------------------- one pass per part
    mix_levels = ST.bar_levels(mixm, grid, n_bars)
    mix_ref = np.percentile(mix_levels[mix_levels > -100], 90) if np.any(mix_levels > -100) else -20.0
    pattern_note = ""
    if D.get("patterns"):
        p0 = D["patterns"][0]
        pattern_note = f"Main groove is pattern {p0['id']} ({p0['count']} of {sum(1 for b in D['bar_pattern'] if b)} drum bars)."
    act: dict[str, np.ndarray] = {}
    kept = []
    for i, e in enumerate(elements):
        st.detail(f"Measuring {e['name']}", 0.3 + 0.5 * i / max(len(elements), 1))
        x = store.load(e["audio"])
        m = audio.mono(x)
        lv = ST.bar_levels(m, grid, n_bars)
        e["level_db"] = round(float(np.percentile(lv[lv > -100], 90) - mix_ref), 1) if np.any(lv > -100) else -60.0
        if e["level_db"] < -40 and e["group"] != "drums":
            continue                               # separation leftovers only add noise
        act[e["id"]] = _activity(e, lv, grid.meter, n_bars)
        e["peaks"] = audio.peaks_b64(m)
        e["features"] = _features(e, x, m, grid, duration, kick_times, timing, pattern_note)
        kept.append(e)
        del x, m
    elements = kept

    # ---------------------------------------------------------------- FX events
    st.detail("Looking for risers, impacts, rolls and drops", 0.82)
    segs = allin1.get("segments") or []
    if segs:
        bounds = sorted({int(round(float(grid.beat(s["start"])) / grid.meter)) for s in segs})
    else:
        bounds = [p["b0"] for p in ST.fallback_phrases(n_bars, mix_levels, act)]
    bounds = [b for b in bounds if 1 <= b < n_bars]
    tex = next((e for e in elements if e["id"] == "other_texture"), None)
    fx_m = store.load(tex["audio"], mono=True) if tex else store.stem("other", mono=True)
    events = FX.transitions(fx_m, grid, n_bars, bounds, tex["id"] if tex else "other")
    events += FX.impacts(D.get("hits", {}), fx_m, grid, bounds)
    del fx_m
    if grid.has_beat:        # rolls and "drop-out before the downbeat" only mean something with a pulse
        events += FX.snare_rolls(D.get("hits", {}), grid, bounds, n_bars)
        events += FX.silences(mixm, grid, bounds)
    events.sort(key=lambda e: (e["bar0"], e["type"]))

    # ---------------------------------------------------------------- sections
    groups = {e["id"]: e["group"] for e in elements}
    four_floor = False
    if D.get("patterns"):
        kick = D["patterns"][0]["grid"].get("kick:36", [])
        spb = D["steps_per_beat"]
        four_floor = bool(kick) and all(kick[i * spb] for i in range(grid.meter))
    secs = ST.sections(segs, grid, n_bars, mix_levels, act, groups,
                       [e for e in events if e["type"] in ("riser", "snare_roll")], edm=four_floor)

    # ---------------------------------------------------------------- key & chords
    st.detail("Key and chord progression", 0.88)
    raw = read_json(cache / "chords_raw.json", []) or []
    key, chords = _key_and_chords(raw, notes, grid)
    bar_ch = H.bar_chords(chords, grid.meter, n_bars)
    for s in secs:
        s["chord_bars"] = bar_ch[s["b0"]:s["b1"]]
        s["chord_loop"] = H.find_loop(s["chord_bars"])
        s["key"] = _section_key(s, notes, chords, key, grid.meter) if key["clear"] else None

    # ---------------------------------------------------------------- MIDI
    st.detail("Writing MIDI files", 0.92)
    _write_midi(d, elements, D, chords, grid, meta.get("title", "track"))

    # ---------------------------------------------------------------- assemble
    st.detail("Saving results", 0.96)
    for e in elements:
        e["activity"] = [round(float(v), 2) for v in act[e["id"]]]
        if e.get("notes") is not None:
            e["notes"] = [[round(n["bs"], 3), round(n["be"], 3), int(n["p"]), EX.velocity(n), round(n["s"], 3),
                           round(n["e"], 3)] for n in e["notes"]]
        if e.get("hits") is not None:
            e["hits"] = [[round(h["beat"], 3), h["vel"], h["note"], round(h["t"], 4)] for h in e["hits"]]
    gj = grid.to_json(duration)
    A = {
        "version": PIPELINE_VERSION,
        "created": time.time(),
        "track": {"id": meta["id"], "title": meta.get("title", ""), "artist": meta.get("artist", ""),
                  "filename": meta.get("filename", ""), "duration": round(duration, 3), "gain": gain,
                  "stem_model": meta.get("stem_model")},
        "options": opts,
        "global": {**{k: gj[k] for k in ("bpm", "meter", "steady", "confidence", "origin_time", "n_bars", "notes",
                                         "has_beat")},
                   "key": key, "allin1_bpm": allin1.get("bpm"), "sections_model": bool(segs),
                   "tempo_alternatives": [round(grid.bpm * 2, 2), round(grid.bpm / 2, 2)]},
        "grid": {"beats": gj["beats"], "first_beat": gj["first_beat"]},
        "mix": {"audio": "stems/mix.flac", "peaks": audio.peaks_b64(mixm),
                "levels": [round(float(v), 1) for v in mix_levels]},
        "sections": secs,
        "chords": chords,
        "fx_events": events,
        "drums": {k: v for k, v in D.items() if k != "hits"},
        "elements": elements,
        "drums_stem": "stems/drums.flac",
    }
    write_json(d / "analysis.json", A)
    write_summary(d, A)
    (d / "GUIDE.md").write_text(EX.guide_markdown(A), encoding="utf-8")
    for z in ("export_midi.zip", "export_full.zip"):
        (d / z).unlink(missing_ok=True)
    return A


# ----------------------------------------------------------------------------- pieces
def _activity(e: dict, lv: np.ndarray, meter: int, n_bars: int) -> np.ndarray:
    """0..1 per bar: level relative to the part's loud bars, gated by where it has hits/notes."""
    a = ST.activity(lv)
    present = np.zeros(n_bars, bool)
    if e.get("hits"):
        for h in e["hits"]:
            b = int(np.floor(h["beat"] / meter))
            if 0 <= b < n_bars:
                present[b] = True
        return np.where(present, np.maximum(a, 0.15), 0.0)
    if e.get("notes"):
        for n in e["notes"]:
            b0 = int(np.floor(n["bs"] / meter))
            b1 = int(np.floor((n["be"] - 1e-6) / meter))
            if b1 < 0 or b0 >= n_bars:
                continue                            # entirely before bar 1 or after the end
            present[max(b0, 0):min(b1, n_bars - 1) + 1] = True
        return np.where(present, a, a * 0.25)
    return a


def _features(e: dict, x: np.ndarray, m: np.ndarray, grid, duration: float, kick_times: np.ndarray,
              timing: dict, pattern_note: str) -> dict:
    """Sound-design measurements and Ableton suggestions for one part (fills e['suggest'])."""
    f = {"width": S.stereo_width(x)}
    if e["kind"] in timing:
        f["timing_ms"] = timing[e["kind"]]
    if "chord_fraction" in e:
        f["chord_fraction"] = e["chord_fraction"]
    if e["group"] == "drums":
        f["drum"] = S.drum_character(x, e["hits"], e["role"])
        e["suggest"] = SG.drum(e["role"], f["drum"], pattern_note if e["role"] == "kick" else "")
        return f
    nts = e.get("notes") or []
    if nts:
        on, off, du = _onset_clusters(nts)
        f["envelope"] = S.envelope(m, on, off, du)
        f["notes"] = S.note_stats(nts, grid)
        sample = nts if len(nts) <= 160 else [nts[j] for j in np.linspace(0, len(nts) - 1, 160).astype(int)]
        f["timbre"] = S.timbre(m, nts, sample)
    else:
        f["timbre"] = S.timbre(m, [])
    pc = S.pumping(m, nts, grid, kick_times, duration)
    if pc:
        f["pumping"] = pc
    if nts and e["role"] not in ("pad", "texture"):
        ec = S.echo(m, nts, grid)
        if ec:
            f["echo"] = ec
    if e["kind"] != "vocals" and e["role"] != "texture":
        f["filter_moves"] = S.filter_moves(m, nts, grid, duration)
    if e["role"] in ("bass", "low"):
        f["low_width_db"] = S.low_end_width(x)
    e["suggest"] = SG.melodic(e["role"], e["kind"], f)
    if e["role"] == "texture":
        e["suggest"]["device"] = "Samples / noise synth (Wavetable noise osc, Operator noise) + automation"
        e["suggest"]["tips"].insert(0, "Everything in the synth stem that isn't a clear note: noise sweeps, "
                                       "risers, reverb wash, atmospheres. Check the FX lane for timings.")
    return f


def _key_and_chords(raw: list[dict], notes: dict, grid) -> tuple[dict, list[dict]]:
    nc = H.chroma_from_notes([(notes["other"], 1.0), (notes["piano"], 1.0), (notes["guitar"], 1.0),
                              (notes["bass"], 0.8), (notes["vocals"], 0.6)])
    parsed = []
    for c in raw:
        ch = H.parse_label(c["chord"])
        if ch:
            parsed.append({**ch, "b0": c["start_time"], "b1": c["end_time"]})
    cc = H.chroma_from_chords(parsed)
    chroma = nc / (nc.sum() + 1e-9) + cc / (cc.sum() + 1e-9)
    if chroma.sum() > 0:
        key = _relative_tiebreak(H.detect_key(chroma), parsed)
    else:
        key = H.detect_key(H.MINOR)                 # placeholder; flagged as unclear below
    # only call it a key when there is real tonal material and the profile fits well
    tonal_s = sum(c["b1"] - c["b0"] for c in parsed) + sum(n["e"] - n["s"] for k in ("other", "piano", "guitar", "bass")
                                                           for n in notes[k])
    key["clear"] = bool(chroma.sum() > 0 and key["score"] >= 0.55 and tonal_s >= 10)
    return key, H.chords_on_grid(raw, grid, key)


def _write_midi(d: Path, elements: list[dict], D: dict, chords: list[dict], grid, title: str) -> None:
    midi_dir = d / "midi"
    midi_dir.mkdir(exist_ok=True)
    for p in midi_dir.glob("*.mid"):
        p.unlink()
    bpm, meter = grid.bpm, grid.meter
    q = 1.0 / (D.get("steps_per_beat", 4) if D.get("present") else 4)
    all_tracks = []
    for e in elements:
        if e.get("hits"):
            nts, ch = [{"bs": h["beat"], "be": h["beat"] + 0.25, "note": h["note"], "vel": h["vel"]} for h in e["hits"]], 9
        elif e.get("notes"):
            nts, ch = e["notes"], 0
        else:
            continue
        EX.write_midi(midi_dir / f"{e['id']}.mid", [(e["name"], nts, ch)], bpm, meter, None, title)
        EX.write_midi(midi_dir / f"{e['id']}_quantized.mid", [(e["name"], nts, ch)], bpm, meter, q, title)
        e["midi"] = f"{e['id']}.mid"
        e["midi_q"] = f"{e['id']}_quantized.mid"
        if ch != 9:
            all_tracks.append((e["name"], nts, 0))
    if D.get("present"):
        dn = EX.drum_notes(D["hits"])
        EX.write_midi(midi_dir / "drums.mid", [("Drums", dn, 9)], bpm, meter, None, title)
        EX.write_midi(midi_dir / "drums_quantized.mid", [("Drums", dn, 9)], bpm, meter, q, title)
        all_tracks.insert(0, ("Drums", dn, 9))
    chord_notes = _chord_midi(chords)
    if chord_notes:
        EX.write_midi(midi_dir / "chords.mid", [("Chords", chord_notes, 0)], bpm, meter, None, title)
        all_tracks.append(("Chords (recognised)", chord_notes, 0))
    if all_tracks:
        EX.write_midi(midi_dir / "all_parts.mid", all_tracks, bpm, meter, None, title)


def write_summary(d: Path, A: dict) -> None:
    """Small per-track header for the library view (the full analysis can be several MB)."""
    g = A["global"]
    S_ = {
        "title": A["track"]["title"], "artist": A["track"].get("artist", ""), "duration": A["track"]["duration"],
        "bpm": g["bpm"], "meter": g["meter"], "bars": g["n_bars"], "has_beat": g.get("has_beat", True),
        "key": g["key"]["name"] if g["key"].get("clear", True) else None,
        "key_clear": bool(g["key"].get("clear", True)),
        "groups": sorted({e["group"] for e in A["elements"]}),
        "sections": [[s["b0"], s["b1"], s.get("energy", 0.5), s["name"]] for s in A["sections"]],
        "version": A.get("version", 1),
    }
    write_json(d / "summary.json", S_)


def _section_key(s: dict, notes: dict, chords: list[dict], key: dict, meter: int) -> str | None:
    """Key of one section when it clearly differs from the song key (modulations, DJ mixes)."""
    if s["b1"] - s["b0"] < 8:
        return None
    lo, hi = s["b0"] * meter, s["b1"] * meter
    sub = [([n for n in notes[k] if lo <= n["bs"] < hi], w)
           for k, w in (("other", 1.0), ("piano", 1.0), ("guitar", 1.0), ("bass", 0.8), ("vocals", 0.6))]
    nc = H.chroma_from_notes(sub)
    cc = H.chroma_from_chords([c for c in chords if c["root"] is not None and lo <= c["b0"] < hi])
    if nc.sum() + cc.sum() == 0:
        return None
    c = nc / (nc.sum() + 1e-9) + cc / (cc.sum() + 1e-9)
    k2 = H.detect_key(c)
    if _same_scale(k2, key) or k2["score"] < 0.7:
        return None
    # a real key change fits the section clearly better than the song key does (not just a
    # section that leans on other chords of the same key)
    if k2["score"] - H.key_fit(c, key["tonic_pc"], key["mode"]) < 0.15:
        return None
    return k2["name"]


def _same_scale(a: dict, b: dict) -> bool:
    """Same key, or relative major/minor (same notes)."""
    if a["tonic_pc"] == b["tonic_pc"] and a["mode"] == b["mode"]:
        return True
    return a["mode"] != b["mode"] and (a["tonic_pc"] + (9 if a["mode"] == "major" else 3)) % 12 == b["tonic_pc"]


def _role_element(eid: str, kind: str, role: str, audio_rel: str, nts: list[dict]) -> dict:
    """A pitched layer; a part that mixes single notes and chords is named as a riff/hook."""
    name, group = NAMES[kind][role], ROLE_GROUP[role]
    cf = R.chord_fraction(nts)
    if role in ("stab", "lead", "arp") and 0.2 <= cf <= 0.8:
        name = {"other": "Synth riff / hook", "piano": "Keys riff", "guitar": "Guitar riff"}[kind]
        group = "melody"
        role = "riff"
    return {"id": eid, "name": name, "group": group, "kind": kind, "role": role, "audio": audio_rel,
            "notes": nts, "chord_fraction": round(cf, 2)}


def _role_rank(e: dict) -> int:
    order = ["kick", "snare", "toms", "hh", "ride", "crash", "bass", "low", "pad", "stab", "riff", "lead", "arp",
             "vocals", "texture"]
    return order.index(e["role"]) if e["role"] in order else 50


def _onset_clusters(nts: list[dict], tol: float = 0.035):
    """Group near-simultaneous note starts (chords): (onsets, offsets, durations) arrays."""
    ns = sorted(nts, key=lambda n: n["s"])
    on, off, du = [], [], []
    cur = None
    for n in ns:
        if cur is not None and n["s"] - cur[0] <= tol:
            cur[1] = max(cur[1], n["e"])
        else:
            if cur is not None:
                on.append(cur[0])
                off.append(cur[1])
                du.append(cur[1] - cur[0])
            cur = [n["s"], n["e"]]
    if cur is not None:
        on.append(cur[0])
        off.append(cur[1])
        du.append(cur[1] - cur[0])
    return np.array(on), np.array(off), np.array(du)


def _relative_tiebreak(key: dict, chords: list[dict]) -> dict:
    """Major vs relative minor share notes; pick the one whose tonic chord dominates."""
    alt = key["alternatives"][0]
    rel_pc = (key["tonic_pc"] + (9 if key["mode"] == "major" else 3)) % 12
    alt_pc = H.PC.get(alt["tonic"])
    if alt_pc != rel_pc or alt["mode"] == key["mode"] or key["score"] - alt["score"] > 0.08 or not chords:
        return key
    dur = {}
    for c in chords:
        ivs = H.QUAL[c["quality"]][0]
        k = (c["root"], "minor" if 3 in ivs and 4 not in ivs else "major")
        dur[k] = dur.get(k, 0) + (c["b1"] - c["b0"])
    here = dur.get((key["tonic_pc"], key["mode"]), 0)
    there = dur.get((rel_pc, alt["mode"]), 0)
    if there > 1.3 * here:
        new = H.detect_key(np.roll(H.MAJOR if alt["mode"] == "major" else H.MINOR, rel_pc))
        new["alternatives"] = [{"tonic": key["tonic"], "mode": key["mode"], "score": key["score"]}] + key["alternatives"][1:]
        new["score"] = key["score"]
        new["confidence"] = round(float(max(0.0, there / (here + there) - 0.5)), 3)
        new["note"] = "Relative major/minor decided by which tonic chord is heard most."
        return new
    return key


def _chord_midi(chords: list[dict]) -> list[dict]:
    """Simple block voicings: chord tones from C3 up, plus the bass note an octave below."""
    out = []
    for c in chords:
        if c["root"] is None:
            continue
        root = 48 + c["root"]
        for iv in H.QUAL[c["quality"]][0]:
            out.append({"bs": c["b0"], "be": c["b1"], "p": root + iv + 12, "vel": 80})
        bass = 36 + (c["bass"] if c["bass"] is not None else c["root"])
        out.append({"bs": c["b0"], "be": c["b1"], "p": bass, "vel": 80})
    return out
