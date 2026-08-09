#!/usr/bin/env python3
import copy
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError, sha256_file, sha256_sorted_json
from v2b_nll_confirmation import (
    DECISION_LABELS, MODEL_ROWS, PROTOCOL_PATH, SCORED_CELLS,
    SECONDARY_IDS, SLOPE_COEFFICIENTS, PROTOCOL_RAW_SHA256,
    PROTOCOL_SEMANTIC_SHA256, load_protocol, validate_protocol)


def _value():
    return json.load(open(PROTOCOL_PATH, encoding="utf-8"))


def _reject(value):
    try:
        validate_protocol(value)
        assert False, "drifted confirmation protocol validated"
    except V2BError:
        pass


def test_checked_in_confirmation_protocol_is_exact_and_loadable():
    value, digest = load_protocol()
    assert len(digest) == 64
    assert value["sample"]["requested_n"] == 200
    assert value["sample"]["pilot_and_confirmation_never_pooled"] is True
    assert value["sample"]["pilot_source_modules_excluded"] is True
    assert value["inputs"]["pilot_sympy_module_count"] == 19
    assert value["source_eligibility_gate"]["candidate_universe_n"] == 19926
    assert value["source_eligibility_gate"]["minimum_post_pilot_eligible_targets"] \
        == 200
    assert value["source_eligibility_gate"][
        "pilot_intersection_expected_from_sealed_pilot_evidence"][
            "keys_sha256"] == \
        "33f932a4426a2c0f9979acef023b55ba66b2ddfca5adea242528b7855a86ca66"
    assert tuple(value["scored_cells"]) == SCORED_CELLS
    assert tuple(row["id"] for row in value["models"]) == \
        tuple(row[0] for row in MODEL_ROWS)
    assert tuple(row["id"] for row in
                 value["secondary_gate"]["endpoints"]) == SECONDARY_IDS
    assert tuple(value["decision_labels_in_precedence_order"]) == \
        DECISION_LABELS
    assert DECISION_LABELS.index("cluster-support-inadequate") < \
        DECISION_LABELS.index("execution-incomplete-not-analyzed")
    blind = value["blinding_and_fixed_n_gate"]
    assert "before scoring" in blind["public_commitment"]
    assert "fixed-width ciphertext" in blind["masked_coverage"]
    assert "+0.0 padding is never an observation" in blind[
        "post_reveal_filter"]
    assert digest == PROTOCOL_RAW_SHA256 == sha256_file(PROTOCOL_PATH)
    assert sha256_sorted_json(value) == PROTOCOL_SEMANTIC_SHA256


def test_confirmation_protocol_rejects_model_cell_and_primary_drift():
    mutations = []
    changed = _value()
    changed["models"][3]["revision"] = "0" * 40
    mutations.append(changed)
    changed = _value()
    changed["models"].reverse()
    mutations.append(changed)
    changed = _value()
    changed["scored_cells"].append("k2:16384")
    mutations.append(changed)
    changed = _value()
    changed["primary"]["model_id"] = "q25c-7b"
    mutations.append(changed)
    changed = _value()
    changed["primary"]["alpha"] = 0.1
    mutations.append(changed)
    changed = _value()
    changed["sample"]["requested_n"] = 220
    mutations.append(changed)
    changed = _value()
    changed["source_eligibility_gate"]["primary_cells"] = ["k4:16384"]
    mutations.append(changed)
    for value in mutations:
        _reject(value)


def test_confirmation_protocol_rejects_inference_and_sequencing_drift():
    mutations = []
    changed = _value()
    changed["inference"]["maximum_realized_two_sided_95_halfwidth_bpb"] = 0.03
    mutations.append(changed)
    changed = _value()
    changed["secondary_gate"]["opens_only_if_primary_confirmed"] = False
    mutations.append(changed)
    changed = _value()
    changed["k5_seed_sensitivity"]["primary_seed"] = 1
    mutations.append(changed)
    changed = _value()
    changed["sequencing"]["all_batteries_before_scoring"] = False
    mutations.append(changed)
    changed = _value()
    changed["decision_labels_in_precedence_order"].reverse()
    mutations.append(changed)
    for value in mutations:
        _reject(value)


def test_model_size_slope_coefficients_are_the_frozen_ols_projection():
    xs = [math.log2(row[3]) for row in MODEL_ROWS]
    center = math.fsum(xs) / len(xs)
    denominator = math.fsum((value - center) ** 2 for value in xs)
    expected = tuple((value - center) / denominator for value in xs)
    assert SLOPE_COEFFICIENTS == expected
    assert abs(math.fsum(SLOPE_COEFFICIENTS)) <= 4e-17
    assert abs(math.fsum(c * x for c, x in zip(
        SLOPE_COEFFICIENTS, xs)) - 1.0) <= 4e-16


def test_protocol_loader_rejects_unknown_keys_and_wrong_schema():
    for mutate in (
            lambda value: value.update(extra_channel="forbidden"),
            lambda value: value.update(schema="future-schema")):
        value = _value()
        mutate(value)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "protocol.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
            try:
                load_protocol(path)
                assert False, "malformed protocol file loaded"
            except V2BError:
                pass


def test_every_scientific_contract_family_is_covered_by_whole_object_freeze():
    mutations = []

    def changed(edit):
        value = _value()
        edit(value)
        mutations.append(value)

    changed(lambda v: v["inputs"]["candidates"].update(sha256="0" * 64))
    changed(lambda v: v["scope"].update(claim_population="different"))
    changed(lambda v: v["sample"].update(sampler="different"))
    changed(lambda v: v["source_eligibility_gate"].update(
        predicate="context_bytes >= 16384"))
    changed(lambda v: v["source_eligibility_gate"]["bindings"]
            ["extraction"].update(sha256="1" * 64))
    changed(lambda v: v["instrument_gate"].update(
        failure_rule="substitute model"))
    changed(lambda v: v["primary"].update(success_requires=[]))
    changed(lambda v: v["inference"].update(
        estimator="naive iid", variance_of_mean="sample variance/N"))
    changed(lambda v: v["inference"].update(
        degeneracy_tolerance="range only"))
    changed(lambda v: v["secondary_gate"]["endpoints"][0].update(
        tail="two-sided"))
    changed(lambda v: v["secondary_gate"]["endpoints"][2].update(
        alternative="mu < 0"))
    changed(lambda v: v["model_size_trend"].update(
        complete_case_population="available targets"))
    changed(lambda v: v["e1b_descriptive"].update(
        confirmatory_p_value=True))
    changed(lambda v: v["k5_seed_sensitivity"].update(
        magnitude_sensitive="any difference"))
    changed(lambda v: v["blinding_and_fixed_n_gate"].update(
        family_domain="different"))
    changed(lambda v: v["blinding_and_fixed_n_gate"].update(
        fixed_width_ciphertext="unencrypted values"))
    changed(lambda v: v["sequencing"].update(
        confirmation_salt_commitment_before_scoring=False))
    changed(lambda v: v["execution_schema_contracts"].update(
        cell_enumerator="pilot grid"))
    changed(lambda v: v["eligibility_and_missingness"].update(
        persistent_infrastructure_failure="complete case"))
    changed(lambda v: v["decision_rule_conditions"].update(
        **{"e2-inconclusive": "anything"}))
    changed(lambda v: v.update(prohibited_claims=[]))
    changed(lambda v: v["primary"].update(extra_nested_key="forbidden"))
    for value in mutations:
        _reject(value)
