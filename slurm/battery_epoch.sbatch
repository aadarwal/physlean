#!/bin/bash
#SBATCH -J physlean-battery-epoch
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=128G
#SBATCH -t 03:00:00
# EPOCH2 instrument re-freeze (EPOCH2_NIGHT_AMENDMENT): per-tier battery
# at the epoch-2 tree for ANY tier, INCLUDING q25c-1.5b — legitimate only
# because the epoch discipline first rebinds (rename-preserves) every
# committed battery, so each sealed artifact survives byte-identically
# under its rebound name and sealed evidence keeps binding it by sha.
# This launcher refuses to run while the standard battery file for the
# tier is still committed at its standard path (rebind first).
# Submit per tier (H200 + --mem=256G for 14b/32b):
#   sbatch [-p/--gres/--mem per tier] \
#     --export=ALL,V2B_MODEL_TIER=<tier> slurm/battery_epoch.sbatch
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

V2B_MODEL_TIER="${V2B_MODEL_TIER:?export V2B_MODEL_TIER}"
case "$V2B_MODEL_TIER" in
  q25c-0.5b) V2B_BATTERY_FILE="results_v2/battery/battery_pilot_0p5b.json" ;;
  q25c-1.5b) V2B_BATTERY_FILE="results_v2/battery/battery_pilot_1p5b.json" ;;
  q25c-3b)   V2B_BATTERY_FILE="results_v2/battery/battery_pilot_3b.json" ;;
  q25c-7b)   V2B_BATTERY_FILE="results_v2/battery/battery_pilot_7b.json" ;;
  q25c-14b|q25c-32b)
    if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -qi h200; then
      echo "$V2B_MODEL_TIER battery requires an H200 GPU" >&2
      exit 1
    fi
    case "$V2B_MODEL_TIER" in
      q25c-14b) V2B_BATTERY_FILE="results_v2/battery/battery_pilot_14b.json" ;;
      q25c-32b)
        V2B_BATTERY_FILE="results_v2/battery/battery_pilot_32b.json"
        export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
        ;;
    esac
    ;;
  *)
    echo "unknown tier: $V2B_MODEL_TIER" >&2
    exit 1
    ;;
esac

# Epoch precondition: the standard battery path must NOT be committed
# evidence right now (the rebind commit renames it first). An untracked
# leftover file is quarantined by the battery program itself.
if git ls-files --error-unmatch "$V2B_BATTERY_FILE" >/dev/null 2>&1; then
  echo "committed battery still at standard path; rebind first: $V2B_BATTERY_FILE" >&2
  exit 1
fi

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing epoch battery" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }
.venv/bin/python validity_battery.py --pilot-tier "$V2B_MODEL_TIER"
