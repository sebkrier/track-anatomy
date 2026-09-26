"""Split a pitched stem's notes into musical roles and render each role as audio.

No open model separates "pad" from "lead" from "arp", so this works on note behaviour:
  pad   sustained chords (>= ~1 beat, several simultaneous notes)
  stab  short chords
  lead  a single melodic line (octave doublings count as one voice)
  arp   fast, regular single-note runs (arpeggios, plucks, sequences)
  low   single notes below ~G2 in a non-bass stem (sub layers, bass bleed)
Each role is then rendered by a harmonic time-frequency mask built from its notes,
so it can be soloed. What no note explains (noise, risers, reverb wash) is the residual.
"""
import numpy as np

from ..config import SR

MIN_NOTES = 12


def _clusters(notes: list[dict], tol: float = 0.035) -> list[list[int]]:
    order = sorted(range(len(notes)), key=lambda i: notes[i]["s"])
    out, cur, t0 = [], [], None
    for i in order:
        if cur and notes[i]["s"] - t0 <= tol:
            cur.append(i)
        else:
            if cur:
                out.append(cur)
            cur, t0 = [i], notes[i]["s"]
    if cur:
        out.append(cur)
    return out


def classify(notes: list[dict], kind: str) -> list[str]:
    """Role per note (same order as `notes`). Notes need 'bs'/'be' (beats)."""
    n = len(notes)
    role = [""] * n
    if n == 0:
        return role
    dur = np.array([nt["be"] - nt["bs"] for nt in notes])
    melodic_units = []   # (start_beat, [note idx])
    for cl in _clusters(notes):
        pcs = {notes[i]["p"] % 12 for i in cl}
        if len(cl) == 1 or len(pcs) == 1:
            melodic_units.append(cl)
            continue
        pitches = sorted(cl, key=lambda i: notes[i]["p"])
        # notes much shorter than the held chord they start with belong to another line
        md_all = float(np.median(dur[pitches]))
        short = [i for i in pitches if dur[i] < 0.35 * md_all]
        if short and len(pitches) - len(short) >= 2:
            for i in short:
                melodic_units.append([i])
            pitches = [i for i in pitches if i not in short]
        top, second = pitches[-1], pitches[-2]
        rest = pitches[:-1]
        md = float(np.median(dur[rest]))
        if notes[top]["p"] - notes[second]["p"] >= 8 and (dur[top] < 0.5 * md or dur[top] > 2 * md):
            melodic_units.append([top])
            pitches = rest
        if len({notes[i]["p"] % 12 for i in pitches}) <= 1:
            melodic_units.append(pitches)
            continue
        r = "pad" if float(np.median(dur[pitches])) >= 0.9 else "stab"
        for i in pitches:
            role[i] = r

    # single-voice units: lead vs arp vs low
    melodic_units.sort(key=lambda u: notes[u[0]]["s"])
    starts = np.array([notes[u[0]]["bs"] for u in melodic_units]) if melodic_units else np.array([])
    udur = np.array([max(dur[i] for i in u) for u in melodic_units]) if melodic_units else np.array([])
    ioi = np.diff(starts, append=starts[-1] + 4) if len(starts) else starts
    pad_iv = [(notes[i]["bs"], notes[i]["be"]) for i in range(n) if role[i] == "pad"]
    for k, u in enumerate(melodic_units):
        # 9-unit window around the note, kept full-size at the start and end of the part
        lo = max(0, k - 4)
        hi = min(len(melodic_units), lo + 9)
        lo = max(0, hi - 9)
        loc_ioi = np.median(ioi[lo:hi])
        loc_dur = np.median(udur[lo:hi])
        reg = np.std(ioi[lo:hi]) / (loc_ioi + 1e-6)
        if udur[k] >= 2.0 and any(a <= notes[u[0]]["bs"] + 0.25 and b >= notes[u[0]]["be"] - 0.5 for a, b in pad_iv):
            r = "pad"
        elif loc_ioi <= 0.55 and loc_dur <= 0.6 and reg < 0.6 and hi - lo >= 6:
            r = "arp"
        else:
            r = "lead"
        for i in u:
            role[i] = r

    # absorb tiny roles into their temporal neighbours
    counts = {r: role.count(r) for r in set(role)}
    small = {r for r, c in counts.items() if c < MIN_NOTES}
    if small and len(counts) > len(small):
        big_idx = [i for i in range(n) if role[i] not in small]
        big_t = np.array([notes[i]["s"] for i in big_idx])
        for i in range(n):
            if role[i] in small:
                j = big_idx[int(np.argmin(np.abs(big_t - notes[i]["s"])))]
                role[i] = role[j]
    return role


