#!/usr/bin/env python3
"""Frozen pure statistics for the fresh SymPy E2 confirmation."""
import functools
import json
import math
import sys

from v2b_common import V2BError, identity_key, validate_identity
from v2b_n_governance import variance_components


ALPHA = 0.05
MAX_HALFWIDTH_BPB = 0.02
MIN_MODULES = 20
MIN_EFFECTIVE_CLUSTERS = 10.0
SECONDARY_IDS = (
    "E1a_1p5b", "E2_0p5b", "E2_3b", "E2_7b",
    "E2_logsize_slope")


def _number(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(float(value)):
        raise V2BError(f"{label} is not finite")
    return float(value)


def _regularized_beta(x, a, b):
    """Deterministic regularized incomplete beta for Student-t tails."""
    if not 0.0 <= x <= 1.0 or a <= 0.0 or b <= 0.0:
        raise V2BError("invalid incomplete-beta arguments")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0

    def continued_fraction(aa, bb, xx):
        max_iter, eps, fpmin = 256, 3e-14, 1e-300
        qab, qap, qam = aa + bb, aa + 1.0, aa - 1.0
        c = 1.0
        d = 1.0 - qab * xx / qap
        if abs(d) < fpmin:
            d = fpmin
        d = 1.0 / d
        h = d
        for m in range(1, max_iter + 1):
            m2 = 2 * m
            term = m * (bb - m) * xx / ((qam + m2) * (aa + m2))
            d = 1.0 + term * d
            if abs(d) < fpmin:
                d = fpmin
            c = 1.0 + term / c
            if abs(c) < fpmin:
                c = fpmin
            d = 1.0 / d
            h *= d * c
            term = -(aa + m) * (qab + m) * xx \
                / ((aa + m2) * (qap + m2))
            d = 1.0 + term * d
            if abs(d) < fpmin:
                d = fpmin
            c = 1.0 + term / c
            if abs(c) < fpmin:
                c = fpmin
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1.0) <= eps:
                return h
        raise V2BError("incomplete-beta continued fraction did not converge")

    log_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) \
        + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        value = bt * continued_fraction(a, b, x) / a
    else:
        value = 1.0 - bt * continued_fraction(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, value))


