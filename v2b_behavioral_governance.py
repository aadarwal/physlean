#!/usr/bin/env python3
"""Arm-anonymous behavioral completion-n governance for V2-b §14.22.

Input arms are opaque and contain exactly 32 binary pilot outcomes per
target.  For candidate n in {8,16,32}, 200 deterministic random half-splits
estimate target-level pass-probability reliability separately within every
opaque arm and outcome stratum.  Pearson is Spearman-Brown projected from
n/2 to n; undefined/nonpositive correlations map to zero and the correction
is clamped to [0,1].  Semantic F1 is governed only by the language's frozen
semantic class, with descriptive reliability for weaker classes.  No arm pass
rate or contrast direction is emitted.

This module is pure nuisance governance.  Generation, parsing, verification,
masking, and the narrow k4 tier aggregate are separate required producers.
"""
import hashlib
import json
import math
import re

from v2b_common import (ASSEMBLY_SCHEMA, BEHAVIOR_SALT_COMMITMENT_SCHEMA,
                        BOUND_SAMPLE_SCHEMA, MASKED_DELTAS_SCHEMA, V2BError,
                        canonical_json_bytes, identity_key, sha256_json,
                        validate_identity)


BEHAVIOR_MASKED_SCHEMA = "v2b_behavior_masked_outcomes_v1"
BEHAVIOR_GOVERNANCE_SCHEMA = "v2b_behavioral_governance_v1"
BEHAVIOR_EVIDENCE_SCHEMA = "v2b_behavior_verified_complete_v1"
BEHAVIOR_PLAN_SCHEMA = "v2b_behavior_plan_v1"
BEHAVIOR_BASELINE_COVERAGE_SCHEMA = "v2b_behavior_baseline_coverage_v1"
BEHAVIOR_MASKED_STATE = "arm-anonymous-behavioral-pilot"
OPAQUE_ARM_RE = re.compile(r"^arm-[0-9a-f]{16}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
N_ARMS = 5
N_DRAWS = 32
N_PILOT_TARGETS = 20
CANDIDATE_N = (8, 16, 32)
N_RESPLITS = 200
RESPLIT_SEED = 20260808
RELIABILITY_TARGET = 0.8
MIN_GOVERNING_TARGETS = 8
MIN_DIAGNOSTIC_TARGETS = 3
REPO_IDENTITIES = {
    "mathlib4": ("lean", "87adeaebd370a3b6a41ac4f044fddd4bf81803ad"),
    "batteries": ("lean", "76e1c118b0700b4ceafe99532e887d6431625e1a"),
    "physlib": ("lean", "e882411d1b6bcbdfdd336d4c509c6cc72e96842d"),
    "sympy": ("python", "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"),
    "astropy": ("python", "440fe546589c4e496235d712bc29783ecf5a5fec"),
}
OUTCOME_CLASSES_BY_LANGUAGE = {
    "lean": frozenset(("lean-theorem-proof", "lean-def-typecheck")),
    "python": frozenset(("python-semantic-covered", "compile-only")),
}
GOVERNING_CLASS_BY_LANGUAGE = {
    "lean": "lean-theorem-proof",
    "python": "python-semantic-covered",
}
BEHAVIOR_ELIGIBILITY_FIELDS = (
    "reference_body_le_448_tokens",
    "baseline_pass",
    "class_verifier_feasible",
)
BEHAVIOR_ELIGIBILITY_RULE = dict(
    pilot_rows="all 20 committed pilot identities remain present",
    fields=list(BEHAVIOR_ELIGIBILITY_FIELDS),
    eligible="all three arm-independent fields are true",
    eligible_passes="exactly 32 integer binary verifier outcomes",
    excluded_passes="JSON null; excluded rows are never imputed or analyzed",
    cross_arm="target, outcome class, and eligibility fields are identical",
)
MODEL_BINDINGS = (
    {"name": "Qwen/Qwen2.5-Coder-0.5B",
     "revision": "8123ea2e9354afb7ffcc6c8641d1b2f5ecf18301"},
    {"name": "Qwen/Qwen2.5-Coder-1.5B",
     "revision": "df3ce67c0e24480f20468b6ef2894622d69eb73b"},
    {"name": "Qwen/Qwen2.5-Coder-3B",
     "revision": "09d9bc5d376b0cfa0100a0694ea7de7232525803"},
)
MASKED_BINDING_SCHEMAS = {
    "behavior_plan": BEHAVIOR_PLAN_SCHEMA,
    "sample": BOUND_SAMPLE_SCHEMA,
    "assembly": ASSEMBLY_SCHEMA,
    "baseline_coverage": BEHAVIOR_BASELINE_COVERAGE_SCHEMA,
    "behavior_complete": BEHAVIOR_EVIDENCE_SCHEMA,
    "salt_commitment": BEHAVIOR_SALT_COMMITMENT_SCHEMA,
    "nll_masked_deltas": MASKED_DELTAS_SCHEMA,
}
MASKED_TOP_LEVEL_KEYS = frozenset((
    "schema", "state", "repo", "language", "corpus_git_sha",
    "model_binding", "n_targets", "n_draws_per_target_arm",
    "n_rows_by_arm", "bindings", "generator", "arms",
))
RESPLIT_RULE = (
    "ascending SHA256(canonical_json(['v2brel:v1',20260808,opaque_arm,"
    "outcome_class,n,resplit,target_key,draw_index])) per target; first n/2 "
    "versus next n/2; unused draws excluded")
EDGE_RULES = dict(
    pearson_undefined="zero",
    pearson_nonpositive="zero",
    spearman_brown="2*r/(1+r), clamped to [0,1]",
    minimum_governing_targets_per_arm_stratum=MIN_GOVERNING_TARGETS,
    minimum_diagnostic_targets_per_arm_stratum=MIN_DIAGNOSTIC_TARGETS,
    insufficient_governing_targets="repo-model semantic F1 infeasible",
    median="average of middle pair for 200 resplits")
EDGE_RULES_SHA256 = sha256_json(EDGE_RULES)
GOVERNANCE_CONTRACT = dict(
    schema="v2b_behavioral_reliability_contract_v1",
    candidate_n=list(CANDIDATE_N), n_arms=N_ARMS,
    n_pilot_targets=N_PILOT_TARGETS, n_draws=N_DRAWS,
    n_resplits=N_RESPLITS, resplit_seed=RESPLIT_SEED,
    resplit_hash_domain="v2brel:v1",
    resplit_hash_fields=["domain", "seed", "opaque_arm",
                         "outcome_class", "n", "resplit", "target_key",
                         "draw_index"],
    resplit_rule=RESPLIT_RULE, reliability_target=RELIABILITY_TARGET,
    governing_class_by_language=GOVERNING_CLASS_BY_LANGUAGE,
    allowed_classes_by_language={
        language: sorted(classes)
        for language, classes in OUTCOME_CLASSES_BY_LANGUAGE.items()},
    eligibility_rule=BEHAVIOR_ELIGIBILITY_RULE,
    gating=("first candidate whose minimum median corrected reliability "
            "across five opaque arms in the language's governing semantic "
            "class is >=0.8; diagnostic classes never set n"),
    edge_rules=EDGE_RULES)
GOVERNANCE_CONTRACT_SHA256 = sha256_json(GOVERNANCE_CONTRACT)


def _safe_binding(value, schema, label):
    """A public blind artifact may carry hashes, never free-form metadata.

    In particular, paths, arm/model names, and analyst-authored notes are
    excluded from this forward contract so a binding cannot become a covert
    pass-rate or contrast-direction channel.
    """
    expected_keys = ({"schema", "sha256", "salt_sha256"}
                     if label == "salt_commitment"
                     else {"schema", "sha256"})
    if not isinstance(value, dict) or set(value) != expected_keys \
            or value.get("schema") != schema \
            or not isinstance(value.get("sha256"), str) \
            or not HEX64_RE.fullmatch(value["sha256"]) \
            or label == "salt_commitment" and (
                not isinstance(value.get("salt_sha256"), str)
                or not HEX64_RE.fullmatch(value["salt_sha256"])):
        raise V2BError(f"malformed behavioral binding: {label}")
    output = {"schema": schema, "sha256": value["sha256"]}
    if label == "salt_commitment":
        output["salt_sha256"] = value["salt_sha256"]
    return output


def _masked_bindings(value):
    if not isinstance(value, dict) \
            or set(value) != set(MASKED_BINDING_SCHEMAS):
        raise V2BError("behavioral masked binding names are not frozen")
    return {name: _safe_binding(value[name], schema, name)
            for name, schema in MASKED_BINDING_SCHEMAS.items()}


def validate_governance_bindings(value, expected_nll_sha256=None):
    """Validate the exact public/transitive binding projection.

    The future production producer must additionally bind the exact committed
    masked-outcomes FILE and recompute the governance object.  This pure
    estimator freezes the non-leaking object-level contract in advance.
    """
    expected = set(MASKED_BINDING_SCHEMAS) | {"masked_outcomes"}
    if not isinstance(value, dict) or set(value) != expected:
        raise V2BError("behavioral governance binding names are not frozen")
    safe = _masked_bindings({name: value[name]
                             for name in MASKED_BINDING_SCHEMAS})
    masked = value["masked_outcomes"]
    if not isinstance(masked, dict) \
            or set(masked) != {"schema", "canonical_sha256"} \
            or masked.get("schema") != BEHAVIOR_MASKED_SCHEMA \
            or not isinstance(masked.get("canonical_sha256"), str) \
            or not HEX64_RE.fullmatch(masked["canonical_sha256"]):
        raise V2BError("malformed behavioral masked-outcomes binding")
    safe["masked_outcomes"] = dict(
        schema=BEHAVIOR_MASKED_SCHEMA,
        canonical_sha256=masked["canonical_sha256"])
    if expected_nll_sha256 is not None \
            and safe["nll_masked_deltas"]["sha256"] != expected_nll_sha256:
        raise V2BError("behavioral governance does not bind this NLL "
                       "masked chain")
    return safe


def _rank(arm, outcome_class, n, resplit, target_key, draw_index):
    payload = ["v2brel:v1", RESPLIT_SEED, arm, outcome_class, n,
               resplit, target_key, draw_index]
    return hashlib.sha256(canonical_json_bytes(payload)).digest()


def validate_behavior_eligibility(value):
    """Return the exact arm-independent eligibility projection.

    All 20 pilot identities stay in the masked table.  An excluded identity
    carries null outcomes rather than 32 synthetic failures, so exclusions
    cannot lower pass rates or manufacture reliability.
    """
    if not isinstance(value, dict) \
            or set(value) != set(BEHAVIOR_ELIGIBILITY_FIELDS) \
            or any(type(value[field]) is not bool
                   for field in BEHAVIOR_ELIGIBILITY_FIELDS):
        raise V2BError("malformed behavioral eligibility projection")
    return {field: value[field] for field in BEHAVIOR_ELIGIBILITY_FIELDS}


def behavior_is_eligible(value):
    return all(value[field] for field in BEHAVIOR_ELIGIBILITY_FIELDS)


def pearson_or_zero(left, right):
    if not isinstance(left, list) or not isinstance(right, list) \
            or len(left) != len(right) or len(left) < 2:
        raise V2BError("Pearson inputs must be equal lists of length >=2")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) for value in (*left, *right)):
        raise V2BError("Pearson inputs are malformed")
    n = len(left)
    mean_left = math.fsum(float(value) for value in left) / n
    mean_right = math.fsum(float(value) for value in right) / n
    dl = [float(value) - mean_left for value in left]
    dr = [float(value) - mean_right for value in right]
    ss_left = math.fsum(value * value for value in dl)
    ss_right = math.fsum(value * value for value in dr)
    if ss_left == 0.0 or ss_right == 0.0:
        return 0.0
    raw = math.fsum(a * b for a, b in zip(dl, dr)) / math.sqrt(
        ss_left * ss_right)
    # Numerical roundoff can leave |r| a few ulps over one.
    return min(1.0, max(-1.0, raw))


