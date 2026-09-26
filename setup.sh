#!/usr/bin/env bash
# One-off environment setup for Linux, WSL2 and macOS. Needs uv and ffmpeg.
# Picks the right PyTorch build: CUDA when an NVIDIA GPU is visible, Apple Silicon (MPS) on
# macOS, CPU otherwise. Override with TORCH_INDEX=<url> (or TORCH_INDEX=pypi).
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
TORCH_VERSION=${TORCH_VERSION:-2.11.0}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: '$1' is not installed. $2" >&2
    exit 1
  fi
}
need uv "Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh"
if [[ "$(uname)" == "Darwin" ]]; then
  need ffmpeg "Install it with:  brew install ffmpeg"
else
  need ffmpeg "Install it with:  sudo apt install ffmpeg   (or your distro's equivalent)"
fi

if [[ -z "${TORCH_INDEX:-}" ]]; then
  if [[ "$(uname)" == "Darwin" ]]; then
    TORCH_INDEX=pypi
    echo "macOS: using PyTorch from PyPI (Apple Silicon GPU via MPS)."
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    TORCH_INDEX=https://download.pytorch.org/whl/cu128
    echo "NVIDIA GPU found: $(nvidia-smi -L | head -1)"
  else
    TORCH_INDEX=https://download.pytorch.org/whl/cpu
    echo "No NVIDIA GPU found: installing the CPU build (analysis takes roughly 10x longer)."
  fi
fi

uv venv --python 3.11 .venv
if [[ "$TORCH_INDEX" == "pypi" ]]; then
  uv pip install --python $PY "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
  EXTRA=()
else
  uv pip install --python $PY "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" --index-url "$TORCH_INDEX"
  EXTRA=(--extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match)
fi

# pin the torch build just installed so nothing below swaps it for another variant
$PY - > .venv/constraints.txt <<'EOF'
import torch, torchaudio
print(f"torch=={torch.__version__}\ntorchaudio=={torchaudio.__version__}")
EOF

uv pip install --python $PY -c .venv/constraints.txt "${EXTRA[@]}" -r requirements.txt

# basic-pitch: its Linux dependency list pulls TensorFlow; only its bundled ONNX model is used
uv pip install --python $PY --no-deps basic-pitch==0.4.0

mkdir -p data/tracks models
$PY -c "from track_anatomy.config import device; print('Models will run on:', device())"
echo "Setup complete. Start with ./run.sh and open http://localhost:8102 (models download on the first analysis, ~1.3 GB)."
