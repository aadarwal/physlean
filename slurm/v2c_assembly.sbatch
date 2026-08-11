#!/bin/bash
#SBATCH -J physlean-v2c-assembly
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 08:00:00
# V2-c confirmatory assembly: the two planned repos at their per-repo
# reshaped samples, full frozen grid, chain inputs derived from the
# committed pilot manifests' own bindings (the proven single source of
# truth). One submission per repo after a produced manifest. Submit:
#   sbatch -p mit_normal --array=0-1 slurm/v2c_assembly.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2c/assembly
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing v2c assembly" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during v2c assembly" >&2; exit 1; }
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || {
    echo "source tree changed during v2c assembly" >&2; exit 1; }
}
v2b_assert_source_identity

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-1}"
case "$V2B_TASK" in
  0) V2B_TAG="mathlib4"; V2B_MANIFEST_I=0; V2B_EXPECTED_N=52
     V2B_CORPUS_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad" ;;
  1) V2B_TAG="sympy"; V2B_MANIFEST_I=3; V2B_EXPECTED_N=77
     V2B_CORPUS_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c" ;;
  *) echo "invalid v2c assembly task: $V2B_TASK" >&2; exit 1 ;;
esac
V2B_SAMPLE="results_v2/v2c/v2c_sample_${V2B_TAG}.json"
git ls-files --error-unmatch "$V2B_SAMPLE" >/dev/null || {
  echo "reshaped sample not tracked/committed: $V2B_SAMPLE" >&2; exit 1; }
git diff --quiet HEAD -- "$V2B_SAMPLE" || {
  echo "reshaped sample differs from HEAD" >&2; exit 1; }
V2B_A6_OUTCOME="results_v2/v2b/a6/a6_outcome_job19930941.json"
git ls-files --error-unmatch "$V2B_A6_OUTCOME" >/dev/null

V2B_PILOT_MANIFEST="results_v2/v2b/assembly/job19991210_${V2B_MANIFEST_I}_${V2B_TAG}.json"
git ls-files --error-unmatch "$V2B_PILOT_MANIFEST" >/dev/null
bind() {
  "$V2B_PY" -c "import json; b=json.load(open('$V2B_PILOT_MANIFEST'))['bindings'].get('$1') or {}; print(b.get('path') or '-')"
}
V2B_CANDIDATES=$(bind candidates)
V2B_EXTRACTION=$(bind extraction)
V2B_NEARDUP=$(bind neardup)
V2B_K7=$(bind k7_order)
V2B_BOUNDARIES=$(bind lean_boundaries)
V2B_KEYWORD_FREEZE=$(bind keyword_freeze)
for V2B_INPUT in "$V2B_CANDIDATES" "$V2B_EXTRACTION" "$V2B_NEARDUP" "$V2B_K7"; do
  [[ -f "$V2B_INPUT" ]] || {
    echo "missing pinned v2c-assembly input: $V2B_INPUT" >&2; exit 1; }
done
V2B_EXTRA_ARGS=()
if [[ "$V2B_BOUNDARIES" != "-" ]]; then
  V2B_EXTRA_ARGS+=(--lean-boundaries "$V2B_BOUNDARIES")
fi
if [[ "$V2B_KEYWORD_FREEZE" != "-" ]]; then
  V2B_EXTRA_ARGS+=(--lean-keyword-freeze "$V2B_KEYWORD_FREEZE")
fi

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
[[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_CORPUS_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2; exit 1; }
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_CORPUS_SHA" ]] || {
    echo "$V2B_TAG revision changed during v2c assembly" >&2; exit 1; }
  [[ -z "$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2; exit 1; }
}
v2b_assert_corpus_identity

V2B_OUT="results_v2/v2c/assembly/v2c_job${SLURM_ARRAY_JOB_ID:-manual}_${V2B_TASK}_${V2B_TAG}.json"
"$V2B_PY" prepare_v2b_assembly.py \
  --sample "$V2B_SAMPLE" \
  --repo "$V2B_TAG" \
  --candidates "$V2B_CANDIDATES" \
  --extraction "$V2B_EXTRACTION" \
  --neardup "$V2B_NEARDUP" \
  --a6-outcome "$V2B_A6_OUTCOME" \
  --k7-order "$V2B_K7" \
  --expected-n "$V2B_EXPECTED_N" \
  "${V2B_EXTRA_ARGS[@]}" \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2C-ASSEMBLY-DONE $V2B_TAG $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
