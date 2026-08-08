#!/usr/bin/env python3
"""Item-A diagnostic pure-logic regression tests: frozen bounds, strata
partition (input-position semantics), and the frozen completeness-gated
verdict rule (PREREG §13).
Run: python3 tests/test_diag.py"""
import math, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from diag_item_a import (BOUNDARY_W, CHUNK_PROD, CHUNKS_ALT, CTX,
                         EXPECTED_ALT_KEYS, EXPECTED_FAMS, GATE_MEAN,
                         GATE_P99, GATE_REPEAT_MAX, ORACLE_CACHEPOS_MAX,
                         ORACLE_CHUNK, ORACLE_MEAN, ORACLE_P99,
                         ORACLE_TOKENS, gate_verdict, strata_of)


def test_frozen_bounds():
    """The diagnostic bounds are the PREREG §13 frozen values — set
    before the run, never tuned to it."""
    assert (CTX, CHUNK_PROD, CHUNKS_ALT) == (8192, 2048, (512, 1024, 4096))
    assert (GATE_MEAN, GATE_P99) == (5e-3, 5e-2)   # ORIGINAL item-A bounds
    assert GATE_REPEAT_MAX == 1e-6
    assert (ORACLE_TOKENS, ORACLE_CHUNK) == (2048, 512)
    assert (ORACLE_MEAN, ORACLE_P99) == (1e-4, 1e-3)
    assert ORACLE_CACHEPOS_MAX == 1e-6
    assert BOUNDARY_W == 32
    assert len(EXPECTED_ALT_KEYS) == 12            # 4 families x 3 chunks


def test_expected_fams_mirror_battery():
    """EXPECTED_FAMS must equal the battery family set (the run also
    asserts this; the test catches drift without a GPU)."""
    from validity_battery import FAM_SMALL
    assert tuple(FAM_SMALL) == EXPECTED_FAMS


def test_strata_partition_exact():
    """Strata key on the INPUT/LOGIT position that computes row j
    (audit fix: row 2047 is still computed inside the first chunk with
    no cache; the cached regime starts at row 2048). Counts match the
    closed forms for the 8k window."""
    n = CTX - 1                                    # 8191 rows
    labels = strata_of(n)
    assert len(labels) == n
    first = labels.count("first")
    boundary = labels.count("boundary")
    interior = labels.count("interior")
    assert first + boundary + interior == n        # partition
    assert first == CHUNK_PROD                     # rows 0..2047
    # boundary: rows j >= 2048 with j % 2048 < 32 ->
    # {2048..2079, 4096..4127, 6144..6175} = 3 * 32
    assert boundary == 3 * BOUNDARY_W
    # spot checks at the cache edge
    assert labels[CHUNK_PROD - 1] == "first"       # row 2047: first chunk
    assert labels[CHUNK_PROD] == "boundary"        # row 2048: cache starts
    assert labels[CHUNK_PROD + BOUNDARY_W - 1] == "boundary"   # row 2079
    assert labels[CHUNK_PROD + BOUNDARY_W] == "interior"       # row 2080


def _ok_alt():
    return {k: dict(mean_abs=1e-4, p99=1e-3) for k in EXPECTED_ALT_KEYS}


def _ok_rep():
    return {f: 1e-9 for f in EXPECTED_FAMS}


def _ok_oracle():
    return dict(mean_abs=1e-5, p99=1e-4)


def test_verdict_all_pass():
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), _ok_oracle(), 1e-9)
    assert ok and fails == []


