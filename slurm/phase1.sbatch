#!/bin/bash
#SBATCH -J physlean-p1
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH -t 06:00:00
#SBATCH --requeue
# Submit with:  sbatch -p <part> --time=<partition-valid-limit> \
#   --gres=gpu:<type>:1 \
#   --export=ALL,MODELS=<short,short,...> slurm/phase1.sbatch
# Cells are skipped once their .meta.json exists -> requeue/resubmit is safe.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
POOL_BASE="/orcd/pool/008/$USER"   # HOME is file-quota-starved: all
export HF_HOME="$POOL_BASE/hf"     # caches live on POOL
export XDG_CACHE_HOME="$POOL_BASE/xdg-cache"
export TORCH_HOME="$POOL_BASE/torch-home"
export MPLCONFIGDIR="$POOL_BASE/mpl"
export TRITON_CACHE_DIR="$POOL_BASE/triton-cache"
mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" "$TRITON_CACHE_DIR"
export CUDA_CACHE_PATH="$POOL_BASE/cuda-cache"
# Lean v2 tooling (G3.5) must never touch HOME: predeclared here so any
# elan/Lake install lands on POOL (XDG_CACHE_HOME already covers ~/.cache)
export ELAN_HOME="$POOL_BASE/elan"
mkdir -p "$CUDA_CACHE_PATH" "$ELAN_HOME"
nvidia-smi -L || { echo "NO GPU VISIBLE"; exit 1; }
.venv/bin/python run_phase1.py ${MODELS:+--models "$MODELS"}
echo "SHARD-DONE ${MODELS:-all}"
