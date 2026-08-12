#!/usr/bin/env python3
"""V2-c reveal producer: go-token gate, coverage, constants."""
import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import finalize_v2c_reveal as rev  # noqa: E402
from v2b_common import V2BError  # noqa: E402


@contextlib.contextmanager
def _expect(exc_type, needle=None):
    try:
        yield
    except exc_type as err:
        if needle is not None and needle not in str(err):
            raise AssertionError(
                f"expected {needle!r} in {exc_type.__name__}: {err}")
    else:
        raise AssertionError(f"expected {exc_type.__name__}, none raised")


def test_go_token_gate():
    import sys as _sys
    argv = _sys.argv
    _sys.argv = ["finalize_v2c_reveal.py", "--confirm-reveal", "nope",
                 "--plan", "p", "--salt", "s", "--salt-commitment", "c",
                 "--repo", "mathlib4=a,b,c,d,e",
                 "--repo", "sympy=a,b,c,d,e"]
    try:
        with _expect(V2BError, "go-token"):
            rev.main()
    finally:
        _sys.argv = argv


def test_constants_and_coverage():
    assert rev.AMENDMENT_SHA256.startswith("49ff6d8f9650")
    assert rev.REVEAL_OUT_PATH == "results_v2/v2c/V2C_REVEAL.json"
    assert rev.REPOS == ("mathlib4", "sympy")
    assert rev.V2C_REVEAL_SCHEMA == "v2c_confirmatory_reveal_v1"
    from v2b_v2c_governance import V2C_CLAIM_LABEL
    assert V2C_CLAIM_LABEL == \
        "confirmatory-with-post-pilot-amended-governance"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("V2C REVEAL TESTS PASS")
