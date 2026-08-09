#!/usr/bin/env python3
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError
from v2b_common import identity_key
from v2b_nll_confirmation_stats import (
    SECONDARY_IDS, holm_adjust, p_greater, p_two_sided, primary_decision,
    student_t_cdf, student_t_quantile, summarize)


def _rows(values):
    return [dict(target_key=identity_key(
                     "python", [f"m{i // 2}", f"f{i}", i]),
                 module=f"m{i // 2}", delta_bpb=value)
            for i, value in enumerate(values)]


def _reject(fn):
    try:
        fn()
        assert False, "invalid stats operation succeeded"
    except V2BError:
        pass


def test_student_t_quantiles_cover_every_possible_df_against_reference():
    from scipy.stats import t
    rows = []
    for df in range(1, 200):
        q95 = student_t_quantile(0.95, df)
        q975 = student_t_quantile(0.975, df)
        assert abs(q95 - float(t.ppf(0.95, df))) <= 3e-10
        assert abs(q975 - float(t.ppf(0.975, df))) <= 3e-10
        assert abs(student_t_cdf(q95, df) - 0.95) <= 2e-13
        assert abs(student_t_cdf(q975, df) - 0.975) <= 2e-13
        rows.append([df, format(q95, ".12g"), format(q975, ".12g")])
    digest = hashlib.sha256(json.dumps(
        rows, separators=(",", ":")).encode()).hexdigest()
    assert digest == \
        "e48651f1acf65b37110f981e4fce63b6f127fe8f00f8363c210211916d059fa2"


def test_holm_exact_family_unavailable_p_one_and_tie_order():
    raw = {name: value for name, value in zip(
        SECONDARY_IDS, (0.01, 1.0, 0.01, 0.2, 0.5))}
    result = holm_adjust(raw)
    assert result["order"][:2] == ["E1a_1p5b", "E2_3b"]
    assert result["adjusted_pvalues"]["E1a_1p5b"] == 0.05
    assert result["adjusted_pvalues"]["E2_3b"] == 0.05
    assert result["adjusted_pvalues"]["E2_0p5b"] == 1.0
    _reject(lambda: holm_adjust({"E1a_1p5b": 0.1}))


def test_module_mom_summary_support_effective_clusters_and_intervals():
    values = [0.02 + (i % 7 - 3) * 0.002 for i in range(40)]
    result = summarize(_rows(values))
    assert result["n_targets"] == 40
    assert result["n_modules"] == 20
    assert result["effective_clusters"] == 20.0
    assert result["support_status"] == "adequate"
    assert result["inference_status"] == "available"
    assert result["ci95_two_sided_bpb"][0] < \
        result["target_equal_mean_bpb"] < result["ci95_two_sided_bpb"][1]
    assert 0.0 <= p_greater(result) <= 1.0
    assert 0.0 <= p_two_sided(result) <= 1.0


def test_inference_edges_fail_closed_or_remain_descriptive():
    empty = summarize([])
    assert primary_decision(empty) == "cluster-support-inadequate"
    too_few = summarize(_rows([0.01 + i * 1e-4 for i in range(10)]))
    assert too_few["n_modules"] == 5
    assert primary_decision(too_few) == "cluster-support-inadequate"
    constant = summarize(_rows([0.01] * 40))
    assert constant["inference_status"] == "degenerate"
    assert primary_decision(constant) == "inference-unavailable-degenerate"


def test_primary_label_precedence_precision_confirmation_opposite_inconclusive():
    base = dict(support_status="adequate", inference_status="available",
                target_equal_mean_bpb=0.02,
                two_sided_95_halfwidth_bpb=0.021,
                standard_error_bpb=0.001, degrees_of_freedom=30,
                lower_one_sided_95_bpb=0.018)
    assert primary_decision(base) == "positive-direction-precision-not-met"
    confirmed = dict(base, two_sided_95_halfwidth_bpb=0.002)
    assert primary_decision(confirmed) == \
        "e2-positive-confirmed-fresh-sympy-1p5b"
    opposite = dict(base, target_equal_mean_bpb=-0.01,
                    two_sided_95_halfwidth_bpb=0.5,
                    lower_one_sided_95_bpb=-0.4)
    assert primary_decision(opposite) == "e2-opposite-direction-descriptive"
    weak = dict(base, target_equal_mean_bpb=0.0001,
                two_sided_95_halfwidth_bpb=0.001,
                standard_error_bpb=0.01, lower_one_sided_95_bpb=-0.02)
    assert primary_decision(weak) == "e2-inconclusive"


def test_malformed_rows_df_and_pvalues_are_rejected():
    _reject(lambda: student_t_cdf(0.0, 0))
    _reject(lambda: student_t_quantile(0.5, 2))
    _reject(lambda: summarize([dict(target_key="x", module="m",
                                    delta_bpb=float("nan"))]))
    canonical = identity_key("python", ["real.module", "f", 0])
    _reject(lambda: summarize([dict(target_key=canonical,
                                    module="fake.module", delta_bpb=0.1)]))
    _reject(lambda: summarize([
        dict(target_key=canonical, module="real.module", delta_bpb=0.1),
        dict(target_key=canonical, module="real.module", delta_bpb=0.2)]))
    bad = {name: 0.5 for name in SECONDARY_IDS}
    bad[SECONDARY_IDS[0]] = -0.1
    _reject(lambda: holm_adjust(bad))


def test_summary_is_permutation_invariant_and_module_cannot_be_split():
    rows = _rows([0.01 + i * 0.0001 for i in range(40)])
    forward = summarize(rows)
    reverse = summarize(list(reversed(rows)))
    assert forward == reverse
    fake = [dict(row, module=f"fake_{index}")
            for index, row in enumerate(rows)]
    _reject(lambda: summarize(fake))
