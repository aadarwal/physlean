#!/usr/bin/env python3
"""Regression tests for deterministic source↔`.ilean` pairing."""
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pair_ilean import PairError, discover_pairs, write_new_json


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _ilean(path, module, version=5):
    _write(path, json.dumps(dict(
        version=version, module=module, directImports=[],
        references={}, decls={})))


def test_exact_and_srcdir_suffix_pairing_with_hashes():
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        arts = os.path.join(repo, ".lake", "build", "lib", "lean")
        _write(os.path.join(repo, "Exact", "A.lean"), "def a := 1\n")
        _write(os.path.join(repo, "src", "Pkg", "B.lean"),
               "def b := 2\n")
        _write(os.path.join(repo, "Unbuilt.lean"), "def u := 3\n")
        _ilean(os.path.join(arts, "Exact", "A.ilean"), "Exact.A")
        _ilean(os.path.join(arts, "Pkg", "B.ilean"), "Pkg.B")
        # A generated/unmatched artifact remains visible, not guessed.
        _ilean(os.path.join(arts, "Generated.ilean"), "Generated")
        got = discover_pairs(repo, arts)
        assert os.path.isabs(got["repo_root"])
        assert os.path.isabs(got["artifact_root"])
        assert got["repo_git_sha"] is None
        assert got["n_pairs"] == 2
        assert [p["module"] for p in got["pairs"]] == ["Exact.A", "Pkg.B"]
        assert [p["match_kind"] for p in got["pairs"]] == \
            ["exact", "srcdir_suffix"]
        assert all(len(p["source_sha256"]) == 64 for p in got["pairs"])
        assert all(len(p["ilean_sha256"]) == 64 for p in got["pairs"])
        assert got["unmatched_sources"] == ["Unbuilt.lean"]
        assert got["unmatched_artifacts"][0]["module"] == "Generated"


def test_ambiguous_suffix_and_duplicate_modules_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        arts = os.path.join(repo, ".lake", "build", "lib", "lean")
        _write(os.path.join(repo, "one", "Pkg", "A.lean"), "def a := 1\n")
        _write(os.path.join(repo, "two", "Pkg", "A.lean"), "def a := 2\n")
        _ilean(os.path.join(arts, "Pkg", "A.ilean"), "Pkg.A")
        try:
            discover_pairs(repo, arts)
            assert False, "ambiguous module source was accepted"
        except PairError as err:
            assert "ambiguous" in str(err)

    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        arts = os.path.join(td, "arts")
        _write(os.path.join(repo, "Pkg", "A.lean"), "def a := 1\n")
        _ilean(os.path.join(arts, "a.ilean"), "Pkg.A")
        _ilean(os.path.join(arts, "nested", "b.ilean"), "Pkg.A")
        try:
            discover_pairs(repo, arts)
            assert False, "duplicate artifact module was accepted"
        except PairError as err:
            assert "duplicate" in str(err)


def test_schema_drift_and_overwrite_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        arts = os.path.join(td, "arts")
        _write(os.path.join(repo, "M.lean"), "def m := 1\n")
        _ilean(os.path.join(arts, "M.ilean"), "M", version=4)
        try:
            discover_pairs(repo, arts)
            assert False, "wrong .ilean version was accepted"
        except PairError as err:
            assert "version" in str(err)

        out = os.path.join(td, "pairs.json")
        write_new_json(out, {"first": True})
        try:
            write_new_json(out, {"second": True})
            assert False, "evidence manifest was overwritten"
        except PairError as err:
            assert "overwrite" in str(err)
        assert json.load(open(out)) == {"first": True}


def test_expected_repo_revision_is_hard_gate():
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        arts = os.path.join(td, "arts")
        _write(os.path.join(repo, "M.lean"), "def m := 1\n")
        _ilean(os.path.join(arts, "M.ilean"), "M")
        with patch("pair_ilean._repo_sha", return_value="a" * 40):
            got = discover_pairs(repo, arts, "A" * 40)
            assert got["repo_git_sha"] == "a" * 40
            assert got["expected_repo_git_sha"] == "a" * 40
            try:
                discover_pairs(repo, arts, "b" * 40)
                assert False, "wrong repository revision accepted"
            except PairError as err:
                assert "revision mismatch" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("ILEAN-PAIR TESTS PASS")
