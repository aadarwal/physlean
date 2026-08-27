#!/usr/bin/env python3
"""Deterministic synthetic tests for the registered confirmation analyzer."""
import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyze_v2b_nll_confirmation import (
    ANALYSIS_SCHEMA, ANALYSIS_STATE, PROGRAM, SECONDARY_IDS,
    _analysis_bindings, _e1b_descriptive, _k5_seed_sensitivity,
    _slope_rows, analysis_sections, build_analysis_value, holm_adjust,
    inference, ledger_record, primary_decision, student_t_cdf,
    student_t_quantile, validate_analysis,
)
from finalize_v2b_nll_confirmation_reveal import REVEAL_SCHEMA
from finalize_v2b_nll_confirmation_sample import key_set as sample_key_set
from prepare_v2b_nll_confirmation_masked import publication_sha256
from prepare_v2b_nll_confirmation_salt import protocol_record
from v2b_common import V2BError, identity_key, sha256_json
from v2b_nll_confirmation import DECISION_LABELS, SLOPE_COEFFICIENTS

from test_finalize_v2b_nll_confirmation_reveal import (
    ANALYZER_SHA, reveal_fixture,
)


_CACHE = None


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation analysis"
    except V2BError as error:
        if text is not None:
            assert text in str(error), str(error)


def _callbacks():
    return dict(
        ancestor_fn=lambda _older, _newer: True,
        current_sha_fn=lambda _path: ANALYZER_SHA,
        commit_sha_fn=lambda _commit, _path: ANALYZER_SHA)


def _analysis_assembly(data):
    assembly = copy.deepcopy(data["assembly"])
    assembly["bindings"] = dict(
        implementation_freeze=copy.deepcopy(data["freeze_binding"]),
        bound_sample=copy.deepcopy(data["reveal"]["bindings"][
            "bound_sample"]))
    for index, target in enumerate(assembly["targets"]):
        if index % 6 == 0:
            coverage = dict(
                n_refs=0, n_resolved_decl=0, n_module_fallback=0,
                n_external=0, n_unresolved=0, resolved_fraction=None,
                coverage_bin="no-references")
        else:
            resolved = index % 5
            n_refs = 4
            fraction = resolved / n_refs
            if fraction < 0.25:
                bin_name = "[0,0.25)"
            elif fraction < 0.5:
                bin_name = "[0.25,0.5)"
            elif fraction < 0.75:
                bin_name = "[0.5,0.75)"
            elif fraction < 1.0:
                bin_name = "[0.75,1)"
            else:
                bin_name = "1.0"
            coverage = dict(
                n_refs=n_refs, n_resolved_decl=resolved,
                n_module_fallback=4 - resolved, n_external=0,
                n_unresolved=0, resolved_fraction=fraction,
                coverage_bin=bin_name)
        target["static_reference_coverage"] = coverage
    return assembly


def _sample(protocol, assembly):
    keys = [target["key"] for target in assembly["targets"]]
    counts = {}
    for target in assembly["targets"]:
        counts[target["module"]] = counts.get(target["module"], 0) + 1
    rows = [[module, counts[module]] for module in sorted(counts)]
    denominator = sum(count * count for count in counts.values())
    effective = 200 * 200 / denominator
    return dict(
        schema="v2b_nll_e2_confirmation_sample_v1",
        study_id=protocol["study_id"], protocol=protocol_record(),
        requested_n=200, realized_n=200,
        bindings=dict(implementation_freeze=copy.deepcopy(
            assembly["bindings"]["implementation_freeze"])),
        selected_keys=sample_key_set(keys),
        cluster_support=dict(
            n_targets=200, n_modules=len(rows), module_counts=rows,
            module_counts_sha256=sha256_json(rows),
            effective_clusters=effective,
            effective_clusters_numerator=40000,
            effective_clusters_denominator=denominator,
            minimum_modules=20, minimum_effective_clusters=10,
            passed=len(rows) >= 20 and effective >= 10))


