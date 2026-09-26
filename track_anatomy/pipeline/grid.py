"""Beat grid: tempo, meter, bar 1, and time <-> beat mapping.

Beats come from beat_this. For steady-tempo music (most electronic, pop and hip hop)
a straight line is fitted through the beats, which removes tracker jitter. When the
tempo drifts (live bands, rubato) the tracked beats are used directly, with gaps filled.
All positions downstream are expressed in beats since bar 1, so exported MIDI lines up
in Ableton at the detected BPM even when the recording drifts.
"""
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Grid:
    times: np.ndarray          # beat times (s), covering a margin before 0 and after the end
    origin: int                # index into `times` of the bar-1 downbeat
    meter: int                 # beats per bar
    bpm: float
    steady: bool
    confidence: float
    notes: list = field(default_factory=list)
    has_beat: bool = True       # False when no pulse was found and the grid is a placeholder

    @property
    def period(self) -> float:
        return 60.0 / self.bpm

    def beat(self, t):
        """Beats since bar 1 for time(s) t; linear extrapolation outside the grid."""
        t = np.asarray(t, dtype=np.float64)
        idx = np.arange(len(self.times), dtype=np.float64) - self.origin
        b = np.interp(t, self.times, idx)
        lo, hi = self.times[0], self.times[-1]
        p0 = self.times[1] - self.times[0]
        p1 = self.times[-1] - self.times[-2]
        b = np.where(t < lo, idx[0] + (t - lo) / p0, b)
        b = np.where(t > hi, idx[-1] + (t - hi) / p1, b)
        return b

    def time(self, b):
        b = np.asarray(b, dtype=np.float64)
        idx = np.arange(len(self.times), dtype=np.float64) - self.origin
        t = np.interp(b, idx, self.times)
        p0 = self.times[1] - self.times[0]
        p1 = self.times[-1] - self.times[-2]
        t = np.where(b < idx[0], self.times[0] + (b - idx[0]) * p0, t)
        t = np.where(b > idx[-1], self.times[-1] + (b - idx[-1]) * p1, t)
        return t

    def bar_start(self, bar: int) -> float:
        """Start time of 0-based bar index (bar 0 == 'bar 1' in the UI)."""
        return float(self.time(bar * self.meter))

    def n_bars(self, duration: float) -> int:
        return int(np.ceil(float(self.beat(duration)) / self.meter - 1e-6))

    def to_json(self, duration: float) -> dict:
        nb = self.n_bars(duration)
        return {
            "bpm": round(self.bpm, 2),
            "meter": self.meter,
            "steady": self.steady,
            "confidence": round(self.confidence, 2),
            "has_beat": self.has_beat,
            "origin_time": round(float(self.times[self.origin]), 4),
            "n_bars": nb,
            "beats": [round(float(t), 4) for t in self.time(np.arange(-self.meter, nb * self.meter + 1))],
            "first_beat": -self.meter,
            "notes": self.notes,
        }


def fix_octave_errors(beats: np.ndarray) -> tuple[np.ndarray, int]:
    """Beat trackers sometimes switch to double (or half) time for a stretch.
    Walk the beats with a slowly adapting period: drop beats that come too early,
    fill gaps with interpolated beats. Returns (beats, number of corrections)."""
    if len(beats) < 8:
        return beats, 0
    T = float(np.median(np.diff(beats)))
    out = [beats[0]]
    fixes = 0
    for b in beats[1:]:
        d = b - out[-1]
        if d < 0.7 * T:
            fixes += 1
            continue
        n = int(round(d / T))
        if n >= 2 and abs(d / n - T) < 0.2 * T:
            out.extend(out[-1] + (d / n) * np.arange(1, n))
            fixes += 1
        out.append(b)
        step = d / max(n, 1)
        if abs(step - T) < 0.15 * T:
            T = 0.9 * T + 0.1 * step
    return np.array(out), fixes


def _local_period(ibi: np.ndarray, k: int, w: int = 8) -> float:
    lo, hi = max(0, k - w), min(len(ibi), k + w + 1)
    return float(np.median(ibi[lo:hi]))


def _comb_fit(beats: np.ndarray, T0: float) -> tuple[float, float]:
    """One period and phase for the whole track: maximise phase coherence of the beats
    over a fine period search around T0, then least-squares refine on the beats that land
    on that grid. Unlike a line fit through sequential indices, a missed, doubled or
    half-beat-shifted stretch (tracker locking onto off-beats in a breakdown) only lowers
    the score; it can't bend the fit. Returns (a, T) with beats ~ a + k*T."""
    span = max(float(beats[-1] - beats[0]), 1.0)
    step = 0.02 * T0 * T0 / span                 # phase error < 2% of a beat across the track
    Ts = np.arange(0.97 * T0, 1.03 * T0, step)
    best_R, T = -1.0, T0
    for i in range(0, len(Ts), 256):
        Tc = Ts[i:i + 256, None]
        R = np.abs(np.exp(2j * np.pi * beats[None, :] / Tc).mean(axis=1))
        j = int(R.argmax())
        if R[j] > best_R:
            best_R, T = float(R[j]), float(Tc[j, 0])
    a = float(np.angle(np.exp(2j * np.pi * beats / T).mean()) / (2 * np.pi) * T)
    for _ in range(3):
        k = np.round((beats - a) / T)
        inl = np.abs(beats - (a + k * T)) < 0.05
        if inl.sum() < 8:
            break
        A = np.vstack([np.ones(inl.sum()), k[inl]]).T
        (a, T), *_ = np.linalg.lstsq(A, beats[inl], rcond=None)
    return float(a), float(T)


