#!/bin/bash
#SBATCH -J physlean-v2b-k4x
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 04:00:00
# CPU-only resolution of PhysLib's preserved external references against
# the already-built, already-extracted manifest-pinned mathlib snapshot:
#   sbatch -p mit_normal slurm/v2b_k4x.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/k4x
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b k4x" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b k4x" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b k4x" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_PHYSLIB_ROOT="$SLURM_SUBMIT_DIR/corpora/physlib"
V2B_PHYSLIB_REV="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
V2B_EXTERNAL_ROOT="$V2B_PHYSLIB_ROOT/.lake/packages/mathlib"
V2B_EXTERNAL_REV="81a5d257c8e410db227a6665ed08f64fea08e997"
V2B_PHYSLIB_EXTRACTION="results_v2/v2a/job19916781_2_physlib/extraction.json"
V2B_EXTERNAL_EXTRACTION="results_v2/v2a/job19916781_2_physlib/pinned_mathlib_extraction.json"
V2B_EXTERNAL_EXTRACTION_SHA="9f4a192059ede347093c4f424940198e45cc93b9140f0ef8e5b8a465e0b6f796"

[[ "$(git -C "$V2B_PHYSLIB_ROOT" rev-parse HEAD)" == "$V2B_PHYSLIB_REV" ]] || {
  echo "PhysLib revision drift" >&2
  exit 1
}
[[ "$(git -C "$V2B_EXTERNAL_ROOT" rev-parse HEAD)" == "$V2B_EXTERNAL_REV" ]] || {
  echo "PhysLib-pinned mathlib revision drift" >&2
  exit 1
}
for V2B_REPO in "$V2B_PHYSLIB_ROOT" "$V2B_EXTERNAL_ROOT"; do
  V2B_DIRT=$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)
  [[ -z "$V2B_DIRT" ]] || {
    echo "k4x source checkout is dirty: $V2B_REPO" >&2
    printf '%s\n' "$V2B_DIRT" >&2
    exit 1
  }
done
[[ -f "$V2B_PHYSLIB_EXTRACTION" && -f "$V2B_EXTERNAL_EXTRACTION" ]] || {
  echo "missing V2-a PhysLib/pinned-mathlib extraction" >&2
  exit 1
}
[[ "$(sha256sum "$V2B_EXTERNAL_EXTRACTION" | cut -d' ' -f1)" == \
   "$V2B_EXTERNAL_EXTRACTION_SHA" ]] || {
  echo "pinned-mathlib extraction hash drift" >&2
  exit 1
}

V2B_RUN_ID="${SLURM_JOB_ID:-manual}"
V2B_OUT="results_v2/v2b/k4x/job${V2B_RUN_ID}_physlib.json"
"$V2B_PY" prepare_v2b_k4x_graph.py \
  --physlib-root "$V2B_PHYSLIB_ROOT" \
  --physlib-extraction "$V2B_PHYSLIB_EXTRACTION" \
  --external-extraction "$V2B_EXTERNAL_EXTRACTION" \
  --out "$V2B_OUT"

v2b_assert_source_identity
echo "V2B-K4X-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
