#!/usr/bin/env python3
"""Registered statistical analyzer for the fresh SymPy NLL confirmation.

This module implements only the analyses frozen in the confirmation protocol:
the 1.5B seed-0 E2 primary, its gated five-endpoint Holm family, descriptive
E1b intersection assays, k5 seed sensitivity, and model-free static-reference
coverage reporting.  Pilot and confirmation observations are never pooled.
"""
import argparse
import copy
import math
import os

from eval_v2b_nll_confirmation import git_is_ancestor
from finalize_v2b_nll_confirmation_reveal import (
    ANALYZER_PATH, REVEAL_SCHEMA, _git_blob_sha256, load_reveal,
    protocol_record,
    validate_analysis_registration,
    validate_implementation_freeze,
)
from finalize_v2b_nll_confirmation_fixed_n import FIXED_N_SCHEMA
from prepare_v2b_nll_confirmation_assembly import (
    ASSEMBLY_SCHEMA, REFERENCE_COVERAGE_BINS, REFERENCE_COVERAGE_KEYS,
    SAMPLE_SCHEMA_CONFIRMATION,
)
from prepare_v2b_nll_confirmation_masked import MASKED_SCHEMA, MODEL_ORDER
from prepare_v2b_nll_confirmation_salt import (
    IMPLEMENTATION_FREEZE_SCHEMA, SALT_COMMITMENT_SCHEMA,
)
from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (
    V2BError, artifact_binding, sha256_file, sha256_json,
    sha256_sorted_json, write_new_json,
)
from v2b_nll_confirmation import (
    DECISION_LABELS, PROTOCOL_PATH, PROTOCOL_RAW_SHA256,
    SECONDARY_IDS, SLOPE_COEFFICIENTS, load_protocol,
)
from v2b_nll_confirmation_stats import (
    holm_adjust as _stats_holm_adjust,
    p_greater as _stats_p_greater,
    p_two_sided as _stats_p_two_sided,
    student_t_cdf as _stats_student_t_cdf,
    student_t_quantile as _stats_student_t_quantile,
    student_t_sf as _stats_student_t_sf,
    summarize as _stats_summarize,
)


ANALYSIS_SCHEMA = "v2b_nll_e2_confirmation_analysis_v1"
ANALYSIS_STATE = "registered-confirmation-analysis-complete"
PROGRAM = os.path.basename(__file__)
N_TARGETS = 200
ALPHA = 0.05
MAX_HALFWIDTH = 0.02
MIN_MODULES = 20
MIN_EFFECTIVE_CLUSTERS = 10
E1B_MARGIN = 0.02

GENERATOR_KEYS = {
    "program", "program_sha256", "source_commit", "source_tree_hash"}
TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "analysis_registration",
    "cohort", "coverage", "eligibility_sets", "primary", "secondary",
    "e1b_descriptive", "k5_seed_sensitivity", "claim_scope",
    "prohibited_claims", "input_ledger", "generator",
}
ANALYSIS_BINDING_SCHEMAS = {
    "implementation_freeze": IMPLEMENTATION_FREEZE_SCHEMA,
    "source_gate": "v2b_nll_e2_confirmation_source_gate_v1",
    "bound_sample": SAMPLE_SCHEMA_CONFIRMATION,
    "assembly": ASSEMBLY_SCHEMA,
    "salt_commitment": "v2b_nll_e2_confirmation_salt_commitment_v1",
    "study_complete": "v2b_nll_e2_confirmation_study_complete_v1",
    "masked": MASKED_SCHEMA,
    "fixed_n": FIXED_N_SCHEMA,
    "reveal": REVEAL_SCHEMA,
}
INFERENCE_KEYS = {
    "n_targets", "n_modules", "effective_clusters", "cluster_sizes",
    "module_counts", "target_keys", "target_keys_sha256",
    "target_range_bpb", "degeneracy_floor_bpb", "target_equal_mean_bpb",
    "variance_components", "standard_error_bpb", "degrees_of_freedom",
    "ci95_two_sided_bpb", "two_sided_95_halfwidth_bpb",
    "lower_one_sided_95_bpb", "upper_one_sided_95_bpb",
    "cluster_support_passed", "inference_status",
}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _number(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(float(value)):
        raise V2BError(f"{label} is not finite binary64")
    return float(value)


def key_set(keys):
    ordered = sorted(keys)
    if any(not isinstance(key, str) or not key for key in ordered) \
            or len(ordered) != len(set(ordered)):
        raise V2BError("malformed analysis key set")
    return dict(n=len(ordered), sha256=sha256_json(ordered), keys=ordered)


def _generator(value, registration=None, freeze=None):
    _exact_keys(value, GENERATOR_KEYS, "confirmation analysis generator")
    if value["program"] != PROGRAM or not _hex(value["program_sha256"]) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("malformed confirmation analysis generator")
    if registration is not None and (
            value["program_sha256"] != registration["analyzer_sha256"]
            or freeze is not None
            and value["source_tree_hash"] != freeze["source_tree_hash"]):
        raise V2BError("analysis generator differs from registration/freeze")
    return value


def student_t_cdf(value, df):
    return _stats_student_t_cdf(value, df)


def student_t_sf(value, df):
    return _stats_student_t_sf(value, df)


def student_t_quantile(probability, df):
    if probability not in (0.95, 0.975):
        raise V2BError("unregistered Student-t quantile query")
    return _stats_student_t_quantile(probability, df)


def inference(rows):
    """Frozen stats-kernel result plus exact reporting ledgers."""
    summary = _stats_summarize(rows)
    counts = {}
    for row in rows:
        counts[row["module"]] = counts.get(row["module"], 0) + 1
    module_counts = [[module, counts[module]] for module in sorted(counts)]
    support = summary["support_status"] == "adequate"
    if summary["n_targets"] == 0:
        status = "no-observations"
    elif not support:
        status = "cluster-support-inadequate"
    elif summary["inference_status"] == "degenerate":
        status = "degenerate-zero-se"
    else:
        status = summary["inference_status"]
    inferential = status in {"available", "degenerate-zero-se"}
    return dict(
        n_targets=summary["n_targets"], n_modules=summary["n_modules"],
        effective_clusters=summary["effective_clusters"],
        cluster_sizes=summary["cluster_sizes"],
        module_counts=module_counts,
        target_keys=summary["target_keys"],
        target_keys_sha256=sha256_json(summary["target_keys"]),
        target_range_bpb=summary["target_range_bpb"],
        degeneracy_floor_bpb=summary["degeneracy_tolerance_bpb"],
        target_equal_mean_bpb=summary["target_equal_mean_bpb"],
        variance_components=summary["variance_components"],
        standard_error_bpb=(summary["standard_error_bpb"]
                            if inferential else None),
        degrees_of_freedom=(summary["degrees_of_freedom"]
                            if inferential else None),
        ci95_two_sided_bpb=(summary["ci95_two_sided_bpb"]
                            if inferential else None),
        two_sided_95_halfwidth_bpb=(
            summary["two_sided_95_halfwidth_bpb"]
            if inferential else None),
        lower_one_sided_95_bpb=(summary["lower_one_sided_95_bpb"]
                                if inferential else None),
        upper_one_sided_95_bpb=(summary["upper_one_sided_95_bpb"]
                                if inferential else None),
        cluster_support_passed=support, inference_status=status)


def p_greater(summary, null=0.0):
    if summary["inference_status"] != "available":
        return 1.0
    return _stats_p_greater(summary, null)


def p_two_sided(summary, null=0.0):
    if summary["inference_status"] != "available":
        return 1.0
    return _stats_p_two_sided(summary, null)


def holm_adjust(pvalues):
    result = _stats_holm_adjust(pvalues)
    return dict(
        method="Holm-step-down-FWER-0.05",
        deterministic_tie_break="endpoint-id-ascending",
        family_size=5, **result)


def primary_decision(provenance_valid, cluster_support_valid,
                     execution_complete, summary, raw_pvalue):
    """Apply the protocol's exact decision-label precedence."""
    if provenance_valid is not True:
        return DECISION_LABELS[0]
    if cluster_support_valid is not True:
        return DECISION_LABELS[1]
    if execution_complete is not True:
        return DECISION_LABELS[2]
    if summary["inference_status"] != "available":
        return DECISION_LABELS[3]
    mean = summary["target_equal_mean_bpb"]
    if mean > 0.0 \
            and summary["two_sided_95_halfwidth_bpb"] > MAX_HALFWIDTH:
        return DECISION_LABELS[4]
    if raw_pvalue <= ALPHA \
            and summary["lower_one_sided_95_bpb"] > 0.0:
        return DECISION_LABELS[5]
    if mean < 0.0:
        return DECISION_LABELS[6]
    return DECISION_LABELS[7]


def _sample_support(sample, assembly, protocol):
    if not isinstance(sample, dict) \
            or sample.get("schema") != SAMPLE_SCHEMA_CONFIRMATION \
            or sample.get("study_id") != protocol["study_id"] \
            or sample.get("protocol") != protocol_record() \
            or sample.get("requested_n") != N_TARGETS \
            or sample.get("realized_n") != N_TARGETS:
        raise V2BError("analysis bound sample identity/count drift")
    binding = assembly["bindings"]["bound_sample"]
    if sample.get("bindings", {}).get("implementation_freeze") != \
            assembly["bindings"]["implementation_freeze"]:
        raise V2BError("sample/assembly implementation-freeze drift")
    keys = [target["key"] for target in assembly["targets"]]
    if sample.get("selected_keys", {}).get("keys") != sorted(keys):
        raise V2BError("sample selected-key set differs from assembly")
    counts = {}
    for target in assembly["targets"]:
        counts[target["module"]] = counts.get(target["module"], 0) + 1
    rows = [[module, counts[module]] for module in sorted(counts)]
    denominator = sum(count * count for count in counts.values())
    effective = N_TARGETS * N_TARGETS / denominator
    expected = dict(
        n_targets=N_TARGETS, n_modules=len(rows), module_counts=rows,
        module_counts_sha256=sha256_json(rows),
        effective_clusters=effective,
        effective_clusters_numerator=N_TARGETS * N_TARGETS,
        effective_clusters_denominator=denominator,
        minimum_modules=MIN_MODULES,
        minimum_effective_clusters=MIN_EFFECTIVE_CLUSTERS,
        passed=len(rows) >= MIN_MODULES
        and effective >= MIN_EFFECTIVE_CLUSTERS)
    if sample.get("cluster_support") != expected:
        raise V2BError("sample cluster-support record does not replay")
    return expected


def _revealed_index(reveal, assembly):
    targets = assembly.get("targets")
    if not isinstance(targets, list) or len(targets) != N_TARGETS:
        raise V2BError("analysis assembly lacks exact target cohort")
    expected_keys = [row["key"] for row in targets]
    if reveal.get("cohort", {}).get("ordered_target_keys") != expected_keys:
        raise V2BError("analysis reveal/assembly cohort drift")
    out = {}
    for model, model_id in zip(reveal["models"], MODEL_ORDER):
        if model["model_id"] != model_id:
            raise V2BError("analysis reveal model order drift")
        for family in model["families"]:
            contrast_id = family["contrast_id"]
            if (model_id, contrast_id) in out:
                raise V2BError("duplicate revealed family")
            rows = family["rows"]
            if len(rows) != N_TARGETS:
                raise V2BError("revealed family is not exact N=200")
            observed = []
            for target, row in zip(targets, rows):
                if row["target_key"] != target["key"] \
                        or row["module"] != target["module"]:
                    raise V2BError("revealed row/assembly identity drift")
                if row["structurally_eligible"] is True:
                    if row["padding_filtered"] is not False:
                        raise V2BError("eligible reveal row marked padding")
                    observed.append(dict(
                        target_key=row["target_key"], module=row["module"],
                        delta_bpb=_number(row["delta_bpb"],
                                          "revealed delta")))
                elif row["padding_filtered"] is not True \
                        or row["delta_bpb"] is not None:
                    raise V2BError("ineligible reveal row was not filtered")
            out[(model_id, contrast_id)] = observed
    expected = {(model, contrast) for model in MODEL_ORDER
                for contrast in (
                    "E1a", "E1b", "E2_seed0", "E2_seed1", "E2_seed2")}
    if set(out) != expected:
        raise V2BError("analysis reveal family grid incomplete")
    return out


def _coverage(assembly):
    bin_counts = {name: 0 for name in REFERENCE_COVERAGE_BINS}
    totals = {name: 0 for name in (
        "n_refs", "n_resolved_decl", "n_module_fallback", "n_external",
        "n_unresolved")}
    for target in assembly["targets"]:
        row = target["static_reference_coverage"]
        _exact_keys(row, REFERENCE_COVERAGE_KEYS,
                    "analysis static-reference coverage row")
        if row["coverage_bin"] not in bin_counts:
            raise V2BError("assembly coverage bin outside frozen bins")
        bin_counts[row["coverage_bin"]] += 1
        for name in totals:
            value = row[name]
            if not isinstance(value, int) or isinstance(value, bool) \
                    or value < 0:
                raise V2BError("malformed assembly coverage count")
            totals[name] += value
        if row["n_refs"] != sum(row[name] for name in (
                "n_resolved_decl", "n_module_fallback", "n_external",
                "n_unresolved")):
            raise V2BError("per-target coverage count arithmetic drift")
        fraction = (row["n_resolved_decl"] / row["n_refs"]
                    if row["n_refs"] else None)
        if row.get("resolved_fraction") != fraction:
            raise V2BError("per-target coverage fraction drift")
        if row["n_refs"] == 0:
            expected_bin = "no-references"
        elif fraction < 0.25:
            expected_bin = "[0,0.25)"
        elif fraction < 0.5:
            expected_bin = "[0.25,0.5)"
        elif fraction < 0.75:
            expected_bin = "[0.5,0.75)"
        elif fraction < 1.0:
            expected_bin = "[0.75,1)"
        else:
            expected_bin = "1.0"
        if row["coverage_bin"] != expected_bin:
            raise V2BError("per-target coverage frozen-bin drift")
    if sum(bin_counts.values()) != N_TARGETS \
            or totals["n_refs"] != sum(totals[name] for name in (
                "n_resolved_decl", "n_module_fallback", "n_external",
                "n_unresolved")):
        raise V2BError("coverage aggregate arithmetic drift")
    return dict(
        n_selected_targets=N_TARGETS,
        frozen_bins=list(REFERENCE_COVERAGE_BINS),
        bin_counts=[[name, bin_counts[name]]
                    for name in REFERENCE_COVERAGE_BINS],
        sums=totals,
        aggregate_resolved_fraction=(
            totals["n_resolved_decl"] / totals["n_refs"]
            if totals["n_refs"] else None),
        eligibility_effect="report-only-never-filters-or-replaces")


def _eligibility_sets(index):
    rows = []
    for contrast_id in (
            "E1a", "E1b", "E2_seed0", "E2_seed1", "E2_seed2"):
        per_model = []
        sets = []
        for model_id in MODEL_ORDER:
            keys = {row["target_key"]
                    for row in index[(model_id, contrast_id)]}
            sets.append(keys)
            per_model.append(dict(model_id=model_id,
                                  observed_keys=key_set(keys)))
        intersection = set.intersection(*sets)
        union = set.union(*sets)
        rows.append(dict(
            contrast_id=contrast_id, models=per_model,
            cross_model_intersection=key_set(intersection),
            cross_model_union=key_set(union),
            identical_across_models=all(group == sets[0]
                                        for group in sets[1:])))
    return rows


def _primary(index, sample_support):
    rows = index[("q25c-1.5b", "E2_seed0")]
    if len(rows) != N_TARGETS:
        raise V2BError("primary E2 is not exact fixed N=200")
    summary = inference(rows)
    raw_p = p_greater(summary)
    label = primary_decision(
        True, sample_support["passed"] and
        summary["cluster_support_passed"], True, summary, raw_p)
    return dict(
        endpoint_id="E2_q25c_1p5b_seed0", model_id="q25c-1.5b",
        contrast_id="E2_seed0", orientation="k5:0:16384-k4:16384",
        alternative="mean>0", alpha=ALPHA,
        maximum_two_sided_95_halfwidth_bpb=MAX_HALFWIDTH,
        summary=summary, one_sided_raw_pvalue=raw_p,
        decision_label=label,
        confirmed=label == "e2-positive-confirmed-fresh-sympy-1p5b",
        decision_labels_in_precedence_order=list(DECISION_LABELS))


def _slope_rows(index):
    by_model = {
        model_id: {row["target_key"]: row
                   for row in index[(model_id, "E2_seed0")]}
        for model_id in MODEL_ORDER}
    common = set.intersection(*(set(rows) for rows in by_model.values()))
    if len(common) != N_TARGETS:
        raise V2BError("four-model E2 slope is not exact common N=200")
    order = [row["target_key"]
             for row in index[(MODEL_ORDER[0], "E2_seed0")]]
    rows = []
    for key in order:
        values = [by_model[model][key]["delta_bpb"]
                  for model in MODEL_ORDER]
        slope = math.fsum(coefficient * value for coefficient, value in
                          zip(SLOPE_COEFFICIENTS, values))
        rows.append(dict(
            target_key=key, module=by_model[MODEL_ORDER[0]][key]["module"],
            delta_bpb=slope))
    return rows


def _secondary(index, primary):
    definitions = (
        ("E1a_1p5b", "q25c-1.5b", "E1a", "one-sided"),
        ("E2_0p5b", "q25c-0.5b", "E2_seed0", "one-sided"),
        ("E2_3b", "q25c-3b", "E2_seed0", "one-sided"),
        ("E2_7b", "q25c-7b", "E2_seed0", "one-sided"),
    )
    summaries = {}
    metadata = {}
    for endpoint, model, contrast, tail in definitions:
        summaries[endpoint] = inference(index[(model, contrast)])
        metadata[endpoint] = dict(model_id=model, contrast_id=contrast,
                                  tail=tail)
    summaries["E2_logsize_slope"] = inference(_slope_rows(index))
    metadata["E2_logsize_slope"] = dict(
        model_id=None, contrast_id="E2_seed0-log2-size-slope",
        tail="two-sided")
    gate_open = primary["confirmed"] is True
    raw = {
        endpoint: (p_two_sided(summary) if endpoint ==
                   "E2_logsize_slope" else p_greater(summary))
        for endpoint, summary in summaries.items()}
    holm = holm_adjust(raw) if gate_open else None
    endpoints = []
    for endpoint in SECONDARY_IDS:
        summary = summaries[endpoint]
        if not gate_open:
            label, raw_p, adjusted = "descriptive-gate-closed", None, None
        else:
            raw_p = raw[endpoint]
            adjusted = holm["adjusted_pvalues"][endpoint]
            if summary["inference_status"] != "available":
                label = "secondary-inference-unavailable"
            elif summary["two_sided_95_halfwidth_bpb"] > MAX_HALFWIDTH:
                label = "secondary-precision-not-met"
            elif adjusted <= ALPHA:
                label = ("secondary-nonzero-supported"
                         if endpoint == "E2_logsize_slope"
                         else "secondary-positive-supported")
            else:
                label = "secondary-null-not-rejected"
        endpoints.append(dict(
            endpoint_id=endpoint, **metadata[endpoint], summary=summary,
            raw_pvalue=raw_p, holm_adjusted_pvalue=adjusted, label=label))
    return dict(
        gate_opens_only_if_primary_confirmed=True, gate_open=gate_open,
        closed_gate_status="descriptive-gate-closed",
        method="Holm-step-down-FWER-0.05-five-endpoint-family",
        slope_coefficients=list(SLOPE_COEFFICIENTS),
        slope_claim_scope="finite-Qwen2.5-Coder-ladder-not-scaling-law",
        holm=holm, endpoints=endpoints)


def _e1b_descriptive(index):
    reports = []
    for model_id in MODEL_ORDER:
        e1b = index[(model_id, "E1b")]
        e1a_by_key = {row["target_key"]: row
                      for row in index[(model_id, "E1a")]}
        intersection = [row["target_key"] for row in e1b
                        if row["target_key"] in e1a_by_key]
        if len(intersection) != len(e1b):
            raise V2BError("E1a/E1b exact intersection drift")
        e1a = [e1a_by_key[key] for key in intersection]
        e1b_summary, e1a_summary = inference(e1b), inference(e1a)
        if e1b_summary["inference_status"] != "available" \
                or e1a_summary["inference_status"] != "available":
            label = "descriptive-inference-unavailable"
        elif e1b_summary["upper_one_sided_95_bpb"] >= E1B_MARGIN:
            label = "descriptive-noninferiority-not-established"
        elif e1a_summary["lower_one_sided_95_bpb"] <= 0.0:
            label = "descriptive-assay-insensitive"
        else:
            label = "descriptive-interface-compatibility-pattern"
        reports.append(dict(
            model_id=model_id, margin_bpb=E1B_MARGIN,
            common_intersection=key_set(intersection),
            e1b_summary=e1b_summary,
            e1a_on_intersection_summary=e1a_summary,
            confirmatory_p_value=False, label=label))
    return dict(
        contrast="k3:16384-k4:16384",
        prohibited_terms=["equivalent", "interfaces suffice"],
        reports=reports)


def _k5_seed_sensitivity(index):
    reports = []
    for model_id in MODEL_ORDER:
        by_seed = [{row["target_key"]: row for row in
                    index[(model_id, f"E2_seed{seed}")]}
                   for seed in (0, 1, 2)]
        common = set.intersection(*(set(rows) for rows in by_seed))
        order = [row["target_key"] for row in
                 index[(model_id, "E2_seed0")] if row["target_key"] in common]
        if not order:
            reports.append(dict(
                model_id=model_id, common_case_keys=key_set([]),
                seed_means_bpb=None, mean_target_three_seed_average_bpb=None,
                range_seed_means_bpb=None,
                mean_target_sample_standard_deviation_bpb=None,
                direction_stable=False, magnitude_sensitivity_threshold_bpb=None,
                magnitude_sensitive=False, status="diagnostic-unavailable"))
            continue
        values = [[by_seed[seed][key]["delta_bpb"] for key in order]
                  for seed in range(3)]
        means = [math.fsum(seed_values) / len(order)
                 for seed_values in values]
        target_averages = [math.fsum(values[seed][index]
                                     for seed in range(3)) / 3.0
                           for index in range(len(order))]
        target_sds = []
        for index_ in range(len(order)):
            triple = [values[seed][index_] for seed in range(3)]
            mean = math.fsum(triple) / 3.0
            target_sds.append(math.sqrt(
                math.fsum((value - mean) ** 2 for value in triple) / 2.0))
        threshold = max(0.005, 0.5 * abs(means[0]))
        reports.append(dict(
            model_id=model_id, common_case_keys=key_set(order),
            seed_means_bpb=means,
            mean_target_three_seed_average_bpb=
            math.fsum(target_averages) / len(target_averages),
            range_seed_means_bpb=max(means) - min(means),
            mean_target_sample_standard_deviation_bpb=
            math.fsum(target_sds) / len(target_sds),
            direction_stable=all(value > 0.0 for value in means),
            magnitude_sensitivity_threshold_bpb=threshold,
            magnitude_sensitive=max(abs(means[1] - means[0]),
                                      abs(means[2] - means[0])) > threshold,
            status="descriptive-seed-sensitivity-complete"))
    return dict(primary_seed=0, diagnostic_seeds=[1, 2],
                cannot_rescue_primary=True, reports=reports)


def analysis_sections(protocol, assembly, sample, reveal):
    support = _sample_support(sample, assembly, protocol)
    index = _revealed_index(reveal, assembly)
    eligibility = _eligibility_sets(index)
    if any(row["identical_across_models"] is not True
           for row in eligibility):
        raise V2BError("model-specific eligibility/target replacement drift")
    primary = _primary(index, support)
    return dict(
        cohort=dict(
            n_selected=N_TARGETS, pilot_n_excluded=20,
            pilot_and_confirmation_never_pooled=True,
            ordered_target_keys=copy.deepcopy(
                reveal["cohort"]["ordered_target_keys"]),
            ordered_target_keys_sha256=reveal["cohort"][
                "ordered_target_keys_sha256"],
            pre_score_cluster_support=copy.deepcopy(support)),
        coverage=_coverage(assembly), eligibility_sets=eligibility,
        primary=primary, secondary=_secondary(index, primary),
        e1b_descriptive=_e1b_descriptive(index),
        k5_seed_sensitivity=_k5_seed_sensitivity(index),
        claim_scope=dict(
            repo="sympy", language="python", budget_bytes=16384,
            fresh_confirmation_only=True, equal_target_weighting=True,
            no_language_pooling=True, no_cross_family_model_claim=True,
            model_trend_scope="finite-ladder-not-scaling-law"),
        prohibited_claims=copy.deepcopy(protocol["prohibited_claims"]))


def _analysis_bindings(reveal, reveal_binding):
    bindings = copy.deepcopy(reveal["bindings"])
    bindings["reveal"] = copy.deepcopy(reveal_binding)
    if set(bindings) != set(ANALYSIS_BINDING_SCHEMAS):
        raise V2BError("analysis predecessor binding set drift")
    for label, schema in ANALYSIS_BINDING_SCHEMAS.items():
        row = bindings[label]
        _exact_keys(row, {"path", "schema", "sha256"},
                    f"analysis {label} binding")
        if row["schema"] != schema or not _hex(row["sha256"]) \
                or not isinstance(row["path"], str) or not row["path"]:
            raise V2BError(f"malformed analysis {label} binding")
    return bindings


def _validate_input_ledger(value, bindings, registration):
    _exact_keys(value, {
        "algorithm", "n_entries", "entries", "entries_sha256",
        "private_pre_post_equal"},
        "analysis input ledger")
    expected = {
        "protocol": PROTOCOL_RAW_SHA256,
        "implementation_freeze": bindings["implementation_freeze"][
            "sha256"],
        "bound_sample": bindings["bound_sample"]["sha256"],
        "assembly": bindings["assembly"]["sha256"],
        "salt_commitment": bindings["salt_commitment"]["sha256"],
        "masked": bindings["masked"]["sha256"],
        "fixed_n": bindings["fixed_n"]["sha256"],
        "reveal": bindings["reveal"]["sha256"],
        "registered_analyzer": registration["analyzer_sha256"],
    }
    entries = value["entries"]
    if value["algorithm"] != "sha256-only-public-input-ledger-v1" \
            or value["private_pre_post_equal"] is not True \
            or not isinstance(entries, list) \
            or value["n_entries"] != len(entries):
        raise V2BError("analysis input-ledger header drift")
    observed = {}
    labels = []
    for row in entries:
        _exact_keys(row, {"label", "sha256"},
                    "analysis input-ledger row")
        if not isinstance(row["label"], str) or not row["label"] \
                or not _hex(row["sha256"]):
            raise V2BError("malformed analysis input-ledger row")
        if row["label"] in observed:
            raise V2BError("duplicate analysis input-ledger label")
        labels.append(row["label"])
        observed[row["label"]] = row["sha256"]
    digest = sha256_sorted_json(entries)
    if labels != sorted(expected) or observed != expected \
            or value["entries_sha256"] != digest:
        raise V2BError("analysis input-ledger/hash/binding drift")
    return value


def ledger_record(pre, post):
    if pre != post:
        raise V2BError("analysis inputs changed during execution")
    projected = []
    for row in pre:
        _exact_keys(row, {"label", "bytes", "sha256"},
                    "private analysis input-ledger row")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError("malformed private analysis input-ledger row")
        projected.append(dict(label=row["label"], sha256=row["sha256"]))
    projected.sort(key=lambda row: row["label"])
    if len({row["label"] for row in projected}) != len(projected):
        raise V2BError("duplicate private analysis input-ledger label")
    digest = sha256_sorted_json(projected)
    return dict(
        algorithm="sha256-only-public-input-ledger-v1",
        n_entries=len(projected), entries=projected,
        entries_sha256=digest, private_pre_post_equal=True)


def build_analysis_value(protocol, assembly, sample, reveal, reveal_binding,
                         input_ledger, generator, freeze,
                         ancestor_fn=None, current_sha_fn=None,
                         commit_sha_fn=None):
    bindings = _analysis_bindings(reveal, reveal_binding)
    registration = copy.deepcopy(reveal["analysis_registration"])
    _generator(generator, registration, freeze)
    _validate_input_ledger(input_ledger, bindings, registration)
    sections = analysis_sections(protocol, assembly, sample, reveal)
    value = dict(
        schema=ANALYSIS_SCHEMA, state=ANALYSIS_STATE,
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=protocol_record(), bindings=bindings,
        analysis_registration=registration,
        input_ledger=copy.deepcopy(input_ledger),
        generator=copy.deepcopy(generator), **sections)
    return validate_analysis(
        value, protocol, assembly, sample, reveal, reveal_binding, freeze,
        ancestor_fn=ancestor_fn, current_sha_fn=current_sha_fn,
        commit_sha_fn=commit_sha_fn)


def validate_analysis(value, protocol, assembly, sample, reveal,
                      reveal_binding, freeze, ancestor_fn=None,
                      current_sha_fn=None, commit_sha_fn=None):
    _exact_keys(value, TOP_KEYS, "confirmation analysis")
    if value["schema"] != ANALYSIS_SCHEMA or value["state"] != ANALYSIS_STATE \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != "sympy" or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != protocol_record():
        raise V2BError("confirmation analysis identity drift")
    bindings = _analysis_bindings(reveal, reveal_binding)
    if value["bindings"] != bindings \
            or value["analysis_registration"] != \
            reveal["analysis_registration"]:
        raise V2BError("analysis/reveal predecessor registration drift")
    freeze_binding = bindings["implementation_freeze"]
    validate_implementation_freeze(freeze, freeze_binding, protocol)
    validate_analysis_registration(
        value["analysis_registration"], freeze, freeze_binding,
        reveal["ancestry"]["scoring_source_commit"],
        ancestor_fn=ancestor_fn or git_is_ancestor,
        current_sha_fn=current_sha_fn, commit_sha_fn=commit_sha_fn)
    _generator(value["generator"], value["analysis_registration"], freeze)
    ancestry_check = ancestor_fn or git_is_ancestor
    if not ancestry_check(
            reveal["ancestry"]["reveal_source_commit"],
            value["generator"]["source_commit"]):
        raise V2BError("reveal/analysis committed ancestry does not replay")
    committed_blob_sha = commit_sha_fn or _git_blob_sha256
    if committed_blob_sha(
            value["generator"]["source_commit"], ANALYZER_PATH) != \
            value["analysis_registration"]["analyzer_sha256"]:
        raise V2BError("analysis execution-commit source differs from freeze")
    _validate_input_ledger(
        value["input_ledger"], bindings, value["analysis_registration"])
    expected = analysis_sections(protocol, assembly, sample, reveal)
    for name, expected_value in expected.items():
        if value[name] != expected_value:
            raise V2BError(f"confirmation analysis recomputation drift: {name}")
    return value


def _file_ledger(label_paths):
    rows = []
    for label, path in label_paths:
        rows.append(dict(label=label, bytes=os.path.getsize(path),
                         sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    return rows


def prepare(reveal_path, assembly_path, sample_path, masked_path,
            fixed_n_path, implementation_freeze_path,
            salt_commitment_path,
            protocol_path=PROTOCOL_PATH):
    if not source_clean():
        raise V2BError("source tree dirty before confirmation analysis")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation analysis requires canonical protocol")
    commit, tree = head_commit(), source_tree_hash()
    paths = (
        ("protocol", protocol_path),
        ("implementation_freeze", implementation_freeze_path),
        ("bound_sample", sample_path), ("assembly", assembly_path),
        ("salt_commitment", salt_commitment_path),
        ("masked", masked_path), ("fixed_n", fixed_n_path),
        ("reveal", reveal_path),
        ("registered_analyzer", os.path.join(BASE, ANALYZER_PATH)),
    )
    for _label, path in paths:
        require_committed(path)
    pre = _file_ledger(paths)
    protocol, digest = load_protocol(protocol_path)
    if digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation analysis protocol raw digest drift")
    assembly_binding_raw, assembly = artifact_binding(
        assembly_path, ASSEMBLY_SCHEMA)
    sample_binding_raw, sample = artifact_binding(
        sample_path, SAMPLE_SCHEMA_CONFIRMATION)
    masked_binding_raw, masked = artifact_binding(masked_path, MASKED_SCHEMA)
    masked_binding = dict(
        path=masked_binding_raw["path"], schema=MASKED_SCHEMA,
        sha256=masked_binding_raw["sha256"])
    fixed_binding_raw, fixed = artifact_binding(fixed_n_path, FIXED_N_SCHEMA)
    fixed_binding = dict(
        path=fixed_binding_raw["path"], schema=FIXED_N_SCHEMA,
        sha256=fixed_binding_raw["sha256"])
    freeze_binding_raw, freeze = artifact_binding(
        implementation_freeze_path, IMPLEMENTATION_FREEZE_SCHEMA)
    freeze_binding = dict(
        path=freeze_binding_raw["path"], schema=IMPLEMENTATION_FREEZE_SCHEMA,
        sha256=freeze_binding_raw["sha256"])
    validate_implementation_freeze(freeze, freeze_binding, protocol)
    commitment_binding_raw, commitment = artifact_binding(
        salt_commitment_path, SALT_COMMITMENT_SCHEMA)
    commitment_record = dict(
        path=commitment_binding_raw["path"],
        sha256=commitment_binding_raw["sha256"], value=commitment)
    reveal, reveal_digest = load_reveal(
        reveal_path, protocol, assembly, masked, masked_binding, fixed,
        fixed_binding, freeze, freeze_binding, commitment_record)
    actual_assembly_binding = dict(
        path=assembly_binding_raw["path"], schema=ASSEMBLY_SCHEMA,
        sha256=assembly_binding_raw["sha256"])
    actual_sample_binding = dict(
        path=sample_binding_raw["path"], schema=SAMPLE_SCHEMA_CONFIRMATION,
        sha256=sample_binding_raw["sha256"])
    if reveal["bindings"]["assembly"] != actual_assembly_binding \
            or reveal["bindings"]["bound_sample"] != actual_sample_binding:
        raise V2BError("analysis sample/assembly bytes differ from reveal")
    reveal_binding = dict(
        path=os.path.abspath(reveal_path), schema=REVEAL_SCHEMA,
        sha256=reveal_digest)
    validate_analysis_registration(
        reveal["analysis_registration"], freeze, freeze_binding,
        reveal["ancestry"]["scoring_source_commit"])
    generator = dict(
        program=PROGRAM, program_sha256=sha256_file(__file__),
        source_commit=commit, source_tree_hash=tree)
    value = build_analysis_value(
        protocol, assembly, sample, reveal, reveal_binding,
        ledger_record(pre, pre), generator, freeze)
    post = _file_ledger(paths)
    if pre != post or not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("analysis inputs/source changed during execution")
    value["input_ledger"] = ledger_record(pre, post)
    return validate_analysis(
        value, protocol, assembly, sample, reveal, reveal_binding, freeze)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reveal", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--masked", required=True)
    parser.add_argument("--fixed-n", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--salt-commitment", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite confirmation analysis")
    value = prepare(
        args.reveal, args.assembly, args.sample, args.masked, args.fixed_n,
        args.implementation_freeze, args.salt_commitment, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-analysis] "
          f"{value['primary']['decision_label']} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(2)
