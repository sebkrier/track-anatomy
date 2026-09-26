"""OS detection and path translation (Windows / WSL / macOS / Linux).

The server may run inside WSL while Ableton Live runs on Windows, so folders typed by the
user ("D:\\Music") and paths written into Live sets need translating between the two worlds.
"""
import glob
import os
import sys
from functools import lru_cache
from pathlib import Path, PureWindowsPath


@lru_cache(maxsize=1)
def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return False


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_mac() -> bool:
    return sys.platform == "darwin"


def to_local(p: str) -> Path:
    """A folder typed by the user -> a path this process can write to."""
    p = p.strip().strip('"').strip("'")
    if is_wsl() and p.startswith("\\\\"):
        # \\wsl.localhost\<distro>\home\... or \\wsl$\<distro>\home\... -> /home/...
        parts = PureWindowsPath(p).parts
        if len(parts) and parts[0].lower().startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")):
            return Path("/", *parts[1:])
    if is_wsl() and len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = PureWindowsPath(p).as_posix()[2:].lstrip("/")
        return Path("/mnt") / drive / rest
    return Path(p).expanduser()


def to_live(p: Path) -> str:
    """A local path -> how Ableton Live (possibly on the Windows side) should see it."""
    s = str(p)
    if is_wsl() and s.startswith("/mnt/") and len(s) >= 6 and (len(s) == 6 or s[6] == "/"):
        return f"{s[5].upper()}:/{s[7:]}"
    if is_windows():
        return PureWindowsPath(s).as_posix()
    return s


def to_display(p: Path) -> str:
    """How to show a local path to the user (Windows style under WSL/Windows)."""
    s = to_live(p)
    if is_windows():
        return s.replace("/", "\\")
    if is_wsl():
        if s[1:2] == ":":                               # /mnt/d/... became D:/...
            return s.replace("/", "\\")
        distro = os.environ.get("WSL_DISTRO_NAME")      # Linux filesystem: the path Explorer uses
        return f"\\\\wsl.localhost\\{distro}{s}".replace("/", "\\") if distro else s
    return s


def ableton_template_candidates() -> list[str]:
    pats = []
    env = os.environ.get("TRACK_ANATOMY_ABLETON_TEMPLATE")
    if env:
        pats.append(env)
    tail = "Resources/Builtin/Templates/DefaultLiveSet.als"
    if is_wsl():
        pats += [f"/mnt/c/ProgramData/Ableton/Live 1*/{tail}"]
    if is_windows():
        pats += [f"C:/ProgramData/Ableton/Live 1*/{tail}"]
    if is_mac():
        pats += ["/Applications/Ableton Live 1*.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als"]
    found = []
    for pat in pats:
        found += sorted(glob.glob(pat))
    return found


def default_ableton_folder() -> str:
    """A sensible place to save exported projects, shown (and editable) in the UI."""
    home = Path.home()
    cands = []
    if is_wsl():
        for user_dir in sorted(glob.glob("/mnt/c/Users/*/Documents")):
            if Path(user_dir, "Ableton").exists():
                cands.append(Path(user_dir) / "Ableton" / "Track Anatomy")
    cands += [home / "Documents" / "Ableton" / "Track Anatomy", home / "Music" / "Ableton" / "Track Anatomy"]
    for c in cands:
        if c.parent.exists():
            return to_display(c)
    return to_display(home / "Track Anatomy exports")
