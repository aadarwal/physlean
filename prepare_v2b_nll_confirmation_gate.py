#!/usr/bin/env python3
"""Build the model/outcome-free SymPy E2 confirmation source gate.

``fragment`` computes exact frozen k4 and k5:seed-0 maximal rendering byte
totals for one contiguous shard of the sealed 19,926-candidate universe.
``reduce`` requires a disjoint, complete shard partition and joins it to the
sealed pilot evidence, publishing the only artifact the later confirmation
sampler may consume.  Neither path imports a tokenizer/model, evaluates BM25,
draws a sample, or reads any NLL/outcome.
"""
import argparse
import os
import sys

from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA, V2BError,
                        identity_key, load_json, sha256_file, sha256_json,
                        sha256_sorted_json, validate_identity, write_new_json)
from v2b_nll_confirmation import (PROTOCOL_PATH, PROTOCOL_RAW_SHA256,
                                  PROTOCOL_SCHEMA,
                                  PROTOCOL_SEMANTIC_SHA256, load_protocol)
from v2b_nll_confirmation_context import (ContextMassIndex, LANGUAGE, REPO,
                                          AUDIT_DOMAIN, AUDIT_N, AUDIT_SCHEMA,
                                          load_bound_json,
                                          load_source_chain)
from v2b_metadata import corpus_git_identity


FRAGMENT_SCHEMA = "v2b_nll_e2_confirmation_source_gate_fragment_v1"
GATE_SCHEMA = "v2b_nll_e2_confirmation_source_gate_v1"
FRAGMENT_STATE = "complete-model-and-outcome-free-fragment"
GATE_STATE = "complete-model-and-outcome-free-before-confirmation-sample"
PROGRAM = "prepare_v2b_nll_confirmation_gate.py"
CONTEXT_PROGRAM = "v2b_nll_confirmation_context.py"

FRAGMENT_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "budget_bytes", "protocol", "bindings",
    "input_ledger", "graph_index", "shard_index", "shard_count",
    "candidate_index_start", "candidate_index_end", "candidate_keys",
    "cross_check", "n_rows", "rows", "rows_sha256", "generator",
}
GATE_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "budget_bytes", "protocol", "bindings",
    "input_ledger", "graph_index", "fragments", "candidate_keys",
    "eligible_keys", "source_ineligible_keys", "pilot_key_intersection",
    "pilot_modules", "pilot_module_candidate_keys",
    "post_pilot_eligible_keys", "n_rows", "rows", "rows_sha256",
    "cross_check", "generator",
}
ROW_KEYS = {
    "key", "identity", "module", "k4_rendering_bytes", "k4_eligible",
    "k5_seed0_rendering_bytes", "k5_seed0_eligible", "eligible",
    "ineligibility_reasons",
}
GENERATOR_KEYS = {
    "program", "program_sha256", "context_program",
    "context_program_sha256", "source_commit", "source_tree_hash",
}
LEDGER_KEYS = {
    "algorithm", "n_entries", "entries", "entries_sha256",
    "pre_entries_sha256", "post_entries_sha256", "unchanged",
}
GRAPH_KEYS = {
    "method", "n_units", "n_edges", "n_scc", "max_scc_size",
    "n_source_files", "source_labels_sha256",
}
FRAGMENT_BINDING_KEYS = {
    "path", "sha256", "schema", "shard_index", "shard_count",
    "candidate_index_start", "candidate_index_end",
    "candidate_keys_sha256", "rows_sha256",
}
CROSS_CHECK_KEYS = {
    "schema", "selection", "reference", "rows", "rows_sha256", "passed",
}
CROSS_CHECK_ROW_KEYS = {
    "key", "bitset_k4_rendering_bytes", "full_k4_rendering_bytes",
    "bitset_k5_seed0_rendering_bytes", "full_k5_seed0_rendering_bytes",
    "k4_eligible", "k5_seed0_eligible", "eligible", "passed",
}


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _repo_rel(path):
    real_base, real = os.path.realpath(BASE), os.path.realpath(path)
    try:
        if os.path.commonpath((real_base, real)) != real_base:
            raise V2BError(f"path lies outside source checkout: {path}")
    except ValueError as err:
        raise V2BError(f"source-checkout path mismatch: {err}") from err
    return os.path.relpath(real, real_base).replace(os.sep, "/")


def protocol_record():
    return dict(path=_repo_rel(PROTOCOL_PATH), schema=PROTOCOL_SCHEMA,
                raw_sha256=PROTOCOL_RAW_SHA256,
                semantic_sha256=PROTOCOL_SEMANTIC_SHA256)


