#!/usr/bin/env python3
"""Exact boundaries and disclosure surface of the k4 tier helper."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_behavior_tier import TIER_RULE, TIER_RULE_SHA256, decide_tier
from v2b_behavioral_governance import (
    BEHAVIOR_ELIGIBILITY_FIELDS, MODEL_BINDINGS, N_DRAWS)
from v2b_common import BEHAVIOR_TIER_SCHEMA, V2BError, identity_key, sha256_json


REPO = "mathlib4"
LANGUAGE = "lean"
REVISION = "87adeaebd370a3b6a41ac4f044fddd4bf81803ad"
TARGET_KEYS = [
    identity_key("lean", [f"Fixture.Module{index:02d}",
                          f"decl{index:02d}"])
    for index in range(20)
]


def _rows(n_semantic=10, semantic_successes=160, excluded_targets=()):
    rows = []
    remaining = semantic_successes
    for index in range(20):
        semantic = index < n_semantic
        eligible = index not in excluded_targets
        n_pass = min(N_DRAWS, remaining) if semantic and eligible else 0
        if semantic and eligible:
            remaining -= n_pass
        rows.append(dict(
            target_key=TARGET_KEYS[index],
            outcome_class=("lean-theorem-proof" if semantic else
                           "lean-def-typecheck"),
            eligibility={
                field: (eligible if field == "baseline_pass" else True)
                for field in BEHAVIOR_ELIGIBILITY_FIELDS
            },
            passes=([1] * n_pass + [0] * (N_DRAWS - n_pass)
                    if eligible else None)))
    assert remaining == 0
    return rows


def _decide(rows, model_index=1):
    return decide_tier(REPO, LANGUAGE, REVISION,
                       dict(MODEL_BINDINGS[model_index]),
                       list(TARGET_KEYS), rows)


def test_exact_boundaries_stay_and_contract_is_hash_bound():
    # Ten semantic targets -> 320 trials, so 16 and 304 are exactly 5/95%.
    for successes, expected_rate in ((16, .05), (304, .95)):
        value = _decide(_rows(semantic_successes=successes))
        assert value["schema"] == BEHAVIOR_TIER_SCHEMA
        assert value["verdict"] == "selected"
        assert value["direction"] == "stay"
        assert value["aggregate_pass_rate"] == expected_rate
        assert value["final_model_binding"] == MODEL_BINDINGS[1]
        assert value["tier_rule"] == TIER_RULE
        assert value["tier_rule_sha256"] == TIER_RULE_SHA256 == \
            sha256_json(TIER_RULE)


def test_strict_floor_and_ceiling_move_exactly_one_tier():
    low = _decide(_rows(semantic_successes=15))
    high = _decide(_rows(semantic_successes=305))
    assert low["direction"] == "move-up-one"
    assert low["final_model_binding"] == MODEL_BINDINGS[2]
    assert high["direction"] == "move-down-one"
    assert high["final_model_binding"] == MODEL_BINDINGS[0]
    # The output is exactly an aggregate: no target row or other named arm.
    for value in (low, high):
        dumped = repr(value)
        assert "target_key" not in dumped
        assert all(arm not in dumped for arm in ("k1", "k3", "k5", "k6"))
        assert value["named_aggregate_arm"] == "k4"


def test_missing_adjacent_and_thin_semantic_slots_are_infeasible():
    low_at_top = _decide(_rows(semantic_successes=0), model_index=2)
    high_at_bottom = _decide(_rows(semantic_successes=320), model_index=0)
    assert low_at_top["verdict"] == "infeasible-missing-adjacent-tier"
    assert high_at_bottom["verdict"] == "infeasible-missing-adjacent-tier"
    thin = _decide(_rows(n_semantic=7, semantic_successes=100))
    assert thin["verdict"] == "infeasible-insufficient-governing-targets"
    assert thin["aggregate_pass_rate"] is None
    assert thin["semantic_passes"] is None


def test_exclusions_preserve_pilot_set_but_never_enter_the_rate():
    value = _decide(_rows(
        n_semantic=10, semantic_successes=144, excluded_targets={0}))
    assert value["n_pilot_targets"] == 20
    assert value["n_eligible_targets"] == 19
    assert value["n_excluded_targets"] == 1
    assert value["n_governing_targets"] == 9
    assert value["semantic_trials"] == 9 * N_DRAWS
    assert value["semantic_passes"] == 144
    assert value["aggregate_pass_rate"] == .5


def test_model_ladder_order_is_explicitly_ascending_capability():
    assert [binding["name"] for binding in MODEL_BINDINGS] == [
        "Qwen/Qwen2.5-Coder-0.5B",
        "Qwen/Qwen2.5-Coder-1.5B",
        "Qwen/Qwen2.5-Coder-3B",
    ]


def test_projection_drift_and_leak_fields_fail_closed():
    mutations = (
        lambda rows: rows[0].update(named_arm="k4"),
        lambda rows: rows[0].update(outcome_class="compile-only"),
        lambda rows: rows[0]["passes"].__setitem__(0, 1.0),
        lambda rows: rows.reverse(),
        lambda rows: rows.pop(),
    )
    for mutate in mutations:
        rows = copy.deepcopy(_rows())
        mutate(rows)
        try:
            _decide(rows)
            assert False, "drifted k4 projection accepted"
        except V2BError:
            pass
    try:
        decide_tier(REPO, "python", REVISION, dict(MODEL_BINDINGS[1]),
                    list(TARGET_KEYS), _rows())
        assert False, "repo/language drift accepted"
    except V2BError:
        pass

    alien = list(TARGET_KEYS)
    alien[0] = identity_key("lean", ["Alien.Module", "foreign"])
    alien.sort()
    try:
        decide_tier(REPO, LANGUAGE, REVISION, dict(MODEL_BINDINGS[1]),
                    alien, _rows())
        assert False, "alien canonical pilot identities accepted"
    except V2BError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B BEHAVIOR TIER TESTS PASS")
