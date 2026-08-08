#!/usr/bin/env python3
"""Static contract for the source-locked exact-five assembly array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_assembly.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_exact_five_cpu_job_and_write_once_inputs():
    src = source()
    assert "#SBATCH -c 8" in src and "#SBATCH --mem=64G" in src
    assert "#SBATCH -t 08:00:00" in src and "#SBATCH --gres" not in src
    assert "--array=0-4" in src
    assert 'V2B_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    assert "V2B_SAMPLE" in src and "V2B_A6_OUTCOME" in src
    assert "V2B_K4X_GRAPH" in src
    assert "V2B_BOUNDARY_JOB" in src and "V2B_CANDIDATES_JOB" in src
    assert "--lean-boundaries" in src
    assert "prepare_v2b_assembly.py" in src
    assert "job${V2B_RUN_ID}_${V2B_TAG}.json" in src
    assert "V2B-ASSEMBLY-DONE" in src


def test_exact_sealed_chain_and_identity_guards():
    src = source()
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "freeze_tokens_job19929877.json" in src
    assert "job19916781_2_physlib/pinned_mathlib_extraction.json" in src
    assert '--k4x-graph "$V2B_K4X_GRAPH"' in src
    assert '--k4x-external-extraction "$V2B_K4X_EXTERNAL"' in src
    for task, tag in enumerate(("mathlib4", "batteries", "physlib",
                                "sympy", "astropy")):
        assert f"job${{V2B_CANDIDATES_JOB}}_{task}_{tag}.json" in src
        assert f"job19921318_{task}_{tag}.json" in src
    assert "job${V2B_BOUNDARY_JOB}_${V2B_TASK}_${V2B_TAG}.json" in src
    assert 'if [[ "$V2B_TASK" -le 2 ]]' in src
    for path in ("job19930941_0_mathlib4.json",
                 "job19929883_1_batteries.json",
                 "job19929883_2_physlib.json",
                 "job19929883_3_sympy.json",
                 "job19929883_4_astropy.json"):
        assert path in src
    for sha in ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
                "76e1c118b0700b4ceafe99532e887d6431625e1a",
                "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
                "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
                "440fe546589c4e496235d712bc29783ecf5a5fec"):
        assert sha in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B ASSEMBLY JOB TESTS PASS")
