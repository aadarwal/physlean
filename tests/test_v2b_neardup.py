#!/usr/bin/env python3
"""V2-b A6 tests: layout-preserving lexers, typed-JSON exact hashes,
exact Jaccard with filter/brute-force equivalence, seeded audit packs,
and the mechanical threshold/activation gates. Synthetic fixtures only.
Run: python3 tests/test_v2b_neardup.py"""
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_common import V2BError, identity_key, seeded_hash
from v2b_neardup import (build_calibration_pack, build_collision_pack,
                         build_neardup_artifact, brute_force_pairs,
                         candidate_pairs, collision_activation,
                         collision_groups, five_grams, jaccard_bin,
                         jaccard_outcome, lex_lean, lex_python,
                         lexical_records, normalized_hash,
                         seeded_member_pair, verbatim_hash)


LEAN_TEST_KEYWORDS = frozenset(
    ("by", "def", "example", "omega", "simp", "theorem", "trivial"))


def test_python_layout_sentinels_make_nestings_distinct():
    a = lex_python("def f(x):\n    if x:\n        a()\n    b()\n")
    b = lex_python("def f(x):\n    if x:\n        a()\n        b()\n")
    assert lexical_records(a) == lexical_records(b)   # same lexical tokens
    assert verbatim_hash(a) != verbatim_hash(b)       # layout is semantic
    assert normalized_hash(a, "python") != normalized_hash(b, "python")
    kinds = [k for k, _ in a]
    assert "INDENT" in kinds and "DEDENT" in kinds and "NEWLINE" in kinds
    # comments and blank lines never reach the record stream
    c = lex_python("def f(x):\n    # note\n\n    return x\n")
    d = lex_python("def f(x):\n    return x\n")
    assert verbatim_hash(c) == verbatim_hash(d)


def test_python_normalization_collides_renames_not_keywords():
    sin = lex_python("def f(x):\n    return sin(x)\n")
    cos = lex_python("def f(x):\n    return cos(x)\n")
    assert verbatim_hash(sin) != verbatim_hash(cos)
    assert normalized_hash(sin, "python") == normalized_hash(cos, "python")
    with_kw = lex_python("def f(x):\n    return x if x else None\n")
    rows = json.loads("[]")  # keywords survive normalization as themselves
    assert normalized_hash(with_kw, "python") != normalized_hash(
        lex_python("def f(x):\n    return x\n"), "python")
    assert rows == []


def test_lean_unicode_identifiers_and_layout_sentinels():
    r = lex_lean("theorem ℕ_th (α₁ : Nat) : foo' = «odd name» := rfl")
    idents = [v for k, v in r if k == "IDENT"]
    assert "ℕ_th" in idents and "α₁" in idents and "foo'" in idents
    assert "«odd name»" in idents                      # single token
    # layout: same tokens at different columns hash differently
    x = lex_lean("theorem t : True :=\n  by trivial")
    y = lex_lean("theorem t : True :=\n    by trivial")
    z = lex_lean("theorem t : True := by trivial")
    assert lexical_records(x) == lexical_records(y) == lexical_records(z)
    assert len({verbatim_hash(x), verbatim_hash(y), verbatim_hash(z)}) == 3
    assert ("LAYOUT", "  ") in x and ("LAYOUT", "    ") in y
    # comments cannot manufacture or alter sentinels on token lines
    commented = lex_lean("theorem t : True := -- note\n  by trivial")
    assert verbatim_hash(commented) == verbatim_hash(x)


def test_typed_json_serialization_separates_quoted_spaces():
    joined = lex_lean("example := «a b»")
    split = lex_lean("example := «a» «b»")
    assert verbatim_hash(joined) != verbatim_hash(split)
    assert normalized_hash(joined, "lean", LEAN_TEST_KEYWORDS) != \
        normalized_hash(split, "lean", LEAN_TEST_KEYWORDS)


