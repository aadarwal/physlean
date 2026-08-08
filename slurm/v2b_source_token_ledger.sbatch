#!/bin/bash
#SBATCH -J physlean-v2b-source-tokens
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=24G
#SBATCH -t 02:00:00
# CPU-only, source-only exact-five array. Run after assembly manifests are
# committed and BEFORE paired NLL scoring:
#   sbatch -p mit_normal --array=0-4 \
#     --export=ALL,V2B_ASSEMBLY_JOB=<committed-array-id> \
#     slurm/v2b_source_token_ledger.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/source_tokens
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing source ledger" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during source-token ledger" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during source-token ledger" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_ASSEMBLY_JOB="${V2B_ASSEMBLY_JOB:?export the committed assembly array job id}"
[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_ASSEMBLY_JOB must be a numeric Slurm job id" >&2
  exit 1
}
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
    echo "invalid source-token array task: $V2B_TASK" >&2
    exit 1
    ;;
esac

V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
[[ -f "$V2B_MANIFEST" ]] || {
  echo "missing committed assembly manifest: $V2B_MANIFEST" >&2
  exit 1
}
git ls-files --error-unmatch "$V2B_MANIFEST" >/dev/null || {
  echo "assembly manifest is not tracked: $V2B_MANIFEST" >&2
  exit 1
}
git diff --quiet HEAD -- "$V2B_MANIFEST" || {
  echo "assembly manifest differs from HEAD: $V2B_MANIFEST" >&2
  exit 1
}

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift: $V2B_ACTUAL_SHA != $V2B_EXPECTED_SHA" >&2
  exit 1
}
V2B_CORPUS_DIRT=$(git -C "$V2B_REPO" status --porcelain \
  --untracked-files=all)
[[ -z "$V2B_CORPUS_DIRT" ]] || {
  echo "$V2B_TAG source tree is dirty" >&2
  printf '%s\n' "$V2B_CORPUS_DIRT" >&2
  exit 1
}

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_OUT="results_v2/v2b/source_tokens/job${V2B_RUN_ID}_${V2B_TAG}.json"
"$V2B_PY" prepare_v2b_source_token_ledger.py \
  --manifest "$V2B_MANIFEST" \
  --out "$V2B_OUT"

v2b_assert_source_identity
[[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_ACTUAL_SHA" ]] || {
  echo "$V2B_TAG revision changed during source-token ledger" >&2
  exit 1
}
echo "V2B-SOURCE-TOKENS-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
