#!/usr/bin/env python3
"""V2-a Python extractor tests (stdlib, GPU-free): byte-exact spans AND
header/body partitions (§2 scores body only) across decorated/
multiline/one-line/async/class forms, declaration-level resolution
(module fallback and external are RECORDED, never resolved), relative
imports, and occurrence-vs-unique-edge accounting.
Run: python3 tests/test_extract_python.py"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract_python import (ExtractError, build_graph, extract_file,
                            module_name, resolve_relative)


def _write(td, rel, text):
    p = os.path.join(td, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)
    return p


def test_module_name_and_relative_resolution():
    assert module_name(os.path.join("pkg", "mod.py")) == "pkg.mod"
    assert module_name(os.path.join("pkg", "__init__.py")) == "pkg"
    # module file: anchor is the parent package
    assert resolve_relative("pkg.sub.mod", False, 1, "x") == "pkg.sub.x"
    assert resolve_relative("pkg.sub.mod", False, 2, "x") == "pkg.x"
    assert resolve_relative("pkg.sub.mod", False, 1, None) == "pkg.sub"
    # __init__: anchor is the package itself
    assert resolve_relative("pkg.sub", True, 1, "x") == "pkg.sub.x"
    assert resolve_relative("pkg.sub", True, 2, "x") == "pkg.x"
    for bad in (("pkg", False, 2, "x"), ("pkg", True, 3, "x")):
        try:
            resolve_relative(*bad)
            assert False, bad
        except ExtractError:
            pass


def test_partitions_byte_exact_all_forms():
    """§2 scores BODY ONLY: header/body must partition the span exactly
    for decorated, multiline-signature, one-line, async, and class
    forms — with unicode shifting byte offsets."""
    src = ('X = "∀𝔸 prefix"\n'
           "@property\n"
           "def f(a):\n"
           "    # this is implementation context, never query prefix\n"
           '    """doc ∀𝔸"""\n'
           "    return a\n"
           "def g(a,\n"
           "      b):\n"
           "    return a + b\n"
           "def h(): return 7\n"
           "async def i():\n"
           "    return 8\n"
           "class C:\n"
           "    def m(self):\n"
           "        return 1\n")
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/m.py", src)
        rec = extract_file(p, os.path.join("pkg", "m.py"))
        by = src.encode()
        expect = {
            "f": ("@property", "# this is implementation context"),
            "g": ("def g(a,", "return a + b"),
            "h": ("def h():", "return 7"),
            "i": ("async def i():", "return 8"),
            "C": ("class C:", "def m(self):"),
        }
        for name, (h_start, b_start) in expect.items():
            t = rec["targets"][name]
            span = by[t["start_byte"]:t["end_byte"]]
            header = span[:t["header_bytes"]].decode()
            body = span[t["header_bytes"]:].decode()
            assert header + body == span.decode()          # partition
            assert t["header_bytes"] + t["body_bytes"] == len(span)
            assert header.startswith(h_start), (name, header)
            assert body.lstrip().startswith(b_start), (name, body)
            assert t["body_start_byte"] == t["start_byte"] + \
                t["header_bytes"]
        # The suite colon belongs to the signature; everything after it,
        # including leading comments/newlines/indentation, is scored body.
        fspan = by[rec["targets"]["f"]["start_byte"]:
                   rec["targets"]["f"]["end_byte"]]
        fh = rec["targets"]["f"]["header_bytes"]
        assert fspan[:fh].decode().endswith("def f(a):")
        assert fspan[fh:].decode().startswith("\n    # this is")
        assert rec["targets"]["f"]["docstring_bytes"] > 0
        assert rec["targets"]["g"]["docstring_bytes"] == 0


def test_cr_and_syntax_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/bad.py", "def f(:\n")
        try:
            extract_file(p, "pkg/bad.py")
            assert False
        except ExtractError as e:
            assert "unparseable" in str(e)
        p2 = os.path.join(td, "pkg", "cr.py")
        open(p2, "wb").write(b"x = 1\r\n")
        try:
            extract_file(p2, "pkg/cr.py")
            assert False
        except ExtractError as e:
            assert "CR" in str(e)


def _corpus(td):
    a = _write(td, "pkg/a.py",
               "from pkg.b import helper\n"
               "from . import c\n"
               "import numpy as np\n"
               "def top(x):\n"
               "    y = 1\n"
               "    return helper(x) + np.dot(x, y) + aux(x) \\\n"
               "        + c.VALUE + helper(y)\n"
               "def aux(z):\n"
               "    return unknown_name(z)\n")
    b = _write(td, "pkg/b.py",
               "def helper(v):\n"
               "    return v\n")
    c = _write(td, "pkg/c.py",
               "VALUE = 3\n"
               "def cfun():\n"
               "    return VALUE\n")
    return [extract_file(a, os.path.join("pkg", "a.py")),
            extract_file(b, os.path.join("pkg", "b.py")),
            extract_file(c, os.path.join("pkg", "c.py"))]


def test_declaration_level_resolution():
    """Only exact declaration hits resolve/create edges; a dereferenced
    corpus module without a decl hit is MODULE_FALLBACK (recorded, no
    edge); external is recorded, NOT resolved; occurrences count per
    use while edges deduplicate."""
    with tempfile.TemporaryDirectory() as td:
        files = _corpus(td)
        g = build_graph(files)
        edges = {tuple(e) for e in g["edges"]}
        assert ("pkg.a.top", "pkg.b.helper") in edges      # decl hit
        assert ("pkg.a.top", "pkg.a.aux") in edges         # same-file
        assert not any(dst.startswith("numpy") for _, dst in edges)
        # c.VALUE: relative import binds c -> pkg.c (corpus module),
        # VALUE is not a top-level decl -> module fallback, NO edge
        assert not any(dst == "pkg.c" or dst.endswith("c.VALUE")
                       for _, dst in edges)
        assert g["n_cross_file"] == 1 and g["n_same_file"] == 1
        assert g["external_by_root"] == {"numpy": 1}
        cov = g["target_coverage"]["pkg.a.top"]
        # occurrences: helper x2, np.dot, aux, c.VALUE = 5
        assert cov["n_refs"] == 5
        assert cov["n_resolved_decl"] == 3      # helper, helper, aux
        assert cov["n_module_fallback"] == 1    # c.VALUE
        assert cov["n_external"] == 1           # np.dot
        assert cov["n_unresolved"] == 0
        assert cov["coverage"] == 3 / 5         # decl-level ONLY
        aux = g["target_coverage"]["pkg.a.aux"]
        assert aux["n_unresolved"] == 1 and aux["coverage"] == 0.0
        assert g["target_coverage"]["pkg.b.helper"]["coverage"] is None


def test_attribute_deref_hits_exact_decl():
    """`import pkg.b as B; B.helper(...)` resolves to the EXACT decl
    pkg.b.helper (attribute-qualified candidate), not module fallback."""
    with tempfile.TemporaryDirectory() as td:
        a = _write(td, "pkg/u.py",
                   "import pkg.b as B\n"
                   "def use(x):\n"
                   "    return B.helper(x)\n")
        b = _write(td, "pkg/b.py", "def helper(v):\n    return v\n")
        g = build_graph([extract_file(a, os.path.join("pkg", "u.py")),
                         extract_file(b, os.path.join("pkg", "b.py"))])
        assert (("pkg.u.use", "pkg.b.helper")
                in {tuple(e) for e in g["edges"]})
        cov = g["target_coverage"]["pkg.u.use"]
        assert cov["n_resolved_decl"] == 1 and cov["coverage"] == 1.0


def test_unaliased_dotted_import_and_deep_attribute_resolve_exact_decl():
    """Python binds ``import pkg.b.deep`` as ``pkg``. The full use-site
    chain, not the import's leaf module, establishes the declaration."""
    with tempfile.TemporaryDirectory() as td:
        a = _write(td, "pkg/u.py",
                   "import pkg.b.deep\n"
                   "def use(x):\n"
                   "    return pkg.b.deep.helper(x)\n")
        b = _write(td, "pkg/b/deep.py",
                   "def helper(v):\n    return v\n")
        files = [extract_file(a, os.path.join("pkg", "u.py")),
                 extract_file(b, os.path.join("pkg", "b", "deep.py"))]
        assert files[0]["imports"] == {"pkg": "pkg"}
        assert files[0]["targets"]["use"]["refs"] == \
            [["pkg", "b.deep.helper"]]
        g = build_graph(files)
        assert (("pkg.u.use", "pkg.b.deep.helper")
                in {tuple(e) for e in g["edges"]})
        assert g["target_coverage"]["pkg.u.use"]["coverage"] == 1.0


