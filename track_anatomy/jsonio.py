"""JSON helpers: numpy-aware, UTF-8, atomic writes (readers never see half-written files)."""
import json
import os
from pathlib import Path

import numpy as np


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def dumps(obj, **kw) -> str:
    return json.dumps(obj, default=_default, **kw)


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(dumps(obj), encoding="utf-8")
    os.replace(tmp, p)
