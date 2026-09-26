"""Track Anatomy web server.

    ./run.sh      (or: python -m track_anatomy serve)

A single worker thread runs one analysis at a time (the GPU is the bottleneck) in a
subprocess, so a crash in a model can't take the server down. Progress is exchanged through
each track's status.json.
"""
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import config, system, tracks
from .config import MAX_UPLOAD_MB, ROOT, STEM_MODELS, TRACKS, WEB, __version__

CSRF_HEADER = "x-track-anatomy"


# ----------------------------------------------------------------------------- job queue
class Worker(threading.Thread):
    """Runs one pipeline job at a time in a subprocess."""

    def __init__(self):
        super().__init__(daemon=True, name="pipeline-worker")
        self.q: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.pending: list[str] = []
        self.current: str | None = None
        self.proc: subprocess.Popen | None = None
        self.cancelled: set[str] = set()
        self.rerun: dict[str, str | None] = {}     # requested while that track was running
        self.lock = threading.Lock()

    def submit(self, tid: str, from_step: str | None = None) -> None:
        with self.lock:
            if tid == self.current:
                self.rerun[tid] = from_step
                return
            if tid in self.pending:
                return
            self.pending.append(tid)
            self.cancelled.discard(tid)
        d = tracks.path(tid)
        st = tracks.read_json(d / "status.json", {}) or {}
        st.update(state="queued", error=None, updated=time.time(), progress=0)
        tracks.write_json(d / "status.json", st)
        self.q.put((tid, from_step))

    def cancel(self, tid: str) -> bool:
        """Drop a queued job or stop the running one. Returns True if something was cancelled."""
        with self.lock:
            if tid in self.pending:
                self.pending.remove(tid)
                self.cancelled.add(tid)
                return True
            if tid == self.current and self.proc is not None:
                self.cancelled.add(tid)
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except Exception:
                    self.proc.terminate()
                return True
        return False

    def position(self, tid: str) -> int | None:
        with self.lock:
            return self.pending.index(tid) + 1 if tid in self.pending else None

    def _wait_for_orphans(self) -> None:
        # a pipeline left running by a previous server process still owns the GPU: let it finish
        for tid in tracks.all_ids():
            st = tracks.read_json(tracks.path(tid) / "status.json", {}) or {}
            while st.get("state") == "running" and _pipeline_alive(st.get("pid")):
                self.current = tid
                time.sleep(2)
                st = tracks.read_json(tracks.path(tid) / "status.json", {}) or {}
        self.current = None

    def run(self):
        self._wait_for_orphans()
        while True:
            tid, from_step = self.q.get()
            with self.lock:
                if tid in self.cancelled or tid not in self.pending:
                    continue                     # cancelled or deleted while queued
                self.pending.remove(tid)
                self.current = tid
            d = tracks.path(tid)
            if not (d / "meta.json").exists():
                self.current = None
                continue
            cmd = [sys.executable, "-m", "track_anatomy.pipeline.run", str(d)]
            if from_step:
                cmd += ["--from", from_step]
            try:
                with open(d / "pipeline.log", "a", encoding="utf-8") as log:
                    log.write(f"\n==== {time.ctime()} {' '.join(cmd[2:])}\n")
                    log.flush()
                    self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                                                 start_new_session=True)
                    rc = self.proc.wait()
                st = tracks.read_json(d / "status.json", {}) or {}
                if tid in self.cancelled:
                    st.update(state="cancelled", error=None)
                    tracks.write_json(d / "status.json", st)
                elif rc != 0 and st.get("state") != "error":
                    st.update(state="error", error=f"The analysis stopped unexpectedly (exit code {rc}). "
                                                   "See pipeline.log in the track folder.")
                    tracks.write_json(d / "status.json", st)
            except Exception as e:                # keep the worker alive whatever happens
                print(f"worker error on {tid}: {e}", flush=True)
            finally:
                self.proc = None
                with self.lock:
                    self.current = None
                    self.cancelled.discard(tid)
                    again = self.rerun.pop(tid, "none")
                if again != "none" and (tracks.path(tid) / "meta.json").exists():
                    self.submit(tid, again)

    def shutdown(self) -> None:
        with self.lock:
            p = self.proc
        if p is not None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                p.terminate()


worker = Worker()