def spearman_brown_clamped(correlation):
    if not isinstance(correlation, (int, float)) \
            or isinstance(correlation, bool) \
            or not math.isfinite(correlation):
        raise V2BError("Spearman-Brown correlation is malformed")
    if correlation <= 0:
        return 0.0
    corrected = 2.0 * correlation / (1.0 + correlation)
    return min(1.0, max(0.0, corrected))


def _median(values):
    if not isinstance(values, list) or not values:
        raise V2BError("median needs non-empty values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _validate_masked(masked):
    if not isinstance(masked, dict) \
            or masked.get("schema") != BEHAVIOR_MASKED_SCHEMA \
            or set(masked) != MASKED_TOP_LEVEL_KEYS:
        raise V2BError("behavioral masked artifact schema mismatch")
    repo = masked.get("repo")
    language = masked.get("language")
    corpus_git_sha = masked.get("corpus_git_sha")
    if REPO_IDENTITIES.get(repo) != (language, corpus_git_sha):
        raise V2BError("behavioral masked repo/language/revision is not "
                       "frozen")
    model_binding = masked.get("model_binding")
    if masked.get("state") != BEHAVIOR_MASKED_STATE \
            or not isinstance(model_binding, dict) \
            or set(model_binding) != {"name", "revision"} \
            or model_binding not in MODEL_BINDINGS:
        raise V2BError("behavioral masked state/model binding is malformed")
    bindings = _masked_bindings(masked.get("bindings"))
    generator = masked.get("generator")
    if not isinstance(generator, dict) \
            or set(generator) != {"source_commit", "source_tree_hash",
                                  "program"} \
            or not isinstance(generator.get("source_commit"), str) \
            or not re.fullmatch(r"[0-9a-f]{40}",
                                generator["source_commit"]) \
            or not isinstance(generator.get("source_tree_hash"), str) \
            or not HEX64_RE.fullmatch(generator["source_tree_hash"]) \
            or generator.get("program") != \
            "prepare_v2b_behavior_masked_outcomes.py":
        raise V2BError("behavioral masked generator is malformed")
    n_targets = masked.get("n_targets")
    n_draws = masked.get("n_draws_per_target_arm")
    if type(n_targets) is not int or n_targets != N_PILOT_TARGETS \
            or type(n_draws) is not int or n_draws != N_DRAWS:
        raise V2BError("behavioral masked counts are malformed")
    arms = masked.get("arms")
    if not isinstance(arms, dict) or len(arms) != N_ARMS \
            or any(not isinstance(name, str) or not OPAQUE_ARM_RE.match(name)
                   for name in arms):
        raise V2BError(f"behavioral masked artifact needs exactly {N_ARMS} "
                       "opaque arms")
    canonical_targets = None
    normalized = {}
    for arm, rows in sorted(arms.items()):
        if not isinstance(rows, list) or not rows:
            raise V2BError(f"opaque behavior arm is empty: {arm}")
        table = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict) \
                    or set(row) != {"target_key", "outcome_class",
                                   "eligibility", "passes"}:
                raise V2BError(f"malformed behavior row {arm}[{index}]")
            key = row.get("target_key")
            outcome_class = row.get("outcome_class")
            eligibility = validate_behavior_eligibility(
                row.get("eligibility"))
            passes = row.get("passes")
            try:
                identity = json.loads(key)
                canonical_key = identity_key(
                    language, validate_identity(language, identity))
            except (TypeError, ValueError, V2BError) as err:
                raise V2BError(f"malformed behavior target key {key!r}") \
                    from err
            if not isinstance(key, str) or key != canonical_key \
                    or key in table \
                    or not isinstance(outcome_class, str) \
                    or outcome_class not in \
                    OUTCOME_CLASSES_BY_LANGUAGE[language]:
                raise V2BError(f"malformed/duplicate behavior row "
                               f"{arm}[{index}]")
            eligible = behavior_is_eligible(eligibility)
            if eligible and (
                    not isinstance(passes, list)
                    or len(passes) != N_DRAWS
                    or any(type(value) is not int or value not in (0, 1)
                           for value in passes)):
                raise V2BError(f"malformed eligible behavior outcomes "
                               f"{arm}[{index}]")
            if not eligible and passes is not None:
                raise V2BError(f"excluded behavior row has outcomes "
                               f"{arm}[{index}]")
            flags = tuple(eligibility[field]
                          for field in BEHAVIOR_ELIGIBILITY_FIELDS)
            table[key] = (outcome_class, flags,
                          tuple(passes) if eligible else None)
        if [row["target_key"] for row in rows] != sorted(table):
            raise V2BError(f"behavior rows are not target-sorted: {arm}")
        target_classes = {key: value[:2] for key, value in table.items()}
        if canonical_targets is None:
            canonical_targets = target_classes
        elif target_classes != canonical_targets:
            raise V2BError("opaque arms do not share the exact target/"
                           "outcome-class/eligibility table")
        normalized[arm] = table
    if n_targets != len(canonical_targets):
        raise V2BError("behavioral masked counts do not match arm rows")
    declared = masked.get("n_rows_by_arm")
    if not isinstance(declared, dict) \
            or set(declared) != set(arms) \
            or any(type(value) is not int or value <= 0
                   for value in declared.values()) \
            or declared != {arm: len(rows) for arm, rows in arms.items()}:
        raise V2BError("behavioral masked n_rows_by_arm drift")
    return normalized, canonical_targets, bindings, model_binding


