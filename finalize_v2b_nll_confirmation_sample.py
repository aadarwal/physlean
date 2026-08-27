#!/usr/bin/env python3
"""Prospective fresh-SymPy confirmation sampler.

This is the only production draw entry point for the confirmation study.  It
does not recompute source eligibility: it consumes the committed, model-free
reduced source gate, independently verifies every key-set relation needed by
sampling, excludes every pilot source module, and delegates the actual draw
to the frozen :func:`v2b_metadata.build_sample_plan` implementation.

The pure ``build_confirmation_sample`` helper is intentionally usable by
synthetic tests.  ``prepare`` adds the write-time input ledger and generator
provenance and is the only function used by the CLI.
"""
import argparse
import copy
import os
import sys

from finalize_v2b_sample import _validate_candidate_table
from prepare_v2b_nll_confirmation_gate import (
    GATE_SCHEMA, key_set, load_reduced_gate, protocol_record,
    validate_reduced_gate,
)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (
    BOUND_SAMPLE_SCHEMA,
    CANDIDATES_SCHEMA,
    SAMPLE_SCHEMA,
    V2BError,
    artifact_binding,
    identity_key,
    sha256_file,
    sha256_json,
    sha256_sorted_json,
    validate_identity,
    write_new_json,
)
from v2b_metadata import build_sample_plan
from v2b_nll_confirmation import (
    PROTOCOL_PATH,
    PROTOCOL_RAW_SHA256,
    PROTOCOL_SEMANTIC_SHA256,
    load_protocol,
)


CONFIRMATION_SAMPLE_SCHEMA = "v2b_nll_e2_confirmation_sample_v1"
SAMPLE_STATE = "drawn-source-gated-module-disjoint-pre-score"
REPO = "sympy"
LANGUAGE = "python"
N_CONFIRMATION = 200
BUDGET_BYTES = 16384
MIN_MODULES = 20
MIN_EFFECTIVE_CLUSTERS = 10
IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_e2_confirmation_implementation_freeze_v1"

ARTIFACT_BINDING_KEYS = frozenset(("path", "schema", "sha256"))
PLAN_KEYS = frozenset((
    "schema", "repo", "language", "n_requested", "n_excluded",
    "excluded_keys_sha256", "n_selected", "quota_table",
    "cell_populations", "cell_fills", "shortfalls", "unsampled_cells",
    "targets",
))
PLAN_TARGET_KEYS = frozenset(("identity", "cell", "priority"))


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _path_matches(actual, recorded):
    if not isinstance(actual, str) or not actual \
            or not isinstance(recorded, str) or not recorded:
        return False
    actual_norm = os.path.normpath(actual)
    recorded_norm = os.path.normpath(recorded)
    if os.path.isabs(recorded_norm):
        return actual_norm == recorded_norm
    return actual_norm == recorded_norm \
        or actual_norm.endswith(os.sep + recorded_norm)


def _artifact_binding_core(value, schema, label):
    _exact_keys(value, ARTIFACT_BINDING_KEYS, label)
    if value.get("schema") != schema or not _hex(value.get("sha256")) \
            or not isinstance(value.get("path"), str) or not value["path"]:
        raise V2BError(f"{label} is malformed")
    return value


def _same_artifact(actual, expected, label):
    """Require identical bytes/schema and the protocol-recorded path."""
    _artifact_binding_core(actual, expected.get("schema"), label)
    if actual["sha256"] != expected.get("sha256") \
            or not _path_matches(actual["path"], expected.get("path")):
        raise V2BError(f"{label} does not match its frozen binding")


def _validate_implementation_freeze(value, binding, protocol,
                                    protocol_binding):
    _artifact_binding_core(binding, IMPLEMENTATION_FREEZE_SCHEMA,
                           "implementation-freeze artifact binding")
    protocol_row = value.get("protocol") if isinstance(value, dict) else None
    if not isinstance(value, dict) \
            or value.get("schema") != IMPLEMENTATION_FREEZE_SCHEMA \
            or value.get("study_id") != protocol["study_id"] \
            or not isinstance(protocol_row, dict) \
            or protocol_row.get("raw_sha256") != \
            protocol_binding["raw_sha256"] \
            or protocol_row.get("semantic_sha256") != \
            protocol_binding["semantic_sha256"]:
        raise V2BError("implementation freeze is not bound to this study "
                       "and exact protocol")
    return value


def _protocol_binding(protocol_path):
    protocol, digest = load_protocol(protocol_path)
    binding = protocol_record()
    if binding["raw_sha256"] != digest:
        raise V2BError("confirmation protocol record/raw bytes drift")
    return protocol, binding


