#!/usr/bin/env bash
# One-shot idempotent bootstrap on an Engaging login node. Run under nohup:
#   nohup bash setup_cluster.sh > logs/setup.log 2>&1 &
# Repo + all data live on POOL (HOME is file-count-starved).
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"
export HF_HOME="${HF_HOME:-/orcd/pool/008/$USER/hf}"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p logs "$HF_HOME"
UV="$HOME/.local/bin/uv"

echo "=== [1/6] python env ==="
[ -d .venv ] || "$UV" venv --python 3.12 .venv
"$UV" pip install -p .venv torch transformers accelerate tokenizers \
  safetensors "huggingface_hub[hf_transfer]" numpy scipy pandas matplotlib
.venv/bin/python -c "import torch, transformers; print('torch', torch.__version__, 'cuda-build', torch.version.cuda, '| transformers', transformers.__version__)"

echo "=== [2/6] corpora clones ==="
./fetch_corpora.sh

echo "=== [3/6] arXiv corpus (pinned manifest) ==="
.venv/bin/python arxiv_fetch.py --from-manifest

echo "=== [4/6] model ladder ==="
bash models_download.sh

echo "=== [5/6] Phase 1 streams ==="
.venv/bin/python prep_streams.py

echo "=== [6/6] Phase 2 pools ==="
.venv/bin/python prep_pools.py

echo "SETUP-ALL-DONE"
