#!/usr/bin/env bash
#SBATCH -J cs2-rungs
#SBATCH -p mit_normal_gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 06:00:00
#SBATCH -o logs/cs2_%A_%a.log
# ARM_CS CS-2 rung runner: one array task = one training run.
# TASKS file (arg 1): lines "lang max_bytes ctx seed lr epochs tag"
set -uo pipefail
# Slurm executes a SPOOLED COPY of this script, so dirname "$0" points at
# the spool dir; the submit directory is the repo root
cd "${SLURM_SUBMIT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TASKS="${1:?tasks file}"
LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$TASKS")
[ -n "$LINE" ] || { echo "no task line $SLURM_ARRAY_TASK_ID"; exit 0; }
read -r LANG_ MB CTX SEED LR EP TAG <<< "$LINE"
echo "[cs2] task=$SLURM_ARRAY_TASK_ID $LINE"
.venv/bin/python train_scratch.py --lang "$LANG_" --size "${CS2_SIZE:-10m}" \
  --pool-dir data/cs2 --out-dir results_cs/runs --dump-dir results_cs/nll \
  --max-train-bytes "$MB" --ctx "$CTX" --seed "$SEED" --lr "$LR" \
  --epochs "$EP" --out-tag="$TAG" --device cuda \
  --val-manifest "data/cs2/${LANG_}_cs2.json"
