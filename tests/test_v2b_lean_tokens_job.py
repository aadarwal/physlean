#!/usr/bin/env python3
"""Static contract for the exact-three parser-token array."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "v2b_lean_tokens.sbatch")


def test_exact_three_source_and_corpus_bound_job():
    src = open(SCRIPT, encoding="utf-8").read()
    assert "--array=0-2" in src and "#SBATCH --mem=32G" in src
    assert "#SBATCH --gres" not in src
    assert "prepare_v2b_lean_tokens.py" in src
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "--untracked-files=all" in src
    assert "V2B_RESERVED_OUT" in src and "V2B_DISPATCH_OUT" in src
    assert "V2B_EXCLUDED_OUT" in src
    assert "[[ ! -e \"$V2B_RESERVED_RAW\"" in src
    assert "lean_artifacts_job19911017.tsv" in src
    assert "ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6" in src
    assert "V2B_ARTIFACT_REPO_SHA" in src
    assert 'END {print value}' in src
    for module, dump in (("Mathlib", "MathlibTokens.lean"),
                         ("Batteries", "BatteriesTokens.lean"),
                         ("Physlib", "PhyslibTokens.lean")):
        assert f'V2B_MODULE="{module}"' in src
        assert dump in src
    for sha in ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
                "76e1c118b0700b4ceafe99532e887d6431625e1a",
                "e882411d1b6bcbdfdd336d4c509c6cc72e96842d"):
        assert sha in src


def test_dump_sources_import_exact_umbrellas_and_write_token_table():
    for file_name, module in (("MathlibTokens.lean", "Mathlib"),
                              ("BatteriesTokens.lean", "Batteries"),
                              ("PhyslibTokens.lean", "Physlib")):
        src = open(os.path.join(ROOT, "lean_tokens", file_name),
                   encoding="utf-8").read()
        assert src.startswith(f"import {module}\n")
        assert "Parser.getTokenTable" in src
        assert "Parser.parserExtension.getState" in src
        assert "leadingTable" in src and "trailingTable" in src
        assert "Lean.identKind" in src and "Lean.nameLitKind" in src
        assert "V2B_RESERVED_OUT" in src and "V2B_DISPATCH_OUT" in src
        assert "V2B_EXCLUDED_OUT" in src
        assert "qsort" in src and "IO.FS.writeFile" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN TOKEN JOB TESTS PASS")
