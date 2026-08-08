#!/usr/bin/env python3
"""Synthetic binding tests for the Lean parser-token evidence wrapper."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prepare_v2b_lean_tokens as wrapper
from v2b_common import V2BError, sha256_bytes


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    proc = subprocess.run(["git", "-C", repo, *args], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _fixture(td):
    repo = os.path.join(td, "repo")
    os.mkdir(repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    source = os.path.join(repo, "Source.lean")
    open(source, "w").write("def x := 1\n")
    _git(repo, "add", "Source.lean")
    _git(repo, "commit", "-q", "-m", "fixture")
    head = _git(repo, "rev-parse", "HEAD")
    raw = os.path.join(td, "tokens.txt")
    tokens = sorted([f"keyword{i:03d}" for i in range(120)] + ["!", "->"])
    open(raw, "w").write("\n".join(tokens) + "\n")
    report = os.path.join(td, "lean-artifacts.tsv")
    report_blob = (f"status\tbuilding\n{repo}.unused\tx\n"
                   f"r.repo_sha\t{head}\nstatus\tcomplete\n").encode()
    open(report, "wb").write(report_blob)
    return repo, head, raw, report, sha256_bytes(report_blob)


def _patch_source(report_sha):
    originals = {name: getattr(wrapper, name) for name in
                 ("source_clean", "head_commit", "source_tree_hash",
                  "LEAN_ARTIFACT_REPORT_SHA256")}
    wrapper.source_clean = lambda: True
    wrapper.head_commit = lambda: "source-commit"
    wrapper.source_tree_hash = lambda: "source-tree"
    wrapper.LEAN_ARTIFACT_REPORT_SHA256 = report_sha
    return originals


def _restore(originals):
    for name, value in originals.items():
        setattr(wrapper, name, value)


def test_prepare_binds_parser_table_revision_and_versions():
    with tempfile.TemporaryDirectory() as td:
        repo, head, raw, report, report_sha = _fixture(td)
        originals = _patch_source(report_sha)
        try:
            artifact = wrapper.prepare(raw, repo, "r", head, "Umbrella",
                                       report, "Lean 4.test", "Lake test")
        finally:
            _restore(originals)
        assert artifact["schema"] == wrapper.PARSER_TOKENS_SCHEMA
        assert artifact["corpus_git_sha"] == head
        assert artifact["umbrella_module"] == "Umbrella"
        assert artifact["lean_artifact_report"]["sha256"] == report_sha
        assert artifact["n_tokens"] == 122
        assert artifact["n_identifier_tokens"] == 120
        assert artifact["identifier_tokens"][0] == "keyword000"
        assert artifact["generator"]["source_commit"] == "source-commit"


def test_token_dump_parser_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        for name, payload in (
                ("unsorted", b"b\na\n"),
                ("duplicate", b"a\na\n"),
                ("crlf", b"a\r\n"),
                ("no-final-lf", b"a")):
            path = os.path.join(td, name)
            open(path, "wb").write(payload)
            try:
                wrapper._read_tokens(path)
                assert False, name
            except V2BError:
                pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN TOKEN BINDING TESTS PASS")
