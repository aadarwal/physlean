#!/usr/bin/env python3
"""Static contract for the pinned-snapshot PhysLib k4x graph job."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_k4x.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_source_and_checkout_locks():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=64G" in src
    assert "#SBATCH -t 04:00:00" in src and "#SBATCH --gres" not in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert "--untracked-files=all" in src
    assert "e882411d1b6bcbdfdd336d4c509c6cc72e96842d" in src
    assert "81a5d257c8e410db227a6665ed08f64fea08e997" in src


def test_exact_existing_extractions_and_new_output():
    src = source()
    assert "job19916781_2_physlib/extraction.json" in src
    assert "job19916781_2_physlib/pinned_mathlib_extraction.json" in src
    assert "9f4a192059ede347093c4f424940198e45cc93b9140f0ef8e5b8a465e0b6f796" in src
    assert "prepare_v2b_k4x_graph.py" in src
    assert "job${V2B_RUN_ID}_physlib.json" in src
    assert "V2B-K4X-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B K4X JOB TESTS PASS")
