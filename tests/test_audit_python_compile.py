#!/usr/bin/env python3
"""Python boundary compile-audit tests on real py_compile."""
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit_python_compile import audit
from extract_python import extract_file


def test_indented_and_one_line_boundaries_compile():
    with tempfile.TemporaryDirectory() as td:
        source = os.path.join(td, "m.py")
        text = ("def block():\n"
                "    # implementation\n"
                "    return 1\n"
                "def inline(): return 2\n")
        open(source, "w").write(text)
        rec = extract_file(source, "m.py")
        extraction = dict(schema="v2a_python_extract_v2", repo="r",
                          files=[rec])
        validation = dict(
            summary=dict(schema="v2a_python_extract_v2", repo="r"),
            targets=[dict(identity="m.block"), dict(identity="m.inline")])
        report = audit(extraction, validation, td, os.path.join(td, "work"),
                       python=sys.executable)
        assert report["summary"]["standalone_compile"] == "PASS"
        assert report["summary"]["n_unique_source_controls"] == 1
        assert [t["marker_mode"] for t in report["targets"]] == [
            "indented-suite-comment", "one-line-string-statement"]
        assert all(t["passed"] for t in report["targets"])


def test_source_hash_drift_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        source = os.path.join(td, "m.py")
        open(source, "w").write("def f(): return 1\n")
        rec = extract_file(source, "m.py")
        rec["source_sha256"] = hashlib.sha256(b"wrong").hexdigest()
        extraction = dict(schema="v2a_python_extract_v2", repo="r",
                          files=[rec])
        validation = dict(
            summary=dict(schema="v2a_python_extract_v2", repo="r"),
            targets=[dict(identity="m.f")])
        try:
            audit(extraction, validation, td, os.path.join(td, "work"),
                  python=sys.executable)
            assert False, "source hash drift accepted"
        except Exception as err:
            assert "source changed" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PYTHON-COMPILE AUDIT TESTS PASS")
