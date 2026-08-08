#!/usr/bin/env python3
"""V2-a Lean extractor tests (stdlib, GPU-free): UTF-16→byte mapping
with surrogate pairs, strict .ilean v5 parsing, comment/string masking,
header/body split, shell reconstruction, edge building, and end-to-end
extract_file round-trip on unicode-dense synthetic sources.
Run: python3 tests/test_extract_lean.py"""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract_lean import (ELIGIBLE_KINDS, ExtractError,
                          K4_CLOSURE_DEFINITION, LineIndex, active_shell,
                          build_corpus_graph, classify_decl_kind,
                          code_mask, extract_file, parse_ilean,
                          split_header_body, target_priority,
                          transitive_closure)


def test_frozen_choice_is_explicit():
    """The k4 closure definition is a single reviewable constant."""
    assert K4_CLOSURE_DEFINITION == "source-reference"


def test_utf16_byte_mapping():
    """∀ = 1 UTF-16 unit / 3 UTF-8 bytes; 𝔸 (U+1D538) = 2 units /
    4 bytes — the exact traps mathlib sets."""
    idx = LineIndex("a∀b\nx𝔸y\nplain\n")
    assert idx.pos_to_byte(0, 0) == 0
    assert idx.pos_to_byte(0, 1) == 1          # after 'a'
    assert idx.pos_to_byte(0, 2) == 4          # after ∀ (3 bytes)
    assert idx.pos_to_byte(0, 3) == 5          # after 'b'
    line1 = len("a∀b\n".encode())
    assert idx.pos_to_byte(1, 0) == line1
    assert idx.pos_to_byte(1, 1) == line1 + 1  # after 'x'
    assert idx.pos_to_byte(1, 3) == line1 + 5  # after 𝔸 (2 units/4 bytes)
    assert idx.pos_to_byte(1, 4) == line1 + 6  # after 'y' == line end
    for bad in ((1, 2), ):                     # inside the surrogate pair
        try:
            idx.pos_to_byte(*bad)
            assert False, bad
        except ExtractError as e:
            assert "surrogate" in str(e)
    for bad in ((0, 4), (5, 0), (0, -1)):      # past end / bad line/col
        try:
            idx.pos_to_byte(*bad)
            assert False, bad
        except ExtractError:
            pass
    try:
        LineIndex("a\r\nb")
        assert False
    except ExtractError as e:
        assert "CR" in str(e)


def _ck(m, n):
    """Compressed RefIdent key exactly as ModuleRefs serializes it."""
    return json.dumps({"c": {"m": m, "n": n}}, separators=(",", ":"))


def _fk(m, i):
    return json.dumps({"f": {"m": m, "i": i}}, separators=(",", ":"))


def _ilean(module="M.A", imports=(("M.B", False, False, False),),
           decls=None, refs=None):
    return dict(version=5, module=module,
                directImports=[list(i) for i in imports],
                decls=decls if decls is not None else {},
                references=refs if refs is not None else {})


def test_parse_ilean_compact_v5():
    """EXACT replication of the observed toolchain Leanc.ilean fragment
    (directImports 4-tuples; 8-int decl arrays; compressed-JSON-string
    RefIdent keys; length-5 usage arrays), verified against the
    installed v4.32 Lean.Data.Lsp.Internal serializers."""
    raw = dict(
        version=5, module="Leanc",
        directImports=[["Lean.Compiler.FFI", False, False, False]],
        decls={"main": [9, 0, 66, 12, 9, 4, 9, 8]},
        references={
            _ck("Init.Data.Array.Basic", "Array.contains"):
                dict(definition=None, usages=[[63, 10, 63, 18, "main"]])})
    ok = parse_ilean(raw)
    assert ok["module"] == "Leanc"
    assert ok["direct_imports"] == [dict(module="Lean.Compiler.FFI",
                                         isPrivate=False, isAll=False,
                                         isMeta=False)]
    d = ok["decls"]["main"]
    assert d["range"] == dict(start=dict(line=9, character=0),
                              end=dict(line=66, character=12))
    assert d["selectionRange"]["start"] == dict(line=9, character=4)
    (r,) = ok["references"]
    assert r == dict(name="Array.contains",
                     module="Init.Data.Array.Basic",
                     parentDecl="main",
                     range=dict(start=dict(line=63, character=10),
                                end=dict(line=63, character=18)))


