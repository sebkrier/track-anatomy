"""Pipeline orchestrator.

    python -m track_anatomy.pipeline.run <track_dir> [--from STEP]

Each step caches its outputs in the track folder, so re-running (e.g. after a tempo
override) only redoes what is needed. Progress goes to <track_dir>/status.json.
"""
import argparse
import os
import sys
import time
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
# privacy: no library telemetry; TRACK_ANATOMY_OFFLINE=1 forbids even update checks for
# already-downloaded model weights (set before any model library is imported)
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
if os.environ.get("TRACK_ANATOMY_OFFLINE") == "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np  # noqa: E402

from ..config import DRUM_MODEL, DRUM_PIECES, MAX_MINUTES, MIN_SECONDS, SR, TOP_STEMS, device, stem_model_file  # noqa: E402
from ..jsonio import read_json as jload  # noqa: E402
from ..jsonio import write_json as jsave  # noqa: E402
from . import audio  # noqa: E402

STEPS = [
    ("decode", "Decoding audio", 0.02),
    ("separate", "Separating stems (6-stem model)", 0.30),
    ("drumsep", "Splitting drums into kick, snare, toms, hats, cymbals", 0.14),
    ("rhythm", "Tracking beats, downbeats and song sections", 0.12),
    ("transcribe", "Transcribing notes from each stem", 0.07),
    ("chords", "Recognising chords", 0.05),
    ("analyze", "Analysing parts, patterns, sound design and FX", 0.30),
]
PITCHED = ["bass", "vocals", "piano", "guitar", "other"]


class UserError(Exception):
    """A problem with the input the user can act on; shown as-is in the UI."""


class Status:
    """Writes progress to status.json for the server/UI to poll."""

    def __init__(self, d: Path):
        self.path = d / "status.json"
        self.data = {"state": "running", "step": None, "detail": "", "progress": 0.0, "error": None,
                     "started": time.time(), "updated": time.time(), "pid": os.getpid(),
                     "steps": [{"id": s, "label": lab, "state": "pending"} for s, lab, _ in STEPS]}
        self.write()

    def write(self):
        self.data["updated"] = time.time()
        jsave(self.path, self.data)

    def begin(self, sid: str):
        done = sum(w for s, _, w in STEPS[: [s for s, _, _ in STEPS].index(sid)])
        for s in self.data["steps"]:
            if s["id"] == sid:
                s["state"] = "running"
                s["t0"] = time.time()
        self.data.update(step=sid, detail="", progress=round(done, 3))
        self.write()
        print(f"[{time.strftime('%H:%M:%S')}] {sid}", flush=True)

    def end(self, sid: str, skipped: bool = False):
        for s in self.data["steps"]:
            if s["id"] == sid:
                s["state"] = "skipped" if skipped else "done"
                s["secs"] = round(time.time() - s.get("t0", time.time()), 1)
        self.write()

    def detail(self, text: str, frac: float | None = None):
        self.data["detail"] = text
        if frac is not None and self.data["step"]:
            ids = [s for s, _, _ in STEPS]
            i = ids.index(self.data["step"])
            base = sum(w for _, _, w in STEPS[:i])
            self.data["progress"] = round(base + STEPS[i][2] * min(max(frac, 0.0), 1.0), 3)
        self.write()
        print(f"    {text}", flush=True)

    def finish(self):
        self.data.update(state="done", progress=1.0, step=None, detail="")
        self.write()

    def fail(self, msg: str, input_error: bool = False):
        # input_error: the file itself is the problem (silent, too short...), so retrying won't help
        self.data.update(state="error", error=msg, input_error=input_error)
        for s in self.data["steps"]:
            if s["state"] == "running":
                s["state"] = "error"
        self.write()


# ----------------------------------------------------------------------------- steps
def _check_level(peak: float, rms_db: float):
    if peak < 1e-4 or rms_db < -70:
        raise UserError("The file is silent (or nearly): there is nothing to analyse.")


