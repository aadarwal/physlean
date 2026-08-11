#!/bin/bash
#SBATCH -J physlean-v2c-k4-scan
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=48G
#SBATCH -t 06:00:00
# V2-c Problem-2 input: model-free k4-fill scan over the FULL candidate
# population, one array task per corpus, with the exact-mass pilot
# cross-check. Submit: sbatch -p mit_normal --array=0-4 \
#   slurm/v2c_k4_scan.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"
mkdir -p "$TMPDIR" logs results_v2/v2c

V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }
V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing scan" >&2; exit 1; }

V2B_NLL="$V2B_POOL_BASE/physlean-nll-launch/results_v2"
V2B_MAIN="$V2B_POOL_BASE/physlean/results_v2"
V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"
case "$V2B_TASK" in
  0) V2B_TAG="mathlib4" ;;
  1) V2B_TAG="batteries" ;;
  2) V2B_TAG="physlib" ;;
  3) V2B_TAG="sympy" ;;
  4) V2B_TAG="astropy" ;;
  *) echo "invalid scan task: $V2B_TASK" >&2; exit 1 ;;
esac
V2B_MANIFEST="results_v2/v2b/assembly/job19991210_${V2B_TASK}_${V2B_TAG}.json"
git ls-files --error-unmatch "$V2B_MANIFEST" >/dev/null
# The committed pilot manifest's own chain bindings are the single
# source of truth for input paths (the scan re-verifies each sha).
V2B_CANDIDATES=$("$V2B_PY" -c "import json; print(json.load(open('$V2B_MANIFEST'))['bindings']['candidates']['path'])")
V2B_EXTRACTION=$("$V2B_PY" -c "import json; print(json.load(open('$V2B_MANIFEST'))['bindings']['extraction']['path'])")
V2B_NEARDUP=$("$V2B_PY" -c "import json; print(json.load(open('$V2B_MANIFEST'))['bindings']['neardup']['path'])")
V2B_BOUNDARIES=$("$V2B_PY" -c "import json; b=json.load(open('$V2B_MANIFEST'))['bindings'].get('lean_boundaries') or {}; print(b.get('path') or '-')")
V2B_A6_OUTCOME="results_v2/v2b/a6/a6_outcome_job19930941.json"
V2B_EXTRA_ARGS=()
if [[ "$V2B_BOUNDARIES" != "-" ]]; then
  V2B_EXTRA_ARGS+=(--lean-boundaries "$V2B_BOUNDARIES")
fi
case "$V2B_TAG" in
  mathlib4|batteries|physlib)
    V2B_EXTRA_ARGS+=(--corpus-root "$SLURM_SUBMIT_DIR/corpora/$V2B_TAG")
    ;;
esac
for V2B_INPUT in "$V2B_CANDIDATES" "$V2B_EXTRACTION" "$V2B_NEARDUP"; do
  [[ -f "$V2B_INPUT" ]] || {
    echo "missing pinned scan input: $V2B_INPUT" >&2; exit 1; }
done
git ls-files --error-unmatch "$V2B_A6_OUTCOME" >/dev/null

V2B_OUT="results_v2/v2c/k4_scan_${SLURM_ARRAY_JOB_ID:-manual}_${V2B_TASK}_${V2B_TAG}.json"
"$V2B_PY" scan_v2c_k4_mass.py \
  --repo "$V2B_TAG" \
  --candidates "$V2B_CANDIDATES" \
  --extraction "$V2B_EXTRACTION" \
  --neardup "$V2B_NEARDUP" \
  --a6-outcome "$V2B_A6_OUTCOME" \
  --pilot-manifest "$V2B_MANIFEST" \
  "${V2B_EXTRA_ARGS[@]}" \
  --out "$V2B_OUT"
echo "V2C-K4-SCAN-DONE $V2B_TAG $V2B_OUT $(sha256sum "$V2B_OUT" | cut -d' ' -f1)"
