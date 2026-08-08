#!/usr/bin/env python3
"""Static contract for the resumable exact-five paired-NLL GPU array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_paired.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_single_gpu_exact_five_resumable_job():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=80G" in src
    assert "#SBATCH -t 12:00:00" in src and "#SBATCH --requeue" in src
    assert "--gres=gpu:l40s:1" in src and "--array=0-4" in src
    assert "nvidia-smi -L" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    for cache in ("HF_HOME", "XDG_CACHE_HOME", "TORCH_HOME",
                  "TRITON_CACHE_DIR", "CUDA_CACHE_PATH", "TMPDIR"):
        assert f"export {cache}=" in src
    assert "eval_paired.py" in src
    assert "--model Qwen/Qwen2.5-Coder-1.5B" in src
    assert "--dtype bfloat16" in src and "--device cuda" in src
    assert "complete.json" in src and "V2B-PAIRED-DONE" in src


def test_manifest_source_and_corpus_are_fail_closed():
    src = source()
    assert "V2B_ASSEMBLY_JOB" in src
    assert "job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json" in src
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "V2B_MANIFEST_SHA" in src
    for tag in ("mathlib4", "batteries", "physlib", "sympy", "astropy"):
        assert f'V2B_TAG="{tag}"' in src
    for sha in ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
                "76e1c118b0700b4ceafe99532e887d6431625e1a",
                "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
                "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
                "440fe546589c4e496235d712bc29783ecf5a5fec"):
        assert sha in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B PAIRED JOB TESTS PASS")