def step_decode(d: Path, st: Status):
    meta = jload(d / "meta.json")
    work = d / "work"
    work.mkdir(exist_ok=True)
    (d / "stems").mkdir(exist_ok=True)
    if (d / "stems" / "mix.flac").exists() and "gain" in meta:
        _check_level(meta.get("peak", 1.0), meta.get("rms_db", 0.0))
        return True
    src = d / meta["source"]
    try:
        audio.decode(src, work / "mix.wav")
    except Exception:
        raise UserError("ffmpeg couldn't decode this file. Is it a complete, playable audio file?") from None
    x = audio.load(work / "mix.wav")
    dur = len(x) / SR
    if dur < MIN_SECONDS:
        raise UserError(f"The decoded audio is only {dur:.1f} s long; at least {MIN_SECONDS:.0f} s are needed.")
    if dur > MAX_MINUTES * 60:
        raise UserError(f"The track is {dur / 60:.0f} min long; the limit is {MAX_MINUTES:.0f} min.")
    peak = float(np.abs(x).max())
    rms_db = float(audio.rms_db(x))
    _check_level(peak, rms_db)
    gain = min(1.0, 0.5 / peak)   # headroom so stems never clip and still sum to the mix
    xg = (x * gain).astype(np.float32)
    import soundfile as sf
    sf.write(str(work / "mix_g.wav"), xg, SR, subtype="FLOAT")
    audio.save(d / "stems" / "mix.flac", xg)
    tags = audio.probe_tags(src)
    meta.update(gain=gain, duration=dur, peak=peak, rms_db=round(rms_db, 1))
    if tags.get("title") and not meta.get("title_locked"):
        meta["title"] = tags["title"]
    if tags.get("artist"):
        meta["artist"] = tags["artist"]
    jsave(d / "meta.json", meta)
    (work / "mix.wav").unlink(missing_ok=True)
    return False


def step_separate(d: Path, st: Status):
    stems = d / "stems"
    if all((stems / f"{s}.flac").exists() for s in TOP_STEMS):
        return True
    from .separate import separate, write_float_wav
    src = d / "work" / "mix_g.wav"
    if not src.exists():
        write_float_wav(stems / "mix.flac", src)
    st.detail("Loading the separation model")
    model = stem_model_file()
    separate(model, src, stems, TOP_STEMS, progress=_progress_cb(st, "Separating vocals, drums, bass, guitar, piano, other"))
    meta = jload(d / "meta.json", {})
    meta["stem_model"] = model
    jsave(d / "meta.json", meta)
    return False


def _progress_cb(st: Status, label: str):
    last = [0.0]

    def cb(kind: str, frac: float):
        now = time.time()
        if now - last[0] < 0.7 and frac < 1.0:
            return
        last[0] = now
        if kind == "download":
            st.detail(f"Downloading the model (first run only)… {frac * 100:.0f}%")
        else:
            st.detail(f"{label}… {frac * 100:.0f}%", 0.05 + 0.9 * frac)
    return cb


def step_drumsep(d: Path, st: Status):
    stems = d / "stems"
    if all((stems / f"{p}.flac").exists() for p in DRUM_PIECES):
        return True
    drums = audio.load(stems / "drums.flac")
    if audio.rms_db(drums) < -60:
        for p in DRUM_PIECES:
            audio.save(stems / f"{p}.flac", np.zeros_like(drums))
        return False
    del drums
    from .separate import separate, write_float_wav
    tmp = write_float_wav(stems / "drums.flac", d / "work" / "drums.wav")
    separate(DRUM_MODEL, tmp, stems, DRUM_PIECES, progress=_progress_cb(st, "Splitting the drum stem"))
    tmp.unlink(missing_ok=True)
    return False


def step_rhythm(d: Path, st: Status):
    cache = d / "cache"
    cache.mkdir(exist_ok=True)
    skipped = True
    if not (cache / "beats.json").exists():
        skipped = False
        st.detail("Beat tracking (beat_this)", 0.1)
        from beat_this.inference import File2Beats
        try:
            f2b = File2Beats(checkpoint_path="final0", device=device(), dbn=False)
            beats, downbeats = f2b(str(d / "stems" / "mix.flac"))
        except Exception:
            if device() == "cpu":
                raise
            traceback.print_exc()                  # e.g. an op missing on MPS: CPU is slower but works
            f2b = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
            beats, downbeats = f2b(str(d / "stems" / "mix.flac"))
        jsave(cache / "beats.json", {"beats": [float(b) for b in beats], "downbeats": [float(b) for b in downbeats]})
        del f2b
    if not (cache / "allin1.json").exists():
        skipped = False
        st.detail("Song structure (All-In-One, Harmonix)", 0.5)
        try:
            jsave(cache / "allin1.json", _allin1(d))
        except Exception:
            # not cached: the analysis falls back to its own sections and the next run retries
            traceback.print_exc()
            st.detail("Song structure model failed; using the fallback section finder", 0.9)
    return skipped


