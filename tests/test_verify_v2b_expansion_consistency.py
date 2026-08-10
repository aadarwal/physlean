#!/usr/bin/env python3
"""Expansion consistency gate: exact tier-block reproduction or refusal."""
import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import verify_v2b_expansion_consistency as vex  # noqa: E402
from v2b_common import V2BError  # noqa: E402


@contextlib.contextmanager
def _expect(needle):
    try:
        yield
    except V2BError as err:
        if needle not in str(err):
            raise AssertionError(f"expected {needle!r} in: {err}")
    else:
        raise AssertionError("expected V2BError, none raised")


def _art(tiers, schema="v2b_nll_ladder_analysis_v1"):
    return dict(schema=schema, repo="sympy", metric="bpb",
                claim_status="c", mode=None, tiers=tiers)


def test_exact_reproduction_passes_and_reports_sets():
    prior = _art({"a": {"x": 1}, "b": {"y": [2, 3]}})
    current = _art({"a": {"x": 1}, "b": {"y": [2, 3]}, "c": {"z": 4}})
    reproduced, added = vex.verify(prior, current)
    assert reproduced == ["a", "b"] and added == ["c"]


def test_block_drift_refused():
    prior = _art({"a": {"x": 1}})
    current = _art({"a": {"x": 2}, "c": {"z": 4}})
    with _expect("does not reproduce"):
        vex.verify(prior, current)


def test_non_strict_extension_refused():
    prior = _art({"a": {"x": 1}})
    with _expect("strictly extend"):
        vex.verify(prior, _art({"a": {"x": 1}}))
    with _expect("strictly extend"):
        vex.verify(prior, _art({"b": {"x": 1}, "c": {}}))


def test_identity_field_drift_refused():
    prior = _art({"a": {}})
    bad = _art({"a": {}, "b": {}})
    bad["repo"] = "mathlib4"
    with _expect("repo drift"):
        vex.verify(prior, bad)
    with _expect("unknown prior schema"):
        vex.verify(_art({"a": {}}, schema="nope"), _art({"a": {}, "b": {}}))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("EXPANSION CONSISTENCY TESTS PASS")
