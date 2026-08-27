#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-prepare-%j.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"

PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"
MODE="${V2B_CONFIRM_PREPARE_MODE:?set sample, assembly, or salt}"
FREEZE="${V2B_CONFIRM_FREEZE:?set exact implementation freeze}"

case "$MODE" in
  sample)
    "$PYTHON" finalize_v2b_nll_confirmation_sample.py \
      --source-gate "${V2B_CONFIRM_GATE:?set reduced source gate}" \
      --candidates "${V2B_CONFIRM_CANDIDATES:?set candidate table}" \
      --pilot-sample "${V2B_CONFIRM_PILOT_SAMPLE:?set pilot sample}" \
      --implementation-freeze "$FREEZE" \
      --out "${V2B_CONFIRM_SAMPLE_OUT:?set sample output}"
    ;;
  assembly)
    "$PYTHON" prepare_v2b_nll_confirmation_assembly.py \
      --sample "${V2B_CONFIRM_SAMPLE:?set committed confirmation sample}" \
      --source-gate "${V2B_CONFIRM_GATE:?set committed source gate}" \
      --implementation-freeze "$FREEZE" \
      --out "${V2B_CONFIRM_ASSEMBLY_OUT:?set assembly output}"
    ;;
  salt)
    "$PYTHON" prepare_v2b_nll_confirmation_salt.py \
      --private-salt "${V2B_CONFIRM_PRIVATE_SALT:?set private salt path}" \
      --private-receipt "${V2B_CONFIRM_PRIVATE_RECEIPT:?set receipt path}" \
      --sample "${V2B_CONFIRM_SAMPLE:?set committed sample}" \
      --assembly "${V2B_CONFIRM_ASSEMBLY:?set committed assembly}" \
      --implementation-freeze "$FREEZE" \
      --out "${V2B_CONFIRM_SALT_OUT:?set public commitment output}"
    ;;
  *)
    echo "FATAL: V2B_CONFIRM_PREPARE_MODE must be sample, assembly, or salt" >&2
    exit 2
    ;;
esac
