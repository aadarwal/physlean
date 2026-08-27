#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-analysis-%j.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"
PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"

"$PYTHON" analyze_v2b_nll_confirmation.py \
  --reveal "${V2B_CONFIRM_REVEAL:?}" \
  --assembly "${V2B_CONFIRM_ASSEMBLY:?}" \
  --sample "${V2B_CONFIRM_SAMPLE:?}" \
  --masked "${V2B_CONFIRM_MASKED:?}" \
  --fixed-n "${V2B_CONFIRM_FIXED_N:?}" \
  --implementation-freeze "${V2B_CONFIRM_FREEZE:?}" \
  --salt-commitment "${V2B_CONFIRM_SALT_COMMITMENT:?}" \
  --out "${V2B_CONFIRM_ANALYSIS_OUT:?}"
