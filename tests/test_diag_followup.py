#!/usr/bin/env python3
"""Item-A follow-up falsifier pure-logic tests: frozen bounds,
perturbation/index semantics (GPU-free), causality partition, and the
frozen verdict rule incl. the sdpa-dispatch gate (PREREG §13).
Run: python3 tests/test_diag_followup.py"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from diag_item_a_followup import (CAUSAL_CHUNK, CAUSAL_MAX, CAUSAL_P,
                                  F2_CHUNK_A, F2_CHUNK_B, F2_CTX,
                                  F2_MEAN, F2_P99, F2_REPEAT_MAX,
                                  causality_partition, followup_verdict,
                                  perturb_ids)


def test_frozen_bounds():
    """Bounds are the PREREG §13 frozen values: F2 reuses the
    pre-incident oracle bounds; causality reuses the verified
    determinism bound; p=4095 is the last input position of chunk 2."""
    assert (F2_CTX, F2_CHUNK_A, F2_CHUNK_B) == (8192, 512, 2048)
    assert (F2_MEAN, F2_P99, F2_REPEAT_MAX) == (1e-4, 1e-3, 1e-6)
    assert (CAUSAL_P, CAUSAL_CHUNK, CAUSAL_MAX) == (4095, 2048, 1e-6)
    assert CAUSAL_P == 2 * CAUSAL_CHUNK - 1     # last position, chunk 2


def test_perturbation_semantics():
    """Deterministic, single-position, always-different, always a valid
    embedding row; wraps at vocab-1."""
    ids = [5, 7, 11, 13]
    out = perturb_ids(ids, 2, vocab=100)
    assert out == [5, 7, 12, 13] and ids == [5, 7, 11, 13]  # no aliasing
    assert perturb_ids([5, 99, 1], 1, vocab=100)[1] == 0    # wraparound
    assert perturb_ids(ids, 0, vocab=2)[0] != ids[0]        # min vocab
    for bad in ((ids, 2, 1), (ids, 4, 100), (ids, -1, 100)):
        try:
            perturb_ids(*bad)
            assert False, f"accepted {bad}"
        except AssertionError:
            pass


def test_causality_partition_exact():
    """Row j = logits at position j scoring target ids[j+1]. Protected =
    rows 0..p-2 (clean logits AND clean targets); excluded = row p-1
    ONLY (clean logits, changed TARGET ids[p] — moves for scoring
    reasons, not leakage); downstream = rows p..n-1. Disjoint, total."""
    n = F2_CTX - 1                                # 8191 rows
    prot, excl, down = causality_partition(n, CAUSAL_P)
    assert prot == list(range(0, 4094))           # rows 0..4093
    assert excl == [4094]                         # target ids[4095] changed
    assert down == list(range(4095, n))
    assert len(prot) + len(excl) + len(down) == n
    assert not (set(prot) & set(down))
    # generic p
    prot2, excl2, down2 = causality_partition(10, 3)
    assert (prot2, excl2, down2) == ([0, 1], [2], [3, 4, 5, 6, 7, 8, 9])


def _f2_ok():
    return dict(mean_abs=1e-5, p99=1e-4)


def _ok():
    # (f2_stats, repeat, protected_max, downstream_max, f2_attn, c_attn)
    return [_f2_ok(), 1e-9, 1e-9, 0.5, "sdpa", "sdpa"]


def test_verdict_all_pass():
    ok, fails = followup_verdict(*_ok())
    assert ok and fails == []


def test_verdict_gates_attn_dispatch():
    """A silent eager (or flash-forced/None) resolution invalidates the
    run — gated for BOTH models, labeled as dispatch, not science."""
    for slot, tag in ((4, "f2-attn-impl"), (5, "causality-attn-impl")):
        for wrong in ("eager", "flash_attention_2", None):
            args = _ok()
            args[slot] = wrong
            ok, fails = followup_verdict(*args)
            assert not ok and any(f.startswith(tag) for f in fails)


def test_verdict_each_gate_trips():
    """Strict '<' on F2 stats; inclusive '<=' on repeat and protected;
    strict '>' on non-vacuity; NaN/None fail everywhere."""
    args = _ok()
    args[0] = dict(mean_abs=F2_MEAN, p99=1e-4)    # mean at bound: fail
    assert followup_verdict(*args) == (False, ["f2-semantic"])
    args = _ok()
    args[0] = dict(mean_abs=1e-5, p99=F2_P99)     # p99 at bound: fail
    assert followup_verdict(*args) == (False, ["f2-semantic"])
    args = _ok()
    args[0] = None
    assert followup_verdict(*args) == (False, ["f2-semantic"])
    args = _ok()
    args[0] = dict(mean_abs=float("nan"), p99=1e-4)
    assert followup_verdict(*args) == (False, ["f2-semantic"])
    args = _ok()
    args[1] = 2e-6
    assert followup_verdict(*args) == (False, ["f2-repeat"])
    args = _ok()
    args[1] = float("nan")
    assert followup_verdict(*args) == (False, ["f2-repeat"])
    args = _ok()
    args[2] = 2e-6                                # leakage above bound
    assert followup_verdict(*args) == (False, ["causality-mask"])
    args = _ok()
    args[2] = None
    assert followup_verdict(*args) == (False, ["causality-mask"])
    args = _ok()
    args[3] = CAUSAL_MAX                          # nothing changed: vacuous
    assert followup_verdict(*args) == (False, ["causality-vacuous"])
    args = _ok()
    args[3] = float("inf")                        # non-finite downstream
    assert followup_verdict(*args) == (False, ["causality-vacuous"])
    # inclusive boundaries pass exactly at the determinism bound
    args = _ok()
    args[1] = F2_REPEAT_MAX
    args[2] = CAUSAL_MAX
    ok, fails = followup_verdict(*args)
    assert ok and fails == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("DIAG-FOLLOWUP TESTS PASS")
