#!/bin/bash
#SBATCH -J physlean-v2b-lean-boundaries
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 16
#SBATCH --mem=128G
#SBATCH -t 12:00:00
#SBATCH --requeue
# CPU-only exact-three array.  This runs prospectively before rebuilding any
# Lean candidate table or drawing any V2-b sample:
#   sbatch -p mit_normal --array=0-2 slurm/v2b_lean_boundaries.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export ELAN_HOME="$V2B_POOL_BASE/elan"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export XDG_CONFIG_HOME="$V2B_POOL_BASE/xdg-config"
export XDG_DATA_HOME="$V2B_POOL_BASE/xdg-data"
export TMPDIR="$V2B_POOL_BASE/tmp"
export LEAN_NUM_THREADS=1
export LEAN_CC=/bin/cc
export CURL_CA_BUNDLE=/etc/pki/tls/cert.pem
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PATH="$ELAN_HOME/bin:/usr/local/bin:/usr/bin:/bin"
unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH LEAN_PATH LEAN_SRC_PATH
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" \
  "$XDG_DATA_HOME" logs results_v2/v2b/lean_boundaries

V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
V2B_ELAN="$ELAN_HOME/bin/elan"
V2B_DRIVER="$SLURM_SUBMIT_DIR/lean_drivers/V2BLeanBoundaryAudit.lean"
[[ -x "$V2B_PY" && -x "$V2B_ELAN" && -f "$V2B_DRIVER" ]] || {
  echo "missing locked Python, POOL Elan, or boundary driver" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing Lean boundary audit" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during Lean boundary audit" >&2
    exit 1
  }
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during Lean boundary audit" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array indices 0-2}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    V2B_EXTRACTION="results_v2/v2a/job19915851_0_mathlib4/extraction.json"
    ;;
  1)
    V2B_TAG="batteries"
    V2B_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    V2B_EXTRACTION="results_v2/v2a/job19915851_1_batteries/extraction.json"
    ;;
  2)
    V2B_TAG="physlib"
    V2B_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    V2B_EXTRACTION="results_v2/v2a/job19916781_2_physlib/extraction.json"
    ;;
  *)
    echo "invalid Lean boundary array task: $V2B_TASK" >&2
    exit 1
    ;;
esac
V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2
  exit 1
}
[[ -f "$V2B_EXTRACTION" ]] || {
  echo "missing frozen V2-a extraction: $V2B_EXTRACTION" >&2
  exit 1
}
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_ACTUAL_SHA" ]] || {
    echo "$V2B_TAG revision changed during Lean boundary audit" >&2
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

V2B_ARTIFACT_REPORT="$SLURM_SUBMIT_DIR/results_v2/v2a/lean_artifacts_job19911017.tsv"
V2B_ARTIFACT_REPORT_EXPECTED_SHA="ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6"
[[ -f "$V2B_ARTIFACT_REPORT" ]] || {
  echo "missing frozen Lean artifact-build report" >&2
  exit 1
}
[[ "$(sha256sum "$V2B_ARTIFACT_REPORT" | cut -d' ' -f1)" == \
  "$V2B_ARTIFACT_REPORT_EXPECTED_SHA" ]] || {
  echo "Lean artifact-build report hash drift" >&2
  exit 1
}
V2B_ARTIFACT_STATUS=$(awk -F '\t' \
  '$1 == "status" {value=$2} END {print value}' "$V2B_ARTIFACT_REPORT")
V2B_ARTIFACT_REPO_SHA=$(awk -F '\t' -v key="$V2B_TAG.repo_sha" \
  '$1 == key {print $2}' "$V2B_ARTIFACT_REPORT")
[[ "$V2B_ARTIFACT_STATUS" == "complete" \
  && "$V2B_ARTIFACT_REPO_SHA" == "$V2B_ACTUAL_SHA" ]] || {
  echo "$V2B_TAG lacks a complete matching artifact build" >&2
  exit 1
}

V2B_TOOLCHAIN=$(tr -d '\r\n' < "$V2B_REPO/lean-toolchain")
V2B_LEAN_BIN=$(ELAN_TOOLCHAIN="$V2B_TOOLCHAIN" \
  "$V2B_ELAN" which lean)
V2B_TOOLCHAIN_ROOT=$(dirname "$(dirname "$V2B_LEAN_BIN")")
export LIBRARY_PATH="$V2B_TOOLCHAIN_ROOT/lib"

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_COMPACT_ROOT="$SLURM_SUBMIT_DIR/results_v2/v2b/lean_boundaries"
V2B_POOL_RUN="$V2B_POOL_BASE/v2b-lean-boundaries/job${V2B_RUN_ID}_${V2B_TAG}"
V2B_SETUP_DIR="$V2B_POOL_RUN/setups"
V2B_MODULE_RUNS="$V2B_POOL_RUN/runs"
V2B_SETUP_INDEX="$V2B_COMPACT_ROOT/job${V2B_RUN_ID}_${V2B_TAG}_setups.json"
V2B_MANIFEST="$V2B_COMPACT_ROOT/job${V2B_RUN_ID}_${V2B_TAG}_manifest.json"
V2B_RESULT="$V2B_COMPACT_ROOT/job${V2B_RUN_ID}_${V2B_TAG}_result.json"
V2B_BOUNDARIES="$V2B_COMPACT_ROOT/job${V2B_RUN_ID}_${V2B_TAG}.json"
mkdir -p "$V2B_POOL_RUN"

if [[ ! -f "$V2B_SETUP_INDEX" ]]; then
  "$V2B_PY" prepare_v2b_lean_setups.py \
    --extraction "$V2B_EXTRACTION" \
    --corpus-root "$V2B_REPO" \
    --expected-corpus-sha "$V2B_EXPECTED_SHA" \
    --elan-home "$ELAN_HOME" \
    --setup-dir "$V2B_SETUP_DIR" \
    --batch-size 128 --timeout 3600 \
    --artifact-workers "${SLURM_CPUS_PER_TASK:-16}" \
    --out "$V2B_SETUP_INDEX"
fi

"$V2B_PY" v2b_lean_boundaries.py plan \
  --extraction "$V2B_EXTRACTION" \
  --setup-map "$V2B_SETUP_INDEX" \
  --out "$V2B_MANIFEST"

"$V2B_PY" run_v2b_lean_boundary_audit.py \
  --manifest "$V2B_MANIFEST" \
  --setup-index "$V2B_SETUP_INDEX" \
  --driver "$V2B_DRIVER" \
  --elan-home "$ELAN_HOME" \
  --run-dir "$V2B_MODULE_RUNS" \
  --workers "${SLURM_CPUS_PER_TASK:-16}" --timeout 7200 \
  --out "$V2B_RESULT"

"$V2B_PY" v2b_lean_boundaries.py finalize \
  --manifest "$V2B_MANIFEST" \
  --result "$V2B_RESULT" \
  --driver "$V2B_DRIVER" \
  --out "$V2B_BOUNDARIES"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-LEAN-BOUNDARIES-DONE $V2B_BOUNDARIES $(sha256sum "$V2B_BOUNDARIES" | cut -d' ' -f1)"
