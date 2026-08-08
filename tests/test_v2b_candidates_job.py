#!/usr/bin/env python3
"""Static contract for the exact-five CPU V2-b candidate array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_candidates.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_pool_source_and_corpus_locks():
    src = source()
    assert "#SBATCH -c 16" in src and "#SBATCH --mem=32G" in src
    assert "#SBATCH -t 08:00:00" in src and "#SBATCH --gres" not in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert ".venv/bin/python" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    for sha in ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
                "76e1c118b0700b4ceafe99532e887d6431625e1a",
                "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
                "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
                "440fe546589c4e496235d712bc29783ecf5a5fec"):
        assert sha in src


def test_exact_structural_inputs_and_new_only_output():
    src = source()
    assert "--array=0-4" in src
    assert "cohort_job19915851_19916781_19915852.json" in src
    for job in ("job19915851_0_mathlib4", "job19915851_1_batteries",
                "job19916781_2_physlib", "job19915852_0_sympy",
                "job19915852_1_astropy"):
        assert job + "/extraction.json" in src
    assert "prepare_v2b_candidates.py" in src
    assert "--structural-cohort" in src
    assert "--expected-corpus-sha" in src
    assert "--workers" in src
    assert "V2B-CANDIDATES-DONE" in src
    assert "job${V2B_RUN_ID}_${V2B_TAG}.json" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B CANDIDATE JOB TESTS PASS")
