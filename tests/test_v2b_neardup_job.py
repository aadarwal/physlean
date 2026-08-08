#!/usr/bin/env python3
"""Static contract for the staged exact-five CPU A6 array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_neardup.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_staged_exact_five_bound_job():
    src = source()
    assert "#SBATCH -c 16" in src and "#SBATCH --mem=128G" in src
    assert "#SBATCH -t 12:00:00" in src and "#SBATCH --gres" not in src
    assert "--array=1-4" in src and "--array=0" in src
    assert "prepare_v2b_neardup.py" in src
    assert "cohort_job19915851_19916781_19915852.json" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "job${V2B_RUN_ID}_${V2B_TAG}.json" in src
    assert "V2B-NEARDUP-DONE" in src
    assert "V2B_KEYWORD_FREEZE" in src
    assert "--lean-keyword-freeze" in src
    for job in ("job19915851_0_mathlib4", "job19915851_1_batteries",
                "job19916781_2_physlib", "job19915852_0_sympy",
                "job19915852_1_astropy"):
        assert job + "/extraction.json" in src
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
    print("V2B NEARDUP JOB TESTS PASS")