def test_parse_ilean_variants_and_strictness():
    # length-4 usage = the reference IS its own declaration -> no parent
    ok = parse_ilean(_ilean(refs={
        _ck("M.A", "M.A.t"): dict(definition=[1, 0, 2, 0],
                                  usages=[[1, 4, 1, 5]])}))
    assert ok["references"][0]["parentDecl"] is None
    assert ok["n_definitions"] == 1
    assert ok["definition_sites"]["M.A.t"]["parentDecl"] is None
    # fvar keys are locals: skipped for edges, counted
    ok2 = parse_ilean(_ilean(refs={
        _fk("M.A", "_uniq.7"): dict(definition=None,
                                    usages=[[3, 0, 3, 1, "d"],
                                            [4, 0, 4, 1, "d"]])}))
    assert ok2["references"] == [] and ok2["n_fvar_refs"] == 2
    for bad in (
            dict(_ilean(), version=4),
            {k: v for k, v in _ilean().items() if k != "decls"},
            dict(_ilean(), module=""),
            dict(_ilean(), directImports=["M.B"]),          # not 4-tuple
            dict(_ilean(), directImports=[["M.B", False, False]]),
            dict(_ilean(), decls={"x": [1, 2, 3]}),         # not 8 ints
            dict(_ilean(), decls={"x": [0, 0, 0, 0, 0, 0, 0, True]}),
            dict(_ilean(), decls={"x": [0, 0, 0, 0, 0, 0, 0, -1]}),
            dict(_ilean(), references={"not-json": dict(usages=[])}),
            dict(_ilean(), references={
                json.dumps({"x": 1}): dict(usages=[])}),    # unknown kind
            dict(_ilean(), references={
                _ck("M", "n"): dict(definition=None)}),     # no usages
            dict(_ilean(), references={
                _ck("M", "n"): dict(usages=[[1, 2, 3]])}),  # bad location
            dict(_ilean(), references={
                _ck("M", "n"): dict(usages=[[1, 2, 3, 4, ""]])})):
        try:
            parse_ilean(bad)
            assert False, bad
        except ExtractError:
            pass


def test_code_mask_and_split():
    """`:=` inside comments, strings, and brackets never splits; the
    first depth-0 `:=` / `where` / match-arm `|` does."""
    t = 'theorem t (h : a := by x) : P := by simp'
    h, b, kind = split_header_body(t)
    assert kind == ":=" and h + b == t
    assert h == 'theorem t (h : a := by x) : P '
    t2 = 'def f : Nat → Nat\n  | 0 => 1\n  | n => n'
    h2, b2, k2 = split_header_body(t2)
    assert k2 == "|" and b2.startswith("| 0") and h2 + b2 == t2
    t3 = 'instance : Foo Bar where\n  x := 1'
    h3, b3, k3 = split_header_body(t3)
    assert k3 == "where" and b3.startswith("where") and h3 + b3 == t3
    t4 = 'theorem s -- := fake\n  /- := /- nested -/ -/ : "x := y" = z'
    h4, b4, k4 = split_header_body(t4)
    assert k4 is None and b4 == "" and h4 == t4   # nothing real to split
    t5 = 'def g : α ⟨x := 1⟩ → β := id'
    h5, b5, k5 = split_header_body(t5)
    assert k5 == ":=" and b5 == ":= id"
    # `where` inside an identifier never matches
    t6 = "def nowhere' : Nat := 0"
    assert split_header_body(t6)[2] == ":="
    m = code_mask("a -- b\nc")
    assert m == [True, True, False, False, False, False, True, True]
    try:
        code_mask("/- open")
        assert False
    except ExtractError:
        pass


def test_active_shell_scoping():
    """open/variable die with their section; namespaces nest."""
    src = ("namespace Outer\n"
           "open Foo\n"
           "section S\n"
           "variable (x : Nat)\n"
           "open Bar\n"
           "end S\n"
           "universe u\n"
           "theorem t : True := trivial\n")
    idx = LineIndex(src)
    decl_start = src.encode().index(b"theorem")
    shell = active_shell(src, decl_start, idx)
    assert shell == ["namespace Outer", "open Foo", "universe u"], shell


def _reference_shell(text, decl_start_byte, idx):
    """INDEPENDENT reference: the original per-decl algorithm (mask +
    prefix decode + line rescan per call), kept here so the batched
    shell_snapshots is regression-tested against a genuinely separate
    implementation, not against its own wrapper."""
    from extract_lean import _SHELL_RE
    mask = code_mask(text)
    frames = [[]]
    for lineno, line in enumerate(idx.lines):
        start = idx.line_start[lineno]
        if start >= decl_start_byte:
            break
        raw = line.rstrip()
        if not raw or line[:1].isspace():
            continue
        cs = idx.pos_to_byte(lineno, 0)
        cpos = len(text.encode("utf-8")[:cs].decode("utf-8"))
        if cpos < len(mask) and not mask[cpos]:
            continue
        m = _SHELL_RE.match(raw)
        if not m:
            continue
        cmd = m.group(1)
        if cmd.startswith(("namespace ", "section")) \
                or cmd.startswith("noncomputable section"):
            frames.append([cmd])
        elif cmd == "end" or cmd.startswith("end "):
            if len(frames) > 1:
                frames.pop()
        else:
            frames[-1].append(cmd)
    return [c for fr in frames for c in fr]


