#!/usr/bin/env python3
"""V2-a independent-report finalizer tests."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2a import (CORPUS_REVISIONS, EVIDENCE_SOURCE_COMMIT,
                          LEAN_ARTIFACT_REPORT_SHA,
                          PHYSLIB_MATHLIB_REVISION, PYTHON_BINARY_SHA,
                          finalize)


def _write(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _write_complete(td, fields):
    complete = os.path.join(td, "complete.tsv")
    with open(complete, "w", encoding="utf-8") as fh:
        fh.write("key\tvalue\n")
        for key, value in fields.items():
            fh.write(f"{key}\t{value}\n")


def _fixture(td, language="python", repo=None):
    lean = language == "lean"
    repo = repo or ("mathlib4" if lean else "sympy")
    schema = "v2a_lean_extract_v3" if lean else "v2a_python_extract_v3"
    ex_path = os.path.join(td, "extraction.json")
    val_path = os.path.join(td, "validation.json")
    comp_path = os.path.join(td, "boundary_compile_audit.json")
    source_commit = EVIDENCE_SOURCE_COMMIT
    repo_sha = CORPUS_REVISIONS.get(repo, "a" * 40)
    extraction = dict(schema=schema, repo=repo)
    if lean:
        pairs_path = os.path.join(td, "pairs.json")
        _write(pairs_path, dict(
            schema="v2a_ilean_pairs_v2", repo_git_sha=repo_sha,
            expected_repo_git_sha=repo_sha))
        extraction.update(
            pairs_manifest_sha256=_sha(pairs_path),
            n_foreign_declaration_infos=0,
            foreign_declaration_infos_by_module={})
    else:
        extraction["n_failed"] = 0
    _write(ex_path, extraction)
    targets = [dict(identity=(["M", f"M.t{i}"] if lean
                              else ["m", f"t{i}", i]),
                    roundtrip_ok=True) for i in range(2)]
    validation = dict(
        extraction_sha256=_sha(ex_path), targets=targets,
        summary=dict(schema=schema, repo=repo, n_selected=2,
                     n_failures=0, failures=[]))
    _write(val_path, validation)
    compile_audit = dict(
        extraction_schema=schema,
        python_sha256=PYTHON_BINARY_SHA,
        inputs=dict(extraction_sha256=_sha(ex_path),
                    validation_sha256=_sha(val_path)),
        summary=dict(n_selected=2, n_failed=0, failures=[],
                     standalone_compile="PASS",
                     closure_check="NOT-APPLICABLE-BEST-EFFORT-AST"),
        targets=[dict(identity=row["identity"], passed=True)
                 for row in targets])
    _write(comp_path, compile_audit)
    fields = dict(status="complete", repo=repo, source_commit=source_commit,
                  repo_sha=repo_sha, extraction_sha256=_sha(ex_path),
                  validation_sha256=_sha(val_path),
                  compile_audit_sha256=_sha(comp_path),
                  python_sha256=PYTHON_BINARY_SHA)
    if lean:
        closure_path = os.path.join(td, "raw_closure_audit.json")
        closure = dict(
            inputs=dict(extraction_sha256=_sha(ex_path),
                        validation_sha256=_sha(val_path),
                        pairs_sha256=_sha(pairs_path)),
            summary=dict(n_selected=2, n_failed=0, failures=[],
                         elaborator_closure_check="PASS",
                         foreign_declaration_info_partition_match=True,
                         n_foreign_declaration_infos=0),
            targets=[dict(identity=row["identity"], match=True)
                     for row in targets])
        _write(closure_path, closure)
        fields.update(pairs_sha256=_sha(pairs_path),
                      closure_audit_sha256=_sha(closure_path),
                      artifact_build_report_sha256=
                      LEAN_ARTIFACT_REPORT_SHA)
    _write_complete(td, fields)
    return fields


def _add_physlib_pin(td, fields, revision=PHYSLIB_MATHLIB_REVISION):
    pairs_path = os.path.join(td, "pinned_mathlib_pairs.json")
    extraction_path = os.path.join(td, "pinned_mathlib_extraction.json")
    _write(pairs_path, dict(
        schema="v2a_ilean_pairs_v2", repo_git_sha=revision,
        expected_repo_git_sha=revision))
    _write(extraction_path, dict(
        schema="v2a_lean_extract_v3", repo="physlib_pinned_mathlib",
        pairs_manifest_sha256=_sha(pairs_path)))
    fields.update(
        pinned_mathlib_repo_sha=revision,
        pinned_mathlib_pairs_sha256=_sha(pairs_path),
        pinned_mathlib_extraction_sha256=_sha(extraction_path))
    _write_complete(td, fields)


def test_python_gate_combines_independent_reports():
    with tempfile.TemporaryDirectory() as td:
        _fixture(td)
        report = finalize(td, "python", expected_n=2)
        assert report["gate_complete"] is True
        assert report["design_v2_s10"] == dict(
            extraction_validation="PASS", standalone_compile="PASS",
            elaborator_closure_check="NOT-APPLICABLE-BEST-EFFORT-AST")


def test_hash_drift_fails_gate():
    with tempfile.TemporaryDirectory() as td:
        _fixture(td)
        with open(os.path.join(td, "boundary_compile_audit.json"), "a") as fh:
            fh.write("\n")
        report = finalize(td, "python", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("compile_audit-hash")
                   for x in report["failures"])


def test_lean_gate_checks_raw_partition_and_physlib_pin():
    with tempfile.TemporaryDirectory() as td:
        _fixture(td, language="lean", repo="mathlib4")
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is True
        assert report["design_v2_s10"]["elaborator_closure_check"] == "PASS"
    with tempfile.TemporaryDirectory() as td:
        _fixture(td, language="lean", repo="physlib")
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("pinned-mathlib-")
                   for x in report["failures"])
    with tempfile.TemporaryDirectory() as td:
        fields = _fixture(td, language="lean", repo="physlib")
        _add_physlib_pin(td, fields)
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is True
        assert report["input_hashes"]["pinned_mathlib_pairs"] == \
            fields["pinned_mathlib_pairs_sha256"]
        assert report["input_hashes"]["pinned_mathlib_extraction"] == \
            fields["pinned_mathlib_extraction_sha256"]


def test_unknown_repo_tag_cannot_skip_physlib_pin_gate():
    with tempfile.TemporaryDirectory() as td:
        _fixture(td, language="lean", repo="PhysLib")
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("completion-repo-tag")
                   for x in report["failures"])


def test_nonphyslib_rejects_stray_pin_envelope():
    with tempfile.TemporaryDirectory() as td:
        _fixture(td, language="lean", repo="mathlib4")
        complete = os.path.join(td, "complete.tsv")
        with open(complete, "a", encoding="utf-8") as fh:
            fh.write("pinned_mathlib_repo_sha\t" + "d" * 40 + "\n")
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("pinned-mathlib-evidence-absent")
                   for x in report["failures"])


def test_physlib_pin_revision_requires_present_git_sha():
    with tempfile.TemporaryDirectory() as td:
        fields = _fixture(td, language="lean", repo="physlib")
        pairs_path = os.path.join(td, "pinned_mathlib_pairs.json")
        extraction_path = os.path.join(td, "pinned_mathlib_extraction.json")
        _write(pairs_path, dict(schema="v2a_ilean_pairs_v2"))
        _write(extraction_path, dict(
            schema="v2a_lean_extract_v3",
            repo="physlib_pinned_mathlib",
            pairs_manifest_sha256=_sha(pairs_path)))
        fields.update(
            pinned_mathlib_pairs_sha256=_sha(pairs_path),
            pinned_mathlib_extraction_sha256=_sha(extraction_path))
        _write_complete(td, fields)
        report = finalize(td, "lean", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("pinned-mathlib-revision")
                   for x in report["failures"])


def test_frozen_source_commit_and_corpus_revision_are_required():
    with tempfile.TemporaryDirectory() as td:
        fields = _fixture(td)
        fields["source_commit"] = "f" * 40
        _write_complete(td, fields)
        report = finalize(td, "python", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("source-commit")
                   for x in report["failures"])
    with tempfile.TemporaryDirectory() as td:
        fields = _fixture(td)
        fields["repo_sha"] = "f" * 40
        _write_complete(td, fields)
        report = finalize(td, "python", expected_n=2)
        assert report["gate_complete"] is False
        assert any(x.startswith("repo-sha")
                   for x in report["failures"])


def test_binding_constants_match_committed_locks():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lock = json.load(open(os.path.join(base, "corpora_lock.json"),
                          encoding="utf-8"))
    assert CORPUS_REVISIONS == {
        repo: lock["repos"][repo]["sha"] for repo in CORPUS_REVISIONS}
    freeze = open(os.path.join(base, "results_v2", "env",
                               "freeze-cluster.txt"),
                  encoding="utf-8").read().splitlines()
    assert f"python-binary=={PYTHON_BINARY_SHA}" in freeze
    proc = subprocess.run(
        ["git", "-C", base, "cat-file", "-e",
         f"{EVIDENCE_SOURCE_COMMIT}^{{commit}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2A FINALIZER TESTS PASS")
