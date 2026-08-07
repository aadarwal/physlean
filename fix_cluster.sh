#!/usr/bin/env bash
# Repair a half-broken cluster bootstrap (empty husk clones from a killed
# first run; venv missing huggingface_hub; streams built from 0-byte
# corpora). Idempotent; safe to re-run. Ends with FIX-ALL-DONE — submission
# stays MANUAL: verify data/streams_stats.json + model cache before
# bash submit_all.sh.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"
export HF_HOME="${HF_HOME:-/orcd/pool/008/$USER/hf}"
export HF_HUB_ENABLE_HF_TRANSFER=1
export GIT_TERMINAL_PROMPT=0

echo "=== [fix 1/6] drop broken clones ==="
for d in corpora/*/; do
  n=$(basename "$d")
  [ "$n" = arxiv ] && continue
  git -C "$d" log -1 --oneline >/dev/null 2>&1 \
    || { echo "[broken] $n — removing"; rm -rf "$d"; }
done

echo "=== [fix 2/6] env reinstall ==="
"$HOME/.local/bin/uv" pip install -p .venv torch transformers accelerate \
  tokenizers safetensors "huggingface_hub[hf_transfer]" numpy scipy pandas \
  matplotlib
.venv/bin/python -c "import torch, huggingface_hub as h, transformers; \
print('torch', torch.__version__, '| hub', h.__version__, '| tf', transformers.__version__)" \
  || { echo "ENV-STILL-BROKEN"; exit 1; }

echo "=== [fix 3/6] reclone missing corpora ==="
./fetch_corpora.sh

echo "=== [fix 4/6] models ==="
bash models_download.sh

echo "=== [fix 5/6] streams ==="
.venv/bin/python prep_streams.py

echo "=== [fix 6/6] pools ==="
.venv/bin/python prep_pools.py

echo "FIX-ALL-DONE"
