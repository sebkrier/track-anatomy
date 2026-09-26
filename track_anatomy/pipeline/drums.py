"""Drum hits, step patterns, swing and feel from the DrumSep sub-stems."""
import librosa
import numpy as np

from ..config import SR
from . import audio

HOP = 256
PIECES = {
    # id: (display name, GM / Drum Rack note, onset band (lo, hi), min gap s, keep threshold dB below typical hit)
    "kick":  ("Kick", 36, (None, 200), 0.06, 16),
    "snare": ("Snare / Clap", 38, (150, None), 0.05, 17),
    "toms":  ("Toms", 45, (60, 1200), 0.05, 13),
    "hh":    ("Hi-hats", 42, (3000, None), 0.03, 17),
    "ride":  ("Ride", 51, (2500, None), 0.04, 15),
    "crash": ("Crash / Cymbals", 49, (2500, None), 0.08, 12),
}
OPEN_HAT = 46
TOM_NOTES = [43, 45, 48]   # low / mid / high tom


def detect_hits(x: np.ndarray, piece: str) -> list[dict]:
    """Onsets with level (dBFS at working gain) for one drum sub-stem."""
    name, note, (lo, hi), gap, keep_db = PIECES[piece]
    m = audio.mono(x)
    if audio.rms_db(m) < -75:
        return []
    y = audio.band_causal(m, lo, hi) if (lo or hi) else m
    env = audio.onset_env(y, hop=HOP, n_mels=64)
    del y
    if env.size == 0 or env.max() <= 0:
        return []
    wait = max(1, int(gap * SR / HOP))
    on = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, units="frames",
                                    backtrack=False, wait=wait, delta=0.04, pre_max=2, post_max=2,
                                    pre_avg=12, post_avg=4)
    if len(on) == 0:
        return []
    r = audio.rms_env(m, hop=64, win=256)                        # 1.45 ms frames
    hits = []
    for f in on:
        t = f * HOP / SR
        # spectral-flux onsets lag for low drums (46 ms mel window), so look back further
        a = int(max(0, (t - 0.05)) * SR / 64)
        b = int((t + 0.045) * SR / 64)
        seg = r[a:b]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg))
        tpk = (a + k) * 64 / SR
        # onset = walk back from the peak until the envelope falls below 20% of it (or turns up again)
        j = k
        while j > 0 and seg[j - 1] > 0.2 * seg[k] and seg[j - 1] <= seg[j] * 1.05:
            j -= 1
        t_on = (a + j) * 64 / SR
        hits.append({"t": float(t_on), "peak_t": float(tpk), "level": float(audio.db(seg[k]))})
    # the same transient can be found twice after the look-back; keep the louder
    hits.sort(key=lambda x: x["t"])
    dedup = []
    for x in hits:
        if dedup and x["t"] - dedup[-1]["t"] < gap * 0.5:
            if x["level"] > dedup[-1]["level"]:
                dedup[-1] = x
            continue
        dedup.append(x)
    hits = dedup
    if not hits:
        return []
    lv = np.array([h["level"] for h in hits])
    typical = np.percentile(lv, 90)
    hits = [h for h in hits if h["level"] >= typical - keep_db and h["level"] > -62]
    # velocity: map level relative to typical hit onto 1..127 (~2.5 velocity steps per dB)
    for h in hits:
        h["rel"] = h["level"] - typical
        h["vel"] = int(np.clip(108 + h["rel"] * 2.6, 20, 127))
    if piece == "hh":
        _classify_hats(r, hits)
    if piece == "toms":
        _tom_pitches(m, hits)
    return hits


def _classify_hats(r: np.ndarray, hits: list[dict]) -> None:
    """Open vs closed hat from how long the hit rings."""
    times = np.array([h["t"] for h in hits])
    for i, h in enumerate(hits):
        nxt = times[i + 1] if i + 1 < len(times) else h["t"] + 1.0
        pk = h["peak_t"]
        probe = pk + 0.11
        h["open"] = False
        if nxt - h["t"] < 0.13:
            continue
        a, b = int(probe * SR / 64), int((probe + 0.03) * SR / 64)
        pa = int(pk * SR / 64)
        if b >= len(r) or pa >= len(r):
            continue
        drop = audio.db(r[pa]) - audio.db(r[a:b].mean())
        h["open"] = bool(drop < 11)