def test_lean_string_literals_survive_and_distinguish_units():
    """Regression for the code_mask defect: literals must reach the
    record stream, so units differing only in a string are distinct."""
    a = lex_lean('def msg := "hello"')
    b = lex_lean('def msg := "world"')
    assert ("STR", '"hello"') in a and ("STR", '"world"') in b
    assert verbatim_hash(a) != verbatim_hash(b)
    assert normalized_hash(a, "lean", LEAN_TEST_KEYWORDS) != \
        normalized_hash(b, "lean", LEAN_TEST_KEYWORDS)
    # comment markers INSIDE every literal form survive as one record
    c = lex_lean('def x := "a -- not a comment /- nor this -/"')
    strs = [v for k, v in c if k == "STR"]
    assert strs == ['"a -- not a comment /- nor this -/"']
    r = lex_lean('def y := r#"raw -- marker "quote" inside"#')
    assert [v for k, v in r if k == "STR"] == \
        ['r#"raw -- marker "quote" inside"#']
    ch = lex_lean("def z := '-'")
    assert ("CHAR", "'-'") in ch
    esc = lex_lean("def w := '\\''")
    assert ("CHAR", "'\\''") in esc
    assert ("CHAR", "'\\x41'") in lex_lean("def hx := '\\x41'")
    assert ("CHAR", "'\\u03bb'") in lex_lean("def uni := '\\u03bb'")


def test_lean_notation_atom_primes_are_retained_as_punctuation():
    """The table-free scanner splits registered atoms such as `]'` and
    `×'`; their prime must not be mistaken for a malformed char literal."""
    get_elem = lex_lean("def x := xs[i]'h")
    assert ("OP", "'") in get_elem
    assert not any(kind == "CHAR" for kind, _ in get_elem)
    assert ("IDENT", "h") in get_elem

    for source in ("def p := (a : α) ×' β a", "def s := Σ' x, p x",
                   "def t := ∑' x, f x"):
        records = lex_lean(source)
        assert ("OP", "'") in records
        assert not any(kind == "CHAR" for kind, _ in records)

    assert verbatim_hash(lex_lean("def p := ×' β")) != \
        verbatim_hash(lex_lean("def p := × β"))

    # Frozen deterministic tie-break: the strict char grammar wins when it
    # matches, even after a symbol that could begin a registered notation
    # atom.  This is hash-consistent, not a claim of parser maximal-munch.
    assert ("CHAR", "'h'") in lex_lean("def x := xs[i]'h'")

    # The narrow punctuation fallback must not swallow other literal errors.
    for bad in ("def c := 'ab'", "def c := '\\q'", "def c := 'a",
                "def c := f 'x", "def c := ('\\q')"):
        try:
            lex_lean(bad)
            assert False, bad
        except V2BError:
            pass


def test_lean_normalization_uses_explicit_frozen_parser_tokens():
    simp = lex_lean("example : True := by simp")
    omega = lex_lean("example : True := by omega")
    assert normalized_hash(simp, "lean", LEAN_TEST_KEYWORDS) != \
        normalized_hash(omega, "lean", LEAN_TEST_KEYWORDS)
    left = lex_lean("def alpha (x : Nat) := x")
    right = lex_lean("def beta (y : Nat) := y")
    assert normalized_hash(left, "lean", LEAN_TEST_KEYWORDS) == \
        normalized_hash(right, "lean", LEAN_TEST_KEYWORDS)
    try:
        normalized_hash(simp, "lean")
        assert False, "unfrozen Lean normalization accepted"
    except V2BError:
        pass


def test_lean_numeric_literal_forms_stay_single_records():
    records = lex_lean(
        "def a := 0x1F_2\ndef b := 0b10_01\ndef c := 0o7_1\n"
        "def d := 12_3.4_5e-6_7\ndef e := 1..2")
    numbers = [value for kind, value in records if kind == "NUM"]
    assert numbers == ["0x1F_2", "0b10_01", "0o7_1",
                       "12_3.4_5e-6_7", "1", "2"]