def _pipeline_alive(pid) -> bool:
    """Is `pid` one of our pipeline processes (not a recycled pid)?"""
    try:
        import psutil
        return "track_anatomy.pipeline.run" in " ".join(psutil.Process(int(pid)).cmdline())
    except Exception:
        return False


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    # resume anything that was queued or interrupted (but not a pipeline that is still running)
    for tid in reversed(tracks.all_ids()):
        st = tracks.read_json(tracks.path(tid) / "status.json", {}) or {}
        if st.get("state") == "running" and _pipeline_alive(st.get("pid")):
            continue
        if st.get("state") in ("queued", "running"):
            worker.submit(tid)
    yield
    worker.shutdown()


app = FastAPI(title="Track Anatomy", version=__version__, lifespan=lifespan)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
EXTRA_HOSTS = {h.strip() for h in os.environ.get("TRACK_ANATOMY_ALLOWED_HOSTS", "").split(",") if h.strip()}


SECURITY_HEADERS = {
    # no framing by other sites (clickjacking would bypass the CSRF header: the app's own JS sends it)
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
                                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Local-app protections. The API has no accounts, so it must only ever be driven by this
    app's own page:
    - unknown Host headers are refused (DNS rebinding: evil.example resolving to 127.0.0.1);
    - cross-site API requests are refused (browsers label them with Sec-Fetch-Site);
    - state-changing requests need a custom header, which a cross-site form can't set;
    - responses forbid framing, so another site can't overlay the UI and borrow clicks."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in LOCAL_HOSTS and host not in EXTRA_HOSTS:
        return JSONResponse({"detail": "Unknown host (set TRACK_ANATOMY_ALLOWED_HOSTS to allow it)"}, status_code=403)
    site = request.headers.get("sec-fetch-site")
    if request.url.path.startswith("/api/") and site not in (None, "same-origin", "none"):
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        return JSONResponse({"detail": "Missing X-Track-Anatomy header"}, status_code=403)
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


# ----------------------------------------------------------------------------- tracks
@app.get("/api/tracks")
def list_tracks():
    out = []
    for tid in tracks.all_ids():
        s = tracks.summary(tid)
        if s:
            s["queue_position"] = worker.position(tid)
            out.append(s)
    return out


@app.post("/api/tracks")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in tracks.AUDIO_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext or '?'}'. Use mp3, wav, flac, aiff, m4a or ogg.")
    TRACKS.mkdir(parents=True, exist_ok=True)
    limit = MAX_UPLOAD_MB * (1 << 20)
    size = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=TRACKS.parent, suffix=ext)
    tmp_path = Path(tmp.name)
    try:
        with tmp:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
                tmp.write(chunk)
        err = await run_in_threadpool(tracks.validate, tmp_path)
        if err:
            raise HTTPException(422, err)
        tid = await run_in_threadpool(tracks.create, tmp_path, file.filename or f"track{ext}")
    finally:
        tmp_path.unlink(missing_ok=True)
    d = tracks.path(tid)
    st = tracks.read_json(d / "status.json", {}) or {}
    done = (d / "analysis.json").exists()
    if not done and st.get("state") != "running":
        worker.submit(tid)
    return {"id": tid, "existing": done}


def _dir(tid: str) -> Path:
    try:
        d = tracks.path(tid)
    except ValueError:
        raise HTTPException(400, "bad id") from None
    if not (d / "meta.json").exists():
        raise HTTPException(404, "no such track")
    return d


def _analysis(d: Path) -> dict:
    A = tracks.read_json(d / "analysis.json")
    if not A:
        raise HTTPException(404, "not analysed yet")
    return A


@app.get("/api/tracks/{tid}/status")
def status(tid: str):
    d = _dir(tid)
    st = tracks.read_json(d / "status.json", {}) or {}
    st["queue_position"] = worker.position(tid)
    st["meta"] = tracks.read_json(d / "meta.json", {})
    st["has_analysis"] = (d / "analysis.json").exists()
    st["stale"] = bool((tracks.summary(tid) or {}).get("stale"))
    return st


@app.get("/api/tracks/{tid}/analysis")
def analysis(tid: str):
    p = _dir(tid) / "analysis.json"
    if not p.exists():
        raise HTTPException(404, "not analysed yet")
    return FileResponse(p, media_type="application/json", headers={"Cache-Control": "no-store"})