def _tom_pitches(m: np.ndarray, hits: list[dict]) -> None:
    f = []
    for h in hits:
        a = int((h["t"] + 0.02) * SR)
        seg = m[a:a + 4096]
        if len(seg) < 4096:
            f.append(np.nan)
            continue
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1 / SR)
        sel = (freqs > 60) & (freqs < 500)
        f.append(float(freqs[sel][np.argmax(spec[sel])]))
    f = np.array(f)
    ok = ~np.isnan(f)
    if ok.sum() < 3 or np.nanmax(f) / max(np.nanmin(f), 1) < 1.2:
        for h in hits:
            h["note"] = 45
        return
    q = np.nanpercentile(f, [33, 67])
    for h, fr in zip(hits, f):
        h["note"] = 45 if np.isnan(fr) else TOM_NOTES[int(np.searchsorted(q, fr))]


def choose_subdivision(beats_pos: np.ndarray) -> int:
    """Steps per beat: 4 (16ths) unless a triplet grid fits clearly better."""
    if len(beats_pos) < 16:
        return 4
    def err(S):
        v = beats_pos * S
        return float(np.mean(np.abs(v - np.round(v)) / S))
    e4, e3, e6 = err(4), err(3), err(6)
    if e6 < 0.55 * e4:
        return 3 if e3 < 1.25 * e6 else 6
    return 4


def detect_all(get_audio, names: list[str]) -> dict[str, list[dict]]:
    """Hits per piece, with cross-piece bleed removed (grid independent).

    get_audio(name) returns that piece's audio; pieces are loaded one at a time."""
    hits = {}
    for p in names:
        h = detect_hits(get_audio(p), p)
        if len(h) >= 8:
            hits[p] = h
    # DrumSep leaks loud hits into neighbouring stems: a quiet hit that coincides with a
    # strong hit in another piece is bleed, not a real note.
    strong = {p: np.array([h["t"] for h in hs if h["rel"] > -6]) for p, hs in hits.items()}
    for p, hs in hits.items():
        keep = []
        for h in hs:
            bleed = False
            if h["rel"] < -8:
                for q, ts in strong.items():
                    if q != p and len(ts) and np.min(np.abs(ts - h["t"])) < 0.015:
                        bleed = True
                        break
            if not bleed:
                keep.append(h)
        hits[p] = keep
    # toms that land exactly on kick/snare hits are the kick/snare body leaking into the toms stem
    if "toms" in hits:
        ref = np.array(sorted(h["t"] for q in ("kick", "snare") for h in hits.get(q, [])))
        if len(ref):
            hits["toms"] = [h for h in hits["toms"] if np.min(np.abs(ref - h["t"])) > 0.02]
    return {p: hs for p, hs in hits.items() if len(hs) >= 8}


