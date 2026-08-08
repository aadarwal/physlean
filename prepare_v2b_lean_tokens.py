#!/usr/bin/env python3
"""Bind one pinned Lean environment's parser vocabulary evidence.

The Lean dump is produced only after the corpus umbrella import.  Reserved
token-table values and contextual category-dispatch keys are bound separately;
the latter are necessary because tactic names deliberately remain ordinary
identifiers in Lean.  Internal literal-kind/non-simple dispatch keys are
recorded but excluded.  The language freeze is the exact identifier-shaped
union of the three sealed artifacts, without a hand-curated vocabulary.
"""
import argparse

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import V2BError, sha256_bytes, write_new_json
from v2b_metadata import corpus_git_identity
from v2b_neardup import lean_identifier_spelling


PARSER_TOKENS_SCHEMA = "v2b_lean_parser_tokens_v2"
LITERAL_KIND_SMOKE = frozenset(
    ("choice", "ident", "str", "num", "scientific", "char", "name"))
LEAN_ARTIFACT_REPORT_SHA256 = \
    "ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6"


def _artifact_report_binding(path, repo, expected_corpus_sha):
    try:
        blob = open(path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read Lean artifact report {path}: {err}") \
            from err
    digest = sha256_bytes(blob)
    if digest != LEAN_ARTIFACT_REPORT_SHA256:
        raise V2BError("Lean artifact report hash drift")
    try:
        lines = blob.decode("utf-8").splitlines()
        pairs = [line.split("\t") for line in lines]
    except UnicodeDecodeError as err:
        raise V2BError(f"Lean artifact report is not UTF-8: {err}") from err
    if not pairs or any(len(pair) != 2 or not pair[0] for pair in pairs):
        raise V2BError("Lean artifact report is malformed")
    statuses = [value for key, value in pairs if key == "status"]
    repo_shas = [value for key, value in pairs
                 if key == f"{repo}.repo_sha"]
    if not statuses or statuses[-1] != "complete" \
            or repo_shas != [expected_corpus_sha]:
        raise V2BError("Lean artifact report lacks matching complete build")
    return dict(path=path, sha256=digest, final_status=statuses[-1],
                repo_sha=repo_shas[0])


def _read_tokens(raw_path, section, minimum):
    try:
        blob = open(raw_path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read {section} parser dump {raw_path}: {err}") \
            from err
    if not blob or not blob.endswith(b"\n") or b"\r" in blob \
            or b"\x00" in blob:
        raise V2BError(f"{section} parser dump must be nonempty LF-only text")
    try:
        tokens = blob[:-1].decode("utf-8").split("\n")
    except UnicodeDecodeError as err:
        raise V2BError(f"{section} parser dump is not UTF-8: {err}") from err
    if not tokens or any(not token for token in tokens) \
            or tokens != sorted(tokens) or len(tokens) != len(set(tokens)):
        raise V2BError(
            f"{section} parser dump is empty, duplicate, or unsorted")
    if len(tokens) < minimum:
        raise V2BError(f"{section} parser dump is implausibly small")
    return blob, tokens


def prepare(reserved_raw_path, dispatch_raw_path, excluded_raw_path,
            corpus_root, repo, expected_corpus_sha,
            umbrella_module, artifact_report_path, lean_version,
            lake_version):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    corpus_git_identity(corpus_root, expected_corpus_sha)
    report = _artifact_report_binding(
        artifact_report_path, repo, expected_corpus_sha)
    reserved_blob, reserved_tokens = _read_tokens(
        reserved_raw_path, "reserved-token", 100)
    dispatch_blob, dispatch_tokens = _read_tokens(
        dispatch_raw_path, "dispatch-key", 20)
    excluded_blob, excluded_keys = _read_tokens(
        excluded_raw_path, "excluded-dispatch-key", 1)
    if not LITERAL_KIND_SMOKE <= set(excluded_keys) \
            or set(dispatch_tokens) & set(excluded_keys):
        raise V2BError("dispatch pseudo-key exclusion evidence is malformed")
    reserved_identifiers = [token for token in reserved_tokens
                            if lean_identifier_spelling(token)]
    dispatch_identifiers = [token for token in dispatch_tokens
                            if lean_identifier_spelling(token)]
    identifier_tokens = sorted(set(reserved_identifiers) |
                               set(dispatch_identifiers))
    if len(reserved_identifiers) < 20 or len(dispatch_identifiers) < 3:
        raise V2BError("parser identifier evidence is implausibly small")
    corpus_git_identity(corpus_root, expected_corpus_sha)
    if _artifact_report_binding(artifact_report_path, repo,
                                expected_corpus_sha) != report:
        raise V2BError("Lean artifact report drifted during token bind")
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during parser-token bind")
    if not isinstance(lean_version, str) or not lean_version.strip() \
            or not isinstance(lake_version, str) or not lake_version.strip():
        raise V2BError("Lean/Lake version evidence is required")
    if not isinstance(umbrella_module, str) or not umbrella_module:
        raise V2BError("umbrella module evidence is required")
    return dict(
        schema=PARSER_TOKENS_SCHEMA,
        repo=repo,
        corpus_git_sha=expected_corpus_sha,
        umbrella_module=umbrella_module,
        lean_artifact_report=report,
        reserved_raw=dict(path=reserved_raw_path,
                          sha256=sha256_bytes(reserved_blob),
                          n_bytes=len(reserved_blob)),
        dispatch_raw=dict(path=dispatch_raw_path,
                          sha256=sha256_bytes(dispatch_blob),
                          n_bytes=len(dispatch_blob)),
        excluded_dispatch_raw=dict(path=excluded_raw_path,
                                   sha256=sha256_bytes(excluded_blob),
                                   n_bytes=len(excluded_blob)),
        lean_version=lean_version.strip(),
        lake_version=lake_version.strip(),
        n_reserved_tokens=len(reserved_tokens),
        n_dispatch_tokens=len(dispatch_tokens),
        n_excluded_dispatch_keys=len(excluded_keys),
        n_tokens=len(set(reserved_tokens) | set(dispatch_tokens)),
        n_reserved_identifier_tokens=len(reserved_identifiers),
        n_dispatch_identifier_tokens=len(dispatch_identifiers),
        n_identifier_tokens=len(identifier_tokens),
        reserved_identifier_tokens=reserved_identifiers,
        dispatch_identifier_tokens=dispatch_identifiers,
        excluded_dispatch_keys=excluded_keys,
        identifier_tokens=identifier_tokens,
        derivation=("identifier-shaped union of Lean.Parser.getTokenTable "
                    "values and simple leading/trailing parser-category "
                    "dispatch keys after the corpus umbrella import; exact "
                    "lexer isIdFirst/isIdRest predicates; literal-kind and "
                    "non-simple dispatch keys recorded and excluded"),
        generator=dict(source_commit=commit_start,
                       source_tree_hash=tree_start,
                       program="prepare_v2b_lean_tokens.py"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reserved-raw", required=True)
    ap.add_argument("--dispatch-raw", required=True)
    ap.add_argument("--excluded-dispatch-raw", required=True)
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--expected-corpus-sha", required=True)
    ap.add_argument("--umbrella-module", required=True)
    ap.add_argument("--artifact-report", required=True)
    ap.add_argument("--lean-version", required=True)
    ap.add_argument("--lake-version", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact = prepare(args.reserved_raw, args.dispatch_raw,
                       args.excluded_dispatch_raw, args.corpus_root, args.repo,
                       args.expected_corpus_sha, args.umbrella_module,
                       args.artifact_report, args.lean_version,
                       args.lake_version)
    digest = write_new_json(args.out, artifact)
    print(f"[v2b-lean-tokens] {args.repo}: "
          f"{artifact['n_reserved_tokens']} reserved + "
          f"{artifact['n_dispatch_tokens']} dispatch, "
          f"{artifact['n_identifier_tokens']} identifier-shaped -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
