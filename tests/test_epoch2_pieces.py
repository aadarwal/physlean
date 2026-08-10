#!/usr/bin/env python3
"""Epoch-2 pieces: replication gate truth table, launcher contracts."""
import contextlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analyze_v2b_interior as intr  # noqa: E402
from v2b_common import V2BError  # noqa: E402

LN2 = math.log(2)


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


def _cell(bpb, scored=1000, eligible=True):
    nll = bpb * LN2 * scored
    return {"primary": {"nll_nats": nll, "bpb": nll / LN2 / scored},
            "boundary_ledger": {"scored_body_bytes": scored},
            "eligible": eligible}


KEY = json.dumps(["ModA", "declX"], separators=(",", ":"))


def _cells(k4_bpb):
    return {"k1": _cell(1.0), "k4:16384": _cell(k4_bpb),
            "k3:16384": _cell(k4_bpb + 0.05),
            "k5:0:16384": _cell(k4_bpb + 0.1),
            "k4:8192": _cell(k4_bpb + 0.02)}


class _Row(dict):
    pass


def _manifest_rows(cells_map):
    # replication_gate only compares _grid_16k(manifest_row); build rows
    # whose _target_cell_rows-derived grids are equal by construction by
    # monkeypatching the grid helper to echo a stable projection.
    return {key: _Row(key=key) for key in cells_map}


def test_replication_gate_truth_table():
    original = intr._grid_16k
    intr._grid_16k = lambda row: [{"cell_id": "k4:16384"}]
    try:
        icells = {KEY: _cells(0.8)}
        pcells = {KEY: _cells(0.8)}
        rows = _manifest_rows(icells)
        report = intr.replication_gate(rows, rows, "envA", "envA",
                                       icells, pcells)
        assert report["status"] == "replicated-exactly"
        assert report["n_compared"] >= 4  # k1 + three 16384 cells

        report = intr.replication_gate(rows, rows, "envA", "envB",
                                       icells, pcells)
        assert report["status"] == "discarded-non-comparable"
        assert report["reason"] == "environment-fingerprint-differs"

        report = intr.replication_gate(rows, rows, "envA", "envA",
                                       icells, {})
        assert report["status"] == "replicated-with-per-target-discards"
        assert report["discarded_targets"][0]["reason"] == \
            "pilot-lacks-target"

        # eligibility mismatch discards, never raises
        p2 = {KEY: _cells(0.8)}
        p2[KEY]["k4:16384"]["eligible"] = False
        report = intr.replication_gate(rows, rows, "envA", "envA",
                                       icells, p2)
        assert report["discarded_targets"][0]["reason"] == \
            "eligibility-differs:k4:16384"

        # bpb inequality under equal preconditions = incident
        p3 = {KEY: _cells(0.80001)}
        with _expect(V2BError, "REPLICATION FAILURE"):
            intr.replication_gate(rows, rows, "envA", "envA", icells, p3)
    finally:
        intr._grid_16k = original


def test_grid_mismatch_discards():
    calls = {"n": 0}

    def alternating(row):
        calls["n"] += 1
        return [{"cell_id": f"g{calls['n']}"}]

    original = intr._grid_16k
    intr._grid_16k = alternating
    try:
        icells = {KEY: _cells(0.8)}
        rows = _manifest_rows(icells)
        report = intr.replication_gate(rows, rows, "e", "e", icells,
                                       {KEY: _cells(0.8)})
        assert report["discarded_targets"][0]["reason"] == \
            "metadata-grid-differs"
    finally:
        intr._grid_16k = original


def test_interior_constants_and_launchers():
    assert intr.PINNED_INTERIOR_SCORING_TREE is None  # pin-commit fills
    assert set(intr.PINNED_INTERIOR_MANIFEST_SHA256) == \
        {"mathlib4", "sympy"}
    src = open(os.path.join(ROOT, "slurm",
                            "v2b_paired_interior.sbatch")).read()
    for sha in intr.PINNED_INTERIOR_MANIFEST_SHA256.values():
        assert sha in src
    assert "expandable_segments" in src  # 32b arm keeps its allocator rule
    bsrc = open(os.path.join(ROOT, "slurm",
                             "battery_epoch.sbatch")).read()
    assert "rebind first" in bsrc  # committed-at-path refusal
    ssrc = open(os.path.join(ROOT, "slurm",
                             "v2b_assembly_supplement.sbatch")).read()
    assert "job19975833_0_mathlib4" in ssrc  # pinned boundary input
    from finalize_v2b_supplement_sample import BOUND_SAMPLE_SCHEMA, \
        SUPPLEMENT_N
    assert BOUND_SAMPLE_SCHEMA == "v2b_bound_sample_v2"
    assert SUPPLEMENT_N == 120


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("EPOCH2 PIECES TESTS PASS")
