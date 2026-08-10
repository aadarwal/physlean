#!/bin/bash
#SBATCH -J physlean-v2b-paired-interior
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH -t 06:00:00
#SBATCH --requeue
# EPOCH2 interior scoring (Part B phase 3): the two interior manifests,
# pinned BY SHA below, scored across the six-tier ladder with the epoch-2
# tier batteries. One submission per (tier, repo). Submit per tier:
#   sbatch [-t/-p/--gres per tier class] --array=0-1 \
#     --export=ALL,V2B_MODEL_TIER=<tier> slurm/v2b_paired_interior.sbatch
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
      echo "q25c-14b interior scoring requires an H200 GPU" >&2
      exit 1
    fi
    ;;
  q25c-32b)
    V2B_MODEL="Qwen/Qwen2.5-Coder-32B"
    if ! nvidia-smi --query-gpu=name --format=csv,noheader | grep -qi h200; then
      echo "q25c-32b interior scoring requires an H200 GPU" >&2
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
  "results_v2/v2b/paired_interior/$V2B_MODEL_TIER"
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || { echo "missing locked project Python" >&2; exit 1; }
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing interior scoring" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  [[ "$(git rev-parse HEAD)" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during interior scoring" >&2; exit 1; }
  [[ -z "$(git status --porcelain -- . ':(exclude)results_v2')" ]] || {
    echo "source tree changed during interior scoring" >&2; exit 1; }
}
v2b_assert_source_identity

V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-1}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    V2B_MANIFEST="results_v2/v2b/assembly/interior_job20042050_0_mathlib4.json"
    V2B_MANIFEST_PIN="1ea57d0c2941a337efdb50d3d3476718398445e796cafe3ea6a9c66ef90831f6"
    ;;
  1)
    V2B_TAG="sympy"
    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    V2B_MANIFEST="results_v2/v2b/assembly/interior_job20040278_1_sympy.json"
    V2B_MANIFEST_PIN="da99692033a7828807d39071c4e9eee23ea0cbaf1d3b0c0082e8ae733e460131"
    ;;
  *)
    echo "invalid interior scoring task: $V2B_TASK" >&2
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
[[ "$V2B_MANIFEST_SHA" == "$V2B_MANIFEST_PIN" ]] || {
  echo "interior manifest does not match its pinned sha" >&2
  exit 1
}

V2B_REPO="$SLURM_SUBMIT_DIR/corpora/$V2B_TAG"
[[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
  echo "$V2B_TAG revision drift" >&2; exit 1; }
v2b_assert_corpus_identity() {
  [[ "$(git -C "$V2B_REPO" rev-parse HEAD)" == "$V2B_EXPECTED_SHA" ]] || {
    echo "$V2B_TAG revision changed during interior scoring" >&2; exit 1; }
  [[ -z "$(git -C "$V2B_REPO" status --porcelain --untracked-files=all)" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2; exit 1; }
}
v2b_assert_corpus_identity

V2B_OUT_DIR="results_v2/v2b/paired_interior/${V2B_MODEL_TIER}/${V2B_SOURCE_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}"
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
echo "V2B-PAIRED-INTERIOR-DONE $V2B_OUT_DIR $(sha256sum "$V2B_OUT_DIR/complete.json" | cut -d' ' -f1)"
