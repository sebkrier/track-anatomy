"""Key, chords (lv-chordia) and chord-loop summaries."""
import re

import numpy as np

SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
PC = {n: i for i, n in enumerate(SHARP)} | {n: i for i, n in enumerate(FLAT)} | {"Cb": 11, "Fb": 4, "E#": 5, "B#": 0}

# Krumhansl-Kessler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# interval sets for chord qualities (Harte shorthand -> semitones, display suffix)
QUAL = {
    "maj": ((0, 4, 7), ""), "min": ((0, 3, 7), "m"), "dim": ((0, 3, 6), "dim"), "aug": ((0, 4, 8), "aug"),
    "7": ((0, 4, 7, 10), "7"), "maj7": ((0, 4, 7, 11), "maj7"), "min7": ((0, 3, 7, 10), "m7"),
    "dim7": ((0, 3, 6, 9), "dim7"), "hdim7": ((0, 3, 6, 10), "m7b5"), "minmaj7": ((0, 3, 7, 11), "m(maj7)"),
    "sus2": ((0, 2, 7), "sus2"), "sus4": ((0, 5, 7), "sus4"), "maj6": ((0, 4, 7, 9), "6"),
    "min6": ((0, 3, 7, 9), "m6"), "9": ((0, 4, 7, 10, 14), "9"), "maj9": ((0, 4, 7, 11, 14), "maj9"),
    "min9": ((0, 3, 7, 10, 14), "m9"), "11": ((0, 4, 7, 10, 17), "11"), "min11": ((0, 3, 7, 10, 17), "m11"),
    "13": ((0, 4, 7, 10, 21), "13"), "5": ((0, 7), "5"), "1": ((0,), ""),
}
DEGREE = {"1": 0, "b2": 1, "2": 2, "#2": 3, "b3": 3, "3": 4, "4": 5, "#4": 6, "b5": 6, "5": 7, "#5": 8,
          "b6": 8, "6": 9, "bb7": 9, "b7": 10, "7": 11, "b9": 1, "9": 2, "#9": 3, "11": 5, "#11": 6, "13": 9}
FLAT_KEYS_MAJ = {5, 10, 3, 8, 1, 6}     # F Bb Eb Ab Db Gb
FLAT_KEYS_MIN = {2, 7, 0, 5, 10, 3}     # Dm Gm Cm Fm Bbm Ebm


def detect_key(chroma: np.ndarray) -> dict:
    """chroma: 12-vector of pitch-class weights."""
    c = chroma / (chroma.sum() + 1e-9)
    scores = []
    for t in range(12):
        for mode, prof in (("major", MAJOR), ("minor", MINOR)):
            r = np.corrcoef(c, np.roll(prof, t))[0, 1]
            scores.append((float(r), t, mode))
    scores.sort(reverse=True)
    best = scores[0]
    spell = _spelling(best[1], best[2])
    alts = [{"tonic": spell[t], "mode": m, "score": round(s, 3)} for s, t, m in scores[1:4]]
    return {"tonic_pc": best[1], "tonic": spell[best[1]], "mode": best[2], "score": round(best[0], 3),
            "confidence": round(float(best[0] - scores[1][0]), 3), "alternatives": alts,
            "name": f"{spell[best[1]]} {best[2]}", "scale": scale_notes(best[1], best[2])}


def key_fit(chroma: np.ndarray, tonic_pc: int, mode: str) -> float:
    """How well a given key's profile matches a chroma vector (same scale as detect_key's score)."""
    c = chroma / (chroma.sum() + 1e-9)
    return float(np.corrcoef(c, np.roll(MAJOR if mode == "major" else MINOR, tonic_pc))[0, 1])


def _spelling(tonic: int, mode: str) -> list[str]:
    flats = tonic in (FLAT_KEYS_MAJ if mode == "major" else FLAT_KEYS_MIN)
    return FLAT if flats else SHARP


def scale_notes(tonic: int, mode: str) -> list[str]:
    steps = [0, 2, 4, 5, 7, 9, 11] if mode == "major" else [0, 2, 3, 5, 7, 8, 10]
    sp = _spelling(tonic, mode)
    return [sp[(tonic + s) % 12] for s in steps]


def parse_label(label: str) -> dict | None:
    """Harte label like 'Eb:min7/b3' -> root pc, quality, bass pc."""
    if label in ("N", "X", "") or label is None:
        return None
    m = re.match(r"^([A-G][b#]?)(?::([^/]+))?(?:/(.+))?$", label)
    if not m:
        return None
    root = PC.get(m.group(1))
    if root is None:
        return None
    qual = m.group(2) or "maj"
    qual = re.sub(r"\(.*\)", "", qual) or "maj"
    if qual not in QUAL:
        qual = "min" if qual.startswith("min") else ("maj" if qual.startswith("maj") else "maj")
    bass = root
    if m.group(3):
        bass = (root + DEGREE.get(m.group(3), 0)) % 12
    return {"root": root, "quality": qual, "bass": bass}


def symbol(ch: dict, spell: list[str]) -> str:
    s = spell[ch["root"]] + QUAL[ch["quality"]][1]
    if ch["bass"] != ch["root"]:
        s += "/" + spell[ch["bass"]]
    return s


