#!/usr/bin/env python3
"""Compile audit for V2-a Lean header/body boundaries.

For each selected target, compile the unchanged source once as an environment
control, then compile a full-source copy containing an inert Lean block comment
inserted exactly at the extracted header/body byte boundary. A passing marker
compile shows that the recorded boundary is a valid token boundary in the
target's real file environment. This is structural validation only; it does not
run a language model or claim the extracted prompt fragment elaborates alone.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


EXTRACT_SCHEMA = "v2a_lean_extract_v2"
AUDIT_SCHEMA = "v2a_lean_boundary_compile_audit_v1"
MARKER = b" /- V2A_BODY_BOUNDARY -/ "


class CompileAuditError(RuntimeError):
    """The audit inputs or execution environment failed closed."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise CompileAuditError(f"cannot read {path}: {err}") from err


def _run(cmd, cwd, timeout):
    try:
        return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as err:
        return subprocess.CompletedProcess(
            cmd, 124, err.stdout or "", (err.stderr or "") + "\nTIMEOUT")


def _tail(text, n=4000):
    return (text or "")[-n:]


def _compile_one(lake, repo_root, source, out_stem, timeout, runner):
    olean = out_stem + ".olean"
    ilean = out_stem + ".ilean"
    cmd = [lake, "env", "lean", "-o", olean, "-i", ilean, source]
    proc = runner(cmd, repo_root, timeout)
    outputs_ok = (os.path.isfile(olean) and os.path.getsize(olean) > 0
                  and os.path.isfile(ilean) and os.path.getsize(ilean) > 0)
    return dict(command=cmd, returncode=proc.returncode,
                outputs_ok=outputs_ok,
                stdout_tail=_tail(proc.stdout), stderr_tail=_tail(proc.stderr),
                pass_compile=(proc.returncode == 0 and outputs_ok))


def audit(extraction, validation, repo_root, work_dir, timeout=300,
          lake=None, runner=_run):
    if extraction.get("schema") != EXTRACT_SCHEMA:
        raise CompileAuditError("wrong Lean extraction schema")
    if validation.get("summary", {}).get("schema") != EXTRACT_SCHEMA:
        raise CompileAuditError("validation/extraction schema mismatch")
    if validation.get("summary", {}).get("repo") != extraction.get("repo"):
        raise CompileAuditError("validation/extraction repo mismatch")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise CompileAuditError(f"invalid timeout: {timeout!r}")
    repo_root = os.path.abspath(repo_root)
    if not os.path.isdir(repo_root):
        raise CompileAuditError(f"missing repo root: {repo_root}")
    lake = lake or shutil.which("lake")
    if not lake:
        raise CompileAuditError("lake is not on PATH")
    os.makedirs(work_dir, exist_ok=True)

    by_identity = {}
    for file_rec in extraction.get("files", []):
        module = file_rec.get("module")
        for name, decl in file_rec.get("decls", {}).items():
            ident = (module, name)
            if ident in by_identity:
                raise CompileAuditError(f"duplicate extraction identity {ident}")
            by_identity[ident] = (file_rec, decl)

    selected = []
    for i, target in enumerate(validation.get("targets", [])):
        ident = target.get("identity")
        if not (isinstance(ident, list) and len(ident) == 2
                and all(isinstance(x, str) and x for x in ident)):
            raise CompileAuditError(f"target[{i}] lacks v2 identity")
        selected.append(tuple(ident))
    if not selected or len(set(selected)) != len(selected):
        raise CompileAuditError("selected identities are empty or duplicated")

    baseline_cache = {}
    rows = []
    failures = []
    for i, ident in enumerate(selected):
        if ident not in by_identity:
            raise CompileAuditError(f"selected target absent: {ident}")
        file_rec, decl = by_identity[ident]
        source = os.path.abspath(file_rec["source"])
        try:
            inside = os.path.commonpath((repo_root, source)) == repo_root
        except ValueError:
            inside = False
        if not inside:
            raise CompileAuditError(f"source outside repo root: {source}")
        source_bytes = open(source, "rb").read()
        if hashlib.sha256(source_bytes).hexdigest() != \
                file_rec["source_sha256"]:
            raise CompileAuditError(f"source changed: {source}")
        start, end = decl["start_byte"], decl["end_byte"]
        header = decl["header_bytes"]
        boundary = start + header
        if not (isinstance(start, int) and isinstance(end, int)
                and isinstance(header, int)
                and 0 <= start < boundary < end <= len(source_bytes)):
            raise CompileAuditError(
                f"invalid target boundary for {ident}: "
                f"{start}+{header} within {end}")

        if source not in baseline_cache:
            stem = os.path.join(work_dir,
                                f"baseline_{len(baseline_cache):04d}")
            baseline_cache[source] = _compile_one(
                lake, repo_root, source, stem, timeout, runner)
        baseline = baseline_cache[source]

        marked = source_bytes[:boundary] + MARKER + source_bytes[boundary:]
        marked_path = os.path.join(work_dir, f"marked_{i:04d}.lean")
        with open(marked_path, "xb") as fh:
            fh.write(marked)
        marker_compile = _compile_one(
            lake, repo_root, marked_path,
            os.path.join(work_dir, f"marked_{i:04d}"), timeout, runner)
        passed = baseline["pass_compile"] and marker_compile["pass_compile"]
        if not passed:
            failures.append(list(ident))
        rows.append(dict(
            identity=list(ident), source=source,
            source_sha256=file_rec["source_sha256"],
            start_byte=start, end_byte=end, boundary_byte=boundary,
            marker_sha256=hashlib.sha256(marked).hexdigest(),
            baseline=baseline, marked=marker_compile, passed=passed))

    return dict(
        schema=AUDIT_SCHEMA, extraction_schema=EXTRACT_SCHEMA,
        mode=("full-source control plus inert block-comment insertion at "
              "the extracted header/body byte boundary; not an isolated-"
              "prompt elaboration claim"),
        repo_root=repo_root, timeout_seconds=timeout,
        summary=dict(n_selected=len(selected),
                     n_unique_source_controls=len(baseline_cache),
                     n_passed=len(selected) - len(failures),
                     n_failed=len(failures), failures=failures,
                     standalone_compile=(
                         "PASS" if not failures else "FAIL")),
        targets=rows)


def _write_new(path, value):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise CompileAuditError(f"refusing to overwrite audit: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".compile-audit-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise CompileAuditError(f"refusing to overwrite audit: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--validation", required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--work-dir")
    args = ap.parse_args()
    extraction = _load(args.extraction)
    validation = _load(args.validation)
    extraction_sha = _sha256(args.extraction)
    if validation.get("extraction_sha256") != extraction_sha:
        raise CompileAuditError(
            "validation is not bound to this extraction hash")
    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)
    if args.work_dir:
        work_dir = args.work_dir
        os.makedirs(work_dir, exist_ok=False)
        report = audit(extraction, validation, args.repo_root, work_dir,
                       args.timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="v2a-compile-",
                                         dir=os.environ.get("TMPDIR")) as td:
            report = audit(extraction, validation, args.repo_root, td,
                           args.timeout)
    report["inputs"] = dict(
        extraction=args.extraction,
        extraction_sha256=extraction_sha,
        validation=args.validation,
        validation_sha256=_sha256(args.validation))
    _write_new(args.out, report)
    s = report["summary"]
    print(f"[compile-audit] {s['n_passed']}/{s['n_selected']} boundary "
          f"compiles -> {args.out}")
    sys.exit(1 if s["n_failed"] else 0)


if __name__ == "__main__":
    main()
