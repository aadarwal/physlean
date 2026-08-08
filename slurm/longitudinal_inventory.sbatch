#!/bin/bash
#SBATCH -J physlean-longitudinal-inventory
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:00:00
# Model-free, non-evidentiary feasibility inventory for DESIGN_V2 §11.
# It reads only committed Git objects; it never loads a model or score.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2L_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2L_POOL_BASE/xdg-cache"
export TMPDIR="$V2L_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/longitudinal
V2L_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2L_PY" ]] || {
  echo "missing locked project Python: $V2L_PY" >&2
  exit 1
}

V2L_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2L_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing inventory" >&2
  printf '%s\n' "$V2L_SOURCE_DIRT" >&2
  exit 1
}
V2L_SOURCE_COMMIT=$(git rev-parse HEAD)
v2l_assert_source_identity() {
  V2L_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2L_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2L_CURRENT_COMMIT" == "$V2L_SOURCE_COMMIT" ]] || {
    echo "source commit changed during longitudinal inventory" >&2
    exit 1
  }
  [[ -z "$V2L_CURRENT_DIRT" ]] || {
    echo "source tree changed during longitudinal inventory" >&2
    exit 1
  }
}
v2l_assert_source_identity

V2L_PLAN="longitudinal/mathlib4_inventory_plan.json"
V2L_REPO="$SLURM_SUBMIT_DIR/corpora/mathlib4"
V2L_EXPECTED_HEAD="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
git ls-files --error-unmatch "$V2L_PLAN" >/dev/null || {
  echo "inventory plan is not tracked: $V2L_PLAN" >&2
  exit 1
}
git diff --quiet HEAD -- "$V2L_PLAN" || {
  echo "inventory plan differs from HEAD: $V2L_PLAN" >&2
  exit 1
}
[[ -d "$V2L_REPO/.git" ]] || {
  echo "missing mathlib4 full-history checkout: $V2L_REPO" >&2
  exit 1
}
V2L_ACTUAL_HEAD=$(git -C "$V2L_REPO" rev-parse HEAD)
[[ "$V2L_ACTUAL_HEAD" == "$V2L_EXPECTED_HEAD" ]] || {
  echo "mathlib4 revision drift: $V2L_ACTUAL_HEAD != $V2L_EXPECTED_HEAD" >&2
  exit 1
}
V2L_TRACKED_DIRT=$(git -C "$V2L_REPO" status --porcelain \
  --untracked-files=no)
[[ -z "$V2L_TRACKED_DIRT" ]] || {
  echo "mathlib4 tracked files are dirty" >&2
  printf '%s\n' "$V2L_TRACKED_DIRT" >&2
  exit 1
}

V2L_RUN_ID="${SLURM_JOB_ID:-manual}"
V2L_OUT="results_v2/longitudinal/job${V2L_RUN_ID}_mathlib4_inventory.json"
"$V2L_PY" prepare_longitudinal_inventory.py \
  --plan "$V2L_PLAN" \
  --repo-root "$V2L_REPO" \
  --out "$V2L_OUT"

v2l_assert_source_identity
[[ "$(git -C "$V2L_REPO" rev-parse HEAD)" == "$V2L_ACTUAL_HEAD" ]] || {
  echo "mathlib4 revision changed during longitudinal inventory" >&2
  exit 1
}
echo "LONGITUDINAL-INVENTORY-DONE $V2L_OUT $(sha256sum "$V2L_OUT" | cut -d' ' -f1)"
