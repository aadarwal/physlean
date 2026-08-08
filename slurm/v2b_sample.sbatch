#!/bin/bash
#SBATCH -J physlean-v2b-sample
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 04:00:00
# Deterministic five-corpus pilot draw after the write-once A6 outcome:
#   sbatch -p mit_normal \
#     --export=ALL,V2B_A6_OUTCOME=<committed-outcome.json> \
#     slurm/v2b_sample.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/sample
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b sample draw" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b sample draw" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b sample draw" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_A6_OUTCOME="${V2B_A6_OUTCOME:?export the committed A6 outcome path}"
V2B_CANDIDATES=(
  results_v2/v2b/candidates/job19931908_0_mathlib4.json
  results_v2/v2b/candidates/job19931908_1_batteries.json
  results_v2/v2b/candidates/job19931908_2_physlib.json
  results_v2/v2b/candidates/job19931908_3_sympy.json
  results_v2/v2b/candidates/job19931908_4_astropy.json
)
[[ -f "$V2B_A6_OUTCOME" ]] || {
  echo "missing committed A6 outcome: $V2B_A6_OUTCOME" >&2
  exit 1
}
for V2B_CANDIDATE in "${V2B_CANDIDATES[@]}"; do
  [[ -f "$V2B_CANDIDATE" ]] || {
    echo "missing sealed candidate table: $V2B_CANDIDATE" >&2
    exit 1
  }
done

V2B_CANDIDATE_ARGS=()
for V2B_CANDIDATE in "${V2B_CANDIDATES[@]}"; do
  V2B_CANDIDATE_ARGS+=(--candidates "$V2B_CANDIDATE")
done
V2B_RUN_ID="${SLURM_JOB_ID:-manual}"
V2B_OUT="results_v2/v2b/sample/job${V2B_RUN_ID}_sample.json"
"$V2B_PY" finalize_v2b_sample.py \
  "${V2B_CANDIDATE_ARGS[@]}" \
  --a6-outcome "$V2B_A6_OUTCOME" \
  --out "$V2B_OUT"

v2b_assert_source_identity
echo "V2B-SAMPLE-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