def test_shell_batch_matches_reference_multi_decl():
    """The batched sweep must equal the independent per-decl reference
    (and the one-target wrapper) at EVERY decl start of a file whose
    shell state changes between decls — nesting, pops, masked shell
    lines, unicode offsets, and a decl on the final line."""
    from extract_lean import shell_snapshots
    src = ("open Root\n"
           "theorem d0 : True := trivial\n"
           "namespace 𝔸Outer\n"
           "open Foo\n"
           "/- namespace Fake\n"
           "open Masked -/\n"
           "theorem d1 : True := trivial\n"
           "section S\n"
           "variable (x : Nat)\n"
           "theorem d2 : True := trivial\n"
           "end S\n"
           "theorem d3 : True := trivial\n"
           "end 𝔸Outer\n"
           "universe u\n"
           "theorem d4 : True := trivial\n")
    idx = LineIndex(src)
    by = src.encode()
    starts = []
    off = 0
    while True:
        i = by.find(b"theorem d", off)
        if i < 0:
            break
        starts.append(i)
        off = i + 1
    assert len(starts) == 5
    batch = shell_snapshots(src, starts, idx)
    for s in starts:
        ref = _reference_shell(src, s, idx)
        assert batch[s] == ref, (s, batch[s], ref)
        assert active_shell(src, s, idx) == ref     # wrapper == batch
    assert batch[starts[0]] == ["open Root"]
    assert batch[starts[1]] == ["open Root", "namespace 𝔸Outer",
                                "open Foo"]
    assert batch[starts[2]] == ["open Root", "namespace 𝔸Outer",
                                "open Foo", "section S",
                                "variable (x : Nat)"]
    assert batch[starts[3]] == ["open Root", "namespace 𝔸Outer",
                                "open Foo"]
    assert batch[starts[4]] == ["open Root", "universe u"]
    # duplicate + unsorted wants normalize; want at byte 0 sees nothing
    b2 = shell_snapshots(src, [starts[3], 0, starts[3]], idx)
    assert b2[0] == [] and b2[starts[3]] == batch[starts[3]]


def test_edges_and_closure():
    def ref(name, module, parent):
        return dict(name=name, module=module, parentDecl=parent, range={})
    mods = [
        dict(module="M.A",
             decls={"M.A.t": {}, "M.A.s": {}},
             references=[
                 ref("M.B.u", "M.B", "M.A.t"),
                 ref("M.A.s", "M.A", "M.A.t"),
                 ref("M.A.t", "M.A", "M.A.t"),          # self: dropped
                 ref("M.B.u", "M.B", "M.A.t"),          # dup: dropped
                 ref("Ext.z", "Ext.Mod.Deep", "M.A.s"),  # external
                 ref("M.B.u", "M.B", None),             # decl-site: orphan
                 ref("M.B.u", "M.B", "M.OTHER.d")]),    # foreign parent:
        dict(module="M.B", decls={"M.B.u": {}}, references=[]),  # orphan
    ]
    # in-corpus defining module but NO decl span for the const:
    # internal-unrenderable, recorded as a QUADRUPLE, never an edge
    mods[0]["references"].append(ref("M.B.u.rec", "M.B", "M.A.t"))
    g = build_corpus_graph(mods)
    edges = {tuple(e) for e in g["edges"]}
    assert ("M.A", "M.A.t", "M.B", "M.B.u") in edges
    assert ("M.A", "M.A.t", "M.A", "M.A.s") in edges
    assert len(edges) == 2
    assert not any(e[3] == "M.B.u.rec" for e in g["edges"])
    assert g["internal_unrenderable_references"] == \
        [["M.A", "M.A.t", "M.B", "M.B.u.rec"]]
    assert g["internal_unrenderable_by_module"] == {"M.B": 1}
    assert g["n_internal_unrenderable"] == 1
    render = g["internal_renderability_by_target"]["M.A"]["M.A.t"]
    assert render == dict(n_internal_occurrences=4,
                          n_renderable_occurrences=3,
                          n_unrenderable_occurrences=1,
                          coverage=3 / 4)
    assert g["n_same_file"] == 1 and g["n_cross_file"] == 1
    # external attribution keys on the DEFINING MODULE root, exactly
    assert g["external_by_root"] == {"Ext": 1}
    assert g["external_reference_edges"] == \
        [["M.A", "M.A.s", "Ext.Mod.Deep", "Ext.z"]]
    assert g["external_ref_counts_by_target"] == {"M.A": {"M.A.s": 1}}
    cov = g["parent_decl_coverage"]["M.A"]
    assert cov["usable"] == 6 and cov["orphan"] == 2
    clo = transitive_closure(
        [["A", "a", "B", "b"], ["B", "b", "C", "c"],
         ["X", "x", "Y", "y"]], ("A", "a"))
    assert clo == {("B", "b"), ("C", "c")}


