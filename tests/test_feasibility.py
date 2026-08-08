#!/usr/bin/env python3
"""Arm-feasibility manifest regression tests (stdlib only): frozen sets,
frozen floors, pure classifier against the exact cluster-realized rows,
boundary behavior, and grid preservation.
Run: python3 tests/test_feasibility.py"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from preflight_check import (ARM_FEASIBILITY, MASK_MIN_BYTES,
                             MASK_MIN_DOCS, feasible_sets)
from prep_streams import MIN_MATCHED


def test_frozen_manifest_content():
    """The manifest is EXACTLY the adopted sets (guards accidental
    edits): masking Lean-only at every cutoff, physlib-only at
    c2026_02; all-new = 4 corpora at the two older cutoffs (qutip out),
    the Lean pair at c2026_02."""
    assert ARM_FEASIBILITY == {
        "c2024_11": dict(masking={"physlib", "mathlib"},
                         allnew={"physlib", "mathlib", "sympy", "geant4"}),
        "c2025_04": dict(masking={"physlib", "mathlib"},
                         allnew={"physlib", "mathlib", "sympy", "geant4"}),
        "c2026_02": dict(masking={"physlib"},
                         allnew={"physlib", "mathlib"}),
    }


def test_floors_frozen():
    """Floors predate the G1 feasibility inspection and must never move
    to admit near-misses (geant4 130,834B all-new; mathlib 167,496B
    masking)."""
    assert MIN_MATCHED == 150_000
    assert MASK_MIN_DOCS == 20
    assert MASK_MIN_BYTES == 300_000


def _cluster_c2026_02():
    """BYTE-EXACT realized cluster rows at the G1 adoption boundary
    (PREREG §5 table and the preserved streams_stats output)."""
    clean_avail = {"c2026_02": dict(physlib=1_498_325, mathlib=5_000_507,
                                    qutip=8_416, sympy=54_843,
                                    geant4=130_834)}
    masking = {"c2026_02": dict(physlib=(47, 632_246),
                                mathlib=(25, 167_496),
                                qutip=(2, 8_416), sympy=(1, 3_868),
                                geant4=(1, 7_975))}
    return clean_avail, masking


def test_classifier_reproduces_cluster_row():
    """feasible_sets on the exact realized numbers reproduces the frozen
    c2026_02 row: mathlib fails masking on the BYTE floor despite 25
    docs and 5MB corpus-wide all-new (seeded-sample dilution)."""
    clean_avail, masking = _cluster_c2026_02()
    got = feasible_sets(clean_avail, masking)["c2026_02"]
    assert got == ARM_FEASIBILITY["c2026_02"], got
    assert "mathlib" in {c for c, v in clean_avail["c2026_02"].items()
                         if v >= MIN_MATCHED}          # all-new rich...
    assert "mathlib" not in got["masking"]             # ...masking poor


def test_floor_boundaries_exact():
    """>= semantics at every floor; one unit under stays out (the
    near-misses stay near-misses)."""
    ca = {"c2026_02": dict(a=MIN_MATCHED, b=MIN_MATCHED - 1)}
    mk = {"c2026_02": dict(a=(MASK_MIN_DOCS, MASK_MIN_BYTES),
                           b=(MASK_MIN_DOCS - 1, MASK_MIN_BYTES),
                           c=(MASK_MIN_DOCS, MASK_MIN_BYTES - 1))}
    got = feasible_sets(ca, mk)["c2026_02"]
    assert got["allnew"] == {"a"}
    assert got["masking"] == {"a"}


def test_drift_is_detected_both_directions():
    """A corpus crossing a floor in EITHER direction changes the derived
    set away from the frozen manifest — the exact-set gate trips on
    gain as well as loss."""
    clean_avail, masking = _cluster_c2026_02()
    up = {"c2026_02": dict(clean_avail["c2026_02"], geant4=151_000)}
    assert feasible_sets(up, masking)["c2026_02"]["allnew"] \
        != ARM_FEASIBILITY["c2026_02"]["allnew"]
    down = {"c2026_02": dict(clean_avail["c2026_02"], mathlib=1_000)}
    assert feasible_sets(down, masking)["c2026_02"]["allnew"] \
        != ARM_FEASIBILITY["c2026_02"]["allnew"]


def test_analyzer_preflight_floor_consistency():
    """The analyzer applies the SAME masking floors as the feasibility
    gate (single-source consistency; analyze_v2 also asserts this at
    import time) — drift would let a cell pass the gate and fail
    analysis, or vice versa."""
    import analyze_v2
    assert (analyze_v2.MASK_MIN_DOCS, analyze_v2.MASK_MIN_BYTES) \
        == (MASK_MIN_DOCS, MASK_MIN_BYTES)


def test_grid_unchanged_by_amendment():
    """The amendment is gate/claims-level ONLY: 216 total / 152 small-mid
    / 44 sentinel cells with unique outputs, exactly as frozen."""
    from run_phase1 import cell_out, jobs
    J = list(jobs())
    p12 = [j for j in J if j[0] <= 2]
    sent = [j for j in p12 if j[2] == "q25c-0.5b"]
    assert (len(J), len(p12), len(sent)) == (216, 152, 44)
    assert len({cell_out(s, c, k, f) for _, _, s, c, k, _, f in J}) == 216


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("FEASIBILITY TESTS PASS")
