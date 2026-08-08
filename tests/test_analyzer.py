#!/usr/bin/env python3
"""Analyzer v3 regression tests (PREREG §6). Run: .venv/bin/python
tests/test_analyzer.py  (needs pandas/numpy/scipy)."""
import os, sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyze_v2 import (LN2, MIN_BIN_WINDOWS, collapse_groups, descriptive,
                        gated_fits, analyze_frame, f_pow)


def mkbins(mids, bpbs, n_windows=20, byts=10000):
    n = len(mids)
    return pd.DataFrame(dict(
        mid=mids, bpb=bpbs, bpb_median=bpbs,
        nll=[b * LN2 * byts for b in bpbs], bytes=[byts] * n,
        n_groups=[byts] * n, n_windows=[n_windows] * n,
        n_docs=[40] * n))


def test_conservation_raise():
    df = pd.DataFrame(dict(win=[0, 0], grp=[0, 0], doc=[1, 1],
                           ctxb=[5, 8], blen=[3, 0], tok=[1, 2],
                           nll=[1.0, 0.5]))
    agg = collapse_groups(df)
    assert len(agg) == 1 and float(agg.nll.iloc[0]) == 1.5
    assert int(agg.blen.iloc[0]) == 3


def test_mixed_doc_group_raises():
    df = pd.DataFrame(dict(win=[0, 0], grp=[0, 0], doc=[1, 2],
                           ctxb=[5, 8], blen=[3, 1], tok=[1, 2],
                           nll=[1.0, 0.5]))
    try:
        collapse_groups(df)
    except AssertionError:
        return
    raise SystemExit("mixed-doc group did not raise")


def test_zero_byte_group_raises():
    df = pd.DataFrame(dict(win=[0], grp=[0], doc=[1], ctxb=[5], blen=[0],
                           tok=[1], nll=[1.0]))
    try:
        collapse_groups(df)
    except AssertionError:
        return
    raise SystemExit("zero-byte group did not raise")


def test_c_hat_suffix_stable_abs():
    # reviewer's repro shape: early lucky dip (1.19), rebound to 1.40,
    # then a genuine approach to top=1.20. One-sided/first-bin logic
    # returned the dip bin; abs+suffix must return the late stable bin.
    mids = [22.6, 45.0, 90.0, 181.0, 362.0, 724.0, 1448.0]
    bpbs = [1.19, 1.40, 1.35, 1.30, 1.24, 1.22, 1.20]
    d = descriptive(mkbins(mids, bpbs))
    assert d["c_hat_eps0.05"] == 362.0, d  # first bin with ALL later |Δ|<=.05
    assert d["c_hat_eps0.05"] != 22.6


def test_c_hat_below_top_not_flat():
    # a bin far BELOW top must not count as flat (abs distance)
    mids = [22.6, 45.0, 90.0]
    bpbs = [0.60, 1.19, 1.20]
    d = descriptive(mkbins(mids, bpbs))
    assert d["c_hat_eps0.05"] == 45.0, d


def test_gain_is_decade_aggregate():
    mids = [20.0, 60.0, 200.0, 2000.0, 20000.0]
    bpbs = [2.0, 1.8, 1.6, 1.2, 1.0]
    d = descriptive(mkbins(mids, bpbs))
    # decade [16,256): equal-byte bins -> aggregate = mean of first three
    assert abs(d["context_gain_bpb"] - (np.mean([2.0, 1.8, 1.6]) - 1.0)) \
        < 1e-9, d


def test_fit_gate_insufficient_bins():
    r = gated_fits(mkbins([100.0, 400.0], [1.5, 1.2]))
    assert r["accepted"] is False and "insufficient" in r["reason"]


def test_fit_gate_accepts_clean_powerlaw():
    mids = list(np.geomspace(2, 120000, 16))
    bpbs = [f_pow(c, 2.0, 0.4, 1.0) for c in mids]
    r = gated_fits(mkbins(mids, bpbs))
    assert r["accepted"] is True, r
    assert abs(r["powerlaw_equal_weight"]["beta"] - 0.4) < 0.05


def test_descriptive_cell_gets_no_fit():
    df = pd.DataFrame(dict(win=[0] * 60, grp=range(60), doc=[0] * 60,
                           ctxb=np.geomspace(2, 3000, 60).astype(int),
                           blen=[3] * 60, tok=[1] * 60, nll=[1.0] * 60))
    r = analyze_frame(collapse_groups(df))
    assert r["quantitative"] is False       # 1 window, 1 doc
    assert r["fit"]["accepted"] is False


