#!/usr/bin/env python3
"""Combine one corpus's independent V2-a structural reports.

The extraction validator deliberately reports ``gate_complete: false``: it
cannot attest to the separate compiler and raw-.ilean processes.  This
finalizer verifies the job completion envelope and every input hash, then
combines those independent verdicts without rewriting their evidence.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile


LEAN_SCHEMA = "v2a_lean_extract_v3"
PYTHON_SCHEMA = "v2a_python_extract_v3"
PAIR_SCHEMA = "v2a_ilean_pairs_v2"
GATE_SCHEMA = "v2a_structural_gate_v1"
# These identify the jobs being finalized, not this newer combiner's own
# commit.  A future structural rerun requires an explicit reviewed rebind;
# silently replacing any value would destroy the evidence boundary.
EVIDENCE_SOURCE_COMMIT = \
    "999cc282836d63ab386a4e8b3007dde909aa9143"
LEAN_ARTIFACT_REPORT_SHA = \
    "ec2279ef1b8c171996f020f6acf5b5d9847ad2e910e538b3142686909bb9bbc6"
PYTHON_BINARY_SHA = \
    "9544d2a29138833e6177d45dbc57468d37710b5080c901fbb579d53f251cdd6f"
PHYSLIB_MATHLIB_REVISION = \
    "81a5d257c8e410db227a6665ed08f64fea08e997"
CORPUS_REVISIONS = {
    "mathlib4": "87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
    "batteries": "76e1c118b0700b4ceafe99532e887d6431625e1a",
    "physlib": "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
    "sympy": "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
    "astropy": "440fe546589c4e496235d712bc29783ecf5a5fec",
}
CORPORA_BY_LANGUAGE = {
    "lean": frozenset(("mathlib4", "batteries", "physlib")),
    "python": frozenset(("sympy", "astropy")),
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class GateError(RuntimeError):
    """The evidence envelope is absent or structurally unreadable."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise GateError(f"cannot read JSON {path}: {err}") from err
    if not isinstance(value, dict):
        raise GateError(f"JSON root is not an object: {path}")
    return value


def _load_complete(path):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeError) as err:
        raise GateError(f"cannot read completion envelope: {err}") from err
    if not lines or lines[0] != "key\tvalue":
        raise GateError("completion envelope lacks key/value header")
    out = {}
    for i, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or fields[0] in out:
            raise GateError(f"invalid/duplicate completion row {i}: {line!r}")
        out[fields[0]] = fields[1]
    return out


def _identity_key(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True)


