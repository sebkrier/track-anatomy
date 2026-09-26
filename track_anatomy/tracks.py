"""Track folders: validate and create from an upload, list, read status."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import PIPELINE_VERSION, TRACKS
from .jsonio import read_json, write_json  # noqa: F401  (re-exported)

AUDIO_EXT = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".mp4", ".webm"}
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:40] or "track"


def probe(src: Path) -> dict:
    """ffprobe: {'audio': bool, 'duration': seconds or None}."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                              "-of", "json", str(src)], capture_output=True, text=True, timeout=60)
        info = json.loads(out.stdout or "{}")
    except Exception:
        return {"audio": False, "duration": None}
    audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    try:
        dur = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    return {"audio": audio, "duration": dur}


def validate(src: Path) -> str | None:
    """A user-facing reason to reject the file, or None if it looks analysable."""
    from .config import MAX_MINUTES, MIN_SECONDS
    info = probe(src)
    if not info["audio"]:
        return "This doesn't look like an audio file (no audio stream found)."
    d = info["duration"]
    if d is not None and d < MIN_SECONDS:
        return f"Too short ({d:.1f} s). Track Anatomy needs at least {MIN_SECONDS:.0f} seconds of music."
    if d is not None and d > MAX_MINUTES * 60:
        return (f"Too long ({d / 60:.0f} min). The limit is {MAX_MINUTES:.0f} min "
                f"(set TRACK_ANATOMY_MAX_MINUTES to change it).")
    return None


def create(src: Path, filename: str) -> str:
    """Copy an audio file into a new track folder (content-addressed: re-uploads reuse it)."""
    h = hashlib.sha1()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest()
    # the same audio under another filename is the same track
    for p in TRACKS.glob(f"*-{sha[:8]}"):
        if (read_json(p / "meta.json", {}) or {}).get("sha1") == sha:
            return p.name
    title = Path(filename).stem.strip() or "Untitled"
    tid = f"{slug(title)}-{sha[:8]}"
    d = TRACKS / tid
    d.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    ext = ext if ext in AUDIO_EXT else ".audio"
    shutil.copyfile(src, d / f"source{ext}")
    write_json(d / "meta.json", {"id": tid, "title": title, "filename": Path(filename).name, "source": f"source{ext}",
                                 "sha1": sha, "created": time.time()})
    write_json(d / "status.json", {"state": "queued", "progress": 0, "updated": time.time()})
    return tid


def path(tid: str) -> Path:
    if not ID_RE.fullmatch(tid or ""):
        raise ValueError("bad id")
    return TRACKS / tid


def rename(tid: str, title: str) -> None:
    """Change the display title (also in the analysis and summary, so everything agrees)."""
    d = path(tid)
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty title")
    meta = read_json(d / "meta.json", {})
    meta.update(title=title, title_locked=True)
    write_json(d / "meta.json", meta)
    for name in ("analysis.json", "summary.json"):
        obj = read_json(d / name)
        if obj:
            if name == "analysis.json":
                obj["track"]["title"] = title
            else:
                obj["title"] = title
            write_json(d / name, obj)


def summary(tid: str) -> dict | None:
    d = path(tid)
    meta = read_json(d / "meta.json")
    if not meta:
        return None
    st = read_json(d / "status.json", {}) or {}
    out = {"id": tid, "title": meta.get("title"), "artist": meta.get("artist", ""), "created": meta.get("created"),
           "state": st.get("state", "unknown"), "progress": st.get("progress", 0), "duration": meta.get("duration"),
           "step": st.get("step"), "error": st.get("error"), "input_error": bool(st.get("input_error"))}
    s = d / "summary.json"
    S = read_json(s)
    if (not S or "version" not in S) and (d / "analysis.json").exists():
        A = read_json(d / "analysis.json", {})         # analysed before summaries existed
        if A:
            try:
                from .pipeline.analyze import write_summary
                write_summary(d, A)
            except Exception:
                pass
    S = read_json(s)
    if S:
        out.update(bpm=S.get("bpm"), key=S.get("key"), bars=S.get("bars"), has_beat=S.get("has_beat", True),
                   sections=S.get("sections"), groups=S.get("groups"), analyzed=True,
                   stale=S.get("version", 1) < PIPELINE_VERSION)
    return out


def all_ids() -> list[str]:
    """Track ids, newest first (by upload time, which never changes)."""
    items = []
    for p in TRACKS.iterdir():
        if p.is_dir() and (p / "meta.json").exists():
            items.append(((read_json(p / "meta.json", {}) or {}).get("created", 0), p.name))
    return [tid for _, tid in sorted(items, reverse=True)]


def disk_usage() -> int:
    total = 0
    for root, _, files in os.walk(TRACKS):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
