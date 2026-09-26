"""Settings shared by the server and the pipeline.

Everything machine-specific can be overridden with environment variables:

  TRACK_ANATOMY_DATA          where tracks, stems and exports live      (default: ./data in a checkout)
  TRACK_ANATOMY_MODELS        where separation models are downloaded    (default: ./models in a checkout)
                              (installed as a package: ~/.local/share/track-anatomy/{data,models})
  TRACK_ANATOMY_HOST / _PORT  server bind address                       (default: 127.0.0.1:8102)
  TRACK_ANATOMY_DEVICE        cuda | mps | cpu                          (default: auto)
  TRACK_ANATOMY_MAX_UPLOAD_MB largest accepted upload                   (default: 1024)
  TRACK_ANATOMY_MAX_MINUTES   longest accepted track                    (default: 30)
  TRACK_ANATOMY_ABLETON_TEMPLATE  path to Live's DefaultLiveSet.als (auto-detected otherwise)
"""
import os
from pathlib import Path

__version__ = "0.2.0"

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
# running from a git checkout keeps data next to the code; an installed package uses the user's data dir
IN_CHECKOUT = (ROOT / "pyproject.toml").exists()
HOME_DIR = (ROOT if IN_CHECKOUT else
            Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "track-anatomy")


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser().resolve() if v else default


DATA = _env_path("TRACK_ANATOMY_DATA", HOME_DIR / "data")
TRACKS = DATA / "tracks"
MODELS = _env_path("TRACK_ANATOMY_MODELS", HOME_DIR / "models")

HOST = os.environ.get("TRACK_ANATOMY_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRACK_ANATOMY_PORT", "8102"))
MAX_UPLOAD_MB = int(os.environ.get("TRACK_ANATOMY_MAX_UPLOAD_MB", "1024"))
MAX_MINUTES = float(os.environ.get("TRACK_ANATOMY_MAX_MINUTES", "30"))
MIN_SECONDS = 8.0            # shorter clips can't be analysed meaningfully

SR = 44100
PIPELINE_VERSION = 3          # bump when analysis results change; older tracks are flagged for a refresh

# Separation models (python-audio-separator names). Both 6-stem models give
# vocals, drums, bass, guitar, piano, other. See README "Models and licenses".
STEM_MODELS = {
    "bs-roformer-sw": {"file": "BS-Roformer-SW.ckpt", "label": "BS-RoFormer SW (best quality; license unknown)"},
    "htdemucs-6s": {"file": "htdemucs_6s.yaml", "label": "Demucs htdemucs_6s (MIT; lower quality)"},
}
DEFAULT_STEM_MODEL = os.environ.get("TRACK_ANATOMY_STEM_MODEL", "bs-roformer-sw")
DRUM_MODEL = "MDX23C-DrumSep-aufr33-jarredou.ckpt"     # kick, snare, toms, hh, ride, crash (CC BY-NC)
SETTINGS_FILE = DATA / "settings.json"


def settings() -> dict:
    """User settings saved from the UI (data/settings.json), with defaults filled in."""
    import json
    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    if s.get("stem_model") not in STEM_MODELS:
        s["stem_model"] = DEFAULT_STEM_MODEL if DEFAULT_STEM_MODEL in STEM_MODELS else "bs-roformer-sw"
    return s


def stem_model_file() -> str:
    return STEM_MODELS[settings()["stem_model"]]["file"]

TOP_STEMS = ["drums", "bass", "vocals", "piano", "guitar", "other"]
DRUM_PIECES = ["kick", "snare", "toms", "hh", "ride", "crash"]


def device() -> str:
    """Torch device for the models: env override, else CUDA, else Apple MPS, else CPU."""
    forced = os.environ.get("TRACK_ANATOMY_DEVICE")
    if forced:
        return forced
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


for d in (TRACKS, MODELS):
    d.mkdir(parents=True, exist_ok=True)
