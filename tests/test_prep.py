#!/usr/bin/env python3
"""Selection-rule + cell-identity regression tests (stdlib only).
Run: python3 tests/test_prep.py"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prep_streams import select_docs, doc_priority, SELECT_SEED
from run_phase1 import phase_of, cell_out


def mkfiles(sizes):
    return [dict(rel=f"f{i:03d}.lean", bytes=s, text="x" * s, date=None)
            for i, s in enumerate(sizes)]


def test_selection_deterministic_and_topo_ordered():
    files = mkfiles([100] * 50)
    order = list(range(50))[::-1]  # topo order reversed on purpose
    a = select_docs(files, order, 2000)
    b = select_docs(files, order, 2000)
    assert a == b
    pos = {i: k for k, i in enumerate(order)}
    assert [pos[i] for i in a] == sorted(pos[i] for i in a)  # topo-ordered


def ref_select(files, order, cap, seed=SELECT_SEED):
    """Independent reference: hash-ranked greedy fill, then topo-sort —
    constructed WITHOUT select_docs internals."""
    ranked = sorted(range(len(files)),
                    key=lambda i: doc_priority(files[i]["rel"], seed))
    chosen, s = [], 0
    for i in ranked:
        if s + files[i]["bytes"] <= cap:
            chosen.append(i)
            s += files[i]["bytes"]
    pos = {i: k for k, i in enumerate(order)}
    return sorted(chosen, key=lambda i: pos[i])


def test_selection_matches_independent_reference():
    for n, cap in ((20, 1000), (2000, 57_300)):
        files = mkfiles([100] * n)
        order = list(range(n))
        assert select_docs(files, order, cap) == ref_select(files, order,
                                                            cap), n


def test_selection_policy_is_corpus_size_independent():
    # REAL invariant (the stride sampler violated it): with equal sizes,
    # selection = global top-m by a universe-independent priority, so the
    # common rels selected in ANY universe are a PREFIX of the common
    # rels' own priority ranking — policy cannot depend on corpus size
    common_rels = [f"f{i:03d}.lean" for i in range(20)]
    ranking = sorted(common_rels,
                     key=lambda r: doc_priority(r, SELECT_SEED))
    small = mkfiles([100] * 20)
    large = mkfiles([100] * 2000)  # rels f000..f1999 superset the common
    for files, n, cap in ((small, 20, 1000), (large, 2000, 57_300)):
        sel = {files[i]["rel"] for i in
               select_docs(files, list(range(n)), cap)}
        sel_common = [r for r in ranking if r in sel]
        assert sel_common == ranking[:len(sel_common)], (n, sel_common[:5])
    # and the seed changes the ranking (non-degenerate priority)
    assert ranking != sorted(common_rels,
                             key=lambda r: doc_priority(r, SELECT_SEED + 1))


def test_selection_never_overflows_and_never_pads():
    files = mkfiles([300, 300, 300, 300, 300])
    sel = select_docs(files, list(range(5)), 1000)
    assert sum(files[i]["bytes"] for i in sel) <= 1000
    assert len(sel) == 3  # greedy whole-document fill


def test_xl_nesting_via_base():
    files = mkfiles([100] * 100)
    order = list(range(100))
    canon = select_docs(files, order, 2000)
    xl = select_docs(files, order, 5000, base=canon)
    assert set(canon) <= set(xl)
    assert sum(files[i]["bytes"] for i in xl) <= 5000


def test_restricted_order_pool():
    files = mkfiles([100] * 30)
    pool = list(range(10))  # clean-subset case: only these selectable
    sel = select_docs(files, pool, 100000)
    assert set(sel) <= set(pool) and sel == sorted(sel)


def test_emitted_bytes_equal_selection_bytes():
    # a file WITHOUT a trailing newline must be budgeted at its EMITTED
    # size (raw + 1), so streams can never exceed the nominal cap
    import tempfile
    import prep_streams as P
    files = [dict(rel="a.lean", text="x" * 99 + "\n", bytes=100, date=None),
             dict(rel="b.lean", text="y" * 100 + "\n", bytes=101,
                  date=None)]  # as collect_files would normalize them
    with tempfile.TemporaryDirectory() as td:
        old = P.OUT
        P.OUT = td
        try:
            sel = P.select_docs(files, [0, 1], 100)
            st = P.emit_stream("t", "k", files, sel)
            import os as _os
            actual = _os.path.getsize(_os.path.join(td, "t", "k.txt"))
            assert st["bytes"] == actual <= 100, (st, actual)
        finally:
            P.OUT = old


def test_phase_identity_encoding():
    assert phase_of(["--window-phase", "8192"]) == 8192
    assert phase_of([]) == 0
    a = cell_out("m", "c", "full_topo", [])
    b = cell_out("m", "c", "full_topo", ["--window-phase", "8192"])
    c = cell_out("m", "c", "full_topo", ["--reset-per-doc"])
    assert len({a, b, c}) == 3
    assert b.endswith("__ph8192.csv.gz")


def test_arxiv_scan_era_qualified():
    """(era,safe) keying: an expected filename in the WRONG era must show
    as missing+extra, and nested strays stay explicit extras."""
    import tempfile
    from arxiv_fetch import scan_disk
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "old", "sub"))
        os.makedirs(os.path.join(td, "new"))
        open(os.path.join(td, "old", "a.tex"), "w").write("x")
        open(os.path.join(td, "new", "b.tex"), "w").write("x")   # wrong era
        open(os.path.join(td, "old", "sub", "c.tex"), "w").write("x")
        found = scan_disk(td)
        assert found == {"old/a", "new/b", "NESTED:old/sub/c.tex"}, found
        expected = {"old/a", "old/b"}   # b belongs in old, not new
        assert sorted(expected - found) == ["old/b"]      # missing
        assert sorted(found - expected) == [
            "NESTED:old/sub/c.tex", "new/b"]              # extras


def test_topo_edges_counted_and_import_all_resolved():
    """topo_order returns the resolved intra-corpus edge count (recorded
    in streams_stats — an import-sparse corpus must be VISIBLE), and the
    Lean parser resolves `import all Foo` (>= 4.9 syntax) like a plain
    import. With zero edges the order is index order — production
    indices follow the collect-time rel-path sort, i.e. LEXICOGRAPHIC
    path order, the disclosed physlib degradation (PREREG §2)."""
    from prep_streams import LEAN_IMPORT, topo_order
    assert LEAN_IMPORT.findall("import all A.B\nimport C\n") == ["A.B",
                                                                 "C"]
    assert LEAN_IMPORT.findall("  import X\n-- import Y\n") == []
    cfg = dict(lang="lean", exts=[".lean"])
    mk = lambda rel, text: dict(rel=rel, text=text, bytes=len(text),
                                date=None)
    files = [mk("A.lean", ""), mk("B.lean", "import A\n"),
             mk("C.lean", "import all B\n")]
    order, cyc, edges = topo_order(files, cfg)
    assert (order, cyc, edges) == ([0, 1, 2], 0, 2)
    # zero-edge corpus: pure index (= collect-time lexicographic) order
    order2, cyc2, edges2 = topo_order(
        [mk("Z.lean", ""), mk("A.lean", ""), mk("M.lean", "")], cfg)
    assert (order2, cyc2, edges2) == ([0, 1, 2], 0, 0)


def test_cell_done_trust_boundary():
    """The resume/analyzer trust boundary: accept one fully valid tiny
    artifact, then reject (a) one-byte dump tampering, (b) a changed
    stream, (c) a changed manifest, (d) non-finite summaries, and —
    schema v4 — (e) a stale measurement harness and (f) a stale
    software environment."""
    import gzip, hashlib, json, tempfile
    from run_phase1 import cell_done, _HASH_CACHE
    from layout import MEASUREMENT_SCHEMA_VERSION
    from provenance import env_fingerprint, harness_hash

    def sha(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()

    with tempfile.TemporaryDirectory() as td:
        stream = os.path.join(td, "s.txt")
        open(stream, "w").write("theorem t : 1 = 1 := rfl\n" * 40)
        man = stream.replace(".txt", ".manifest.jsonl")
        open(man, "w").write(json.dumps(
            dict(doc_id=0, rel="a.lean", start=0, end=1000,
                 date="2026-01-01")) + "\n")
        out = os.path.join(td, "m__c__full_topo.csv.gz")
        with gzip.open(out, "wt") as f:
            f.write("win,doc,ctxb,blen,tok,nll,grp\n0,0,1,1,5,1.0,0\n")
        blob = open(out, "rb").read()
        meta = dict(schema_version=MEASUREMENT_SCHEMA_VERSION, model="M",
                    revision="r", random_init=False, max_bytes=0,
                    window_phase=0, source_clean=True, dtype="bfloat16",
                    device="cuda", ctx_tokens=32768,
                    max_position_embeddings=32768, reset_per_doc=False,
                    stream_sha256=sha(stream), manifest_sha256=sha(man),
                    byte_ledger_ok=True, source_unchanged_during_eval=True,
                    n_scored=1, bytes_scored=1,
                    overall_bpb=1.5, per_token_nats=1.0,
                    dump_sha256=hashlib.sha256(blob).hexdigest(),
                    dump_file_bytes=len(blob),
                    harness_hash=harness_hash(),
                    env_fingerprint=env_fingerprint())
        json.dump(meta, open(out + ".meta.json", "w"))
        mj = {"M": {"sha": "r"}}
        args = (out, "M", 32768, [], stream, mj)
        assert cell_done(*args) is True, "valid artifact must be accepted"
        # (a) one-byte tamper in the dump body
        bad = bytearray(blob)
        bad[-1] ^= 0x01
        open(out, "wb").write(bytes(bad))
        assert not cell_done(*args), "tampered dump accepted"
        open(out, "wb").write(blob)
        assert cell_done(*args) is True  # restored
        # (b) stream content changed after measurement
        _HASH_CACHE.clear()
        open(stream, "a").write("-- drift\n")
        assert not cell_done(*args), "changed stream accepted"
        open(stream, "w").write("theorem t : 1 = 1 := rfl\n" * 40)
        _HASH_CACHE.clear()
        assert cell_done(*args) is True  # restored
        # (c) manifest changed after measurement
        open(man, "a").write("\n")
        _HASH_CACHE.clear()
        assert not cell_done(*args), "changed manifest accepted"
        open(man, "w").write(json.dumps(
            dict(doc_id=0, rel="a.lean", start=0, end=1000,
                 date="2026-01-01")) + "\n")
        _HASH_CACHE.clear()
        meta["manifest_sha256"] = sha(man)
        json.dump(meta, open(out + ".meta.json", "w"))
        assert cell_done(*args) is True  # restored
        # (d) non-finite summary must be rejected (measurement invariant)
        meta_bad = dict(meta, overall_bpb=float("nan"))
        json.dump(meta_bad, open(out + ".meta.json", "w"))
        assert not cell_done(*args), "NaN overall_bpb accepted"
        # (e) STALE HARNESS (schema v4): a cell produced by different
        # evaluator/layout code must not mix into the current grid
        meta_bad = dict(meta, harness_hash="0" * 64)
        json.dump(meta_bad, open(out + ".meta.json", "w"))
        assert not cell_done(*args), "stale harness hash accepted"
        # (f) STALE ENVIRONMENT: different software environment rejected
        meta_bad = dict(meta, env_fingerprint="0" * 64)
        json.dump(meta_bad, open(out + ".meta.json", "w"))
        assert not cell_done(*args), "stale env fingerprint accepted"
        # missing identities (pre-v4 meta shape) must also be rejected
        meta_bad = dict(meta)
        del meta_bad["harness_hash"], meta_bad["env_fingerprint"]
        json.dump(meta_bad, open(out + ".meta.json", "w"))
        assert not cell_done(*args), "identity-less (pre-v4) meta accepted"
        json.dump(meta, open(out + ".meta.json", "w"))
        assert cell_done(*args) is True  # restored


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PREP TESTS PASS")
