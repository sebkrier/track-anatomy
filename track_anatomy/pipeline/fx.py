"""Arrangement FX events: risers, downlifters, impacts, snare rolls, pre-drop silences."""
import numpy as np

from ..config import SR
from . import audio


def spearman(x, y) -> float:
    """Rank correlation (0 when either side is constant or too short)."""
    if len(x) < 3:
        return 0.0
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    if np.std(rx) == 0 or np.std(ry) == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def _frame_stats(m: np.ndarray, n_fft: int = 2048, hop: int = 512):
    """Per-frame energy, spectral centroid and flatness, computed in chunks (bounded memory)."""
    import librosa
    freqs = np.arange(n_fft // 2 + 1) * SR / n_fft
    chunk = hop * 4096
    E, C, F = [], [], []
    for a in range(0, len(m), chunk):
        seg = m[a:a + chunk + n_fft]
        P = np.abs(librosa.stft(seg, n_fft=n_fft, hop_length=hop, center=False)) ** 2
        keep = min(P.shape[1], chunk // hop)
        P = P[:, :keep]
        e = P.sum(0)
        E.append(e)
        C.append((freqs[:, None] * P).sum(0) / (e + 1e-12))
        mag = np.sqrt(P[5:])
        F.append(np.exp(np.mean(np.log(mag + 1e-9), 0)) / (np.mean(mag, 0) + 1e-9))
    t = np.arange(sum(len(e) for e in E)) * hop / SR + n_fft / 2 / SR
    return t, np.concatenate(E) if E else np.zeros(0), np.concatenate(C) if C else np.zeros(0), \
        np.concatenate(F) if F else np.zeros(0)


def _beat_features(m: np.ndarray, grid, n_beats: int):
    """Per-beat level (dB), centroid (Hz) and flatness."""
    t, E, C, F = _frame_stats(m)
    bt = grid.time(np.arange(n_beats + 1))
    idx = np.searchsorted(t, bt)
    lvl = np.full(n_beats, -120.0)
    cen = np.full(n_beats, np.nan)
    flat = np.full(n_beats, np.nan)
    for i in range(n_beats):
        a, b = idx[i], idx[i + 1]
        if b <= a:
            continue
        e = E[a:b]
        tot = e.sum()
        lvl[i] = 10 * np.log10(tot / (b - a) + 1e-12)
        if tot > 0:
            cen[i] = float((C[a:b] * e).sum() / tot)
            flat[i] = float(F[a:b].mean())
    return lvl, cen, flat


def transitions(m: np.ndarray, grid, n_bars: int, boundaries: list[int], source: str) -> list[dict]:
    """Risers into and downlifters out of section boundaries, from one pass over the FX layer."""
    meter = grid.meter
    lvl, cen, flat = _beat_features(m, grid, n_bars * meter)
    ref = np.percentile(lvl[lvl > -100], 95) if np.any(lvl > -100) else -20
    return _risers(lvl, cen, flat, ref, meter, boundaries, source) + \
        _downlifters(lvl, cen, ref, meter, n_bars, boundaries, source)


def _risers(lvl, cen, flat, ref, meter, boundaries, source) -> list[dict]:
    out = []
    for B in boundaries:
        if B < 2:
            continue
        for L in (16, 8, 4, 2):
            b0 = B - L
            if b0 < 0:
                continue
            seg = slice(b0 * meter, B * meter)
            c, lv = cen[seg], lvl[seg]
            ok = ~np.isnan(c) & (lv > ref - 35)
            if len(c) == 0 or ok.mean() < 0.7 or ok.sum() < 4:
                continue
            x = np.arange(len(c))[ok]
            lc = np.log2(np.maximum(c[ok], 1.0))
            k = max(2, len(lc) // 4)
            ratio = 2 ** (np.mean(lc[-k:]) - np.mean(lc[:k]))
            lslope = np.polyfit(x, lv[ok], 1)[0] * meter      # dB per bar
            if spearman(x, lc) >= 0.75 and ratio >= 1.6 and lslope >= -0.3:
                noisy = np.nanmedian(flat[seg]) > 0.25
                out.append({"type": "riser", "bar0": b0, "bar1": B, "source": source,
                            "label": "Noise sweep / riser" if noisy else "Tonal riser / pitch sweep",
                            "detail": f"brightness rises x{ratio:.1f} over {L} bars"})
                break
    return out


def _downlifters(lvl, cen, ref, meter, n_bars, boundaries, source) -> list[dict]:
    out = []
    for B in boundaries:
        for L in (4, 2):
            if B + L > n_bars:
                continue
            seg = slice(B * meter, (B + L) * meter)
            c, lv = cen[seg], lvl[seg]
            ok = ~np.isnan(c) & (lv > ref - 30)
            if len(c) == 0 or ok.mean() < 0.6 or ok.sum() < 4:
                continue
            x = np.arange(len(c))[ok]
            lc = np.log2(np.maximum(c[ok], 1.0))
            if spearman(x, lc) <= -0.8 and 2 ** (lc[0] - lc[-1]) >= 1.8:
                out.append({"type": "downlifter", "bar0": B, "bar1": B + L, "source": source,
                            "label": "Downlifter / sweep down", "detail": ""})
                break
    return out


def impacts(drum_hits: dict, fx_m: np.ndarray | None, grid, boundaries: list[int]) -> list[dict]:
    """Crash on a section's downbeat, or (with the FX layer) a sudden boom."""
    out = []
    crash = np.array([h["beat"] for h in drum_hits.get("crash", [])])
    for B in boundaries:
        beat = B * grid.meter
        if len(crash) and np.min(np.abs(crash - beat)) < 0.5:
            out.append({"type": "impact", "bar0": B, "bar1": B, "label": "Crash / impact on the downbeat", "detail": ""})
            continue
        if fx_m is not None:
            t = float(grid.time(beat))
            a, b = int(max(0, t - 0.5) * SR), int(t * SR)
            c, d = int(t * SR), int((t + 0.25) * SR)
            if d < len(fx_m) and b > a:
                jump = audio.rms_db(fx_m[c:d]) - audio.rms_db(fx_m[a:b])
                if jump > 9 and audio.rms_db(fx_m[c:d]) > -45:
                    out.append({"type": "impact", "bar0": B, "bar1": B, "label": "Impact / boom FX",
                                "detail": f"+{jump:.0f} dB"})
    return out


def snare_rolls(drum_hits: dict, grid, boundaries: list[int], n_bars: int) -> list[dict]:
    """A run of bars ending at a section boundary where the snare plays far denser than usual."""
    sn = np.array([h["beat"] for h in drum_hits.get("snare", [])])
    out = []
    if len(sn) < 8 or n_bars < 2:
        return out
    m = grid.meter
    bars = np.floor(sn / m).astype(int)
    per_bar = np.bincount(bars[(bars >= 0) & (bars < n_bars)], minlength=n_bars)
    typical = float(np.median(per_bar[per_bar > 0])) if np.any(per_bar > 0) else 2.0
    for B in boundaries:
        if B < 1 or B > n_bars:
            continue
        last = per_bar[B - 1]
        if last >= max(2 * m, 2.5 * typical):
            b0 = B - 1
            while b0 - 1 >= 0 and per_bar[b0 - 1] >= max(1.5 * typical, m) and B - b0 < 16:
                b0 -= 1
            out.append({"type": "snare_roll", "bar0": int(b0), "bar1": int(B), "label": "Snare roll build",
                        "detail": f"{per_bar[b0]} → {last} hits/bar over {B - b0} bars (usually {typical:.0f})"})
    return out


def silences(mix_m: np.ndarray, grid, boundaries: list[int]) -> list[dict]:
    """The whole mix drops out for the last beat before a section starts."""
    out = []
    for B in boundaries:
        t = float(grid.time(B * grid.meter))
        a, b = int((t - grid.period) * SR), int(t * SR)
        c = int((t - 4 * grid.meter * grid.period) * SR)
        if a <= 0 or c <= 0 or b > len(mix_m):
            continue
        gap = audio.rms_db(mix_m[a:b])
        ctx = audio.rms_db(mix_m[c:a])
        if ctx - gap > 14:
            out.append({"type": "silence", "bar0": B - 1, "bar1": B, "label": "Drop-out before the downbeat",
                        "detail": f"{ctx - gap:.0f} dB quieter for the last beat"})
    return out