def student_t_cdf(value, df):
    if not isinstance(df, int) or isinstance(df, bool) or not 1 <= df <= 199:
        raise V2BError("confirmation Student-t df must be in 1..199")
    value = _number(value, "Student-t query")
    if value == 0.0:
        return 0.5
    absolute = abs(value)
    x = 0.0 if absolute > math.sqrt(sys.float_info.max) \
        else df / (df + absolute * absolute)
    twice_tail = _regularized_beta(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * twice_tail if value > 0 else 0.5 * twice_tail


def student_t_sf(value, df):
    return student_t_cdf(-_number(value, "Student-t query"), df)


@functools.lru_cache(maxsize=None)
def student_t_quantile(probability, df):
    """Positive Student-t quantile by frozen binary64 bisection."""
    probability = _number(probability, "Student-t probability")
    if not 0.5 < probability < 1.0 or not isinstance(df, int) \
            or isinstance(df, bool) or not 1 <= df <= 199:
        raise V2BError("malformed confirmation Student-t quantile query")
    low, high = 0.0, 1.0
    while student_t_cdf(high, df) < probability:
        high *= 2.0
        if not math.isfinite(high):
            raise V2BError("cannot bracket Student-t quantile")
    for _ in range(160):
        middle = (low + high) / 2.0
        if middle == low or middle == high:
            break
        if student_t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def holm_adjust(pvalues):
    """Frozen five-endpoint Holm adjustment; unavailable endpoints use p=1."""
    if not isinstance(pvalues, dict) or set(pvalues) != set(SECONDARY_IDS):
        raise V2BError("confirmation Holm family must contain exact endpoints")
    normalized = {name: _number(value, f"p-value {name}")
                  for name, value in pvalues.items()}
    if any(not 0.0 <= value <= 1.0 for value in normalized.values()):
        raise V2BError("confirmation p-value outside [0,1]")
    order = sorted(SECONDARY_IDS, key=lambda name: (normalized[name], name))
    adjusted, running = {}, 0.0
    for rank, name in enumerate(order):
        running = max(running, min(1.0,
                                   (len(order) - rank) * normalized[name]))
        adjusted[name] = running
    return dict(order=order,
                raw_pvalues={name: normalized[name]
                             for name in sorted(normalized)},
                adjusted_pvalues={name: adjusted[name]
                                  for name in sorted(adjusted)})


def summarize(rows):
    """Equal-target module-MoM summary for rows {target_key,module,delta}."""
    if not isinstance(rows, list):
        raise V2BError("confirmation inference rows must be a list")
    if not rows:
        return dict(
            n_targets=0, n_modules=0, effective_clusters=0.0,
            cluster_sizes=[], target_keys=[], target_equal_mean_bpb=None,
            target_range_bpb=None, degeneracy_tolerance_bpb=None,
            variance_components=None, standard_error_bpb=None,
            degrees_of_freedom=None, ci95_two_sided_bpb=None,
            lower_one_sided_95_bpb=None, upper_one_sided_95_bpb=None,
            two_sided_95_halfwidth_bpb=None,
            support_status="cluster-support-inadequate",
            inference_status="insufficient-clusters")
    normalized, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) \
                or set(row) != {"target_key", "module", "delta_bpb"} \
                or not isinstance(row["target_key"], str) \
                or not row["target_key"] or row["target_key"] in seen \
                or not isinstance(row["module"], str) or not row["module"]:
            raise V2BError("malformed/duplicate confirmation inference row")
        seen.add(row["target_key"])
        try:
            decoded = json.loads(row["target_key"])
            identity = validate_identity("python", decoded)
        except (json.JSONDecodeError, TypeError, V2BError) as err:
            raise V2BError("confirmation target key is not a canonical "
                           "Python identity") from err
        if identity_key("python", identity) != row["target_key"] \
                or row["module"] != identity[0]:
            raise V2BError("confirmation target key/module identity drift")
        value = _number(row["delta_bpb"], "target delta")
        normalized.append((row["target_key"], identity[0], value))
    normalized.sort(key=lambda row: row[0])
    by_module = {}
    for _, module, value in normalized:
        by_module.setdefault(module, []).append(value)
    values = [value for module in sorted(by_module)
              for value in by_module[module]]
    n, g = len(values), len(by_module)
    sizes = sorted((len(rows_) for rows_ in by_module.values()), reverse=True)
    geff = n * n / math.fsum(size * size for size in sizes)
    mean = math.fsum(values) / n
    target_range = max(values) - min(values)
    tolerance = 64.0 * math.ulp(max(1.0,
                                    max(abs(value) for value in values)))
    support = ("adequate" if g >= MIN_MODULES
               and geff >= MIN_EFFECTIVE_CLUSTERS
               else "cluster-support-inadequate")
    components = variance_components(by_module)
    base = dict(
        n_targets=n, n_modules=g, effective_clusters=geff,
        cluster_sizes=sizes, target_keys=sorted(seen),
        target_equal_mean_bpb=mean, target_range_bpb=target_range,
        degeneracy_tolerance_bpb=tolerance,
        variance_components=components, standard_error_bpb=None,
        degrees_of_freedom=None, ci95_two_sided_bpb=None,
        lower_one_sided_95_bpb=None, upper_one_sided_95_bpb=None,
        two_sided_95_halfwidth_bpb=None, support_status=support)
    if components["mode"] == "insufficient-clusters":
        return dict(base, inference_status="insufficient-clusters")
    df = g - 1
    variance = components["sigma_b2"] * math.fsum(
        size * size for size in sizes) / (n * n) \
        + components["sigma_w2"] / n
    if variance < 0.0 or not math.isfinite(variance):
        raise V2BError("confirmation mean variance is negative/nonfinite")
    se = math.sqrt(variance)
    if target_range <= tolerance or se <= tolerance:
        return dict(base, standard_error_bpb=se, degrees_of_freedom=df,
                    inference_status="degenerate")
    q975 = student_t_quantile(0.975, df)
    q95 = student_t_quantile(0.95, df)
    halfwidth = q975 * se
    interval = [mean - halfwidth, mean + halfwidth]
    lower, upper = mean - q95 * se, mean + q95 * se
    if any(not math.isfinite(value) for value in
           (*interval, lower, upper, halfwidth)):
        raise V2BError("confirmation interval is nonfinite")
    return dict(
        base, standard_error_bpb=se, degrees_of_freedom=df,
        ci95_two_sided_bpb=interval, lower_one_sided_95_bpb=lower,
        upper_one_sided_95_bpb=upper,
        two_sided_95_halfwidth_bpb=halfwidth,
        inference_status="available")


def p_greater(summary, null=0.0):
    if summary.get("inference_status") != "available":
        return 1.0
    statistic = (summary["target_equal_mean_bpb"] - float(null)) \
        / summary["standard_error_bpb"]
    return student_t_sf(statistic, summary["degrees_of_freedom"])


def p_two_sided(summary, null=0.0):
    if summary.get("inference_status") != "available":
        return 1.0
    statistic = abs((summary["target_equal_mean_bpb"] - float(null))
                    / summary["standard_error_bpb"])
    return min(1.0, 2.0 * student_t_sf(statistic,
                                      summary["degrees_of_freedom"]))


def primary_decision(summary):
    """Primary statistical label after provenance/execution have passed."""
    if summary.get("support_status") != "adequate":
        return "cluster-support-inadequate"
    if summary.get("inference_status") != "available":
        return "inference-unavailable-degenerate"
    mean = summary["target_equal_mean_bpb"]
    halfwidth = summary["two_sided_95_halfwidth_bpb"]
    if mean > 0.0 and halfwidth > MAX_HALFWIDTH_BPB:
        return "positive-direction-precision-not-met"
    pvalue = p_greater(summary)
    if mean > 0.0 and pvalue <= ALPHA \
            and summary["lower_one_sided_95_bpb"] > 0.0:
        return "e2-positive-confirmed-fresh-sympy-1p5b"
    if mean < 0.0:
        return "e2-opposite-direction-descriptive"
    return "e2-inconclusive"
