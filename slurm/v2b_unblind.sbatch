#!/bin/bash
#SBATCH -J physlean-v2b-unblind
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:00:00
# CPU-only SINGLE job (one reveal spans all five corpora): §15.A14
# post-governance salt reveal. §14.22 SEQUENCING: NLL governance alone
# is INSUFFICIENT — no formal V2-b unblinding occurs before a committed
# five-corpus BEHAVIORAL-governance chain (schema
# v2b_behavioral_governance_v1; producer not yet implemented, so this
# wrapper is deliberately unrunnable until it exists). Every
# masked/governance/behavioral input must be a committed HEAD blob and
# the private salt stays 0600 and untracked to the end:
#   sbatch -p mit_normal \
#     --export=ALL,V2B_CANDIDATES_JOB=<id>,V2B_MASKED_JOB=<id>,V2B_GOVERNANCE_JOB=<id>,V2B_BEHAVIORAL_JOB=<id>,V2B_ASSEMBLY_JOB=<id>,V2B_SCORING_COMMIT=<40-hex>,V2B_SAMPLE=<sample.json>,V2B_SALT=<pool-salt>,V2B_SALT_COMMITMENT=<tracked-commitment.json> \
#     slurm/v2b_unblind.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/unblinding
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing unblinding" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during unblinding" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during unblinding" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_MASKED_JOB="${V2B_MASKED_JOB:?export the committed masked array job id}"
[[ "$V2B_MASKED_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_MASKED_JOB must be a numeric Slurm job id" >&2
  exit 1
}
V2B_GOVERNANCE_JOB="${V2B_GOVERNANCE_JOB:?export the committed governance array job id}"
[[ "$V2B_GOVERNANCE_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_GOVERNANCE_JOB must be a numeric Slurm job id" >&2
  exit 1
}
V2B_BEHAVIORAL_JOB="${V2B_BEHAVIORAL_JOB:?export the committed behavioral governance array job id — NLL governance alone is insufficient (§14.22)}"
[[ "$V2B_BEHAVIORAL_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_BEHAVIORAL_JOB must be a numeric Slurm job id" >&2
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
[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "V2B_SCORING_COMMIT must be exactly 40 hex characters" >&2
  exit 1
}
V2B_SAMPLE="${V2B_SAMPLE:?export the committed V2-b sample path}"
V2B_SALT="${V2B_SALT:?export the private POOL salt path}"
V2B_SALT_COMMITMENT="${V2B_SALT_COMMITMENT:?export the tracked salt commitment path}"

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
V2B_SALT_MODE=$(stat -c %a "$V2B_SALT")
[[ "$V2B_SALT_MODE" == "600" ]] || {
  echo "private salt must have mode 0600, found $V2B_SALT_MODE" >&2
  exit 1
}

V2B_TAGS=(mathlib4 batteries physlib sympy astropy)
V2B_MASKED_ARGS=()
V2B_GOVERNANCE_ARGS=()
V2B_BEHAVIORAL_ARGS=()
V2B_COMPLETE_ARGS=()
V2B_MANIFEST_ARGS=()
V2B_CANDIDATE_ARGS=()
for V2B_TASK in 0 1 2 3 4; do
  V2B_TAG="${V2B_TAGS[$V2B_TASK]}"
  V2B_MASKED="results_v2/v2b/masked/job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_GOVERNANCE="results_v2/v2b/governance/job${V2B_GOVERNANCE_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_BEHAVIORAL="results_v2/v2b/behavioral_governance/job${V2B_BEHAVIORAL_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  for V2B_TRACKED in "$V2B_MASKED" "$V2B_GOVERNANCE" "$V2B_BEHAVIORAL" \
                     "$V2B_MANIFEST"; do
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
  # candidate tables are POOL evidence (never tracked, frozen B0
  # decision): the committed-equivalent gate is exact SHA equality with
  # the masked artifact's sealed candidates binding
  [[ -f "$V2B_CANDIDATES" ]] || {
    echo "missing sealed candidate table: $V2B_CANDIDATES" >&2
    exit 1
  }
  V2B_MASKED_CAND_SHA=$("$V2B_PY" -c "import json,sys; \
print(json.load(open(sys.argv[1]))['bindings']['candidates']['sha256'])" \
"$V2B_MASKED")
  V2B_CAND_SHA=$(sha256sum "$V2B_CANDIDATES" | cut -d' ' -f1)
  [[ "$V2B_CAND_SHA" == "$V2B_MASKED_CAND_SHA" ]] || {
    echo "candidate table differs from the masked binding: $V2B_CANDIDATES" >&2
    exit 1
  }
  V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)
  V2B_COMPLETE="results_v2/v2b/paired/q25c-1.5b/${V2B_SCORING_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}/complete.json"
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
  V2B_MASKED_ARGS+=(--masked "$V2B_MASKED")
  V2B_GOVERNANCE_ARGS+=(--governance "$V2B_GOVERNANCE")
  V2B_BEHAVIORAL_ARGS+=(--behavioral-governance "$V2B_BEHAVIORAL")
  V2B_COMPLETE_ARGS+=(--complete "$V2B_COMPLETE")
  V2B_MANIFEST_ARGS+=(--manifest "$V2B_MANIFEST")
  V2B_CANDIDATE_ARGS+=(--candidates "$V2B_CANDIDATES")
done

V2B_RUN_ID="${SLURM_JOB_ID:-manual}"
V2B_OUT="results_v2/v2b/unblinding/job${V2B_RUN_ID}_unblinding.json"
"$V2B_PY" finalize_v2b_unblinding.py \
  "${V2B_MASKED_ARGS[@]}" \
  "${V2B_GOVERNANCE_ARGS[@]}" \
  "${V2B_BEHAVIORAL_ARGS[@]}" \
  "${V2B_COMPLETE_ARGS[@]}" \
  "${V2B_MANIFEST_ARGS[@]}" \
  "${V2B_CANDIDATE_ARGS[@]}" \
  --sample "$V2B_SAMPLE" \
  --salt "$V2B_SALT" \
  --salt-commitment "$V2B_SALT_COMMITMENT" \
  --out "$V2B_OUT"

v2b_assert_source_identity
echo "V2B-UNBLIND-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
