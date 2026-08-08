#!/bin/bash
#SBATCH -J physlean-v2a
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 16
#SBATCH --mem=128G
#SBATCH -t 08:00:00
# CPU-only array: submit with
#   sbatch -p mit_normal --array=0-2 slurm/v2a_extract.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
V2_ARTIFACT_REPORT="$V2_POOL_BASE/physlean/results_v2/v2a/lean_artifacts_job19911017.tsv"
V2_ARTIFACT_REPORT_EXPECTED_SHA="ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6"
export ELAN_HOME="$V2_POOL_BASE/elan"
export XDG_CACHE_HOME="$V2_POOL_BASE/xdg-cache"
export TMPDIR="$V2_POOL_BASE/tmp"
export LEAN_NUM_THREADS="${SLURM_CPUS_PER_TASK:-16}"
export LEAN_CC="/bin/cc"
export CURL_CA_BUNDLE="/etc/pki/tls/cert.pem"
export PATH="$ELAN_HOME/bin:$PATH"
V2_BASE_LIBRARY_PATH="${LIBRARY_PATH-}"
mkdir -p "$TMPDIR" logs results_v2/v2a

V2_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-a evidence" >&2
  printf '%s\n' "$V2_SOURCE_DIRT" >&2
  exit 1
}
V2_SOURCE_COMMIT=$(git rev-parse HEAD)
v2_assert_source_identity() {
  V2_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2_CURRENT_COMMIT" == "$V2_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-a job: $V2_SOURCE_COMMIT -> $V2_CURRENT_COMMIT" >&2
    exit 1
  }
  [[ -z "$V2_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-a job" >&2
    printf '%s\n' "$V2_CURRENT_DIRT" >&2
    exit 1
  }
}
v2_assert_source_identity

V2_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2_PY" ]] || {
  echo "missing locked project Python: $V2_PY" >&2
  exit 1
}
V2_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-2}"
case "$V2_TASK" in
  0)
    V2_TAG="mathlib4"
    V2_REPO="$SLURM_SUBMIT_DIR/corpora/mathlib4"
    V2_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    ;;
  1)
    V2_TAG="batteries"
    V2_REPO="$SLURM_SUBMIT_DIR/corpora/batteries"
    V2_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    ;;
  2)
    V2_TAG="physlib"
    V2_REPO="$SLURM_SUBMIT_DIR/corpora/physlib"
    V2_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    ;;
  *)
    echo "invalid V2-a array task: $V2_TASK" >&2
    exit 1
    ;;
esac

V2_ACTUAL_SHA=$(git -C "$V2_REPO" rev-parse HEAD)
[[ "$V2_ACTUAL_SHA" == "$V2_EXPECTED_SHA" ]] || {
  echo "$V2_TAG revision drift: $V2_ACTUAL_SHA != $V2_EXPECTED_SHA" >&2
  exit 1
}
[[ -f "$V2_ARTIFACT_REPORT" ]] || {
  echo "missing frozen Lean artifact-build report: $V2_ARTIFACT_REPORT" >&2
  exit 1
}
V2_ARTIFACT_REPORT_SHA=$(sha256sum "$V2_ARTIFACT_REPORT" | cut -d' ' -f1)
[[ "$V2_ARTIFACT_REPORT_SHA" == "$V2_ARTIFACT_REPORT_EXPECTED_SHA" ]] || {
  echo "Lean artifact-build report hash drift" >&2
  exit 1
}
V2_ARTIFACT_STATUS=$(awk -F '\t' \
  '$1 == "status" {value=$2} END {print value}' \
  "$V2_ARTIFACT_REPORT")
V2_ARTIFACT_REPO_SHA=$(awk -F '\t' -v key="$V2_TAG.repo_sha" \
  '$1 == key {print $2}' "$V2_ARTIFACT_REPORT")
[[ "$V2_ARTIFACT_STATUS" == "complete" \
  && "$V2_ARTIFACT_REPO_SHA" == "$V2_ACTUAL_SHA" ]] || {
  echo "$V2_TAG is not bound to a complete matching artifact build" >&2
  exit 1
}
v2_assert_corpus_identity() {
  V2_CURRENT_REPO_SHA=$(git -C "$V2_REPO" rev-parse HEAD)
  V2_CORPUS_DIRT=$(git -C "$V2_REPO" status --porcelain \
    --untracked-files=all)
  [[ "$V2_CURRENT_REPO_SHA" == "$V2_ACTUAL_SHA" ]] || {
    echo "$V2_TAG revision changed during V2-a job" >&2
    exit 1
  }
  [[ -z "$V2_CORPUS_DIRT" ]] || {
    echo "$V2_TAG source tree is dirty" >&2
    printf '%s\n' "$V2_CORPUS_DIRT" >&2
    exit 1
  }
}
v2_assert_corpus_identity
V2_TOOLCHAIN=$(tr -d '\r\n' < "$V2_REPO/lean-toolchain")
V2_LEAN_BIN=$(ELAN_TOOLCHAIN="$V2_TOOLCHAIN" \
  "$ELAN_HOME/bin/elan" which lean)
V2_TOOLCHAIN_ROOT=$(dirname "$(dirname "$V2_LEAN_BIN")")
export LIBRARY_PATH="$V2_TOOLCHAIN_ROOT/lib${V2_BASE_LIBRARY_PATH:+:$V2_BASE_LIBRARY_PATH}"

