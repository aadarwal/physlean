#!/bin/bash
#SBATCH -J physlean-p1
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH -t 08:00:00
#SBATCH --requeue
# Submit with:  sbatch -p <part> --gres=gpu:<type>:1 \
#   --export=ALL,MODELS=<short,short,...> slurm/phase1.sbatch
# Cells are skipped once their .meta.json exists -> requeue/resubmit is safe.
cd "$SLURM_SUBMIT_DIR"
export HF_HOME=/orcd/pool/008/$USER/hf
nvidia-smi -L || true
.venv/bin/python run_phase1.py ${MODELS:+--models "$MODELS"}
echo "SHARD-DONE ${MODELS:-all}"
