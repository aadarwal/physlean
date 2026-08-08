#!/usr/bin/env python3
"""Production, structurally bound V2-b candidate-metadata entry point."""
import argparse
import os

from finalize_v2a import EVIDENCE_SOURCE_COMMIT, GATE_SCHEMA
from finalize_v2a_cohort import COHORT_SCHEMA, EXPECTED
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, artifact_binding, load_json, sha256_file,
                        write_new_json)
from v2b_lean_boundaries import load_boundary_overlay
from v2b_metadata import build_candidate_table, corpus_git_identity


def validate_structural_inputs(cohort_path, extraction_path, repo, language,
                               expected_corpus_sha):
    """Bind one extraction to its exact row/gate in the sealed V2-a cohort."""
    cohort_binding, cohort = artifact_binding(cohort_path, COHORT_SCHEMA)
    if cohort.get("gate_complete") is not True or cohort.get("failures") != []:
        raise V2BError("V2-a structural cohort is not passing")
    if cohort.get("evidence_source_commit") != EVIDENCE_SOURCE_COMMIT:
        raise V2BError("V2-a cohort evidence-source commit drift")
    rows = cohort.get("corpora")
    if not isinstance(rows, list) or len(rows) != len(EXPECTED) \
            or any(not isinstance(row, dict) for row in rows) \
            or {row.get("repo") for row in rows} != set(EXPECTED):
        raise V2BError("V2-a cohort does not contain the exact corpus set")
    matches = [row for row in rows if row.get("repo") == repo]
    if len(matches) != 1:
        raise V2BError(f"V2-a cohort has {len(matches)} rows for {repo}")
    row = matches[0]
    if row.get("language") != language or EXPECTED.get(repo) != language:
        raise V2BError(f"V2-a cohort language drift for {repo}")
    run_dir = row.get("run_dir")
    if not isinstance(run_dir, str) or not os.path.isabs(run_dir):
        raise V2BError(f"V2-a cohort run directory invalid for {repo}")
    expected_extraction = os.path.realpath(os.path.join(run_dir,
                                                        "extraction.json"))
    if os.path.realpath(extraction_path) != expected_extraction:
        raise V2BError(f"extraction is not the cohort run's artifact for {repo}")
    extraction_sha = sha256_file(extraction_path)
    row_hashes = row.get("input_hashes")
    if not isinstance(row_hashes, dict) \
            or row_hashes.get("extraction") != extraction_sha:
        raise V2BError(f"extraction hash is not cohort-bound for {repo}")

    gate_path = row.get("gate_path")
    if not isinstance(gate_path, str) or not os.path.isabs(gate_path):
        raise V2BError(f"structural gate path invalid for {repo}")
    gate, gate_sha = load_json(gate_path, schema=GATE_SCHEMA)
    if gate_sha != row.get("gate_sha256"):
        raise V2BError(f"structural gate hash drift for {repo}")
    checks = gate.get("checks")
    if gate.get("gate_complete") is not True or gate.get("failures") != [] \
            or not isinstance(checks, dict) or not checks \
            or not all(isinstance(check, dict)
                       and check.get("passed") is True
                       for check in checks.values()):
        raise V2BError(f"structural corpus gate is not passing for {repo}")
    gate_run_dir = gate.get("run_dir")
    if not isinstance(gate_run_dir, str) \
            or gate.get("repo") != repo or gate.get("language") != language \
            or gate.get("repo_sha") != expected_corpus_sha \
            or gate.get("source_commit") != EVIDENCE_SOURCE_COMMIT \
            or os.path.realpath(gate_run_dir) != \
            os.path.realpath(run_dir) \
            or gate.get("input_hashes") != row_hashes:
        raise V2BError(f"structural gate/cohort identity mismatch for {repo}")
    return dict(cohort=cohort_binding,
                corpus_gate=dict(path=os.path.abspath(gate_path),
                                 sha256=gate_sha, schema=GATE_SCHEMA,
                                 run_dir=run_dir),
                extraction_sha256=extraction_sha,
                evidence_source_commit=EVIDENCE_SOURCE_COMMIT)


def prepare(cohort_path, extraction_path, corpus_root, repo, language,
            expected_corpus_sha, workers, lean_boundaries_path=None):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    source_commit_start = head_commit()
    source_hash_start = source_tree_hash()
    structural = validate_structural_inputs(
        cohort_path, extraction_path, repo, language, expected_corpus_sha)
    table = build_candidate_table(extraction_path, corpus_root, repo,
                                  expected_corpus_sha=expected_corpus_sha,
                                  workers=workers,
                                  lean_boundaries_path=
                                  lean_boundaries_path)
    if table.get("language") != language:
        raise V2BError(f"candidate language {table.get('language')} != "
                       f"expected {language}")
    boundary_binding = table.get("lean_boundaries")
    if language == "lean":
        if not isinstance(boundary_binding, dict):
            raise V2BError("Lean candidate table lacks boundary binding")
        structural["lean_boundaries"] = boundary_binding
    elif lean_boundaries_path is not None or boundary_binding is not None:
        raise V2BError("Python candidate build received a Lean boundary "
                       "artifact")
    else:
        structural["lean_boundaries"] = None
    # Recheck the long-running inputs before publication: a concurrent git
    # update or evidence edit cannot be hidden behind start-time hashes.
    corpus_git_identity(corpus_root, expected_corpus_sha)
    structural_end = validate_structural_inputs(
        cohort_path, extraction_path, repo, language, expected_corpus_sha)
    structural_end["lean_boundaries"] = structural["lean_boundaries"]
    if structural_end != structural:
        raise V2BError("structural inputs drifted during candidate build")
    if language == "lean":
        boundary_end, _artifact, _index = load_boundary_overlay(
            lean_boundaries_path, extraction_path, expected_repo=repo)
        if boundary_end != boundary_binding:
            raise V2BError("Lean boundary artifact drifted during candidate "
                           "build")
    if not source_clean() or head_commit() != source_commit_start \
            or source_tree_hash() != source_hash_start:
        raise V2BError("measurement source drifted during candidate build")
    table["structural_evidence"] = structural
    table["generator"] = dict(source_commit=source_commit_start,
                              source_tree_hash=source_hash_start,
                              program="prepare_v2b_candidates.py")
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structural-cohort", required=True)
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--language", required=True, choices=("lean", "python"))
    ap.add_argument("--expected-corpus-sha", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--lean-boundaries")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = prepare(args.structural_cohort, args.extraction,
                    args.corpus_root, args.repo, args.language,
                    args.expected_corpus_sha, args.workers,
                    args.lean_boundaries)
    digest = write_new_json(args.out, table)
    print(f"[v2b-candidates] {args.repo}: {table['n_candidates']} -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
