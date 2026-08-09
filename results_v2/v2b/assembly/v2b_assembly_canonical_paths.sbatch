#!/bin/bash
#SBATCH -J physlean-v2b-assembly
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 08:00:00
# CPU-only exact-five array. The two write-once, committed artifacts are
# supplied only after the blind A6 review and deterministic target draw:
#   sbatch -p mit_normal --array=0-4 \
#     --export=ALL,V2B_SAMPLE=<sample.json>,V2B_A6_OUTCOME=<outcome.json>,V2B_K4X_GRAPH=<k4x.json>,V2B_CANDIDATES_JOB=<candidate-array-id>,V2B_BOUNDARY_JOB=<boundary-array-id> \
#     slurm/v2b_assembly.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2b/assembly
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b assembly" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b assembly" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b assembly" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_SAMPLE="${V2B_SAMPLE:?export the committed V2-b sample path}"
V2B_A6_OUTCOME="${V2B_A6_OUTCOME:?export the committed A6 outcome path}"
[[ -f "$V2B_SAMPLE" && -f "$V2B_A6_OUTCOME" ]] || {
  echo "missing committed sample or A6 outcome" >&2
  exit 1
}
for V2B_TRACKED in "$V2B_SAMPLE" "$V2B_A6_OUTCOME"; do
  git ls-files --error-unmatch "$V2B_TRACKED" >/dev/null || {
    echo "assembly input is not tracked/committed: $V2B_TRACKED" >&2
    exit 1
  }
  git diff --quiet HEAD -- "$V2B_TRACKED" || {
    echo "assembly input differs from HEAD: $V2B_TRACKED" >&2
    exit 1
  }
done

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"
V2B_K4X_ARGS=()
V2B_BOUNDARY_ARGS=()
V2B_CANDIDATES_JOB="${V2B_CANDIDATES_JOB:?export the completed candidate array job id}"
[[ "$V2B_CANDIDATES_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_CANDIDATES_JOB must be numeric" >&2
  exit 1
}
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_0_mathlib4.json"
    V2B_EXTRACTION="results_v2/v2a/job19915851_0_mathlib4/extraction.json"
    V2B_NEARDUP="results_v2/v2b/neardup/job19930941_0_mathlib4.json"
    V2B_K7="results_v2/v2b/k7/job19921318_0_mathlib4.json"
    ;;
  1)
    V2B_TAG="batteries"
    V2B_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_1_batteries.json"
    V2B_EXTRACTION="results_v2/v2a/job19915851_1_batteries/extraction.json"
    V2B_NEARDUP="results_v2/v2b/neardup/job19929883_1_batteries.json"
    V2B_K7="results_v2/v2b/k7/job19921318_1_batteries.json"
    ;;
  2)
    V2B_TAG="physlib"
    V2B_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_2_physlib.json"
    V2B_EXTRACTION="results_v2/v2a/job19916781_2_physlib/extraction.json"
    V2B_NEARDUP="results_v2/v2b/neardup/job19929883_2_physlib.json"
    V2B_K7="results_v2/v2b/k7/job19921318_2_physlib.json"
    V2B_K4X_GRAPH="${V2B_K4X_GRAPH:?export the sealed PhysLib k4x graph path}"
    V2B_K4X_EXTERNAL="results_v2/v2a/job19916781_2_physlib/pinned_mathlib_extraction.json"
    V2B_K4X_ARGS=(--k4x-graph "$V2B_K4X_GRAPH"
                  --k4x-external-extraction "$V2B_K4X_EXTERNAL")
    ;;
  3)
    V2B_TAG="sympy"
    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_3_sympy.json"
    V2B_EXTRACTION="results_v2/v2a/job19915852_0_sympy/extraction.json"
    V2B_NEARDUP="results_v2/v2b/neardup/job19929883_3_sympy.json"
    V2B_K7="results_v2/v2b/k7/job19921318_3_sympy.json"
    ;;
  4)
    V2B_TAG="astropy"
    V2B_EXPECTED_SHA="440fe546589c4e496235d712bc29783ecf5a5fec"
    V2B_CANDIDATES="results_v2/v2b/candidates/job${V2B_CANDIDATES_JOB}_4_astropy.json"
    V2B_EXTRACTION="results_v2/v2a/job19915852_1_astropy/extraction.json"
    V2B_NEARDUP="results_v2/v2b/neardup/job19929883_4_astropy.json"
    V2B_K7="results_v2/v2b/k7/job19921318_4_astropy.json"
    ;;
  *)
    echo "invalid V2-b assembly array task: $V2B_TASK" >&2
    exit 1
    ;;
esac

if [[ "$V2B_TASK" -le 2 ]]; then
  V2B_BOUNDARY_JOB="${V2B_BOUNDARY_JOB:?export the completed Lean boundary array job id}"
  [[ "$V2B_BOUNDARY_JOB" =~ ^[0-9]+$ ]] || {
    echo "V2B_BOUNDARY_JOB must be numeric" >&2
    exit 1
  }
  V2B_BOUNDARIES="$V2B_POOL_BASE/physlean/results_v2/v2b/lean_boundaries/job${V2B_BOUNDARY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
  V2B_BOUNDARY_ARGS=(--lean-boundaries "$V2B_BOUNDARIES")
fi

V2B_KEYWORD_ARGS=()
if [[ "$V2B_TASK" -le 2 ]]; then
  V2B_KEYWORD_FREEZE="$V2B_POOL_BASE/physlean/results_v2/v2b/lean_keywords/freeze_tokens_job19929877.json"
  V2B_KEYWORD_ARGS=(--lean-keyword-freeze "$V2B_KEYWORD_FREEZE")
fi
for V2B_INPUT in "$V2B_CANDIDATES" "$V2B_EXTRACTION" "$V2B_NEARDUP" \
                 "$V2B_K7" "${V2B_KEYWORD_FREEZE:-}" \
                 "${V2B_K4X_GRAPH:-}" "${V2B_K4X_EXTERNAL:-}" \
                 "${V2B_BOUNDARIES:-}"; do
  [[ -z "$V2B_INPUT" || -f "$V2B_INPUT" ]] || {
    echo "missing sealed V2-b assembly input: $V2B_INPUT" >&2
    exit 1
  }
done

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
V2B_ACTUAL_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
[[ "$V2B_ACTUAL_SHA" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift: $V2B_ACTUAL_SHA != $V2B_EXPECTED_SHA" >&2
  exit 1
}
v2b_assert_corpus_identity() {
  V2B_CURRENT_REPO_SHA=$(git -C "$V2B_REPO" rev-parse HEAD)
  V2B_CORPUS_DIRT=$(git -C "$V2B_REPO" status --porcelain \
    --untracked-files=all)
  [[ "$V2B_CURRENT_REPO_SHA" == "$V2B_ACTUAL_SHA" ]] || {
    echo "$V2B_TAG revision changed during V2-b assembly" >&2
    exit 1
  }
  [[ -z "$V2B_CORPUS_DIRT" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2
    printf '%s\n' "$V2B_CORPUS_DIRT" >&2
    exit 1
  }
}
v2b_assert_corpus_identity

V2B_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-manual}}"
V2B_RUN_ID="${V2B_ARRAY_ID}_${V2B_TASK}"
V2B_OUT="results_v2/v2b/assembly/job${V2B_RUN_ID}_${V2B_TAG}.json"
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
  "${V2B_K4X_ARGS[@]}" \
  --out "$V2B_OUT"

v2b_assert_corpus_identity
v2b_assert_source_identity
echo "V2B-ASSEMBLY-DONE $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
