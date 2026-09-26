"""Arrangement: per-bar activity of every element, and named sections."""
import numpy as np

from ..config import SR
from . import audio


def bar_levels(x_mono: np.ndarray, grid, n_bars: int) -> np.ndarray:
    """RMS dB per bar."""
    out = np.full(n_bars, -120.0)
    for b in range(n_bars):
        a, e = int(max(0, grid.bar_start(b)) * SR), int(max(0, grid.bar_start(b + 1)) * SR)
        if e > a:
            out[b] = audio.rms_db(x_mono[a:e])
    return out


def activity(levels: np.ndarray, rel: float = 30.0, abs_floor: float = -62.0) -> np.ndarray:
    """0..1 per bar, relative to the element's own loud bars."""
    if not np.any(levels > -100):
        return np.zeros_like(levels)
    ref = np.percentile(levels[levels > -100], 95)
    a = np.clip((levels - (ref - rel)) / rel, 0, 1)
    a[levels < abs_floor] = 0
    return a


LABELS = {"intro": "Intro", "outro": "Outro", "verse": "Verse", "chorus": "Chorus", "bridge": "Bridge",
          "inst": "Instrumental", "solo": "Solo", "break": "Breakdown", "start": "Intro", "end": "Outro"}


def sections(segments: list[dict] | None, grid, n_bars: int, mix_levels: np.ndarray,
             act: dict[str, np.ndarray], groups: dict[str, str], risers: list[dict], edm: bool = True) -> list[dict]:
    """segments: allin1 [{'start','end','label'}] or None for the fallback.
    edm: use dance-music names (Drop, Groove) only for four-on-the-floor tracks."""
    phrases = []
    if segments:
        for s in segments:
            b0 = int(round(float(grid.beat(s["start"])) / grid.meter))
            b1 = int(round(float(grid.beat(s["end"])) / grid.meter))
            b0, b1 = max(0, b0), min(n_bars, b1)
            if b1 > b0:
                phrases.append({"b0": b0, "b1": b1, "raw": s["label"]})
    if not phrases:
        phrases = fallback_phrases(n_bars, mix_levels, act)
    # fix gaps/overlaps, absorb 1-bar slivers
    phrases.sort(key=lambda p: p["b0"])
    fixed = []
    for p in phrases:
        if fixed and p["b0"] < fixed[-1]["b1"]:
            p["b0"] = fixed[-1]["b1"]
        if fixed and p["b0"] > fixed[-1]["b1"]:
            fixed[-1]["b1"] = p["b0"]
        if p["b1"] > p["b0"]:
            fixed.append(p)
    if fixed:
        fixed[0]["b0"] = 0
        fixed[-1]["b1"] = n_bars
    merged = []
    for p in fixed:
        if merged and (p["b1"] - p["b0"] < 2 or p["raw"] in ("end",) and p["b1"] - p["b0"] < 4):
            merged[-1]["b1"] = p["b1"]
            continue
        if merged and merged[-1]["raw"] == "start":
            p["b0"] = merged[-1]["b0"]
            merged[-1] = p
            continue
        merged.append(p)

    # group consecutive phrases with the same label into sections (unless the instrumentation changes)
    secs = []
    for p in merged:
        if secs and secs[-1]["raw"] == p["raw"] and _change(act, mix_levels, p["b0"]) < GROUP_SPLIT_SCORE:
            secs[-1]["b1"] = p["b1"]
            secs[-1]["phrases"].append([p["b0"], p["b1"]])
        else:
            cont = bool(secs) and secs[-1]["raw"] == p["raw"]
            secs.append({"b0": p["b0"], "b1": p["b1"], "raw": p["raw"], "phrases": [[p["b0"], p["b1"]]], "cont": cont})
    secs = _split_long(secs, act, mix_levels)

    def mean_act(gid, b0, b1):
        ids = [k for k, g in groups.items() if g == gid or k == gid]
        if not ids:
            return 0.0
        return float(max(np.mean(act[k][b0:b1]) for k in ids if k in act))

    lv = mix_levels
    audible = lv[lv > -100]
    emin, emax = (np.percentile(audible, 5), np.percentile(audible, 98)) if len(audible) else (-60.0, -20.0)
    for s in secs:
        b0, b1 = s["b0"], s["b1"]
        s["energy_db"] = round(float(np.mean(lv[b0:b1])), 1)
        s["energy"] = round(float(np.clip((np.mean(lv[b0:b1]) - emin) / (emax - emin + 1e-9), 0, 1)), 2)
        s["kick"] = round(mean_act("kick", b0, b1), 2)
        s["drums"] = round(mean_act("drums", b0, b1), 2)
        s["bass"] = round(mean_act("bass", b0, b1), 2)
        s["vocals"] = round(mean_act("vocals", b0, b1), 2)
        s["busy"] = round(max((float(np.mean(a[b0:b1])) for a in act.values()), default=0.0), 2)
    has_drums = any(s["kick"] >= 0.4 or s["drums"] >= 0.4 for s in secs)
    started = False                     # has the song proper begun (anything but intro/silence yet)?
    for i, s in enumerate(secs):
        near_end = s is secs[-1] or (len(secs) > 1 and s is secs[-2] and secs[-1]["energy"] < 0.1)
        # an outro the structure model spread over several changing phrases: all of them are Outro
        in_outro = s["raw"] in ("outro", "end") and all(t["raw"] in ("outro", "end") for t in secs[i + 1:])
        s["name"] = _name(s, is_first=s is secs[0], near_end=near_end or in_outro, edm=edm,
                          started=started, has_drums=has_drums)
        started = started or s["name"] not in ("Intro", "Silence")
    for s in secs:
        s.pop("cont", None)

    _build_ups(secs, lv, risers)
    counts, seen = {}, {}
    for s in secs:
        counts[s["name"]] = counts.get(s["name"], 0) + 1
    for s in secs:
        if counts[s["name"]] > 1:
            seen[s["name"]] = seen.get(s["name"], 0) + 1
            s["label"] = f"{s['name']} {seen[s['name']]}"
        else:
            s["label"] = s["name"]
        s["bars"] = s["b1"] - s["b0"]
        s["t0"] = round(grid.bar_start(s["b0"]), 3)
        s["t1"] = round(grid.bar_start(s["b1"]), 3)
        s["elements"] = [k for k in act if np.mean(act[k][s["b0"]:s["b1"]] > 0.3) >= 0.5]
    return secs


