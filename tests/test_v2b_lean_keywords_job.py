#!/usr/bin/env python3
"""Static contract for the dependency-bound keyword finalizer."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_lean_keywords.sbatch")


def test_finalizer_requires_exact_parent_array_and_write_once_output():
    src = open(SCRIPT, encoding="utf-8").read()
    assert "--dependency=afterok:<id>" in src
    assert "V2B_TOKEN_ARRAY_ID" in src
    assert "finalize_v2b_lean_keywords.py" in src
    assert src.count("--table") == 3
    assert "_0_mathlib4.json" in src
    assert "_1_batteries.json" in src
    assert "_2_physlib.json" in src
    assert "freeze_tokens_job${V2B_TOKEN_ARRAY_ID}.json" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert "V2B-LEAN-KEYWORDS-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN KEYWORD JOB TESTS PASS")
