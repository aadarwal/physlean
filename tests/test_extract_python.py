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


def _matches(file_rec, name):
    return [t for t in file_rec["targets"] if t["name"] == name]


def _target(file_rec, name):
    matches = _matches(file_rec, name)
    assert len(matches) == 1, (name, matches)
    return matches[0]


def _node(file_rec, name):
    return tuple(_target(file_rec, name)["identity"])


def _coverage(graph, node):
    matches = [row for row in graph["target_coverage"]
               if tuple(row["identity"]) == tuple(node)]
    assert len(matches) == 1
    return matches[0]


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
            t = _target(rec, name)
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
        ft = _target(rec, "f")
        fspan = by[ft["start_byte"]:ft["end_byte"]]
        fh = ft["header_bytes"]
        assert fspan[:fh].decode().endswith("def f(a):")
        assert fspan[fh:].decode().startswith("\n    # this is")
        assert ft["docstring_bytes"] > 0
        assert _target(rec, "g")["docstring_bytes"] == 0


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
        top = _node(files[0], "top")
        aux_node = _node(files[0], "aux")
        helper = _node(files[1], "helper")
        assert top + helper in edges                         # decl hit
        assert top + aux_node in edges                       # same-file
        assert not any(e[3].startswith("numpy") for e in edges)
        # c.VALUE: relative import binds c -> pkg.c (corpus module),
        # VALUE is not a top-level decl -> module fallback, NO edge
        assert not any(e[3] == "pkg.c" or e[4] == "VALUE"
                       for e in edges)
        assert g["n_cross_file"] == 1 and g["n_same_file"] == 1
        assert g["external_by_root"] == {"numpy": 1}
        cov = _coverage(g, top)
        # occurrences: helper x2, np.dot, aux, c.VALUE = 5
        assert cov["n_refs"] == 5
        assert cov["n_resolved_decl"] == 3      # helper, helper, aux
        assert cov["n_module_fallback"] == 1    # c.VALUE
        assert cov["n_external"] == 1           # np.dot
        assert cov["n_unresolved"] == 0
        assert cov["coverage"] == 3 / 5         # decl-level ONLY
        aux = _coverage(g, aux_node)
        assert aux["n_unresolved"] == 1 and aux["coverage"] == 0.0
        assert _coverage(g, helper)["coverage"] is None


def test_attribute_deref_hits_exact_decl():
    """`import pkg.b as B; B.helper(...)` resolves to the EXACT decl
    pkg.b.helper (attribute-qualified candidate), not module fallback."""
    with tempfile.TemporaryDirectory() as td:
        a = _write(td, "pkg/u.py",
                   "import pkg.b as B\n"
                   "def use(x):\n"
                   "    return B.helper(x)\n")
        b = _write(td, "pkg/b.py", "def helper(v):\n    return v\n")
        files = [extract_file(a, os.path.join("pkg", "u.py")),
                 extract_file(b, os.path.join("pkg", "b.py"))]
        g = build_graph(files)
        use = _node(files[0], "use")
        helper = _node(files[1], "helper")
        assert use + helper in {tuple(e) for e in g["edges"]}
        cov = _coverage(g, use)
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
        assert _target(files[0], "use")["refs"] == \
            [["pkg", "b.deep.helper"]]
        g = build_graph(files)
        use, helper = _node(files[0], "use"), _node(files[1], "helper")
        assert use + helper in {tuple(e) for e in g["edges"]}
        assert _coverage(g, use)["coverage"] == 1.0


def test_attribute_candidate_precedes_ambiguous_bare_symbol():
    """When both ``pkg.b`` and ``pkg.b.helper`` are declaration symbols,
    a use of imported ``b.helper`` must choose the attribute-qualified
    declaration, not silently collapse to the bare imported binding."""
    with tempfile.TemporaryDirectory() as td:
        init = _write(td, "pkg/__init__.py",
                      "def b():\n    return 0\n")
        sub = _write(td, "pkg/b.py",
                     "def helper(x):\n    return x\n")
        use_path = _write(td, "pkg/u.py",
                          "from pkg import b\n"
                          "def use(x):\n"
                          "    return b.helper(x)\n")
        files = [extract_file(init, os.path.join("pkg", "__init__.py")),
                 extract_file(sub, os.path.join("pkg", "b.py")),
                 extract_file(use_path, os.path.join("pkg", "u.py"))]
        graph = build_graph(files)
        use = _node(files[2], "use")
        helper = _node(files[1], "helper")
        bare_b = _node(files[0], "b")
        edges = {tuple(edge) for edge in graph["edges"]}
        assert use + helper in edges
        assert use + bare_b not in edges


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


def test_duplicate_top_level_names_have_source_position_identities():
    """Repeated module bindings are ordinary Python (overloads,
    singledispatch registrations, compatibility branches).  Preserve every
    declaration unit while resolving ordinary module-name references to the
    final source-order binding as an explicitly best-effort graph policy."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pkg/dup.py",
                   "def f(x):\n"
                   "    return f(x - 1) if x else 0\n"
                   "def use(x):\n"
                   "    return f(x)\n"
                   "def f(x):\n"
                   "    return x + 2\n")
        rec = extract_file(p, os.path.join("pkg", "dup.py"))
        defs = _matches(rec, "f")
        assert len(defs) == 2
        assert defs[0]["identity"] != defs[1]["identity"]
        assert [d["binding_ordinal"] for d in defs] == [0, 1]
        assert all(d["is_duplicate_binding"] for d in defs)
        assert [d["is_final_module_binding"] for d in defs] == [False, True]
        assert rec["duplicate_target_name_counts"] == {"f": 2}
        assert rec["n_duplicate_target_names"] == 1
        assert rec["n_duplicate_target_declarations"] == 2

        g = build_graph([rec])
        use = _node(rec, "use")
        first_f, final_f = (tuple(d["identity"]) for d in defs)
        assert use + tuple(defs[1]["identity"]) in {
            tuple(edge) for edge in g["edges"]}
        # The earlier body's f(...) is not self-recursion after normal module
        # import completion: it resolves to the later binding under the
        # documented approximation.
        assert first_f + final_f in {tuple(edge) for edge in g["edges"]}
        assert g["duplicate_module_bindings"] == [dict(
            symbol="pkg.dup.f",
            identities=[d["identity"] for d in defs])]
        assert "final source-order" in g["reference_binding_policy"]


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
        cov = _coverage(g, _node(rec, "use"))
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
        assert _target(rec, "rec")["refs"] == []   # self + bound only


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
        argv = ["extract_python.py", "--repo", td, "--repo-tag", "r",
                "--pkg", "pkg", "--out", out]
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
        assert parsed["schema"] == "v2a_python_extract_v3"
        assert parsed["repo"] == "r"
        assert parsed["n_files"] == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("EXTRACT-PYTHON TESTS PASS")
