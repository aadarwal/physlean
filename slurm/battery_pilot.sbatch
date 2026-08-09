#!/bin/bash
#SBATCH -J physlean-battery-pilot-1p5b
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 01:30:00
# Prospective V2-b pilot-model instrument battery. Submit:
#   sbatch -p mit_normal_gpu --gres=gpu:l40s:1 slurm/battery_pilot.sbatch
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

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing pilot battery" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }
.venv/bin/python validity_battery.py --pilot
