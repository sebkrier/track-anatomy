"""Audio I/O and small signal helpers."""
import base64
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import SR

EPS = 1e-10


def decode(src: Path, dst: Path, sr: int = SR) -> None:
    """Decode any ffmpeg-readable file to stereo float32 WAV at `sr`."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(sr),
           "-c:a", "pcm_f32le", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)


def probe_tags(src: Path) -> dict:
    """Best-effort title/artist from container tags."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist",
                              "-of", "default=nw=1", str(src)], capture_output=True, timeout=20)
        tags = {}
        for line in out.stdout.decode("utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                v = "".join(ch for ch in v.strip() if ch.isprintable())[:200]
                if v:
                    tags[k.split(":")[-1].lower()] = v
        return tags
    except Exception:
        return {}


def load(path: Path, mono: bool = False) -> np.ndarray:
    """Read a pipeline audio file (always at SR); float32 [n, ch], or [n] when mono=True."""
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError(f"{path} is {sr} Hz, expected {SR}")
    return x.mean(axis=1) if mono else x


def save(path: Path, x: np.ndarray, subtype: str = "PCM_24") -> None:
    sf.write(str(path), x, SR, subtype=subtype)


def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def db(x, floor: float = -120.0):
    return np.maximum(20 * np.log10(np.abs(x) + EPS), floor)


def rms_env(x: np.ndarray, hop: int = 256, win: int = 1024) -> np.ndarray:
    """Frame RMS of a mono signal, frames centred on hop multiples (computed in blocks)."""
    x = mono(x)
    pad = win // 2
    xp = np.pad(x.astype(np.float32, copy=False), (pad, pad))
    n = 1 + (len(xp) - win) // hop
    if n <= 0:
        return np.zeros(0, np.float32)
    out = np.empty(n, np.float32)
    block = max(1, (1 << 22) // hop)
    starts = hop * np.arange(block)
    for f0 in range(0, n, block):
        f1 = min(n, f0 + block)
        seg = xp[f0 * hop:(f1 - 1) * hop + win].astype(np.float64)
        cs = np.concatenate([[0.0], np.cumsum(seg * seg)])
        st = starts[:f1 - f0]
        out[f0:f1] = np.sqrt(np.maximum((cs[st + win] - cs[st]) / win, 0))
    return out


def onset_env(y: np.ndarray, hop: int = 256, fmin: float | None = None, fmax: float | None = None,
              n_mels: int = 64, chunk_s: float = 60.0) -> np.ndarray:
    """librosa's spectral-flux onset strength, optionally limited to fmin..fmax, computed in
    overlapping chunks so memory doesn't grow with the length of the track."""
    import librosa
    y = mono(y).astype(np.float32, copy=False)
    kw = {"n_mels": n_mels, "fmin": fmin or 0.0, "fmax": fmax or SR / 2}
    total = 1 + len(y) // hop
    chunk = int(chunk_s * SR) // hop * hop
    ov = 8 * hop
    out = np.zeros(total, np.float32)
    for a in range(0, len(y), chunk):
        s0 = max(0, a - ov)
        seg = y[s0:min(len(y), a + chunk + ov)]
        env = librosa.onset.onset_strength(y=seg, sr=SR, hop_length=hop, lag=1, max_size=1, center=True, **kw)
        f_off = s0 // hop
        keep0 = (a - s0) // hop                       # drop the overlap frames at the start
        keep1 = min(len(env), keep0 + chunk // hop)
        dst0 = f_off + keep0
        n_ = min(keep1 - keep0, total - dst0)
        if n_ > 0:
            out[dst0:dst0 + n_] = env[keep0:keep0 + n_]
    return out


def rms_db(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + EPS))


def peaks_b64(x: np.ndarray, per_sec: int = 50, ref: float | None = None) -> str:
    """Downsampled |peak| envelope, uint8 (0-255) base64, for waveform drawing.

    Scaled against `ref` (usually the mix peak) so lanes are comparable.
    """
    m = np.abs(mono(x))
    hop = SR // per_sec
    n = int(np.ceil(len(m) / hop))
    m = np.pad(m, (0, n * hop - len(m)))
    p = m.reshape(n, hop).max(axis=1)
    ref = ref or (p.max() + EPS)
    # sqrt-ish compression so quiet parts stay visible
    v = np.clip(np.sqrt(p / ref), 0, 1) * 255
    return base64.b64encode(v.astype(np.uint8).tobytes()).decode()


def _sos(lo: float | None, hi: float | None, order: int):
    from scipy.signal import butter
    if lo and hi:
        return butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    if lo:
        return butter(order, lo, btype="high", fs=SR, output="sos")
    return butter(order, hi, btype="low", fs=SR, output="sos")


def band(x: np.ndarray, lo: float | None, hi: float | None, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth filter (whole signal; use for short excerpts)."""
    from scipy.signal import sosfiltfilt
    return sosfiltfilt(_sos(lo, hi, order), x, axis=0).astype(np.float32)


def band_causal(x: np.ndarray, lo: float | None, hi: float | None, order: int = 4,
                block: int = 1 << 21) -> np.ndarray:
    """One-pass Butterworth filter of a mono signal in blocks with carried state: identical to a
    whole-signal sosfilt, float32 memory only. Its few ms of phase delay don't matter for onset
    detection (onsets are re-located on the unfiltered envelope afterwards)."""
    from scipy.signal import sosfilt
    sos = _sos(lo, hi, order)
    x = mono(x)
    out = np.empty(len(x), np.float32)
    zi = np.zeros((sos.shape[0], 2))
    for a in range(0, len(x), block):
        y, zi = sosfilt(sos, x[a:a + block].astype(np.float64), zi=zi)
        out[a:a + block] = y
    return out