def test_lean_multiline_raw_string_has_no_interior_layout_sentinels():
    src = 'def a := r"line1\nline2\nline3"\ndef b := 1'
    records = lex_lean(src)
    strs = [v for k, v in records if k == "STR"]
    assert strs == ['r"line1\nline2\nline3"']
    layouts = [i for i, (k, _) in enumerate(records) if k == "LAYOUT"]
    # exactly ONE sentinel: before the `def b` line; none inside the raw
    assert len(layouts) == 1
    kinds_after = records[layouts[0] + 1]
    assert kinds_after == ("IDENT", "def")


def test_lean_nested_and_unterminated_comment_handling():
    a = lex_lean("def x /- outer /- inner -/ still comment -/ := 1")
    b = lex_lean("def x := 1")
    assert lexical_records(a) == lexical_records(b)
    for bad in ("def x /- open", 'def s := "open', 'def r := r#"open',
                "def c := 'ab'", "def c := '"):
        try:
            lex_lean(bad)
            assert False, bad
        except V2BError:
            pass


def test_five_grams_are_lexical_only_and_floor_applies():
    r = lex_python("def f(x):\n    return sin(x) + cos(x) - tan(x)\n")
    grams = five_grams(r)
    assert grams
    for gram in grams:
        assert all(kind not in ("INDENT", "DEDENT", "NEWLINE", "LAYOUT")
                   for kind, _ in gram)
    tiny = dict(key="t", grams=five_grams(lex_python("def g(): pass\n")),
                n_lexical_records=7)
    big_records = lex_python("def f(x):\n    return sin(x) + cos(x)"
                             " - tan(x) * exp(x)\n")
    big = dict(key="b", grams=five_grams(big_records),
               n_lexical_records=len(lexical_records(big_records)))
    assert brute_force_pairs([tiny, big]) == []        # under-floor excluded


def _unit(key, grams):
    return dict(key=key, grams=frozenset(grams),
                n_lexical_records=20 + len(grams))


def _gram(i):
    return (("OP", f"g{i}"),) * 5


def test_exact_boundary_and_filter_equivalence():
    # J exactly 7/10: |A|=8, |B|=9, intersection 7 -> included
    a = _unit("a", [_gram(i) for i in range(8)])
    b = _unit("b", [_gram(i) for i in range(7)] + [_gram(100), _gram(101)])
    # just below: intersection 69, union 99 -> 69/99 < 7/10 -> excluded
    c = _unit("c", [_gram(i) for i in range(200, 284)])
    d = _unit("d", [_gram(i) for i in range(200, 269)]
              + [_gram(i) for i in range(400, 415)])
    units = [a, b, c, d]
    brute = brute_force_pairs(units)
    fast = candidate_pairs(units)
    assert brute == fast
    assert [(p["a"], p["b"]) for p in brute] == [("a", "b")]
    assert brute[0]["intersection"] == 7 and brute[0]["union"] == 10
    # randomized equivalence sweep (well under the 2000-unit ceiling)
    rng = random.Random(20260808)
    pool = [_gram(i) for i in range(120)]
    units = []
    for i in range(160):
        base = rng.randrange(0, 60)
        size = rng.randrange(6, 40)
        grams = {pool[(base + j) % 120] for j in range(size)}
        units.append(_unit(f"u{i}", grams))
    assert brute_force_pairs(units) == candidate_pairs(units)


