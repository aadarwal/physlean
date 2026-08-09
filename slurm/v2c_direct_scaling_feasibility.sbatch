#!/bin/bash
#SBATCH -J physlean-v2c-p1a
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 12:00:00
#SBATCH --requeue
# Model-free P1a structural census. No accelerator, tokenizer, or model load.
set -euo pipefail
V2C_PROJECT_ROOT=$(realpath "$SLURM_SUBMIT_DIR")
cd "$V2C_PROJECT_ROOT"

: "${V2C_PROTOCOL:?set V2C_PROTOCOL to the committed frozen P0 protocol}"
: "${V2C_PROTOCOL_SHA256:?set V2C_PROTOCOL_SHA256 to the frozen raw P0 SHA256}"
V2C_CORPORA_ROOT="${V2C_CORPORA_ROOT:-$V2C_PROJECT_ROOT/corpora}"
V2C_LOCK="$V2C_PROJECT_ROOT/corpora_lock.json"
V2C_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export HOME="$V2C_POOL_BASE/home"
export XDG_CACHE_HOME="$V2C_POOL_BASE/xdg-cache"
export TMPDIR="$V2C_POOL_BASE/tmp"
export ELAN_HOME="$V2C_POOL_BASE/elan"
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$TMPDIR" logs results_v2/v2c/p1a

V2C_PY="$V2C_PROJECT_ROOT/.venv/bin/python"
[[ -x "$V2C_PY" ]] || {
  echo "missing locked project Python: $V2C_PY" >&2
  exit 1
}
[[ -f "$V2C_PROTOCOL" ]] || {
  echo "missing frozen P0 protocol: $V2C_PROTOCOL" >&2
  exit 1
}
V2C_PROTOCOL=$(realpath "$V2C_PROTOCOL")
case "$V2C_PROTOCOL" in
  "$V2C_PROJECT_ROOT"/*) ;;
  *)
    echo "frozen P0 protocol must live inside the submitted checkout" >&2
    exit 1
    ;;
esac
V2C_PROTOCOL_REL="${V2C_PROTOCOL#"$V2C_PROJECT_ROOT"/}"
[[ "$(sha256sum "$V2C_PROTOCOL" | cut -d' ' -f1)" == \
    "$V2C_PROTOCOL_SHA256" ]] || {
  echo "frozen P0 protocol SHA256 mismatch" >&2
  exit 1
}
git ls-files --error-unmatch "$V2C_PROTOCOL_REL" "corpora_lock.json" \
  >/dev/null || {
  echo "protocol and corpus lock must both be tracked" >&2
  exit 1
}
git diff --quiet HEAD -- "$V2C_PROTOCOL_REL" "corpora_lock.json" || {
  echo "protocol or corpus lock differs from HEAD" >&2
  exit 1
}
# `.venv` and `corpora` are read-only POOL links in the dedicated cluster
# worktree. They are runtime inputs, not measurement source; their exact
# corpus identities are validated by the producer against `corpora_lock.json`.
V2C_SOURCE_DIRTY=$(git status --porcelain -- . \
  ':(exclude)results_v2' ':(exclude).venv' ':(exclude)corpora')
[[ -z "$V2C_SOURCE_DIRTY" ]] || {
  echo "source tree dirty outside results_v2; refusing P1a" >&2
  printf '%s\n' "$V2C_SOURCE_DIRTY" >&2
  exit 1
}
V2C_SOURCE_COMMIT=$(git rev-parse HEAD)
V2C_RUN_ID="${SLURM_JOB_ID:-manual}"
V2C_OUT="results_v2/v2c/p1a/job${V2C_RUN_ID}_feasibility.json"

if [[ ! -f "$V2C_OUT" ]]; then
  "$V2C_PY" direct_scaling_feasibility.py produce \
    --protocol "$V2C_PROTOCOL" \
    --protocol-sha256 "$V2C_PROTOCOL_SHA256" \
    --corpora-lock "$V2C_LOCK" \
    --corpora-root "$V2C_CORPORA_ROOT" \
    --out "$V2C_OUT"
else
  echo "resuming from existing write-once artifact $V2C_OUT"
fi

[[ "$(git rev-parse HEAD)" == "$V2C_SOURCE_COMMIT" ]] || {
  echo "source commit changed during P1a" >&2
  exit 1
}
V2C_SOURCE_DIRTY=$(git status --porcelain -- . \
  ':(exclude)results_v2' ':(exclude).venv' ':(exclude)corpora')
[[ -z "$V2C_SOURCE_DIRTY" ]] || {
  echo "source tree changed during P1a" >&2
  exit 1
}
"$V2C_PY" direct_scaling_feasibility.py validate \
  --protocol "$V2C_PROTOCOL" \
  --protocol-sha256 "$V2C_PROTOCOL_SHA256" \
  --corpora-lock "$V2C_LOCK" \
  --corpora-root "$V2C_CORPORA_ROOT" \
  --artifact "$V2C_OUT" \
  --deep
echo "V2C-P1A-CLUSTER-DONE $V2C_OUT $(sha256sum "$V2C_OUT" | cut -d' ' -f1)"
