#!/usr/bin/env python3
"""Static contract for the §15.A14 B3 masked-delta production job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_masked.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_array_job_and_source_boundary():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=16G" in src
    assert "#SBATCH -t 01:00:00" in src and "#SBATCH --gres" not in src
    assert "slurm-%x-%A_%a.out" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert 'V2B_TASK="${SLURM_ARRAY_TASK_ID:?submit as array 0-4}"' in src


def test_exact_five_cohort_and_completion_derivation():
    src = source()
    for task, tag in enumerate(("mathlib4", "batteries", "physlib",
                                "sympy", "astropy")):
        assert f"job19931908_{task}_{tag}.json" in src
    # complete dir derived exactly as the paired job named it: current
    # scoring HEAD + committed manifest SHA
    assert ("job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json") in src
    assert ("results_v2/v2b/paired/q25c-1.5b/${V2B_SOURCE_COMMIT:0:12}-"
            "${V2B_MANIFEST_SHA:0:12}-${V2B_TAG}") in src
    assert 'V2B_COMPLETE="$V2B_COMPLETE_DIR/complete.json"' in src


def test_committed_inputs_and_private_salt_boundary():
    src = source()
    # numeric job id, as paired validates it
    assert '[[ "$V2B_ASSEMBLY_JOB" =~ ^[0-9]+$ ]]' in src
    # sample + salt commitment + manifest are committed HEAD blobs
    assert src.count("git ls-files --error-unmatch") >= 3
    assert src.count("git diff --quiet HEAD --") >= 2
    assert "V2B_SALT_COMMITMENT" in src
    # the PRIVATE salt must exist, must NEVER be tracked, and must not be
    # group/world-readable (a lax mode is a pre-reveal unblinding channel)
    assert "private salt must NEVER be tracked" in src
    assert 'if git ls-files --error-unmatch "$V2B_SALT"' in src
    assert 'V2B_SALT_MODE=$(stat -c %a "$V2B_SALT")' in src
    assert '[[ "$V2B_SALT_MODE" == "600" ]]' in src


def test_producer_invocation_and_write_once_output():
    src = source()
    assert "prepare_v2b_masked_deltas.py" in src
    for flag in ("--complete", "--manifest", "--sample", "--candidates",
                 "--salt ", "--salt-commitment", "--out"):
        assert flag in src
    assert "results_v2/v2b/masked/job${V2B_RUN_ID}_${V2B_TAG}.json" in src
    assert "V2B-MASKED-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B MASKED JOB TESTS PASS")
