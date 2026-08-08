#!/usr/bin/env python3
"""Static contract for the pre-score source-only ledger array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_source_token_ledger.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_only_pre_score_array():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=24G" in src
    assert "#SBATCH --gres" not in src and "--model" not in src
    assert "SLURM_ARRAY_TASK_ID" in src
    assert "BEFORE paired NLL scoring" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src


def test_exact_committed_assembly_and_corpus_boundary():
    src = source()
    assert '[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]]' in src
    assert "job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json" in src
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD --" in src
    for tag in ("mathlib4", "batteries", "physlib", "sympy", "astropy"):
        assert f'V2B_TAG="{tag}"' in src
    assert "--untracked-files=all" in src
    assert src.count("v2b_assert_source_identity") >= 3


def test_write_once_job_scoped_ledger():
    src = source()
    assert "prepare_v2b_source_token_ledger.py" in src
    assert '--manifest "$V2B_MANIFEST"' in src
    assert '--out "$V2B_OUT"' in src
    assert "results_v2/v2b/source_tokens/job${V2B_RUN_ID}_${V2B_TAG}.json" \
        in src
    assert "V2B-SOURCE-TOKENS-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B SOURCE TOKEN LEDGER JOB TESTS PASS")
