#!/usr/bin/env python3
"""Synthetic tests for the §15.A13 k4x external-graph generator: exact
lake-manifest pin extraction, the identical definition-parents fold,
resolution partitioning (direct/folded/unresolved/out-of-snapshot), and
fail-closed artifact construction. No corpus or cluster path is touched.
Run: python3 tests/test_prepare_v2b_k4x_graph.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prepare_v2b_k4x_graph import (K4X_EXTERNAL_EXTRACTION_REPO,
                                   K4X_EXTERNAL_REVISION, build_k4x_graph,
                                   lake_manifest_mathlib_rev,
                                   resolve_external_references)
from v2b_common import K4X_GRAPH_SCHEMA, V2BError


def _manifest_bytes(rev=K4X_EXTERNAL_REVISION):
    return json.dumps(dict(packages=[
        dict(name="batteries", rev="b" * 40),
        dict(name="mathlib", rev=rev)])).encode("utf-8")


def test_lake_manifest_pin_extraction_is_exact():
    assert lake_manifest_mathlib_rev(
        json.loads(_manifest_bytes())) == K4X_EXTERNAL_REVISION
    for bad in (dict(), dict(packages=[]),
                dict(packages=[dict(name="mathlib", rev="a" * 40),
                               dict(name="mathlib", rev="c" * 40)]),
                dict(packages=[dict(name="mathlib", rev="")])):
        try:
            lake_manifest_mathlib_rev(bad)
            assert False, bad
        except V2BError:
            pass


def _physlib_extraction(edges):
    return dict(schema="v2a_lean_extract_v3", repo="physlib",
                files=[dict(module="Physlib.A",
                            decls={"Physlib.A.t": {}, "Physlib.A.dep": {}},
                            definition_parents={})],
                graph=dict(edges=[], external_reference_edges=edges))


def _external_extraction():
    return dict(
        schema="v2a_lean_extract_v3", repo=K4X_EXTERNAL_EXTRACTION_REPO,
        files=[dict(module="Mathlib.X",
                    decls={"Mathlib.X.foo": {}, "Mathlib.X.bar": {}},
                    definition_parents={
                        # generated chain: gen2 -> gen1 -> foo (folds)
                        "Mathlib.X.gen1": "Mathlib.X.foo",
                        "Mathlib.X.gen2": "Mathlib.X.gen1",
                        # cycle: never resolves
                        "Mathlib.X.loopA": "Mathlib.X.loopB",
                        "Mathlib.X.loopB": "Mathlib.X.loopA"})])


def test_resolution_partitions_exactly():
    edges = [
        ["Physlib.A", "Physlib.A.t", "Mathlib.X", "Mathlib.X.foo"],
        ["Physlib.A", "Physlib.A.t", "Mathlib.X", "Mathlib.X.gen2"],
        ["Physlib.A", "Physlib.A.dep", "Mathlib.X", "Mathlib.X.loopA"],
        ["Physlib.A", "Physlib.A.t", "Std.Y", "Std.Y.z"],
        ["Physlib.A", "Physlib.A.t", "Init.Core", "Init.Core.id"],
    ]
    out = resolve_external_references(_physlib_extraction(edges),
                                      _external_extraction())
    assert out["n_raw_external_reference_edges"] == 5
    assert out["n_resolved_direct"] == 1
    assert out["n_resolved_folded"] == 1          # gen2 -> gen1 -> foo
    assert out["n_unresolved"] == 1               # loopA cycle
    assert out["n_out_of_snapshot"] == 2          # Std + Init
    assert out["out_of_snapshot_by_root"] == {"Std": 1, "Init": 1}
    assert out["unresolved_by_target"] == {"Physlib.A":
                                           {"Physlib.A.dep": 1}}
    resolved = {tuple(edge) for edge in out["resolved_edges"]}
    assert ("Physlib.A", "Physlib.A.t", "Mathlib.X", "Mathlib.X.foo",
            "direct") in resolved
    # the folded reference lands on the SPANNED parent, deduplicating
    # onto the direct edge is impossible here because provenance differs
    assert ("Physlib.A", "Physlib.A.t", "Mathlib.X", "Mathlib.X.foo",
            "folded") in resolved


def test_resolution_fails_closed():
    # source is not a physlib unit
    edges = [["Physlib.B", "Physlib.B.ghost", "Mathlib.X",
              "Mathlib.X.foo"]]
    try:
        resolve_external_references(_physlib_extraction(edges),
                                    _external_extraction())
        assert False, "foreign quadruple source accepted"
    except V2BError as err:
        assert "not a physlib unit" in str(err)
    # module namespace overlap between the two extractions
    overlapping = _external_extraction()
    overlapping["files"].append(dict(module="Physlib.A", decls={"x": {}},
                                     definition_parents={}))
    try:
        resolve_external_references(
            _physlib_extraction([["Physlib.A", "Physlib.A.t",
                                  "Mathlib.X", "Mathlib.X.foo"]]),
            overlapping)
        assert False, "module namespace overlap accepted"
    except V2BError as err:
        assert "overlap" in str(err)
    # no preserved quadruples at all
    try:
        resolve_external_references(_physlib_extraction([]),
                                    _external_extraction())
        assert False, "empty quadruple list accepted"
    except V2BError:
        pass


def test_build_k4x_graph_binds_and_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        physlib_path = os.path.join(td, "physlib.json")
        external_path = os.path.join(td, "external.json")
        json.dump(_physlib_extraction(
            [["Physlib.A", "Physlib.A.t", "Mathlib.X",
              "Mathlib.X.foo"]]), open(physlib_path, "w"))
        json.dump(_external_extraction(), open(external_path, "w"))
        artifact = build_k4x_graph(physlib_path, external_path,
                                   _manifest_bytes())
        assert artifact["schema"] == K4X_GRAPH_SCHEMA
        assert artifact["external_revision"] == K4X_EXTERNAL_REVISION
        assert artifact["resolution"]["n_resolved_edges"] == 1
        assert artifact["physlib_extraction"]["sha256"]
        assert artifact["external_extraction"]["sha256"]
        # wrong manifest pin
        try:
            build_k4x_graph(physlib_path, external_path,
                            _manifest_bytes(rev="1" * 40))
            assert False, "wrong lake-manifest pin accepted"
        except V2BError as err:
            assert "frozen" in str(err)
        # wrong snapshot repo tag
        wrong = _external_extraction()
        wrong["repo"] = "mathlib4"
        wrong_path = os.path.join(td, "wrong.json")
        json.dump(wrong, open(wrong_path, "w"))
        try:
            build_k4x_graph(physlib_path, wrong_path, _manifest_bytes())
            assert False, "wrong snapshot repo tag accepted"
        except V2BError as err:
            assert "repo tag" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B K4X GRAPH TESTS PASS")
