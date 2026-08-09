#!/bin/bash
#SBATCH -J physlean-v2b-governance
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 04:00:00
# CPU-only exact-five array: §15.A14 blind N governance, AFTER the
# masked artifacts are committed. HEAD has advanced past the scoring
# commit by the evidence-only masked commit, so the paired complete dir
# is re-derived from the exported scoring commit + committed manifest:
#   sbatch -p mit_normal --array=0-4 \
#     --export=ALL,V2B_CANDIDATES_JOB=<id>,V2B_MASKED_JOB=<id>,V2B_ASSEMBLY_JOB=<id>,V2B_SCORING_COMMIT=<40-hex>,V2B_SAMPLE=<sample.json> \
#     slurm/v2b_governance.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/governance
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing N governance" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during N governance" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during N governance" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_MASKED_JOB="${V2B_MASKED_JOB:?export the committed masked array job id}"
[[ "$V2B_MASKED_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_MASKED_JOB must be a numeric Slurm job id" >&2
  exit 1
}
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
V2B_SCORING_COMMIT="${V2B_SCORING_COMMIT:?export the exact 40-hex scoring commit}"
V2B_SAMPLE="${V2B_SAMPLE:?export the committed V2-b sample path}"
[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "V2B_SCORING_COMMIT must be exactly 40 hex characters" >&2
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
    echo "invalid N governance array task: $V2B_TASK" >&2
    exit 1
    ;;
esac
V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_${V2B_TASK}_${V2B_TAG}.json"
[[ -f "$V2B_CANDIDATES" ]] || {
  echo "missing sealed candidate table: $V2B_CANDIDATES" >&2
  exit 1
}

V2B_MASKED="results_v2/v2b/masked/job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json"
V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
for V2B_TRACKED in "$V2B_MASKED" "$V2B_MANIFEST" "$V2B_SAMPLE"; do
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

# the paired complete dir as slurm/v2b_paired.sbatch named it, from the
# exported scoring commit + the committed manifest SHA
V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)
V2B_COMPLETE_DIR="results_v2/v2b/paired/q25c-1.5b/${V2B_SCORING_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}"
V2B_COMPLETE="$V2B_COMPLETE_DIR/complete.json"
[[ -f "$V2B_COMPLETE" ]] || {
  echo "missing paired completion: $V2B_COMPLETE" >&2
  exit 1
}
# the dir name proves only a 12-hex prefix; require the completion's
# generator to equal the FULL exported scoring commit
V2B_COMPLETE_COMMIT=$("$V2B_PY" -c "import json,sys; \
print(json.load(open(sys.argv[1]))['generator']['source_commit'])" \
"$V2B_COMPLETE")
[[ "$V2B_COMPLETE_COMMIT" == "$V2B_SCORING_COMMIT" ]] || {
  echo "completion generator commit $V2B_COMPLETE_COMMIT != exported \
scoring commit $V2B_SCORING_COMMIT" >&2
  exit 1
}

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_OUT="results_v2/v2b/governance/job${V2B_RUN_ID}_${V2B_TAG}.json"
"$V2B_PY" v2b_n_governance.py \
  --masked-deltas "$V2B_MASKED" \
  --candidates "$V2B_CANDIDATES" \
  --sample "$V2B_SAMPLE" \
  --complete "$V2B_COMPLETE" \
  --out "$V2B_OUT"

v2b_assert_source_identity
echo "V2B-GOVERNANCE-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
