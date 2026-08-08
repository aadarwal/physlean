#!/bin/bash
#SBATCH -J physlean-v2a-py
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 04:00:00
# CPU-only array: submit with
#   sbatch -p mit_normal --array=0-1 slurm/v2a_python.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2_POOL_BASE/xdg-cache"
export TMPDIR="$V2_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2a
V2_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2_PY" ]] || {
  echo "missing locked project Python: $V2_PY" >&2
  exit 1
}
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

V2_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-1}"
case "$V2_TASK" in
  0)
    V2_TAG="sympy"
    V2_PACKAGE="sympy"
    V2_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    ;;
  1)
    V2_TAG="astropy"
    V2_PACKAGE="astropy"
    V2_EXPECTED_SHA="440fe546589c4e496235d712bc29783ecf5a5fec"
    ;;
  *)
    echo "invalid Python V2-a array task: $V2_TASK" >&2
    exit 1
    ;;
esac
V2_REPO="$SLURM_SUBMIT_DIR/corpora/$V2_TAG"
V2_ACTUAL_SHA=$(git -C "$V2_REPO" rev-parse HEAD)
[[ "$V2_ACTUAL_SHA" == "$V2_EXPECTED_SHA" ]] || {
  echo "$V2_TAG revision drift: $V2_ACTUAL_SHA != $V2_EXPECTED_SHA" >&2
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

V2_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2_RUN_ID="${V2_ARRAY_ID}_${V2_TASK}"
V2_RUN_DIR="$SLURM_SUBMIT_DIR/results_v2/v2a/job${V2_RUN_ID}_${V2_TAG}"
mkdir "$V2_RUN_DIR"
V2_EXTRACT="$V2_RUN_DIR/extraction.json"
V2_VALIDATE="$V2_RUN_DIR/validation.json"
V2_COMPILE="$V2_RUN_DIR/boundary_compile_audit.json"

"$V2_PY" extract_python.py \
  --repo "$V2_REPO" --pkg "$V2_PACKAGE" --out "$V2_EXTRACT"
"$V2_PY" validate_v2a.py \
  --extraction "$V2_EXTRACT" --repo "$V2_TAG" --n 20 \
  --out "$V2_VALIDATE"
"$V2_PY" audit_python_compile.py \
  --extraction "$V2_EXTRACT" --validation "$V2_VALIDATE" \
  --repo-root "$V2_REPO" --python "$V2_PY" --timeout 120 \
  --out "$V2_COMPILE"

v2_assert_corpus_identity
v2_assert_source_identity
V2_REPORT_TMP=$(mktemp "$V2_RUN_DIR/.complete.XXXXXX")
{
  printf 'key\tvalue\n'
  printf 'status\tcomplete\n'
  printf 'repo\t%s\n' "$V2_TAG"
  printf 'repo_sha\t%s\n' "$V2_ACTUAL_SHA"
  printf 'source_commit\t%s\n' "$V2_SOURCE_COMMIT"
  printf 'python_sha256\t%s\n' "$(sha256sum "$V2_PY" | cut -d' ' -f1)"
  printf 'extraction_sha256\t%s\n' "$(sha256sum "$V2_EXTRACT" | cut -d' ' -f1)"
  printf 'validation_sha256\t%s\n' "$(sha256sum "$V2_VALIDATE" | cut -d' ' -f1)"
  printf 'compile_audit_sha256\t%s\n' "$(sha256sum "$V2_COMPILE" | cut -d' ' -f1)"
  printf 'utc_finished\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$V2_REPORT_TMP"
mv "$V2_REPORT_TMP" "$V2_RUN_DIR/complete.tsv"
echo "V2A-PYTHON-STRUCTURAL-DONE $V2_RUN_DIR/complete.tsv"