def roman(ch: dict, key: dict) -> str:
    deg = (ch["root"] - key["tonic_pc"]) % 12
    minor_key = key["mode"] == "minor"
    names = {0: "I", 1: "bII", 2: "II", 3: "III" if minor_key else "bIII", 4: "III", 5: "IV", 6: "bV",
             7: "V", 8: "VI" if minor_key else "bVI", 9: "VI", 10: "VII" if minor_key else "bVII", 11: "VII"}
    if minor_key and deg == 4:
        names[4] = "#III"
    if minor_key and deg == 9:
        names[9] = "#VI"
    if minor_key and deg == 11:
        names[11] = "#VII"
    r = names[deg]
    q = ch["quality"]
    third = QUAL[q][0]
    if 3 in third and 4 not in third:
        r = r.lower()
    if q == "dim" or q == "dim7":
        r += "°"
    elif q == "hdim7":
        r += "ø"
    elif q == "aug":
        r += "+"
    suffix = {"7": "7", "maj7": "maj7", "min7": "7", "dim7": "7", "hdim7": "7", "9": "9", "min9": "9",
              "maj9": "maj9", "sus2": "sus2", "sus4": "sus4", "maj6": "6", "min6": "6"}.get(q, "")
    return r + suffix


def recognise(path: str) -> list[dict]:
    from lv_chordia import chord_recognition
    return chord_recognition(path, chord_dict_name="submission")


def chords_on_grid(raw: list[dict], grid, key: dict) -> list[dict]:
    """Snap chord changes to beats, drop sub-beat blips, merge repeats."""
    spell = _spelling(key["tonic_pc"], key["mode"])
    segs = []
    for c in raw:
        ch = parse_label(c["chord"])
        b0 = round(float(grid.beat(c["start_time"])))
        b1 = round(float(grid.beat(c["end_time"])))
        if b1 <= b0:
            continue
        segs.append({"b0": b0, "b1": b1, "ch": ch})
    # drop blips shorter than one beat by extending the previous segment
    clean = []
    for s in segs:
        if clean and s["b1"] - s["b0"] < 1:
            clean[-1]["b1"] = s["b1"]
            continue
        if clean and clean[-1]["b1"] != s["b0"]:
            s["b0"] = clean[-1]["b1"]
        clean.append(s)
    merged = []
    for s in clean:
        if merged and _same(merged[-1]["ch"], s["ch"]):
            merged[-1]["b1"] = s["b1"]
        else:
            merged.append(dict(s))
    out = []
    for s in merged:
        ch = s["ch"]
        item = {"b0": s["b0"], "b1": s["b1"], "t0": round(float(grid.time(s["b0"])), 3),
                "t1": round(float(grid.time(s["b1"])), 3)}
        if ch is None:
            item.update({"symbol": "N.C.", "roman": "", "root": None, "quality": None, "bass": None, "tones": []})
        else:
            item.update({"symbol": symbol(ch, spell), "roman": roman(ch, key), "root": ch["root"],
                         "quality": ch["quality"], "bass": ch["bass"],
                         "tones": [spell[(ch["root"] + i) % 12] for i in QUAL[ch["quality"]][0]]})
        out.append(item)
    return out


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a["root"] == b["root"] and a["quality"] == b["quality"] and a["bass"] == b["bass"]


def bar_chords(chords: list[dict], meter: int, n_bars: int) -> list[list[str]]:
    """Per bar, the chord symbols in order (usually 1-2 per bar)."""
    out = []
    for b in range(n_bars):
        lo, hi = b * meter, (b + 1) * meter
        syms = []
        for c in chords:
            if c["b1"] > lo and c["b0"] < hi:
                ov = min(c["b1"], hi) - max(c["b0"], lo)
                if ov >= 1 and (not syms or syms[-1] != c["symbol"]):
                    syms.append(c["symbol"])
        out.append(syms)
    return out


def find_loop(bars: list[list[str]]) -> dict | None:
    """Shortest bar period (1,2,4,8) whose chord pattern repeats across the span."""
    seq = [" ".join(b) if b else "-" for b in bars]
    if len(seq) < 2 or all(s == "-" for s in seq):
        return None
    for P in (1, 2, 4, 8):
        if P > len(seq):
            break
        agree = sum(1 for i in range(P, len(seq)) if seq[i] == seq[i - P])
        if len(seq) > P and agree / (len(seq) - P) >= 0.75:
            pat = seq[:P]
            # rotate so the loop starts on its most common first chord position
            return {"bars": P, "chords": pat}
    return None


def chroma_from_notes(note_sets: list[tuple[list[dict], float]]) -> np.ndarray:
    c = np.zeros(12)
    for notes, w in note_sets:
        for n in notes:
            c[n["p"] % 12] += w * (n["e"] - n["s"]) * (0.5 + n.get("a", 0.5))
    return c


def chroma_from_chords(chords: list[dict]) -> np.ndarray:
    c = np.zeros(12)
    for ch in chords:
        if ch["root"] is None:
            continue
        d = ch["b1"] - ch["b0"]
        for i in QUAL[ch["quality"]][0]:
            c[(ch["root"] + i) % 12] += d * (1.5 if i == 0 else 1.0)
    return c