SPLIT_SCORE = 1.2        # ~two parts entering/leaving (or one plus a big level jump) marks a new section
GROUP_SPLIT_SCORE = 2.0  # phrases the model gave the same label need a bigger change to stay apart


def _change(act: dict[str, np.ndarray], lv: np.ndarray, b: int, w: int = 8) -> float:
    """How much the arrangement changes at bar b: parts clearly entering or leaving (8 bars
    either side). Small wobbles are ignored, so busy mixes with many parts don't add up noise."""
    lo, hi = max(0, b - w), min(len(lv), b + w)
    if b - lo < 2 or hi - b < 2:
        return 0.0
    d = [abs(float(np.mean(a[lo:b])) - float(np.mean(a[b:hi]))) for a in act.values()]
    return sum(x for x in d if x > 0.3) + abs(float(np.mean(lv[b:hi])) - float(np.mean(lv[lo:b]))) / 6.0


def _split_long(secs: list[dict], act: dict[str, np.ndarray], lv: np.ndarray, min_len: int = 8) -> list[dict]:
    """Structure models sometimes run one label over a big change (drums entering half-way
    through a 60-bar intro). Split sections longer than 24 bars at the strongest 4-bar-aligned
    change, recursively."""
    out = []
    for s in secs:
        if s["b1"] - s["b0"] <= 24:
            out.append(s)
            continue
        cands = [b for b in range(s["b0"] + min_len, s["b1"] - min_len + 1) if (b - s["b0"]) % 4 == 0]
        scores = [_change(act, lv, b) for b in cands]
        if not cands or max(scores) < SPLIT_SCORE:
            out.append(s)
            continue
        c = cands[int(np.argmax(scores))]
        first = dict(s, b1=c, phrases=[[a, min(b, c)] for a, b in s["phrases"] if a < c])
        second = dict(s, b0=c, cont=True, phrases=[[max(a, c), b] for a, b in s["phrases"] if b > c])
        out.extend(_split_long([first, second], act, lv, min_len))
    return out


