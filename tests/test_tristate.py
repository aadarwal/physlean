#!/usr/bin/env python3
"""Optional-corpus (arXiv) tri-state regression tests (stdlib only):
recursive presence, frozen lock semantics, prep active-corpus omission,
core-budget invariance, and the frozen job-grid counts.
Run: python3 tests/test_tristate.py"""
import hashlib, json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from arxiv_fetch import (ledger_vs_pin, material_present, scan_disk,
                         verify_disk_against_pin)
from preflight_check import lock_arxiv_ok, stats_arxiv_rows_ok
import corpus_lock
import prep_streams
from prep_streams import CORE_CORPORA, compute_targets, CAP, MIN_MATCHED
from run_phase1 import jobs, cell_out


def test_material_present_recursive_nested():
    """Presence must be RECURSIVE (review fix: a shallow listdir called a
    nested-only stray .tex 'absent', contradicting scan_disk)."""
    with tempfile.TemporaryDirectory() as tmp:
        assert material_present(tmp) is False          # nothing at all
        os.makedirs(os.path.join(tmp, "old"))
        os.makedirs(os.path.join(tmp, "new"))
        assert material_present(tmp) is False          # empty era dirs
        nested = os.path.join(tmp, "old", "sub", "deep")
        os.makedirs(nested)
        open(os.path.join(nested, "stray.tex"), "w").write("x")
        assert material_present(tmp) is True           # nested-only = PRESENT
        assert material_present(tmp, era="old") is True
        assert material_present(tmp, era="new") is False
        # and scan_disk flags it as an explicit extra, so present-must-
        # validate will fail on it rather than ignore it
        assert any(k.startswith("NESTED:old/") for k in scan_disk(tmp))
        open(os.path.join(tmp, "new", "top.tex"), "w").write("y")
        assert material_present(tmp, era="new") is True


def test_whole_tree_strays_present_and_surfaced():
    """Review fix: scan_disk previously walked only old/ and new/, so a
    root-level or third-directory .tex read as ABSENT. Invariant: any
    .tex anywhere under the corpus root is PRESENT (era=None) and
    surfaces as an explicit STRAY extra that fails exact-set validation;
    it belongs to NO era, so it activates no optional corpus."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "old"))
        open(os.path.join(tmp, "root.tex"), "w").write("x")
        assert material_present(tmp) is True           # root-level stray
        assert material_present(tmp, era="old") is False
        assert material_present(tmp, era="new") is False
        assert scan_disk(tmp) == {"STRAY:root.tex"}
        third = os.path.join(tmp, "misc", "deep")
        os.makedirs(third)
        open(os.path.join(third, "t.tex"), "w").write("y")
        assert "STRAY:misc/deep/t.tex" in scan_disk(tmp)
        # canonical era keys are unchanged by the widened walk, and the
        # STRAY namespace can never collide with expected 'era/safe'
        # keys — exact-set subtraction always surfaces strays as extras
        open(os.path.join(tmp, "old", "a.tex"), "w").write("z")
        got = scan_disk(tmp)
        assert "old/a" in got
        expected = {"old/a", "new/b"}
        assert got - expected == {"STRAY:root.tex",
                                  "STRAY:misc/deep/t.tex"}


def test_disk_verify_catches_tamper_despite_ledger():
    """Review fix: the gate must measure the DISK, not the fetch-time
    checksums ledger — a .tex mutated after the ledger was written used
    to pass. verify_disk_against_pin never reads any ledger, so a stale
    or even perfectly pin-consistent checksums.json cannot mask it."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "old"))
        content = b"\\documentclass{article} hello"
        open(os.path.join(tmp, "old", "2301.1.tex"), "wb").write(content)
        pin = {"2301.1": dict(era="old", version="v1",
                              bytes=len(content),
                              sha256=hashlib.sha256(content).hexdigest())}
        clean = verify_disk_against_pin(tmp, pin)
        assert all(not v for v in clean.values()), clean
        # a stale ledger claiming the pin is fine sits on disk untouched
        json.dump({"files": {"old/2301.1": {"matches_pin": True}}},
                  open(os.path.join(tmp, "checksums.json"), "w"))
        # same-length tamper: byte count unchanged -> only re-hashing
        # can catch it
        tampered = content[:-1] + b"X"
        open(os.path.join(tmp, "old", "2301.1.tex"), "wb").write(tampered)
        d = verify_disk_against_pin(tmp, pin)
        assert d["hash_mismatch"] == ["old/2301.1"]
        assert d["bytes_mismatch"] == []          # proves hash, not size
        # length-changing tamper trips both
        open(os.path.join(tmp, "old", "2301.1.tex"), "wb").write(
            content + b"!")
        d = verify_disk_against_pin(tmp, pin)
        assert d["hash_mismatch"] and d["bytes_mismatch"]
        # deletion -> missing; unexpected file -> extra
        os.remove(os.path.join(tmp, "old", "2301.1.tex"))
        open(os.path.join(tmp, "old", "rogue.tex"), "w").write("r")
        d = verify_disk_against_pin(tmp, pin)
        assert d["missing"] == ["old/2301.1"] and d["extra"] == ["old/rogue"]
        # weak (byte-only) pin is surfaced but byte-checked
        pin2 = {"2301.2": dict(era="old", version="v1", bytes=1)}
        open(os.path.join(tmp, "old", "2301.2.tex"), "w").write("q")
        d2 = verify_disk_against_pin(tmp, pin2)
        assert d2["byte_only_pins"] == ["old/2301.2"]
        assert d2["bytes_mismatch"] == []


