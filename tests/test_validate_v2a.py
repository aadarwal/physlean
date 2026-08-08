#!/usr/bin/env python3
"""V2-a validation-driver tests (stdlib, GPU-free): eligibility filter
(§2 kinds + containment), under-filled hard failure, repo-tag identity,
and the honest-gate accounting (§10 NOT-RUN checks; §14.19 strata
recorded as missing — never silently claimed).
Run: python3 tests/test_validate_v2a.py"""
import hashlib, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from validate_v2a import lean_exclusions, validate


def _lean_ex(td, n_ok=3, repo="mathlib"):
    src = "theorem t : True := trivial\n" * 40
    sp = os.path.join(td, "M.lean")
    open(sp, "w").write(src)
    sha = hashlib.sha256(src.encode()).hexdigest()
    decls = {}
    for i in range(n_ok):
        decls[f"M.t{i}"] = dict(
            start_byte=28 * i, end_byte=28 * i + 27,
            sel_start_byte=28 * i + 8, sel_end_byte=28 * i + 9,
            selection_contained=True, kind="theorem",
            kind_token="theorem", eligible_kind=True,
            header_bytes=17, body_bytes=10, split_kind=":=", shell=[])
    # one excluded kind, one uncontained, one splitless — all counted
    decls["M.inst"] = dict(start_byte=0, end_byte=27, sel_start_byte=0,
                           sel_end_byte=1, selection_contained=True,
                           kind="instance", kind_token="instance",
                           eligible_kind=False, header_bytes=27,
                           body_bytes=0, split_kind=None, shell=[])
    decls["M.unc"] = dict(start_byte=0, end_byte=27, sel_start_byte=900,
                          sel_end_byte=901, selection_contained=False,
                          kind="theorem", kind_token="theorem",
                          eligible_kind=True, header_bytes=20,
                          body_bytes=7, split_kind=":=", shell=[])
    decls["M.nos"] = dict(start_byte=0, end_byte=27, sel_start_byte=0,
                          sel_end_byte=1, selection_contained=True,
                          kind="def", kind_token="def",
                          eligible_kind=True, header_bytes=27,
                          body_bytes=0, split_kind=None, shell=[])
    return dict(
        schema="v2a_lean_extract_v2", repo=repo,
        k4_closure_definition="source-reference",
        files=[dict(module="M", source=sp, source_sha256=sha,
                    decls=decls, references=[])],
        graph=dict(edges=[], n_same_file=0, n_cross_file=0,
                   external_by_root={}, n_internal_unrenderable=0,
                   n_folded_generated=7,
                   external_reference_edges=[
                       ["M", "M.t0", "Mathlib.Data.Nat", "Nat.succ"]],
                   external_ref_counts_by_target={"M": {"M.t0": 3}},
                   internal_unrenderable_by_module={},
                   internal_renderability_by_target={
                       "M": {"M.t0": dict(
                           n_internal_occurrences=4,
                           n_renderable_occurrences=3,
                           n_unrenderable_occurrences=1,
                           coverage=0.75)}},
                   parent_decl_coverage={}))


def test_eligibility_and_exclusion_accounting():
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td, n_ok=3)
        rep = validate(ex, "mathlib", 3)
        s = rep["summary"]
        assert s["n_eligible"] == 3 and s["n_selected"] == 3
        assert s["n_failures"] == 0
        exc = s["exclusions"]
        assert exc["kind_excluded"] == 1
        assert exc["selection_uncontained"] == 1
        assert exc["no_split"] == 1
        assert exc["kind_histogram"]["theorem"] == 4
        assert exc["kind_histogram"]["instance"] == 1
        assert s["n_folded_generated"] == 7
        assert s["n_external_reference_edges"] == 1
        by_name = {t["name"]: t for t in rep["targets"]}
        assert by_name["M.t0"]["external_ref_occurrences"] == 3
        assert by_name["M.t0"]["identity"] == ["M", "M.t0"]
        assert by_name["M.t0"]["internal_renderability"]["coverage"] == 0.75


def test_underfilled_selection_hard_fails():
    """Review fix: fewer eligible targets than requested previously
    exited 0 — it must be a counted failure."""
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td, n_ok=2)
        rep = validate(ex, "mathlib", 20)
        s = rep["summary"]
        assert s["n_selected"] == 2
        assert any(f.startswith("insufficient-eligible:2<20")
                   for f in s["failures"])
        assert s["n_failures"] >= 1