def _arm_stratum_reliability(arm, outcome_class, rows, n, minimum_targets,
                             role):
    target_keys = sorted(rows)
    if len(target_keys) < minimum_targets:
        return dict(
            n_targets=len(target_keys), n=n, n_resplits=N_RESPLITS,
            role=role, minimum_targets_required=minimum_targets,
            median_corrected_reliability=None,
            min_corrected_reliability=None,
            max_corrected_reliability=None,
            verdict="insufficient-targets")
    corrected = []
    half = n // 2
    for resplit in range(N_RESPLITS):
        left, right = [], []
        for key in target_keys:
            passes = rows[key]
            order = sorted(range(N_DRAWS), key=lambda draw: (
                _rank(arm, outcome_class, n, resplit, key, draw), draw))
            selected = order[:n]
            left.append(math.fsum(passes[draw]
                                  for draw in selected[:half]) / half)
            right.append(math.fsum(passes[draw]
                                   for draw in selected[half:]) / half)
        corrected.append(spearman_brown_clamped(
            pearson_or_zero(left, right)))
    median = _median(corrected)
    return dict(
        n_targets=len(target_keys), n=n, n_resplits=N_RESPLITS,
        role=role, minimum_targets_required=minimum_targets,
        median_corrected_reliability=median,
        min_corrected_reliability=min(corrected),
        max_corrected_reliability=max(corrected),
        verdict=("meets" if median >= RELIABILITY_TARGET else
                 "below-target"))