def test_duplicate_decl_names_across_modules_are_legal():
    """STRESS REGRESSION (live compiler graph): `main` in both LakeMain
    and LeanChecker is legitimate Lean — names are unique per
    ENVIRONMENT, not per source tree. Under module-qualified identity
    the old corpus-wide duplicate abort is UNREPRESENTABLE: both nodes
    exist, edges disambiguate, and same-named generated consts fold
    independently per module."""
    def ref(name, module, parent):
        return dict(name=name, module=module, parentDecl=parent, range={})
    mods = [
        dict(module="LakeMain", decls={"main": {}},
             definition_parents={},
             references=[ref("main", "LeanChecker", "main")]),
        dict(module="LeanChecker", decls={"main": {}},
             definition_parents={},
             references=[ref("main", "LakeMain", "main")]),
    ]
    g = build_corpus_graph(mods)                     # must NOT raise
    edges = {tuple(e) for e in g["edges"]}
    assert ("LakeMain", "main", "LeanChecker", "main") in edges
    assert ("LeanChecker", "main", "LakeMain", "main") in edges
    assert len(edges) == 2 and g["n_cross_file"] == 2
    mods2 = [
        dict(module="A", decls={"T": {}, "u": {}},
             definition_parents={"T.mk": "T"},
             references=[ref("T.mk", "A", "u")]),
        dict(module="B", decls={"T": {}, "v": {}},
             definition_parents={"T.mk": "T"},   # same NAME, own map
             references=[ref("T.mk", "B", "v")]),
    ]
    g2 = build_corpus_graph(mods2)
    e2 = {tuple(e) for e in g2["edges"]}
    assert ("A", "u", "A", "T") in e2 and ("B", "v", "B", "T") in e2
    assert g2["n_folded_generated"] == 2


def test_extract_file_end_to_end_unicode():
    """Byte-exact spans + partition on a unicode-dense source, driven
    through real files and a synthetic v5 .ilean."""
    src = ("namespace 𝔸Test\n"
           "theorem t𝔸 (h : ∀ x, x = x) : ⟨1, 2⟩ = ⟨1, 2⟩ := by\n"
           "  rfl\n"
           "end 𝔸Test\n")
    # decl spans lines 1..2 (full body), selection = the name t𝔸
    d_end_col16 = sum(2 if ord(c) > 0xFFFF else 1
                      for c in "  rfl")
    ilean = dict(version=5, module="T.U",
                 directImports=[],
                 decls={"T.U.t𝔸": [1, 0, 2, d_end_col16, 1, 8, 1, 11]},
                 references={
                     _ck("Init.Ext", "Ext.rfl"): dict(
                         definition=None,
                         usages=[[2, 2, 2, 5, "T.U.t𝔸"]])})
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "TU.lean")
        ip = os.path.join(td, "TU.ilean")
        open(sp, "w", encoding="utf-8").write(src)
        json.dump(ilean, open(ip, "w"))
        rec = extract_file(sp, ip)
        d = rec["decls"]["T.U.t𝔸"]
        by = src.encode()
        span = by[d["start_byte"]:d["end_byte"]].decode()
        assert span.startswith("theorem t𝔸") and span.endswith("rfl")
        assert d["header_bytes"] + d["body_bytes"] == len(span.encode())
        assert d["split_kind"] == ":="
        hdr = span.encode()[:d["header_bytes"]].decode()
        assert hdr.endswith(": ⟨1, 2⟩ = ⟨1, 2⟩ ")   # body starts at :=
        assert d["shell"] == ["namespace 𝔸Test"]
        sel = by[d["sel_start_byte"]:d["sel_end_byte"]].decode()
        assert sel == "t𝔸"


