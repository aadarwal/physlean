#!/usr/bin/env python3
"""Static fail-closed contract for the separate exploratory reveal job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_nll_exploratory_reveal.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_single_job_and_separate_output_surface():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=16G" in src
    assert "#SBATCH -t 01:00:00" in src and "#SBATCH --gres" not in src
    assert "SLURM_ARRAY_TASK_ID" not in src
    assert 'git rev-parse --show-toplevel' in src
    assert '== "$SLURM_SUBMIT_DIR"' in src
    assert "results_v2/v2b/nll_exploratory_reveal/" in src
    assert "V2B-NLL-EXPLORATORY-REVEAL-DONE" in src
    assert "results_v2/v2b/unblinding/" not in src
    assert "V2B-UNBLIND-DONE" not in src


def test_exact_pre_score_amendment_and_committed_blind_chain():
    src = source()
    assert "NLL_ONLY_EXPLORATORY_REVEAL_AMENDMENT.md" in src
    assert "NLL_ONLY_EXPLORATORY_REVEAL_IMPLEMENTATION_FREEZE.json" in src
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD --" in src
    assert "V2B_TAGS=(mathlib4 batteries physlib sympy astropy)" in src
    assert ("job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert ("job${V2B_GOVERNANCE_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert "--masked" in src and "--governance" in src
    assert "--behavioral-governance" not in src
    assert "V2B_BEHAVIORAL_JOB" not in src


def test_source_scoring_candidates_and_private_salt_are_gated():
    src = source()
    for name in ("V2B_CANDIDATES_JOB", "V2B_MASKED_JOB",
                 "V2B_GOVERNANCE_JOB", "V2B_ASSEMBLY_JOB"):
        assert name in src
    assert "V2B_SCORING_COMMIT" in src
    assert '[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]]' in src
    assert "['generator']['source_commit']" in src
    assert '[[ "$V2B_COMPLETE_COMMIT" == "$V2B_SCORING_COMMIT" ]]' in src
    assert "['bindings']['candidates']['sha256']" in src
    assert "private salt must NEVER be tracked" in src
    assert '[[ "$(stat -c %a "$V2B_SALT")" == "600" ]]' in src
    assert src.count("v2b_assert_source_identity") >= 3


def test_invokes_only_the_distinct_exploratory_entry_point():
    src = source()
    assert "finalize_v2b_nll_exploratory_reveal.py" in src
    assert "finalize_v2b_unblinding.py" not in src
    for flag in ("--masked", "--governance", "--complete", "--manifest",
                 "--candidates", "--sample", "--salt ",
                 "--salt-commitment", "--out"):
        assert flag in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B NLL EXPLORATORY REVEAL JOB TESTS PASS")
