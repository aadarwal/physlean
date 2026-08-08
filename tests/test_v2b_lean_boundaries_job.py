#!/usr/bin/env python3
"""Static contract tests for the CPU-only Lean boundary array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = open(os.path.join(ROOT, "slurm", "v2b_lean_boundaries.sbatch"),
           encoding="utf-8").read()


def test_exact_three_corpora_and_frozen_extractions():
    for tag, revision, extraction in (
        ("mathlib4", "87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
         "job19915851_0_mathlib4/extraction.json"),
        ("batteries", "76e1c118b0700b4ceafe99532e887d6431625e1a",
         "job19915851_1_batteries/extraction.json"),
        ("physlib", "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
         "job19916781_2_physlib/extraction.json")):
        assert f'V2B_TAG="{tag}"' in JOB
        assert revision in JOB
        assert extraction in JOB
    assert "submit as array indices 0-2" in JOB


def test_cpu_pool_requeue_and_exact_pipeline_order():
    lower = JOB.lower()
    assert "#sbatch --requeue" in lower
    assert "#sbatch -c 16" in lower
    assert "#sbatch --mem=128g" in lower
    assert "--gres" not in lower and "--gpus" not in lower
    assert 'export ELAN_HOME="$V2B_POOL_BASE/elan"' in JOB
    assert "export HOME=" not in JOB
    setup = JOB.index("prepare_v2b_lean_setups.py")
    plan = JOB.index("v2b_lean_boundaries.py plan")
    run = JOB.index("run_v2b_lean_boundary_audit.py")
    finalize = JOB.index("v2b_lean_boundaries.py finalize")
    assert setup < plan < run < finalize
    assert 'V2B_POOL_RUN="$V2B_POOL_BASE/v2b-lean-boundaries/' in JOB
    assert "--workers \"${SLURM_CPUS_PER_TASK:-16}\"" in JOB


def test_source_corpus_artifact_and_runtime_are_fail_closed():
    assert "git status --porcelain -- . ':(exclude)results_v2'" in JOB
    assert "git -C \"$V2B_REPO\" status --porcelain" in JOB
    assert "lean_artifacts_job19911017.tsv" in JOB
    assert "ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6" \
        in JOB
    assert 'V2B_TOOLCHAIN=$(tr -d' in JOB
    assert '"$V2B_ELAN" which lean' in JOB
    assert "export LEAN_NUM_THREADS=1" in JOB
    assert "unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH LEAN_PATH LEAN_SRC_PATH" \
        in JOB
    assert '--workers "${SLURM_CPUS_PER_TASK:-16}" --timeout 7200' in JOB


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
    print("V2B LEAN BOUNDARY JOB TESTS PASS")