def test_noncontiguous_support_rejected():
    mids = list(np.geomspace(2, 120000, 16))
    bpbs = [f_pow(c, 2.0, 0.4, 1.0) for c in mids]
    b = mkbins(mids, bpbs)
    b.loc[7, "n_windows"] = 2  # punch a hole mid-support
    r = gated_fits(b)
    assert r["accepted"] is False and "non-contiguous" in r["reason"]


def test_phase_pair_oriented_gain_no_cancellation():
    # BOTH orientations, more context ALWAYS helps by 1 nat: a raw signed
    # mean cancels to ~0 (the bug); the oriented gain must be positive
    # and uniform. Calls the ACTUAL helper.
    from analyze_v2 import phase_pair_stats
    d0 = pd.DataFrame(dict(
        win=[0] * 4, grp=[1, 2, 3, 4], doc=[1, 1, 2, 2],
        ctxb=[100, 100, 900, 900], blen=[4] * 4, tok=[1] * 4,
        nll=[2.0, 2.0, 1.0, 1.0]))       # low-ctx rows hard, high easy
    dp = pd.DataFrame(dict(
        win=[0] * 4, grp=[1, 2, 3, 4], doc=[1, 1, 2, 2],
        ctxb=[900, 900, 100, 100], blen=[4] * 4, tok=[1] * 4,
        nll=[1.0, 1.0, 2.0, 2.0]))       # orientation flipped per pair
    r = phase_pair_stats(collapse_groups(d0), collapse_groups(dp),
                         n_boot=0)
    assert r["n_pairs"] == 4
    assert r["frac_positive"] == 1.0
    expected = 1.0 / (np.log(2) * 4)     # 1 nat over 4 bytes, in bits
    assert abs(r["oriented_gain_bpb_byte_weighted"] - expected) < 1e-9
    assert abs(r["oriented_gain_bpb_equal_group"] - expected) < 1e-9
    # raw signed mean over the same pairs would have been exactly 0:
    raw = ((dp.nll.values - d0.nll.values) / np.log(2) / 4).mean()
    assert abs(raw) < 1e-12


def test_phase_pair_asserts_blen_and_excludes_zero_delta():
    from analyze_v2 import phase_pair_stats
    d0 = pd.DataFrame(dict(win=[0], grp=[1], doc=[1], ctxb=[100],
                           blen=[4], tok=[1], nll=[2.0]))
    dp_same = pd.DataFrame(dict(win=[0], grp=[1], doc=[1], ctxb=[100],
                                blen=[4], tok=[1], nll=[1.0]))
    r = phase_pair_stats(collapse_groups(d0), collapse_groups(dp_same),
                         n_boot=0)
    assert r["n_pairs"] == 0             # zero context delta excluded
    dp_bad = pd.DataFrame(dict(win=[0], grp=[1], doc=[1], ctxb=[900],
                               blen=[5], tok=[1], nll=[1.0]))
    try:
        phase_pair_stats(collapse_groups(d0), collapse_groups(dp_bad),
                         n_boot=0)
    except AssertionError:
        return
    raise SystemExit("blen mismatch did not raise")


def test_masked_floor_blocks_fit_and_cis():
    # generically quantitative (many windows/docs) but FAILS the masking
    # byte floor -> must be descriptive with NO fit and NO boot CIs
    n = 4000
    rng = np.random.default_rng(0)
    df = pd.DataFrame(dict(
        win=np.repeat(np.arange(40), n // 40), grp=np.arange(n),
        doc=np.repeat(np.arange(40), n // 40),
        ctxb=np.tile(np.geomspace(2, 100000, n // 40).astype(int), 40),
        blen=[3] * n, tok=[1] * n,
        nll=rng.normal(2.0, 0.05, n)))
    r = analyze_frame(collapse_groups(df), extra_quant_ok=False,
                      ineligible_reason="masking floors")
    assert r["quantitative"] is False
    assert r["fit"]["accepted"] is False
    assert "boot_windows" not in r
    assert r["ineligible_reason"] == "masking floors"


def test_unmatched_resolved_from_prep_stats():
    from analyze_v2 import stream_unmatched
    stats = dict(corpora=dict(
        qutip=dict(streams=dict(
            full_topo=dict(bytes=100, unmatched=False),
            clean_c2024_11=dict(bytes=50, unmatched=True),
            full_topo_xl=dict(bytes=100, matched=False)))))
    assert stream_unmatched("qutip", "clean_c2024_11", stats) is True
    assert stream_unmatched("qutip", "full_topo", stats) is False
    assert stream_unmatched("qutip", "full_topo_xl", stats) is True
    # FAIL CLOSED: unknown corpus or stream metadata is NEVER eligible
    # for matched comparison
    assert stream_unmatched("nope", "full_topo", stats) is True
    assert stream_unmatched("qutip", "never_prepped", stats) is True
    assert stream_unmatched("nope", "anything_xl", stats) is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("ANALYZER TESTS PASS")