def analyze(hits: dict[str, list[dict]], grid, duration: float) -> dict:
    meter = grid.meter
    n_bars = grid.n_bars(duration)
    if not hits:
        return {"present": False, "pieces": [], "patterns": [], "bar_pattern": [], "hits": {}}

    all_pos = np.concatenate([grid.beat(np.array([h["t"] for h in hs])) for hs in hits.values()])
    S = choose_subdivision(all_pos)
    steps = meter * S

    out_hits = {}
    for p, hs in hits.items():
        b = grid.beat(np.array([h["t"] for h in hs]))
        for h, bb in zip(hs, b):
            h["beat"] = float(bb)
            q = round(bb * S) / S
            h["q"] = float(q)
            h["dev"] = float(bb - q)           # in beats, + = late
        out_hits[p] = hs

    # swing & feel -------------------------------------------------------
    ref = "hh" if "hh" in out_hits and len(out_hits["hh"]) > 32 else None
    pool = out_hits[ref] if ref else [h for hs in out_hits.values() for h in hs]
    frac = np.array([h["beat"] % 1.0 for h in pool])
    swing = None
    need = max(16, int(0.15 * len(frac)))
    # measure swing relative to where the same instrument sits on the straight positions,
    # so a laid-back or early part is not mistaken for swing
    even = frac[(frac < 0.1) | (frac > 0.9) | ((frac > 0.4) & (frac < 0.6))]
    even_dev = np.where(even > 0.75, even - 1.0, np.where(even > 0.25, even - 0.5, even))
    base = float(np.median(even_dev)) if len(even_dev) >= 16 else 0.0
    if S == 4:
        odd16 = frac[((frac > 0.125) & (frac < 0.40)) | ((frac > 0.625) & (frac < 0.90))]
        if len(odd16) >= need:
            dev = np.where(odd16 < 0.5, odd16 - 0.25, odd16 - 0.75) - base
            med = float(np.median(dev))
            if np.median(np.abs(dev - med)) < 0.04:
                swing = {"grid": "16th", "percent": round(50 + 200 * med, 1), "n": int(len(odd16))}
        if swing is None:
            off8 = frac[(frac > 0.40) & (frac < 0.66)]
            on = frac[(frac < 0.1) | (frac > 0.9)]
            base8 = float(np.median(np.where(on > 0.5, on - 1.0, on))) if len(on) >= 16 else 0.0
            if len(off8) >= need:
                dev = off8 - 0.5 - base8
                med = float(np.median(dev))
                if np.median(np.abs(dev - med)) < 0.05:
                    swing = {"grid": "8th", "percent": round(50 + 100 * med, 1), "n": int(len(off8))}
    # below 50% is not a swing setting anyone uses: it's a part played ahead of the beat (see feel_ms)
    if swing and swing["percent"] < 52.5:
        swing["percent"] = 50.0
        swing["straight"] = True
    # groove template: median timing offset and velocity at each step of the bar
    # (hats carry the feel in most styles; otherwise all pieces)
    gpool = out_hits["hh"] if ref else [h for hs in out_hits.values() for h in hs]
    g_off = [[] for _ in range(steps)]
    g_vel = [[] for _ in range(steps)]
    for h in gpool:
        pos = int(round(h["beat"] * S)) % steps
        g_off[pos].append(h["dev"])
        g_vel[pos].append(h["vel"])
    groove = None
    if sum(len(o) for o in g_off) >= 32:
        offs = [float(np.median(o)) if len(o) >= 4 else 0.0 for o in g_off]
        vels = [float(np.median(v)) if len(v) >= 4 else 0.0 for v in g_vel]
        vmax = max(vels) or 1.0
        groove = {"steps": steps, "steps_per_beat": S,
                  "offsets": [round(o, 4) for o in offs],
                  "velocities": [int(round(40 + 87 * v / vmax)) if v else 64 for v in vels]}

    feel = {}
    for p, hs in out_hits.items():
        on_beat = [h["dev"] for h in hs if abs(h["q"] - round(h["q"])) < 1e-6]
        if len(on_beat) >= 8:
            feel[p] = round(float(np.median(on_beat)) * grid.period * 1000, 1)   # ms, + = behind

    # per-bar step grids ---------------------------------------------------
    bars = [dict() for _ in range(max(n_bars, 1))]
    for p, hs in out_hits.items():
        for h in hs:
            step_total = int(round(h["beat"] * S))
            bar, step = divmod(step_total, steps)
            if bar < 0 or bar >= len(bars):
                continue
            note = h.get("note", PIECES[p][1])
            if p == "hh" and h.get("open"):
                note = OPEN_HAT
            key = _lane_key(p, note)
            lane = bars[bar].setdefault(key, [0] * steps)
            lane[step] = max(lane[step], h["vel"])

    # which lanes are "core" (define the groove) vs accents/fills
    lanes = sorted({k for b in bars for k in b}, key=_lane_order)
    drum_bars = [i for i, b in enumerate(bars) if b]
    core = set()
    for k in lanes:
        n = sum(1 for i in drum_bars if k in bars[i])
        if drum_bars and n / len(drum_bars) >= 0.35:
            core.add(k)
    if not core:
        core = set(lanes)

    # Patterns are grouped by their kick + snare backbone; hats and percussion vary too much
    # bar to bar (ghost notes, opens, fills) and would make every bar unique. The pattern grid
    # still shows every lane that plays in at least half of the pattern's bars.
    backbone = {k for k in core if k.split(":")[0] in ("kick", "snare")} or core

    def sig(b):
        s = frozenset((k, st) for k, v in b.items() if k in backbone for st, vel in enumerate(v) if vel)
        if not s:   # e.g. hats-only bars
            s = frozenset((k, st) for k, v in b.items() if k in core for st, vel in enumerate(v) if vel)
        return s

    sigs = [sig(b) if b else None for b in bars]
    centers = _cluster_bars(sigs)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    bar_pattern = ["" for _ in bars]
    pattern_out = []
    for n, c in enumerate([c for c in centers if len(c["bars"]) >= 2][:12]):
        pid = letters[n]
        exact = [b for b in c["bars"] if sigs[b] == c["sig"]]
        for b in c["bars"]:
            bar_pattern[b] = pid if sigs[b] == c["sig"] else pid + "'"
        # representative grid: steps hit in >= half of the member bars, median velocity
        rep = {}
        for k in lanes:
            arr = np.array([bars[b].get(k, [0] * steps) for b in c["bars"]])
            present = (arr > 0).mean(axis=0)
            with np.errstate(all="ignore"):
                med = np.nanmedian(np.where(arr > 0, arr, np.nan), axis=0)
            vel = np.nan_to_num(np.where(present >= 0.5, med, 0))
            if vel.any():
                rep[k] = [int(v) for v in vel]
        pattern_out.append({
            "id": pid, "bars": sorted(c["bars"]), "count": len(c["bars"]), "exact": len(exact),
            "grid": rep, "density": sum(1 for v in rep.values() for s in v if s),
        })
    for i, s in enumerate(sigs):
        if s and not bar_pattern[i]:
            bar_pattern[i] = "*"      # one-off bar (fill / transition)

    fills = [i for i in drum_bars if bar_pattern[i] in ("*",) or
             (bars[i] and any(k not in core for k in bars[i]) and _is_tom(bars[i]))]

    lane_meta = []
    for k in lanes:
        piece, note = k.split(":")
        lane_meta.append({"key": k, "piece": piece, "note": int(note), "label": _lane_label(piece, int(note)),
                          "core": k in core})

    return {
        "present": True,
        "steps_per_beat": S,
        "steps_per_bar": steps,
        "swing": swing,
        "feel_ms": feel,
        "groove": groove,
        "lanes": lane_meta,
        "patterns": pattern_out,
        "bar_pattern": bar_pattern,
        "bar_grids": [{k: v for k, v in b.items()} for b in bars],
        "fills": fills,
        "hits": {p: [{"t": round(h["t"], 4), "beat": round(h["beat"], 4), "vel": h["vel"],
                      "note": (OPEN_HAT if (p == "hh" and h.get("open")) else h.get("note", PIECES[p][1]))}
                     for h in hs] for p, hs in out_hits.items()},
    }