MEDIA = {".flac": "audio/flac", ".wav": "audio/wav", ".mid": "audio/midi", ".md": "text/markdown; charset=utf-8",
         ".als": "application/octet-stream"}
FILE_DIRS = {"stems": "stems", "midi": "midi", "root": ""}
ROOT_FILES = {"GUIDE.md"}


@app.get("/api/tracks/{tid}/file/{sub}/{name}")
def file(tid: str, sub: str, name: str, download: int = 0):
    d = _dir(tid)
    if sub not in FILE_DIRS or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad path")
    if sub == "root" and name not in ROOT_FILES:
        raise HTTPException(404, "missing")
    p = (d / FILE_DIRS[sub] / name).resolve()
    if d.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "missing")
    kw = {"filename": _download_name(d, p)} if download else {}
    return FileResponse(p, media_type=MEDIA.get(p.suffix, "application/octet-stream"),
                        headers={"Cache-Control": "no-cache"}, **kw)


def _download_name(d: Path, p: Path) -> str:
    meta = tracks.read_json(d / "meta.json", {}) or {}
    return f"{tracks.slug(meta.get('title', 'track'))} - {p.name}"


_export_locks: dict[str, threading.Lock] = {}


@app.get("/api/tracks/{tid}/export")
def export(tid: str, full: int = 0):
    from .pipeline import export as EX
    d = _dir(tid)
    A = _analysis(d)
    base = tracks.slug(A["track"].get("title", "track"))
    name = f"{base} - {'Ableton project' if full else 'MIDI + guide'}.zip"
    with _export_locks.setdefault(tid, threading.Lock()):
        if full:
            # the full project is large (all stems): build a one-off copy and delete it after sending
            z = EX.build_zip(d, A, True, out=Path(tempfile.mkstemp(dir=d / "work" if (d / "work").exists() else d,
                                                                    suffix=".zip")[1]))
            return FileResponse(z, media_type="application/zip", filename=name,
                                background=BackgroundTask(lambda: z.unlink(missing_ok=True)))
        z = d / "export_midi.zip"
        if not z.exists():
            EX.build_zip(d, A, False)
    return FileResponse(z, media_type="application/zip", filename=name)


@app.get("/api/tracks/{tid}/guide", response_class=PlainTextResponse)
def guide(tid: str):
    p = _dir(tid) / "GUIDE.md"
    if not p.exists():
        raise HTTPException(404, "not analysed yet")
    return p.read_text(encoding="utf-8")


@app.post("/api/tracks/{tid}/reanalyze")
def reanalyze(tid: str, opts: dict = Body(default={})):
    """Redo the grid-dependent analysis with user corrections (tempo x2 / /2, bar-1 shift, meter)."""
    d = _dir(tid)
    if not (d / "cache" / "beats.json").exists():
        raise HTTPException(409, "Run the full analysis first.")
    cur = {} if opts.get("reset") else (tracks.read_json(d / "options.json", {}) or {})
    try:
        tf = float(opts.get("tempo_factor", 1.0))           # relative: x2 / x0.5
        ds = int(opts.get("downbeat_shift", 0))             # relative: +/- beats
    except (TypeError, ValueError):
        raise HTTPException(400, "bad options") from None
    combined = float(cur.get("tempo_factor", 1.0)) * tf
    if combined not in (0.5, 1.0, 2.0):
        raise HTTPException(400, "The tempo can be halved or doubled once from the detected value.")
    cur["tempo_factor"] = combined
    if opts.get("meter") in (2, 3, 4, 5, 6, 7):
        cur["meter"] = int(opts["meter"])
    meter = cur.get("meter") or (tracks.read_json(d / "analysis.json", {}) or {}).get("global", {}).get("meter", 4)
    cur["downbeat_shift"] = (int(cur.get("downbeat_shift", 0)) + ds) % int(meter)
    cur = {k: v for k, v in cur.items() if not (k == "tempo_factor" and v == 1.0) and not (k == "downbeat_shift" and v == 0)}
    tracks.write_json(d / "options.json", cur)
    worker.submit(tid, "analyze")
    return {"ok": True, "options": cur}


@app.post("/api/tracks/{tid}/retry")
def retry(tid: str, fresh: int = 0):
    """Run the analysis again: resume from cache, or (fresh=1) from separation onwards."""
    _dir(tid)
    worker.submit(tid, "separate" if fresh else None)
    return {"ok": True}


