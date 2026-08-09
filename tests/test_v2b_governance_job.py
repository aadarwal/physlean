#!/usr/bin/env python3
"""Static contract for the §15.A14 blind N governance job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_governance.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_array_job_and_source_boundary():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=64G" in src
    assert "#SBATCH -t 04:00:00" in src and "#SBATCH --gres" not in src
    assert "slurm-%x-%A_%a.out" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert 'V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"' in src


def test_exact_five_cohort_and_committed_inputs():
    src = source()
    assert 'V2B_CANDIDATES_JOB="${V2B_CANDIDATES_JOB:?' in src
    assert '[[ "$V2B_CANDIDATES_JOB" =~ ^[0-9]+$ ]]' in src
    assert ("job${V2B_CANDIDATES_JOB}_${V2B_TASK}_"
            "${V2B_TAG}.json") in src
    assert "19931908" not in src
    # masked + manifest + sample must all be committed HEAD blobs
    assert ("job${V2B_MASKED_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert ("job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert 'for V2B_TRACKED in "$V2B_MASKED" "$V2B_MANIFEST" ' \
        '"$V2B_SAMPLE"' in src
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD --" in src


def test_scoring_commit_derivation_post_masked_commit():
    src = source()
    # numeric job ids, as paired validates them
    assert '[[ "$V2B_MASKED_JOB" =~ ^[0-9]+$ ]]' in src
    assert '[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]]' in src
    # HEAD advanced past scoring by the evidence-only masked commit, so
    # the paired dir is re-derived from the exported scoring commit
    assert 'V2B_SCORING_COMMIT="${V2B_SCORING_COMMIT:?' in src
    assert '[[ "$V2B_SCORING_COMMIT" =~ ^[0-9a-f]{40}$ ]]' in src
    assert ("results_v2/v2b/paired/q25c-1.5b/${V2B_SCORING_COMMIT:0:12}-"
            "${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}") in src
    assert 'V2B_COMPLETE="$V2B_COMPLETE_DIR/complete.json"' in src
    # the dir prefix proves 12 hex chars; the completion generator must
    # equal the FULL exported scoring commit
    assert "['generator']['source_commit']" in src
    assert '[[ "$V2B_COMPLETE_COMMIT" == "$V2B_SCORING_COMMIT" ]]' in src


def test_governance_invocation_and_write_once_output():
    src = source()
    assert "v2b_n_governance.py" in src
    for flag in ("--masked-deltas", "--candidates", "--sample",
                 "--complete", "--out"):
        assert flag in src
    assert ("results_v2/v2b/governance/job${V2B_RUN_ID}_${V2B_TAG}.json"
            ) in src
    assert "V2B-GOVERNANCE-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B GOVERNANCE JOB TESTS PASS")
