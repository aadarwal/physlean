#!/bin/bash
#SBATCH -J physlean-p2
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=48G
#SBATCH -t 12:00:00
# Submit with:  sbatch -p mit_normal_gpu --gres=gpu:l40s:1 \
#   --export=ALL,PLANG=<lean|python|cpp|latex> slurm/phase2.sbatch
# (PLANG, not LANG: LANG is the locale.) Finished runs are skipped by their
# results json -> resubmit is safe.
cd "$SLURM_SUBMIT_DIR"
export HF_HOME=/orcd/pool/008/$USER/hf
LANG_ARG=${PLANG:-lean}
nvidia-smi -L || true
for ss in 10m:1 10m:2 10m:3 30m:1 30m:2 30m:3 100m:1 100m:2 300m:1; do
  size=${ss%%:*}; seed=${ss##*:}
  out="results_v2/scratch/scratch-${size}-${LANG_ARG}-s${seed}.json"
  [ -f "$out" ] && { echo "[skip] $out"; continue; }
  .venv/bin/python train_scratch.py --lang "$LANG_ARG" --size "$size" \
    --seed "$seed"
done
echo "P2-DONE $LANG_ARG"
