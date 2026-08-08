#!/bin/bash
#SBATCH -J physlean-v2b-lean-keywords
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 1
#SBATCH --mem=4G
#SBATCH -t 00:20:00
# Submit after the exact-three token array, exporting its numeric job ID:
#   sbatch -p mit_normal --dependency=afterok:<id> \
#     --export=ALL,V2B_TOKEN_ARRAY_ID=<id> slurm/v2b_lean_keywords.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python" >&2
  exit 1
}
V2B_TOKEN_ARRAY_ID="${V2B_TOKEN_ARRAY_ID:?export token array job ID}"
[[ "$V2B_TOKEN_ARRAY_ID" =~ ^[0-9]+$ ]] || {
  echo "token array job ID is not numeric" >&2
  exit 1
}
V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing keyword freeze" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
mkdir -p logs results_v2/v2b/lean_keywords
V2B_OUT="results_v2/v2b/lean_keywords/freeze_tokens_job${V2B_TOKEN_ARRAY_ID}.json"

"$V2B_PY" finalize_v2b_lean_keywords.py \
  --table "results_v2/v2b/lean_tokens/job${V2B_TOKEN_ARRAY_ID}_0_mathlib4.json" \
  --table "results_v2/v2b/lean_tokens/job${V2B_TOKEN_ARRAY_ID}_1_batteries.json" \
  --table "results_v2/v2b/lean_tokens/job${V2B_TOKEN_ARRAY_ID}_2_physlib.json" \
  --out "$V2B_OUT"

V2B_FINAL_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" \
   && -z "$V2B_FINAL_DIRT" ]] || {
  echo "source drifted during keyword freeze" >&2
  exit 1
}
echo "V2B-LEAN-KEYWORDS-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