def _pilot_sets(protocol, pilot, candidates_sha):
    if pilot.get("schema") != BOUND_SAMPLE_SCHEMA \
            or pilot.get("sampling_state") != "drawn" \
            or not isinstance(pilot.get("plans"), dict):
        raise V2BError("pilot sample is malformed")
    plan = pilot["plans"].get(REPO)
    if not isinstance(plan, dict) or plan.get("repo") != REPO \
            or plan.get("language") != LANGUAGE \
            or plan.get("candidates_sha256") != candidates_sha \
            or not isinstance(plan.get("targets"), list):
        raise V2BError("pilot sample lacks the exact SymPy plan")
    keys = []
    modules = set()
    for index, row in enumerate(plan["targets"]):
        if not isinstance(row, dict):
            raise V2BError(f"pilot target[{index}] is malformed")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        keys.append(identity_key(LANGUAGE, identity))
        modules.add(identity[0])
    key_set_record = key_set(keys)
    module_set = key_set(modules)
    inputs = protocol["inputs"]
    if key_set_record["n"] != inputs["pilot_sympy_target_count"] \
            or key_set_record["sha256"] != \
            inputs["pilot_sympy_keys_sha256"] \
            or module_set["n"] != inputs["pilot_sympy_module_count"] \
            or module_set["sha256"] != inputs["pilot_sympy_modules_sha256"]:
        raise V2BError("pilot target/module exclusion binding drift")
    return key_set_record, module_set