def test_ledger_records_compared_to_pin_not_trusted():
    """Review fix: matches_pin is the ledger's own claim — a forged or
    stale record asserting True must still fail when its recorded
    sha256/bytes disagree with the manifest."""
    pin = {"2301.9": dict(era="old", version="v1", bytes=5,
                          sha256="deadbeef")}
    honest = {"old/2301.9": dict(sha256="deadbeef", bytes=5,
                                 matches_pin=True)}
    assert ledger_vs_pin(honest, pin) == []
    forged_sha = {"old/2301.9": dict(sha256="f00d", bytes=5,
                                     matches_pin=True)}
    assert ledger_vs_pin(forged_sha, pin) == ["old/2301.9"]
    forged_bytes = {"old/2301.9": dict(sha256="deadbeef", bytes=6,
                                       matches_pin=True)}
    assert ledger_vs_pin(forged_bytes, pin) == ["old/2301.9"]
    # weak (byte-only) pin: recorded bytes still independently checked
    pin_weak = {"2301.8": dict(era="old", version="v1", bytes=7)}
    assert ledger_vs_pin({"old/2301.8": dict(bytes=8, matches_pin=True)},
                         pin_weak) == ["old/2301.8"]
    assert ledger_vs_pin({"old/2301.8": dict(bytes=7, matches_pin=True)},
                         pin_weak) == []
    # a record absent from the ledger is universe-coverage's job, not
    # this comparison's
    assert ledger_vs_pin({}, pin) == []


def test_stats_rows_must_match_era_presence_exactly():
    """Review fix: bool(rows) let a fully valid two-era corpus pass with
    a missing or stale era row; the rule is exact-set equality plus
    hash-iff-present."""
    both = {"old": True, "new": True}
    none = {"old": False, "new": False}
    old_only = {"old": True, "new": False}
    ok = stats_arxiv_rows_ok
    # THE regression: both eras on disk, one row missing -> fail
    assert ok(["arxiv_old"], "h", both) is False
    assert ok(["arxiv_old", "arxiv_new"], "h", both) is True
    # stale extra row for an absent era -> fail
    assert ok(["arxiv_old", "arxiv_new"], "h", old_only) is False
    assert ok(["arxiv_old"], "h", old_only) is True
    # hash must exist iff any era is present
    assert ok(["arxiv_old", "arxiv_new"], None, both) is False
    assert ok([], "h", none) is False
    assert ok([], None, none) is True
    # stray-only global presence (no era files) implies NO rows
    assert ok(["arxiv_old"], "h", none) is False


def test_lock_rule_tristate():
    """Frozen rule: current ABSENT passes even when the lock records a
    prior identity; current PRESENT requires a carried, matching identity."""
    ident = {"checksums_sha256": "aa", "manifest_sha256": "bb"}
    cur_match = {"checksums_sha256": "aa", "manifest_sha256": "bb"}
    cur_miss = {"checksums_sha256": "aa", "manifest_sha256": None}
    cur_wrong = {"checksums_sha256": "aa", "manifest_sha256": "cc"}
    # THE regression: absent current corpus + prior locked identity -> ok
    assert lock_arxiv_ok(False, ident, cur_wrong) is True
    assert lock_arxiv_ok(False, {}, cur_miss) is True
    # present: identity must be carried by the lock AND match on-disk
    assert lock_arxiv_ok(True, ident, cur_match) is True
    assert lock_arxiv_ok(True, {}, cur_match) is False   # unlocked-present
    assert lock_arxiv_ok(True, ident, cur_wrong) is False
    assert lock_arxiv_ok(True, ident, cur_miss) is False  # file missing


def _patched(mod, **attrs):
    old = {k: getattr(mod, k) for k in attrs}
    for k, v in attrs.items():
        setattr(mod, k, v)
    return old


