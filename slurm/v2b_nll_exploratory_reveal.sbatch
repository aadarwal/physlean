#!/bin/bash
#SBATCH -J physlean-v2b-nll-exploratory-reveal
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:00:00
# CPU-only SINGLE job. This is explicitly NOT formal V2-b unblinding.
# It may run only after all five masked artifacts and all five blind
# N-governance artifacts are committed:
#   sbatch -p mit_normal \
#     --export=ALL,V2B_CANDIDATES_JOB=<id>,V2B_MASKED_JOB=<id>,V2B_GOVERNANCE_JOB=<id>,V2B_ASSEMBLY_JOB=<id>,V2B_SCORING_COMMIT=<40-hex>,V2B_SAMPLE=<sample.json>,V2B_SALT=<pool-salt>,V2B_SALT_COMMITMENT=<tracked-commitment.json> \
#     slurm/v2b_nll_exploratory_reveal.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
[[ "$(git rev-parse --show-toplevel)" == "$SLURM_SUBMIT_DIR" ]] || {
  echo "SLURM_SUBMIT_DIR is not the canonical repository root" >&2
  exit 1
}

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/nll_exploratory_reveal
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing reveal" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || exit 1
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || exit 1
}
v2b_assert_source_identity

for V2B_NAME in V2B_CANDIDATES_JOB V2B_MASKED_JOB V2B_GOVERNANCE_JOB \
                V2B_ASSEMBLY_JOB; do
  V2B_VALUE="${!V2B_NAME:?missing $V2B_NAME}"
  [[ "$V2B_VALUE" =~ ^[0-9]+$ ]] || { echo "$V2B_NAME must be numeric" >&2; exit 1; }
done
V2B_SCORING_COMMIT="${V2B_SCORING_COMMIT:?export exact scoring commit}"
[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 1
V2B_SAMPLE="${V2B_SAMPLE:?export committed sample path}"
V2B_SALT="${V2B_SALT:?export private POOL salt path}"
V2B_SALT_COMMITMENT="${V2B_SALT_COMMITMENT:?export commitment path}"

for V2B_TRACKED in "$V2B_SAMPLE" "$V2B_SALT_COMMITMENT" \
  results_v2/v2b/NLL_ONLY_EXPLORATORY_REVEAL_AMENDMENT.md \
  results_v2/v2b/NLL_ONLY_EXPLORATORY_REVEAL_IMPLEMENTATION_FREEZE.json; do
  [[ -f "$V2B_TRACKED" ]] || { echo "missing committed input: $V2B_TRACKED" >&2; exit 1; }
  git ls-files --error-unmatch -- "$V2B_TRACKED" >/dev/null || exit 1
  git diff --quiet HEAD -- "$V2B_TRACKED" || exit 1
done
[[ -f "$V2B_SALT" ]] || { echo "missing private salt" >&2; exit 1; }
if git ls-files --error-unmatch -- "$V2B_SALT" >/dev/null 2>&1; then
  echo "private salt must NEVER be tracked" >&2; exit 1
fi
[[ "$(stat -c %a "$V2B_SALT")" == "600" ]] || {
  echo "private salt must have mode 0600" >&2; exit 1
}

V2B_TAGS=(mathlib4 batteries physlib sympy astropy)
V2B_MASKED_ARGS=(); V2B_GOVERNANCE_ARGS=(); V2B_COMPLETE_ARGS=()
V2B_MANIFEST_ARGS=(); V2B_CANDIDATE_ARGS=()
for V2B_TASK in 0 1 2 3 4; do
  V2B_TAG="${V2B_TAGS[$V2B_TASK]}"
  V2B_MASKED="results_v2/v2b/masked/job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_GOVERNANCE="results_v2/v2b/governance/job${V2B_GOVERNANCE_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  for V2B_TRACKED in "$V2B_MASKED" "$V2B_GOVERNANCE" "$V2B_MANIFEST"; do
    [[ -f "$V2B_TRACKED" ]] || { echo "missing committed input: $V2B_TRACKED" >&2; exit 1; }
    git ls-files --error-unmatch -- "$V2B_TRACKED" >/dev/null || exit 1
    git diff --quiet HEAD -- "$V2B_TRACKED" || exit 1
  done
  [[ -f "$V2B_CANDIDATES" ]] || { echo "missing candidate table" >&2; exit 1; }
  V2B_MASKED_CAND_SHA=$("$V2B_PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['bindings']['candidates']['sha256'])" "$V2B_MASKED")
  [[ "$(sha256sum "$V2B_CANDIDATES" | cut -d' ' -f1)" == "$V2B_MASKED_CAND_SHA" ]] || exit 1
  V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)
  V2B_COMPLETE="results_v2/v2b/paired/q25c-1.5b/${V2B_SCORING_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}/complete.json"
  [[ -f "$V2B_COMPLETE" ]] || { echo "missing paired completion" >&2; exit 1; }
  V2B_COMPLETE_COMMIT=$("$V2B_PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['generator']['source_commit'])" "$V2B_COMPLETE")
  [[ "$V2B_COMPLETE_COMMIT" == "$V2B_SCORING_COMMIT" ]] || exit 1
  V2B_MASKED_ARGS+=(--masked "$V2B_MASKED")
  V2B_GOVERNANCE_ARGS+=(--governance "$V2B_GOVERNANCE")
  V2B_COMPLETE_ARGS+=(--complete "$V2B_COMPLETE")
  V2B_MANIFEST_ARGS+=(--manifest "$V2B_MANIFEST")
  V2B_CANDIDATE_ARGS+=(--candidates "$V2B_CANDIDATES")
done

V2B_RUN_ID="${SLURM_JOB_ID:-manual}"
V2B_OUT="results_v2/v2b/nll_exploratory_reveal/job${V2B_RUN_ID}_nll_exploratory_reveal.json"
"$V2B_PY" finalize_v2b_nll_exploratory_reveal.py \
  "${V2B_MASKED_ARGS[@]}" "${V2B_GOVERNANCE_ARGS[@]}" \
  "${V2B_COMPLETE_ARGS[@]}" "${V2B_MANIFEST_ARGS[@]}" \
  "${V2B_CANDIDATE_ARGS[@]}" --sample "$V2B_SAMPLE" \
  --salt "$V2B_SALT" --salt-commitment "$V2B_SALT_COMMITMENT" \
  --out "$V2B_OUT"
v2b_assert_source_identity
echo "V2B-NLL-EXPLORATORY-REVEAL-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
