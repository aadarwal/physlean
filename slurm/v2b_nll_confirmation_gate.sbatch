#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH -o logs/v2b-confirm-gate-%A_%a.out

set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?submit from the frozen confirmation checkout}"

PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"
test -x "$PYTHON"
MODE="${V2B_CONFIRM_GATE_MODE:?set fragment or reduce}"
FREEZE="${V2B_CONFIRM_FREEZE:?set exact committed implementation freeze}"

case "$MODE" in
  fragment)
    INDEX="${SLURM_ARRAY_TASK_ID:?submit fragment mode as an array}"
    COUNT="${V2B_CONFIRM_GATE_SHARD_COUNT:?set frozen shard count}"
    OUT_DIR="${V2B_CONFIRM_GATE_FRAGMENT_DIR:?set fragment directory}"
    mkdir -p "$OUT_DIR"
    "$PYTHON" prepare_v2b_nll_confirmation_gate.py fragment \
      --shard-index "$INDEX" --shard-count "$COUNT" \
      --implementation-freeze "$FREEZE" \
      --out "$OUT_DIR/fragment-${INDEX}.json"
    ;;
  reduce)
    COUNT="${V2B_CONFIRM_GATE_SHARD_COUNT:?set frozen shard count}"
    OUT_DIR="${V2B_CONFIRM_GATE_FRAGMENT_DIR:?set fragment directory}"
    OUT="${V2B_CONFIRM_GATE_OUT:?set reduced gate output}"
    fragments=()
    for ((index=0; index<COUNT; index++)); do
      path="$OUT_DIR/fragment-${index}.json"
      test -f "$path"
      fragments+=("$path")
    done
    "$PYTHON" prepare_v2b_nll_confirmation_gate.py reduce \
      --implementation-freeze "$FREEZE" --out "$OUT" \
      --fragments "${fragments[@]}"
    ;;
  *)
    echo "FATAL: V2B_CONFIRM_GATE_MODE must be fragment or reduce" >&2
    exit 2
    ;;
esac