def _name(s: dict, is_first: bool, near_end: bool, edm: bool, started: bool = True, has_drums: bool = True) -> str:
    """Readable section name from the structure model's label plus what actually plays.
    Dance-music names (Drop, Groove) only when the track is four-on-the-floor (edm).
    started: something other than an intro has already played; has_drums: the track has drums
    somewhere (a "breakdown" means the drums drop out)."""
    raw = s["raw"]
    e, kick, voc, bass = s["energy"], s["kick"], s["vocals"], s["bass"]
    if s.get("energy_db", 0) < -55 or (e < 0.04 and s.get("busy", 0) < 0.3):
        return "End" if near_end else "Silence"
    if raw in ("intro", "start"):
        if is_first or (s.get("cont") and not started and e < 0.55):
            return "Intro"                    # an intro in stages: Intro 1, Intro 2...
        if not s.get("cont"):
            return "Interlude"
        raw = "section"                       # the model's "intro" runs on into the track proper
    if raw in ("outro", "end"):
        return "Outro" if near_end else "Interlude"
    if is_first and e < 0.55:
        return "Intro"
    if near_end and e < 0.55:
        return "Outro"
    peak = e >= 0.75 and kick >= 0.4 and bass >= 0.3
    if raw == "chorus":
        return "Drop" if edm and voc < 0.35 and peak else "Chorus"
    if raw == "verse":
        return "Groove" if edm and voc < 0.25 and kick >= 0.4 else "Verse"
    if raw == "bridge":
        return "Bridge"
    if raw == "break" or (has_drums and raw in ("inst", "solo", "section") and kick < 0.25 and e < 0.65):
        return "Breakdown"
    if raw in ("solo", "inst", "section"):
        if voc >= 0.5:                        # labelled instrumental (or unlabelled), but someone is singing
            return ("Drop" if edm else "Chorus") if peak else "Verse"
        if edm and peak:
            return "Drop"
        if raw == "solo":
            return "Solo"
        if raw == "section" and edm and kick >= 0.4:
            return "Groove"
        return "Interlude" if raw == "section" and e < 0.45 else "Instrumental"
    return LABELS.get(raw, raw.title())


def _build_ups(secs: list[dict], lv: np.ndarray, builds: list[dict]) -> None:
    """Label (or split off) the phrase that ramps into a Drop/Chorus as a Build-up.

    Evidence: rising level over the last phrase, or a riser / snare roll ending there.
    """
    i = 1
    while i < len(secs):
        s, p = secs[i], secs[i - 1]
        intro_ok = p["name"] != "Intro" or p["b1"] - p["b0"] >= 16
        if s["name"] in ("Drop", "Chorus") and p["name"] not in ("Drop", "Chorus", "Build-up", "Silence") and intro_ok:
            L = min(8, p["b1"] - p["b0"])
            tail = lv[p["b1"] - L:p["b1"]]
            slope = np.polyfit(np.arange(L), tail, 1)[0] if L >= 3 else 0
            riser = any(r["bar1"] >= p["b1"] - 2 and r["bar1"] <= p["b1"] + 1 and r["bar0"] >= p["b0"] for r in builds)
            if slope > 0.35 or riser:
                if p["b1"] - p["b0"] <= 8 and p["name"] != "Intro":
                    p["name"] = "Build-up"
                elif L >= 4:
                    new = {k: v for k, v in p.items()}
                    new.update({"b0": p["b1"] - L, "name": "Build-up", "raw": "build",
                                "phrases": [[p["b1"] - L, p["b1"]]]})
                    p["b1"] = p["b1"] - L
                    p["phrases"] = [ph for ph in p["phrases"] if ph[0] < p["b1"]]
                    if p["phrases"]:
                        p["phrases"][-1][1] = min(p["phrases"][-1][1], p["b1"])
                    secs.insert(i, new)
                    i += 1
        i += 1


def fallback_phrases(n_bars: int, lv: np.ndarray, act: dict[str, np.ndarray]) -> list[dict]:
    """Without a structure model: 4-bar blocks, cut where the set of playing elements changes."""
    keys = list(act)
    M = np.array([act[k] for k in keys]) if keys else np.zeros((1, n_bars))
    cuts = [0]
    for b in range(4, n_bars, 4):
        prev = M[:, max(0, b - 4):b].mean(axis=1)
        nxt = M[:, b:b + 4].mean(axis=1)
        dl = abs(np.mean(lv[b:b + 4]) - np.mean(lv[max(0, b - 4):b]))
        if np.abs(prev - nxt).sum() > 1.0 or dl > 4:
            if b - cuts[-1] >= 4:
                cuts.append(b)
    cuts.append(n_bars)
    return [{"b0": a, "b1": b, "raw": "section"} for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
