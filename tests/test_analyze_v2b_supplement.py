#!/usr/bin/env python3
"""Supplement consumer: pooling law, pin refusals, guard ordering."""
import contextlib
import hashlib
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analyze_v2b_supplement as sup  # noqa: E402
from analyze_v2b_dose import build_panel, contrast_table  # noqa: E402
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


def _key(module, decl):
    return json.dumps([module, decl], separators=(",", ":"))


def _row(module, decl, delta):
    return {"target_key": _key(module, decl), "module": module,
            "delta_bpb": delta}


def test_pool_disjointness_and_order():
    pilot = [_row("ModB", "b", 0.2), _row("ModA", "a", 0.1)]
    supp = [_row("ModC", "c", 0.3)]
    pooled = sup._pool(pilot, supp, "here")
    assert [row["target_key"] for row in pooled] == sorted(
        row["target_key"] for row in pilot + supp)
    with _expect(V2BError, "identity overlap in here"):
        sup._pool(pilot, [_row("ModA", "a", 0.9)], "here")


def test_pooled_panel_inference_over_union():
    contrasts = contrast_table("budget", 16384)
    pilot = [_row("ModA", "a1", 0.10), _row("ModA", "a2", 0.20),
             _row("ModB", "b1", 0.30), _row("ModB", "b2", 0.40)]
    supp = [_row("ModC", "c1", 0.50), _row("ModC", "c2", 0.60),
            _row("ModD", "d1", 0.70)]
    pooled = sup._pool(pilot, supp, "E1a")
    rows_by_name = {"E1a": pooled, "E1b": list(pooled),
                    "E2": list(pooled)}
    panel = build_panel("lean", rows_by_name, contrasts)
    inference = panel["contrasts"]["E1a"]["inference"]
    assert inference["n_targets"] == 7
    assert inference["n_modules"] == 4
    expected = math.fsum(row["delta_bpb"] for row in pooled) / 7
    assert inference["target_equal_mean_bpb"] == expected
    assert len(panel["contrasts"]["E1a"]["target_rows"]) == 7


def test_pin_refusals_precede_everything():
    with _expect(V2BError, "no pinned supplement manifest"):
        sup.analyze_supplement("nope.json", "nope.json", {}, {}, {},
                               expected_manifest_sha=None,
                               expected_tree="t")
    with _expect(V2BError, "no pinned epoch-2 scoring tree"):
        sup.analyze_supplement("nope.json", "nope.json", {}, {}, {},
                               expected_manifest_sha="aa" * 32,
                               expected_tree=None)


def test_guard_ordering_sha_then_tiers():
    with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False) as handle:
        handle.write("{}")
        path = handle.name
    try:
        actual = hashlib.sha256(b"{}").hexdigest()
        with _expect(V2BError, "does not match its pin"):
            sup.analyze_supplement(path, path, {}, {}, {},
                                   expected_manifest_sha="bb" * 32,
                                   expected_tree="t")
        with _expect(V2BError, "full tier set"):
            sup.analyze_supplement(path, path, {}, {}, {},
                                   expected_manifest_sha=actual,
                                   expected_tree="t")
    finally:
        os.unlink(path)


def test_supplement_launcher_contract():
    src = open(os.path.join(ROOT, "slurm",
                            "v2b_paired_supplement.sbatch")).read()
    assert "V2B_SUPPLEMENT_MANIFEST:?" in src  # manifest is required
    assert "not tracked/committed" in src  # committed-input refusal
    assert "supplement_*_mathlib4.json" in src  # assembly-artifact shape
    assert "87adeaebd370a3b6a41ac4f044fddd4bf81803ad" in src  # corpus pin
    assert "expandable_segments" in src  # 32b allocator rule
    assert src.count("h200") >= 2  # 14b and 32b gates
    # deliberately NO in-script manifest sha pin: the manifest is created
    # mid-epoch and a post-assembly launcher edit would break the
    # battery/scoring shared-tree rule; binding = ledger + consumer pin.
    assert "V2B_MANIFEST_PIN" not in src


def test_supplement_constants():
    assert sup.PINNED_SUPPLEMENT_MANIFEST_SHA256 is None  # pin-commit fills
    assert sup.EPOCH2_SCORING_TREE is None  # post-scoring pin fills
    assert sup.SUPPLEMENT_ANALYSIS_SCHEMA == "v2b_supplement_dose_v1"
    assert sup.SUPPLEMENT_CLAIM == \
        "exploratory-nll-only-supplemented-pilot"
    assert sup.REPO == "mathlib4"
    import analyze_v2b_interior as intr
    assert sup.EPOCH2_SCORING_TREE is intr.PINNED_INTERIOR_SCORING_TREE


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("SUPPLEMENT CONSUMER TESTS PASS")
