#!/usr/bin/env python3
"""Combine the five corpus-level V2-a gates into one cohort boundary."""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

from finalize_v2a import (CORPUS_REVISIONS, EVIDENCE_SOURCE_COMMIT,
                          GATE_SCHEMA)


COHORT_SCHEMA = "v2a_structural_cohort_v1"
EXPECTED = {
    "mathlib4": "lean",
    "batteries": "lean",
    "physlib": "lean",
    "sympy": "python",
    "astropy": "python",
}
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
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class CohortError(RuntimeError):
    """A corpus-gate report is unreadable or the output already exists."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise CohortError(f"cannot read corpus gate {path}: {err}") from err
    if not isinstance(value, dict):
        raise CohortError(f"corpus gate root is not an object: {path}")
    return value


def finalize(gate_paths):
    failures = []
    checks = {}

    def check(name, condition, detail=None):
        passed = bool(condition)
        checks[name] = dict(passed=passed, detail=detail)
        if not passed:
            failures.append(name if detail is None else f"{name}:{detail}")

    reports = {}
    rows = []
    for i, raw_path in enumerate(gate_paths):
        path = os.path.abspath(raw_path)
        report = _load(path)
        repo = report.get("repo")
        label = repo if isinstance(repo, str) and repo else f"input-{i}"
        check(f"{label}:known-repo", repo in EXPECTED, repo)
        check(f"{label}:unique-repo", repo not in reports, repo)
        if repo in reports:
            continue
        reports[repo] = report
        language = report.get("language")
        check(f"{label}:schema", report.get("schema") == GATE_SCHEMA,
              report.get("schema"))
        check(f"{label}:language", language == EXPECTED.get(repo), language)
        check(f"{label}:gate-complete", report.get("gate_complete") is True)
        check(f"{label}:no-failures", report.get("failures") == [],
              report.get("failures"))
        report_checks = report.get("checks")
        check(f"{label}:all-checks-pass",
              isinstance(report_checks, dict) and bool(report_checks)
              and all(isinstance(row, dict) and row.get("passed") is True
                      for row in report_checks.values()))
        check(f"{label}:source-commit",
              report.get("source_commit") == EVIDENCE_SOURCE_COMMIT,
              report.get("source_commit"))
        check(f"{label}:repo-sha",
              report.get("repo_sha") == CORPUS_REVISIONS.get(repo),
              report.get("repo_sha"))
        check(f"{label}:target-count", report.get("expected_n") == 20,
              report.get("expected_n"))
        run_dir = report.get("run_dir")
        check(f"{label}:run-directory",
              isinstance(run_dir, str) and os.path.isabs(run_dir)
              and os.path.isdir(run_dir), run_dir)
        design = report.get("design_v2_s10", {})
        expected_closure = ("PASS" if language == "lean" else
                            "NOT-APPLICABLE-BEST-EFFORT-AST")
        check(f"{label}:design-verdicts",
              design.get("extraction_validation") == "PASS"
              and design.get("standalone_compile") == "PASS"
              and design.get("elaborator_closure_check") ==
              expected_closure, design)
        input_hashes = report.get("input_hashes", {})
        required = {"completion_envelope", "extraction", "validation",
                    "compile_audit"}
        if language == "lean":
            required.update(("pairs", "closure_audit"))
        if repo == "physlib":
            required.update(("pinned_mathlib_pairs",
                             "pinned_mathlib_extraction"))
        check(f"{label}:transitive-input-hashes",
              isinstance(input_hashes, dict)
              and required <= set(input_hashes)
              and all(bool(SHA_RE.fullmatch(input_hashes.get(key, "")))
                      for key in required), sorted(input_hashes))
        for key in sorted(required):
            source_path = (os.path.join(run_dir, INPUT_FILES[key])
                           if isinstance(run_dir, str) else None)
            exists = bool(source_path and os.path.isfile(source_path))
            check(f"{label}:transitive-{key}-exists", exists,
                  source_path)
            if exists:
                got = _sha256(source_path)
                check(f"{label}:transitive-{key}-rehash",
                      got == input_hashes.get(key), got)
        rows.append(dict(repo=repo, language=language, gate_path=path,
                         gate_sha256=_sha256(path), run_dir=run_dir,
                         input_hashes=input_hashes))

    missing = sorted(set(EXPECTED) - set(reports))
    extra = sorted(repo for repo in reports if repo not in EXPECTED)
    check("exact-corpus-set", not missing and not extra,
          dict(missing=missing, extra=extra))
    run_dirs = [row["run_dir"] for row in rows]
    check("distinct-run-directories",
          all(isinstance(path, str) and path for path in run_dirs)
          and len(set(run_dirs)) == len(run_dirs), run_dirs)
    rows.sort(key=lambda row: row["repo"] or "")
    return dict(schema=COHORT_SCHEMA,
                evidence_source_commit=EVIDENCE_SOURCE_COMMIT,
                gate_complete=not failures, failures=failures,
                checks=checks, corpora=rows)


def _write_new(path, value):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise CohortError(f"refusing to overwrite cohort gate: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".v2a-cohort-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise CohortError(
                f"refusing to overwrite cohort gate: {path}") from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="append", required=True,
                    help="one corpus structural_gate.json; repeat five times")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report = finalize(args.gate)
    _write_new(args.out, report)
    print(f"[v2a-cohort] gate_complete={report['gate_complete']} -> "
          f"{args.out}")
    sys.exit(0 if report["gate_complete"] else 1)


if __name__ == "__main__":
    main()
