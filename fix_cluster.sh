#!/usr/bin/env bash
# Repair a half-broken cluster bootstrap (empty husk clones from a killed
# first run; venv missing huggingface_hub; streams built from 0-byte
# corpora). Idempotent; safe to re-run. Ends with FIX-ALL-DONE — submission
# stays MANUAL: verify data/streams_stats.json + model cache before
# bash submit_all.sh.
set -euo pipefail  # expected failures are explicitly guarded; anything unguarded aborts
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"
# HOME is at ~98% of its FILE quota: every cache goes to POOL
POOL_BASE="/orcd/pool/008/$USER"
export HF_HOME="${HF_HOME:-$POOL_BASE/hf}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$POOL_BASE/uv-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$POOL_BASE/xdg-cache}"
export TORCH_HOME="${TORCH_HOME:-$POOL_BASE/torch-home}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$POOL_BASE/mpl}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$POOL_BASE/triton-cache}"
mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" "$TRITON_CACHE_DIR"
export CUDA_CACHE_PATH="$POOL_BASE/cuda-cache"
# Lean v2 tooling (G3.5) must never touch HOME: predeclared here so any
# elan/Lake install lands on POOL (XDG_CACHE_HOME already covers ~/.cache)
export ELAN_HOME="$POOL_BASE/elan"
mkdir -p "$CUDA_CACHE_PATH" "$ELAN_HOME"
export HF_HUB_ENABLE_HF_TRANSFER=1
export GIT_TERMINAL_PROMPT=0

echo "=== [fix 1/6] drop broken clones ==="
for d in corpora/*/; do
  n=$(basename "$d")
  [ "$n" = arxiv ] && continue
  git -C "$d" log -1 --oneline >/dev/null 2>&1 \
    || { echo "[broken] $n — removing"; rm -rf "$d"; }
done

echo "=== [fix 2/6] env (venv created if absent; install must succeed) ==="
[ -d .venv ] || "$HOME/.local/bin/uv" venv --python 3.12 .venv \
  || { echo "VENV-CREATE-FAILED"; exit 1; }
"$HOME/.local/bin/uv" pip install -p .venv torch "transformers==5.14.1" \
  accelerate tokenizers safetensors "huggingface_hub[hf_transfer]" numpy \
  scipy pandas matplotlib \
  || { echo "ENV-INSTALL-FAILED"; exit 1; }
.venv/bin/python - <<'PYEOF' || { echo "ENV-STILL-BROKEN"; exit 1; }
import torch, huggingface_hub as h, transformers
assert transformers.__version__ == "5.14.1", transformers.__version__
print("torch", torch.__version__, "| hub", h.__version__,
      "| tf", transformers.__version__, "(pin asserted)")
PYEOF
# freeze the exact environment used for every NLL dump (PREREG §4)
mkdir -p results_v2/env
"$HOME/.local/bin/uv" pip freeze -p .venv > results_v2/env/freeze-cluster.txt
{ .venv/bin/python -c "import torch; print('torch', torch.__version__, 'cuda-build', torch.version.cuda)"; \
  nvidia-smi -L 2>/dev/null || echo "no GPU on login node (GPU recorded per job)"; \
  uname -a; } >> results_v2/env/freeze-cluster.txt

FAILED=0

echo "=== [fix 3/6] reclone missing corpora ==="
./fetch_corpora.sh || FAILED=1
for d in corpora/*/; do
  n=$(basename "$d")
  [ "$n" = arxiv ] && continue
  git -C "$d" log -1 --oneline >/dev/null 2>&1 \
    || { echo "[STILL-BROKEN] $n"; FAILED=1; }
done

echo "=== [fix 3b/6] arXiv pinned refetch (exact-set fail-closed) ==="
# the script itself validates the EXACT expected set (missing/mismatch ->
# nonzero); loose shell counts were fail-open (review fix)
.venv/bin/python arxiv_fetch.py --from-manifest || { echo "[ARXIV-FAILED]"; FAILED=1; }
echo "arxiv: old=$(ls corpora/arxiv/old 2>/dev/null | wc -l) new=$(ls corpora/arxiv/new 2>/dev/null | wc -l) (informational)"
# replay pinned corpus SHAs + verify arXiv identity when a committed lock
# exists (runs AFTER arxiv fetch so checksums.json is present); on the
# first acquisition there is no lock — `corpus_lock.py write` at the G1
# boundary emits it for review + commit
.venv/bin/python corpus_lock.py checkout || FAILED=1

echo "=== [fix 4/6] models ==="
# pipefail propagates models_download's own exit (incl. METADATA-FAILED,
# which produces no [FAIL] line) through the tee|tail pipe
bash models_download.sh 2>&1 | tee /tmp/models_dl.$$ | tail -40 || FAILED=1
if grep -q "\[FAIL\]" /tmp/models_dl.$$; then
  echo "[MODEL-FAILURES] see above"; FAILED=1
fi
rm -f /tmp/models_dl.$$

echo "=== [fix 5/6] streams ==="
.venv/bin/python prep_streams.py || FAILED=1

echo "=== [fix 6/6] pools: DEFERRED to G6 (Phase 2 hard-blocked) ==="

# fail-closed: the done marker only appears when every step verified
if [ "$FAILED" -eq 0 ]; then
  echo "FIX-ALL-DONE"
else
  echo "FIX-INCOMPLETE (see failures above)"
  exit 1
fi