def test_installed_toolchain_leanc_if_present():
    """FIXTURE AGAINST THE REAL ARTIFACT: parses the installed v4.32
    toolchain's Leanc.ilean when a toolchain is available (ELAN_HOME or
    ~/.elan; cluster keeps elan on POOL via ELAN_HOME). Skips cleanly —
    with a visible marker — when absent, so the suite stays hermetic on
    machines without Lean; the compact-literal test above carries the
    same schema unconditionally."""
    import glob
    roots = [os.environ.get("ELAN_HOME"), os.path.expanduser("~/.elan")]
    hits = []
    for r in roots:
        if r and os.path.isdir(r):
            hits += glob.glob(os.path.join(
                r, "toolchains", "*lean4*v4.32*", "lib", "lean",
                "Leanc.ilean"))
    if not hits:
        print("    [skip] no installed v4.32 toolchain found")
        return
    ok = parse_ilean(json.load(open(hits[0])))
    assert ok["module"] == "Leanc"
    assert "main" in ok["decls"]
    assert len(ok["references"]) >= 10       # dozens of const usages
    assert all(r["parentDecl"] == "main" for r in ok["references"])
    assert any(r["name"] == "Array.contains"
               and r["module"] == "Init.Data.Array.Basic"
               for r in ok["references"])
    assert ok["n_fvar_refs"] == 0 and ok["n_definitions"] >= 1


def test_kind_classifier():
    """§2 freezes targets to theorem/lemma/def; doc comments, @[...]
    attributes (string-safe), and modifier stacks must not defeat the
    classifier; excluded and unknown kinds are labeled, never crashed
    (review finding: 85k-decl core would otherwise sample macros,
    instances, notations as targets)."""
    assert ELIGIBLE_KINDS == ("theorem", "lemma", "def")
    cases = [
        ("theorem foo : P := by simp", "theorem"),
        ("/-- doc ∀ -/ @[simp, to_additive \"def fake\"] private "
         "noncomputable def f := 1", "def"),
        ("protected lemma bar : Q := h", "lemma"),
        ("@[instance] noncomputable instance : Foo := i", "instance"),
        ("macro_rules | `(x) => `(y)", "macro_rules"),
        ("notation \"⟦\" a \"⟧\" => quot a", "notation"),
        ("unsafe partial def g : Nat := 0", "def"),
        ("structure S where\n  x : Nat", "structure"),
        ("weird_command z := 1", "unknown"),
        ("", "unknown"),
    ]
    for text, want in cases:
        kind, tok = classify_decl_kind(text)
        assert kind == want, (text, kind, tok)
    assert classify_decl_kind("weird_command z")[1] == "weird_command"


def test_selection_uncontained_demoted_not_fatal():
    """Review finding: 24/85,353 core decls carry a shared enclosing
    macro_rules selectionRange OUTSIDE the decl range — valid files.
    Extraction must record selection_contained=False (ineligible),
    never abort the module."""
    src = ("theorem a : True := trivial\n"
           "theorem b : True := trivial\n")
    ilean = dict(version=5, module="T.V", directImports=[],
                 decls={"T.V.a": [0, 0, 0, 27, 1, 8, 1, 9],  # sel in b!
                        "T.V.b": [1, 0, 1, 27, 1, 8, 1, 9]},
                 references={})
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "TV.lean")
        ip = os.path.join(td, "TV.ilean")
        open(sp, "w").write(src)
        json.dump(ilean, open(ip, "w"))
        rec = extract_file(sp, ip)               # must NOT raise
        assert rec["decls"]["T.V.a"]["selection_contained"] is False
        assert rec["decls"]["T.V.b"]["selection_contained"] is True
        assert rec["decls"]["T.V.a"]["kind"] == "theorem"
    # but a range beyond the FILE is still fatal
    ilean_bad = dict(ilean, decls={"T.V.a": [0, 0, 99, 0, 0, 0, 0, 1]})
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "TV.lean")
        ip = os.path.join(td, "TV.ilean")
        open(sp, "w").write(src)
        json.dump(ilean_bad, open(ip, "w"))
        try:
            extract_file(sp, ip)
            assert False
        except ExtractError:
            pass