def test_checkout_tristate():
    """corpus_lock.checkout: absent-current passes under a present-era
    lock identity; present-current fails closed on missing checksums and
    passes when identity files match the lock."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "corpora")
        os.makedirs(os.path.join(root, "arxiv"))
        lockp = os.path.join(tmp, "corpora_lock.json")
        ident = dict(checksums_sha256="0" * 64, manifest_sha256="1" * 64)
        json.dump({"repos": {}, "arxiv": ident}, open(lockp, "w"))
        old = _patched(corpus_lock, ROOT=root, LOCK=lockp, BASE=tmp)
        try:
            corpus_lock.checkout()          # absent + identity: must PASS
            # now make it PRESENT via a nested stray only
            nest = os.path.join(root, "arxiv", "old", "d")
            os.makedirs(nest)
            open(os.path.join(nest, "s.tex"), "w").write("x")
            try:
                corpus_lock.checkout()
                assert False, "present without checksums.json must fail"
            except SystemExit as e:
                assert e.code == 1
            # present with matching identity files -> pass
            cj = os.path.join(root, "arxiv", "checksums.json")
            mf = os.path.join(tmp, "arxiv_manifest.json")
            open(cj, "w").write("{}")
            open(mf, "w").write("{}")
            ident2 = dict(
                checksums_sha256=hashlib.sha256(b"{}").hexdigest(),
                manifest_sha256=hashlib.sha256(b"{}").hexdigest())
            json.dump({"repos": {}, "arxiv": ident2}, open(lockp, "w"))
            corpus_lock.checkout()
            # present with MISMATCHED locked identity -> fail
            json.dump({"repos": {}, "arxiv": ident}, open(lockp, "w"))
            try:
                corpus_lock.checkout()
                assert False, "identity mismatch must fail"
            except SystemExit as e:
                assert e.code == 1
        finally:
            _patched(corpus_lock, **old)


def test_lock_write_records_absence():
    """corpus_lock.write with no material: valid lock, arxiv=None,
    arxiv_absent=True — never touches checksums.json."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "corpora")
        os.makedirs(os.path.join(root, "arxiv", "old"))
        lockp = os.path.join(tmp, "corpora_lock.json")
        old = _patched(corpus_lock, ROOT=root, LOCK=lockp, BASE=tmp)
        try:
            corpus_lock.write()
            lock = json.load(open(lockp))
            assert lock["arxiv"] is None and lock["arxiv_absent"] is True
        finally:
            _patched(corpus_lock, **old)


def test_active_corpora_omits_absent_optional():
    """prep never loads/builds/records an optional corpus without source
    material; a nested stray makes exactly its era active again."""
    with tempfile.TemporaryDirectory() as tmp:
        old = _patched(prep_streams, ROOT=tmp)
        try:
            act = prep_streams.active_corpora()
            assert set(act) == CORE_CORPORA          # optional both absent
            nest = os.path.join(tmp, "arxiv", "old", "sub")
            os.makedirs(nest)
            open(os.path.join(nest, "p.tex"), "w").write("x")
            act = prep_streams.active_corpora()
            assert set(act) == CORE_CORPORA | {"arxiv_old"}
            assert "arxiv_new" not in act
        finally:
            _patched(prep_streams, **old)


def test_core_targets_invariant_under_optional():
    """Adding/removing a (tiny, would-shrink-the-min) optional corpus
    must not move ANY core target, availability entry, or - since
    selection depends only on (files, order, cap, seed) - any core doc
    selection. The pre-amendment code fails this test."""
    def fs(total, n=10, date="2026-06-01"):
        per = total // n
        return [dict(rel=f"f{i}.x", bytes=per, text="t" * per, date=date)
                for i in range(n)]
    core = {n: fs(2_000_000 + i * 100_000) for i, n in
            enumerate(sorted(CORE_CORPORA))}
    with_opt = dict(core)
    with_opt["arxiv_old"] = fs(40_000)     # far below every core corpus
    with_opt["arxiv_new"] = fs(60_000)     # and below MIN_MATCHED
    t0, a0 = compute_targets(core)
    t1, a1 = compute_targets(with_opt)
    assert t0 == t1, "optional corpus moved a core target"
    assert a0 == a1, "optional corpus entered clean availability"
    assert t0["full"] == min(CAP, 2_000_000)
    assert all(set(per) == CORE_CORPORA for per in a0.values())
    assert 40_000 < MIN_MATCHED  # the decoy would have moved the old min
    # doc selection: identical inputs -> identical choice, corpus-local
    files = core["mathlib"]
    order = list(range(len(files)))
    sel0 = prep_streams.select_docs(files, order, t0["full"])
    sel1 = prep_streams.select_docs(files, order, t1["full"])
    assert sel0 == sel1


def test_job_grid_frozen_counts():
    """Frozen grid identity (PREREG §11 amendment): 216 total, 152 at
    prio<=2, 44 sentinel cells incl. 15 phase variants, 64 big; unique
    outputs; NO arXiv corpus anywhere."""
    J = list(jobs())
    outs = [cell_out(s, c, k, f) for _, _, s, c, k, _, f in J]
    assert len(J) == 216 and len(set(outs)) == 216
    p12 = [j for j in J if j[0] <= 2]
    sent = [j for j in p12 if j[2] == "q25c-0.5b"]
    assert len(p12) == 152 and len(sent) == 44
    assert sum("--window-phase" in j[6] for j in sent) == 15
    assert len(J) - len(p12) == 64
    assert not any("arxiv" in j[3] for j in J)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("TRISTATE TESTS PASS")