def _candidate_index(candidates):
    if candidates.get("schema") != CANDIDATES_SCHEMA \
            or candidates.get("repo") != REPO \
            or candidates.get("language") != LANGUAGE:
        raise V2BError("confirmation candidates are not the SymPy table")
    rows = candidates.get("targets")
    if not isinstance(rows, list) \
            or candidates.get("n_candidates") != len(rows):
        raise V2BError("confirmation candidate table/count drift")
    out = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise V2BError(f"candidate[{index}] is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if key in out:
            raise V2BError(f"duplicate candidate key {key}")
        out[key] = dict(identity=list(identity), module=identity[0], row=row)
    return out


def _cluster_support(selected_keys, candidate_index):
    counts = {}
    for key in selected_keys:
        module = candidate_index[key]["module"]
        counts[module] = counts.get(module, 0) + 1
    rows = [[module, counts[module]] for module in sorted(counts)]
    n = len(selected_keys)
    denominator = sum(count * count for count in counts.values())
    effective = n * n / denominator if denominator else 0.0
    passed = len(counts) >= MIN_MODULES and effective >= \
        MIN_EFFECTIVE_CLUSTERS
    return dict(
        n_targets=n, n_modules=len(counts),
        module_counts=rows,
        module_counts_sha256=sha256_json(rows),
        effective_clusters=effective,
        effective_clusters_numerator=n * n,
        effective_clusters_denominator=denominator,
        minimum_modules=MIN_MODULES,
        minimum_effective_clusters=MIN_EFFECTIVE_CLUSTERS,
        passed=passed,
    )


def build_confirmation_sample(protocol, protocol_binding, gate, gate_binding,
                              candidates, candidate_binding, pilot,
                              pilot_binding, implementation_freeze,
                              implementation_freeze_binding):
    """Build the deterministic confirmation draw from already loaded values.

    This function is pure: it never checks Git state and never writes.  The
    production wrapper performs committed-input and before/after ledger gates.
    """
    if protocol_binding.get("raw_sha256") != PROTOCOL_RAW_SHA256 \
            or protocol_binding.get("semantic_sha256") != \
            PROTOCOL_SEMANTIC_SHA256:
        raise V2BError("confirmation protocol binding drift")
    _artifact_binding_core(gate_binding, GATE_SCHEMA,
                           "source-gate artifact binding")
    _artifact_binding_core(candidate_binding, CANDIDATES_SCHEMA,
                           "candidate artifact binding")
    _artifact_binding_core(pilot_binding, BOUND_SAMPLE_SCHEMA,
                           "pilot-sample artifact binding")
    _validate_implementation_freeze(
        implementation_freeze, implementation_freeze_binding, protocol,
        protocol_binding)
    protocol_candidate = protocol["inputs"]["candidates"]
    protocol_pilot = protocol["inputs"]["pilot_sample"]
    _same_artifact(candidate_binding, protocol_candidate,
                   "candidate artifact")
    _same_artifact(pilot_binding, protocol_pilot,
                   "pilot-sample artifact")

    # The gate module owns all schema, ledger, fragment, row, and source-only
    # eligibility validation.  Reuse that exact implementation here; the
    # checks below are only the sampler's joins to the separately loaded
    # candidate and pilot artifacts.
    validate_reduced_gate(gate, protocol)
    _same_artifact(candidate_binding, gate["bindings"]["candidates"],
                   "source-gate/candidate join")
    _same_artifact(pilot_binding, gate["bindings"]["pilot_sample"],
                   "source-gate/pilot join")
    _same_artifact(
        implementation_freeze_binding,
        gate["bindings"]["implementation_freeze"],
        "source-gate/implementation-freeze join")

    candidate_index = _candidate_index(candidates)
    pilot_keys, pilot_modules = _pilot_sets(
        protocol, pilot, candidate_binding["sha256"])
    if not set(pilot_keys["keys"]).issubset(candidate_index):
        raise V2BError("pilot target is absent from the candidate universe")

    candidate_keys = set(gate["candidate_keys"]["keys"])
    eligible = set(gate["eligible_keys"]["keys"])
    source_ineligible = set(gate["source_ineligible_keys"]["keys"])
    pilot_intersection = set(gate["pilot_key_intersection"]["keys"])
    gate_pilot_modules = set(gate["pilot_modules"]["keys"])
    pilot_module_candidates = set(
        gate["pilot_module_candidate_keys"]["keys"])
    post_pilot = set(gate["post_pilot_eligible_keys"]["keys"])
    exact_pilot_keys = set(pilot_keys["keys"])
    exact_pilot_modules = set(pilot_modules["keys"])
    expected_module_candidates = {
        key for key, row in candidate_index.items()
        if row["module"] in exact_pilot_modules}
    if candidate_keys != set(candidate_index):
        raise V2BError("source gate is not the loaded candidate universe")
    if gate_pilot_modules != exact_pilot_modules \
            or pilot_intersection != eligible & exact_pilot_keys \
            or pilot_module_candidates != expected_module_candidates \
            or post_pilot != eligible - expected_module_candidates:
        raise V2BError("source-gate/pilot module exclusion join drift")

    union_excluded = source_ineligible | pilot_module_candidates \
        | exact_pilot_keys
    if set(candidate_index) - union_excluded != post_pilot:
        raise V2BError("sampler exclusion union does not equal gate population")
    if len(post_pilot) < N_CONFIRMATION:
        raise V2BError("fewer than 200 module-disjoint source-eligible targets")

    plan = build_sample_plan(candidates, N_CONFIRMATION,
                             exclude_keys=frozenset(union_excluded))
    _exact_keys(plan, PLAN_KEYS, "confirmation sample plan")
    targets = plan.get("targets")
    if plan.get("schema") != SAMPLE_SCHEMA or plan.get("repo") != REPO \
            or plan.get("language") != LANGUAGE \
            or not isinstance(targets, list) \
            or len(targets) != plan.get("n_selected"):
        raise V2BError("confirmation sample plan identity/count drift")
    for index, row in enumerate(targets):
        _exact_keys(row, PLAN_TARGET_KEYS,
                    f"confirmation plan target[{index}]")
    shortfall = sum(plan.get("shortfalls", {}).values()) \
        if isinstance(plan.get("shortfalls"), dict) else None
    if plan.get("n_requested") != N_CONFIRMATION \
            or plan.get("n_selected") != N_CONFIRMATION \
            or shortfall != 0 \
            or plan.get("n_excluded") != len(union_excluded) \
            or plan.get("excluded_keys_sha256") != \
            sha256_json(sorted(union_excluded)):
        raise V2BError("confirmation draw did not realize exact N=200")

    selected = key_set(identity_key(
        LANGUAGE, validate_identity(LANGUAGE, row.get("identity")))
        for row in targets)
    selected_set = set(selected["keys"])
    if selected["n"] != N_CONFIRMATION \
            or not selected_set.issubset(post_pilot):
        raise V2BError("confirmation draw escaped the post-pilot population")
    selected_modules = key_set({
        candidate_index[key]["module"] for key in selected["keys"]})
    if set(selected_modules["keys"]) & set(pilot_modules["keys"]):
        raise V2BError("confirmation draw overlaps a pilot source module")
    cluster = _cluster_support(selected["keys"], candidate_index)
    if not cluster["passed"]:
        raise V2BError(
            "confirmation sample cluster support is inadequate pre-score")

    exclusions = dict(
        source_ineligible_keys=key_set(source_ineligible),
        pilot_target_keys=copy.deepcopy(pilot_keys),
        pilot_modules=copy.deepcopy(pilot_modules),
        pilot_module_candidate_keys=key_set(pilot_module_candidates),
        union_excluded_keys=key_set(union_excluded),
        post_pilot_eligible_keys=key_set(post_pilot),
    )
    return dict(
        schema=CONFIRMATION_SAMPLE_SCHEMA,
        state=SAMPLE_STATE,
        study_id=protocol["study_id"],
        repo=REPO,
        language=LANGUAGE,
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=BUDGET_BYTES,
        requested_n=N_CONFIRMATION,
        realized_n=N_CONFIRMATION,
        protocol=copy.deepcopy(protocol_binding),
        bindings=dict(
            source_gate=copy.deepcopy(gate_binding),
            candidates=copy.deepcopy(candidate_binding),
            pilot_sample=copy.deepcopy(pilot_binding),
            implementation_freeze=copy.deepcopy(
                implementation_freeze_binding)),
        exclusion_bindings=exclusions,
        plan=plan,
        selected_keys=selected,
        selected_modules=selected_modules,
        cluster_support=cluster,
    )


def _ledger_entries(paths_and_bindings):
    rows = []
    for label, path, binding in paths_and_bindings:
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat sample input {label}: {err}") from err
        digest = sha256_file(path)
        if digest != binding["sha256"]:
            raise V2BError(f"sample input changed before ledger: {label}")
        rows.append(dict(label=label, bytes=size, sha256=digest))
    rows.sort(key=lambda row: row["label"])
    return rows


def _input_ledger(pre, post):
    pre_digest = sha256_sorted_json(pre)
    post_digest = sha256_sorted_json(post)
    if pre != post or pre_digest != post_digest:
        raise V2BError("confirmation sample inputs changed during the draw")
    return dict(
        algorithm="sha256-sorted-json-file-ledger-v1",
        n_entries=len(pre), entries=post,
        entries_sha256=post_digest,
        pre_entries_sha256=pre_digest,
        post_entries_sha256=post_digest,
        unchanged=True,
    )


def prepare(source_gate_path, candidates_path, pilot_sample_path,
            implementation_freeze_path, protocol_path=PROTOCOL_PATH):
    """Production entry: committed blind boundary plus one exact draw."""
    if not source_clean():
        raise V2BError("source tree dirty outside results_v2")
    for path in (protocol_path, source_gate_path, pilot_sample_path,
                 implementation_freeze_path):
        require_committed(path)
    commit_start, tree_start = head_commit(), source_tree_hash()
    protocol, protocol_row = _protocol_binding(protocol_path)
    gate, gate_digest = load_reduced_gate(source_gate_path, protocol_path)
    gate_binding = dict(path=os.path.abspath(source_gate_path),
                        schema=GATE_SCHEMA, sha256=gate_digest)
    repo, candidate_binding, candidates, _ = \
        _validate_candidate_table(candidates_path)
    if repo != REPO:
        raise V2BError("confirmation candidate table is not SymPy")
    pilot_binding, pilot = artifact_binding(pilot_sample_path,
                                            BOUND_SAMPLE_SCHEMA)
    implementation_freeze_binding, implementation_freeze = artifact_binding(
        implementation_freeze_path, IMPLEMENTATION_FREEZE_SCHEMA)
    from freeze_v2b_nll_confirmation import validate_live_freeze
    validate_live_freeze(implementation_freeze,
                         implementation_freeze_path)
    inputs = (
        ("candidates", candidates_path, candidate_binding),
        ("pilot_sample", pilot_sample_path, pilot_binding),
        ("protocol", protocol_path,
         dict(path=protocol_row["path"], schema=protocol_row["schema"],
              sha256=protocol_row["raw_sha256"])),
        ("source_gate", source_gate_path, gate_binding),
        ("implementation_freeze", implementation_freeze_path,
         implementation_freeze_binding),
    )
    pre = _ledger_entries(inputs)
    sample = build_confirmation_sample(
        protocol, protocol_row, gate, gate_binding, candidates,
        candidate_binding, pilot, pilot_binding, implementation_freeze,
        implementation_freeze_binding)
    post = _ledger_entries(inputs)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("source changed during confirmation draw")
    sample["input_ledger"] = _input_ledger(pre, post)
    program_path = os.path.abspath(__file__)
    sample["generator"] = dict(
        program=os.path.basename(program_path),
        program_sha256=sha256_file(program_path),
        source_commit=commit_start,
        source_tree_hash=tree_start,
    )
    return sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-gate", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--pilot-sample", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sample = prepare(args.source_gate, args.candidates, args.pilot_sample,
                     args.implementation_freeze, args.protocol)
    digest = write_new_json(args.out, sample)
    print(f"[v2b-confirmation-sample] {sample['realized_n']} fresh SymPy "
          f"targets / G={sample['cluster_support']['n_modules']} -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
