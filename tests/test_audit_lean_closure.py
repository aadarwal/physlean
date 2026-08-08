#!/usr/bin/env python3
"""Independent raw-.ilean closure audit regression tests."""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit_lean_closure import AuditError, _definition_state, audit


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _ck(module, name):
    return json.dumps({"c": {"m": module, "n": name}},
                      separators=(",", ":"), sort_keys=True)


def _fixture(td):
    sa, sb = os.path.join(td, "A.lean"), os.path.join(td, "B.lean")
    ia, ib = os.path.join(td, "A.ilean"), os.path.join(td, "B.ilean")
    open(sa, "w").write("theorem A.t : True := trivial\n")
    open(sb, "w").write("def B.u := 1\n")
    refs_a = {
        _ck("B", "B.u"): {"definition": None,
                            "usages": [[0, 0, 0, 1, "A.t"]]},
        _ck("B", "B.gen"): {"definition": None,
                              "usages": [[0, 1, 0, 2, "A.t"]]},
        _ck("B", "B.lost"): {"definition": None,
                               "usages": [[0, 2, 0, 3, "A.t"]]},
        _ck("Ext.Mod", "Ext.z"): {"definition": None,
                                   "usages": [[0, 3, 0, 4, "A.t"]]},
        _ck("A", "A.t"): {"definition": None,
                            "usages": [[0, 4, 0, 5, "A.t"]]},
    }
    refs_b = {
        # length-4 parentless generated definition lies uniquely inside B.u
        _ck("B", "B.gen"): {"definition": [0, 4, 0, 7],
                              "usages": []},
    }
    _write_json(ia, dict(version=5, module="A", directImports=[],
                         decls={"A.t": [0] * 8}, references=refs_a))
    _write_json(ib, dict(version=5, module="B", directImports=[],
                         decls={"B.u": [0, 0, 0, 12, 0, 4, 0, 7]},
                         references=refs_b))
    pairs = dict(schema="v2a_ilean_pairs_v2", pairs=[])
    for module, source, ilean in (("A", sa, ia), ("B", sb, ib)):
        pairs["pairs"].append(dict(
            module=module, match_kind="exact", source=source, ilean=ilean,
            source_sha256=_sha(source), ilean_sha256=_sha(ilean)))
    pp = os.path.join(td, "pairs.json")
    _write_json(pp, pairs)
    extraction = dict(
        schema="v2a_lean_extract_v3", repo="r",
        pairs_manifest_sha256=_sha(pp),
        graph=dict(
            edges=[["A", "A.t", "B", "B.u"]],
            external_reference_edges=[
                ["A", "A.t", "Ext.Mod", "Ext.z"]],
            external_ref_counts_by_target={"A": {"A.t": 1}},
            internal_unrenderable_references=[
                ["A", "A.t", "B", "B.lost"]],
            internal_renderability_by_target={"A": {"A.t": dict(
                n_internal_occurrences=3,
                n_renderable_occurrences=2,
                n_unrenderable_occurrences=1,
                coverage=2 / 3)}}))
    validation = dict(
        summary=dict(schema="v2a_lean_extract_v3", repo="r"),
        targets=[dict(identity=["A", "A.t"])])
    return extraction, validation, pp


def test_exact_raw_partition_matches():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, pairs = _fixture(td)
        report = audit(extraction, validation, pairs)
        assert report["summary"]["n_passed"] == 1
        assert report["summary"]["elaborator_closure_check"] == "PASS"
        row = report["targets"][0]
        assert row["match"] is True
        assert row["raw"]["edges"] == [["A", "A.t", "B", "B.u"]]


def test_graph_disagreement_fails_target():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, pairs = _fixture(td)
        extraction["graph"]["edges"] = []
        report = audit(extraction, validation, pairs)
        assert report["summary"]["n_failed"] == 1
        assert report["summary"]["elaborator_closure_check"] == "FAIL"
        assert report["targets"][0]["match"] is False


def test_foreign_declinfo_partition_disagreement_is_global_failure():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, pairs = _fixture(td)
        extraction["foreign_declaration_infos_by_module"] = {
            "A": [dict(name="ghost", defining_modules=["Ext.Mod"])]}
        report = audit(extraction, validation, pairs)
        summary = report["summary"]
        assert summary["n_failed"] == 1
        assert summary["global_failures"] == [
            "foreign-declaration-info-partition"]
        assert summary["foreign_declaration_info_partition_match"] is False
        assert summary["elaborator_closure_check"] == "FAIL"


def test_hash_and_schema_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        extraction, validation, pairs = _fixture(td)
        extraction["schema"] = "old"
        try:
            audit(extraction, validation, pairs)
            assert False, "old extraction schema accepted"
        except AuditError as err:
            assert "schema" in str(err)
        extraction["schema"] = "v2a_lean_extract_v3"
        open(json.load(open(pairs))["pairs"][0]["source"], "a").write("--x")
        try:
            audit(extraction, validation, pairs)
            assert False, "drifted source accepted"
        except AuditError as err:
            assert "changed" in str(err)


def test_foreign_declinfo_partition_is_independently_identity_checked():
    with tempfile.TemporaryDirectory() as td:
        source = os.path.join(td, "Attr.lean")
        ilean = os.path.join(td, "Attr.ilean")
        open(source, "w").write("attribute [simp] foreign_t\n")
        raw = dict(version=5, module="T.Attr", directImports=[],
                   decls={"foreign_t": [99, 0, 99, 9, 99, 0, 99, 9]},
                   references={_ck("Init.Core", "foreign_t"): dict(
                       definition=None,
                       usages=[[0, 17, 0, 26, "foreign_t"]])})
        _write_json(ilean, raw)
        pair = dict(source=source, ilean=ilean)
        decls, _, foreign = _definition_state({"T.Attr": pair})
        assert decls == {"T.Attr": set()}
        assert foreign == {
            "T.Attr": {"foreign_t": ["Init.Core"]}}

        raw["references"] = {_ck("T.Attr", "foreign_t"): dict(
            definition=None, usages=[[0, 17, 0, 26, "foreign_t"]])}
        _write_json(ilean, raw)
        try:
            _definition_state({"T.Attr": pair})
            assert False, "out-of-range current-module DeclInfo accepted"
        except AuditError as err:
            assert "invalid LSP position" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("RAW-ILEAN AUDIT TESTS PASS")
