#!/usr/bin/env python3
"""V2-c amended governance: power rule, budget rule, stratum, pins."""
import contextlib
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import v2b_v2c_governance as gov  # noqa: E402
from v2b_common import V2BError  # noqa: E402


@contextlib.contextmanager
def _expect(exc_type, needle=None):
    try:
        yield
    except exc_type as err:
        if needle is not None and needle not in str(err):
            raise AssertionError(
                f"expected {needle!r} in {exc_type.__name__}: {err}")
    else:
        raise AssertionError(f"expected {exc_type.__name__}, none raised")


SIZES = {n: [2] * (n // 2) + ([1] if n % 2 else [])
         for n in range(gov.N_MIN, gov.N_MAX + 1)}


def test_power_rule_monotone_in_anchor():
    fam = dict(sigma_b2=0.187, sigma_w2=0.0, n_modules=13)
    chosen = {}
    for anchor in (0.2, 0.5, 0.8):
        chosen[anchor] = gov.standardized_power_n(fam, SIZES, anchor)
    assert chosen[0.5]["chosen_n"] == 100
    assert chosen[0.8]["chosen_n"] == gov.N_MIN  # floor binds
    assert chosen[0.2]["chosen_n"] is None  # honest under-powered
    assert chosen[0.2]["verdict"] == "under-powered-at-cap"
    # smaller anchor can never need FEWER targets
    order = [chosen[a]["chosen_n"] or (gov.N_MAX + 1)
             for a in (0.8, 0.5, 0.2)]
    assert order == sorted(order)


def test_power_rule_formula_exact():
    fam = dict(sigma_b2=0.04, sigma_w2=0.0, n_modules=11)
    result = gov.standardized_power_n(fam, SIZES, 0.5)
    n = result["chosen_n"]
    df = 10
    threshold = gov.T_0975_BY_DF[df] + gov.T_090_BY_DF[df]
    for candidate, expect_ok in ((n, True), (n - 1, False)):
        sizes = SIZES[candidate]
        se = gov._projected_se(0.04, 0.0, sizes)
        ok = 0.5 * math.sqrt(0.04) >= threshold * se
        assert ok is expect_ok, candidate


def test_power_rule_degenerate_and_underfilled():
    fam = dict(sigma_b2=0.0, sigma_w2=0.0, n_modules=8)
    assert gov.standardized_power_n(fam, SIZES, 0.5)["verdict"] == \
        "degenerate-zero-variance"
    sparse = dict(SIZES)
    for n in range(gov.N_MIN, gov.N_MAX + 1):
        sparse[n] = None  # pool cannot fill any N
    fam = dict(sigma_b2=0.01, sigma_w2=0.0, n_modules=8)
    assert gov.standardized_power_n(fam, sparse, 0.5)["chosen_n"] is None


def test_t090_breakpoints():
    assert gov._t_floor(gov.T_090_BY_DF, 19) == 1.327728
    assert gov._t_floor(gov.T_090_BY_DF, 102) == 1.292224  # df80 floor
    vals = [gov.T_090_BY_DF[k] for k in sorted(gov.T_090_BY_DF)]
    assert vals == sorted(vals, reverse=True)
    with _expect(V2BError, "no frozen t quantile"):
        gov._t_floor({5: 2.0}, 4)


def test_stratum_regex_forms():
    R = gov.TEST_STRATUM_RE
    hits = ["sympy.core.tests.test_args", "astropy/wcs/tests/x.py",
            "pkg/test_foo.py", "pkg.conftest", "a.testing.b",
            "tests/helper.py", "pkg.test_mod"]
    misses = ["sympy.integrals.rde", "Mathlib/Analysis/Contest.lean",
              "pkg/attest.py", "pkg.latest.mod", "protester/x.py"]
    for text in hits:
        assert R.search(text), text
    for text in misses:
        assert not R.search(text), text


def test_constants_and_label():
    assert gov.V2C_CLAIM_LABEL == \
        "confirmatory-with-post-pilot-amended-governance"
    assert gov.AMENDMENT_SHA256.startswith("49ff6d8f9650")
    assert gov.N_MIN == 40 and gov.N_MAX == 400
    assert gov.FILL_FLOOR == 0.60
    assert gov.FILL_SENSITIVITY == (0.50, 0.70)
    assert gov.ANCHOR_SENSITIVITY == (0.2, 0.8)



def test_primary_budget_override_clause():
    import types
    def fake(fractions):
        # exercise plan_repo's primary_at via a minimal closure clone
        def primary_at(floor):
            eligible = [b for b in gov.BUDGET_GRID
                        if isinstance(fractions.get(str(b)), float)
                        and fractions[str(b)] >= floor]
            if not eligible:
                return None
            if gov.ORIGINAL_PRIMARY in eligible:
                return gov.ORIGINAL_PRIMARY
            return max(b for b in eligible if b < gov.ORIGINAL_PRIMARY)
        return primary_at
    # sympy-like: 64KiB above floor too -> STAYS at 16384 (override)
    p = fake({"4096": 0.76, "16384": 0.73, "65536": 0.711})
    assert p(0.60) == 16384
    # rescue-down: only 4KiB eligible
    p = fake({"4096": 0.65, "16384": 0.30, "65536": 0.10})
    assert p(0.60) == 4096
    # structurally ineligible
    p = fake({"4096": 0.37, "16384": 0.16, "65536": 0.07})
    assert p(0.60) is None



def test_sampler_test_stratum_extension():
    from v2b_metadata import (CELL_LABELS, CELL_LABELS_TEST_STRATUM,
                              allocate_quotas)
    assert len(CELL_LABELS_TEST_STRATUM) == 2 * len(CELL_LABELS)
    assert all(label.endswith(("-N", "-T"))
               for label in CELL_LABELS_TEST_STRATUM)
    # default label space unchanged, quotas behave as before
    quotas = allocate_quotas({label: 10 for label in CELL_LABELS}, 18)
    assert sum(quotas.values()) == 18
    # doubled space allocates over 36 cells
    quotas = allocate_quotas(
        {label: 5 for label in CELL_LABELS_TEST_STRATUM}, 36,
        labels=CELL_LABELS_TEST_STRATUM)
    assert sum(quotas.values()) == 36
    assert set(quotas) == set(CELL_LABELS_TEST_STRATUM)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("V2C GOVERNANCE TESTS PASS")
