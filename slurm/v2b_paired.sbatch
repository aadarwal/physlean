#!/bin/bash
#SBATCH -J physlean-v2b-paired
#SBATCH -o logs/slurm-%x-%A_%a.out
#SBATCH --nodes=1
#SBATCH -c 8
#SBATCH --mem=80G
#SBATCH -t 12:00:00
#SBATCH --requeue
# One frozen-model load per corpus; target artifacts are atomic and resumable.
# Assembly manifests must be committed before this job is submitted:
#   sbatch -p mit_normal_gpu --gres=gpu:l40s:1 --array=0-4 \
#     --export=ALL,V2B_ASSEMBLY_JOB=<array-job-id> slurm/v2b_paired.sbatch
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
mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" \
  "$TRITON_CACHE_DIR" "$CUDA_CACHE_PATH" "$TMPDIR" logs \
  results_v2/v2b/paired/q25c-1.5b
V2B_PY="$SLURM_SUBMIT_DIR/.venv/bin/python"
[[ -x "$V2B_PY" ]] || {
  echo "missing locked project Python: $V2B_PY" >&2
  exit 1
}
nvidia-smi -L || { echo "NO GPU VISIBLE" >&2; exit 1; }

V2B_SOURCE_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
[[ -z "$V2B_SOURCE_DIRT" ]] || {
  echo "source tree dirty outside results_v2; refusing V2-b paired NLL" >&2
  printf '%s\n' "$V2B_SOURCE_DIRT" >&2
  exit 1
}
V2B_SOURCE_COMMIT=$(git rev-parse HEAD)
v2b_assert_source_identity() {
  V2B_CURRENT_COMMIT=$(git rev-parse HEAD)
  V2B_CURRENT_DIRT=$(git status --porcelain -- . ':(exclude)results_v2')
  [[ "$V2B_CURRENT_COMMIT" == "$V2B_SOURCE_COMMIT" ]] || {
    echo "source commit changed during V2-b paired NLL" >&2
    exit 1
  }
  [[ -z "$V2B_CURRENT_DIRT" ]] || {
    echo "source tree changed during V2-b paired NLL" >&2
    exit 1
  }
}
v2b_assert_source_identity

V2B_ASSEMBLY_JOB="${V2B_ASSEMBLY_JOB:?export the committed assembly array job id}"
[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]] || {
  echo "V2B_ASSEMBLY_JOB must be a numeric Slurm job id" >&2
  exit 1
}
V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"
case "$V2B_TASK" in
  0)
    V2B_TAG="mathlib4"
    V2B_EXPECTED_SHA="87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
    ;;
  1)
    V2B_TAG="batteries"
    V2B_EXPECTED_SHA="76e1c118b0700b4ceafe99532e887d6431625e1a"
    ;;
  2)
    V2B_TAG="physlib"
    V2B_EXPECTED_SHA="e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
    ;;
  3)
    V2B_TAG="sympy"
    V2B_EXPECTED_SHA="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"
    ;;
  4)
    V2B_TAG="astropy"
    V2B_EXPECTED_SHA="440fe546589c4e496235d712bc29783ecf5a5fec"
    ;;
  *)
    echo "invalid V2-b paired array task: $V2B_TASK" >&2
    exit 1
    ;;
esac

V2B_MANIFEST="results_v2/v2b/assembly/job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json"
[[ -f "$V2B_MANIFEST" ]] || {
  echo "missing committed assembly manifest: $V2B_MANIFEST" >&2
  exit 1
}
git ls-files --error-unmatch "$V2B_MANIFEST" >/dev/null || {
  echo "assembly manifest is not tracked/committed: $V2B_MANIFEST" >&2
  exit 1
}
git diff --quiet HEAD -- "$V2B_MANIFEST" || {
  echo "assembly manifest differs from HEAD: $V2B_MANIFEST" >&2
  exit 1
}

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
    echo "$V2B_TAG revision changed during V2-b paired NLL" >&2
    exit 1
  }
  [[ -z "$V2B_CORPUS_DIRT" ]] || {
    echo "$V2B_TAG source tree is dirty" >&2
    printf '%s\n' "$V2B_CORPUS_DIRT" >&2
    exit 1
  }
}
v2b_assert_corpus_identity

V2B_MANIFEST_SHA=$(sha256sum "$V2B_MANIFEST" | cut -d' ' -f1)
V2B_OUT_DIR="results_v2/v2b/paired/q25c-1.5b/${V2B_SOURCE_COMMIT:0:12}-${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}"
"$V2B_PY" eval_paired.py \
  --model Qwen/Qwen2.5-Coder-1.5B \
  --manifest "$V2B_MANIFEST" \
  --out-dir "$V2B_OUT_DIR" \
  --dtype bfloat16 \
  --device cuda

v2b_assert_corpus_identity
v2b_assert_source_identity
[[ -f "$V2B_OUT_DIR/complete.json" ]] || {
  echo "paired evaluator returned without complete.json" >&2
  exit 1
}
echo "V2B-PAIRED-DONE $V2B_OUT_DIR $(sha256sum "$V2B_OUT_DIR/complete.json" | cut -d' ' -f1)"