def analyze(masked):
    """Pure arm-anonymous n governance; emits no pass-rate means."""
    arms, target_metadata, masked_bindings, model_binding = \
        _validate_masked(masked)
    strata = sorted(set(value[0] for value in target_metadata.values()))
    language = masked["language"]
    governing_class = GOVERNING_CLASS_BY_LANGUAGE[language]
    diagnostic_classes = [value for value in strata
                          if value != governing_class]
    by_n = {}
    chosen_n = None
    for n in CANDIDATE_N:
        cells = {}
        gate_values = []
        governing_feasible = governing_class in strata
        for arm, table in sorted(arms.items()):
            cells[arm] = {}
            for outcome_class in strata:
                is_governing = outcome_class == governing_class
                minimum_targets = (MIN_GOVERNING_TARGETS if is_governing
                                   else MIN_DIAGNOSTIC_TARGETS)
                rows = {
                    key: passes
                    for key, (row_class, eligibility, passes)
                    in table.items()
                    if row_class == outcome_class and all(eligibility)
                }
                result = _arm_stratum_reliability(
                    arm, outcome_class, rows, n, minimum_targets,
                    "semantic-f1-governing" if is_governing else
                    "descriptive-only")
                cells[arm][outcome_class] = result
                if is_governing:
                    value = result["median_corrected_reliability"]
                    if value is None:
                        governing_feasible = False
                    else:
                        gate_values.append(value)
        gate = (min(gate_values)
                if governing_feasible and len(gate_values) == N_ARMS
                else None)
        meets = gate is not None and gate >= RELIABILITY_TARGET
        by_n[str(n)] = dict(
            semantic_f1_gate_minimum_median=gate,
            semantic_f1_verdict=(
                "meets" if meets else "infeasible"
                if not governing_feasible else "below-target"),
            arms=cells)
        if chosen_n is None and meets:
            chosen_n = n
    verdict = "feasible" if chosen_n is not None else "infeasible"
    return dict(
        schema=BEHAVIOR_GOVERNANCE_SCHEMA,
        repo=masked["repo"], language=language,
        corpus_git_sha=masked["corpus_git_sha"],
        model_binding=dict(model_binding),
        repo_model_slot_sha256=sha256_json(
            [masked["repo"], model_binding["name"],
             model_binding["revision"]]),
        state="arm-anonymous-reliability-governance",
        candidate_n=list(CANDIDATE_N), n_draws=N_DRAWS,
        n_resplits=N_RESPLITS, resplit_seed=RESPLIT_SEED,
        resplit_rule=RESPLIT_RULE,
        reliability_target=RELIABILITY_TARGET,
        edge_rules=EDGE_RULES, edge_rules_sha256=EDGE_RULES_SHA256,
        governance_contract=GOVERNANCE_CONTRACT,
        governance_contract_sha256=GOVERNANCE_CONTRACT_SHA256,
        n_opaque_arms=len(arms), n_targets=len(target_metadata),
        n_eligible_targets=sum(
            all(eligibility)
            for _, eligibility in target_metadata.values()),
        n_excluded_targets=sum(
            not all(eligibility)
            for _, eligibility in target_metadata.values()),
        outcome_classes=strata,
        governing_outcome_class=governing_class,
        diagnostic_outcome_classes=diagnostic_classes,
        by_n=by_n,
        semantic_f1_verdict=verdict,
        semantic_f1_chosen_n=chosen_n,
        bindings=dict(
            masked_bindings,
            masked_outcomes=dict(
                schema=BEHAVIOR_MASKED_SCHEMA,
                canonical_sha256=sha256_json(masked))))