def test_duplicate_module_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        a = _write(td, "one.py", "def a():\n    return 1\n")
        b = _write(td, "two.py", "def b():\n    return 2\n")
        fa = extract_file(a, "pkg/m.py")
        fb = extract_file(b, "pkg/m.py")
        try:
            build_graph([fa, fb])
            assert False, "duplicate module accepted"
        except ExtractError as err:
            assert "duplicate Python module" in str(err)


def test_known_package_root_is_not_misclassified_external():
    """Even if an imported submodule has no successfully parsed file, a
    known corpus package root makes it module fallback, not external."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/use.py",
                   "import pkg.missing as missing\n"
                   "def use():\n"
                   "    return missing.value\n")
        rec = extract_file(p, os.path.join("pkg", "use.py"))
        g = build_graph([rec])
        cov = g["target_coverage"]["pkg.use.use"]
        assert cov["n_module_fallback"] == 1
        assert cov["n_external"] == 0
        assert g["external_by_root"] == {}


def test_self_and_bound_names_excluded():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/r.py",
                   "def rec(n):\n"
                   "    inner = 2\n"
                   "    return rec(n - 1) + inner\n")
        rec = extract_file(p, os.path.join("pkg", "r.py"))
        assert rec["targets"]["rec"]["refs"] == []   # self + bound only


def test_source_path_recorded_absolute():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/s.py", "def q():\n    return 0\n")
        rec = extract_file(p, os.path.join("pkg", "s.py"))
        assert os.path.isabs(rec["source"]) and \
            rec["source"] == os.path.abspath(p)


def test_collection_order_is_deterministic_and_output_is_new_only():
    """Directory enumeration must not define evidence identity, and a
    completed extraction is never silently overwritten."""
    import json
    from unittest.mock import patch
    from extract_python import collect, main
    with tempfile.TemporaryDirectory() as td:
        _write(td, "pkg/z/f.py", "def z():\n    return 1\n")
        _write(td, "pkg/a/f.py", "def a():\n    return 1\n")
        got = collect(td, "pkg")
        assert [f["rel"] for f in got] == ["pkg/a/f.py", "pkg/z/f.py"]
        out = os.path.join(td, "out.json")
        argv = ["extract_python.py", "--repo", td, "--pkg", "pkg",
                "--out", out]
        with patch.object(sys, "argv", argv):
            main()
        first = open(out, "rb").read()
        with patch.object(sys, "argv", argv):
            try:
                main()
                assert False, "existing extraction was overwritten"
            except ExtractError as err:
                assert "overwrite" in str(err)
        assert open(out, "rb").read() == first
        parsed = json.loads(first)
        assert parsed["schema"] == "v2a_python_extract_v2"
        assert parsed["n_files"] == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("EXTRACT-PYTHON TESTS PASS")
