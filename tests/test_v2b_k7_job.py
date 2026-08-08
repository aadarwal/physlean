#!/usr/bin/env python3
"""Static contract for the exact-five CPU V2-b k7 array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_k7.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_exact_five_locked_cpu_job():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=32G" in src
    assert "#SBATCH -t 02:00:00" in src and "#SBATCH --gres" not in src
    assert "--array=0-4" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert ".venv/bin/python" in src and "v2b_k7.py" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "job${V2B_RUN_ID}_${V2B_TAG}.json" in src
    assert "V2B-K7-DONE" in src
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
    print("V2B K7 JOB TESTS PASS")
