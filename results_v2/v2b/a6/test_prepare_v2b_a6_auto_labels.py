#!/usr/bin/env python3
"""Outcome-free focused regressions for the automatic A6 lexical gate."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "a6auto", HERE / "prepare_v2b_a6_auto_labels.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

LEAN_KEYWORDS = frozenset({"by", "lemma", "rw", "simp"})


def test_one_systematic_lean_type_rename_passes():
    left = "lemma f (x : LengthUnit) : LengthUnit := by\n  simp\n"
    right = "lemma f (x : TemperatureUnit) : TemperatureUnit := by\n  simp\n"
    assert module.lexical_gate("lean", left, right, LEAN_KEYWORDS)


def test_two_identifier_renames_or_literal_drift_fail():
    left = "lemma alpha (x : Nat) : Nat := by\n  simp\n"
    two = "lemma beta (y : Nat) : Nat := by\n  simp\n"
    literal = "lemma alpha (x : Nat) : Nat := by\n  exact 1\n"
    assert not module.lexical_gate("lean", left, two, LEAN_KEYWORDS)
    assert not module.lexical_gate("lean", left, literal, LEAN_KEYWORDS)


def test_python_layout_width_is_not_semantic_but_two_renames_are():
    left = "def f(x):\n    return x + 1\n"
    one = "def g(x):\n  return x + 1\n"
    two = "def g(y):\n  return y + 1\n"
    assert module.lexical_gate("python", left, one, LEAN_KEYWORDS)
    assert not module.lexical_gate("python", left, two, LEAN_KEYWORDS)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print("A6 AUTOMATIC ADJUDICATION TESTS PASS")