def test_definition_parents_and_refinfo_tightening():
    """Length-5 DEFINITION locations map generated consts to their
    source generating declaration (Iff.intro -> Iff class of finding;
    7,823 in installed core); RefInfo validation is uniform: both keys
    required, fvar usages fully location-validated."""
    ok = parse_ilean(_ilean(refs={
        _ck("M.A", "M.A.Foo.mk"): dict(
            definition=[3, 2, 3, 8, "M.A.Foo"],
            usages=[[9, 0, 9, 6, "M.A.user"]])}))
    assert ok["definition_parents"] == {"M.A.Foo.mk": "M.A.Foo"}
    assert ok["n_definitions"] == 1
    # conflicting parents for the same const fail closed
    try:
        parse_ilean(_ilean(refs={
            _ck("M.A", "X"): dict(definition=[0, 0, 0, 1, "P1"],
                                  usages=[]),
            _ck("M.B", "X"): dict(definition=[0, 0, 0, 1, "P2"],
                                  usages=[])}))
        assert False
    except ExtractError as e:
        assert "conflicting" in str(e) or "foreign module" in str(e)
    for bad in (
            # missing definition key entirely (uniform RefInfo shape)
            dict(_ilean(), references={
                _ck("M", "n"): dict(usages=[[1, 2, 3, 4]])}),
            # fvar usages must be a list
            dict(_ilean(), references={
                _fk("M", "u1"): dict(definition=None, usages="bad")}),
            # fvar locations validate as length 4/5
            dict(_ilean(), references={
                _fk("M", "u1"): dict(definition=None,
                                     usages=[[1, 2, 3]])}),
            # fvar definition locations validate too
            dict(_ilean(), references={
                _fk("M", "u1"): dict(definition=[1, 2], usages=[])})):
        try:
            parse_ilean(bad)
            assert False, bad
        except ExtractError:
            pass


def test_length4_definition_site_folds_to_unique_smallest_enclosing_decl():
    """Length-4 generated definition sites (e.g. structure projections)
    carry no parentDecl; a unique smallest enclosing source span supplies
    the parent without any name heuristic."""
    src = ("structure Foo where\n"
           "  x : Nat\n"
           "theorem user : True := trivial\n")
    raw = dict(
        version=5, module="W.M", directImports=[],
        decls={"W.M.Foo": [0, 0, 1, 9, 0, 10, 0, 13],
               "W.M.user": [2, 0, 2, 30, 2, 8, 2, 12]},
        references={_ck("W.M", "W.M.Foo.mk"): dict(
            definition=[0, 10, 0, 13],
            usages=[[2, 23, 2, 30, "W.M.user"]])})
    with tempfile.TemporaryDirectory() as td:
        sp, ip = os.path.join(td, "W.lean"), os.path.join(td, "W.ilean")
        open(sp, "w").write(src)
        json.dump(raw, open(ip, "w"))
        rec = extract_file(sp, ip)
        assert rec["definition_parents"] == {"W.M.Foo.mk": "W.M.Foo"}
        assert rec["definition_parent_provenance"]["W.M.Foo.mk"] == \
            "unique-smallest-enclosing"
        assert rec["definition_site_diagnostics"] == dict(
            own_decl=0, explicit_parent=0,
            unique_smallest_enclosing=1,
            ambiguous_smallest=0, no_enclosing_span=0,
            position_name_prefix_agree=1,
            position_name_prefix_mismatch=0)
        assert rec["definition_position_name_mismatches"] == []


def test_generated_fold_and_external_preservation():
    """Usages of span-less generated consts FOLD onto their generating
    declaration (chains chased, self-folds dropped, residue stays
    unrenderable); external reference identities are preserved as
    deduplicated [parentDecl, definingModule, constName] triples with
    per-target/per-module counts — §14.20 k4x needs the exact
    identities, and they never enter the same-repo k4 edges."""
    def ref(name, module, parent):
        return dict(name=name, module=module, parentDecl=parent, range={})
    mods = [
        dict(module="M.A", decls={"M.A.user": {}, "M.A.Foo": {}},
             definition_parents={"M.A.Foo.mk": "M.A.Foo",
                                 "M.A.gen1": "M.A.gen2",
                                 "M.A.gen2": "M.A.Foo",
                                 "M.A.lost": "M.A.nowhere"},
             references=[
                 ref("M.A.Foo.mk", "M.A", "M.A.user"),   # fold -> Foo
                 ref("M.A.gen1", "M.A", "M.A.user"),     # chain -> Foo
                 ref("M.A.Foo.mk", "M.A", "M.A.Foo"),    # self-fold
                 ref("M.A.lost", "M.A", "M.A.user"),     # residue
                 ref("Mathlib.Order.le", "Mathlib.Order.Defs",
                     "M.A.user"),                        # external
                 ref("Mathlib.Order.le", "Mathlib.Order.Defs",
                     "M.A.user"),                        # dup external
                 ref("Mathlib.Ring.mul", "Mathlib.Ring.Defs",
                     "M.A.Foo")]),                       # external #2
    ]
    g = build_corpus_graph(mods)
    edges = {tuple(e) for e in g["edges"]}
    assert edges == {("M.A", "M.A.user", "M.A", "M.A.Foo")}  # folded
    assert g["n_folded_generated"] == 3           # mk, gen1-chain, self
    assert g["internal_unrenderable_by_module"] == {"M.A": 1}  # lost
    assert g["n_internal_unrenderable"] == 1
    render = g["internal_renderability_by_target"]["M.A"]["M.A.user"]
    assert render == dict(n_internal_occurrences=3,
                          n_renderable_occurrences=2,
                          n_unrenderable_occurrences=1,
                          coverage=2 / 3)
    # exact QUADRUPLES preserved, parallel to external_reference_edges
    assert g["internal_unrenderable_references"] == \
        [["M.A", "M.A.user", "M.A", "M.A.lost"]]
    assert g["external_reference_edges"] == [
        ["M.A", "M.A.Foo", "Mathlib.Ring.Defs", "Mathlib.Ring.mul"],
        ["M.A", "M.A.user", "Mathlib.Order.Defs", "Mathlib.Order.le"]]
    assert g["external_ref_counts_by_target"] == \
        {"M.A": {"M.A.user": 2, "M.A.Foo": 1}}
    assert g["external_ref_counts_by_module"] == {
        "Mathlib.Order.Defs": 2, "Mathlib.Ring.Defs": 1}
    assert not any(e[2].startswith("Mathlib") for e in edges)
    # same-named generating maps in ANOTHER module no longer conflict —
    # per-module by construction under module-qualified identity
    g3 = build_corpus_graph(mods + [dict(
        module="M.B", decls={},
        definition_parents={"M.A.Foo.mk": "M.B.other"},
        references=[])])
    assert {tuple(e) for e in g3["edges"]} == edges


