"""Source separation with python-audio-separator.

Stems are written as 24-bit FLAC at the pipeline's working gain (see run.py),
so they never clip and still sum back to the working mix.
"""
import gc
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import MODELS, SR
from . import audio


def _separator(out_dir: Path):
    from audio_separator.separator import Separator
    return Separator(log_level=logging.WARNING, model_file_dir=str(MODELS), output_dir=str(out_dir),
                     output_format="WAV", normalization_threshold=1.0, sample_rate=SR)


def _hook_progress(progress):
    """Route audio-separator's tqdm bars (model download, chunk loop) to `progress(kind, frac)`."""
    if progress is None:
        return
    import audio_separator.separator.separator as sp
    from audio_separator.separator.architectures import mdxc_separator as mx
    from tqdm import tqdm

    def make(kind):
        class Hooked(tqdm):
            def __init__(self, *a, **k):
                k["disable"] = True           # keep logs clean; we report ourselves
                super().__init__(*a, **k)
                self._total = k.get("total") or (len(a[0]) if a and hasattr(a[0], "__len__") else 0)
                self._done = 0

            def __iter__(self):
                for x in self.iterable:
                    yield x
                    self._done += 1
                    if self._total:
                        progress(kind, self._done / self._total)

            def update(self, n=1):
                self._done += n
                if self._total:
                    progress(kind, min(1.0, self._done / self._total))
                return super().update(n)
        return Hooked

    sp.tqdm = make("download")
    mx.tqdm = make("separate")


def separate(model: str, src_wav: Path, dst_dir: Path, names: list[str], progress=None) -> dict[str, Path]:
    """Run one separation model; return {stem_name: flac_path} for the requested names.
    progress(kind, frac) is called with kind in {"download", "separate"}."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    _hook_progress(progress)
    with tempfile.TemporaryDirectory(dir=dst_dir) as tmp:
        tmp = Path(tmp)
        sep = _separator(tmp)
        try:
            sep.load_model(model)
        except Exception:
            # most often a download that was interrupted earlier: delete it and fetch again
            _forget_model(model)
            sep = _separator(tmp)
            sep.load_model(model)
        outputs = sep.separate(str(src_wav))
        found = {}
        for o in outputs:
            p = Path(o) if Path(o).is_absolute() else tmp / o
            groups = re.findall(r"\(([^)]+)\)", p.stem)
            if not groups:
                continue
            found[groups[-1].strip().lower()] = p
        result = {}
        for name in names:
            p = found.get(name)
            dst = dst_dir / f"{name}.flac"
            part = tmp / f"{name}.part.flac"
            if p is None:
                # model didn't produce it: write silence of the right length
                n = sf.info(str(src_wav)).frames
                audio.save(part, np.zeros((n, 2), np.float32))
            else:
                x, _ = sf.read(str(p), dtype="float32", always_2d=True)
                audio.save(part, x)
                del x
            os.replace(part, dst)          # a stem is either complete or absent
            result[name] = dst
        del sep
    _free_gpu()
    return result


def _forget_model(model: str) -> None:
    """Delete a (possibly truncated) model checkpoint so audio-separator downloads it again."""
    stem = Path(model).stem
    for p in MODELS.glob(f"{stem}*"):
        if p.suffix in (".ckpt", ".pth", ".onnx", ".th", ".yaml"):
            p.unlink(missing_ok=True)


def _free_gpu():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def write_float_wav(src_flac: Path, dst_wav: Path) -> Path:
    x, _ = sf.read(str(src_flac), dtype="float32", always_2d=True)
    sf.write(str(dst_wav), x, SR, subtype="FLOAT")
    return dst_wav


def cleanup(paths):
    for p in paths:
        try:
            if Path(p).is_dir():
                shutil.rmtree(p)
            else:
                Path(p).unlink(missing_ok=True)
        except Exception:
            pass
