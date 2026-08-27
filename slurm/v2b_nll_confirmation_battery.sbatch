#!/bin/bash
#SBATCH -p mit_normal_gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-battery-%A_%a.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"

PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"
INDEX="${SLURM_ARRAY_TASK_ID:?submit battery as array 0-3}"
MODELS=(q25c-0.5b q25c-1.5b q25c-3b q25c-7b)
if (( INDEX < 0 || INDEX >= ${#MODELS[@]} )); then
  echo "FATAL: battery array index must be 0..3" >&2
  exit 2
fi
MODEL="${MODELS[$INDEX]}"
OUT_DIR="${V2B_CONFIRM_BATTERY_DIR:?set battery output directory}"
mkdir -p "$OUT_DIR"

"$PYTHON" v2b_nll_confirmation_battery.py \
  --model-id "$MODEL" \
  --assembly "${V2B_CONFIRM_ASSEMBLY:?set committed assembly}" \
  --sample "${V2B_CONFIRM_SAMPLE:?set committed sample}" \
  --source-gate "${V2B_CONFIRM_GATE:?set committed source gate}" \
  --implementation-freeze "${V2B_CONFIRM_FREEZE:?set implementation freeze}" \
  --out "$OUT_DIR/${MODEL}.json"