def _allin1(d: Path) -> dict:
    import allin1_infer as allin1
    import soundfile as sf
    from allin1_infer import create_stems_input_from_directory

    from .separate import cleanup
    sdir = d / "work" / "allin1" / "track"
    sdir.mkdir(parents=True, exist_ok=True)
    stems = d / "stems"
    try:
        for k in ("bass", "drums", "vocals"):
            sf.write(str(sdir / f"{k}.wav"), audio.load(stems / f"{k}.flac"), SR, subtype="FLOAT")
        other = sum(audio.load(stems / f"{k}.flac") for k in ("other", "piano", "guitar"))
        sf.write(str(sdir / "other.wav"), other, SR, subtype="FLOAT")
        del other
        res = allin1.analyze(stems_input=create_stems_input_from_directory(sdir), device=device(),
                             multiprocess=False, demix_dir=str(d / "work" / "demix"), spec_dir=str(d / "work" / "spec"))
        return {"bpm": res.bpm, "beats": list(map(float, res.beats)), "downbeats": list(map(float, res.downbeats)),
                "segments": [{"start": float(s.start), "end": float(s.end), "label": s.label} for s in res.segments]}
    finally:
        cleanup([d / "work" / "allin1", d / "work" / "demix", d / "work" / "spec"])


def step_transcribe(d: Path, st: Status):
    cache = d / "cache"
    from . import notes as N
    skipped = True
    for i, k in enumerate(PITCHED):
        p = cache / f"notes_{k}.json"
        if p.exists():
            continue
        skipped = False
        st.detail(f"Transcribing {k}", i / len(PITCHED))
        x = audio.load(d / "stems" / f"{k}.flac", mono=True)
        jsave(p, N.transcribe(d / "stems" / f"{k}.flac", k, x))
    return skipped


def step_chords(d: Path, st: Status):
    p = d / "cache" / "chords_raw.json"
    if p.exists():
        return True
    import soundfile as sf

    from . import harmony
    stems = d / "stems"
    harm = sum(audio.load(stems / f"{k}.flac") for k in ("bass", "other", "piano", "guitar"))
    tmp = d / "work" / "harmonic.wav"
    sf.write(str(tmp), harm, SR, subtype="FLOAT")
    del harm
    try:
        jsave(p, harmony.recognise(str(tmp.resolve())))
    except Exception:
        traceback.print_exc()      # chords are optional; retried on the next run
    finally:
        tmp.unlink(missing_ok=True)
    return False


def step_analyze(d: Path, st: Status):
    from .analyze import analyze
    analyze(d, st)
    return False


RUNNERS = {"decode": step_decode, "separate": step_separate, "drumsep": step_drumsep, "rhythm": step_rhythm,
           "transcribe": step_transcribe, "chords": step_chords, "analyze": step_analyze}


def _invalidate(d: Path, from_step: str) -> None:
    """Delete cached outputs of `from_step` and everything after it."""
    from .separate import cleanup
    stems = d / "stems"
    cache = d / "cache"
    by_step = {
        "separate": [p for p in stems.glob("*.flac") if p.name != "mix.flac"] if stems.exists() else [],
        "drumsep": [stems / f"{p}.flac" for p in DRUM_PIECES],
        "rhythm": [cache / "beats.json", cache / "allin1.json"],
        "transcribe": list(cache.glob("notes_*.json")) if cache.exists() else [],
        "chords": [cache / "chords_raw.json"],
    }
    ids = [s for s, _, _ in STEPS]
    for s in ids[ids.index(from_step):]:
        cleanup(by_step.get(s, []))


def run(d: Path, from_step: str | None = None) -> None:
    d = Path(d).resolve()
    st = Status(d)
    if from_step:
        _invalidate(d, from_step)
    try:
        for sid, _, _ in STEPS:
            st.begin(sid)
            skipped = RUNNERS[sid](d, st)
            st.end(sid, skipped=bool(skipped))
        st.finish()
    except UserError as e:
        st.fail(str(e), input_error=True)
        sys.exit(2)
    except Exception as e:
        traceback.print_exc()
        step = st.data.get("step") or "?"
        oom = "out of memory" in str(e).lower() or isinstance(e, MemoryError)
        hint = (" The machine ran out of memory: try a shorter track or close other apps." if oom else
                " Details are in pipeline.log in the track's folder.")
        st.fail(f"The '{step}' step failed ({type(e).__name__}: {str(e)[:200]}).{hint}")
        sys.exit(1)
    finally:
        from .separate import cleanup
        # big float copies; regenerated from mix.flac / stems if a later run needs them
        cleanup([d / "work" / "mix_g.wav", d / "work" / "drums.wav", d / "work" / "harmonic.wav",
                 d / "work" / "mix.wav", d / "work" / "allin1", d / "work" / "demix", d / "work" / "spec"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("track_dir")
    ap.add_argument("--from", dest="from_step", default=None, choices=[s for s, _, _ in STEPS])
    a = ap.parse_args()
    d = Path(a.track_dir)
    if not (d / "meta.json").is_file():
        ap.error(f"{d} is not a track folder (no meta.json)")
    run(d, a.from_step)


if __name__ == "__main__":
    main()
