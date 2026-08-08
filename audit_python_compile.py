#!/usr/bin/env python3
"""Independent Python boundary compile audit for V2-a.

Each selected file is byte-compiled unchanged as an environment control. A
copy is then byte-compiled with an inert marker inserted exactly at the
recorded suite-colon boundary. Indented suites receive a comment marker;
one-line suites receive a harmless string-expression statement. This validates
the structural split without executing imports or target code.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile


EXTRACT_SCHEMA = "v2a_python_extract_v3"
AUDIT_SCHEMA = "v2a_python_boundary_compile_audit_v1"
BLOCK_MARKER = b" # V2A_BODY_BOUNDARY"
INLINE_MARKER = b' "V2A_BODY_BOUNDARY";'
COMPILE_CODE = (
    "import py_compile,sys;"
    "py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True)")


class CompileAuditError(RuntimeError):
    """The input identity or Python compile audit failed closed."""


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


def _runner(cmd, cwd, timeout):
    try:
        return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as err:
        return subprocess.CompletedProcess(
            cmd, 124, err.stdout or "", (err.stderr or "") + "\nTIMEOUT")


def _compile(python, repo_root, source, pyc, timeout, runner):
    cmd = [python, "-c", COMPILE_CODE, source, pyc]
    proc = runner(cmd, repo_root, timeout)
    output_ok = os.path.isfile(pyc) and os.path.getsize(pyc) > 0
    return dict(command=cmd, returncode=proc.returncode,
                output_ok=output_ok,
                stdout_tail=(proc.stdout or "")[-4000:],
                stderr_tail=(proc.stderr or "")[-4000:],
                pass_compile=(proc.returncode == 0 and output_ok))


def _marked_source(source, boundary):
    if boundary <= 0 or source[boundary - 1:boundary] != b":":
        raise CompileAuditError(
            f"Python boundary {boundary} does not follow a suite colon")
    line_end = source.find(b"\n", boundary)
    if line_end < 0:
        line_end = len(source)
    before_newline = source[boundary:line_end]
    # Blank/comment-only remainder means an indented suite follows. Adding a
    # comment before the existing remainder preserves that grammar. Otherwise
    # this is a one-line simple_stmt suite, so prepend an inert statement.
    if not before_newline.strip() or before_newline.lstrip().startswith(b"#"):
        marker = BLOCK_MARKER
        mode = "indented-suite-comment"
    else:
        marker = INLINE_MARKER
        mode = "one-line-string-statement"
    return source[:boundary] + marker + source[boundary:], mode


def audit(extraction, validation, repo_root, work_dir, python=sys.executable,
          timeout=120, runner=_runner):
    if extraction.get("schema") != EXTRACT_SCHEMA:
        raise CompileAuditError("wrong Python extraction schema")
    summary = validation.get("summary", {})
    if summary.get("schema") != EXTRACT_SCHEMA:
        raise CompileAuditError("validation/extraction schema mismatch")
    if summary.get("repo") != extraction.get("repo"):
        raise CompileAuditError("validation/extraction repo mismatch")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise CompileAuditError(f"invalid timeout: {timeout!r}")
    repo_root = os.path.abspath(repo_root)
    if not os.path.isdir(repo_root):
        raise CompileAuditError(f"missing repo root: {repo_root}")
    if not os.path.isfile(python) or not os.access(python, os.X_OK):
        raise CompileAuditError(f"invalid Python executable: {python}")
    os.makedirs(work_dir, exist_ok=True)

    identities = {}
    for file_rec in extraction.get("files", []):
        for target in file_rec.get("targets", []):
            raw_identity = target.get("identity")
            identity = tuple(raw_identity) \
                if isinstance(raw_identity, list) else ()
            if (len(identity) != 3
                    or identity[0] != file_rec.get("module")
                    or identity[1] != target.get("name")
                    or identity[2] != target.get("start_byte")):
                raise CompileAuditError(
                    f"invalid target identity: {raw_identity!r}")
            if identity in identities:
                raise CompileAuditError(f"duplicate target: {identity!r}")
            identities[identity] = (file_rec, target)

    selected_raw = [t.get("identity")
                    for t in validation.get("targets", [])]
    selected = [tuple(x) if isinstance(x, list) else ()
                for x in selected_raw]
    if (not selected or len(set(selected)) != len(selected)
            or not all(len(x) == 3 and isinstance(x[0], str)
                       and isinstance(x[1], str)
                       and isinstance(x[2], int)
                       and not isinstance(x[2], bool) for x in selected)):
        raise CompileAuditError("selected identities are empty/invalid/duplicate")

    baseline_cache = {}
    rows = []
    failures = []
    for i, identity in enumerate(selected):
        if identity not in identities:
            raise CompileAuditError(f"selected target absent: {identity}")
        file_rec, target = identities[identity]
        source_path = os.path.abspath(file_rec["source"])
        try:
            inside = os.path.commonpath((repo_root, source_path)) == repo_root
        except ValueError:
            inside = False
        if not inside:
            raise CompileAuditError(f"source outside repo root: {source_path}")
        source = open(source_path, "rb").read()
        if hashlib.sha256(source).hexdigest() != file_rec["source_sha256"]:
            raise CompileAuditError(f"source changed: {source_path}")
        boundary = target.get("body_start_byte")
        start, end = target.get("start_byte"), target.get("end_byte")
        if not (isinstance(start, int) and isinstance(end, int)
                and isinstance(boundary, int)
                and 0 <= start < boundary < end <= len(source)):
            raise CompileAuditError(f"invalid boundary for {identity}")

        if source_path not in baseline_cache:
            baseline_cache[source_path] = _compile(
                python, repo_root, source_path,
                os.path.join(work_dir,
                             f"baseline_{len(baseline_cache):04d}.pyc"),
                timeout, runner)
        baseline = baseline_cache[source_path]

        marked, marker_mode = _marked_source(source, boundary)
        marked_path = os.path.join(work_dir, f"marked_{i:04d}.py")
        with open(marked_path, "xb") as fh:
            fh.write(marked)
        marked_compile = _compile(
            python, repo_root, marked_path,
            os.path.join(work_dir, f"marked_{i:04d}.pyc"), timeout, runner)
        passed = baseline["pass_compile"] and marked_compile["pass_compile"]
        if not passed:
            failures.append(list(identity))
        rows.append(dict(
            identity=list(identity), source=source_path,
            source_sha256=file_rec["source_sha256"],
            start_byte=start, end_byte=end, boundary_byte=boundary,
            marker_mode=marker_mode,
            marker_sha256=hashlib.sha256(marked).hexdigest(),
            baseline=baseline, marked=marked_compile, passed=passed))
    return dict(
        schema=AUDIT_SCHEMA, extraction_schema=EXTRACT_SCHEMA,
        mode=("full-source py_compile control plus inert boundary marker; "
              "no import or target execution, no exact-closure claim"),
        repo_root=repo_root, python=os.path.abspath(python),
        python_sha256=_sha256(python), timeout_seconds=timeout,
        summary=dict(n_selected=len(selected),
                     n_unique_source_controls=len(baseline_cache),
                     n_passed=len(selected) - len(failures),
                     n_failed=len(failures), failures=failures,
                     standalone_compile=(
                         "PASS" if not failures else "FAIL"),
                     closure_check="NOT-APPLICABLE-BEST-EFFORT-AST"),
        targets=rows)


def _write_new(path, value):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise CompileAuditError(f"refusing to overwrite audit: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".python-compile-audit-",
                               suffix=".json", dir=parent)
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
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()
    extraction = _load(args.extraction)
    validation = _load(args.validation)
    extraction_sha = _sha256(args.extraction)
    if validation.get("extraction_sha256") != extraction_sha:
        raise CompileAuditError(
            "validation is not bound to this extraction hash")
    with tempfile.TemporaryDirectory(prefix="v2a-python-compile-",
                                     dir=os.environ.get("TMPDIR")) as td:
        report = audit(extraction, validation, args.repo_root, td,
                       args.python, args.timeout)
    report["inputs"] = dict(
        extraction=args.extraction, extraction_sha256=extraction_sha,
        validation=args.validation,
        validation_sha256=_sha256(args.validation))
    _write_new(args.out, report)
    summary = report["summary"]
    print(f"[python-compile-audit] {summary['n_passed']}/"
          f"{summary['n_selected']} -> {args.out}")
    sys.exit(1 if summary["n_failed"] else 0)


if __name__ == "__main__":
    main()
