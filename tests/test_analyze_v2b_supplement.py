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
    # The module pins are filled now; explicit None inherits them, so
    # exercise the refusal branches by blanking the constants.
    saved = (sup.PINNED_SUPPLEMENT_MANIFEST_SHA256,
             sup.PINNED_SUPPLEMENT_SCORING_TREE)
    sup.PINNED_SUPPLEMENT_MANIFEST_SHA256 = None
    sup.PINNED_SUPPLEMENT_SCORING_TREE = None
    try:
        with _expect(V2BError, "no pinned supplement manifest"):
            sup.analyze_supplement("nope.json", "nope.json", {}, {}, {},
                                   expected_manifest_sha=None,
                                   expected_tree="t")
        with _expect(V2BError, "no pinned supplement scoring tree"):
            sup.analyze_supplement("nope.json", "nope.json", {}, {}, {},
                                   expected_manifest_sha="aa" * 32,
                                   expected_tree=None)
    finally:
        (sup.PINNED_SUPPLEMENT_MANIFEST_SHA256,
         sup.PINNED_SUPPLEMENT_SCORING_TREE) = saved


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
    assert sup.PINNED_SUPPLEMENT_MANIFEST_SHA256 == \
        "2543b185e8d6d9359a112079df7b98dfd6547015b7b88a5ac29a3ea1ba5c88e5"
    assert sup.PINNED_SUPPLEMENT_SCORING_TREE == (
        "f767635242a71e1341545ec48bcdb72bb8d6c83cad807dc26ddf"
        "b92da37c8d4c")
    assert sup.SUPPLEMENT_ANALYSIS_SCHEMA == "v2b_supplement_dose_v1"
    assert sup.SUPPLEMENT_CLAIM == \
        "exploratory-nll-only-supplemented-pilot"
    assert sup.REPO == "mathlib4"
    import analyze_v2b_interior as intr
    assert intr.PINNED_INTERIOR_SCORING_TREE == (
        "b7632c5deb3a89ac11d5da4532cb98fa247ad31d70c4083a49fedcaf"
        "0736cab1")  # interior stays pinned to ITS tree


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("SUPPLEMENT CONSUMER TESTS PASS")


def test_frozen_t_breakpoint_extension():
    from analyze_v2b_nll_exploratory import (
        T_095_BY_DF, T_0975_BY_DF, _inference)
    # df 1-19 entries untouched (committed artifacts must reproduce)
    assert T_0975_BY_DF[19] == 2.093024 and T_095_BY_DF[19] == 1.729132812
    # breakpoints present and strictly decreasing in df
    breaks = [20, 25, 30, 40, 60, 80, 120]
    for table in (T_095_BY_DF, T_0975_BY_DF):
        vals = [table[b] for b in breaks]
        assert vals == sorted(vals, reverse=True)
    # df=102 (103 modules) resolves via the df=80 breakpoint and yields
    # a wider CI than the df=120 entry would give: conservative.
    rows = [{"target_key": f'["M{i:03d}","d",0]', "module": f"M{i:03d}",
             "delta_bpb": 0.1 + (0.001 * (i % 7))} for i in range(103)]
    summary = _inference(rows)
    assert summary["inference_status"] == "available"
    assert summary["degrees_of_freedom"] == 102
    half = summary["ci95_two_sided_bpb"][1] - summary["target_equal_mean_bpb"]
    assert abs(half - T_0975_BY_DF[80] * summary["standard_error_bpb"]) \
        < 1e-12
