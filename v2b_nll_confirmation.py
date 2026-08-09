#!/usr/bin/env python3
"""Frozen scientific contract helpers for the fresh SymPy E2 confirmation.

This module is deliberately GPU-free.  It validates the exact prospective
protocol before any confirmation sample, battery, score, mask, or analysis is
allowed to exist.  Execution producers import this validator rather than
copying model lists, cells, hypotheses, or decision thresholds.
"""
import math
import os

from v2b_common import V2BError, load_json, sha256_sorted_json


PROTOCOL_SCHEMA = "v2b_nll_e2_confirmation_protocol_v1"
PROTOCOL_RAW_SHA256 = \
    "06c179e0fae57330737ba2a9918beadb363556a837b94ba3d30822de8f2fefd1"
PROTOCOL_SEMANTIC_SHA256 = \
    "2faacb2a502e3dc2971d73cf7ec78164e7df00bd7886a0209e1e40311120715e"
PROTOCOL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "results_v2", "v2b",
    "NLL_E2_CONFIRMATION_PROTOCOL.json")
MODEL_ROWS = (
    ("q25c-0.5b", "Qwen/Qwen2.5-Coder-0.5B",
     "8123ea2e9354afb7ffcc6c8641d1b2f5ecf18301", 0.5,
     "gated-secondary"),
    ("q25c-1.5b", "Qwen/Qwen2.5-Coder-1.5B",
     "df3ce67c0e24480f20468b6ef2894622d69eb73b", 1.5,
     "sole-primary"),
    ("q25c-3b", "Qwen/Qwen2.5-Coder-3B",
     "09d9bc5d376b0cfa0100a0694ea7de7232525803", 3.0,
     "gated-secondary"),
    ("q25c-7b", "Qwen/Qwen2.5-Coder-7B",
     "0396a76181e127dfc13e5c5ec48a8cee09938b02", 7.0,
     "key-generalization-gated-secondary"),
)
SCORED_CELLS = (
    "k1", "k3:16384", "k4:16384", "k5:0:16384",
    "k5:1:16384", "k5:2:16384")
SLOPE_COEFFICIENTS = (
    -0.25631165485344465,
    -0.05261096221822387,
    0.07590986558845116,
    0.23301275148321737,
)
SECONDARY_IDS = (
    "E1a_1p5b", "E2_0p5b", "E2_3b", "E2_7b",
    "E2_logsize_slope")
DECISION_LABELS = (
    "invalid-provenance-not-analyzed",
    "cluster-support-inadequate",
    "execution-incomplete-not-analyzed",
    "inference-unavailable-degenerate",
    "positive-direction-precision-not-met",
    "e2-positive-confirmed-fresh-sympy-1p5b",
    "e2-opposite-direction-descriptive",
    "e2-inconclusive",
)
STUDENT_T_CONTRACT = (
    "frozen and tested for every possible integer df 1..199; all means, "
    "SEs, Student-t CDFs, confidence bounds, p-values, Holm comparisons, "
    "and labels use unrounded binary64 values, with rounding only for "
    "display")
SECONDARY_METHOD = (
    "Holm step-down FWER 0.05 with ties broken by exact endpoint id; an "
    "inferentially unavailable endpoint remains in the five-endpoint family "
    "with raw p=1")

TOP_KEYS = {
    "schema", "state", "study_id", "adoption_basis", "scope", "inputs",
    "sample", "source_eligibility_gate", "models", "instrument_gate",
    "scored_cells", "primary",
    "inference", "secondary_gate", "model_size_trend", "e1b_descriptive",
    "k5_seed_sensitivity", "blinding_and_fixed_n_gate",
    "execution_schema_contracts", "eligibility_and_missingness",
    "decision_labels_in_precedence_order", "decision_rule_conditions",
    "prohibited_claims", "sequencing",
}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _ols_coefficients(sizes):
    xs = [math.log2(value) for value in sizes]
    center = math.fsum(xs) / len(xs)
    denominator = math.fsum((value - center) ** 2 for value in xs)
    return tuple((value - center) / denominator for value in xs)


