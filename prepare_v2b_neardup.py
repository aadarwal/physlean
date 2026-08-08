#!/usr/bin/env python3
"""Production, structurally and environment-bound V2-b A6 entry point."""
import argparse
import os

from prepare_v2b_candidates import validate_structural_inputs
from provenance import (env_fingerprint, env_matches_freeze,
                        env_matches_lock, head_commit, source_clean,
                        source_tree_hash)
from v2b_common import (V2BError, load_json, relative_source_path,
                        write_new_json)
from v2b_metadata import corpus_git_identity
from v2b_neardup import (build_neardup_artifact,
                         load_lean_keyword_freeze)


def _locked_environment():
    lock_ok, lock_problems = env_matches_lock()
    if not lock_ok:
        raise V2BError("A6 environment differs from wheel lock: "
                       + "; ".join(lock_problems[:5]))
    freeze_ok, freeze_detail = env_matches_freeze()
    if not freeze_ok:
        raise V2BError(f"A6 environment differs from freeze: {freeze_detail}")
    return env_fingerprint()


def _validate_source_roots(extraction_path, corpus_root):
    extraction, _ = load_json(extraction_path)
    files = extraction.get("files")
    if not isinstance(files, list) or not files:
        raise V2BError("A6 extraction has no files")
    seen = set()
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise V2BError(f"A6 extraction file[{index}] is malformed")
        source = row.get("source")
        if not isinstance(source, str) or not source:
            raise V2BError(f"A6 extraction file[{index}] lacks source path")
        rel = relative_source_path(corpus_root, source)
        if rel in seen:
            raise V2BError(f"A6 extraction repeats source path {rel}")
        seen.add(rel)
    return len(seen)


def prepare(cohort_path, extraction_path, corpus_root, repo, language,
            expected_corpus_sha, lean_keyword_freeze=None):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    source_commit_start = head_commit()
    source_hash_start = source_tree_hash()
    environment_start = _locked_environment()
    structural = validate_structural_inputs(
        cohort_path, extraction_path, repo, language, expected_corpus_sha)
    n_source_files = _validate_source_roots(extraction_path, corpus_root)
    corpus_git_identity(corpus_root, expected_corpus_sha)
    lean_keywords = keyword_evidence = None
    if language == "lean":
        if not lean_keyword_freeze:
            raise V2BError("Lean A6 requires the sealed parser-token freeze")
        lean_keywords, keyword_evidence = load_lean_keyword_freeze(
            lean_keyword_freeze)
    elif lean_keyword_freeze:
        raise V2BError("Python A6 must not receive a Lean keyword freeze")
    artifact = build_neardup_artifact(
        extraction_path, repo, lean_keywords, keyword_evidence)
    if artifact.get("language") != language:
        raise V2BError(f"A6 language {artifact.get('language')} != {language}")
    if artifact.get("extraction", {}).get("sha256") != \
            structural["extraction_sha256"]:
        raise V2BError("A6 extraction binding differs from structural cohort")

    corpus_git_identity(corpus_root, expected_corpus_sha)
    structural_end = validate_structural_inputs(
        cohort_path, extraction_path, repo, language, expected_corpus_sha)
    if language == "lean":
        end_keywords, end_evidence = load_lean_keyword_freeze(
            lean_keyword_freeze)
        if end_keywords != lean_keywords or end_evidence != keyword_evidence:
            raise V2BError("Lean keyword freeze drifted during A6 generation")
    if structural_end != structural \
            or _validate_source_roots(extraction_path, corpus_root) != \
            n_source_files:
        raise V2BError("A6 structural inputs drifted during generation")
    if not source_clean() or head_commit() != source_commit_start \
            or source_tree_hash() != source_hash_start:
        raise V2BError("measurement source drifted during A6 generation")
    if _locked_environment() != environment_start:
        raise V2BError("A6 environment drifted during generation")
    artifact["corpus_git_sha"] = expected_corpus_sha
    artifact["n_source_files"] = n_source_files
    artifact["structural_evidence"] = structural
    artifact["generator"] = dict(
        source_commit=source_commit_start,
        source_tree_hash=source_hash_start,
        environment_fingerprint=environment_start,
        program="prepare_v2b_neardup.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structural-cohort", required=True)
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--language", required=True, choices=("lean", "python"))
    ap.add_argument("--expected-corpus-sha", required=True)
    ap.add_argument("--lean-keyword-freeze")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact = prepare(args.structural_cohort, args.extraction,
                       args.corpus_root, args.repo, args.language,
                       args.expected_corpus_sha, args.lean_keyword_freeze)
    digest = write_new_json(args.out, artifact)
    print(f"[v2b-neardup] {args.repo}: {artifact['n_units']} units, "
          f"{len(artifact['jaccard_pairs'])} pairs, "
          f"{len(artifact['collision_groups'])} collision groups -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
