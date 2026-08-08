#!/usr/bin/env python3
"""Tests that candidate generation cannot escape the sealed V2-a cohort."""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2a import EVIDENCE_SOURCE_COMMIT, GATE_SCHEMA
from finalize_v2a_cohort import COHORT_SCHEMA, EXPECTED
from prepare_v2b_candidates import validate_structural_inputs
from v2b_common import V2BError


def _write(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _fixture(td):
    repo, language, repo_sha = "sympy", "python", "a" * 40
    run_dir = os.path.join(td, "run-sympy")
    os.mkdir(run_dir)
    extraction_path = os.path.join(run_dir, "extraction.json")
    extraction_sha = _write(extraction_path,
                            {"schema": "v2a_python_extract_v3"})
    input_hashes = {"completion_envelope": "1" * 64,
                    "extraction": extraction_sha,
                    "validation": "2" * 64,
                    "compile_audit": "3" * 64}
    gate_path = os.path.join(run_dir, "structural_gate.json")
    gate = dict(schema=GATE_SCHEMA, repo=repo, language=language,
                repo_sha=repo_sha, source_commit=EVIDENCE_SOURCE_COMMIT,
                run_dir=run_dir, gate_complete=True, failures=[],
                checks={"synthetic": {"passed": True}},
                input_hashes=input_hashes)
    gate_sha = _write(gate_path, gate)
    rows = []
    for name, expected_language in EXPECTED.items():
        if name == repo:
            rows.append(dict(repo=name, language=language, run_dir=run_dir,
                             gate_path=gate_path, gate_sha256=gate_sha,
                             input_hashes=input_hashes))
        else:
            rows.append(dict(repo=name, language=expected_language,
                             run_dir=os.path.join(td, "unused-" + name),
                             gate_path=os.path.join(td, "unused-" + name,
                                                    "gate.json"),
                             gate_sha256="4" * 64, input_hashes={}))
    cohort_path = os.path.join(td, "cohort.json")
    cohort = dict(schema=COHORT_SCHEMA,
                  evidence_source_commit=EVIDENCE_SOURCE_COMMIT,
                  gate_complete=True, failures=[], corpora=rows)
    _write(cohort_path, cohort)
    return dict(repo=repo, language=language, repo_sha=repo_sha,
                run_dir=run_dir, extraction_path=extraction_path,
                gate_path=gate_path, cohort_path=cohort_path,
                cohort=cohort)


def test_exact_structural_row_gate_and_extraction_bind():
    with tempfile.TemporaryDirectory() as td:
        f = _fixture(td)
        binding = validate_structural_inputs(
            f["cohort_path"], f["extraction_path"], f["repo"],
            f["language"], f["repo_sha"])
        assert binding["extraction_sha256"] == hashlib.sha256(
            open(f["extraction_path"], "rb").read()).hexdigest()
        assert binding["corpus_gate"]["run_dir"] == f["run_dir"]


def test_extraction_gate_or_cohort_drift_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        f = _fixture(td)
        with open(f["extraction_path"], "ab") as fh:
            fh.write(b"drift")
        try:
            validate_structural_inputs(
                f["cohort_path"], f["extraction_path"], f["repo"],
                f["language"], f["repo_sha"])
            raise AssertionError("accepted extraction drift")
        except V2BError:
            pass
    with tempfile.TemporaryDirectory() as td:
        f = _fixture(td)
        with open(f["gate_path"], "ab") as fh:
            fh.write(b" ")
        try:
            validate_structural_inputs(
                f["cohort_path"], f["extraction_path"], f["repo"],
                f["language"], f["repo_sha"])
            raise AssertionError("accepted gate drift")
        except V2BError:
            pass
    with tempfile.TemporaryDirectory() as td:
        f = _fixture(td)
        f["cohort"]["evidence_source_commit"] = "f" * 40
        _write(f["cohort_path"], f["cohort"])
        try:
            validate_structural_inputs(
                f["cohort_path"], f["extraction_path"], f["repo"],
                f["language"], f["repo_sha"])
            raise AssertionError("accepted mixed evidence commit")
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("BOUND V2B CANDIDATE TESTS PASS")