def _residuals(beats: np.ndarray, a: float, T: float) -> np.ndarray:
    return beats - (a + np.round((beats - a) / T) * T)


def _drift(res: np.ndarray, inl: np.ndarray, w: int = 16) -> float:
    """Largest systematic offset of on-grid beats, smoothed over ~w beats (catches slow tempo drift)."""
    r = res[inl]
    if len(r) < 2 * w:
        return 0.0
    return float(np.max(np.abs(np.convolve(r, np.ones(w) / w, mode="valid"))))


def _snap_bpm(beats: np.ndarray, a: float, T: float, inl: np.ndarray, drift: float) -> tuple[float, float]:
    """Productions almost always use a whole-number tempo: use it when it fits as well."""
    bpm = 60.0 / T
    nb = round(bpm)
    if abs(bpm - nb) >= 0.05 or nb <= 0:
        return a, T
    Ts = 60.0 / nb
    k = np.round((beats - a) / Ts)
    a2 = float(np.median((beats - k * Ts)[inl]))
    res2 = _residuals(beats, a2, Ts)
    inl2 = np.abs(res2) < 0.05
    if inl2.mean() >= inl.mean() - 0.02 and _drift(res2, inl2) < max(0.025, drift + 0.005):
        return a2, Ts
    return a, T