def test_verdict_requires_complete_inputs():
    """Empty/partial inputs can never pass (audit fix): all 12 alternate
    pairs and all 4 repeat families are required; extras also fail."""
    ok, fails = gate_verdict({}, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and any(f.startswith("alt-coverage") for f in fails)
    alt = _ok_alt()
    alt.pop("q35/chunk1024")
    ok, fails = gate_verdict(alt, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and any("q35/chunk1024" in f for f in fails)
    alt = _ok_alt()
    alt["rogue/chunk99"] = dict(mean_abs=0.0, p99=0.0)
    ok, fails = gate_verdict(alt, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and any(f.startswith("alt-coverage") for f in fails)
    rep = _ok_rep()
    rep.pop("sc2")
    ok, fails = gate_verdict(_ok_alt(), rep, _ok_oracle(), 1e-9)
    assert not ok and any(f.startswith("repeat-coverage") for f in fails)
    ok, fails = gate_verdict(_ok_alt(), {}, _ok_oracle(), 1e-9)
    assert not ok and any(f.startswith("repeat-coverage") for f in fails)


def test_verdict_fails_nan_nonfinite():
    """NaN comparisons are False, which previously PASSED the > bounds
    (audit fix): NaN/inf anywhere gated must fail."""
    rep = _ok_rep()
    rep["q3"] = float("nan")
    ok, fails = gate_verdict(_ok_alt(), rep, _ok_oracle(), 1e-9)
    assert not ok and "repeat-determinism:q3" in fails
    rep = _ok_rep()
    rep["q25c"] = float("inf")
    ok, fails = gate_verdict(_ok_alt(), rep, _ok_oracle(), 1e-9)
    assert not ok and "repeat-determinism:q25c" in fails
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), _ok_oracle(),
                             float("nan"))
    assert not ok and "cache-position" in fails
    alt = _ok_alt()
    alt["q3/chunk512"] = dict(mean_abs=float("nan"), p99=1e-3)
    ok, fails = gate_verdict(alt, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and "prod-stability:q3/chunk512" in fails
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(),
                             dict(mean_abs=float("nan"), p99=1e-4), 1e-9)
    assert not ok and "fp32-eager-oracle" in fails


def test_verdict_each_failure_class_trips():
    """Every gate fails independently; boundaries are strict (< / <=)."""
    alt = _ok_alt()
    alt["q3/chunk1024"] = dict(mean_abs=GATE_MEAN, p99=1e-3)  # at bound
    ok, fails = gate_verdict(alt, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and fails == ["prod-stability:q3/chunk1024"]
    alt["q3/chunk1024"] = dict(mean_abs=1e-4, p99=GATE_P99)   # at bound
    ok, fails = gate_verdict(alt, _ok_rep(), _ok_oracle(), 1e-9)
    assert not ok and fails == ["prod-stability:q3/chunk1024"]
    rep = _ok_rep()
    rep["q35"] = 2e-6
    ok, fails = gate_verdict(_ok_alt(), rep, _ok_oracle(), 1e-9)
    assert not ok and fails == ["repeat-determinism:q35"]
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(),
                             dict(mean_abs=ORACLE_MEAN, p99=1e-4), 1e-9)
    assert not ok and fails == ["fp32-eager-oracle"]
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), None, 1e-9)
    assert not ok and fails == ["fp32-eager-oracle"]
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), _ok_oracle(), 2e-6)
    assert not ok and fails == ["cache-position"]
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), _ok_oracle(), None)
    assert not ok and fails == ["cache-position"]
    # repeat/cachepos bounds are inclusive (<=)
    rep = dict(_ok_rep(), q25c=GATE_REPEAT_MAX)
    ok, fails = gate_verdict(_ok_alt(), rep, _ok_oracle(),
                             ORACLE_CACHEPOS_MAX)
    assert ok and fails == []


def test_verdict_ignores_characterization():
    """bf16 true-one-shot never enters gate_verdict by construction —
    the caller passes only alternate-vs-prod pairs; a huge
    characterization delta must not be able to fail the verdict (it is
    not an input, and an extra key would trip alt-coverage anyway)."""
    ok, fails = gate_verdict(_ok_alt(), _ok_rep(), _ok_oracle(), 1e-9)
    assert ok and fails == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("DIAG TESTS PASS")
