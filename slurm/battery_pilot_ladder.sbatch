#!/bin/bash
#SBATCH -J physlean-battery-pilot-ladder
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=128G
#SBATCH -t 03:00:00
# Prospective per-tier V2-b ladder instrument battery
# (NLL_LADDER_EXPLORATORY_AMENDMENT). Submit one tier at a time:
#   sbatch -p mit_normal_gpu --gres=gpu:l40s:1 \
#     --export=ALL,V2B_MODEL_TIER=q25c-3b slurm/battery_pilot_ladder.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export HF_HOME="$V2B_POOL_BASE/hf"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TORCH_HOME="$V2B_POOL_BASE/torch-home"
export MPLCONFIGDIR="$V2B_POOL_BASE/mpl"
export TRITON_CACHE_DIR="$V2B_POOL_BASE/triton-cache"
export CUDA_CACHE_PATH="$V2B_POOL_BASE/cuda-cache"
export ELAN_HOME="$V2B_POOL_BASE/elan"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" \
  "$TRITON_CACHE_DIR" "$CUDA_CACHE_PATH" "$ELAN_HOME" "$TMPDIR" logs

V2B_MODEL_TIER="${V2B_MODEL_TIER:?export V2B_MODEL_TIER (q25c-0.5b|q25c-3b|q25c-7b)}"
case "$V2B_MODEL_TIER" in
  q25c-0.5b|q25c-3b|q25c-7b) ;;
  q25c-1.5b)
    echo "the q25c-1.5b battery is sealed evidence; never rerun it" >&2
    exit 1
    ;;
  *)
    echo "unknown ladder tier: $V2B_MODEL_TIER" >&2
    exit 1
    ;;
esac

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing ladder battery" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }
.venv/bin/python validity_battery.py --pilot-tier "$V2B_MODEL_TIER"