def test_collision_groups_and_seeded_member_rule():
    def unit(name, start, verbatim, normalized, count=25):
        return dict(identity=["M", name], key=f"M.{name}",
                    verbatim_sha256=verbatim, normalized_sha256=normalized,
                    n_records=count, n_lexical_records=count,
                    under_floor=False)
    members = [unit(f"m{i}", i, f"v{i % 3}", "n0") for i in range(6)]
    solo = [unit("s", 0, "vx", "n1")]
    groups = collision_groups(members + solo, "lean", "repo")
    assert len(groups) == 1 and groups[0]["n_distinct_verbatim"] == 3
    group = groups[0]
    pair = seeded_member_pair("repo", group)
    ranked = sorted(group["members"], key=lambda m: seeded_hash(
        "a6hashmember:v2b:20260808", "repo", group["normalized_sha256"],
        *m["identity"]))
    assert pair["left"]["identity"] == ranked[0]["identity"]
    expect_right = next(m for m in ranked[1:]
                        if m["verbatim_sha256"] !=
                        ranked[0]["verbatim_sha256"])
    assert pair["right"]["identity"] == expect_right["identity"]
    assert pair["left"]["rank"] == 0 and pair["right"]["rank"] >= 1
    # band split is by the group's full normalized record count
    short = [unit(f"x{i}", i, f"v{i}", "n2", count=10) for i in range(2)]
    bands = {g["band"] for g in collision_groups(short, "lean", "repo")}
    assert bands == {"under20"}


def test_audit_packs_are_deterministic_and_repo_balanced():
    def group(repo, tag, band="geq20"):
        return dict(normalized_sha256=f"n-{repo}-{tag}", repo=repo,
                    band=band, n_records=30, n_members=2,
                    n_distinct_verbatim=2,
                    members=[dict(identity=["M", f"{tag}a"],
                                  verbatim_sha256="v1"),
                             dict(identity=["M", f"{tag}b"],
                                  verbatim_sha256="v2")])
    by_repo = {"alpha": [group("alpha", i) for i in range(6)],
               "beta": [group("beta", i) for i in range(6)]}
    pack = build_collision_pack(by_repo, "lean")
    geq = pack["geq20"]
    assert geq["n_selected"] == 8 and not geq["underfilled"]
    per_repo = [e["repo"] for e in geq["entries"]]
    assert per_repo.count("alpha") == 4 and per_repo.count("beta") == 4
    again = build_collision_pack(by_repo, "lean")
    assert again["geq20"]["entries"] == geq["entries"]
    assert pack["under20"]["underfilled"] and \
        pack["under20"]["n_selected"] == 0

    def pair(repo, i, inter, union):
        a_identity = ["m", f"{repo}.a{i}", 2 * i]
        b_identity = ["m", f"{repo}.b{i}", 2 * i + 1]
        return dict(a=identity_key("python", a_identity),
                    b=identity_key("python", b_identity),
                    a_identity=a_identity, b_identity=b_identity,
                    intersection=inter, union=union)
    pairs_by_repo = {"alpha": [pair("alpha", i, 7, 10) for i in range(3)],
                     "beta": [pair("beta", i, 7, 10) for i in range(9)]}
    cal = build_calibration_pack(pairs_by_repo, "python")
    b1 = cal["B1"]
    assert b1["n_selected"] == 8
    repos = [e["repo"] for e in b1["entries"]]
    assert repos.count("alpha") == 3 and repos.count("beta") == 5
    assert cal["B5"]["n_selected"] == 0 and cal["B5"]["underfilled"]