def test_pairs_manifest_strict_and_carried():
    """v2a_ilean_pairs_v2 consumption: hashes re-verified, embedded
    module must agree, manifest sha carried into the output; wrong
    schema, legacy list form, duplicates, missing keys, drifted inputs,
    and module disagreement all fail closed."""
    import hashlib
    from extract_lean import extract_from_manifest, load_pairs_manifest
    src = ("theorem t : True := trivial\n")
    ilean = dict(version=5, module="P.Q", directImports=[],
                 decls={"P.Q.t": [0, 0, 0, 27, 0, 8, 0, 9]},
                 references={})
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "PQ.lean")
        ip = os.path.join(td, "PQ.ilean")
        open(sp, "w").write(src)
        json.dump(ilean, open(ip, "w"))
        sh = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        pair = dict(module="P.Q", match_kind="exact", source=sp, ilean=ip,
                    source_sha256=sh(sp), ilean_sha256=sh(ip))
        mp = os.path.join(td, "pairs.json")
        json.dump(dict(schema="v2a_ilean_pairs_v2", pairs=[pair]),
                  open(mp, "w"))
        out = extract_from_manifest(mp, "physlib")
        assert out["pairs_manifest_sha256"] == sh(mp)
        assert out["n_files"] == 1 and "P.Q.t" in out["files"][0]["decls"]
        # embedded-module disagreement fails closed
        bad = dict(pair, module="P.WRONG")
        mp2 = os.path.join(td, "pairs2.json")
        json.dump(dict(schema="v2a_ilean_pairs_v2", pairs=[bad]),
                  open(mp2, "w"))
        try:
            extract_from_manifest(mp2, "physlib")
            assert False
        except ExtractError as e:
            assert "embedded module" in str(e)
        # drifted source fails at hash re-verification
        open(sp, "a").write("-- drift\n")
        try:
            load_pairs_manifest(mp)
            assert False
        except ExtractError as e:
            assert "drifted" in str(e)
        open(sp, "w").write(src)          # restore
        for blob in (dict(schema="wrong", pairs=[pair]),
                     [ [sp, ip] ],                       # legacy list
                     dict(schema="v2a_ilean_pairs_v2", pairs=[]),
                     dict(schema="v2a_ilean_pairs_v2",
                          pairs=[[sp, ip]]),             # legacy pair
                     dict(schema="v2a_ilean_pairs_v2",
                          pairs=[{k: v for k, v in pair.items()
                                  if k != "ilean_sha256"}]),
                     dict(schema="v2a_ilean_pairs_v2",
                          pairs=[pair, dict(pair)])):    # dup module
            mpx = os.path.join(td, "px.json")
            json.dump(blob, open(mpx, "w"))
            try:
                load_pairs_manifest(mpx)
                assert False, blob
            except ExtractError:
                pass