def _union(iv: list[tuple[float, float]]) -> list[list[float]]:
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _length(iv) -> float:
    return sum(b - a for a, b in iv)


def _intersect(A, B) -> float:
    i = j = 0
    tot = 0.0
    while i < len(A) and j < len(B):
        lo, hi = max(A[i][0], B[j][0]), min(A[i][1], B[j][1])
        if hi > lo:
            tot += hi - lo
        if A[i][1] < B[j][1]:
            i += 1
        else:
            j += 1
    return tot


def _merge_alternating(out: dict[str, list[dict]]) -> None:
    """Roles that share bars but never sound together are one instrument
    (e.g. a riff that alternates single notes and chords): merge them."""
    while len(out) > 1:
        best = None
        rs = list(out)
        for x in range(len(rs)):
            for y in range(x + 1, len(rs)):
                A, B = out[rs[x]], out[rs[y]]
                ua = _union([(n["bs"], n["be"] - 0.02) for n in A])
                ub = _union([(n["bs"], n["be"] - 0.02) for n in B])
                ov = _intersect(ua, ub) / max(1e-6, min(_length(ua), _length(ub)))
                bars_a = {int(n["bs"] // 4) for n in A}
                bars_b = {int(n["bs"] // 4) for n in B}
                co = len(bars_a & bars_b) / max(1, min(len(bars_a), len(bars_b)))
                if ov < 0.15 and co >= 0.5 and (best is None or ov < best[0]):
                    best = (ov, rs[x], rs[y])
        if best is None:
            return
        _, a, b = best
        keep, drop = (a, b) if len(out[a]) >= len(out[b]) else (b, a)
        if {a, b} == {"stab", "lead"}:       # a riff mixing notes and chords reads as a lead
            keep, drop = "lead", "stab"
        for nt in out[drop]:
            nt["role"] = keep
        out[keep] = sorted(out[keep] + out.pop(drop), key=lambda n: n["s"])


def chord_fraction(notes: list[dict]) -> float:
    """Share of notes that start together with a different pitch class (i.e. in chords)."""
    if not notes:
        return 0.0
    n_chord = 0
    for cl in _clusters(notes):
        if len({notes[i]["p"] % 12 for i in cl}) >= 2:
            n_chord += len(cl)
    return n_chord / len(notes)


def split(notes: list[dict], kind: str) -> dict[str, list[dict]]:
    roles = classify(notes, kind)
    out: dict[str, list[dict]] = {}
    for nt, r in zip(notes, roles):
        nt["role"] = r
        out.setdefault(r, []).append(nt)
    _merge_alternating(out)
    # a role that spans less than 2 bars' worth of time is folded into the biggest role
    if len(out) > 1:
        biggest = max(out, key=lambda r: len(out[r]))
        for r in list(out):
            if r == biggest:
                continue
            span = max(n["be"] for n in out[r]) - min(n["bs"] for n in out[r])
            if len(out[r]) < MIN_NOTES or span < 8:
                for nt in out[r]:
                    nt["role"] = biggest
                out[biggest].extend(out.pop(r))
        out[biggest].sort(key=lambda n: n["s"])
    return out


# --------------------------------------------------------------------------- rendering
N_FFT, HOP = 4096, 1024


def _note_template(p: int, n_bins: int) -> np.ndarray:
    f0 = 440.0 * 2 ** ((p - 69) / 12)
    k = np.arange(n_bins)
    binhz = SR / N_FFT
    t = np.zeros(n_bins, np.float32)
    h = 1
    while h * f0 < min(18000, SR / 2 - 200) and h <= 60:
        c = h * f0 / binhz
        w = max(1.3, c * 0.022)
        lo, hi = int(max(0, c - 3 * w)), int(min(n_bins, c + 3 * w + 1))
        t[lo:hi] = np.maximum(t[lo:hi], np.exp(-0.5 * ((k[lo:hi] - c) / w) ** 2) * h ** -0.15)
        h += 1
    return t


def render(x: np.ndarray, roles: dict[str, list[dict]], residual_name: str | None,
           chunk_s: float = 30.0, overlap_s: float = 1.0) -> dict[str, np.ndarray]:
    """Soft-mask the stem into one signal per role (+ optional residual).

    Works in overlapping chunks with complementary linear crossfades, so memory stays
    bounded however long the track is. The outputs (plus residual) sum back to the stem."""
    outs = {}
    for s0, s1, _, _, seg in _chunks(x, roles, residual_name, chunk_s, overlap_s):
        for r, y in seg.items():
            if r not in outs:
                outs[r] = np.zeros_like(x, dtype=np.float32)
            outs[r][s0:s1] += y
    return outs


def render_to_files(x: np.ndarray, roles: dict[str, list[dict]], residual_name: str | None,
                    paths: dict, chunk_s: float = 30.0, overlap_s: float = 1.0) -> None:
    """Like render(), but streams each part into its FLAC file in `paths` (constant memory).

    After a chunk, everything before the next crossfade is final and is written; the
    crossfade region is carried over and completed by the next chunk."""
    import soundfile as sf
    n, ch = x.shape
    writers = {r: sf.SoundFile(str(p), "w", SR, ch, subtype="PCM_24") for r, p in paths.items()}
    carry: dict[str, tuple[int, np.ndarray]] = {}
    written = 0
    ov = int(overlap_s * SR) // HOP * HOP                # same overlap as _chunks
    try:
        for s0, _s1, _a, b, seg in _chunks(x, roles, residual_name, chunk_s, overlap_s):
            done_to = b - ov // 2 if b < n else n        # every sample before this is final
            for r, y in seg.items():
                if r not in writers:
                    continue
                if r in carry:
                    c0, cy = carry.pop(r)
                    y[c0 - s0:c0 - s0 + len(cy)] += cy
                writers[r].write(np.clip(y[written - s0:done_to - s0], -1.0, 1.0))
                if done_to < n:
                    carry[r] = (done_to, y[done_to - s0:].copy())
            written = done_to
    finally:
        for wr in writers.values():
            wr.close()


def _chunks(x, roles, residual_name, chunk_s, overlap_s):
    """Yield (s0, s1, a, b, {part: crossfade-weighted audio for samples s0..s1})."""
    n = x.shape[0]
    names = list(roles)
    # multiples of HOP keep every chunk on the same STFT frame grid as a whole-file render
    chunk, ov = int(chunk_s * SR) // HOP * HOP, int(overlap_s * SR) // HOP * HOP
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        s0, s1 = max(0, a - ov), min(n, b + ov)
        t0, t1 = s0 / SR, s1 / SR
        sub = {r: [dict(nt, s=nt["s"] - t0, e=nt["e"] - t0) for nt in roles[r]
                   if nt["e"] + 0.5 > t0 and nt["s"] < t1] for r in names}
        seg = _render_segment(x[s0:s1], sub, residual_name, names)
        t = np.arange(s0, s1)
        w = np.ones(s1 - s0, np.float32)
        if a > 0:                                      # fade in across [a - ov/2, a + ov/2]
            w = np.minimum(w, np.clip((t - (a - ov / 2)) / ov, 0, 1))
        if b < n:                                      # fade out across [b - ov/2, b + ov/2]
            w = np.minimum(w, np.clip(((b + ov / 2) - t) / ov, 0, 1))
        yield s0, s1, a, b, {r: y * w[:, None] for r, y in seg.items()}


def _render_segment(x: np.ndarray, roles: dict[str, list[dict]], residual_name: str | None,
                    names: list[str]) -> dict[str, np.ndarray]:
    import librosa
    X = librosa.stft(x.T, n_fft=N_FFT, hop_length=HOP)          # (ch, bins, frames)
    n_bins, n_frames = X.shape[1], X.shape[2]
    masks = np.zeros((len(names), n_bins, n_frames), np.float32)
    cache = {}
    tail = 0.12
    for ri, r in enumerate(names):
        for nt in roles[r]:
            tp = cache.get(nt["p"])
            if tp is None:
                tp = cache[nt["p"]] = _note_template(nt["p"], n_bins)
            f0 = int(max(0, (nt["s"] - 0.03) * SR / HOP))
            f1 = int(min(n_frames, (nt["e"] + tail * 3) * SR / HOP + 1))
            if f1 <= f0:
                continue
            tt = np.arange(f0, f1) * HOP / SR
            env = np.where(tt <= nt["e"], 1.0, np.exp(-(tt - nt["e"]) / tail)).astype(np.float32)
            masks[ri, :, f0:f1] = np.maximum(masks[ri, :, f0:f1], tp[:, None] * env[None, :])
    power = masks ** 2
    del masks
    floor = 0.06 ** 2
    denom = power.sum(axis=0) + floor
    out = {}
    n = x.shape[0]
    for ri, r in enumerate(names):
        out[r] = librosa.istft(X * (power[ri] / denom)[None], hop_length=HOP, length=n).T.astype(np.float32)
    rest = librosa.istft(X * (floor / denom)[None], hop_length=HOP, length=n).T.astype(np.float32)
    if residual_name:
        out[residual_name] = rest
    else:
        # hand the unexplained energy to the biggest role so the parts still sum to the stem
        big = max(names, key=lambda r: len(roles[r]))
        out[big] = out[big] + rest
    return out