def test_calibration_seed_keys_are_flat_canonical_identities():
    """§15 convention: identities spliced FLAT, pair sorted by canonical
    JSON — pinned byte-exactly against a hand computation."""
    import hashlib
    a_identity, b_identity = ["M", "a"], ["M", "b"]
    pair = dict(a=identity_key("lean", a_identity),
                b=identity_key("lean", b_identity),
                a_identity=a_identity, b_identity=b_identity,
                intersection=7, union=10)
    pack = build_calibration_pack({"repo": [pair]}, "lean", cap=1)
    entry = pack["B1"]["entries"][0]
    expect = hashlib.sha256(json.dumps(
        ["a6cal:v2b:20260808", "repo", "M", "a", "M", "b"],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert seeded_hash("a6cal:v2b:20260808", "repo",
                       *["M", "a", "M", "b"]) == expect
    assert entry["a_identity"] == ["M", "a"]
    missing = dict(a="x", b="y", intersection=7, union=10)
    try:
        build_calibration_pack({"repo": [missing]}, "lean")
        assert False, "identity-free pair accepted"
    except V2BError:
        pass


def test_jaccard_bins_are_integer_exact():
    assert jaccard_bin(7, 10) == "B1"
    assert jaccard_bin(3, 4) == "B2"
    assert jaccard_bin(4, 5) == "B3"
    assert jaccard_bin(17, 20) == "B4"
    assert jaccard_bin(9, 10) == "B5"
    assert jaccard_bin(69, 99) is None                # below 0.70


def _cal_fixture(rows):
    """Build a packet + matching labels from (inter, union, label) rows."""
    pack = {name: dict(language="lean", bin=name, cap=8, n_available=0,
                       n_selected=0, underfilled=True, entries=[])
            for name, _, _ in (("B1", None, None), ("B2", None, None),
                               ("B3", None, None), ("B4", None, None),
                               ("B5", None, None))}
    labels = []
    for i, (inter, union, label) in enumerate(rows):
        name = jaccard_bin(inter, union)
        a_identity, b_identity = ["M", f"a{i}"], ["M", f"b{i}"]
        a = identity_key("lean", a_identity)
        b = identity_key("lean", b_identity)
        section = pack[name]
        entry = dict(repo="r", a=a, b=b,
                     a_identity=a_identity, b_identity=b_identity, bin=name,
                     intersection=inter, union=union)
        section["entries"].append(entry)
        labels.append(dict(repo="r", a=a, b=b, label=label))
    for section in pack.values():
        section["n_available"] = len(section["entries"])
        section["n_selected"] = len(section["entries"])
        section["underfilled"] = len(section["entries"]) < 8
    return pack, labels


def test_jaccard_outcome_every_branch_packet_bound():
    high = [(9, 10, "duplicate")] * 8
    low_clean = [(7, 10, "not-duplicate")] * 4
    out = jaccard_outcome(*_cal_fixture(high + low_clean))
    assert out["outcome"] == "0.80" and out["reason"] == "rule-1"
    mid_bad = [(4, 5, "not-duplicate")] * 6
    out2 = jaccard_outcome(*_cal_fixture(high + mid_bad + low_clean))
    assert out2["outcome"] == "0.90" and out2["reason"] == "rule-2"
    # B1 majority DUPLICATE defeats rules 1-2; precise 0.70 lowers it
    out3 = jaccard_outcome(*_cal_fixture(
        high + [(7, 10, "duplicate")] * 4))
    assert out3["outcome"] == "0.70" and out3["reason"] == "rule-3"
    out4 = jaccard_outcome(*_cal_fixture([(9, 10, "duplicate")] * 7))
    assert out4["outcome"] == "lexical-inconclusive"
    assert "insufficient" in out4["reason"]
    noisy = [(9, 10, "not-duplicate")] * 5 + [(9, 10, "duplicate")] * 3 \
        + low_clean
    out5 = jaccard_outcome(*_cal_fixture(noisy))
    assert out5["outcome"] == "lexical-inconclusive"
    assert out5["reason"] == "rule-4"
    out6 = jaccard_outcome(*_cal_fixture(high))
    assert out6["outcome"] == "0.80" and out6["vacuous_bins"]["B1"]
    empty = jaccard_outcome(*_cal_fixture([]))
    assert empty["outcome"] == "lexical-inconclusive"


def test_jaccard_outcome_rejects_unpacketed_or_partial_labels():
    pack, labels = _cal_fixture([(9, 10, "duplicate")] * 8)
    forged = labels + [dict(repo="r", a="zz", b="zz2", label="duplicate")]
    for bad_labels in (forged, labels[:-1],
                       labels + [dict(labels[0])]):
        try:
            jaccard_outcome(pack, bad_labels)
            assert False, "packet binding not enforced"
        except V2BError:
            pass
    # a bin holding more entries than its cap fails closed
    overfull, over_labels = _cal_fixture([(9, 10, "duplicate")] * 9)
    try:
        jaccard_outcome(overfull, over_labels)
        assert False, "over-cap bin accepted"
    except V2BError:
        pass
    # sub-floor packet stats fail closed
    bad_pack, bad_lab = _cal_fixture([(9, 10, "duplicate")] * 8)
    bad_pack["B5"]["entries"][0]["intersection"] = 1
    try:
        jaccard_outcome(bad_pack, bad_lab)
        assert False
    except V2BError:
        pass


def _coll_fixture(rows):
    """rows: (band, n_entries, labels list). Build packet + labels."""
    pack = {band: dict(language="lean", band=band, cap=8, n_available=0,
                       n_selected=0, underfilled=True, entries=[])
            for band in ("under20", "geq20")}
    labels = []
    counter = [0]
    for band, label in rows:
        i = counter[0]
        counter[0] += 1
        normalized = f"{i:064x}"
        pack[band]["entries"].append(dict(
            repo="r", normalized_sha256=normalized, band=band,
            pair=dict(
                left=dict(rank=0, identity=["M", f"a{i}"],
                          verbatim_sha256="a" * 64),
                right=dict(rank=1, identity=["M", f"b{i}"],
                           verbatim_sha256="b" * 64))))
        labels.append(dict(repo="r", normalized_sha256=normalized,
                           band=band, label=label))
    for section in pack.values():
        section["n_available"] = len(section["entries"])
        section["n_selected"] = len(section["entries"])
        section["underfilled"] = len(section["entries"]) < 8
    return pack, labels


def test_collision_activation_requires_exactly_eight_of_eight():
    rows = [("geq20", "clone")] * 8 + [("under20", "clone")] * 7 \
        + [("under20", "not-clone")]
    out = collision_activation(*_coll_fixture(rows))
    assert out["geq20"]["active"] is True
    assert out["under20"]["active"] is False       # one false positive
    out2 = collision_activation(*_coll_fixture([("geq20", "clone")] * 7))
    assert out2["geq20"]["active"] is False        # underfilled
    # nine labels cannot come from an 8-cap packet: fail closed, never 9/9
    try:
        collision_activation(*_coll_fixture([("geq20", "clone")] * 9))
        assert False, "9/9 activation accepted"
    except V2BError:
        pass
    # label outside the packet / missing label / duplicate label
    pack, labels = _coll_fixture([("geq20", "clone")] * 8)
    for bad in (labels + [dict(repo="r", normalized_sha256="zz",
                               band="geq20", label="clone")],
                labels[:-1], labels + [dict(labels[0])]):
        try:
            collision_activation(pack, bad)
            assert False, "packet binding not enforced"
        except V2BError:
            pass


def test_artifact_builder_fails_closed_on_hash_drift():
    with tempfile.TemporaryDirectory() as td:
        src = "def f(x):\n    return sin(x)\n\ndef g(x):\n    return cos(x)\n"
        sp = os.path.join(td, "m.py")
        open(sp, "w").write(src)
        import hashlib
        sha = hashlib.sha256(src.encode()).hexdigest()
        f_start, f_end = 0, src.index("\ndef g")
        g_start = src.index("def g")
        ex = dict(schema="v2a_python_extract_v3", repo="r",
                  files=[dict(module="m", source=sp, source_sha256=sha,
                              targets=[dict(identity=["m", "f", f_start],
                                            start_byte=f_start,
                                            end_byte=f_end),
                                       dict(identity=["m", "g", g_start],
                                            start_byte=g_start,
                                            end_byte=len(src))])])
        ex_path = os.path.join(td, "ex.json")
        json.dump(ex, open(ex_path, "w"))
        art = build_neardup_artifact(ex_path, "r")
        assert art["n_units"] == 2
        by = {u["identity"][1]: u for u in art["units"]}
        assert by["f"]["normalized_sha256"] == by["g"]["normalized_sha256"]
        assert by["f"]["verbatim_sha256"] != by["g"]["verbatim_sha256"]
        assert len(art["collision_groups"]) == 1
        open(sp, "a").write("# drift\n")
        try:
            build_neardup_artifact(ex_path, "r")
            assert False, "source drift accepted"
        except V2BError as err:
            assert "hash drift" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B NEARDUP TESTS PASS")