def _load_protocol_boundary(path=PROTOCOL_PATH):
    if os.path.realpath(path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation gate requires the canonical protocol path")
    require_committed(path)
    return load_protocol(path)[0]


def _freeze_schema(protocol):
    schema = protocol.get("execution_schema_contracts", {}).get(
        "implementation_freeze")
    if schema != "v2b_nll_e2_confirmation_implementation_freeze_v1":
        raise V2BError("confirmation implementation-freeze schema drift")
    return schema


def _validate_freeze_binding(value, protocol):
    _exact_keys(value, {"path", "schema", "sha256"},
                "implementation_freeze binding")
    if not isinstance(value["path"], str) or not value["path"] \
            or not os.path.isabs(value["path"]) \
            or os.path.normpath(value["path"]) != value["path"] \
            or value["schema"] != _freeze_schema(protocol) \
            or not _hex(value["sha256"]):
        raise V2BError("implementation-freeze binding drift")
    return value


def load_implementation_freeze(path, protocol):
    absolute = os.path.abspath(path)
    if os.path.normpath(absolute) != absolute:
        raise V2BError("implementation-freeze path is noncanonical")
    require_committed(absolute)
    schema = _freeze_schema(protocol)
    value, digest = load_json(absolute, schema)
    if value.get("study_id") != protocol["study_id"] \
            or value.get("protocol") != protocol_record():
        raise V2BError("implementation freeze study/protocol binding drift")
    from freeze_v2b_nll_confirmation import validate_live_freeze
    validate_live_freeze(value, absolute)
    return value, dict(path=absolute, schema=schema, sha256=digest)


def key_set(keys):
    ordered = sorted(keys)
    if any(not isinstance(key, str) or not key for key in ordered) \
            or len(ordered) != len(set(ordered)):
        raise V2BError("key set contains malformed/duplicate entries")
    return dict(n=len(ordered), sha256=sha256_json(ordered), keys=ordered)


def _validate_key_set(value, label):
    _exact_keys(value, {"n", "sha256", "keys"}, label)
    keys = value["keys"]
    if not isinstance(keys, list) or keys != sorted(keys) \
            or len(keys) != len(set(keys)) \
            or any(not isinstance(key, str) or not key for key in keys) \
            or value["n"] != len(keys) \
            or value["sha256"] != sha256_json(keys):
        raise V2BError(f"{label} content/hash drift")
    return keys


def capture_ledger(label_paths):
    rows = []
    seen = set()
    for label, path in label_paths:
        if not isinstance(label, str) or not label or label in seen:
            raise V2BError(f"duplicate/malformed input-ledger label {label!r}")
        seen.add(label)
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat ledger input {label}: {err}") from err
        rows.append(dict(label=label, bytes=size, sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    return rows


def ledger_record(pre, post):
    if pre != post:
        raise V2BError("confirmation gate input bytes drifted during execution")
    digest = sha256_sorted_json(pre)
    return dict(algorithm="sha256", n_entries=len(pre), entries=pre,
                entries_sha256=digest, pre_entries_sha256=digest,
                post_entries_sha256=digest, unchanged=True)


def _validate_ledger(value, label="input_ledger"):
    _exact_keys(value, LEDGER_KEYS, label)
    entries = value["entries"]
    if value["algorithm"] != "sha256" or value["unchanged"] is not True \
            or not isinstance(entries, list) \
            or value["n_entries"] != len(entries):
        raise V2BError(f"{label} header drift")
    labels = []
    for i, row in enumerate(entries):
        _exact_keys(row, {"label", "bytes", "sha256"},
                    f"{label}.entries[{i}]")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError(f"malformed {label} entry")
        labels.append(row["label"])
    digest = sha256_sorted_json(entries)
    if labels != sorted(labels) or len(labels) != len(set(labels)) \
            or any(value[name] != digest for name in (
                "entries_sha256", "pre_entries_sha256",
                "post_entries_sha256")):
        raise V2BError(f"{label} equality/hash drift")
    return value


def generator_record(commit=None, tree=None):
    commit = head_commit() if commit is None else commit
    tree = source_tree_hash() if tree is None else tree
    return dict(program=PROGRAM,
                program_sha256=sha256_file(os.path.join(BASE, PROGRAM)),
                context_program=CONTEXT_PROGRAM,
                context_program_sha256=sha256_file(
                    os.path.join(BASE, CONTEXT_PROGRAM)),
                source_commit=commit, source_tree_hash=tree)


def _validate_generator(value):
    _exact_keys(value, GENERATOR_KEYS, "generator")
    if value["program"] != PROGRAM \
            or value["context_program"] != CONTEXT_PROGRAM \
            or not _hex(value["program_sha256"]) \
            or not _hex(value["context_program_sha256"]) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("confirmation gate generator drift")
    return value


def _validate_graph(value):
    _exact_keys(value, GRAPH_KEYS, "graph_index")
    if value["method"] != \
            "scc-condensation-python-int-bitset-additive-render-mass-v1":
        raise V2BError("confirmation graph-index method drift")
    for name in GRAPH_KEYS - {"method", "source_labels_sha256"}:
        if not isinstance(value[name], int) or isinstance(value[name], bool) \
                or value[name] < (1 if name in ("n_units", "n_scc",
                                                "max_scc_size") else 0):
            raise V2BError(f"invalid graph-index {name}")
    if value["n_scc"] > value["n_units"] \
            or value["max_scc_size"] > value["n_units"] \
            or value["n_source_files"] <= 0 \
            or not _hex(value["source_labels_sha256"]):
        raise V2BError("impossible graph-index component counts")
    return value


def _audit_selection(candidate_keys):
    return sorted(sorted(
        candidate_keys,
        key=lambda key: (sha256_json([AUDIT_DOMAIN, key]), key)
    )[:min(AUDIT_N, len(candidate_keys))])


def _validate_cross_check(value, budget, candidate_keys=None):
    _exact_keys(value, CROSS_CHECK_KEYS, "cross_check")
    selection = value["selection"]
    _exact_keys(selection, {"domain", "n", "sha256", "keys"},
                "cross_check.selection")
    keys = selection["keys"]
    if value["schema"] != AUDIT_SCHEMA or value["passed"] is not True \
            or selection["domain"] != AUDIT_DOMAIN \
            or not isinstance(keys, list) or keys != sorted(keys) \
            or len(keys) != len(set(keys)) or selection["n"] != len(keys) \
            or selection["sha256"] != sha256_json(keys) \
            or value["reference"] != \
            "canonical_dependency_order+k5_unit_order(seed=0)+render_chunks":
        raise V2BError("cross-check identity/selection drift")
    if candidate_keys is not None and keys != _audit_selection(candidate_keys):
        raise V2BError("cross-check is not the deterministic candidate sample")
    rows = value["rows"]
    if not isinstance(rows, list) or len(rows) != len(keys) \
            or value["rows_sha256"] != sha256_sorted_json(rows):
        raise V2BError("cross-check row count/hash drift")
    for key, row in zip(keys, rows):
        _exact_keys(row, CROSS_CHECK_ROW_KEYS, "cross-check row")
        k4a, k4b = row["bitset_k4_rendering_bytes"], \
            row["full_k4_rendering_bytes"]
        k5a, k5b = row["bitset_k5_seed0_rendering_bytes"], \
            row["full_k5_seed0_rendering_bytes"]
        if row["key"] != key \
                or any(not isinstance(n, int) or isinstance(n, bool) or n < 0
                       for n in (k4a, k4b, k5a, k5b)) \
                or k4a != k4b or k5a != k5b \
                or row["k4_eligible"] is not (k4b >= budget) \
                or row["k5_seed0_eligible"] is not (k5b >= budget) \
                or row["eligible"] is not (k4b >= budget and k5b >= budget) \
                or row["passed"] is not True:
            raise V2BError("cross-check optimized/full-render mismatch")
    return value


def _validate_row(row, budget):
    _exact_keys(row, ROW_KEYS, "source-gate row")
    identity = validate_identity(LANGUAGE, row["identity"])
    key = identity_key(LANGUAGE, identity)
    if row["key"] != key or row["module"] != identity[0]:
        raise V2BError("source-gate row identity/key/module drift")
    for name in ("k4_rendering_bytes", "k5_seed0_rendering_bytes"):
        if not isinstance(row[name], int) or isinstance(row[name], bool) \
                or row[name] < 0:
            raise V2BError(f"source-gate row invalid {name}")
    k4 = row["k4_rendering_bytes"] >= budget
    k5 = row["k5_seed0_rendering_bytes"] >= budget
    expected_reasons = ([] if k4 else ["k4-rendering-below-budget"]) + \
        ([] if k5 else ["k5-seed0-rendering-below-budget"])
    if row["k4_eligible"] is not k4 \
            or row["k5_seed0_eligible"] is not k5 \
            or row["eligible"] is not (k4 and k5) \
            or row["ineligibility_reasons"] != expected_reasons:
        raise V2BError("source-gate row eligibility/reason drift")
    return key


def build_fragment_value(protocol, bindings, candidate_identities,
                         mass_index, shard_index, shard_count,
                         input_ledger, generator):
    if not isinstance(shard_count, int) or isinstance(shard_count, bool) \
            or shard_count <= 0 or not isinstance(shard_index, int) \
            or isinstance(shard_index, bool) \
            or not 0 <= shard_index < shard_count:
        raise V2BError("invalid confirmation source-gate shard")
    ordered = sorted(candidate_identities)
    expected_n = protocol["source_eligibility_gate"]["candidate_universe_n"]
    if len(ordered) != expected_n:
        raise V2BError("fragment candidate universe count drift")
    if shard_count > len(ordered):
        raise V2BError("fragment shard count exceeds candidate count")
    start = len(ordered) * shard_index // shard_count
    end = len(ordered) * (shard_index + 1) // shard_count
    keys = ordered[start:end]
    budget = protocol["scope"]["budget_bytes"]
    rows = [mass_index.row(candidate_identities[key], budget) for key in keys]
    return dict(
        schema=FRAGMENT_SCHEMA, state=FRAGMENT_STATE,
        study_id=protocol["study_id"], repo=REPO, language=LANGUAGE,
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=budget, protocol=protocol_record(), bindings=bindings,
        input_ledger=input_ledger, graph_index=mass_index.stats,
        shard_index=shard_index, shard_count=shard_count,
        candidate_index_start=start, candidate_index_end=end,
        candidate_keys=key_set(keys),
        cross_check=mass_index.cross_check(candidate_identities, budget),
        n_rows=len(rows), rows=rows,
        rows_sha256=sha256_sorted_json(rows), generator=generator)


def _expected_source_bindings(protocol):
    return protocol["source_eligibility_gate"]["bindings"]


def _validate_fragment(value, protocol, implementation_freeze_binding=None):
    _exact_keys(value, FRAGMENT_KEYS, "source-gate fragment")
    budget = protocol["scope"]["budget_bytes"]
    if value["schema"] != FRAGMENT_SCHEMA or value["state"] != FRAGMENT_STATE \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != REPO or value["language"] != LANGUAGE \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["budget_bytes"] != budget \
            or value["protocol"] != protocol_record():
        raise V2BError("source-gate fragment identity/binding drift")
    expected_bindings = dict(_expected_source_bindings(protocol))
    if implementation_freeze_binding is None:
        if not isinstance(value["bindings"], dict) \
                or set(value["bindings"]) != \
                set(expected_bindings) | {"implementation_freeze"}:
            raise V2BError("source-gate fragment binding key drift")
        implementation_freeze_binding = value["bindings"][
            "implementation_freeze"]
    _validate_freeze_binding(implementation_freeze_binding, protocol)
    expected_bindings["implementation_freeze"] = \
        implementation_freeze_binding
    if value["bindings"] != expected_bindings:
        raise V2BError("source-gate fragment implementation binding drift")
    ledger = _validate_ledger(value["input_ledger"],
                              "fragment input_ledger")
    ledger_labels = {row["label"] for row in ledger["entries"]}
    mandatory = {
        "input:protocol", "input:implementation_freeze", "input:candidates",
        "input:extraction", "input:k7_order", "input:neardup",
        "input:a6_outcome",
    }
    corpus_labels = sorted(
        label for label in ledger_labels if label.startswith("corpus:"))
    if ledger_labels != mandatory | set(corpus_labels) \
            or len(corpus_labels) != value["graph_index"]["n_source_files"] \
            or sha256_json(corpus_labels) != \
            value["graph_index"]["source_labels_sha256"]:
        raise V2BError("fragment input ledger is not the complete exact "
                       "source-file set")
    ledger_by_label = {row["label"]: row for row in ledger["entries"]}
    expected_digests = {
        "input:protocol": PROTOCOL_RAW_SHA256,
        "input:implementation_freeze": implementation_freeze_binding[
            "sha256"],
        **{f"input:{name}": expected_bindings[name]["sha256"]
           for name in ("candidates", "extraction", "k7_order", "neardup",
                        "a6_outcome")},
    }
    if any(ledger_by_label[label]["sha256"] != digest
           for label, digest in expected_digests.items()):
        raise V2BError("fragment input-ledger digest disagrees with binding")
    _validate_graph(value["graph_index"])
    if value["graph_index"]["n_units"] < protocol[
            "source_eligibility_gate"]["candidate_universe_n"]:
        raise V2BError("fragment graph has fewer units than candidates")
    _validate_generator(value["generator"])
    shard, count = value["shard_index"], value["shard_count"]
    start, end = value["candidate_index_start"], \
        value["candidate_index_end"]
    if not isinstance(shard, int) or isinstance(shard, bool) \
            or not isinstance(count, int) or isinstance(count, bool) \
            or count <= 0 or not 0 <= shard < count \
            or not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) \
            or not 0 <= start <= end:
        raise V2BError("source-gate fragment shard/range drift")
    keys = _validate_key_set(value["candidate_keys"],
                             "fragment candidate_keys")
    rows = value["rows"]
    if not isinstance(rows, list) or value["n_rows"] != len(rows) \
            or len(rows) != len(keys) or end - start != len(keys) \
            or value["rows_sha256"] != sha256_sorted_json(rows):
        raise V2BError("source-gate fragment row/range hash drift")
    row_keys = [_validate_row(row, budget) for row in rows]
    if row_keys != keys:
        raise V2BError("fragment row order does not equal candidate-key slice")
    _validate_cross_check(value["cross_check"], budget)
    return value


def _candidate_identities(candidates, protocol):
    targets = candidates.get("targets")
    expected_n = protocol["source_eligibility_gate"]["candidate_universe_n"]
    if candidates.get("schema") != CANDIDATES_SCHEMA \
            or candidates.get("repo") != REPO \
            or candidates.get("language") != LANGUAGE \
            or candidates.get("corpus_git_sha") != \
            protocol["scope"]["corpus_git_sha"] \
            or candidates.get("n_candidates") != expected_n \
            or not isinstance(targets, list) or len(targets) != expected_n:
        raise V2BError("reducer candidate table drift")
    out = {}
    for row in targets:
        if not isinstance(row, dict):
            raise V2BError("candidate target is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if key in out:
            raise V2BError("duplicate candidate target identity")
        out[key] = list(identity)
    return out


def _pilot_sets(pilot, protocol, candidate_identities):
    inputs = protocol["inputs"]
    plans = pilot.get("plans")
    plan = plans.get(REPO) if isinstance(plans, dict) else None
    targets = plan.get("targets") if isinstance(plan, dict) else None
    if pilot.get("schema") != BOUND_SAMPLE_SCHEMA \
            or pilot.get("sampling_state") != "drawn" \
            or pilot.get("plans_sha256") != sha256_sorted_json(plans) \
            or pilot.get("n_requested_per_corpus") != \
            inputs["pilot_sympy_target_count"] \
            or not isinstance(targets, list) \
            or plan.get("candidates_sha256") != \
            inputs["candidates"]["sha256"] \
            or plan.get("n_selected") != len(targets) \
            or len(targets) != inputs["pilot_sympy_target_count"]:
        raise V2BError("sealed pilot plan binding/count drift")
    keys, modules = [], set()
    for row in targets:
        if not isinstance(row, dict):
            raise V2BError("pilot target is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if key not in candidate_identities:
            raise V2BError("pilot target absent from candidate universe")
        keys.append(key)
        modules.add(identity[0])
    keys = sorted(keys)
    modules = sorted(modules)
    if len(keys) != len(set(keys)) \
            or sha256_json(keys) != inputs["pilot_sympy_keys_sha256"] \
            or len(modules) != inputs["pilot_sympy_module_count"] \
            or sha256_json(modules) != inputs["pilot_sympy_modules_sha256"]:
        raise V2BError("sealed pilot key/module hash drift")
    return keys, modules


def reduce_gate_values(protocol, candidates, pilot, fragment_inputs,
                       implementation_freeze_binding, input_ledger,
                       generator):
    candidate_identities = _candidate_identities(candidates, protocol)
    ordered_candidates = sorted(candidate_identities)
    if not isinstance(fragment_inputs, list) or not fragment_inputs:
        raise V2BError("source-gate reducer received no fragments")
    validated = []
    for record in fragment_inputs:
        _exact_keys(record, {"path", "sha256", "value"},
                    "reducer fragment input")
        if not isinstance(record["path"], str) or not record["path"] \
                or not _hex(record["sha256"]):
            raise V2BError("malformed reducer fragment binding")
        validated.append((record, _validate_fragment(
            record["value"], protocol, implementation_freeze_binding)))
    validated.sort(key=lambda pair: pair[1]["shard_index"])
    shard_count = validated[0][1]["shard_count"]
    if len(validated) != shard_count \
            or [value["shard_index"] for _, value in validated] != \
            list(range(shard_count)):
        raise V2BError("fragments are not exactly shards 0..count-1")
    graph = validated[0][1]["graph_index"]
    fragment_generator = validated[0][1]["generator"]
    fragment_ledger = validated[0][1]["input_ledger"]
    cross_check = validated[0][1]["cross_check"]
    if fragment_generator != generator:
        raise V2BError("fragment generator is not the reducer implementation")
    rows = []
    fragment_rows = []
    for expected_shard, (record, value) in enumerate(validated):
        start = len(ordered_candidates) * expected_shard // shard_count
        end = len(ordered_candidates) * (expected_shard + 1) // shard_count
        expected_keys = ordered_candidates[start:end]
        if value["shard_count"] != shard_count \
                or value["candidate_index_start"] != start \
                or value["candidate_index_end"] != end \
                or value["candidate_keys"]["keys"] != expected_keys \
                or value["graph_index"] != graph \
                or value["generator"] != fragment_generator \
                or value["input_ledger"] != fragment_ledger \
                or value["cross_check"] != cross_check:
            raise V2BError("fragment range/graph/provenance does not match "
                           "the exact common census")
        rows.extend(value["rows"])
        fragment_rows.append(dict(
            path=record["path"], sha256=record["sha256"],
            schema=FRAGMENT_SCHEMA, shard_index=expected_shard,
            shard_count=shard_count, candidate_index_start=start,
            candidate_index_end=end,
            candidate_keys_sha256=value["candidate_keys"]["sha256"],
            rows_sha256=value["rows_sha256"]))
    if [row["key"] for row in rows] != ordered_candidates:
        raise V2BError("fragment union is not the exact candidate universe")

    eligible = [row["key"] for row in rows if row["eligible"]]
    ineligible = [row["key"] for row in rows if not row["eligible"]]
    pilot_keys, pilot_modules = _pilot_sets(
        pilot, protocol, candidate_identities)
    pilot_intersection = sorted(set(eligible) & set(pilot_keys))
    expectation = protocol["source_eligibility_gate"][
        "pilot_intersection_expected_from_sealed_pilot_evidence"]
    if len(pilot_intersection) != expectation["count"] \
            or sha256_json(pilot_intersection) != expectation["keys_sha256"]:
        raise V2BError("pilot/source-eligible audit expectation failed")
    pilot_module_candidates = sorted(
        key for key, identity in candidate_identities.items()
        if identity[0] in set(pilot_modules))
    post_pilot = sorted(set(eligible) - set(pilot_module_candidates))

    bindings = dict(_expected_source_bindings(protocol),
                    pilot_sample=protocol["inputs"]["pilot_sample"],
                    implementation_freeze=implementation_freeze_binding)
    return dict(
        schema=GATE_SCHEMA, state=GATE_STATE, study_id=protocol["study_id"],
        repo=REPO, language=LANGUAGE,
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=protocol["scope"]["budget_bytes"],
        protocol=protocol_record(), bindings=bindings,
        input_ledger=input_ledger, graph_index=graph,
        fragments=fragment_rows, candidate_keys=key_set(ordered_candidates),
        eligible_keys=key_set(eligible),
        source_ineligible_keys=key_set(ineligible),
        pilot_key_intersection=key_set(pilot_intersection),
        pilot_modules=key_set(pilot_modules),
        pilot_module_candidate_keys=key_set(pilot_module_candidates),
        post_pilot_eligible_keys=key_set(post_pilot), cross_check=cross_check,
        n_rows=len(rows), rows=rows, rows_sha256=sha256_sorted_json(rows),
        generator=generator)


def validate_reduced_gate(value, protocol):
    _exact_keys(value, GATE_KEYS, "reduced confirmation source gate")
    bindings = value.get("bindings")
    base_bindings = dict(_expected_source_bindings(protocol),
                         pilot_sample=protocol["inputs"]["pilot_sample"])
    if not isinstance(bindings, dict) or set(bindings) != \
            set(base_bindings) | {"implementation_freeze"}:
        raise V2BError("reduced source-gate binding key drift")
    freeze_binding = _validate_freeze_binding(
        bindings["implementation_freeze"], protocol)
    expected_bindings = dict(base_bindings,
                             implementation_freeze=freeze_binding)
    budget = protocol["scope"]["budget_bytes"]
    if value["schema"] != GATE_SCHEMA or value["state"] != GATE_STATE \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != REPO or value["language"] != LANGUAGE \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["budget_bytes"] != budget \
            or value["protocol"] != protocol_record() \
            or value["bindings"] != expected_bindings:
        raise V2BError("reduced source-gate identity/binding drift")
    _validate_ledger(value["input_ledger"])
    _validate_graph(value["graph_index"])
    _validate_generator(value["generator"])
    candidates = _validate_key_set(value["candidate_keys"],
                                   "candidate_keys")
    if value["graph_index"]["n_units"] < len(candidates):
        raise V2BError("reduced graph has fewer units than candidates")
    eligible = _validate_key_set(value["eligible_keys"], "eligible_keys")
    ineligible = _validate_key_set(value["source_ineligible_keys"],
                                   "source_ineligible_keys")
    pilot_intersection = _validate_key_set(
        value["pilot_key_intersection"], "pilot_key_intersection")
    pilot_modules = _validate_key_set(value["pilot_modules"],
                                      "pilot_modules")
    pilot_module_candidates = _validate_key_set(
        value["pilot_module_candidate_keys"],
        "pilot_module_candidate_keys")
    post_pilot = _validate_key_set(value["post_pilot_eligible_keys"],
                                   "post_pilot_eligible_keys")
    if len(candidates) != protocol["source_eligibility_gate"][
            "candidate_universe_n"] \
            or set(eligible) & set(ineligible) \
            or sorted(set(eligible) | set(ineligible)) != candidates:
        raise V2BError("eligible/ineligible sets do not partition candidates")
    expectation = protocol["source_eligibility_gate"][
        "pilot_intersection_expected_from_sealed_pilot_evidence"]
    if len(pilot_intersection) != expectation["count"] \
            or sha256_json(pilot_intersection) != expectation["keys_sha256"] \
            or len(pilot_modules) != protocol["inputs"][
                "pilot_sympy_module_count"] \
            or sha256_json(pilot_modules) != protocol["inputs"][
                "pilot_sympy_modules_sha256"]:
        raise V2BError("reduced source-gate pilot expectation drift")
    rows = value["rows"]
    if not isinstance(rows, list) or value["n_rows"] != len(rows) \
            or len(rows) != len(candidates) \
            or value["rows_sha256"] != sha256_sorted_json(rows):
        raise V2BError("reduced source-gate row hash/count drift")
    row_keys = [_validate_row(row, budget) for row in rows]
    if row_keys != candidates:
        raise V2BError("reduced source-gate row order/universe drift")
    row_eligible = [row["key"] for row in rows if row["eligible"]]
    if row_eligible != eligible:
        raise V2BError("eligible key set disagrees with rows")
    cross_check = _validate_cross_check(
        value["cross_check"], budget, candidates)
    row_by_key = {row["key"]: row for row in rows}
    for audit in cross_check["rows"]:
        census = row_by_key[audit["key"]]
        if census["k4_rendering_bytes"] != \
                audit["full_k4_rendering_bytes"] \
                or census["k5_seed0_rendering_bytes"] != \
                audit["full_k5_seed0_rendering_bytes"] \
                or census["k4_eligible"] is not audit["k4_eligible"] \
                or census["k5_seed0_eligible"] is not \
                audit["k5_seed0_eligible"] \
                or census["eligible"] is not audit["eligible"]:
            raise V2BError("full-render cross-check disagrees with census row")
    modules_from_rows = {
        row["key"] for row in rows if row["module"] in set(pilot_modules)}
    if sorted(modules_from_rows) != pilot_module_candidates \
            or sorted(set(eligible) - modules_from_rows) != post_pilot \
            or not set(pilot_intersection) <= set(eligible):
        raise V2BError("pilot-module/post-pilot set derivation drift")

    fragments = value["fragments"]
    if not isinstance(fragments, list) or not fragments:
        raise V2BError("reduced source gate lacks fragment bindings")
    expected_start = 0
    count = len(fragments)
    fragment_paths = []
    for index, row in enumerate(fragments):
        _exact_keys(row, FRAGMENT_BINDING_KEYS, f"fragments[{index}]")
        if row["schema"] != FRAGMENT_SCHEMA \
                or row["shard_index"] != index \
                or row["shard_count"] != count \
                or row["candidate_index_start"] != expected_start \
                or not isinstance(row["candidate_index_end"], int) \
                or row["candidate_index_end"] < expected_start \
                or not isinstance(row["path"], str) or not row["path"] \
                or not _hex(row["sha256"]) \
                or not _hex(row["candidate_keys_sha256"]) \
                or not _hex(row["rows_sha256"]):
            raise V2BError("reduced source-gate fragment manifest drift")
        expected_slice_start = len(candidates) * index // count
        expected_slice_end = len(candidates) * (index + 1) // count
        if row["candidate_index_start"] != expected_slice_start \
                or row["candidate_index_end"] != expected_slice_end \
                or row["candidate_keys_sha256"] != sha256_json(
                    candidates[expected_slice_start:expected_slice_end]) \
                or row["rows_sha256"] != sha256_sorted_json(
                    rows[expected_slice_start:expected_slice_end]):
            raise V2BError("fragment manifest hash/range disagrees with rows")
        fragment_paths.append(row["path"])
        expected_start = row["candidate_index_end"]
    if expected_start != len(candidates) \
            or len(fragment_paths) != len(set(fragment_paths)):
        raise V2BError("fragment manifest does not cover candidate universe")
    expected_ledger_labels = {
        "input:protocol", "input:implementation_freeze", "input:candidates",
        "input:pilot_sample",
        *(f"fragment:{index:06d}" for index in range(len(fragments))),
    }
    observed_ledger_labels = {
        row["label"] for row in value["input_ledger"]["entries"]}
    if observed_ledger_labels != expected_ledger_labels:
        raise V2BError("reducer input ledger is not the exact input set")
    ledger_by_label = {
        row["label"]: row for row in value["input_ledger"]["entries"]}
    expected_ledger_digests = {
        "input:protocol": PROTOCOL_RAW_SHA256,
        "input:implementation_freeze": freeze_binding["sha256"],
        "input:candidates": expected_bindings["candidates"]["sha256"],
        "input:pilot_sample": expected_bindings["pilot_sample"]["sha256"],
        **{f"fragment:{index:06d}": row["sha256"]
           for index, row in enumerate(fragments)},
    }
    if any(ledger_by_label[label]["sha256"] != digest
           for label, digest in expected_ledger_digests.items()):
        raise V2BError("reducer input-ledger digest disagrees with binding")
    return value


def load_reduced_gate(path, protocol_path=PROTOCOL_PATH):
    """Load and fully validate a reduced gate for downstream consumers."""
    protocol = _load_protocol_boundary(protocol_path)
    value, digest = load_json(path, GATE_SCHEMA)
    validate_reduced_gate(value, protocol)
    _, freeze_binding = load_implementation_freeze(
        value["bindings"]["implementation_freeze"]["path"], protocol)
    if freeze_binding != value["bindings"]["implementation_freeze"]:
        raise V2BError("reduced gate implementation-freeze bytes drift")
    return value, digest


def prepare_fragment(shard_index, shard_count, implementation_freeze_path,
                     protocol_path=PROTOCOL_PATH):
    if not source_clean():
        raise V2BError("source tree is dirty before confirmation census")
    commit, tree = head_commit(), source_tree_hash()
    protocol = _load_protocol_boundary(protocol_path)
    _, freeze_binding = load_implementation_freeze(
        implementation_freeze_path, protocol)
    chain = load_source_chain(protocol)
    ledger_paths = [("input:protocol", protocol_path),
                    ("input:implementation_freeze",
                     implementation_freeze_path),
                    *chain["ledger_paths"]]
    pre = capture_ledger(ledger_paths)
    mass_index = ContextMassIndex(
        chain["units"], chain["edges"], chain["adjacency"],
        source_labels=chain["source_labels"])
    value = build_fragment_value(
        protocol, dict(chain["bindings"],
                       implementation_freeze=freeze_binding),
        chain["candidate_identities"],
        mass_index, shard_index, shard_count,
        ledger_record(pre, capture_ledger(ledger_paths)),
        generator_record(commit, tree))
    if corpus_git_identity(
            chain["corpus_root"], protocol["scope"]["corpus_git_sha"]) != \
            chain["corpus_identity"]:
        raise V2BError("corpus git identity drifted during confirmation census")
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source tree drifted during confirmation census")
    return _validate_fragment(value, protocol)


def reduce_fragment_files(fragment_paths, implementation_freeze_path,
                          protocol_path=PROTOCOL_PATH):
    if not source_clean():
        raise V2BError("source tree is dirty before confirmation reduction")
    commit, tree = head_commit(), source_tree_hash()
    protocol = _load_protocol_boundary(protocol_path)
    _, freeze_binding = load_implementation_freeze(
        implementation_freeze_path, protocol)
    candidates, candidates_path, _ = load_bound_json(
        protocol["inputs"]["candidates"], "candidates", CANDIDATES_SCHEMA)
    pilot, pilot_path, _ = load_bound_json(
        protocol["inputs"]["pilot_sample"], "pilot_sample",
        BOUND_SAMPLE_SCHEMA)
    inputs = []
    ledger_paths = [("input:protocol", protocol_path),
                    ("input:implementation_freeze",
                     implementation_freeze_path),
                    ("input:candidates", candidates_path),
                    ("input:pilot_sample", pilot_path)]
    seen_paths = set()
    loaded = []
    for path in fragment_paths:
        absolute = os.path.abspath(path)
        if absolute in seen_paths:
            raise V2BError("duplicate fragment path supplied to reducer")
        seen_paths.add(absolute)
        value, digest = load_json(absolute, FRAGMENT_SCHEMA)
        loaded.append(dict(path=absolute, sha256=digest, value=value))
    loaded.sort(key=lambda record: (
        record["value"].get("shard_index")
        if isinstance(record["value"].get("shard_index"), int) else -1,
        record["path"]))
    for index, record in enumerate(loaded):
        inputs.append(record)
        ledger_paths.append((f"fragment:{index:06d}", record["path"]))
    pre = capture_ledger(ledger_paths)
    value = reduce_gate_values(
        protocol, candidates, pilot, inputs, freeze_binding,
        ledger_record(pre, capture_ledger(ledger_paths)),
        generator_record(commit, tree))
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source tree drifted during confirmation reduction")
    return validate_reduced_gate(value, protocol)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fragment = sub.add_parser("fragment")
    fragment.add_argument("--shard-index", type=int, required=True)
    fragment.add_argument("--shard-count", type=int, required=True)
    fragment.add_argument("--out", required=True)
    fragment.add_argument("--implementation-freeze", required=True)
    reduce = sub.add_parser("reduce")
    reduce.add_argument("--fragments", nargs="+", required=True)
    reduce.add_argument("--out", required=True)
    reduce.add_argument("--implementation-freeze", required=True)
    args = parser.parse_args(argv)
    if args.command == "fragment":
        value = prepare_fragment(args.shard_index, args.shard_count,
                                 args.implementation_freeze)
    else:
        value = reduce_fragment_files(args.fragments,
                                      args.implementation_freeze)
    digest = write_new_json(args.out, value)
    print(f"wrote {args.out} sha256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (V2BError, OSError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        raise SystemExit(2)
