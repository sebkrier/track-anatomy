"""Sound-design features for each element: width, envelope, timbre, pumping, tails, filter moves.

Everything here is a measurement on separated (hence imperfect) audio, so values are
reported as hints with the evidence behind them, not as ground truth.
"""
import numpy as np

from ..config import SR
from . import audio
from .fx import spearman

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_name(p: float) -> str:
    p = int(round(p))
    return f"{NOTE_NAMES[p % 12]}{p // 12 - 2}"   # Ableton convention: C3 = MIDI 60


def hz_to_midi(f: float) -> float:
    return 69 + 12 * np.log2(max(f, 1e-6) / 440.0)


def active_mask(env_db: np.ndarray, rel: float = 35.0) -> np.ndarray:
    ref = np.percentile(env_db, 97)
    return env_db > ref - rel


# ------------------------------------------------------------------ width
def stereo_width(x: np.ndarray, hop: int = 2048) -> dict:
    """Side/mid energy ratio over the part's active frames, plus L/R correlation.
    Computed frame by frame in blocks (no full-length float64 copies)."""
    if x.ndim == 1 or x.shape[1] == 1:
        return {"side_mid_db": -60.0, "corr": 1.0, "label": "mono"}
    n = (x.shape[0] // hop) * hop
    em, es = [], []
    lr = ll = rr = 0.0
    block = hop * 256
    for a in range(0, n, block):
        seg = x[a:min(n, a + block)].astype(np.float64)
        L, R = seg[:, 0], seg[:, 1]
        mid, side = (L + R) / 2, (L - R) / 2
        em.append((mid * mid).reshape(-1, hop).mean(1))
        es.append((side * side).reshape(-1, hop).mean(1))
        lr += float(np.dot(L, R))
        ll += float(np.dot(L, L))
        rr += float(np.dot(R, R))
    if not em:
        return {"side_mid_db": -60.0, "corr": 1.0, "label": "mono"}
    em, es = np.concatenate(em), np.concatenate(es)
    act = active_mask(audio.db(np.sqrt(em)), 30)
    if act.sum() < 10:
        return {"side_mid_db": -60.0, "corr": 1.0, "label": "mono"}
    r = 10 * np.log10(es[act].sum() / (em[act].sum() + 1e-12) + 1e-12)
    corr = lr / (np.sqrt(ll * rr) + 1e-12)
    label = "mono / centred" if r < -24 else "narrow" if r < -13 else "wide" if r < -5 else "very wide"
    return {"side_mid_db": round(float(r), 1), "corr": round(float(corr), 2), "label": label}


def low_end_width(x: np.ndarray) -> float | None:
    """Stereo width below ~120 Hz (should be close to mono for a bass)."""
    if x.ndim == 1:
        return None
    from scipy.signal import butter, resample_poly, sosfiltfilt
    # to ~1.1 kHz first: the anti-alias filter is cheap and the low-pass then runs on 40x fewer samples
    y = resample_poly(x.astype(np.float32), 1, 40, axis=0)
    fs = SR / 40
    lo = sosfiltfilt(butter(4, 120, btype="low", fs=fs, output="sos"), y, axis=0)
    return stereo_width(lo, hop=64)["side_mid_db"]


# ------------------------------------------------------------------ envelope
def envelope(m: np.ndarray, onsets: np.ndarray, offs: np.ndarray, durs: np.ndarray) -> dict:
    hop = 128
    env = audio.rms_env(m, hop=hop, win=512)
    edb = audio.db(env)
    fr = SR / hop
    atk, sus, tails = [], [], []
    order = np.argsort(onsets)
    onsets, offs, durs = onsets[order], offs[order], durs[order]
    for i, t in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else t + 5
        prv = onsets[i - 1] if i > 0 else -5
        if t - prv < 0.08:
            continue
        win = min(0.4, nxt - t - 0.01)
        if win < 0.08:
            continue
        a, b = int((t - 0.02) * fr), int((t + win) * fr)
        seg = env[max(a, 0):b]
        if len(seg) < 5 or seg.max() <= 0:
            continue
        k = int(np.argmax(seg))
        lo = np.nonzero(seg[:k + 1] >= 0.1 * seg[k])[0]
        hi = np.nonzero(seg[:k + 1] >= 0.9 * seg[k])[0]
        if len(lo) and len(hi):
            atk.append((hi[0] - lo[0]) / fr * 1000)
        if durs[i] >= 0.25 and nxt - t > 0.3:
            probe = t + min(durs[i] * 0.8, 0.6)
            j = int(probe * fr)
            if j < len(env):
                sus.append(float(edb[j] - audio.db(seg[k])))
        # tail after note-off with a clear gap
        gap = nxt - offs[i]
        if gap >= 0.5:
            s0 = int(offs[i] * fr)
            s1 = int(min(offs[i] + min(gap, 2.0), len(env) / fr) * fr)
            seg2 = edb[s0:s1]
            if len(seg2) > 20 and seg2[0] > -90:
                y = seg2 - seg2[0]
                # fit the decay between -3 and -33 dB (Schroeder-style)
                sel = (y < -3) & (y > -33)
                if sel.sum() > 8:
                    tt = np.arange(len(y))[sel] / fr
                    slope = np.polyfit(tt, y[sel], 1)[0]
                    if slope < -2:
                        tails.append(min(60.0 / -slope, 8.0))
    res = {}
    if len(atk) >= 4:
        a = float(np.median(atk))
        res["attack_ms"] = round(a, 1)
        res["attack_label"] = ("instant (< 10 ms)" if a < 10 else "fast" if a < 40 else
                               "soft" if a < 150 else "slow swell")
    if len(sus) >= 4:
        s = float(np.median(sus))
        res["sustain_db"] = round(s, 1)
        res["sustain_label"] = "sustained" if s > -6 else "decaying" if s > -18 else "plucky (no sustain)"
    if len(tails) >= 3:
        t = float(np.median(tails))
        res["tail_s"] = round(t, 2)
        res["tail_label"] = ("dry / tight" if t < 0.35 else "short (room / plate)" if t < 1.0 else
                             "medium hall" if t < 2.2 else "long hall / ambient")
    return res


# ------------------------------------------------------------------ timbre
def timbre(m: np.ndarray, notes: list[dict], sample: list[dict] | None = None) -> dict:
    """Harmonic profile from moments where exactly one note of the element sounds.

    `notes` (all of them) decide where only one note sounds; the harmonics are measured on
    `sample` (default: all notes). Only short windows around those notes are transformed, so
    this costs the same for a 3-minute or a 30-minute track."""
    from .fx import _frame_stats
    n_fft, hop = 8192, 2048

    # generic spectral shape on the louder frames (chunked, bounded memory)
    _, E, C, F = _frame_stats(m)
    out = {}
    if len(E) and E.max() > 0:
        act = E > np.percentile(E, 97) * 10 ** (-30 / 10)
        if act.sum() >= 8:
            out["centroid_hz"] = round(float(np.median(C[act])), 0)
            out["flatness"] = round(float(np.median(F[act])), 3)
    if not notes:
        return out
    sample = notes if sample is None else sample
    env = audio.rms_env(m, hop=hop, win=n_fft)
    loud = np.percentile(env, 97) * 10 ** (-30 / 20) if env.size else 0.0
    starts = np.sort([n["s"] for n in notes])
    ends = np.sort([n["e"] for n in notes])
    freqs_per_bin = SR / n_fft
    win = np.hanning(n_fft).astype(np.float32)

    profiles, noise, moves = [], [], []
    for n in sample:
        if n["e"] - n["s"] < 0.2:
            continue
        t0, t1 = n["s"] + 0.06, min(n["e"], n["s"] + 1.2)
        a = int(t0 * SR)
        nfr = max(0, int((t1 - t0) * SR - n_fft) // hop + 1)
        if nfr < 2 or a + n_fft > len(m):
            continue
        frame_t = t0 + (np.arange(nfr) * hop + n_fft / 2) / SR
        sounding = np.searchsorted(starts, frame_t, side="right") - np.searchsorted(ends, frame_t, side="right")
        ok = []
        for j in range(nfr):
            i0 = a + j * hop
            seg = m[i0:i0 + n_fft]
            if len(seg) < n_fft or sounding[j] != 1 or np.sqrt(np.mean(seg * seg)) < loud:
                continue
            ok.append(np.abs(np.fft.rfft(seg * win)))
        if len(ok) < 2:
            continue
        f0 = 440 * 2 ** ((n["p"] - 69) / 12)
        K = int(min(40, 16000 // f0))
        if K < 2:
            continue
        lev = np.zeros((len(ok), K))
        nz = []
        for j, col in enumerate(ok):
            for k in range(1, K + 1):
                c = k * f0 / freqs_per_bin
                w = max(2, c * 0.015)
                lo, hi = int(c - w), int(c + w) + 1
                lev[j, k - 1] = col[lo:hi].max() if hi > lo else 0
            mids = [(k + 0.5) * f0 / freqs_per_bin for k in range(1, min(K, 12))]
            nz.append(np.median([col[int(c)] for c in mids]) if mids else 0)
        ldb = audio.db(lev)
        ref = ldb[:, :4].max(axis=1, keepdims=True)
        prof = ldb - ref
        profiles.append(np.median(prof, axis=0))
        noise.append(float(np.median(audio.db(np.array(nz)) - ref[:, 0])))
        if len(ok) >= 6 and K >= 6:
            # movement: detrended dB fluctuation of harmonics 2..8 over time
            sd = []
            for k in range(1, min(8, K)):
                y = ldb[:, k]
                x = np.arange(len(y))
                y = y - np.polyval(np.polyfit(x, y, 1), x)
                sd.append(np.std(y))
            moves.append(float(np.median(sd)))
    if len(profiles) < 3:
        out["polyphonic"] = True
        return out
    Kmin = min(len(p) for p in profiles)
    Kmin = min(Kmin, 24)
    prof = np.median(np.array([p[:Kmin] for p in profiles]), axis=0)
    ks = np.arange(1, Kmin + 1)
    audible = prof > -30
    nh = int(audible.sum())
    sel = prof > -60
    slope = float(np.polyfit(np.log2(ks[sel]), prof[sel], 1)[0]) if sel.sum() >= 3 else -30.0
    ev = prof[1::2][:6]      # k = 2,4,6,...
    od = prof[2::2][:6]      # k = 3,5,7,...
    n_eo = min(len(ev), len(od))
    even_odd = float(np.mean(ev[:n_eo]) - np.mean(od[:n_eo])) if n_eo >= 2 else 0.0
    hnr = -float(np.median(noise))
    med_f0 = float(np.median([440 * 2 ** ((n["p"] - 69) / 12) for n in notes]))
    reach = float(ks[audible].max() * med_f0) if nh else med_f0

    if nh <= 2 and slope < -25:
        wave = "Sine-like (pure tone / sub)"
    elif even_odd <= -8:
        wave = "Triangle-like (odd harmonics, soft)" if slope <= -13 else "Square / pulse-like (odd harmonics)"
    elif slope >= -8:
        wave = "Saw-like, bright (all harmonics)"
    elif slope >= -14:
        wave = "Saw-like, filtered"
    else:
        wave = "Soft / heavily low-passed"
    mv = float(np.median(moves)) if moves else None
    out.update({
        "harmonics_db": [round(float(v), 1) for v in prof],
        "n_harmonics": nh, "slope_db_oct": round(slope, 1), "even_odd_db": round(even_odd, 1),
        "hnr_db": round(hnr, 1), "reach_hz": round(reach, 0), "wave": wave,
    })
    if hnr < 12:
        out["noisy"] = True
    if mv is not None:
        out["movement_db"] = round(mv, 2)
        out["movement_label"] = ("static (single oscillator, no modulation)" if mv < 1.2 else
                                 "some movement (chorus / slight detune / LFO)" if mv < 3 else
                                 "strong movement (unison detune / supersaw / heavy modulation)")
    return out


# ------------------------------------------------------------------ sidechain / pumping
def pumping(m: np.ndarray, notes: list[dict], grid, kick_times: np.ndarray, duration: float) -> dict | None:
    """Beat-synchronous level dip (sidechain pumping), measured only on beats where a held note
    spans the beat and no new note starts, so note attacks can't fake a pump."""
    hop = 256
    env = audio.rms_env(m, hop=hop, win=512)
    if env.size == 0 or env.max() <= 0:
        return None
    fr = SR / hop
    T = grid.period
    loud_floor = audio.db(np.percentile(env, 97)) - 25
    order = np.argsort([n["s"] for n in notes]) if notes else np.array([], int)
    starts = np.array([notes[i]["s"] for i in order])
    ends_cummax = np.maximum.accumulate(np.array([notes[i]["e"] for i in order])) if len(order) else np.array([])
    beats = grid.time(np.arange(0, grid.n_bars(duration) * grid.meter))
    shapes, with_kick = [], 0
    npts = 32
    for tb in beats:
        if tb <= 0 or tb + T >= duration:
            continue
        if len(starts):
            lo, hi = np.searchsorted(starts, [tb - 0.06, tb + 0.7 * T])
            if hi > lo:                          # a note starts near this beat
                continue
            k = int(np.searchsorted(starts, tb - 0.03))
            if k == 0 or ends_cummax[k - 1] <= tb + 0.6 * T:
                continue                         # nothing held across the beat
        a, b = int(tb * fr), int((tb + T) * fr)
        seg = env[a:b]
        if len(seg) < 8 or seg.max() <= 0:
            continue
        seg_db = audio.db(seg)
        if seg_db.max() < loud_floor:
            continue
        xs = np.linspace(0, len(seg) - 1, npts)
        shapes.append(np.interp(xs, np.arange(len(seg)), seg_db - seg_db.max()))
        if len(kick_times) and np.min(np.abs(kick_times - tb)) < 0.04:
            with_kick += 1
    if len(shapes) < 12:
        return None
    shape = np.median(np.array(shapes), axis=0)
    early = shape[: int(npts * 0.4)]
    imin = int(np.argmin(early))
    depth = float(shape.max() - early[imin])
    late = float(np.median(shape[int(npts * 0.75):]))
    detected = depth >= 2.5 and imin <= int(npts * 0.3) and late >= shape.max() - 2.5
    rec = np.nonzero(shape[imin:] >= shape.max() - 1.0)[0]
    rel_frac = float((imin + rec[0]) / npts) if len(rec) else 1.0
    return {
        "detected": bool(detected),
        "depth_db": round(depth, 1),
        "release_frac": round(rel_frac, 2),
        "release_ms": round(rel_frac * T * 1000, 0),
        "kick_locked": round(with_kick / len(shapes), 2),
        "beats_used": len(shapes),
        "shape": [round(float(v), 2) for v in shape],
    }


# ------------------------------------------------------------------ echo / delay
DELAY_LAGS = [(0.25, "1/16"), (1 / 3, "1/8 triplet"), (0.5, "1/8"), (2 / 3, "1/4 triplet"),
              (0.75, "dotted 1/8"), (1.0, "1/4"), (1.5, "dotted 1/4")]


def echo(m: np.ndarray, notes: list[dict], grid) -> dict | None:
    """Tempo-synced delay: onsets in the audio that repeat the played notes at a fixed lag.

    The note-onset train (strong notes only, since echoes are quieter) is cross-correlated
    with the audio's onset envelope. The rhythm itself also correlates at musical lags,
    so we look for correlation *in excess of* the train's own autocorrelation."""
    if len(notes) < 24:
        return None
    hop = 256
    fr = SR / hop
    env = audio.onset_env(m, hop=hop, n_mels=128)
    if env.size == 0 or env.max() <= 0:
        return None
    env = env / env.max()
    amps = np.array([n.get("a", 0.5) for n in notes])
    strong = [n for n, a in zip(notes, amps) if a >= np.median(amps)]
    train = np.zeros(len(env))
    for n in strong:
        i = int(n["s"] * fr)
        if 0 <= i < len(train):
            train[max(0, i - 1):i + 2] += 1
    if train.sum() == 0:
        return None
    def xc(a, b, L):
        if L >= 0:
            return float(np.dot(a[:len(a) - L], b[L:]))
        return float(np.dot(a[-L:], b[:len(b) + L]))

    # the onset envelope peaks a few frames off the note start: find that offset first
    offs = list(range(-10, 11))
    c0 = [xc(train, env, L) for L in offs]
    delta = offs[int(np.argmax(c0))]
    x0 = max(c0)
    t0 = xc(train, train, 0)
    if x0 <= 0 or t0 <= 0:
        return None
    T = grid.period

    def excess(lag_beats):
        L = int(round(lag_beats * T * fr))
        if L <= 4 or L >= len(env) // 4:
            return None
        x = max(xc(train, env, delta + L + j) for j in (-2, -1, 0, 1, 2))
        a = max(xc(train, train, L + j) for j in (-2, -1, 0, 1, 2))
        return x / x0 - a / t0

    cands = [(excess(l), l, name) for l, name in DELAY_LAGS]
    cands = [c for c in cands if c[0] is not None]
    if len(cands) < 4:
        return None
    top = max(cands)
    # an echo must be clearly present and stand out from the other musical lags
    med = float(np.median([max(0.0, c[0]) for c in cands]))
    thr = max(0.2, 2.5 * med)
    if top[0] < thr:
        return {"detected": False, "score": round(top[0], 3), "threshold": round(thr, 3)}
    # prefer the shortest lag that explains most of the peak (the delay time, not its repeats)
    best = min((c for c in cands if c[0] >= max(thr, 0.7 * top[0])), key=lambda c: c[1])
    e1 = best[0]
    e2 = excess(2 * best[1])
    fb = float(np.clip((e2 or 0) / e1, 0, 0.95)) if e1 > 0 else 0.0
    return {"detected": True, "label": best[2], "beats": round(best[1], 3), "ms": round(best[1] * T * 1000),
            "feedback": round(fb, 2), "score": round(best[0], 3), "threshold": round(thr, 3)}


# ------------------------------------------------------------------ filter movement
def filter_moves(m: np.ndarray, notes: list[dict], grid, duration: float) -> list[dict]:
    """Runs of 4-16 bars where brightness (relative to the pitch played) rises or falls steadily:
    the signature of filter-cutoff automation."""
    from .fx import _frame_stats
    n_bars = grid.n_bars(duration)
    if n_bars < 6:
        return []
    t, E, C, _ = _frame_stats(m)
    if not len(E) or E.max() <= 0:
        return []
    act = E > np.percentile(E, 97) * 10 ** (-30 / 10)
    bar_t = grid.time(np.arange(n_bars + 1) * grid.meter)
    idx = np.searchsorted(t, bar_t)
    order = np.argsort([n["s"] for n in notes]) if notes else np.array([], int)
    n_s = np.array([notes[i]["s"] for i in order])
    n_p = np.array([notes[i]["p"] for i in order])
    n_e = np.array([notes[i]["e"] for i in order])
    series = np.full(n_bars, np.nan)
    for b in range(n_bars):
        a, z = idx[b], idx[b + 1]
        sel = act[a:z]
        if sel.sum() < 3:
            continue
        e, c = E[a:z][sel], C[a:z][sel]
        cent = float((c * e).sum() / (e.sum() + 1e-12))
        if len(n_s):
            k = np.searchsorted(n_s, bar_t[b + 1])      # notes starting before the bar ends...
            live = n_e[:k] > bar_t[b]                   # ...and still sounding in it
            if not live.any():
                continue
            f0 = 440 * 2 ** ((np.median(n_p[:k][live]) - 69) / 12)
        else:
            f0 = 100.0
        series[b] = np.log2(max(cent, 1.0) / f0)
    events = []
    b = 0
    while b < n_bars - 3:
        best = None
        for L in range(16, 3, -1):
            seg = series[b:b + L]
            if len(seg) < L or np.isnan(seg).any():
                continue
            rho = spearman(np.arange(L), seg)
            k = max(1, L // 4)
            change = float(np.mean(seg[-k:]) - np.mean(seg[:k]))
            if abs(rho) >= 0.9 and abs(change) >= 1.0 and np.sign(rho) == np.sign(change):
                best = (L, change)
                break
        if best:
            L, change = best
            events.append({"bar0": b, "bar1": b + L - 1, "direction": "opens" if change > 0 else "closes",
                           "octaves": round(float(abs(change)), 2)})
            b += L
        else:
            b += 1
    return events


# ------------------------------------------------------------------ note statistics
def note_stats(notes: list[dict], grid) -> dict:
    if not notes:
        return {}
    p = np.array([n["p"] for n in notes])
    dur_b = np.array([n["be"] - n["bs"] for n in notes])
    starts = np.array([n["s"] for n in notes])
    ends = np.array([n["e"] for n in notes])
    # polyphony
    ev = sorted([(s, 1) for s in starts] + [(e, -1) for e in ends])
    cur = mx = 0
    acc = union = 0.0
    last = ev[0][0]
    for t, d in ev:
        if cur > 0:
            acc += cur * (t - last)
            union += t - last
        cur += d
        mx = max(mx, cur)
        last = t
    order = np.argsort(starts)
    legato = float(np.mean(ends[order][:-1] >= starts[order][1:] - 0.02)) if len(notes) > 2 else 0.0
    bars = np.floor(np.array([n["bs"] for n in notes]) / grid.meter)
    per_bar = np.bincount((bars - bars.min()).astype(int))
    per_bar = per_bar[per_bar > 0]
    md = float(np.median(dur_b))
    values = [(0.125, "1/32"), (0.25, "1/16"), (0.5, "1/8"), (0.75, "dotted 1/8"), (1, "1/4"), (1.5, "dotted 1/4"),
              (2, "1/2"), (4, "1 bar"), (8, "2 bars")]
    typical = min(values, key=lambda v: abs(np.log2(md / v[0])))[1]
    bends = np.array([abs(n.get("bend", 0)) for n in notes])
    return {
        "count": len(notes),
        "low": midi_name(p.min()), "high": midi_name(p.max()), "median": midi_name(np.median(p)),
        "low_midi": int(p.min()), "high_midi": int(p.max()),
        "max_polyphony": int(mx), "mean_polyphony": round(acc / union, 2) if union > 0 else 0,
        "mono": bool(mx <= 1 or np.mean(_overlaps(starts, ends)) < 0.1),
        "notes_per_bar": round(float(np.median(per_bar)), 1),
        "typical_length": typical, "median_len_beats": round(md, 3),
        "legato": round(legato, 2),
        "glide_fraction": round(float(np.mean(bends >= 0.7)), 2),
    }


def _overlaps(starts, ends):
    o = np.argsort(starts)
    s, e = starts[o], ends[o]
    return (s[1:] < e[:-1] - 0.03).astype(float) if len(s) > 1 else np.zeros(1)


# ------------------------------------------------------------------ drums
def drum_character(x: np.ndarray, hits: list[dict], piece: str) -> dict:
    m = audio.mono(x)
    strong = sorted(hits, key=lambda h: -h["vel"])[:40]
    if len(strong) < 3:
        return {}
    out = {}
    env = audio.rms_env(m, hop=64, win=256)
    fr = SR / 64
    decays = []
    for h in strong:
        a = int(h["t"] * fr)
        seg = audio.db(env[a:a + int(1.2 * fr)])
        if len(seg) < 10:
            continue
        k = int(np.argmax(seg[: int(0.05 * fr)]))
        below = np.nonzero(seg[k:] < seg[k] - 30)[0]
        if len(below):
            decays.append(below[0] / fr * 1000)
    if decays:
        out["decay_ms"] = round(float(np.median(decays)), 0)

    def spectrum(t0, length):
        segs = []
        for h in strong:
            a = int((h["t"] + t0) * SR)
            s = m[a:a + length]
            if len(s) == length:
                segs.append(np.abs(np.fft.rfft(s * np.hanning(length), n=max(length, 16384))))
        if not segs:
            return None, None
        f = np.fft.rfftfreq(max(length, 16384), 1 / SR)
        return f, np.median(np.array(segs), axis=0)

    if piece == "kick":
        f, sp = spectrum(0.03, 8192)
        if sp is not None:
            sel = (f > 30) & (f < 160)
            f0 = float(f[sel][np.argmax(sp[sel])])
            out["pitch_hz"] = round(f0, 1)
            out["pitch_note"] = midi_name(hz_to_midi(f0))
        f2, sp2 = spectrum(0.0, 1024)
        if sp2 is not None:
            sel = (f2 > 40) & (f2 < 400)
            out["start_hz"] = round(float(f2[sel][np.argmax(sp2[sel])]), 0)
            hi = sp2[f2 > 2000].sum() / (sp2.sum() + 1e-9)
            out["click"] = round(float(hi), 3)
        d = out.get("decay_ms", 0)
        out["style"] = ("808-style long boom" if d > 450 else "punchy / 909-style" if d > 180 else "short / tight")
    elif piece == "snare":
        f, sp = spectrum(0.005, 4096)
        if sp is not None:
            sel = (f > 120) & (f < 450)
            out["tone_hz"] = round(float(f[sel][np.argmax(sp[sel])]), 0)
            out["noise_ratio"] = round(float(sp[f > 2000].sum() / (sp.sum() + 1e-9)), 2)
        # clap = several bursts in the first ~40 ms
        bursts = []
        for h in strong[:20]:
            a = int(h["t"] * fr)
            seg = env[a:a + int(0.045 * fr)]
            if len(seg) < 10:
                continue
            from scipy.signal import find_peaks
            pk, _ = find_peaks(seg, prominence=seg.max() * 0.15, distance=int(0.004 * fr))
            bursts.append(len(pk))
        if bursts:
            out["clap_like"] = bool(np.median(bursts) >= 2.5)
    elif piece in ("hh", "ride", "crash"):
        f, sp = spectrum(0.0, 2048)
        if sp is not None:
            out["centroid_khz"] = round(float((f * sp).sum() / (sp.sum() + 1e-9) / 1000), 1)
        if piece == "hh":
            opens = [h for h in hits if h.get("note") == 46]
            out["open_ratio"] = round(len(opens) / max(len(hits), 1), 2)
    return out
