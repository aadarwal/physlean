#!/usr/bin/env python3
"""Pure V2-b k4 floor/ceiling tier decision.

This is the sole behavioral pilot aggregate permitted before formal joint
unblinding.  It consumes a normalized k4-only binary projection supplied by a
future file-based verifier producer and emits no target rows or other-arm
quantity.  Threshold comparisons use exact integer arithmetic: <0.05 moves
one tier up, >0.95 moves one tier down, and equality stays.

The helper does not itself prove that rows came from k4; the still-missing
production wrapper must reconstruct this projection from the hash-bound
verified evidence.  No production entry point exists here.
"""
import json
import math

from v2b_behavioral_governance import (
    BEHAVIOR_ELIGIBILITY_RULE, GOVERNING_CLASS_BY_LANGUAGE,
    MIN_GOVERNING_TARGETS, MODEL_BINDINGS, N_DRAWS, N_PILOT_TARGETS,
    OUTCOME_CLASSES_BY_LANGUAGE, REPO_IDENTITIES, behavior_is_eligible,
    validate_behavior_eligibility)
from v2b_common import (BEHAVIOR_TIER_SCHEMA, V2BError, identity_key,
                        sha256_json, validate_identity)


TIER_RULE = dict(
    named_aggregate_arm="k4",
    governing_class_by_language=GOVERNING_CLASS_BY_LANGUAGE,
    eligibility_rule=BEHAVIOR_ELIGIBILITY_RULE,
    lower_floor="strictly less than 0.05 moves one capability tier up",
    upper_ceiling="strictly greater than 0.95 moves one capability tier down",
    exact_boundary="stay",
    maximum_move="one adjacent tier from the supplied slot",
    missing_adjacent="semantic F1 slot infeasible",
    minimum_governing_targets=MIN_GOVERNING_TARGETS,
    comparisons="integer cross-products; no floating threshold decision")
TIER_RULE_SHA256 = sha256_json(TIER_RULE)


def _validate_expected_target_keys(language, expected_target_keys):
    if not isinstance(expected_target_keys, list) \
            or len(expected_target_keys) != N_PILOT_TARGETS:
        raise V2BError("expected k4 pilot target set needs exactly "
                       f"{N_PILOT_TARGETS} keys")
    canonical = []
    for key in expected_target_keys:
        try:
            identity = json.loads(key)
            normalized = identity_key(
                language, validate_identity(language, identity))
        except (TypeError, ValueError, V2BError) as err:
            raise V2BError(f"malformed expected k4 target key {key!r}") \
                from err
        if key != normalized:
            raise V2BError(f"noncanonical expected k4 target key {key!r}")
        canonical.append(key)
    if canonical != sorted(set(canonical)):
        raise V2BError("expected k4 pilot target keys are not unique/sorted")
    return canonical


def _validate_rows(language, expected_target_keys, rows):
    expected = _validate_expected_target_keys(language,
                                               expected_target_keys)
    if not isinstance(rows, list) or len(rows) != N_PILOT_TARGETS:
        raise V2BError(f"k4 tier projection needs exactly "
                       f"{N_PILOT_TARGETS} pilot targets")
    normalized = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) \
                or set(row) != {"target_key", "outcome_class",
                               "eligibility", "passes"}:
            raise V2BError(f"malformed k4 tier row[{index}]")
        key = row.get("target_key")
        try:
            identity = json.loads(key)
            canonical = identity_key(
                language, validate_identity(language, identity))
        except (TypeError, ValueError, V2BError) as err:
            raise V2BError(f"malformed k4 target key {key!r}") from err
        outcome_class = row.get("outcome_class")
        eligibility = validate_behavior_eligibility(
            row.get("eligibility"))
        passes = row.get("passes")
        if key != canonical or key in seen \
                or outcome_class not in OUTCOME_CLASSES_BY_LANGUAGE[language]:
            raise V2BError(f"malformed/duplicate k4 tier row[{index}]")
        eligible = behavior_is_eligible(eligibility)
        if eligible and (
                not isinstance(passes, list) or len(passes) != N_DRAWS
                or any(type(value) is not int or value not in (0, 1)
                       for value in passes)):
            raise V2BError(f"malformed eligible k4 tier row[{index}]")
        if not eligible and passes is not None:
            raise V2BError(f"excluded k4 tier row has outcomes[{index}]")
        seen.add(key)
        normalized.append(dict(target_key=key, outcome_class=outcome_class,
                               eligibility=eligibility,
                               passes=list(passes) if eligible else None))
    if [row["target_key"] for row in normalized] != sorted(seen):
        raise V2BError("k4 tier projection is not target-sorted")
    if [row["target_key"] for row in normalized] != expected:
        raise V2BError("k4 tier projection is not the committed pilot set")
    return normalized


def decide_tier(repo, language, corpus_git_sha, model_binding,
                expected_target_keys, k4_rows):
    """Construct the exact non-target-level k4 aggregate decision."""
    if REPO_IDENTITIES.get(repo) != (language, corpus_git_sha):
        raise V2BError("k4 tier repo/language/revision is not frozen")
    if not isinstance(model_binding, dict) \
            or set(model_binding) != {"name", "revision"} \
            or model_binding not in MODEL_BINDINGS:
        raise V2BError("k4 tier model binding is not in the frozen ladder")
    rows = _validate_rows(language, expected_target_keys, k4_rows)
    governing = GOVERNING_CLASS_BY_LANGUAGE[language]
    eligible = [row for row in rows
                if behavior_is_eligible(row["eligibility"])]
    semantic = [row for row in eligible
                if row["outcome_class"] == governing]
    base = dict(
        schema=BEHAVIOR_TIER_SCHEMA,
        state="sole-permitted-pre-unblinding-behavioral-aggregate",
        repo=repo, language=language, corpus_git_sha=corpus_git_sha,
        supplied_model_binding=dict(model_binding),
        named_aggregate_arm="k4", governing_outcome_class=governing,
        n_pilot_targets=len(rows), n_governing_targets=len(semantic),
        n_eligible_targets=len(eligible),
        n_excluded_targets=len(rows) - len(eligible),
        n_draws_per_target=N_DRAWS,
        tier_rule=TIER_RULE, tier_rule_sha256=TIER_RULE_SHA256,
        pilot_target_set_sha256=sha256_json(expected_target_keys),
        k4_projection_sha256=sha256_json(rows))
    if len(semantic) < MIN_GOVERNING_TARGETS:
        return dict(
            base, semantic_trials=None, semantic_passes=None,
            aggregate_pass_rate=None, direction=None,
            final_model_binding=None,
            verdict="infeasible-insufficient-governing-targets")
    trials = len(semantic) * N_DRAWS
    successes = sum(sum(row["passes"]) for row in semantic)
    if not 0 <= successes <= trials:
        raise AssertionError("binary k4 successes do not conserve trials")
    if successes * 100 < trials * 5:
        step, direction = 1, "move-up-one"
    elif successes * 100 > trials * 95:
        step, direction = -1, "move-down-one"
    else:
        step, direction = 0, "stay"
    index = MODEL_BINDINGS.index(model_binding)
    destination = index + step
    if not 0 <= destination < len(MODEL_BINDINGS):
        final = None
        verdict = "infeasible-missing-adjacent-tier"
    else:
        final = dict(MODEL_BINDINGS[destination])
        verdict = "selected"
    rate = successes / trials
    if not math.isfinite(rate):
        raise AssertionError("k4 pass-rate is non-finite")
    return dict(
        base, semantic_trials=trials, semantic_passes=successes,
        aggregate_pass_rate=rate, direction=direction,
        final_model_binding=final, verdict=verdict)
