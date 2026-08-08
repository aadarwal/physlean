#!/usr/bin/env python3
"""Synthetic exact-union tests for the Lean parser-token freeze."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2b_lean_keywords import EXPECTED, SMOKE_TOKENS, build_freeze
from prepare_v2b_lean_tokens import PARSER_TOKENS_SCHEMA
from prepare_v2b_lean_tokens import LEAN_ARTIFACT_REPORT_SHA256
from v2b_common import V2BError, sha256_json
from v2b_neardup import load_lean_keyword_freeze


def _tables(td, extra=None):
    extra = extra or {}
    paths = []
    base = set(SMOKE_TOKENS) | {"theorem", "example", "Type", "Prop"}
    for repo, (sha, module) in EXPECTED.items():
        tokens = sorted(base | set(extra.get(repo, ())))
        row = dict(
            schema=PARSER_TOKENS_SCHEMA,
            repo=repo,
            corpus_git_sha=sha,
            umbrella_module=module,
            lean_artifact_report=dict(
                path="lean-artifacts.tsv",
                sha256=LEAN_ARTIFACT_REPORT_SHA256,
                final_status="complete", repo_sha=sha),
            raw=dict(path=f"{repo}.txt", sha256="a" * 64, n_bytes=1000),
            lean_version="Lean test",
            lake_version="Lake test",
            n_tokens=len(tokens) + 50,
            n_identifier_tokens=len(tokens),
            identifier_tokens=tokens,
            derivation="test",
            generator=dict(source_commit="c" * 40,
                           source_tree_hash="t" * 64,
                           program="prepare_v2b_lean_tokens.py"))
        path = os.path.join(td, repo + ".json")
        json.dump(row, open(path, "w"), sort_keys=True)
        paths.append(path)
    return paths


def test_exact_union_is_deterministic_and_loadable():
    with tempfile.TemporaryDirectory() as td:
        paths = _tables(td, {"mathlib4": ["aesop"],
                             "physlib": ["fun_prop"]})
        freeze = build_freeze(list(reversed(paths)), "c" * 40, "t" * 64)
        assert freeze["tokens"] == sorted(set(SMOKE_TOKENS) | {
            "theorem", "example", "Type", "Prop", "aesop", "fun_prop"})
        assert freeze["tokens_sha256"] == sha256_json(freeze["tokens"])
        assert [row["repo"] for row in freeze["source_tables"]] == \
            ["batteries", "mathlib4", "physlib"]
        out = os.path.join(td, "freeze.json")
        json.dump(freeze, open(out, "w"), sort_keys=True)
        tokens, binding = load_lean_keyword_freeze(out)
        assert tokens == frozenset(freeze["tokens"])
        assert binding["tokens_sha256"] == freeze["tokens_sha256"]


def test_freeze_rejects_missing_duplicate_and_source_drift():
    with tempfile.TemporaryDirectory() as td:
        paths = _tables(td)
        bad_sets = (paths[:2], [paths[0], paths[0], paths[2]])
        for bad in bad_sets:
            try:
                build_freeze(bad, "c" * 40, "t" * 64)
                assert False, bad
            except V2BError:
                pass
        table = json.load(open(paths[0]))
        table["identifier_tokens"] = list(reversed(table["identifier_tokens"]))
        json.dump(table, open(paths[0], "w"), sort_keys=True)
        try:
            build_freeze(paths, "c" * 40, "t" * 64)
            assert False, "unsorted token table accepted"
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN KEYWORD FREEZE TESTS PASS")
