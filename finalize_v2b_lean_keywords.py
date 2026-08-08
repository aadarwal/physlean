#!/usr/bin/env python3
"""Seal the language-wide Lean normalization vocabulary.

The output is the exact sorted union of identifier-shaped reserved tokens and
contextual parser dispatch keys from the three pinned Lean umbrella
environments.  Pseudo-key exclusions and per-token provenance are explicit.
There is no manual word-list step, and the write-once freeze is created before
A6 corpus hashes, audit packets, labels, target sampling, or scores.
"""
import argparse

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (LEAN_KEYWORD_FREEZE_SCHEMA, V2BError,
                        artifact_binding, sha256_json,
                        write_new_json)
from prepare_v2b_lean_tokens import (LEAN_ARTIFACT_REPORT_SHA256,
                                     LITERAL_KIND_SMOKE,
                                     PARSER_TOKENS_SCHEMA)
from v2b_neardup import (lean_identifier_spelling,
                         lean_keyword_provenance_hash)


EXPECTED = {
    "mathlib4": ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad", "Mathlib"),
    "batteries": ("76e1c118b0700b4ceafe99532e887d6431625e1a", "Batteries"),
    "physlib": ("e882411d1b6bcbdfdd336d4c509c6cc72e96842d", "Physlib"),
}
SMOKE_TOKENS = frozenset(("by", "def", "rfl", "simp", "omega"))


def _valid_identifier_token(token):
    return lean_identifier_spelling(token)


