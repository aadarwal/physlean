#!/usr/bin/env python3
"""Pure adversarial tests for exact Lake ModuleSetup planning/parsing."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prepare_v2b_lean_setups import (
    SETUP_INDEX_SCHEMA, _artifact_rows, _safe_module_relpath,
    extraction_modules, parse_lake_environment, parse_query_stdout,
    runtime_search_closure, setup_artifact_roles, validate_setup)
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


def test_lake_environment_projection_is_exact_and_nul_terminated():
    raw = ("PATH=/pool/bin:/usr/bin\0LEAN_SRC_PATH=/pool/src\0"
           "LEAN_PATH=/pool/lib/lean\0LD_LIBRARY_PATH=/pool/lib\0"
           "UNBOUND_SECRET=not-persisted\0")
    projection = parse_lake_environment(raw, "fixture")
    assert projection == {
        "LEAN_PATH": "/pool/lib/lean",
        "LEAN_SRC_PATH": "/pool/src",
        "LD_LIBRARY_PATH": "/pool/lib",
        "DYLD_LIBRARY_PATH": None,
        "PATH": "/pool/bin:/usr/bin",
    }
    _expect_failure(lambda: parse_lake_environment(raw[:-1], "fixture"),
                    "terminal NUL")
    _expect_failure(lambda: parse_lake_environment(
        raw + "PATH=/different\0", "fixture"), "duplicate")


def test_extraction_module_map_binds_unique_live_sources():
    with tempfile.TemporaryDirectory() as td:
        corpus = os.path.realpath(os.path.join(td, "corpus"))
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
    assert SETUP_INDEX_SCHEMA == "v2b_lean_setup_index_v2"


def test_setup_artifact_closure_covers_imports_plugins_and_dynlibs():
    value = _setup("M.A")
    value["importArts"] = {
        "Init": [["/pool/Init.olean", "/pool/Init.olean.server"],
                 ["/pool/Init.ir.sig", "/pool/Init.ir"]],
        "Lean": [["/pool/Lean.olean"]]}
    value["dynlibs"] = ["/pool/runtime.so"]
    value["plugins"] = [
        "/pool/simple-plugin.so",
        {"path": "/pool/custom-plugin.so", "initFn": "initialize_custom"}]
    roles = setup_artifact_roles(value, "fixture")
    assert roles["/pool/Init.olean"] == {"import-artifact"}
    assert roles["/pool/runtime.so"] == {"dynamic-library"}
    assert roles["/pool/simple-plugin.so"] == {"plugin"}
    assert roles["/pool/custom-plugin.so"] == {"plugin"}
    lean432 = _setup("M.B")
    lean432["importArts"] = {
        "Init": ["/pool/432/Init.olean", "/pool/432/Init.ir"]}
    roles432 = setup_artifact_roles(lean432, "lean-4.32")
    assert roles432["/pool/432/Init.olean"] == {"import-artifact"}
    invalid = _setup("M.A")
    invalid["importArts"] = {"Init": [[""]]}
    _expect_failure(lambda: setup_artifact_roles(invalid, "fixture"),
                    "malformed ModuleSetup")
    mixed = _setup("M.A")
    mixed["importArts"] = {
        "Init": ["/pool/Init.olean", ["/pool/Init.ir"]]}
    _expect_failure(lambda: validate_setup(mixed, "M.A", "mixed"),
                    "malformed ModuleSetup")
    relative = _setup("M.A")
    relative["dynlibs"] = ["relative.so"]
    _expect_failure(lambda: setup_artifact_roles(relative, "relative"),
                    "relative dynamic-library")
    assert setup_artifact_roles(_setup("M.Empty"), "empty") == {}
    assert _artifact_rows({}) == []


def test_runtime_search_closure_binds_files_directories_missing_and_links():
    with tempfile.TemporaryDirectory() as td:
        td = os.path.realpath(td)
        corpus = os.path.realpath(os.path.join(td, "corpus"))
        corpus_lake = os.path.join(corpus, ".lake")
        lean_root = os.path.join(corpus_lake, "build", "lib", "lean")
        dynamic_root = os.path.join(corpus_lake, "build", "lib")
        toolchain = os.path.realpath(os.path.join(td, "toolchain"))
        lean = os.path.join(toolchain, "bin", "lean")
        tool_lean = os.path.join(toolchain, "lib", "lean")
        tool_lib = os.path.join(toolchain, "lib")
        for path in (lean_root, tool_lean, os.path.dirname(lean)):
            os.makedirs(path, exist_ok=True)
        def _write(path, text):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        _write(lean, "lean")
        _write(os.path.join(lean_root, "A.olean"), "a")
        _write(os.path.join(lean_root, "A.olean.private"), "private")
        _write(os.path.join(lean_root, "ignored.c"), "ignored")
        _write(os.path.join(tool_lean, "Init.ir"), "ir")
        runtime = os.path.join(tool_lib, "libLeanRuntime.so.1")
        _write(runtime, "runtime")
        link = os.path.join(tool_lib, "libLeanRuntime.so")
        os.symlink("libLeanRuntime.so.1", link)
        missing = os.path.join(corpus_lake, "packages", "Missing",
                               ".lake", "build", "lib", "lean")
        environment = dict(
            LEAN_PATH=os.pathsep.join((lean_root, missing, tool_lean)),
            LEAN_SRC_PATH=os.path.join(corpus, "src"),
            LD_LIBRARY_PATH=os.pathsep.join((dynamic_root, tool_lib)),
            DYLD_LIBRARY_PATH=None, PATH="/usr/bin")
        closure = runtime_search_closure(
            environment, corpus, lean, "fixture")
        assert [row["state"] for row in closure["roots"]].count(
            "missing") == 1
        assert lean_root in closure["directories"]
        assert os.path.join(lean_root, "ignored.c") not in \
            closure["artifact_roles"]
        assert closure["artifact_roles"][
            os.path.join(lean_root, "A.olean.private")] == {
                "lean-search-artifact"}
        assert closure["artifact_roles"][runtime] == {
            "dynamic-search-artifact"}
        assert closure["artifact_roles"][link] == {
            "dynamic-search-artifact"}
        assert closure["symlinks"] == [dict(
            path=link, target="libLeanRuntime.so.1",
            roles=["dynamic-search-artifact"])]
        escaped = dict(environment, LEAN_PATH="/tmp/foreign")
        _expect_failure(
            lambda: runtime_search_closure(
                escaped, corpus, lean, "escaped"), "escapes")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN SETUP TESTS PASS")
