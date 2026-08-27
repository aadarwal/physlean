#!/usr/bin/env python3
"""Static fail-closed contracts for the nine confirmation Slurm wrappers.

Every wrapper is a thin, env-var-driven shim around exactly one frozen
confirmation entry point.  These tests bind the on-disk slurm/ set to the
implementation-freeze closure, the CPU/GPU partition split, the frozen
06:00:00 walltime, the four-model battery array, and the blinding boundary
(the analyzer wrapper never receives the private salt).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freeze_v2b_nll_confirmation import FILE_ROLES


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLURM_DIR = os.path.join(ROOT, "slurm")

# wrapper -> the only confirmation program it may invoke
ENTRY_POINTS = {
    "v2b_nll_confirmation_gate.sbatch":
        "prepare_v2b_nll_confirmation_gate.py",
    "v2b_nll_confirmation_prepare.sbatch": (
        "finalize_v2b_nll_confirmation_sample.py",
        "prepare_v2b_nll_confirmation_assembly.py",
        "prepare_v2b_nll_confirmation_salt.py"),
    "v2b_nll_confirmation_battery.sbatch":
        "v2b_nll_confirmation_battery.py",
    "v2b_nll_confirmation_score.sbatch":
        "eval_v2b_nll_confirmation.py",
    "v2b_nll_confirmation_reduce.sbatch":
        "eval_v2b_nll_confirmation.py",
    "v2b_nll_confirmation_mask.sbatch":
        "prepare_v2b_nll_confirmation_masked.py",
    "v2b_nll_confirmation_fixed_n.sbatch":
        "finalize_v2b_nll_confirmation_fixed_n.py",
    "v2b_nll_confirmation_reveal.sbatch":
        "finalize_v2b_nll_confirmation_reveal.py",
    "v2b_nll_confirmation_analysis.sbatch":
        "analyze_v2b_nll_confirmation.py",
}
GPU_JOBS = frozenset((
    "v2b_nll_confirmation_battery.sbatch",
    "v2b_nll_confirmation_score.sbatch"))
FORBIDDEN_PROGRAMS = (
    "eval_paired.py", "prepare_v2b_assembly.py", "finalize_v2b_sample.py",
    "prepare_v2b_masked_deltas.py", "finalize_v2b_unblinding.py",
    "finalize_v2b_nll_exploratory_reveal.py", "analyze_v2b_nll_ladder.py",
    "finalize_v2c_sample.py", "finalize_v2c_reveal.py",
    "v2b_n_governance.py", "v2b_v2c_governance.py")


def _source(name):
    return open(os.path.join(SLURM_DIR, name), encoding="utf-8").read()


def test_freeze_closure_slurm_set_is_exactly_the_on_disk_set():
    frozen = {os.path.basename(path) for path, role in FILE_ROLES.items()
              if role == "slurm"}
    on_disk = {name for name in os.listdir(SLURM_DIR)
               if name.startswith("v2b_nll_confirmation_")
               and name.endswith(".sbatch")}
    assert frozen == set(ENTRY_POINTS)
    assert on_disk == set(ENTRY_POINTS)
    for name in sorted(ENTRY_POINTS):
        path = os.path.join(SLURM_DIR, name)
        assert os.path.isfile(path) and not os.path.islink(path)


def test_common_fail_closed_execution_contract():
    for name in sorted(ENTRY_POINTS):
        src = _source(name)
        assert src.startswith("#!/bin/bash\n"), name
        assert "set -euo pipefail" in src, name
        assert "umask 077" in src, name
        assert ('cd "${SLURM_SUBMIT_DIR:?submit from the frozen '
                'confirmation checkout}"') in src, name
        assert 'PYTHON="${V2B_CONFIRM_PYTHON:-$PWD/.venv/bin/python}"' \
            in src, name
        assert 'test -x "$PYTHON"' in src, name
        # frozen execution_policy partition_time_limit
        assert "#SBATCH --time=06:00:00" in src, name


def test_cpu_gpu_partition_split_matches_the_stage():
    for name in sorted(ENTRY_POINTS):
        src = _source(name)
        if name in GPU_JOBS:
            assert "#SBATCH -p mit_normal_gpu\n" in src, name
            assert "#SBATCH --gres=gpu:l40s:1" in src, name
        else:
            assert "#SBATCH -p mit_normal\n" in src, name
            assert "--gres" not in src, name


def test_each_wrapper_invokes_only_its_frozen_entry_points():
    for name, expected in sorted(ENTRY_POINTS.items()):
        src = _source(name)
        allowed = expected if isinstance(expected, tuple) else (expected,)
        for program in allowed:
            assert program in src, (name, program)
        for other_name, other in ENTRY_POINTS.items():
            other_allowed = other if isinstance(other, tuple) else (other,)
            for program in other_allowed:
                if program not in allowed:
                    assert program not in src, (name, program)
        for program in FORBIDDEN_PROGRAMS:
            assert program not in src, (name, program)


def test_battery_is_an_exact_four_model_array():
    src = _source("v2b_nll_confirmation_battery.sbatch")
    assert "MODELS=(q25c-0.5b q25c-1.5b q25c-3b q25c-7b)" in src
    assert '"${SLURM_ARRAY_TASK_ID:?submit battery as array 0-3}"' in src
    assert "battery array index must be 0..3" in src
    for flag in ("--model-id", "--assembly", "--sample", "--source-gate",
                 "--implementation-freeze", "--out"):
        assert flag in src


def test_score_is_a_requeueable_shard_array_with_all_four_batteries():
    src = _source("v2b_nll_confirmation_score.sbatch")
    assert "#SBATCH --requeue" in src
    assert "SLURM_ARRAY_TASK_ID" in src
    assert '"${V2B_CONFIRM_MODEL_ID:?set one exact protocol model id}"' \
        in src
    for index in range(4):
        assert f'"${{V2B_CONFIRM_BATTERY_{index}:?}}"' in src
    for flag in ("score", "--salt-commitment", "--shard-index",
                 "--target-dir"):
        assert flag in src


def test_mode_dispatch_is_closed_for_gate_prepare_and_reduce():
    gate = _source("v2b_nll_confirmation_gate.sbatch")
    assert '"${V2B_CONFIRM_GATE_MODE:?set fragment or reduce}"' in gate
    for mode in ("fragment)", "reduce)"):
        assert mode in gate
    prepare = _source("v2b_nll_confirmation_prepare.sbatch")
    assert '"${V2B_CONFIRM_PREPARE_MODE:?set sample, assembly, or salt}"' \
        in prepare
    for mode in ("sample)", "assembly)", "salt)"):
        assert mode in prepare
    reduce_src = _source("v2b_nll_confirmation_reduce.sbatch")
    assert '"${V2B_CONFIRM_REDUCE_MODE:?set model or study}"' in reduce_src
    for mode in ("model)", "study)"):
        assert mode in reduce_src
    assert "reduce-model" in reduce_src and "reduce-study" in reduce_src
    for index in range(4):
        assert f'"${{V2B_CONFIRM_MODEL_COMPLETE_{index}:?}}"' in reduce_src


def test_blind_stages_take_the_private_salt_and_analysis_never_does():
    for name in ("v2b_nll_confirmation_mask.sbatch",
                 "v2b_nll_confirmation_fixed_n.sbatch",
                 "v2b_nll_confirmation_reveal.sbatch"):
        src = _source(name)
        assert "V2B_CONFIRM_PRIVATE_SALT" in src, name
        assert "--private-salt" in src, name
    analysis = _source("v2b_nll_confirmation_analysis.sbatch")
    assert "V2B_CONFIRM_PRIVATE_SALT" not in analysis
    assert "--private-salt" not in analysis


def test_reveal_and_analysis_bind_the_full_evidence_chain():
    reveal = _source("v2b_nll_confirmation_reveal.sbatch")
    for flag in ("--private-salt", "--salt-commitment", "--assembly",
                 "--study-complete", "--masked", "--fixed-n",
                 "--implementation-freeze", "--out"):
        assert flag in reveal
    assert '"${V2B_CONFIRM_REVEAL_OUT:?}"' in reveal
    analysis = _source("v2b_nll_confirmation_analysis.sbatch")
    for flag in ("--reveal", "--assembly", "--sample", "--masked",
                 "--fixed-n", "--implementation-freeze",
                 "--salt-commitment", "--out"):
        assert flag in analysis
    assert '"${V2B_CONFIRM_REVEAL:?}"' in analysis
    assert '"${V2B_CONFIRM_ANALYSIS_OUT:?}"' in analysis


if __name__ == "__main__":
    for test_name, function in sorted(globals().items()):
        if test_name.startswith("test_"):
            function()
            print(f"[ok] {test_name}")
    print("V2B NLL CONFIRMATION JOB TESTS PASS")
