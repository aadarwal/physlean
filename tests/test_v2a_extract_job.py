#!/usr/bin/env python3
"""Static contract for the CPU-only V2-a extraction/audit array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2a_extract.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_resources_and_pool_environment():
    src = source()
    assert "#SBATCH -c 16" in src
    assert "#SBATCH --mem=128G" in src
    assert "#SBATCH -t 08:00:00" in src
    assert "#SBATCH --gres" not in src
    assert 'V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    for name in ("ELAN_HOME", "XDG_CACHE_HOME", "TMPDIR"):
        assert f"export {name}=" in src
    assert '.venv/bin/python' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert 'V2_SOURCE_COMMIT=$(git rev-parse HEAD)' in src
    assert src.count("v2_assert_source_identity") >= 3
    assert "source commit changed during V2-a job" in src
    assert src.count("v2_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "lean_artifacts_job19911017.tsv" in src
    assert "ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6" in src
    assert "V2_ARTIFACT_REPO_SHA" in src
    assert "artifact_build_report_sha256" in src
    assert 'END {print value}' in src  # final status, not initial "building"


def test_all_frozen_repo_revisions_are_hard_coded():
    src = source()
    for sha in (
        "87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
        "76e1c118b0700b4ceafe99532e887d6431625e1a",
        "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
    ):
        assert sha in src
    assert "--expected-repo-sha" in src
    assert 'V2_ACTUAL_SHA=$(git -C "$V2_REPO" rev-parse HEAD)' in src


def test_structural_gate_runs_both_independent_audits():
    src = source()
    for program in ("pair_ilean.py", "extract_lean.py", "validate_v2a.py",
                    "audit_lean_closure.py", "audit_lean_compile.py"):
        assert program in src
    assert "--n 20" in src
    assert "V2A-STRUCTURAL-DONE" in src
    assert "complete.tsv" in src
    assert '"$V2_SOURCE_COMMIT"' in src


def test_physlib_pinned_mathlib_is_manifest_derived():
    src = source()
    assert 'if [[ "$V2_TAG" == "physlib" ]]' in src
    assert 'p["name"] == "mathlib"' in src
    assert '.lake/packages/mathlib' in src
    assert "physlib_pinned_mathlib" in src
    assert 'V2_PIN_ACTUAL=$(git -C "$V2_PIN_REPO" rev-parse HEAD)' in src
    assert "PhysLib-pinned mathlib source tree is dirty" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2A-EXTRACT JOB TESTS PASS")
