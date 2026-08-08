#!/usr/bin/env python3
"""Tests for the exact-five-corpus V2-a cohort combiner."""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2a import CORPUS_REVISIONS, EVIDENCE_SOURCE_COMMIT, GATE_SCHEMA
from finalize_v2a_cohort import EXPECTED, finalize


INPUT_FILES = {
    "completion_envelope": "complete.tsv",
    "extraction": "extraction.json",
    "validation": "validation.json",
    "compile_audit": "boundary_compile_audit.json",
    "pairs": "pairs.json",
    "closure_audit": "raw_closure_audit.json",
    "pinned_mathlib_pairs": "pinned_mathlib_pairs.json",
    "pinned_mathlib_extraction": "pinned_mathlib_extraction.json",
}


def _write_gate(td, repo, gate_complete=True):
    language = EXPECTED[repo]
    required = {"completion_envelope", "extraction", "validation",
                "compile_audit"}
    if language == "lean":
        required.update(("pairs", "closure_audit"))
    if repo == "physlib":
        required.update(("pinned_mathlib_pairs",
                         "pinned_mathlib_extraction"))
    run_dir = os.path.join(td, "run-" + repo)
    os.mkdir(run_dir)
    input_hashes = {}
    for key in sorted(required):
        path = os.path.join(run_dir, INPUT_FILES[key])
        with open(path, "wb") as fh:
            fh.write(f"{repo}:{key}\n".encode())
        input_hashes[key] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    report = dict(
        schema=GATE_SCHEMA, repo=repo, language=language,
        source_commit=EVIDENCE_SOURCE_COMMIT,
        repo_sha=CORPUS_REVISIONS[repo], expected_n=20,
        run_dir=run_dir,
        gate_complete=gate_complete,
        failures=[] if gate_complete else ["synthetic-failure"],
        checks={"synthetic": {"passed": gate_complete, "detail": None}},
        design_v2_s10=dict(
            extraction_validation="PASS", standalone_compile="PASS",
            elaborator_closure_check=(
                "PASS" if language == "lean" else
                "NOT-APPLICABLE-BEST-EFFORT-AST")),
        input_hashes=input_hashes)
    path = os.path.join(td, repo + "-gate.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    return path


def test_exact_five_passing_gates_complete_cohort():
    with tempfile.TemporaryDirectory() as td:
        paths = [_write_gate(td, repo) for repo in EXPECTED]
        report = finalize(paths)
        assert report["gate_complete"] is True
        assert report["failures"] == []
        assert {row["repo"] for row in report["corpora"]} == set(EXPECTED)
        for row in report["corpora"]:
            assert len(row["gate_sha256"]) == 64
            assert row["gate_sha256"] == hashlib.sha256(
                open(row["gate_path"], "rb").read()).hexdigest()


def test_missing_or_failed_corpus_fails_cohort():
    with tempfile.TemporaryDirectory() as td:
        paths = [_write_gate(td, repo) for repo in EXPECTED
                 if repo != "astropy"]
        report = finalize(paths)
        assert report["gate_complete"] is False
        assert any(x.startswith("exact-corpus-set")
                   for x in report["failures"])
    with tempfile.TemporaryDirectory() as td:
        paths = [_write_gate(td, repo, gate_complete=(repo != "batteries"))
                 for repo in EXPECTED]
        report = finalize(paths)
        assert report["gate_complete"] is False
        assert any(x.startswith("batteries:gate-complete")
                   for x in report["failures"])


def test_duplicate_corpus_does_not_substitute_for_missing_one():
    with tempfile.TemporaryDirectory() as td:
        paths = [_write_gate(td, repo) for repo in EXPECTED
                 if repo != "astropy"]
        paths.append(paths[0])
        report = finalize(paths)
        assert report["gate_complete"] is False
        assert any(x.startswith("mathlib4:unique-repo")
                   for x in report["failures"])
        assert any(x.startswith("exact-corpus-set")
                   for x in report["failures"])


def test_transitive_evidence_drift_fails_cohort():
    with tempfile.TemporaryDirectory() as td:
        paths = [_write_gate(td, repo) for repo in EXPECTED]
        with open(os.path.join(td, "run-sympy", "extraction.json"),
                  "ab") as fh:
            fh.write(b"drift\n")
        report = finalize(paths)
        assert report["gate_complete"] is False
        assert any(x.startswith("sympy:transitive-extraction-rehash")
                   for x in report["failures"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2A COHORT FINALIZER TESTS PASS")
