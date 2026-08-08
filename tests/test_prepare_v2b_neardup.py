#!/usr/bin/env python3
"""Production A6 wrapper binding tests on synthetic sources only."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prepare_v2b_neardup as wrapper
from v2b_common import V2BError, sha256_file


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    proc = subprocess.run(["git", "-C", repo, *args], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _fixture(td):
    root = os.path.join(td, "repo")
    os.mkdir(root)
    _git(root, "init", "-q")
    _git(root, "config", "commit.gpgsign", "false")
    source = "def f(x):\n    return sin(x)\n\ndef g(x):\n    return cos(x)\n"
    source_path = os.path.join(root, "m.py")
    open(source_path, "w").write(source)
    _git(root, "add", "m.py")
    _git(root, "commit", "-q", "-m", "fixture")
    head = _git(root, "rev-parse", "HEAD")
    g_start = source.index("def g")
    extraction = dict(
        schema="v2a_python_extract_v3", repo="r",
        files=[dict(
            module="m", source=source_path,
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            targets=[dict(identity=["m", "f", 0], start_byte=0,
                          end_byte=source.index("\ndef g")),
                     dict(identity=["m", "g", g_start], start_byte=g_start,
                          end_byte=len(source))])])
    extraction_path = os.path.join(td, "extraction.json")
    json.dump(extraction, open(extraction_path, "w"))
    structural = dict(
        cohort=dict(path="cohort", sha256="c" * 64, schema="cohort"),
        corpus_gate=dict(path="gate", sha256="g" * 64, schema="gate",
                         run_dir="run"),
        extraction_sha256=sha256_file(extraction_path),
        evidence_source_commit="e" * 40)
    return root, head, extraction_path, structural


def _patch(structural, environments=("env", "env")):
    originals = {name: getattr(wrapper, name) for name in (
        "validate_structural_inputs", "source_clean", "head_commit",
        "source_tree_hash", "_locked_environment")}
    envs = iter(environments)
    wrapper.validate_structural_inputs = lambda *args: structural
    wrapper.source_clean = lambda: True
    wrapper.head_commit = lambda: "source-commit"
    wrapper.source_tree_hash = lambda: "source-tree"
    wrapper._locked_environment = lambda: next(envs)
    return originals


def _restore(originals):
    for name, value in originals.items():
        setattr(wrapper, name, value)


def test_prepare_binds_revision_structure_environment_and_sources():
    with tempfile.TemporaryDirectory() as td:
        root, head, extraction_path, structural = _fixture(td)
        originals = _patch(structural)
        try:
            artifact = wrapper.prepare("cohort", extraction_path, root,
                                       "r", "python", head)
        finally:
            _restore(originals)
        assert artifact["schema"] == "v2b_neardup_v1"
        assert artifact["corpus_git_sha"] == head
        assert artifact["structural_evidence"] == structural
        assert artifact["n_source_files"] == 1
        assert artifact["generator"] == {
            "source_commit": "source-commit",
            "source_tree_hash": "source-tree",
            "environment_fingerprint": "env",
            "program": "prepare_v2b_neardup.py"}


def test_prepare_rejects_environment_drift_and_source_escape():
    with tempfile.TemporaryDirectory() as td:
        root, head, extraction_path, structural = _fixture(td)
        originals = _patch(structural, environments=("before", "after"))
        try:
            try:
                wrapper.prepare("cohort", extraction_path, root,
                                "r", "python", head)
                assert False, "environment drift accepted"
            except V2BError as err:
                assert "environment drifted" in str(err)
        finally:
            _restore(originals)

        extraction = json.load(open(extraction_path))
        outside = os.path.join(td, "outside.py")
        open(outside, "w").write("def x(): pass\n")
        extraction["files"][0]["source"] = outside
        escaped = os.path.join(td, "escaped.json")
        json.dump(extraction, open(escaped, "w"))
        try:
            wrapper._validate_source_roots(escaped, root)
            assert False, "source outside corpus root accepted"
        except V2BError as err:
            assert "outside corpus root" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("BOUND V2B NEARDUP TESTS PASS")