def _ledger(bindings, registration):
    expected = {
        "protocol": __import__(
            "v2b_nll_confirmation").PROTOCOL_RAW_SHA256,
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
    rows = [dict(label=label, bytes=1, sha256=expected[label])
            for label in sorted(expected)]
    return ledger_record(rows, copy.deepcopy(rows))


def analysis_fixture():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    data = reveal_fixture()
    assembly = _analysis_assembly(data)
    sample = _sample(data["protocol"], assembly)
    reveal_binding = dict(
        path="/synthetic/reveal.json", schema=REVEAL_SCHEMA,
        sha256=publication_sha256(data["reveal"]))
    bindings = _analysis_bindings(data["reveal"], reveal_binding)
    generator = dict(
        program=PROGRAM, program_sha256=ANALYZER_SHA,
        source_commit="9" * 40,
        source_tree_hash=data["freeze"]["source_tree_hash"])
    ledger = _ledger(bindings, data["registration"])
    value = build_analysis_value(
        data["protocol"], assembly, sample, data["reveal"], reveal_binding,
        ledger, generator, data["freeze"], **_callbacks())
    _CACHE = dict(data)
    _CACHE.update(
        assembly=assembly, sample=sample, reveal_binding=reveal_binding,
        analysis=value, analysis_ledger=ledger,
        analysis_generator=generator)
    return _CACHE


def _validate(data, value):
    return validate_analysis(
        value, data["protocol"], data["assembly"], data["sample"],
        data["reveal"], data["reveal_binding"], data["freeze"],
        **_callbacks())


def _rows(values, prefix="k"):
    rows = []
    for index, value in enumerate(values):
        module = f"{prefix}.module{index:03d}"
        rows.append(dict(
            target_key=identity_key(
                "python", [module, f"function{index:03d}", index]),
            module=module, delta_bpb=value))
    return rows


def test_exact_registered_analysis_sections_and_determinism():
    data = analysis_fixture()
    value = data["analysis"]
    assert value["schema"] == ANALYSIS_SCHEMA
    assert value["state"] == ANALYSIS_STATE
    assert value["primary"]["model_id"] == "q25c-1.5b"
    assert value["primary"]["contrast_id"] == "E2_seed0"
    assert value["primary"]["summary"]["n_targets"] == 200
    assert len(value["secondary"]["endpoints"]) == 5
    assert {row["endpoint_id"] for row in
            value["secondary"]["endpoints"]} == set(SECONDARY_IDS)
    assert value["cohort"]["pilot_and_confirmation_never_pooled"] is True
    assert sum(count for _name, count in value["coverage"]["bin_counts"]) \
        == 200
    assert all(row["identical_across_models"] is True
               for row in value["eligibility_sets"])
    assert _validate(data, value) == value
    again = build_analysis_value(
        data["protocol"], data["assembly"], data["sample"], data["reveal"],
        data["reveal_binding"], data["analysis_ledger"],
        data["analysis_generator"], data["freeze"], **_callbacks())
    assert again == value


def test_student_t_binary64_contract_all_df_1_through_199():
    previous_95 = previous_975 = math.inf
    for df in range(1, 200):
        q95 = student_t_quantile(0.95, df)
        q975 = student_t_quantile(0.975, df)
        assert abs(student_t_cdf(q95, df) - 0.95) < 3e-14
        assert abs(student_t_cdf(q975, df) - 0.975) < 3e-14
        assert 0 < q95 < q975
        assert q95 <= previous_95 and q975 <= previous_975
        previous_95, previous_975 = q95, q975
    assert abs(student_t_quantile(0.975, 1) - 12.706204736) < 2e-9
    assert abs(student_t_quantile(0.95, 10) - 1.812461123) < 2e-9
    assert abs(student_t_quantile(0.975, 199) - 1.971956545) < 2e-9


def test_inference_degeneracy_support_and_primary_label_precedence():
    degenerate = inference(_rows([0.04] * 200, "d"))
    assert degenerate["inference_status"] == "degenerate-zero-se"
    inadequate = []
    for index in range(200):
        module = f"m{index % 5}"
        inadequate.append(dict(
            target_key=identity_key("python", [module, f"f{index}", index]),
            module=module, delta_bpb=index / 1000.0))
    inadequate = inference(inadequate)
    assert inadequate["inference_status"] == "cluster-support-inadequate"
    wide = inference(_rows(
        [-1.0 if index % 2 else 1.2 for index in range(200)], "w"))
    assert wide["two_sided_95_halfwidth_bpb"] > 0.02
    assert primary_decision(False, True, True, wide, 0.0) == \
        DECISION_LABELS[0]
    assert primary_decision(True, False, False, wide, 0.0) == \
        DECISION_LABELS[1]
    assert primary_decision(True, True, False, wide, 0.0) == \
        DECISION_LABELS[2]
    assert primary_decision(True, True, True, degenerate, 0.0) == \
        DECISION_LABELS[3]
    assert primary_decision(True, True, True, wide, 0.0) == \
        DECISION_LABELS[4]
    opposite = inference(_rows(
        [-0.02 + index * 1e-5 for index in range(200)], "o"))
    assert primary_decision(True, True, True, opposite, 1.0) == \
        DECISION_LABELS[6]


def test_holm_exact_family_ties_and_all_four_model_slope():
    raw = {
        "E1a_1p5b": 0.01, "E2_0p5b": 0.01, "E2_3b": 0.02,
        "E2_7b": 1.0, "E2_logsize_slope": 1.0}
    holm = holm_adjust(raw)
    assert holm["order"][:2] == ["E1a_1p5b", "E2_0p5b"]
    assert holm["adjusted_pvalues"]["E1a_1p5b"] == 0.05
    index = {}
    for model_index, model_id in enumerate(
            ("q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b")):
        index[(model_id, "E2_seed0")] = _rows(
            [float(model_index)] * 200, model_id)
    # Align keys/modules across models, as the production cohort requires.
    for model_id in ("q25c-1.5b", "q25c-3b", "q25c-7b"):
        for index_, row in enumerate(index[(model_id, "E2_seed0")]):
            row["target_key"] = index[("q25c-0.5b", "E2_seed0")][
                index_]["target_key"]
            row["module"] = index[("q25c-0.5b", "E2_seed0")][index_][
                "module"]
    slopes = _slope_rows(index)
    expected = math.fsum(coefficient * model_index
                         for model_index, coefficient in
                         enumerate(SLOPE_COEFFICIENTS))
    assert len(slopes) == 200
    assert all(abs(row["delta_bpb"] - expected) < 1e-15 for row in slopes)


def test_e1b_intersection_labels_and_seed_sensitivity_math():
    index = {}
    models = ("q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b")
    noise = [(position - 99.5) * 1e-6 for position in range(200)]
    for model_id in models:
        index[(model_id, "E1a")] = _rows(
            [0.04 + value for value in noise], f"{model_id}-e1a")
        index[(model_id, "E1b")] = _rows(
            [value for value in noise], f"{model_id}-e1b")
        # Align the exact E1a/E1b target/module intersection.
        for position, row in enumerate(index[(model_id, "E1b")]):
            row["target_key"] = index[(model_id, "E1a")][position][
                "target_key"]
            row["module"] = index[(model_id, "E1a")][position]["module"]
        for seed, offset in enumerate((0.01, 0.011, 0.009)):
            index[(model_id, f"E2_seed{seed}")] = _rows(
                [offset + value for value in noise], f"{model_id}-seed")
            for position, row in enumerate(
                    index[(model_id, f"E2_seed{seed}")]):
                row["target_key"] = index[(model_id, "E1a")][position][
                    "target_key"]
                row["module"] = index[(model_id, "E1a")][position]["module"]
    e1b = _e1b_descriptive(index)
    assert all(row["label"] ==
               "descriptive-interface-compatibility-pattern"
               for row in e1b["reports"])
    seeds = _k5_seed_sensitivity(index)
    first = seeds["reports"][0]
    assert abs(first["mean_target_three_seed_average_bpb"] - 0.01) < 1e-15
    assert abs(first["range_seed_means_bpb"] - 0.002) < 1e-15
    assert first["direction_stable"] is True
    assert first["magnitude_sensitive"] is False


def test_missing_padding_model_specific_and_analysis_tamper_fail_closed():
    data = analysis_fixture()
    missing = copy.deepcopy(data["reveal"])
    family = next(row for row in missing["models"][1]["families"]
                  if row["contrast_id"] == "E2_seed0")
    family["rows"].pop()
    _reject(lambda: analysis_sections(
        data["protocol"], data["assembly"], data["sample"], missing),
        "N=200")
    padding = copy.deepcopy(data["reveal"])
    family = next(row for row in padding["models"][0]["families"]
                  if row["contrast_id"] == "E1b")
    family["rows"][0]["padding_filtered"] = False
    family["rows"][0]["delta_bpb"] = 0.0
    _reject(lambda: analysis_sections(
        data["protocol"], data["assembly"], data["sample"], padding),
        "ineligible")
    model_specific = copy.deepcopy(data["reveal"])
    family = next(row for row in model_specific["models"][0]["families"]
                  if row["contrast_id"] == "E1b")
    observed = next(row for row in family["rows"]
                    if row["structurally_eligible"] is True)
    observed["structurally_eligible"] = False
    observed["padding_filtered"] = True
    observed["delta_bpb"] = None
    _reject(lambda: analysis_sections(
        data["protocol"], data["assembly"], data["sample"], model_specific),
        "model-specific")
    bad = copy.deepcopy(data["analysis"])
    bad["primary"]["decision_label"] = "e2-inconclusive"
    _reject(lambda: _validate(data, bad), "primary")


def test_analysis_registration_ledger_and_coverage_tamper_fail_closed():
    data = analysis_fixture()
    bad = copy.deepcopy(data["analysis"])
    bad["analysis_registration"]["analyzer_sha256"] = "0" * 64
    _reject(lambda: _validate(data, bad), "registration")
    bad = copy.deepcopy(data["analysis"])
    bad["input_ledger"]["entries"].pop()
    _reject(lambda: _validate(data, bad), "ledger")
    callbacks = _callbacks()
    callbacks["commit_sha_fn"] = lambda commit, _path: (
        "0" * 64 if commit == data["analysis_generator"]["source_commit"]
        else ANALYZER_SHA)
    _reject(lambda: validate_analysis(
        data["analysis"], data["protocol"], data["assembly"], data["sample"],
        data["reveal"], data["reveal_binding"], data["freeze"],
        **callbacks), "execution-commit source")
    assembly = copy.deepcopy(data["assembly"])
    assembly["targets"][0]["static_reference_coverage"][
        "coverage_bin"] = "1.0"
    _reject(lambda: analysis_sections(
        data["protocol"], assembly, data["sample"], data["reveal"]),
        "bin")
    pre = [dict(label="x", bytes=1, sha256="1" * 64)]
    post = [dict(label="x", bytes=2, sha256="1" * 64)]
    _reject(lambda: ledger_record(pre, post), "changed during")


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"[ok] {name}")
    print("confirmation analyzer synthetic tests: PASS")
