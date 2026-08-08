#!/bin/bash
#SBATCH -J physlean-v2b-k7
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 02:00:00
# CPU-only exact-five array:
#   sbatch -p mit_normal --array=0-4 slurm/v2b_k7.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/k7
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b k7" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b k7 job" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b k7 job" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    ;;
  1)
    V2B_TAG="batteries"
    V2B_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    ;;
  2)
    V2B_TAG="physlib"
    V2B_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    ;;
  3)
    V2B_TAG="sympy"
    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    ;;
  4)
    V2B_TAG="astropy"
    V2B_EXPECTED_SHA="440fe546589c4e496235d712bc29783ecf5a5fec"
    ;;
  *)
    echo "invalid V2-b k7 array task: $V2B_TASK" >&2
    exit 1
    ;;
esac
V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift: $V2B_ACTUAL_SHA != $V2B_EXPECTED_SHA" >&2
  exit 1
}
v2b_assert_corpus_identity() {
  V2B_CURRENT_REPO_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
  V2B_CORPUS_DIRT=$(git -C "$V2B_REPO" status --porcelain \
    --untracked-files=all)
  [[ "$V2B_CURRENT_REPO_SHA" == "$V2B_ACTUAL_SHA" ]] || {
    echo "$V2B_TAG revision changed during V2-b k7 job" >&2
    exit 1
  }
  [[ -z "$V2B_CORPUS_DIRT" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2
    printf '%s\n' "$V2B_CORPUS_DIRT" >&2
    exit 1
  }
}
v2b_assert_corpus_identity

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_OUT="results_v2/v2b/k7/job${V2B_RUN_ID}_${V2B_TAG}.json"
"$V2B_PY" v2b_k7.py \
  --corpus-root "$V2B_REPO" \
  --repo "$V2B_TAG" \
  --expected-corpus-sha "$V2B_EXPECTED_SHA" \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-K7-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
