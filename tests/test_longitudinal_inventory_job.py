#!/usr/bin/env python3
"""Static contract for the model-free mathlib longitudinal inventory job."""
import json
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "longitudinal_inventory.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_cpu_only_model_free_job():
    src = source()
    assert "#SBATCH -c 4" in src and "#SBATCH --mem=16G" in src
    assert "#SBATCH -t 01:00:00" in src
    assert "#SBATCH --gres" not in src
    assert "transformers" not in src and "--model" not in src
    assert 'V2L_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "prepare_longitudinal_inventory.py" in src


def test_plan_source_and_corpus_are_locked():
    src = source()
    assert 'V2L_PLAN="longitudinal/mathlib4_inventory_plan.json"' in src
    assert 'git ls-files --error-unmatch "$V2L_PLAN"' in src
    assert 'git diff --quiet HEAD -- "$V2L_PLAN"' in src
    plan = json.load(open(os.path.join(
        ROOT, "longitudinal", "mathlib4_inventory_plan.json"),
        encoding="utf-8"))
    lock = json.load(open(os.path.join(ROOT, "corpora_lock.json"),
                          encoding="utf-8"))
    found = re.search(r'V2L_EXPECTED_HEAD="([0-9a-f]{40})"', src)
    assert found and found.group(1) == plan["expected_head"]
    assert plan["expected_head"] == lock["repos"][plan["repo"]]["sha"]
    assert "--untracked-files=no" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2l_assert_source_identity") >= 3


def test_write_once_job_scoped_evidence():
    src = source()
    assert '--plan "$V2L_PLAN"' in src
    assert '--repo-root "$V2L_REPO"' in src
    assert '--out "$V2L_OUT"' in src
    assert "job${V2L_RUN_ID}_mathlib4_inventory.json" in src
    assert "LONGITUDINAL-INVENTORY-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("LONGITUDINAL INVENTORY JOB TESTS PASS")
