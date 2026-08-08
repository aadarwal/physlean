#!/usr/bin/env python3
"""Pure adversarial tests for exact Lake ModuleSetup planning/parsing."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prepare_v2b_lean_setups import (
    SETUP_INDEX_SCHEMA, _safe_module_relpath, extraction_modules,
    parse_query_stdout, setup_artifact_roles, validate_setup)
from v2b_common import V2BError, sha256_file


def _setup(name):
    return dict(dynlibs=[], importArts={}, isModule=True, name=name,
                options={"autoImplicit": False}, package="fixture",
                plugins=[])


def _expect_failure(call, fragment):
    try:
        call()
        assert False, f"accepted invalid input expected to mention {fragment}"
    except V2BError as err:
        assert fragment in str(err), str(err)


def test_setup_query_rows_are_exact_ordered_and_duplicate_key_free():
    modules = ["M.A", "M.B"]
    stdout = "\n".join(json.dumps(_setup(name), separators=(",", ":"))
                       for name in modules) + "\n"
    values = parse_query_stdout(stdout, modules)
    assert [value["name"] for value in values] == modules
    assert validate_setup(values[0], "M.A", "fixture") is values[0]

    _expect_failure(lambda: parse_query_stdout(stdout, modules[:1]),
                    "setup rows")
    swapped = "\n".join(json.dumps(_setup(name))
                         for name in reversed(modules))
    _expect_failure(lambda: parse_query_stdout(swapped, modules),
                    "malformed ModuleSetup")
    duplicate = ('{"dynlibs":[],"importArts":{},"isModule":true,'
                 '"name":"M.A","name":"M.A","options":{},'
                 '"plugins":[]}')
    _expect_failure(lambda: parse_query_stdout(duplicate, ["M.A"]),
                    "duplicate ModuleSetup key")
    nonfinite = json.dumps(_setup("M.A")).replace(
        '"options": {"autoImplicit": false}',
        '"options": {"x": NaN}')
    _expect_failure(lambda: parse_query_stdout(nonfinite, ["M.A"]),
                    "non-finite")
    extra = dict(_setup("M.A"), unbound="drift")
    _expect_failure(lambda: parse_query_stdout(json.dumps(extra), ["M.A"]),
                    "key drift")
    with_plugins = _setup("M.A")
    with_plugins["plugins"] = [
        "/pool/libSimple.so",
        {"path": "/pool/libCustom.so", "initFn": "initialize_custom"},
        {"path": "/pool/libDefault.so", "initFn": None},
    ]
    assert parse_query_stdout(json.dumps(with_plugins), ["M.A"])[0] == \
        with_plugins
    with_imports = _setup("M.A")
    with_imports["imports"] = [dict(
        module="Lean", importAll=False, isExported=True, isMeta=False)]
    assert parse_query_stdout(json.dumps(with_imports), ["M.A"])[0] == \
        with_imports
    for plugin in ({"path": ""}, {"path": "/x", "extra": 1},
                   {"path": "/x", "initFn": ""}, 7):
        invalid = _setup("M.A")
        invalid["plugins"] = [plugin]
        _expect_failure(
            lambda value=invalid: parse_query_stdout(
                json.dumps(value), ["M.A"]),
            "malformed ModuleSetup")


def test_extraction_module_map_binds_unique_live_sources():
    with tempfile.TemporaryDirectory() as td:
        corpus = os.path.join(td, "corpus")
        os.makedirs(os.path.join(corpus, "M"))
        a = os.path.join(corpus, "M", "A.lean")
        b = os.path.join(corpus, "M", "B.lean")
        open(a, "w", encoding="utf-8").write("def a := 1\n")
        open(b, "w", encoding="utf-8").write("def b := 2\n")
        extraction = dict(
            schema="v2a_lean_extract_v3", repo="fixture",
            files=[
                dict(module="M.B", source=b, source_sha256=sha256_file(b)),
                dict(module="M.A", source=a, source_sha256=sha256_file(a)),
            ])
        path = os.path.join(td, "extraction.json")
        json.dump(extraction, open(path, "w", encoding="utf-8"))
        binding, repo, rows = extraction_modules(path, corpus)
        assert len(binding["sha256"]) == 64
        assert repo == "fixture"
        assert [row["module"] for row in rows] == ["M.A", "M.B"]
        assert [row["source_rel"] for row in rows] == \
            ["M/A.lean", "M/B.lean"]

        extraction["files"][1]["module"] = "M.B"
        json.dump(extraction, open(path, "w", encoding="utf-8"))
        _expect_failure(lambda: extraction_modules(path, corpus),
                        "duplicate module/source")


def test_module_setup_output_paths_cannot_escape():
    assert _safe_module_relpath("Mathlib.Algebra.Basic") == os.path.join(
        "Mathlib", "Algebra", "Basic.setup.json")
    for bad in ("", ".Bad", "Bad.", "Bad..Name", "../Bad", "Bad/Name",
                "Bad\\Name", "Bad\x00Name"):
        _expect_failure(lambda value=bad: _safe_module_relpath(value),
                        "module name")
    assert SETUP_INDEX_SCHEMA == "v2b_lean_setup_index_v1"


def test_setup_artifact_closure_covers_imports_plugins_and_dynlibs():
    value = _setup("M.A")
    value["importArts"] = {
        "Init": ["/pool/Init.olean", "/pool/Init.ir"],
        "Lean": ["/pool/Lean.olean"]}
    value["dynlibs"] = ["/pool/runtime.so"]
    value["plugins"] = [
        "/pool/simple-plugin.so",
        {"path": "/pool/custom-plugin.so", "initFn": "initialize_custom"}]
    roles = setup_artifact_roles(value, "fixture")
    assert roles["/pool/Init.olean"] == {"import-artifact"}
    assert roles["/pool/runtime.so"] == {"dynamic-library"}
    assert roles["/pool/simple-plugin.so"] == {"plugin"}
    assert roles["/pool/custom-plugin.so"] == {"plugin"}
    invalid = _setup("M.A")
    invalid["importArts"] = {"Init": [""]}
    _expect_failure(lambda: setup_artifact_roles(invalid, "fixture"),
                    "malformed importArts")
    relative = _setup("M.A")
    relative["dynlibs"] = ["relative.so"]
    _expect_failure(lambda: setup_artifact_roles(relative, "relative"),
                    "relative dynamic-library")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN SETUP TESTS PASS")