def validate_protocol(value):
    """Return *value* after strict validation; never supply defaults."""
    _exact_keys(value, TOP_KEYS, "confirmation protocol")
    if sha256_sorted_json(value) != PROTOCOL_SEMANTIC_SHA256:
        raise V2BError("confirmation protocol semantic digest drift")
    if value.get("schema") != PROTOCOL_SCHEMA \
            or value.get("state") != \
            "frozen-before-confirmation-sample-and-score" \
            or value.get("study_id") != \
            "v2b-nll-e2-fresh-sympy-q25c-ladder-20260809":
        raise V2BError("confirmation protocol identity/state drift")

    scope = value["scope"]
    if scope.get("repo") != "sympy" or scope.get("language") != "python" \
            or scope.get("corpus_git_sha") != \
            "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c" \
            or scope.get("metric") != "bits-per-scored-body-byte" \
            or scope.get("budget_bytes") != 16384 \
            or scope.get("no_language_pooling") is not True \
            or scope.get("no_cross_family_model_claim") is not True:
        raise V2BError("confirmation scope drift")

    inputs = value["inputs"]
    if inputs.get("pilot_sympy_target_count") != 20 \
            or inputs.get("pilot_sympy_keys_sha256") != \
            "826674b637a196457415800d3609a39cd80c3b7aef316445137b0fab7edb7fd0" \
            or inputs.get("pilot_sympy_module_count") != 19 \
            or inputs.get("pilot_sympy_modules_sha256") != \
            "af1a3a82cc227960d50ebe5b6ab6e411e40f7e220021e460fc4fb815e5046ee8":
        raise V2BError("pilot exclusion binding drift")
    for label in ("candidates", "pilot_sample"):
        row = inputs.get(label)
        if not isinstance(row, dict) or not _hex(row.get("sha256"), 64):
            raise V2BError(f"malformed {label} binding")

    adoption = value["adoption_basis"]
    if adoption.get("sealed_parent_commit") != \
            "d70f335388d3383604f7b6afa3ddc734df9ab9f4":
        raise V2BError("sealed parent drift")
    for label in ("pilot_analysis", "pilot_sympy_governance"):
        row = adoption.get(label)
        if not isinstance(row, dict) or not _hex(row.get("sha256"), 64):
            raise V2BError(f"malformed adoption {label} binding")
    if adoption["pilot_sympy_governance"].get("verdict") != "feasible" \
            or adoption["pilot_sympy_governance"].get("repo_n") != 200:
        raise V2BError("pilot governance N=200 rationale drift")

    sample = value["sample"]
    if sample.get("requested_n") != 200 \
            or sample.get("required_realized_primary_eligible_n") != 200 \
            or sample.get("pilot_targets_excluded") is not True \
            or sample.get("pilot_source_modules_excluded") is not True \
            or sample.get("pilot_and_confirmation_never_pooled") is not True \
            or sample.get("sampling_seed") != "v2a:20260808" \
            or sample.get("shortfall_rule") != "abort-before-score" \
            or sample.get("identical_target_cohort_across_models") is not True \
            or sample.get("analysis_weighting") != "equal-target":
        raise V2BError("confirmation sample contract drift")

    source_gate = value["source_eligibility_gate"]
    if source_gate.get("schema") != \
            "v2b_nll_e2_confirmation_source_gate_v1" \
            or source_gate.get("candidate_universe_n") != 19926 \
            or source_gate.get("model_free_and_outcome_free") is not True \
            or source_gate.get("primary_cells") != \
            ["k4:16384", "k5:0:16384"] \
            or source_gate.get("pilot_exclusion_is_separate") is not True \
            or not isinstance(source_gate.get("pilot_module_exclusion"), str) \
            or source_gate.get("minimum_post_pilot_eligible_targets") != 200 \
            or source_gate.get("shortfall_rule") != \
            "abort-before-sample-and-score":
        raise V2BError("confirmation source-eligibility gate drift")

    models = value["models"]
    expected_models = [dict(id=row[0], name=row[1], revision=row[2],
                            nominal_billions=row[3], role=row[4])
                       for row in MODEL_ROWS]
    if models != expected_models:
        raise V2BError("confirmation model ladder/order/revision drift")
    if tuple(value["scored_cells"]) != SCORED_CELLS:
        raise V2BError("confirmation scored-cell grid drift")

    primary = value["primary"]
    if primary.get("id") != "E2_q25c_1p5b_seed0" \
            or primary.get("model_id") != "q25c-1.5b" \
            or primary.get("contrast") != \
            "BPB(k5:0:16384) - BPB(k4:16384)" \
            or primary.get("null") != "mu_E2 <= 0" \
            or primary.get("alternative") != "mu_E2 > 0" \
            or primary.get("alpha") != 0.05 \
            or primary.get("test") != "one-sided module-MoM Student-t":
        raise V2BError("confirmation primary hypothesis drift")

    inference = value["inference"]
    if inference.get("cluster") != "identity[0] source module" \
            or inference.get("degrees_of_freedom") != "G-1" \
            or inference.get("upper_icc_clamp") is not False \
            or inference.get("minimum_modules") != 20 \
            or inference.get("minimum_effective_clusters") != 10 \
            or inference.get("maximum_realized_two_sided_95_halfwidth_bpb") \
            != 0.02 \
            or inference.get("halfwidth_is_precision_not_effect_margin") \
            is not True \
            or inference.get("student_t_implementation") != \
            STUDENT_T_CONTRACT:
        raise V2BError("confirmation inference contract drift")

    gate = value["secondary_gate"]
    if gate.get("opens_only_if_primary_confirmed") is not True \
            or gate.get("closed_gate_status") != "descriptive-gate-closed" \
            or gate.get("method") != SECONDARY_METHOD \
            or tuple(row.get("id") for row in gate.get("endpoints", [])) != \
            SECONDARY_IDS:
        raise V2BError("confirmation secondary family drift")

    trend = value["model_size_trend"]
    observed_coefficients = tuple(trend.get("per_target_ols_coefficients", []))
    expected_coefficients = _ols_coefficients([row[3] for row in MODEL_ROWS])
    if observed_coefficients != SLOPE_COEFFICIENTS \
            or any(abs(a - b) > 2e-16 for a, b in
                   zip(observed_coefficients, expected_coefficients)) \
            or trend.get("allowed_claim") != \
            "finite Qwen2.5-Coder ladder trend" \
            or trend.get("forbidden_claim") != "model scaling law":
        raise V2BError("confirmation model-size trend drift")

    seed = value["k5_seed_sensitivity"]
    if seed.get("primary_seed") != 0 \
            or seed.get("diagnostic_seeds") != [1, 2] \
            or seed.get("cannot_rescue_primary") is not True:
        raise V2BError("confirmation k5 seed contract drift")
    eligibility = value["eligibility_and_missingness"]
    if eligibility.get("frozen_before_scores") is not True \
            or eligibility.get("primary_requires_cells") != \
            ["k4:16384", "k5:0:16384"] \
            or eligibility.get("no_imputation") is not True \
            or eligibility.get("no_available_cell_substitution") is not True \
            or eligibility.get("no_model_specific_target_replacement") \
            is not True \
            or "never triggers redraw or replacement" not in \
            eligibility.get("all_model_tokenizer_fit_gate", ""):
        raise V2BError("confirmation eligibility/missingness drift")
    if tuple(value["decision_labels_in_precedence_order"]) != DECISION_LABELS:
        raise V2BError("confirmation decision-label drift")
    sequencing = value["sequencing"]
    if any(sequencing.get(name) is not True for name in (
            "protocol_commit_before_sample", "source_gate_and_reducer_before_sample",
            "implementation_freeze_before_sample", "all_batteries_before_scoring",
            "confirmation_salt_commitment_before_scoring",
            "all_model_tokenizer_fit_before_scoring", "sample_and_scores_write_once",
            "analysis_implementation_committed_before_scoring",
            "fixed_n_blind_gate_before_reveal",
            "one_registered_reveal", "no_confirmation_score_exists_at_adoption")):
        raise V2BError("confirmation sequencing gate drift")
    return value


def load_protocol(path=PROTOCOL_PATH):
    value, digest = load_json(path, PROTOCOL_SCHEMA)
    if digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation protocol raw blob digest drift")
    return validate_protocol(value), digest


if __name__ == "__main__":
    protocol, digest = load_protocol()
    print(f"VALID {protocol['study_id']} {digest}")
