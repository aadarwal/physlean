#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-mask-%j.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"
PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"

"$PYTHON" prepare_v2b_nll_confirmation_masked.py \
  --private-salt "${V2B_CONFIRM_PRIVATE_SALT:?}" \
  --salt-commitment "${V2B_CONFIRM_SALT_COMMITMENT:?}" \
  --assembly "${V2B_CONFIRM_ASSEMBLY:?}" \
  --study-complete "${V2B_CONFIRM_STUDY_COMPLETE:?}" \
  --out "${V2B_CONFIRM_MASKED_OUT:?}"
