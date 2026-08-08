#!/usr/bin/env bash
# Gated submission (PREREG §11/§12). DEFAULT is the G3a SENTINEL: one
# L40S job, Qwen2.5-Coder-0.5B only (battery-cached), across
# full+clean+XL+shuffled+per-doc — an instrument-viability pass reviewed
# BEFORE any larger spend (stop/go criteria are instrument viability,
# never whether any language looks favorable). Expansion is explicit:
#   bash submit_all.sh              # G3a sentinel
#   bash submit_all.sh --smallmid   # G3b: 4 x L40S small/mid shards
#   bash submit_all.sh --big        # + 2 x H200 big shards
# Phase 2 is hard-disabled (G6). Refuses without a passing G3 preflight.
set -euo pipefail  # expected failures are explicitly guarded; anything unguarded aborts
cd "$(dirname "$0")"
mkdir -p logs

SUBMIT_FAIL=0
P1() { sbatch -p "$1" --gres="$2" --export=ALL,MODELS="$3" slurm/phase1.sbatch \
  || { echo "SBATCH-FAILED for $3"; SUBMIT_FAIL=1; }; }

# parse mode FIRST, then run the gate matching the mode (review fix:
# gating g3b before parsing defeated battery-stage sentinel caching)
MODE=sentinel; DO_BIG=0
for a in "$@"; do
  case "$a" in
    --smallmid) MODE=smallmid;;
    --big) DO_BIG=1;;
    --big-only) DO_BIG=1; MODE=none;;
    --phase2)
      # rejected IMMEDIATELY at parse time: no combination may submit
      # other jobs first and only then hit this (review fix)
      echo "PHASE 2 IS HARD-DISABLED: PREREG §10 blocks it pending"
      echo "redesign (fixed-D budgets cannot estimate L(N,D); G6 gate)."
      exit 1;;
    *) echo "unknown arg $a"; exit 2;;
  esac
done

if [ "$MODE" = "sentinel" ] && [ "$DO_BIG" -eq 0 ]; then
  .venv/bin/python preflight_check.py --gate g3a || {
    echo "REFUSING SENTINEL: g3a preflight failed"; exit 1; }
elif [ "$MODE" = "smallmid" ]; then
  .venv/bin/python preflight_check.py --gate g3b || {
    echo "REFUSING EXPANSION: g3b preflight failed"; exit 1; }
fi
# ANY big path requires the full g3b science gate (incl. reviewed sentinel
# signoff) AND the big gate — both run BEFORE any sbatch, so a known-
# failing big gate cannot let smallmid jobs launch first (review fix)
if [ "$DO_BIG" -eq 1 ]; then
  .venv/bin/python preflight_check.py --gate g3b || {
    echo "REFUSING BIG: g3b preflight (sentinel signoff) failed"; exit 1; }
  .venv/bin/python preflight_check.py --gate big || {
    echo "REFUSING BIG: big-gate preflight failed"; exit 1; }
fi

if [ "$MODE" = "sentinel" ]; then
  echo "[G3a] sentinel: q25c-0.5b only — stop/go on instrument viability"
  P1 mit_normal_gpu gpu:l40s:1 "q25c-0.5b"
elif [ "$MODE" = "smallmid" ]; then
  P1 mit_normal_gpu gpu:l40s:1 "q25c-7b,q25c-3b"
  P1 mit_normal_gpu gpu:l40s:1 "q25c-0.5b,q25c-1.5b"
  P1 mit_normal_gpu gpu:l40s:1 "q3-0.6b,q3-1.7b,q3-4b,sc2-3b"
  P1 mit_normal_gpu gpu:l40s:1 "q35-0.8b,q35-2b,q35-4b"
fi

if [ "$DO_BIG" -eq 1 ]; then
  P1 mit_preemptable gpu:h200:1 "q25c-14b,q25c-32b,dsc2-lite,q35-2b-131k"
  P1 mit_preemptable gpu:h200:1 "q3-8b,q3-14b,q35-9b"
fi

squeue -u "$USER" -o "%.10i %.14j %.12P %.8T %.10M %R"
if [ "$SUBMIT_FAIL" -ne 0 ]; then
  echo "SUBMISSION INCOMPLETE — at least one sbatch failed"
  exit 1
fi