def finalize(run_dir, language, expected_n=20):
    run_dir = os.path.abspath(run_dir)
    if language not in ("lean", "python"):
        raise GateError(f"unknown language {language!r}")
    if not isinstance(expected_n, int) or isinstance(expected_n, bool) \
            or expected_n <= 0:
        raise GateError(f"invalid expected target count {expected_n!r}")
    complete_path = os.path.join(run_dir, "complete.tsv")
    complete = _load_complete(complete_path)
    failures = []
    checks = {}

    def check(name, condition, detail=None):
        passed = bool(condition)
        checks[name] = dict(passed=passed, detail=detail)
        if not passed:
            failures.append(name if detail is None else f"{name}:{detail}")
        return passed

    repo = complete.get("repo")
    check("completion-status", complete.get("status") == "complete",
          complete.get("status"))
    check("completion-repo", isinstance(repo, str) and bool(repo), repo)
    check("completion-repo-tag", repo in CORPORA_BY_LANGUAGE[language], repo)
    check("source-commit",
          complete.get("source_commit") == EVIDENCE_SOURCE_COMMIT,
          complete.get("source_commit"))
    check("repo-sha", complete.get("repo_sha") ==
          CORPUS_REVISIONS.get(repo), complete.get("repo_sha"))

    paths = {
        "extraction": os.path.join(run_dir, "extraction.json"),
        "validation": os.path.join(run_dir, "validation.json"),
        "compile_audit": os.path.join(run_dir,
                                      "boundary_compile_audit.json"),
    }
    hash_fields = {
        "extraction": "extraction_sha256",
        "validation": "validation_sha256",
        "compile_audit": "compile_audit_sha256",
    }
    if language == "lean":
        paths.update(
            pairs=os.path.join(run_dir, "pairs.json"),
            closure_audit=os.path.join(run_dir, "raw_closure_audit.json"))
        hash_fields.update(pairs="pairs_sha256",
                           closure_audit="closure_audit_sha256")
    actual_hashes = {"completion_envelope": _sha256(complete_path)}
    for name, path in paths.items():
        exists = os.path.isfile(path)
        check(f"{name}-exists", exists, path)
        if exists:
            actual_hashes[name] = _sha256(path)
            recorded = complete.get(hash_fields[name])
            check(f"{name}-hash", bool(SHA_RE.fullmatch(recorded or ""))
                  and recorded == actual_hashes[name], recorded)

    # Missing files make semantic reads impossible; still return a complete
    # negative report rather than manufacturing placeholder evidence.
    if any(name not in actual_hashes for name in paths):
        return dict(schema=GATE_SCHEMA, language=language, repo=repo,
                    run_dir=run_dir, gate_complete=False,
                    failures=failures, checks=checks,
                    input_hashes=actual_hashes)

    extraction = _load_json(paths["extraction"])
    validation = _load_json(paths["validation"])
    compile_audit = _load_json(paths["compile_audit"])
    expected_schema = LEAN_SCHEMA if language == "lean" else PYTHON_SCHEMA
    summary = validation.get("summary", {})
    check("extraction-schema", extraction.get("schema") == expected_schema,
          extraction.get("schema"))
    check("extraction-repo", extraction.get("repo") == repo,
          extraction.get("repo"))
    check("validation-schema", summary.get("schema") == expected_schema,
          summary.get("schema"))
    check("validation-repo", summary.get("repo") == repo,
          summary.get("repo"))
    check("validation-extraction-hash",
          validation.get("extraction_sha256") ==
          actual_hashes["extraction"],
          validation.get("extraction_sha256"))
    check("validation-target-count",
          summary.get("n_selected") == expected_n
          and len(validation.get("targets", [])) == expected_n,
          summary.get("n_selected"))
    check("validation-pass", summary.get("n_failures") == 0,
          summary.get("failures"))
    target_ids = [row.get("identity")
                  for row in validation.get("targets", [])]
    check("validation-target-identities",
          len({_identity_key(x) for x in target_ids}) == len(target_ids)
          and all(row.get("roundtrip_ok") is True
                  for row in validation.get("targets", [])))

    compile_summary = compile_audit.get("summary", {})
    compile_inputs = compile_audit.get("inputs", {})
    check("compile-extraction-schema",
          compile_audit.get("extraction_schema") == expected_schema,
          compile_audit.get("extraction_schema"))
    check("compile-input-hashes",
          compile_inputs.get("extraction_sha256") ==
          actual_hashes["extraction"]
          and compile_inputs.get("validation_sha256") ==
          actual_hashes["validation"])
    check("compile-target-count",
          compile_summary.get("n_selected") == expected_n
          and len(compile_audit.get("targets", [])) == expected_n,
          compile_summary.get("n_selected"))
    compile_target_ids = [row.get("identity")
                          for row in compile_audit.get("targets", [])]
    check("compile-target-identities", compile_target_ids == target_ids)
    check("compile-pass",
          compile_summary.get("n_failed") == 0
          and compile_summary.get("standalone_compile") == "PASS"
          and all(row.get("passed") is True
                  for row in compile_audit.get("targets", [])),
          compile_summary.get("failures"))

    closure_status = "NOT-APPLICABLE-BEST-EFFORT-AST"
    if language == "python":
        check("python-source-files-complete",
              extraction.get("n_failed") == 0,
              extraction.get("n_failed"))
        check("python-closure-scope",
              compile_summary.get("closure_check") == closure_status,
              compile_summary.get("closure_check"))
        check("python-binary-identity",
              complete.get("python_sha256") == PYTHON_BINARY_SHA
              and compile_audit.get("python_sha256") ==
              complete.get("python_sha256"),
              complete.get("python_sha256"))
    else:
        pairs = _load_json(paths["pairs"])
        closure = _load_json(paths["closure_audit"])
        closure_summary = closure.get("summary", {})
        check("pairs-schema", pairs.get("schema") == PAIR_SCHEMA,
              pairs.get("schema"))
        check("pairs-repo-sha",
              pairs.get("repo_git_sha") == complete.get("repo_sha")
              and pairs.get("expected_repo_git_sha") ==
              complete.get("repo_sha"), pairs.get("repo_git_sha"))
        check("lean-artifact-build-report",
              complete.get("artifact_build_report_sha256") ==
              LEAN_ARTIFACT_REPORT_SHA,
              complete.get("artifact_build_report_sha256"))
        check("extraction-pairs-hash",
              extraction.get("pairs_manifest_sha256") ==
              actual_hashes["pairs"])
        check("closure-input-hashes",
              closure.get("inputs", {}).get("extraction_sha256") ==
              actual_hashes["extraction"]
              and closure.get("inputs", {}).get("validation_sha256") ==
              actual_hashes["validation"]
              and closure.get("inputs", {}).get("pairs_sha256") ==
              actual_hashes["pairs"])
        check("closure-target-count",
              closure_summary.get("n_selected") == expected_n
              and len(closure.get("targets", [])) == expected_n,
              closure_summary.get("n_selected"))
        check("closure-target-identities",
              [row.get("identity") for row in closure.get("targets", [])]
              == target_ids
              and all(row.get("match") is True
                      for row in closure.get("targets", [])))
        check("closure-pass",
              closure_summary.get("n_failed") == 0
              and closure_summary.get("elaborator_closure_check") == "PASS"
              and closure_summary.get(
                  "foreign_declaration_info_partition_match") is True,
              closure_summary.get("failures"))
        foreign_rows = extraction.get(
            "foreign_declaration_infos_by_module", {})
        foreign_n = sum(len(rows) for rows in foreign_rows.values())
        check("foreign-declinfo-count",
              extraction.get("n_foreign_declaration_infos", 0) == foreign_n
              and closure_summary.get("n_foreign_declaration_infos") ==
              foreign_n, foreign_n)
        closure_status = (
            "PASS" if checks["closure-pass"]["passed"] else "FAIL")

        if repo == "physlib":
            pin_paths = {
                "pairs": os.path.join(run_dir, "pinned_mathlib_pairs.json"),
                "extraction": os.path.join(
                    run_dir, "pinned_mathlib_extraction.json")}
            pin_hash_fields = {
                "pairs": "pinned_mathlib_pairs_sha256",
                "extraction": "pinned_mathlib_extraction_sha256"}
            pin_values = {}
            for name, path in pin_paths.items():
                if os.path.isfile(path):
                    pin_values[name] = _load_json(path)
                    got = _sha256(path)
                    actual_hashes[f"pinned_mathlib_{name}"] = got
                    check(f"pinned-mathlib-{name}-hash",
                          got == complete.get(pin_hash_fields[name]), got)
                else:
                    check(f"pinned-mathlib-{name}-exists", False, path)
            if set(pin_values) == set(pin_paths):
                pin_pairs, pin_ex = (pin_values["pairs"],
                                     pin_values["extraction"])
                check("pinned-mathlib-schemas",
                      pin_pairs.get("schema") == PAIR_SCHEMA
                      and pin_ex.get("schema") == LEAN_SCHEMA)
                check("pinned-mathlib-revision",
                      complete.get("pinned_mathlib_repo_sha") ==
                      PHYSLIB_MATHLIB_REVISION
                      and pin_pairs.get("repo_git_sha") ==
                      complete.get("pinned_mathlib_repo_sha")
                      and pin_pairs.get("expected_repo_git_sha") ==
                      complete.get("pinned_mathlib_repo_sha"))
                check("pinned-mathlib-pair-binding",
                      pin_ex.get("pairs_manifest_sha256") ==
                      complete.get("pinned_mathlib_pairs_sha256")
                      and pin_ex.get("repo") ==
                      "physlib_pinned_mathlib")
        else:
            unexpected_pin_fields = sorted(
                key for key in complete if key.startswith("pinned_mathlib_"))
            unexpected_pin_files = sorted(
                name for name in (
                    "pinned_mathlib_pairs.json",
                    "pinned_mathlib_extraction.json")
                if os.path.exists(os.path.join(run_dir, name)))
            check("pinned-mathlib-evidence-absent",
                  not unexpected_pin_fields and not unexpected_pin_files,
                  dict(fields=unexpected_pin_fields,
                       files=unexpected_pin_files))

    gate_complete = not failures
    extraction_validation_ok = all(
        checks.get(name, {}).get("passed") for name in (
            "extraction-hash", "validation-hash", "extraction-schema",
            "extraction-repo", "validation-schema", "validation-repo",
            "validation-extraction-hash", "validation-target-count",
            "validation-pass", "validation-target-identities"))
    return dict(
        schema=GATE_SCHEMA, language=language, repo=repo, run_dir=run_dir,
        source_commit=complete.get("source_commit"),
        repo_sha=complete.get("repo_sha"), expected_n=expected_n,
        gate_complete=gate_complete, failures=failures, checks=checks,
        design_v2_s10=dict(
            extraction_validation=(
                "PASS" if extraction_validation_ok else "FAIL"),
            standalone_compile=("PASS" if checks.get(
                "compile-pass", {}).get("passed") else "FAIL"),
            elaborator_closure_check=closure_status),
        input_hashes=actual_hashes,
        structural_diagnostics=dict(
            target_coverage_mean=summary.get("target_coverage_mean"),
            internal_renderability_coverage=summary.get(
                "internal_renderability_coverage"),
            n_foreign_declaration_infos=summary.get(
                "n_foreign_declaration_infos"),
            n_duplicate_python_bindings=summary.get(
                "n_duplicate_python_bindings")))


def _write_new(path, value):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise GateError(f"refusing to overwrite gate report: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".v2a-gate-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise GateError(f"refusing to overwrite gate report: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--language", required=True, choices=("lean", "python"))
    ap.add_argument("--expected-n", type=int, default=20)
    ap.add_argument("--out")
    args = ap.parse_args()
    report = finalize(args.run_dir, args.language, args.expected_n)
    out = args.out or os.path.join(args.run_dir, "structural_gate.json")
    _write_new(out, report)
    print(f"[v2a-finalize] {report['repo']} {report['language']}: "
          f"gate_complete={report['gate_complete']} -> {out}")
    sys.exit(0 if report["gate_complete"] else 1)


if __name__ == "__main__":
    main()
