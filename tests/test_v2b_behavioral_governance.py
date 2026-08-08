#!/usr/bin/env python3
"""Deterministic arm-anonymous behavioral reliability governance."""
import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_behavioral_governance import (
    BEHAVIOR_ELIGIBILITY_FIELDS, BEHAVIOR_ELIGIBILITY_RULE,
    BEHAVIOR_BASELINE_COVERAGE_SCHEMA, BEHAVIOR_EVIDENCE_SCHEMA,
    BEHAVIOR_GOVERNANCE_SCHEMA, BEHAVIOR_MASKED_SCHEMA,
    BEHAVIOR_MASKED_STATE, BEHAVIOR_PLAN_SCHEMA,
    BEHAVIOR_SALT_COMMITMENT_SCHEMA, CANDIDATE_N, EDGE_RULES,
    EDGE_RULES_SHA256, GOVERNANCE_CONTRACT, GOVERNANCE_CONTRACT_SHA256,
    MIN_DIAGNOSTIC_TARGETS, MIN_GOVERNING_TARGETS, MODEL_BINDINGS, N_ARMS,
    N_DRAWS, N_PILOT_TARGETS, N_RESPLITS, analyze, pearson_or_zero,
    spearman_brown_clamped, validate_governance_bindings)
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        MASKED_DELTAS_SCHEMA, V2BError, identity_key,
                        sha256_json)


ARMS = [f"arm-{index:016x}" for index in range(N_ARMS)]
CLASSES = ("lean-theorem-proof", "lean-def-typecheck")


def _binding(schema, fill, salt=False):
    value = dict(schema=schema, sha256=fill * 64)
    if salt:
        value["salt_sha256"] = "9" * 64
    return value


def _masked(n_targets=N_PILOT_TARGETS, n_governing=10, constant=False):
    arms = {}
    for arm_index, arm in enumerate(ARMS):
        rows = []
        for target in range(n_targets):
            # Target-specific latent pass probability supplies the stable
            # signal; arm_index rotates draw positions without revealing a
            # named condition.
            n_pass = 0 if constant else 2 + (target % 10) * 3
            passes = [0] * N_DRAWS
            for draw in range(min(n_pass, N_DRAWS)):
                passes[(draw * 7 + arm_index * 3) % N_DRAWS] = 1
            rows.append(dict(
                target_key=identity_key(
                    "lean", [f"Fixture.Module{target:02d}",
                             f"decl{target:02d}"]),
                outcome_class=(CLASSES[0] if target < n_governing
                               else CLASSES[1]),
                eligibility={
                    field: True for field in BEHAVIOR_ELIGIBILITY_FIELDS
                },
                passes=passes))
        arms[arm] = rows
    return dict(
        schema=BEHAVIOR_MASKED_SCHEMA, state=BEHAVIOR_MASKED_STATE,
        repo="mathlib4", language="lean",
        corpus_git_sha="87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
        model_binding=dict(MODEL_BINDINGS[1]),
        n_targets=n_targets,
        n_draws_per_target_arm=N_DRAWS,
        n_rows_by_arm={arm: n_targets for arm in ARMS},
        bindings=dict(
            behavior_plan=_binding(BEHAVIOR_PLAN_SCHEMA, "1"),
            sample=_binding(BOUND_SAMPLE_SCHEMA, "2"),
            assembly=_binding(ASSEMBLY_SCHEMA, "3"),
            baseline_coverage=_binding(
                BEHAVIOR_BASELINE_COVERAGE_SCHEMA, "4"),
            behavior_complete=_binding(BEHAVIOR_EVIDENCE_SCHEMA, "5"),
            salt_commitment=_binding(
                BEHAVIOR_SALT_COMMITMENT_SCHEMA, "6", salt=True),
            nll_masked_deltas=_binding(MASKED_DELTAS_SCHEMA, "c")),
        generator=dict(source_commit="d" * 40, source_tree_hash="e" * 64,
                       program="prepare_v2b_behavior_masked_outcomes.py"),
        arms=arms)


def _python_compile_only():
    value = _masked()
    value.update(repo="sympy", language="python",
                 corpus_git_sha=
                 "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c")
    for rows in value["arms"].values():
        for index, row in enumerate(rows):
            row["target_key"] = identity_key(
                "python", [f"fixture.module{index:02d}",
                           f"function{index:02d}", index * 10])
            row["outcome_class"] = "compile-only"
    return value


def test_pearson_and_spearman_brown_edge_rules():
    assert pearson_or_zero([1, 1, 1], [0, 1, 0]) == 0.0
    assert pearson_or_zero([0, 1, 2], [2, 1, 0]) == -1.0
    assert spearman_brown_clamped(-1.0) == 0.0
    assert spearman_brown_clamped(0.0) == 0.0
    assert spearman_brown_clamped(1.0) == 1.0
    r = pearson_or_zero([0, 1, 2, 3], [0, 1, 1, 3])
    assert 0 < r < 1 and 0 < spearman_brown_clamped(r) < 1
    assert EDGE_RULES_SHA256 == sha256_json(EDGE_RULES)