def test_end_to_end_fold_through_manifest():
    """WIRING regression (stress-test finding): definition_parents must
    survive extract_file AND extract_from_manifest — the unit fixtures
    fed build_corpus_graph directly, so the dropped field never folded
    a single generated const in a REAL extraction. This drives the full
    pipeline from compact .ilean fixtures and requires
    n_folded_generated > 0 with the folded edge present."""
    import hashlib
    from extract_lean import extract_from_manifest
    src = ("structure Foo where\n"
           "  x : Nat\n"
           "theorem user : True := trivial\n")
    # decl spans: Foo lines 0-1, user line 2; usage of Foo.mk (NO own
    # decl span) inside `user`; its definition entry names parent Foo
    ilean = dict(
        version=5, module="W.M", directImports=[],
        decls={"W.M.Foo": [0, 0, 1, 9, 0, 10, 0, 13],
               "W.M.user": [2, 0, 2, 30, 2, 8, 2, 12]},
        references={
            _ck("W.M", "W.M.Foo.mk"): dict(
                definition=[0, 0, 1, 9, "W.M.Foo"],
                usages=[[2, 23, 2, 30, "W.M.user"]])})
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "WM.lean")
        ip = os.path.join(td, "WM.ilean")
        open(sp, "w").write(src)
        json.dump(ilean, open(ip, "w"))
        sh = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        mp = os.path.join(td, "pairs.json")
        json.dump(dict(schema="v2a_ilean_pairs_v2", pairs=[dict(
            module="W.M", match_kind="exact", source=sp, ilean=ip,
            source_sha256=sh(sp), ilean_sha256=sh(ip))]), open(mp, "w"))
        out = extract_from_manifest(mp, "physlib")
        g = out["graph"]
        assert g["n_folded_generated"] > 0, g
        assert ("W.M", "W.M.user", "W.M", "W.M.Foo") in \
            {tuple(e) for e in g["edges"]}
        assert g["n_internal_unrenderable"] == 0
        assert out["files"][0]["definition_parents"] == \
            {"W.M.Foo.mk": "W.M.Foo"}
        assert "references" not in out["files"][0]
        assert out["files"][0]["n_reference_occurrences"] == 1


def test_target_priority_deterministic():
    a = target_priority("mathlib", "M.A", "M.A.t")
    assert a == target_priority("mathlib", "M.A", "M.A.t")
    assert a != target_priority("mathlib", "M.A", "M.A.s")
    assert a != target_priority("physlib", "M.A", "M.A.t")
    # module-qualified: same decl name in a different module ranks
    # independently (LakeMain/LeanChecker `main` regression)
    assert a != target_priority("mathlib", "M.B", "M.A.t")


def test_lean_output_is_atomic_new_only():
    from extract_lean import write_new_json
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "extract.json")
        write_new_json(out, {"first": True})
        first = open(out, "rb").read()
        try:
            write_new_json(out, {"second": True})
            assert False, "existing extraction was overwritten"
        except ExtractError as err:
            assert "overwrite" in str(err)
        assert open(out, "rb").read() == first
def test_target_priority_encoding_is_frozen_canonical_json():
    """The priority key is SHA256 of the canonical JSON array
    [V2A_SEED, repo, module, decl] (UTF-8, ensure_ascii=False,
    separators=(",", ":")) — length-delimited, so punctuation inside
    quoted Lean names («...» identifiers may contain ':') can never
    re-split into a different (repo, module, decl)."""
    import hashlib
    from extract_lean import V2A_SEED
    expect = hashlib.sha256(json.dumps(
        [V2A_SEED, "mathlib", "M.A", "M.A.t"],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert target_priority("mathlib", "M.A", "M.A.t") == expect
    # colon-concatenation collision pair: ("a:b", "c") vs ("a", "b:c")
    assert target_priority("r", "a:b", "c") != target_priority("r", "a", "b:c")
    # non-ASCII guillemet identifier round-trips deterministically
    assert target_priority("r", "M", "«odd:name»") == \
        target_priority("r", "M", "«odd:name»")


def test_duplicate_module_record_fails_closed():
    """Two parse outputs claiming the same module must raise, not
    silently overwrite decls/parents in decls_by_module."""
    m1 = dict(module="M.A", decls={"M.A.t": [0, 0, 0, 9, 0, 2, 0, 3]},
              references=[], definition_parents={})
    m2 = dict(module="M.A", decls={"M.A.s": [1, 0, 1, 9, 1, 2, 1, 3]},
              references=[], definition_parents={})
    try:
        build_corpus_graph([m1, m2])
        assert False, "duplicate module record accepted"
    except ExtractError as e:
        assert "duplicate module" in str(e) and "M.A" in str(e)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("EXTRACT-LEAN TESTS PASS")