def build(beats, downbeats, duration: float, first_onset: float,
          tempo_factor: float = 1.0, downbeat_shift: int = 0, meter: int | None = None) -> Grid:
    beats = np.sort(np.asarray(beats, dtype=np.float64))
    downbeats = np.sort(np.asarray(downbeats, dtype=np.float64))
    notes = []

    if len(beats) < 8:
        # No usable pulse (ambient, spoken word...). Fall back to 120 BPM from 0.
        T = 0.5
        n = int(duration / T) + 16
        times = np.arange(-8, n) * T
        return Grid(times, 8, meter or 4, 120.0, True, 0.0,
                    ["No clear beat found; the grid is a 120 BPM placeholder."], has_beat=False)

    beats, fixes = fix_octave_errors(beats)
    if fixes > 8:
        notes.append(f"Beat tracker switched to double/half time in places; {fixes} beats were corrected.")

    if tempo_factor == 2.0:
        mids = (beats[:-1] + beats[1:]) / 2
        beats = np.sort(np.concatenate([beats, mids]))
        notes.append("Tempo doubled by user override.")
    elif tempo_factor == 0.5:
        # keep the beat phase that contains most downbeats
        def on_downbeats(ph: int) -> int:
            sel = beats[ph::2]
            return sum(np.min(np.abs(sel - d)) < 0.07 for d in downbeats) if len(downbeats) else 0
        beats = beats[int(on_downbeats(1) > on_downbeats(0))::2]
        notes.append("Tempo halved by user override.")

    ibi = np.diff(beats)
    # sequential beat indices, tolerant of missed (gap -> skip index) and spurious (same index) beats
    idx = np.zeros(len(beats))
    for k in range(1, len(beats)):
        Tl = _local_period(ibi, k - 1)
        idx[k] = idx[k - 1] + max(0, int(round(ibi[k - 1] / Tl)))
    keep = np.concatenate([[True], np.diff(idx) > 0])
    beats, idx = beats[keep], idx[keep]

    # one tempo for the whole track? (comb fit; see _comb_fit)
    w = min(16, len(beats) - 1)
    T0 = float(np.median((beats[w:] - beats[:-w]) / np.maximum(idx[w:] - idx[:-w], 1)))
    a, T = _comb_fit(beats, T0)
    res = _residuals(beats, a, T)
    inl = np.abs(res) < 0.05
    res_std = float(np.std(res[inl])) if inl.sum() > 1 else 1.0
    drift = _drift(res, inl)
    # most beats on one straight grid (a breakdown where the tracker wanders is fine), tight, no slow drift
    steady = inl.mean() >= 0.7 and res_std < 0.022 and drift < 0.035
    margin = 8

    if steady:
        a, T = _snap_bpm(beats, a, T, inl, drift)
        i0 = int(np.floor((-4.0 - a) / T)) - margin
        i1 = int(np.ceil((duration + 4.0 - a) / T)) + margin
        times = a + T * np.arange(i0, i1 + 1)
        offs = -i0  # grid index of k == 0
        bpm = 60.0 / T
        confidence = float(np.mean(np.abs(_residuals(beats, a, T)) < 0.05))
    else:
        full_idx = np.arange(idx[0], idx[-1] + 1)
        tt = np.interp(full_idx, idx, beats)
        T0 = float(np.median(np.diff(tt[:9]))) if len(tt) > 9 else float(np.median(ibi))
        T1 = float(np.median(np.diff(tt[-9:]))) if len(tt) > 9 else float(np.median(ibi))
        n_pre = int(np.ceil((tt[0] + 4.0) / T0)) + margin
        n_post = int(np.ceil((duration + 4.0 - tt[-1]) / T1)) + margin
        pre = tt[0] - T0 * np.arange(n_pre, 0, -1)
        post = tt[-1] + T1 * np.arange(1, n_post + 1)
        times = np.concatenate([pre, tt, post])
        offs = n_pre
        # average tempo = slope through all beats (the median beat interval is quantised to the
        # tracker's 20 ms frames, which would report e.g. 136.36 for a 136 BPM track)
        bpm = 60.0 / float(np.polyfit(full_idx, tt, 1)[0])
        confidence = float(max(0.3, 1.0 - res_std * 10) * min(1.0, 0.4 + inl.mean()))
        notes.append("Tempo varies (live or rubato performance); the grid follows the tracked beats. "
                     "In Ableton, warp the audio stems to the grid or use the exported warp-marker set.")
        idx = idx - idx[0]

    # meter from downbeat spacing
    grid_idx_of = lambda t: int(np.argmin(np.abs(times - t)))
    if meter is None:
        counts = []
        for d0, d1 in zip(downbeats[:-1], downbeats[1:]):
            c = grid_idx_of(d1) - grid_idx_of(d0)
            if 2 <= c <= 7:
                counts.append(c)
        meter = int(np.bincount(counts).argmax()) if counts else 4
        if tempo_factor == 2.0:
            meter = meter * 2 if meter <= 3 else meter
        # downbeats every 2 beats: nearly always a 4/4 track where the tracker marks half bars,
        # and 4-beat bars are what you'd program in Ableton anyway
        if meter == 2:
            meter = 4
    # downbeat phase vote
    votes = np.zeros(meter)
    for d in downbeats:
        j = grid_idx_of(d)
        if abs(times[j] - d) < 0.08:
            votes[j % meter] += 1
    phase = int(votes.argmax()) if votes.sum() else offs % meter
    db_agree = float(votes.max() / votes.sum()) if votes.sum() else 0.0
    phase = (phase + downbeat_shift) % meter

    # bar 1 = last downbeat at or before the first sound
    cands = np.array([j for j in range(len(times)) if j % meter == phase])
    before = cands[times[cands] <= first_onset + 0.06]
    origin = int(before[-1]) if len(before) else int(cands[0])
    # make sure there is at least one bar of grid before bar 1
    if origin < meter:
        origin += meter

    confidence = round(confidence * (0.5 + 0.5 * db_agree), 3)
    if db_agree < 0.7 and len(downbeats) > 4:
        notes.append("Downbeat position is uncertain; check that bar 1 lands on the first strong beat.")
    return Grid(times, origin, meter, float(bpm), bool(steady), confidence, notes)


def refine(grid: Grid, onsets: np.ndarray, window: float = 0.05) -> float:
    """Nudge the grid onto drum transients (beat trackers work on ~20 ms frames).

    Steady grids get one global shift; drifting grids get a smoothed per-beat shift.
    Returns the median shift applied (s).
    """
    onsets = np.sort(np.asarray(onsets, dtype=np.float64))
    if len(onsets) < 16:
        return 0.0
    t = grid.times
    j = np.clip(np.searchsorted(onsets, t), 1, len(onsets) - 1)
    near = np.where(np.abs(onsets[j] - t) < np.abs(onsets[j - 1] - t), onsets[j], onsets[j - 1])
    dev = near - t
    ok = np.abs(dev) < window
    if ok.sum() < 16:
        return 0.0
    if grid.steady:
        shift = float(np.median(dev[ok]))
        grid.times = t + shift
        return shift
    # per-beat: interpolate matched deviations, smooth over ~8 beats
    idx = np.arange(len(t))
    d = np.interp(idx, idx[ok], dev[ok])
    k = 9
    d = np.convolve(np.pad(d, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
    grid.times = t + d
    return float(np.median(d))


def first_onset(mix_mono: np.ndarray, sr: int, hop: int = 512) -> float:
    from .audio import rms_env
    env = rms_env(mix_mono, hop=hop, win=2048)
    ref = np.percentile(env, 95)
    # low threshold: bar 1 must not start after any audible intro (exports never cut audio)
    above = np.nonzero(env > max(ref * 10 ** (-50 / 20), 10 ** (-70 / 20)))[0]
    return float(above[0] * hop / sr) if len(above) else 0.0
