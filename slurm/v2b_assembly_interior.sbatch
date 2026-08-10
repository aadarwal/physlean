#!/bin/bash
#SBATCH -J physlean-v2b-assembly-interior
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 08:00:00
# DOSE_CURVE_EXPANSION Part B: interior-budget assembly {8192,16384,32768}
# for mathlib4 (task 0) and sympy (task 1) ONLY, on the sealed sample and
# the exact pinned pilot chain inputs (no operator-supplied paths: every
# input below is the byte-identical binding recorded in the committed
# job19991210 manifests). ONE SUBMISSION ONLY per the amendment: any
# re-submission after a produced manifest quarantines the interior arm.
#   sbatch -p mit_normal --array=0-1 slurm/v2b_assembly_interior.sbatch
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
  echo "source tree dirty outside results_v2; refusing interior assembly" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during interior assembly" >&2; exit 1; }
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || {
    echo "source tree changed during interior assembly" >&2; exit 1; }
}
v2b_assert_source_identity

V2B_BUDGETS="8192,16384,32768"
V2B_NLL="$V2B_POOL_BASE/physlean-nll-launch/results_v2"
V2B_MAIN="$V2B_POOL_BASE/physlean/results_v2"
V2B_SAMPLE="results_v2/v2b/sample/job19989076_sample.json"
V2B_A6_OUTCOME="results_v2/v2b/a6/a6_outcome_job19930941.json"
for V2B_TRACKED in "$V2B_SAMPLE" "$V2B_A6_OUTCOME"; do
  git ls-files --error-unmatch "$V2B_TRACKED" >/dev/null || {
    echo "input not tracked/committed: $V2B_TRACKED" >&2; exit 1; }
  git diff --quiet HEAD -- "$V2B_TRACKED" || {
    echo "input differs from HEAD: $V2B_TRACKED" >&2; exit 1; }
done

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-1}"
V2B_BOUNDARY_ARGS=()
V2B_KEYWORD_ARGS=()
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    V2B_CANDIDATES="$V2B_NLL/v2b/candidates/job19982184_0_mathlib4.json"
    V2B_EXTRACTION="$V2B_NLL/v2a/job19915851_0_mathlib4/extraction.json"
    V2B_NEARDUP="$V2B_NLL/v2b/neardup/job19930941_0_mathlib4.json"
    V2B_K7="$V2B_NLL/v2b/k7/job19921318_0_mathlib4.json"
    V2B_BOUNDARIES="$V2B_MAIN/v2b/lean_boundaries/job19975833_0_mathlib4.json"
    V2B_BOUNDARY_ARGS=(--lean-boundaries "$V2B_BOUNDARIES")
    V2B_KEYWORD_FREEZE="$V2B_MAIN/v2b/lean_keywords/freeze_tokens_job19929877.json"
    V2B_KEYWORD_ARGS=(--lean-keyword-freeze "$V2B_KEYWORD_FREEZE")
    ;;
  1)
    V2B_TAG="sympy"
    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    V2B_CANDIDATES="$V2B_NLL/v2b/candidates/job19982184_3_sympy.json"
    V2B_EXTRACTION="$V2B_NLL/v2a/job19915852_0_sympy/extraction.json"
    V2B_NEARDUP="$V2B_NLL/v2b/neardup/job19929883_3_sympy.json"
    V2B_K7="$V2B_NLL/v2b/k7/job19921318_3_sympy.json"
    ;;
  *)
    echo "invalid interior assembly task: $V2B_TASK" >&2
    exit 1
    ;;
esac

for V2B_INPUT in "$V2B_CANDIDATES" "$V2B_EXTRACTION" "$V2B_NEARDUP" \
                 "$V2B_K7" "${V2B_KEYWORD_FREEZE:-}" \
                 "${V2B_BOUNDARIES:-}"; do
  [[ -z "$V2B_INPUT" || -f "$V2B_INPUT" ]] || {
    echo "missing pinned interior-assembly input: $V2B_INPUT" >&2
    exit 1
  }
done

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2; exit 1; }
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_ACTUAL_SHA" ]] || {
    echo "$V2B_TAG revision changed during interior assembly" >&2; exit 1; }
  [[ -z "$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2; exit 1; }
}
v2b_assert_corpus_identity

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_OUT="results_v2/v2b/assembly/interior_job${V2B_ARRAY_ID}_${V2B_TASK}_${V2B_TAG}.json"
"$V2B_PY" prepare_v2b_assembly.py \
  --sample "$V2B_SAMPLE" \
  --repo "$V2B_TAG" \
  --candidates "$V2B_CANDIDATES" \
  --extraction "$V2B_EXTRACTION" \
  "${V2B_BOUNDARY_ARGS[@]}" \
  --neardup "$V2B_NEARDUP" \
  --a6-outcome "$V2B_A6_OUTCOME" \
  "${V2B_KEYWORD_ARGS[@]}" \
  --k7-order "$V2B_K7" \
  --budgets "$V2B_BUDGETS" \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-INTERIOR-ASSEMBLY-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
