#!/usr/bin/env python3
"""Lean boundary compile-audit tests with a fake Lake runner."""
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit_lean_compile import MARKER, audit


def _fixture(td):
    source = os.path.join(td, "M.lean")
    text = (b"theorem M.a : True := trivial\n"
            b"theorem M.b : True := trivial\n")
    open(source, "wb").write(text)
    first_end = text.index(b"\n")
    second_start = first_end + 1
    decls = {
        "M.a": dict(start_byte=0, end_byte=first_end,
                    header_bytes=text.index(b":=")),
        "M.b": dict(start_byte=second_start, end_byte=len(text) - 1,
                    header_bytes=text.index(b":=", second_start)
                    - second_start),
    }
    extraction = dict(
        schema="v2a_lean_extract_v2", repo="r",
        files=[dict(module="M", source=source,
                    source_sha256=hashlib.sha256(text).hexdigest(),
                    decls=decls)])
    validation = dict(
        summary=dict(schema="v2a_lean_extract_v2", repo="r"),
        targets=[dict(identity=["M", "M.a"]),
                 dict(identity=["M", "M.b"])])
    return extraction, validation, source


class FakeRunner:
    def __init__(self, fail_marked=False):
        self.calls = []
        self.fail_marked = fail_marked

    def __call__(self, cmd, cwd, timeout):
        self.calls.append((cmd, cwd, timeout))
        source = cmd[-1]
        marked = MARKER in open(source, "rb").read()
        rc = 1 if self.fail_marked and marked else 0
        if rc == 0:
            for flag in ("-o", "-i"):
                out = cmd[cmd.index(flag) + 1]
                open(out, "wb").write(b"artifact")
        return subprocess.CompletedProcess(cmd, rc, "", "marked failed" if rc else "")


def test_baseline_cached_and_each_boundary_marked():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, _ = _fixture(td)
        runner = FakeRunner()
        work = os.path.join(td, "work")
        report = audit(extraction, validation, td, work, lake="lake",
                       runner=runner)
        assert report["summary"]["standalone_compile"] == "PASS"
        assert report["summary"]["n_unique_source_controls"] == 1
        assert len(runner.calls) == 3       # one source baseline + 2 markers
        assert all(t["passed"] for t in report["targets"])
        assert sum(MARKER in open(c[0][-1], "rb").read()
                   for c in runner.calls) == 2


def test_marker_compile_failure_is_counted():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, _ = _fixture(td)
        report = audit(extraction, validation, td, os.path.join(td, "work"),
                       lake="lake", runner=FakeRunner(fail_marked=True))
        assert report["summary"]["standalone_compile"] == "FAIL"
        assert report["summary"]["n_failed"] == 2
        assert all(not t["passed"] for t in report["targets"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("LEAN-COMPILE AUDIT TESTS PASS")
