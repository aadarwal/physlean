#!/bin/bash
#SBATCH -J physlean-v2b-lean-tokens
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 02:00:00
# CPU-only exact-three array: reserved token tables plus contextual parser
# dispatch keys imported by the three pinned Lean corpora, before A6.
#   sbatch -p mit_normal --array=0-2 slurm/v2b_lean_tokens.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export ELAN_HOME="$V2B_POOL_BASE/elan"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
export PATH="$ELAN_HOME/bin:$PATH"
mkdir -p "$TMPDIR" logs results_v2/v2b/lean_tokens
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
V2B_LAKE="$ELAN_HOME/bin/lake"
V2B_ARTIFACT_REPORT="$SLURM_SUBMIT_DIR/results_v2/v2a/lean_artifacts_job19911017.tsv"
V2B_ARTIFACT_REPORT_EXPECTED_SHA="ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6"
[[ -x "$V2B_PY" && -x "$V2B_LAKE" ]] || {
  echo "missing locked Python or Lake" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing token dump" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during parser-token job" >&2
    exit 1
  }
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during parser-token job" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array indices 0-2}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_MODULE="Mathlib"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    V2B_DUMP="$SLURM_SUBMIT_DIR/lean_tokens/MathlibTokens.lean"
    ;;
  1)
    V2B_TAG="batteries"
    V2B_MODULE="Batteries"
    V2B_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    V2B_DUMP="$SLURM_SUBMIT_DIR/lean_tokens/BatteriesTokens.lean"
    ;;
  2)
    V2B_TAG="physlib"
    V2B_MODULE="Physlib"
    V2B_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    V2B_DUMP="$SLURM_SUBMIT_DIR/lean_tokens/PhyslibTokens.lean"
    ;;
  *)
    echo "invalid parser-token array task: $V2B_TASK" >&2
    exit 1
    ;;
esac
V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2
  exit 1
}
[[ -f "$V2B_ARTIFACT_REPORT" ]] || {
  echo "missing frozen Lean artifact-build report" >&2
  exit 1
}
V2B_ARTIFACT_REPORT_SHA=$(sha256sum "$V2B_ARTIFACT_REPORT" | cut -d' ' -f1)
[[ "$V2B_ARTIFACT_REPORT_SHA" == "$V2B_ARTIFACT_REPORT_EXPECTED_SHA" ]] || {
  echo "Lean artifact-build report hash drift" >&2
  exit 1
}
V2B_ARTIFACT_STATUS=$(awk -F '\t' \
  '$1 == "status" {value=$2} END {print value}' "$V2B_ARTIFACT_REPORT")
V2B_ARTIFACT_REPO_SHA=$(awk -F '\t' -v key="$V2B_TAG.repo_sha" \
  '$1 == key {print $2}' "$V2B_ARTIFACT_REPORT")
[[ "$V2B_ARTIFACT_STATUS" == "complete" \
  && "$V2B_ARTIFACT_REPO_SHA" == "$V2B_ACTUAL_SHA" ]] || {
  echo "$V2B_TAG is not bound to a complete matching artifact build" >&2
  exit 1
}
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_ACTUAL_SHA" ]] || {
    echo "$V2B_TAG revision changed during parser-token job" >&2
    exit 1
  }
  V2B_CORPUS_DIRT=$(git -C "$V2B_REPO" status --porcelain \
    --untracked-files=all)
  [[ -z "$V2B_CORPUS_DIRT" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2
    printf '%s\n' "$V2B_CORPUS_DIRT" >&2
    exit 1
  }
}
v2b_assert_corpus_identity

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_RESERVED_RAW="$SLURM_SUBMIT_DIR/results_v2/v2b/lean_tokens/job${V2B_RUN_ID}_${V2B_TAG}_reserved.txt"
V2B_DISPATCH_RAW="$SLURM_SUBMIT_DIR/results_v2/v2b/lean_tokens/job${V2B_RUN_ID}_${V2B_TAG}_dispatch.txt"
V2B_EXCLUDED_RAW="$SLURM_SUBMIT_DIR/results_v2/v2b/lean_tokens/job${V2B_RUN_ID}_${V2B_TAG}_excluded.txt"
V2B_OUT="$SLURM_SUBMIT_DIR/results_v2/v2b/lean_tokens/job${V2B_RUN_ID}_${V2B_TAG}.json"
[[ ! -e "$V2B_RESERVED_RAW" && ! -e "$V2B_DISPATCH_RAW" \
   && ! -e "$V2B_EXCLUDED_RAW" && ! -e "$V2B_OUT" ]] || {
  echo "refusing to overwrite parser-token evidence for $V2B_RUN_ID" >&2
  exit 1
}
V2B_LEAN_VERSION=$(cd "$V2B_REPO" && "$V2B_LAKE" env lean --version)
V2B_LAKE_VERSION=$(cd "$V2B_REPO" && "$V2B_LAKE" --version)
(cd "$V2B_REPO" \
  && V2B_RESERVED_OUT="$V2B_RESERVED_RAW" \
  V2B_DISPATCH_OUT="$V2B_DISPATCH_RAW" \
  V2B_EXCLUDED_OUT="$V2B_EXCLUDED_RAW" \
  "$V2B_LAKE" env lean "$V2B_DUMP")

"$V2B_PY" prepare_v2b_lean_tokens.py \
  --reserved-raw "$V2B_RESERVED_RAW" \
  --dispatch-raw "$V2B_DISPATCH_RAW" \
  --excluded-dispatch-raw "$V2B_EXCLUDED_RAW" \
  --corpus-root "$V2B_REPO" \
  --repo "$V2B_TAG" \
  --expected-corpus-sha "$V2B_EXPECTED_SHA" \
  --umbrella-module "$V2B_MODULE" \
  --artifact-report "$V2B_ARTIFACT_REPORT" \
  --lean-version "$V2B_LEAN_VERSION" \
  --lake-version "$V2B_LAKE_VERSION" \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-LEAN-TOKENS-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
