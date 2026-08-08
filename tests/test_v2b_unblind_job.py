#!/usr/bin/env python3
"""Static contract for the §15.A14 post-governance salt-reveal job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_unblind.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_single_job_and_source_boundary():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=16G" in src
    assert "#SBATCH -t 01:00:00" in src and "#SBATCH --gres" not in src
    assert "slurm-%x-%j.out" in src           # single job, never an array
    assert "SLURM_ARRAY_TASK_ID" not in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3


def test_numeric_ids_and_scoring_commit_guards():
    src = source()
    assert '[[ "$V2B_MASKED_JOB" =~ ^[0-9]+$ ]]' in src
    assert '[[ "$V2B_GOVERNANCE_JOB" =~ ^[0-9]+$ ]]' in src
    assert '[[ "$V2B_BEHAVIORAL_JOB" =~ ^[0-9]+$ ]]' in src
    assert '[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]]' in src
    assert '[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]]' in src
    # the completion generator must equal the FULL exported commit
    assert "['generator']['source_commit']" in src
    assert '[[ "$V2B_COMPLETE_COMMIT" == "$V2B_SCORING_COMMIT" ]]' in src


def test_behavioral_governance_hard_gate():
    # §14.22: NLL governance alone must never reveal the salt — the
    # wrapper is unrunnable until the behavioral chain exists
    src = source()
    assert "NLL governance alone" in src
    assert "v2b_behavioral_governance_v1" in src
    assert ("job${V2B_BEHAVIORAL_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert "--behavioral-governance" in src
    assert 'for V2B_TRACKED in "$V2B_MASKED" "$V2B_GOVERNANCE" ' \
        '"$V2B_BEHAVIORAL"' in src


def test_five_corpus_committed_chain_and_salt_boundary():
    src = source()
    assert "V2B_TAGS=(mathlib4 batteries physlib sympy astropy)" in src
    assert ("job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert ("job${V2B_GOVERNANCE_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert ("job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert "job19931908_${V2B_TASK}_${V2B_TAG}.json" in src
    # masked + governance + behavioral + manifest committed per corpus;
    # candidates are POOL evidence, gated by SHA equality with the
    # masked binding instead of tracking (frozen B0 decision)
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD --" in src
    assert "['bindings']['candidates']['sha256']" in src
    assert '[[ "$V2B_CAND_SHA" == "$V2B_MASKED_CAND_SHA" ]]' in src
    # the private salt stays untracked and 0600 through the reveal
    assert "private salt must NEVER be tracked" in src
    assert 'V2B_SALT_MODE=$(stat -c %a "$V2B_SALT")' in src
    assert '[[ "$V2B_SALT_MODE" == "600" ]]' in src
    assert ("results_v2/v2b/paired/q25c-1.5b/${V2B_SCORING_COMMIT:0:12}-"
            "${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}/complete.json") in src


def test_unblinder_invocation_and_write_once_output():
    src = source()
    assert "finalize_v2b_unblinding.py" in src
    for flag in ("--masked", "--governance", "--complete", "--manifest",
                 "--candidates", "--sample", "--salt ",
                 "--salt-commitment", "--out"):
        assert flag in src
    assert ("results_v2/v2b/unblinding/job${V2B_RUN_ID}_unblinding.json"
            ) in src
    assert "V2B-UNBLIND-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B UNBLIND JOB TESTS PASS")
