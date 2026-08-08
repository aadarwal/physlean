#!/usr/bin/env python3
"""Static contract for the SymPy/Astropy V2-a CPU array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2a_python.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_only_pool_job_and_clean_source():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=64G" in src
    assert "#SBATCH -t 04:00:00" in src and "#SBATCH --gres" not in src
    assert 'V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert '.venv/bin/python' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert 'V2_SOURCE_COMMIT=$(git rev-parse HEAD)' in src
    assert src.count("v2_assert_source_identity") >= 3
    assert "source commit changed during V2-a job" in src
    assert src.count("v2_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src


def test_frozen_revisions_and_structural_checks():
    src = source()
    assert "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c" in src
    assert "440fe546589c4e496235d712bc29783ecf5a5fec" in src
    for program in ("extract_python.py", "validate_v2a.py",
                    "audit_python_compile.py"):
        assert program in src
    assert '--repo-tag "$V2_TAG"' in src
    assert "--n 20" in src
    assert "V2A-PYTHON-STRUCTURAL-DONE" in src
    assert "complete.tsv" in src
    assert '"$V2_SOURCE_COMMIT"' in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2A-PYTHON JOB TESTS PASS")