@app.post("/api/tracks/{tid}/cancel")
def cancel(tid: str):
    d = _dir(tid)
    if not worker.cancel(tid):
        raise HTTPException(409, "not queued or running")
    st = tracks.read_json(d / "status.json", {}) or {}
    if st.get("state") == "queued":
        st.update(state="cancelled")
        tracks.write_json(d / "status.json", st)
    return {"ok": True}


@app.post("/api/tracks/{tid}/rename")
def rename(tid: str, body: dict = Body(default={})):
    _dir(tid)
    try:
        tracks.rename(tid, str(body.get("title", "")))
    except ValueError:
        raise HTTPException(400, "Title can't be empty.") from None
    for z in ("export_full.zip", "export_midi.zip"):
        (tracks.path(tid) / z).unlink(missing_ok=True)
    return {"ok": True}


@app.delete("/api/tracks/{tid}")
def delete(tid: str):
    d = _dir(tid)
    if worker.current == tid:
        worker.cancel(tid)
        for _ in range(50):                      # give the pipeline a moment to exit
            if worker.current != tid:
                break
            time.sleep(0.1)
    else:
        worker.cancel(tid)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


# ----------------------------------------------------------------------------- settings / Ableton export
def _settings() -> dict:
    s = config.settings()
    if not s.get("ableton_dir"):
        s["ableton_dir"] = system.default_ableton_folder()
    return s


@app.get("/api/settings")
def get_settings():
    from .pipeline import als
    s = _settings()
    ids = tracks.all_ids()
    return {**s, "als_available": als.available(), "version": __version__,
            "stem_models": {k: v["label"] for k, v in STEM_MODELS.items()},
            "data_dir": system.to_display(config.DATA), "n_tracks": len(ids),
            "n_stale": len(_stale_ids()),
            "disk_bytes": tracks.disk_usage(), "device": config.device()}


def _stale_ids() -> list[str]:
    out = []
    for tid in tracks.all_ids():
        s = tracks.summary(tid) or {}
        if s.get("stale") and s.get("state") == "done":
            out.append(tid)
    return out


@app.post("/api/refresh_stale")
def refresh_stale():
    """Re-run the (fast, cached-stems) analysis for every track made by an older pipeline version."""
    ids = _stale_ids()
    for tid in ids:
        worker.submit(tid, "analyze")
    return {"ok": True, "queued": len(ids)}


@app.post("/api/settings")
def set_settings(body: dict = Body(default={})):
    s = config.settings()
    if "ableton_dir" in body:
        s["ableton_dir"] = str(body["ableton_dir"]).strip()[:500]
    if body.get("stem_model") in STEM_MODELS:
        s["stem_model"] = body["stem_model"]
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tracks.write_json(config.SETTINGS_FILE, s)
    return s


MARKER = ".track-anatomy-export"


@app.post("/api/tracks/{tid}/save_project")
def save_project(tid: str, body: dict = Body(default={})):
    """Write an Ableton project folder into the user's chosen folder.

    Never deletes anything the app didn't create: an existing folder of the same name is only
    replaced if it carries our marker file; otherwise a numbered name is used."""
    from .pipeline import export as EX
    d = _dir(tid)
    A = _analysis(d)
    target = (body.get("ableton_dir") or _settings().get("ableton_dir") or "").strip()
    if not target:
        raise HTTPException(400, "Choose a folder first.")
    root = system.to_local(target)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Can't create {target}: {e}") from None
    base = f"{EX.safe_name(A['track']['title'])} Project"
    proj = root / base
    n = 2
    while proj.exists() and not (proj / MARKER).exists():
        proj = root / f"{base} {n}"
        n += 1
    if proj.exists():
        shutil.rmtree(proj)                      # our own earlier export of this track
    proj.mkdir(parents=True)
    (proj / MARKER).write_text(json.dumps({"track": tid, "created": time.time()}), encoding="utf-8")
    als_path = EX.build_project(d, A, proj, abs_root=system.to_live(proj))
    return {"folder": system.to_display(proj), "als": system.to_display(als_path) if als_path else None}


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "current": worker.current, "queued": list(worker.pending)}


# ----------------------------------------------------------------------------- UI
class _Static(StaticFiles):
    """Always revalidate UI files (ES modules otherwise stick in the browser cache)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _Static(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})