def test_governance_is_deterministic_arm_anonymous_and_mean_free():
    masked = _masked()
    first = analyze(masked)
    second = analyze(copy.deepcopy(masked))
    assert first == second
    assert first["schema"] == BEHAVIOR_GOVERNANCE_SCHEMA
    assert first["candidate_n"] == list(CANDIDATE_N)
    assert first["n_resplits"] == N_RESPLITS
    assert first["n_opaque_arms"] == N_ARMS
    assert first["n_targets"] == N_PILOT_TARGETS
    assert first["n_eligible_targets"] == N_PILOT_TARGETS
    assert first["n_excluded_targets"] == 0
    assert first["semantic_f1_verdict"] == "feasible"
    assert first["semantic_f1_chosen_n"] == 16
    assert first["model_binding"] == MODEL_BINDINGS[1]
    assert first["governing_outcome_class"] == CLASSES[0]
    assert first["diagnostic_outcome_classes"] == [CLASSES[1]]
    assert first["governance_contract"] == GOVERNANCE_CONTRACT
    assert first["governance_contract"]["eligibility_rule"] == \
        BEHAVIOR_ELIGIBILITY_RULE
    assert first["governance_contract_sha256"] == \
        GOVERNANCE_CONTRACT_SHA256
    assert first["bindings"]["nll_masked_deltas"]["sha256"] == "c" * 64
    assert validate_governance_bindings(
        first["bindings"], expected_nll_sha256="c" * 64) == \
        first["bindings"]
    dumped = repr(first)
    assert all(name not in dumped for name in ("k1", "k3", "k4", "k5",
                                               "k6"))
    assert "pass_rate" not in dumped and "mean_pass" not in dumped
    for n in CANDIDATE_N:
        row = first["by_n"][str(n)]
        assert set(row["arms"]) == set(ARMS)
        for strata in row["arms"].values():
            assert set(strata) == set(CLASSES)
            assert all(value["n_resplits"] == N_RESPLITS
                       for value in strata.values())


def test_constant_pass_profiles_map_undefined_reliability_to_zero():
    value = analyze(_masked(constant=True))
    assert value["semantic_f1_verdict"] == "infeasible"
    assert value["semantic_f1_chosen_n"] is None
    for row in value["by_n"].values():
        assert row["semantic_f1_gate_minimum_median"] == 0.0
        assert row["semantic_f1_verdict"] == "below-target"


def test_thin_outcome_stratum_is_explicitly_infeasible():
    masked = _masked(n_governing=7)
    value = analyze(masked)
    assert value["semantic_f1_verdict"] == "infeasible"
    for by_n in value["by_n"].values():
        assert by_n["semantic_f1_gate_minimum_median"] is None
        assert by_n["semantic_f1_verdict"] == "infeasible"
        for arm in ARMS:
            assert by_n["arms"][arm][CLASSES[0]]["verdict"] == \
                "insufficient-targets"


def test_thin_diagnostic_class_does_not_kill_semantic_gate():
    value = analyze(_masked(n_governing=18))
    assert value["semantic_f1_verdict"] == "feasible"
    for by_n in value["by_n"].values():
        for arm in ARMS:
            diagnostic = by_n["arms"][arm][CLASSES[1]]
            assert diagnostic["role"] == "descriptive-only"
            assert diagnostic["verdict"] == "insufficient-targets"


def test_arm_independent_exclusions_are_null_and_never_imputed():
    masked = _masked()
    exclusions = (
        (0, "baseline_pass"),
        (10, "reference_body_le_448_tokens"),
        (11, "class_verifier_feasible"),
    )
    for rows in masked["arms"].values():
        for target, field in exclusions:
            rows[target]["eligibility"][field] = False
            rows[target]["passes"] = None
    value = analyze(masked)
    assert value["n_targets"] == N_PILOT_TARGETS
    assert value["n_eligible_targets"] == 17
    assert value["n_excluded_targets"] == 3
    for by_n in value["by_n"].values():
        for arm in ARMS:
            assert by_n["arms"][arm][CLASSES[0]]["n_targets"] == 9
            assert by_n["arms"][arm][CLASSES[1]]["n_targets"] == 8


def test_class_present_only_in_excluded_rows_stays_visible_but_infeasible():
    masked = _masked(n_governing=1)
    for rows in masked["arms"].values():
        rows[0]["eligibility"]["baseline_pass"] = False
        rows[0]["passes"] = None
    value = analyze(masked)
    assert CLASSES[0] in value["outcome_classes"]
    assert value["semantic_f1_verdict"] == "infeasible"
    for by_n in value["by_n"].values():
        assert by_n["semantic_f1_gate_minimum_median"] is None
        for arm in ARMS:
            cell = by_n["arms"][arm][CLASSES[0]]
            assert cell["n_targets"] == 0
            assert cell["verdict"] == "insufficient-targets"


