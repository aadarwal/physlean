#!/bin/bash
#SBATCH -J physlean-v2c-paired
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH -t 06:00:00
#SBATCH --requeue
# V2-c confirmatory scoring: one planned repo's committed v2c assembly
# manifest scored at the frozen primary tier with the current epoch
# battery. The manifest is created mid-campaign, so it carries no
# in-script sha pin (the proven env-path pattern): it must be a
# COMMITTED, HEAD-clean artifact, with binding enforced downstream by
# the v2c ledger's row equality and the consumer's post-assembly pin.
# One submission per repo:
#   sbatch -p mit_normal_gpu --gres=gpu:l40s:1 \
#     --export=ALL,V2B_MODEL_TIER=q25c-1.5b,V2C_MANIFEST=<path> \
#     slurm/v2c_paired.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export HF_HOME="$V2B_POOL_BASE/hf"
export XDG_CACHE_HOME="$V2B_POOL_BASE/xdg-cache"
export TORCH_HOME="$V2B_POOL_BASE/torch-home"
export MPLCONFIGDIR="$V2B_POOL_BASE/mpl"
export TRITON_CACHE_DIR="$V2B_POOL_BASE/triton-cache"
export CUDA_CACHE_PATH="$V2B_POOL_BASE/cuda-cache"
export TMPDIR="$V2B_POOL_BASE/tmp"

V2B_MODEL_TIER="${V2B_MODEL_TIER:?export V2B_MODEL_TIER}"
case "$V2B_MODEL_TIER" in
  q25c-0.5b) V2B_MODEL="Qwen/Qwen2.5-Coder-0.5B" ;;
  q25c-1.5b) V2B_MODEL="Qwen/Qwen2.5-Coder-1.5B" ;;
  q25c-3b)   V2B_MODEL="Qwen/Qwen2.5-Coder-3B" ;;
  q25c-7b)   V2B_MODEL="Qwen/Qwen2.5-Coder-7B" ;;
  q25c-14b)
    V2B_MODEL="Qwen/Qwen2.5-Coder-14B"
    if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -qi h200; then
      echo "q25c-14b v2c scoring requires an H200 GPU" >&2
      exit 1
    fi
    ;;
  q25c-32b)
    V2B_MODEL="Qwen/Qwen2.5-Coder-32B"
    if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -qi h200; then
      echo "q25c-32b v2c scoring requires an H200 GPU" >&2
      exit 1
    fi
    export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
    ;;
  *)
    echo "unknown tier: $V2B_MODEL_TIER" >&2
    exit 1
    ;;
esac
case "$V2B_MODEL_TIER" in
  q25c-0.5b) V2B_TIER_BATTERY="results_v2/battery/battery_pilot_0p5b.json" ;;
  q25c-1.5b) V2B_TIER_BATTERY="results_v2/battery/battery_pilot_1p5b.json" ;;
  q25c-3b)   V2B_TIER_BATTERY="results_v2/battery/battery_pilot_3b.json" ;;
  q25c-7b)   V2B_TIER_BATTERY="results_v2/battery/battery_pilot_7b.json" ;;
  q25c-14b)  V2B_TIER_BATTERY="results_v2/battery/battery_pilot_14b.json" ;;
  q25c-32b)  V2B_TIER_BATTERY="results_v2/battery/battery_pilot_32b.json" ;;
esac

mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" \
  "$TRITON_CACHE_DIR" "$CUDA_CACHE_PATH" "$TMPDIR" logs \
  "results_v2/v2c/paired/$V2B_MODEL_TIER"
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing v2c scoring" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during v2c scoring" >&2; exit 1; }
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || {
    echo "source tree changed during v2c scoring" >&2; exit 1; }
}
v2b_assert_source_identity

V2B_TAG=$("$V2B_PY" -c "import json; print(json.load(open('${V2C_MANIFEST:?export the committed v2c manifest path}'))['repo'])")
case "$V2B_TAG" in
  mathlib4) V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad" ;;
  sympy)    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c" ;;
  *) echo "v2c scoring covers mathlib4/sympy only: $V2B_TAG" >&2; exit 1 ;;
esac
V2B_MANIFEST="${V2C_MANIFEST:?export the committed v2c manifest path}"
case "$V2B_MANIFEST" in
  results_v2/v2c/assembly/v2c_*_*.json) ;;
  *)
    echo "v2c manifest path is not a v2c assembly artifact: $V2B_MANIFEST" >&2
    exit 1
    ;;
esac

for V2B_TRACKED in "$V2B_TIER_BATTERY" "$V2B_MANIFEST"; do
  [[ -f "$V2B_TRACKED" ]] || {
    echo "missing input: $V2B_TRACKED" >&2; exit 1; }
  git ls-files --error-unmatch "$V2B_TRACKED" >/dev/null || {
    echo "input not tracked/committed: $V2B_TRACKED" >&2; exit 1; }
  git diff --quiet HEAD -- "$V2B_TRACKED" || {
    echo "input differs from HEAD: $V2B_TRACKED" >&2; exit 1; }
done
V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
[[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2; exit 1; }
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
    echo "$V2B_TAG revision changed during v2c scoring" >&2; exit 1; }
  [[ -z "$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2; exit 1; }
}
v2b_assert_corpus_identity

V2B_OUT_DIR="results_v2/v2c/paired/${V2B_MODEL_TIER}/${V2B_SOURCE_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}"
"$V2B_PY" eval_paired.py \
  --model "$V2B_MODEL" \
  --manifest "$V2B_MANIFEST" \
  --pilot-battery "$V2B_TIER_BATTERY" \
  --out-dir "$V2B_OUT_DIR" \
  --dtype bfloat16 \
  --device cuda

v2b_assert_corpus_identity
v2b_assert_source_identity
[[ -f "$V2B_OUT_DIR/complete.json" ]] || {
  echo "paired evaluator returned without complete.json" >&2
  exit 1
}
echo "V2B-PAIRED-SUPPLEMENT-DONE $V2B_OUT_DIR $(sha256sum "$V2B_OUT_DIR/complete.json" | cut -d' ' -f1)"