V2_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2_RUN_ID="${V2_ARRAY_ID}_${V2_TASK}"
V2_RUN_DIR="$SLURM_SUBMIT_DIR/results_v2/v2a/job${V2_RUN_ID}_${V2_TAG}"
mkdir "$V2_RUN_DIR"
V2_PAIRS="$V2_RUN_DIR/pairs.json"
V2_EXTRACT="$V2_RUN_DIR/extraction.json"
V2_VALIDATE="$V2_RUN_DIR/validation.json"
V2_CLOSURE="$V2_RUN_DIR/raw_closure_audit.json"
V2_COMPILE="$V2_RUN_DIR/boundary_compile_audit.json"

"$V2_PY" pair_ilean.py \
  --repo-root "$V2_REPO" \
  --artifact-root "$V2_REPO/.lake/build/lib/lean" \
  --expected-repo-sha "$V2_EXPECTED_SHA" \
  --out "$V2_PAIRS"
"$V2_PY" extract_lean.py \
  --pairs "$V2_PAIRS" --repo "$V2_TAG" --out "$V2_EXTRACT"
"$V2_PY" validate_v2a.py \
  --extraction "$V2_EXTRACT" --repo "$V2_TAG" --n 20 \
  --out "$V2_VALIDATE"
"$V2_PY" audit_lean_closure.py \
  --extraction "$V2_EXTRACT" --validation "$V2_VALIDATE" \
  --pairs "$V2_PAIRS" --out "$V2_CLOSURE"
"$V2_PY" audit_lean_compile.py \
  --extraction "$V2_EXTRACT" --validation "$V2_VALIDATE" \
  --repo-root "$V2_REPO" --timeout 300 --out "$V2_COMPILE"

# §14.20: PhysLib additionally requires the exact mathlib revision pinned by
# PhysLib's own lake-manifest, not the independent mathlib4 corpus HEAD.
if [[ "$V2_TAG" == "physlib" ]]; then
  V2_PIN_REPO="$V2_REPO/.lake/packages/mathlib"
  V2_PIN_EXPECTED=$("$V2_PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(next(p["rev"] for p in d["packages"] if p["name"] == "mathlib"))' \
    "$V2_REPO/lake-manifest.json")
  V2_PIN_ACTUAL=$(git -C "$V2_PIN_REPO" rev-parse HEAD)
  [[ "$V2_PIN_ACTUAL" == "$V2_PIN_EXPECTED" ]] || {
    echo "PhysLib-pinned mathlib drift: $V2_PIN_ACTUAL != $V2_PIN_EXPECTED" >&2
    exit 1
  }
  V2_PIN_DIRT=$(git -C "$V2_PIN_REPO" status --porcelain \
    --untracked-files=all)
  [[ -z "$V2_PIN_DIRT" ]] || {
    echo "PhysLib-pinned mathlib source tree is dirty" >&2
    printf '%s\n' "$V2_PIN_DIRT" >&2
    exit 1
  }
  V2_PIN_PAIRS="$V2_RUN_DIR/pinned_mathlib_pairs.json"
  V2_PIN_EXTRACT="$V2_RUN_DIR/pinned_mathlib_extraction.json"
  "$V2_PY" pair_ilean.py \
    --repo-root "$V2_PIN_REPO" \
    --artifact-root "$V2_PIN_REPO/.lake/build/lib/lean" \
    --expected-repo-sha "$V2_PIN_EXPECTED" \
    --out "$V2_PIN_PAIRS"
  "$V2_PY" extract_lean.py \
    --pairs "$V2_PIN_PAIRS" --repo physlib_pinned_mathlib \
    --out "$V2_PIN_EXTRACT"
fi

v2_assert_corpus_identity
v2_assert_source_identity
V2_REPORT_TMP=$(mktemp "$V2_RUN_DIR/.complete.XXXXXX")
{
  printf 'key\tvalue\n'
  printf 'status\tcomplete\n'
  printf 'repo\t%s\n' "$V2_TAG"
  printf 'repo_sha\t%s\n' "$V2_ACTUAL_SHA"
  printf 'toolchain\t%s\n' "$V2_TOOLCHAIN"
  printf 'source_commit\t%s\n' "$V2_SOURCE_COMMIT"
  printf 'artifact_build_report\t%s\n' "$V2_ARTIFACT_REPORT"
  printf 'artifact_build_report_sha256\t%s\n' "$V2_ARTIFACT_REPORT_SHA"
  printf 'pairs_sha256\t%s\n' "$(sha256sum "$V2_PAIRS" | cut -d' ' -f1)"
  printf 'extraction_sha256\t%s\n' "$(sha256sum "$V2_EXTRACT" | cut -d' ' -f1)"
  printf 'validation_sha256\t%s\n' "$(sha256sum "$V2_VALIDATE" | cut -d' ' -f1)"
  printf 'closure_audit_sha256\t%s\n' "$(sha256sum "$V2_CLOSURE" | cut -d' ' -f1)"
  printf 'compile_audit_sha256\t%s\n' "$(sha256sum "$V2_COMPILE" | cut -d' ' -f1)"
  printf 'utc_finished\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$V2_REPORT_TMP"
mv "$V2_REPORT_TMP" "$V2_RUN_DIR/complete.tsv"
echo "V2A-STRUCTURAL-DONE $V2_RUN_DIR/complete.tsv"