def test_missing_python_semantic_class_is_f1_infeasible():
    value = analyze(_python_compile_only())
    assert value["governing_outcome_class"] == "python-semantic-covered"
    assert value["diagnostic_outcome_classes"] == ["compile-only"]
    assert value["semantic_f1_verdict"] == "infeasible"
    assert value["semantic_f1_chosen_n"] is None
    assert all(row["semantic_f1_gate_minimum_median"] is None
               for row in value["by_n"].values())


def test_cross_arm_target_or_class_drift_fails_closed():
    for mutate in (
            lambda value: value["arms"][ARMS[0]].pop(),
            lambda value: value["arms"][ARMS[0]][0].update(
                outcome_class="foreign"),
            lambda value: value["arms"][ARMS[0]][0]["passes"].pop(),
            lambda value: value["arms"][ARMS[0]][0]["passes"].__setitem__(
                0, 2),
            lambda value: value["arms"][ARMS[0]][0]["passes"].__setitem__(
                0, 1.0),
            lambda value: value["arms"][ARMS[0]][0]["eligibility"].update(
                baseline_pass=False),
            lambda value: value["arms"][ARMS[0]][0]["eligibility"].update(
                baseline_pass=1),
            lambda value: value.update(n_targets=True),
            lambda value: value["n_rows_by_arm"].__setitem__(
                ARMS[0], True)):
        value = _masked()
        mutate(value)
        try:
            analyze(value)
            assert False, "malformed masked behavioral arm accepted"
        except V2BError:
            pass

    for mutate in (
            lambda row: row.update(passes=None),
            lambda row: row["eligibility"].update(
                baseline_pass=False)):
        value = _masked()
        mutate(value["arms"][ARMS[0]][0])
        try:
            analyze(value)
            assert False, "eligibility/outcome inconsistency accepted"
        except V2BError:
            pass

    value = _masked()
    for rows in value["arms"].values():
        rows[0]["eligibility"]["baseline_pass"] = False
        # Exclusions must be null; 32 zeroes would silently impute failure.
    try:
        analyze(value)
        assert False, "excluded rows with synthetic failures accepted"
    except V2BError:
        pass


def test_named_or_duplicate_arm_labels_are_rejected():
    value = _masked()
    value["arms"]["k4"] = value["arms"].pop(ARMS[0])
    value["n_rows_by_arm"]["k4"] = value["n_rows_by_arm"].pop(ARMS[0])
    try:
        analyze(value)
        assert False, "named behavioral arm reached governance"
    except V2BError as err:
        assert "opaque" in str(err)


def test_free_form_leak_channels_and_schema_extras_are_rejected():
    mutations = (
        lambda value: value.update(k4_pass_rate=.9375),
        lambda value: value.update(model="k4-best-tier"),
        lambda value: value["bindings"]["behavior_complete"].update(
            named_arm="k4"),
        lambda value: value["arms"][ARMS[0]][0].update(
            pass_rate=.9375),
        lambda value: value["arms"][ARMS[0]][0].update(
            outcome_class="k4-pass-rate=0.9"),
        lambda value: value.update(model_slot="model-k4"),
        lambda value: value["model_binding"].update(arm="k4"),
        lambda value: value["arms"][ARMS[0]][0].update(
            target_key="target-k4"),
    )
    for mutate in mutations:
        value = _masked()
        mutate(value)
        try:
            analyze(value)
            assert False, "free-form behavioral leakage channel accepted"
        except V2BError:
            pass


def test_binding_projection_is_exact_and_mean_free():
    result = analyze(_masked())
    assert set(result["bindings"]) == {
        "behavior_plan", "sample", "assembly", "baseline_coverage",
        "behavior_complete", "salt_commitment", "nll_masked_deltas",
        "masked_outcomes"}
    assert result["bindings"]["masked_outcomes"] == dict(
        schema=BEHAVIOR_MASKED_SCHEMA,
        canonical_sha256=sha256_json(_masked()))
    assert "pass" not in repr(result["bindings"])
    for mutate in (
            lambda value: value.pop("behavior_complete"),
            lambda value: value["nll_masked_deltas"].update(
                sha256="f" * 64),
            lambda value: value["masked_outcomes"].update(path="leak")):
        bindings = copy.deepcopy(result["bindings"])
        mutate(bindings)
        try:
            validate_governance_bindings(
                bindings, expected_nll_sha256="c" * 64)
            assert False, "drifted governance binding accepted"
        except V2BError:
            pass


def test_small_stratum_floor_is_prospectively_conservative():
    assert MIN_GOVERNING_TARGETS == 8
    assert MIN_DIAGNOSTIC_TARGETS == 3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B BEHAVIORAL GOVERNANCE TESTS PASS")
