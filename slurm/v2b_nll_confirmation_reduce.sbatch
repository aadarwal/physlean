#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-reduce-%j.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"

PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"
MODE="${V2B_CONFIRM_REDUCE_MODE:?set model or study}"
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

case "$MODE" in
  model)
    "$PYTHON" eval_v2b_nll_confirmation.py reduce-model "${common[@]}" \
      --model-id "${V2B_CONFIRM_MODEL_ID:?}" \
      --target-dir "${V2B_CONFIRM_TARGET_DIR:?}" \
      --out "${V2B_CONFIRM_MODEL_COMPLETE_OUT:?}"
    ;;
  study)
    "$PYTHON" eval_v2b_nll_confirmation.py reduce-study "${common[@]}" \
      --model-complete "${V2B_CONFIRM_MODEL_COMPLETE_0:?}" \
      --model-complete "${V2B_CONFIRM_MODEL_COMPLETE_1:?}" \
      --model-complete "${V2B_CONFIRM_MODEL_COMPLETE_2:?}" \
      --model-complete "${V2B_CONFIRM_MODEL_COMPLETE_3:?}" \
      --out "${V2B_CONFIRM_STUDY_COMPLETE_OUT:?}"
    ;;
  *)
    echo "FATAL: V2B_CONFIRM_REDUCE_MODE must be model or study" >&2
    exit 2
    ;;
esac
