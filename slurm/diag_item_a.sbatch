#!/bin/bash
#SBATCH -J physlean-diagA
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=48G
#SBATCH -t 01:30:00
# Item-A incident diagnostic (PREREG §13 frozen decision rule). Submit:
#   sbatch -p mit_normal_gpu --gres=gpu:l40s:1 slurm/diag_item_a.sbatch
# Exit 0 = all gates pass (numerics verdict; A re-spec may proceed);
# exit 1 = hard stop (cache/position bug path).
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
.venv/bin/python diag_item_a.py
