"""Note transcription (Spotify basic-pitch, ONNX on CPU) and clean-up."""
import logging

import numpy as np

from ..config import SR
from . import audio

logging.getLogger().setLevel(logging.ERROR)

_MODEL = None

PARAMS = {
    #          onset, frame, min_len_ms, fmin, fmax, melodia
    "bass":   (0.45, 0.30, 70, 27.0, 500.0, True),
    "vocals": (0.50, 0.30, 90, 70.0, 1400.0, True),
    "piano":  (0.50, 0.30, 60, 27.0, None, False),
    "guitar": (0.50, 0.30, 60, 60.0, None, False),
    "other":  (0.50, 0.30, 60, 30.0, None, False),
}


def _model():
    global _MODEL
    if _MODEL is None:
        from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
        from basic_pitch.inference import Model
        _MODEL = Model(build_icassp_2022_model_path(FilenameSuffix.onnx))
    return _MODEL


def transcribe(path, kind: str, x_mono: np.ndarray) -> list[dict]:
    from basic_pitch.inference import predict
    onset, frame, min_len, fmin, fmax, melodia = PARAMS.get(kind, PARAMS["other"])
    if audio.rms_db(x_mono) < -60:
        return []
    _, _, events = predict(str(path), _model(), onset_threshold=onset, frame_threshold=frame,
                           minimum_note_length=min_len, minimum_frequency=fmin, maximum_frequency=fmax,
                           multiple_pitch_bends=False, melodia_trick=melodia)
    notes = []
    for ev in events:
        s, e, p, amp = float(ev[0]), float(ev[1]), int(ev[2]), float(ev[3])
        bends = ev[4] if len(ev) > 4 and ev[4] is not None else []
        notes.append({"s": s, "e": e, "p": p, "a": amp, "bend": _bend_summary(bends)})
    notes.sort(key=lambda n: (n["s"], n["p"]))
    notes = gate(notes, x_mono)
    if kind in ("bass", "vocals"):
        notes = monophonic(notes)
    return notes


def _bend_summary(bends) -> float:
    """Largest pitch excursion in semitones (basic-pitch bends are in 1/3 semitone steps)."""
    if bends is None or len(bends) == 0:
        return 0.0
    b = np.asarray(bends, dtype=float) / 3.0
    return float(b[np.argmax(np.abs(b))])


def gate(notes: list[dict], x_mono: np.ndarray, rel_db: float = 38.0) -> list[dict]:
    """Drop notes where the stem is (nearly) silent: separation bleed / ghost notes."""
    if not notes:
        return notes
    hop = 512
    env = audio.db(audio.rms_env(x_mono, hop=hop, win=2048))
    ref = np.percentile(env[env > -100], 95) if np.any(env > -100) else -20
    kept = []
    for n in notes:
        a, b = int(n["s"] * SR / hop), max(int(n["s"] * SR / hop) + 1, int(n["e"] * SR / hop))
        lvl = float(env[a:b].max()) if a < len(env) else -120
        if lvl > ref - rel_db and n["a"] > 0.12:
            n["lvl"] = round(float(lvl - ref), 1)
            kept.append(n)
    return kept


def monophonic(notes: list[dict]) -> list[dict]:
    """Keep one note at a time (strongest wins overlaps)."""
    notes = sorted(notes, key=lambda n: n["s"])
    out = []
    for n in notes:
        if out and n["s"] < out[-1]["e"] - 0.02:
            cur = out[-1]
            if abs(n["s"] - cur["s"]) < 0.04:            # simultaneous: keep louder
                if n["a"] > cur["a"]:
                    out[-1] = n
                continue
            if n["a"] >= cur["a"] * 0.6:                  # new note takes over
                cur["e"] = n["s"]
                out.append(n)
            # else: quieter overlapping note, drop
        else:
            out.append(n)
    return [n for n in out if n["e"] - n["s"] > 0.03]


def repair_octave_splits(notes: list[dict]) -> list[dict]:
    """basic-pitch often splits a bass note into a short blip an octave up + the sustained note.
    Merge the blip into the following note (keeping the sustained note's pitch)."""
    out = []
    i = 0
    notes = sorted(notes, key=lambda n: n["s"])
    while i < len(notes):
        n = notes[i]
        if i + 1 < len(notes):
            m = notes[i + 1]
            if (n["e"] - n["s"] < 0.09 and abs(m["s"] - n["e"]) < 0.025 and abs(m["p"] - n["p"]) in (12, 24)
                    and m["e"] - m["s"] > n["e"] - n["s"]):
                merged = dict(m)
                merged["s"] = n["s"]
                merged["a"] = max(n["a"], m["a"])
                out.append(merged)
                i += 2
                continue
        out.append(n)
        i += 1
    return out


def drop_bass_bleed(notes: list[dict], bass: list[dict], below: int = 48) -> list[dict]:
    """Low notes in a synth/keys stem that duplicate a sounding bass note are separation bleed."""
    if not bass:
        return notes
    bs = np.array([b["s"] for b in bass])
    be = np.array([b["e"] for b in bass])
    bp = np.array([b["p"] % 12 for b in bass])
    keep = []
    for n in notes:
        if n["p"] < below:
            ov = (bs < n["e"] - 0.03) & (be > n["s"] + 0.03) & (bp == n["p"] % 12)
            if ov.any():
                continue
        keep.append(n)
    return keep


def add_beats(notes: list[dict], grid) -> None:
    if not notes:
        return
    s = grid.beat(np.array([n["s"] for n in notes]))
    e = grid.beat(np.array([n["e"] for n in notes]))
    for n, bs, be in zip(notes, s, e):
        n["bs"] = float(bs)
        n["be"] = float(max(be, bs + 0.05))


def timing_offset(notes: list[dict], steps_per_beat: int = 4) -> float:
    """Consistent onset offset (beats) vs the 16th grid, e.g. transcription latency.
    Returns 0 unless the offset is clear and consistent."""
    if len(notes) < 30:
        return 0.0
    S = steps_per_beat
    b = np.array([n["bs"] for n in notes])
    dev = b - np.round(b * S) / S
    med = float(np.median(dev))
    mad = float(np.median(np.abs(dev - med)))
    if 0.012 <= abs(med) <= 0.08 and mad < 0.05:
        return med
    return 0.0


def shift_notes(notes: list[dict], grid, beats: float) -> None:
    if not notes or not beats:
        return
    for n in notes:
        n["bs"] -= beats
        n["be"] -= beats
    s = grid.time(np.array([n["bs"] for n in notes]))
    e = grid.time(np.array([n["be"] for n in notes]))
    for n, ts, te in zip(notes, s, e):
        n["s"], n["e"] = float(ts), float(te)