def _cluster_bars(sigs: list) -> list[dict]:
    """Group bars whose core hits differ by at most ~12% (min 1 hit); majority-vote centres."""
    freq = {}
    for i, s in enumerate(sigs):
        if s:
            freq.setdefault(s, []).append(i)
    order = sorted(freq, key=lambda s: -len(freq[s]))
    centers: list[dict] = []

    def tol(a, b):
        return max(1, round(0.12 * max(len(a), len(b))))

    for s in order:
        best, bd = None, None
        for c in centers:
            dist = len(s ^ c["sig"])
            if dist <= tol(s, c["sig"]) and (bd is None or dist < bd):
                best, bd = c, dist
        if best is None:
            centers.append({"sig": s, "bars": list(freq[s])})
        else:
            best["bars"].extend(freq[s])
    # two refinement passes: majority centre, then reassign
    for _ in range(2):
        for c in centers:
            if not c["bars"]:
                continue
            cnt = {}
            for b in c["bars"]:
                for x in sigs[b]:
                    cnt[x] = cnt.get(x, 0) + 1
            maj = frozenset(x for x, n in cnt.items() if n >= len(c["bars"]) / 2)
            if maj:
                c["sig"] = maj
            c["bars"] = []
        for i, s in enumerate(sigs):
            if not s:
                continue
            dists = [(len(s ^ c["sig"]), k) for k, c in enumerate(centers)]
            d, k = min(dists)
            if d <= tol(s, centers[k]["sig"]):
                centers[k]["bars"].append(i)
            else:
                centers.append({"sig": s, "bars": [i]})
        centers = [c for c in centers if c["bars"]]
    return sorted(centers, key=lambda c: (-len(c["bars"]), min(c["bars"])))


def _lane_key(piece: str, note: int) -> str:
    return f"{piece}:{note}"


def _lane_order(k: str) -> tuple:
    order = ["kick", "snare", "toms", "hh", "ride", "crash"]
    p, n = k.split(":")
    return (order.index(p) if p in order else 9, int(n))


def _lane_label(piece: str, note: int) -> str:
    if piece == "hh":
        return "Open hat" if note == OPEN_HAT else "Closed hat"
    if piece == "toms":
        return {43: "Low tom", 45: "Tom", 48: "High tom"}.get(note, "Tom")
    return PIECES[piece][0]


def _is_tom(bar: dict) -> bool:
    return any(k.startswith("toms") for k in bar)
