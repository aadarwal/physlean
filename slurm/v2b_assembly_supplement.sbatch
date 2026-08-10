#!/bin/bash
#SBATCH -J physlean-v2b-assembly-supplement
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 08:00:00
# EPOCH2 Part C: mathlib4 supplement assembly (n<=120 fresh targets, full
# frozen grid {4096,16384,65536}) on the SAME pinned pilot chain inputs as
# the interior launcher. The committed supplement sample is exported:
#   sbatch -p mit_normal \
#     --export=ALL,V2B_SUPPLEMENT_SAMPLE=<committed supplement json> \
#     slurm/v2b_assembly_supplement.sbatch
# ONE SUBMISSION ONLY after a produced manifest, per the standing rule.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/assembly
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing supplement assembly" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during supplement assembly" >&2; exit 1; }
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || {
    echo "source tree changed during supplement assembly" >&2; exit 1; }
}
v2b_assert_source_identity

V2B_SUPPLEMENT_SAMPLE="${V2B_SUPPLEMENT_SAMPLE:?export the committed supplement sample path}"
git ls-files --error-unmatch "$V2B_SUPPLEMENT_SAMPLE" >/dev/null || {
  echo "supplement sample not tracked/committed" >&2; exit 1; }
git diff --quiet HEAD -- "$V2B_SUPPLEMENT_SAMPLE" || {
  echo "supplement sample differs from HEAD" >&2; exit 1; }
V2B_A6_OUTCOME="results_v2/v2b/a6/a6_outcome_job19930941.json"
git ls-files --error-unmatch "$V2B_A6_OUTCOME" >/dev/null || {
  echo "A6 outcome not tracked/committed" >&2; exit 1; }

V2B_NLL="$V2B_POOL_BASE/physlean-nll-launch/results_v2"
V2B_MAIN="$V2B_POOL_BASE/physlean/results_v2"
V2B_TAG="mathlib4"
V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
V2B_CANDIDATES="$V2B_NLL/v2b/candidates/job19982184_0_mathlib4.json"
V2B_EXTRACTION="$V2B_NLL/v2a/job19915851_0_mathlib4/extraction.json"
V2B_NEARDUP="$V2B_NLL/v2b/neardup/job19930941_0_mathlib4.json"
V2B_K7="$V2B_NLL/v2b/k7/job19921318_0_mathlib4.json"
V2B_BOUNDARIES="$V2B_MAIN/v2b/lean_boundaries/job19975833_0_mathlib4.json"
V2B_KEYWORD_FREEZE="$V2B_MAIN/v2b/lean_keywords/freeze_tokens_job19929877.json"
for V2B_INPUT in "$V2B_CANDIDATES" "$V2B_EXTRACTION" "$V2B_NEARDUP" \
                 "$V2B_K7" "$V2B_BOUNDARIES" "$V2B_KEYWORD_FREEZE"; do
  [[ -f "$V2B_INPUT" ]] || {
    echo "missing pinned supplement-assembly input: $V2B_INPUT" >&2
    exit 1
  }
done

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
[[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2; exit 1; }
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
    echo "$V2B_TAG revision changed during supplement assembly" >&2; exit 1; }
  [[ -z "$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2; exit 1; }
}
v2b_assert_corpus_identity

V2B_OUT="results_v2/v2b/assembly/supplement_job${SLURM_JOB_ID:-manual}_0_mathlib4.json"
"$V2B_PY" prepare_v2b_assembly.py \
  --sample "$V2B_SUPPLEMENT_SAMPLE" \
  --repo "$V2B_TAG" \
  --candidates "$V2B_CANDIDATES" \
  --extraction "$V2B_EXTRACTION" \
  --lean-boundaries "$V2B_BOUNDARIES" \
  --neardup "$V2B_NEARDUP" \
  --a6-outcome "$V2B_A6_OUTCOME" \
  --lean-keyword-freeze "$V2B_KEYWORD_FREEZE" \
  --k7-order "$V2B_K7" \
  --expected-n 120 \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-SUPPLEMENT-ASSEMBLY-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
