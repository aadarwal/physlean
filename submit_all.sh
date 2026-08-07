#!/usr/bin/env bash
# Submit the whole campaign as parallel single-GPU jobs (fast to schedule).
# Safe to re-run: every cell/run is individually resumable and skipped when
# done; the runner defers models not yet in the HF cache.
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p logs

P1() { sbatch -p "$1" --gres="$2" --export=ALL,MODELS="$3" slurm/phase1.sbatch; }

# Phase 1 small/mid shards -> L40S on the normal (non-preemptable) partition
P1 mit_normal_gpu gpu:l40s:1 "q25c-7b,q25c-3b"
P1 mit_normal_gpu gpu:l40s:1 "q25c-0.5b,q25c-1.5b"
P1 mit_normal_gpu gpu:l40s:1 "q3-0.6b,q3-1.7b,q3-4b,sc2-3b"
P1 mit_normal_gpu gpu:l40s:1 "q35-0.8b,q35-2b,q35-4b"

# Phase 1 big rungs -> H200 141GB (preemptable pool has depth; requeue-safe)
P1 mit_preemptable gpu:h200:1 "q25c-14b,q25c-32b,dsc2-lite,q35-2b-131k"
P1 mit_preemptable gpu:h200:1 "q3-8b,q3-14b,q3-32b,q35-9b"

# Phase 2 -> one L40S per language (trainings are not preemption-safe)
for L in lean python cpp latex; do
  sbatch -p mit_normal_gpu --gres=gpu:l40s:1 --export=ALL,PLANG="$L" \
    slurm/phase2.sbatch
done

squeue -u "$USER" -o "%.10i %.14j %.12P %.8T %.10M %R"