def build_freeze(table_paths, expected_source_commit=None,
                 expected_source_tree_hash=None):
    if not isinstance(table_paths, (list, tuple)) or len(table_paths) != 3:
        raise V2BError("Lean keyword freeze requires exactly three tables")
    rows = {}
    union = set()
    for path in table_paths:
        binding, table = artifact_binding(path, PARSER_TOKENS_SCHEMA)
        repo = table.get("repo")
        if repo not in EXPECTED or repo in rows:
            raise V2BError(f"unexpected/duplicate Lean token repo {repo!r}")
        expected_sha, expected_module = EXPECTED[repo]
        tokens = table.get("identifier_tokens")
        reserved = table.get("reserved_identifier_tokens")
        dispatch = table.get("dispatch_identifier_tokens")
        excluded = table.get("excluded_dispatch_keys")
        reserved_raw = table.get("reserved_raw")
        dispatch_raw = table.get("dispatch_raw")
        excluded_raw = table.get("excluded_dispatch_raw")
        artifact_report = table.get("lean_artifact_report")
        generator = table.get("generator")
        if table.get("corpus_git_sha") != expected_sha \
                or table.get("umbrella_module") != expected_module \
                or not isinstance(tokens, list) or not tokens \
                or tokens != sorted(tokens) or len(tokens) != len(set(tokens)) \
                or table.get("n_identifier_tokens") != len(tokens) \
                or not all(_valid_identifier_token(token) for token in tokens) \
                or not isinstance(reserved, list) or not reserved \
                or reserved != sorted(reserved) \
                or len(reserved) != len(set(reserved)) \
                or not all(_valid_identifier_token(token)
                           for token in reserved) \
                or not isinstance(dispatch, list) or not dispatch \
                or dispatch != sorted(dispatch) \
                or len(dispatch) != len(set(dispatch)) \
                or not all(_valid_identifier_token(token)
                           for token in dispatch) \
                or tokens != sorted(set(reserved) | set(dispatch)) \
                or table.get("n_reserved_identifier_tokens") != \
                len(reserved) \
                or table.get("n_dispatch_identifier_tokens") != \
                len(dispatch) \
                or not isinstance(excluded, list) or not excluded \
                or excluded != sorted(excluded) \
                or len(excluded) != len(set(excluded)) \
                or table.get("n_excluded_dispatch_keys") != len(excluded) \
                or not LITERAL_KIND_SMOKE <= set(excluded) \
                or set(dispatch) & set(excluded) \
                or not all(isinstance(raw, dict)
                           and isinstance(raw.get("sha256"), str)
                           and len(raw["sha256"]) == 64
                           for raw in (reserved_raw, dispatch_raw,
                                       excluded_raw)) \
                or not all(isinstance(table.get(field), int)
                           and not isinstance(table.get(field), bool)
                           and table[field] > 0
                           for field in ("n_reserved_tokens",
                                         "n_dispatch_tokens", "n_tokens")) \
                or not max(table["n_reserved_tokens"],
                           table["n_dispatch_tokens"]) <= table["n_tokens"] \
                <= table["n_reserved_tokens"] + table["n_dispatch_tokens"] \
                or not isinstance(artifact_report, dict) \
                or artifact_report.get("sha256") != \
                LEAN_ARTIFACT_REPORT_SHA256 \
                or artifact_report.get("final_status") != "complete" \
                or artifact_report.get("repo_sha") != expected_sha \
                or not isinstance(generator, dict) \
                or generator.get("program") != "prepare_v2b_lean_tokens.py":
            raise V2BError(f"malformed/binding-drifted token table for {repo}")
        if expected_source_commit is not None \
                and generator.get("source_commit") != expected_source_commit:
            raise V2BError(f"token table source commit drift for {repo}")
        if expected_source_tree_hash is not None \
                and generator.get("source_tree_hash") != \
                expected_source_tree_hash:
            raise V2BError(f"token table source tree drift for {repo}")
        union.update(tokens)
        rows[repo] = dict(binding, repo=repo,
                          corpus_git_sha=expected_sha,
                          umbrella_module=expected_module,
                          lean_version=table.get("lean_version"),
                          lake_version=table.get("lake_version"),
                          reserved_raw_sha256=reserved_raw["sha256"],
                          dispatch_raw_sha256=dispatch_raw["sha256"],
                          excluded_dispatch_raw_sha256=
                          excluded_raw["sha256"],
                          lean_artifact_report_sha256=
                          artifact_report["sha256"],
                          n_reserved_tokens=table["n_reserved_tokens"],
                          n_dispatch_tokens=table["n_dispatch_tokens"],
                          n_excluded_dispatch_keys=len(excluded),
                          n_tokens=table["n_tokens"],
                          n_reserved_identifier_tokens=len(reserved),
                          n_dispatch_identifier_tokens=len(dispatch),
                          n_identifier_tokens=len(tokens))
        rows[repo]["_reserved"] = reserved
        rows[repo]["_dispatch"] = dispatch
    if set(rows) != set(EXPECTED):
        raise V2BError("Lean token tables do not cover the exact corpus set")
    tokens = sorted(union)
    missing = sorted(SMOKE_TOKENS - set(tokens))
    if missing:
        raise V2BError(f"Lean parser-token union lacks smoke tokens: {missing}")
    token_provenance = []
    for token in tokens:
        sources = []
        for repo in sorted(rows):
            in_reserved = token in rows[repo]["_reserved"]
            in_dispatch = token in rows[repo]["_dispatch"]
            if in_reserved or in_dispatch:
                sources.append(dict(repo=repo,
                                    reserved_token_table=in_reserved,
                                    parser_dispatch=in_dispatch))
        token_provenance.append(dict(token=token, sources=sources))
    source_rows = []
    for repo in sorted(rows):
        row = dict(rows[repo])
        row.pop("_reserved")
        row.pop("_dispatch")
        source_rows.append(row)
    return dict(
        schema=LEAN_KEYWORD_FREEZE_SCHEMA,
        derivation=("exact identifier-shaped union of each pinned umbrella "
                    "environment's reserved token table and contextual "
                    "leading/trailing parser dispatch keys"),
        source_tables=source_rows,
        n_excluded_dispatch_keys_total=sum(
            row["n_excluded_dispatch_keys"] for row in source_rows),
        n_tokens=len(tokens),
        tokens_sha256=sha256_json(tokens),
        tokens=tokens,
        token_provenance_sha256=lean_keyword_provenance_hash(
            token_provenance),
        token_provenance=token_provenance)


def prepare(table_paths):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    freeze = build_freeze(table_paths, commit_start, tree_start)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during keyword freeze")
    freeze["generator"] = dict(source_commit=commit_start,
                               source_tree_hash=tree_start,
                               program="finalize_v2b_lean_keywords.py")
    return freeze


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="append", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    freeze = prepare(args.table)
    digest = write_new_json(args.out, freeze)
    print(f"[v2b-lean-keywords] {freeze['n_tokens']} tokens -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
