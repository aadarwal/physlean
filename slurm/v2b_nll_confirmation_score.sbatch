#!/bin/bash
#SBATCH -p mit_normal_gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH --time=06:00:00
#SBATCH --requeue
#SBATCH -o logs/v2b-confirm-score-%A_%a.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"

PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"
MODEL="${V2B_CONFIRM_MODEL_ID:?set one exact protocol model id}"
SHARD="${SLURM_ARRAY_TASK_ID:?submit score as battery-sized shard array}"
TARGET_DIR="${V2B_CONFIRM_TARGET_DIR:?set model target-score directory}"
mkdir -p "$TARGET_DIR"

common=(
  --implementation-freeze "${V2B_CONFIRM_FREEZE:?}"
  --source-gate "${V2B_CONFIRM_GATE:?}"
  --sample "${V2B_CONFIRM_SAMPLE:?}"
  --assembly "${V2B_CONFIRM_ASSEMBLY:?}"
  --battery "${V2B_CONFIRM_BATTERY_0:?}"
  --battery "${V2B_CONFIRM_BATTERY_1:?}"
  --battery "${V2B_CONFIRM_BATTERY_2:?}"
  --battery "${V2B_CONFIRM_BATTERY_3:?}"
  --salt-commitment "${V2B_CONFIRM_SALT_COMMITMENT:?}"
)
"$PYTHON" eval_v2b_nll_confirmation.py score "${common[@]}" \
  --model-id "$MODEL" --shard-index "$SHARD" \
  --target-dir "$TARGET_DIR"
