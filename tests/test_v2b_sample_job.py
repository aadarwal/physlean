#!/usr/bin/env python3
"""Static contract for the sealed five-corpus pilot draw job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_sample.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_job_and_source_boundary():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=64G" in src
    assert "#SBATCH -t 04:00:00" in src and "#SBATCH --gres" not in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert "V2B_A6_OUTCOME" in src


def test_exact_candidate_cohort_and_new_output():
    src = source()
    for task, tag in enumerate(("mathlib4", "batteries", "physlib",
                                "sympy", "astropy")):
        assert f"job19931908_{task}_{tag}.json" in src
    assert "finalize_v2b_sample.py" in src
    assert 'V2B_CANDIDATE_ARGS+=(--candidates "$V2B_CANDIDATE")' in src
    assert "job${V2B_RUN_ID}_sample.json" in src
    assert "V2B-SAMPLE-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B SAMPLE JOB TESTS PASS")