def test_repo_tag_mismatch_fails():
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td, repo="corpora/mathlib4")
        rep = validate(ex, "physlib", 1)
        assert any(f.startswith("repo-tag-mismatch")
                   for f in rep["summary"]["failures"])
        # basename matching accepted: tag mathlib4 vs path corpora/mathlib4
        rep2 = validate(ex, "mathlib4", 1)
        assert not any(f.startswith("repo-tag-mismatch")
                       for f in rep2["summary"]["failures"])


def test_honest_gate_accounting():
    """§10 checks not run must read NOT-RUN with gate_complete False,
    and the sampling caveat must state strata are NOT implemented."""
    with tempfile.TemporaryDirectory() as td:
        rep = validate(_lean_ex(td), "mathlib", 1)
        s = rep["summary"]
        g = s["design_v2_s10"]
        assert g["extraction_validation"] == "RUN"
        assert g["standalone_compile"] == "NOT-RUN"
        assert g["elaborator_closure_check"] == "NOT-RUN"
        assert s["gate_complete"] is False
        assert "WITHOUT" in s["sampling"] and "strata" in s["sampling"]


def test_schema_n_and_closure_identity_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td)
        try:
            validate(dict(ex, schema="v2a_mystery"), "mathlib", 1)
            assert False, "unknown schema accepted"
        except ValueError as err:
            assert "schema" in str(err)
        rep = validate(ex, "mathlib", 0)
        assert any(f.startswith("invalid-n-targets")
                   for f in rep["summary"]["failures"])
        rep = validate(dict(ex, k4_closure_definition="kernel-premise"),
                       "mathlib", 1)
        assert any(f.startswith("k4-closure-definition-mismatch")
                   for f in rep["summary"]["failures"])


def test_python_header_body_partition_is_rechecked():
    with tempfile.TemporaryDirectory() as td:
        src = b"def f():\n    return 1\n"
        sp = os.path.join(td, "p.py")
        open(sp, "wb").write(src)
        target = dict(start_byte=0, end_byte=len(src) - 1,
                      body_start_byte=8, header_bytes=8,
                      body_bytes=len(src) - 1 - 8, docstring_bytes=0,
                      kind="FunctionDef")
        ex = dict(schema="v2a_python_extract_v2", repo="pyrepo",
                  n_failed=0,
                  files=[dict(module="p", rel="p.py", source=sp,
                              source_sha256=hashlib.sha256(src).hexdigest(),
                              targets={"f": target})],
                  graph=dict(edges=[], n_same_file=0, n_cross_file=0,
                             external_by_root={}, target_coverage={}))
        good = validate(ex, "pyrepo", 1)
        assert good["summary"]["n_failures"] == 0
        target["body_bytes"] -= 1
        bad = validate(ex, "pyrepo", 1)
        assert bad["summary"]["n_failures"] == 1
        assert bad["targets"][0]["roundtrip_ok"] is False


def test_python_failed_files_make_partial_graph_fail_gate():
    with tempfile.TemporaryDirectory() as td:
        src = b"def f():\n    return 1\n"
        sp = os.path.join(td, "p.py")
        open(sp, "wb").write(src)
        target = dict(start_byte=0, end_byte=len(src) - 1,
                      body_start_byte=8, header_bytes=8,
                      body_bytes=len(src) - 1 - 8, docstring_bytes=0,
                      kind="FunctionDef")
        ex = dict(schema="v2a_python_extract_v2", repo="pyrepo",
                  n_failed=1, failed=[dict(rel="bad.py", error="syntax")],
                  files=[dict(module="p", rel="p.py", source=sp,
                              source_sha256=hashlib.sha256(src).hexdigest(),
                              import_errors=[], targets={"f": target})],
                  graph=dict(edges=[], n_same_file=0, n_cross_file=0,
                             external_by_root={}, target_coverage={}))
        rep = validate(ex, "pyrepo", 1)
        assert "failed-source-files:1" in rep["summary"]["failures"]


def test_roundtrip_failure_counted():
    """A live source drift (sha mismatch) is a per-target failure."""
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td, n_ok=1)
        open(ex["files"][0]["source"], "a").write("-- drift\n")
        rep = validate(ex, "mathlib", 1)
        assert rep["summary"]["n_failures"] >= 1
        assert rep["targets"][0]["roundtrip_ok"] is False


def test_live_lean_kind_is_recomputed_not_trusted():
    with tempfile.TemporaryDirectory() as td:
        ex = _lean_ex(td, n_ok=1)
        ex["files"][0]["decls"]["M.t0"]["kind"] = "def"
        rep = validate(ex, "mathlib", 1)
        assert rep["summary"]["n_failures"] == 1
        assert "recomputation differs" in rep["targets"][0]["error"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("VALIDATE-V2A TESTS PASS")
