#!/bin/bash
#SBATCH -J physlean-v2b-masked
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:00:00
# CPU-only exact-five array: §15.A14 B3 masked-delta production. Runs at
# the EXACT scoring HEAD (the producer refuses otherwise) and derives
# each paired complete dir from that HEAD + the committed assembly
# manifest SHA, exactly as slurm/v2b_paired.sbatch named it:
#   sbatch -p mit_normal --array=0-4 \
#     --export=ALL,V2B_CANDIDATES_JOB=<id>,V2B_ASSEMBLY_JOB=<id>,V2B_SAMPLE=<sample.json>,V2B_SALT=<pool-salt>,V2B_SALT_COMMITMENT=<tracked-commitment.json> \
#     slurm/v2b_masked.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/masked
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b masking" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b masking" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b masking" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_ASSEMBLY_JOB="${V2B_ASSEMBLY_JOB:?export the committed assembly array job id}"
[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_ASSEMBLY_JOB must be a numeric Slurm job id" >&2
  exit 1
}
V2B_CANDIDATES_JOB="${V2B_CANDIDATES_JOB:?export the sealed candidate array job id}"
[[ "$V2B_CANDIDATES_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_CANDIDATES_JOB must be a numeric Slurm job id" >&2
  exit 1
}
V2B_SAMPLE="${V2B_SAMPLE:?export the committed V2-b sample path}"
V2B_SALT="${V2B_SALT:?export the private POOL salt path}"
V2B_SALT_COMMITMENT="${V2B_SALT_COMMITMENT:?export the tracked salt commitment path}"

# committed public inputs; the PRIVATE salt must exist and must NEVER be
# tracked — a tracked salt is an unblinding, not a configuration error
for V2B_TRACKED in "$V2B_SAMPLE" "$V2B_SALT_COMMITMENT"; do
  [[ -f "$V2B_TRACKED" ]] || {
    echo "missing committed input: $V2B_TRACKED" >&2
    exit 1
  }
  git ls-files --error-unmatch "$V2B_TRACKED" >/dev/null || {
    echo "input is not tracked/committed: $V2B_TRACKED" >&2
    exit 1
  }
  git diff --quiet HEAD -- "$V2B_TRACKED" || {
    echo "input differs from HEAD: $V2B_TRACKED" >&2
    exit 1
  }
done
[[ -f "$V2B_SALT" ]] || {
  echo "missing private salt: $V2B_SALT" >&2
  exit 1
}
if git ls-files --error-unmatch "$V2B_SALT" >/dev/null 2>&1; then
  echo "private salt must NEVER be tracked: $V2B_SALT" >&2
  exit 1
fi
# group/world-readable salt is a silent pre-reveal unblinding channel
V2B_SALT_MODE=$(stat -c %a "$V2B_SALT")
[[ "$V2B_SALT_MODE" == "600" ]] || {
  echo "private salt must have mode 0600, found $V2B_SALT_MODE" >&2
  exit 1
}

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    ;;
  1)
    V2B_TAG="batteries"
    ;;
  2)
    V2B_TAG="physlib"
    ;;
  3)
    V2B_TAG="sympy"
    ;;
  4)
    V2B_TAG="astropy"
    ;;
  *)
    echo "invalid V2-b masked array task: $V2B_TASK" >&2
    exit 1
    ;;
esac
V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_${V2B_TASK}_${V2B_TAG}.json"
[[ -f "$V2B_CANDIDATES" ]] || {
  echo "missing sealed candidate table: $V2B_CANDIDATES" >&2
  exit 1
}

V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
[[ -f "$V2B_MANIFEST" ]] || {
  echo "missing committed assembly manifest: $V2B_MANIFEST" >&2
  exit 1
}
git ls-files --error-unmatch "$V2B_MANIFEST" >/dev/null || {
  echo "assembly manifest is not tracked/committed: $V2B_MANIFEST" >&2
  exit 1
}
git diff --quiet HEAD -- "$V2B_MANIFEST" || {
  echo "assembly manifest differs from HEAD: $V2B_MANIFEST" >&2
  exit 1
}

# derive the paired complete dir exactly as slurm/v2b_paired.sbatch did:
# scoring HEAD (this job's HEAD, enforced by the producer) + manifest SHA
V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)
V2B_COMPLETE_DIR="results_v2/v2b/paired/q25c-1.5b/${V2B_SOURCE_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}"
V2B_COMPLETE="$V2B_COMPLETE_DIR/complete.json"
[[ -f "$V2B_COMPLETE" ]] || {
  echo "missing paired completion: $V2B_COMPLETE" >&2
  exit 1
}

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_OUT="results_v2/v2b/masked/job${V2B_RUN_ID}_${V2B_TAG}.json"
"$V2B_PY" prepare_v2b_masked_deltas.py \
  --complete "$V2B_COMPLETE" \
  --manifest "$V2B_MANIFEST" \
  --sample "$V2B_SAMPLE" \
  --candidates "$V2B_CANDIDATES" \
  --salt "$V2B_SALT" \
  --salt-commitment "$V2B_SALT_COMMITMENT" \
  --out "$V2B_OUT"

v2b_assert_source_identity
echo "V2B-MASKED-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
