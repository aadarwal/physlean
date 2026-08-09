#!/usr/bin/env python3
"""Build and materialize the fresh-SymPy confirmation's six cells.

This is deliberately a new production boundary, not a parameterization of
``prepare_v2b_assembly``.  It accepts only the prospectively frozen
confirmation protocol/sample and emits exactly

    k1, k3:16384, k4:16384, k5:0:16384, k5:1:16384, k5:2:16384.

The model-independent eligibility ledger is complete before scoring.  The
three cells required by the fixed-N execution contract (k1, k4, and k5 seed
0) must be eligible for every target.  A structurally ineligible diagnostic
cell remains an explicit six-cell record, but its materialized context and
all context-valued fields are ``None``; a short rendering is never presented
to the scorer as a numeric zero or an alternate-budget observation.

Only source/rendering primitives are reused.  No legacy assembly production
entry point, BM25 arm, 23-cell enumerator, tokenizer, model, or NLL code is
called here.
"""
import argparse
import heapq
import os
import sys
from collections import deque

from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_assemble import (_components, interface_payload, k5_unit_order,
                          make_chunk, render_chunks, utf8_budget_suffix)
from v2b_common import (SAMPLE_SCHEMA, V2BError, artifact_binding,
                        identity_key, seeded_hash, sha256_bytes,
                        sha256_file, sha256_json, sha256_sorted_json,
                        validate_identity, write_new_json)
from v2b_nll_confirmation import (PROTOCOL_PATH, PROTOCOL_RAW_SHA256,
                                  PROTOCOL_SCHEMA,
                                  PROTOCOL_SEMANTIC_SHA256, SCORED_CELLS,
                                  load_protocol, validate_protocol)
from v2b_nll_confirmation_context import (ContextMassIndex, LANGUAGE, REPO,
                                          load_source_chain)


ASSEMBLY_SCHEMA = "v2b_nll_e2_confirmation_assembly_v1"
ASSEMBLY_STATE = "complete-model-independent-pre-score-six-cell-assembly"
SAMPLE_SCHEMA_CONFIRMATION = "v2b_nll_e2_confirmation_sample_v1"
SAMPLE_STATE = "drawn-source-gated-module-disjoint-pre-score"
SOURCE_GATE_SCHEMA = "v2b_nll_e2_confirmation_source_gate_v1"
FREEZE_SCHEMA = "v2b_nll_e2_confirmation_implementation_freeze_v1"
PROGRAM = "prepare_v2b_nll_confirmation_assembly.py"
CONTEXT_PROGRAM = "v2b_nll_confirmation_context.py"
RENDERER_PROGRAM = "v2b_assemble.py"
BUDGET_BYTES = 16384
N_TARGETS = 200

CELL_ORDER = tuple(SCORED_CELLS)
REQUIRED_CELLS = ("k1", "k4:16384", "k5:0:16384")
DIAGNOSTIC_CELLS = ("k3:16384", "k5:1:16384", "k5:2:16384")

TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "budget_bytes", "protocol", "bindings",
    "source_bindings",
    "input_ledger", "cell_order", "required_cells", "diagnostic_cells",
    "n_targets", "ordered_target_keys", "targets", "targets_sha256",
    "generator",
}
TARGET_KEYS = {
    "key", "identity", "module", "source_rel", "sample_cell",
    "sample_priority", "prefix_bytes", "prefix_sha256", "body_bytes",
    "body_sha256", "cells", "cells_sha256",
}
CELL_KEYS = {
    "cell_id", "role", "required_for_fixed_n", "budget_bytes",
    "eligible", "eligibility_basis", "ineligibility_reason",
    "rendering_bytes", "context_bytes", "context_sha256",
    "utf8_shortfall_bytes", "n_ordered_units",
    "ordered_unit_keys_sha256", "unit_pool_keys_sha256",
}
SAMPLE_TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "budget_bytes", "requested_n", "realized_n",
    "protocol", "bindings", "exclusion_bindings", "plan",
    "selected_keys", "selected_modules", "cluster_support",
    "input_ledger", "generator",
}
PLAN_KEYS = {
    "schema", "repo", "language", "n_requested", "n_excluded",
    "excluded_keys_sha256", "n_selected", "quota_table",
    "cell_populations", "cell_fills", "shortfalls", "unsampled_cells",
    "targets",
}
CLUSTER_KEYS = {
    "n_targets", "n_modules", "module_counts", "module_counts_sha256",
    "effective_clusters", "effective_clusters_numerator",
    "effective_clusters_denominator", "minimum_modules",
    "minimum_effective_clusters", "passed",
}
LEDGER_KEYS = {
    "algorithm", "n_entries", "entries", "entries_sha256",
    "pre_entries_sha256", "post_entries_sha256", "unchanged",
}
GENERATOR_KEYS = {
    "program", "program_sha256", "context_program",
    "context_program_sha256", "renderer_program",
    "renderer_program_sha256", "source_commit", "source_tree_hash",
}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _path_matches(left, right):
    if not isinstance(left, str) or not left \
            or not isinstance(right, str) or not right:
        return False
    a, b = os.path.normpath(left), os.path.normpath(right)
    return a == b or a.endswith(os.sep + b) or b.endswith(os.sep + a)


def _artifact_row(value, schema, label):
    _exact_keys(value, {"path", "schema", "sha256"}, label)
    if value["schema"] != schema or not _hex(value["sha256"]) \
            or not isinstance(value["path"], str) or not value["path"]:
        raise V2BError(f"malformed {label}")
    return value


def _same_artifact(left, right, schema, label):
    _artifact_row(left, schema, label)
    _artifact_row(right, schema, label)
    if left["schema"] != right["schema"] \
            or left["sha256"] != right["sha256"] \
            or not _path_matches(left["path"], right["path"]):
        raise V2BError(f"{label} binding drift")


def protocol_record(path=PROTOCOL_PATH):
    real_base, real = os.path.realpath(BASE), os.path.realpath(path)
    try:
        if os.path.commonpath((real_base, real)) != real_base:
            raise V2BError("confirmation protocol lies outside checkout")
    except ValueError as err:
        raise V2BError(f"confirmation protocol path mismatch: {err}") \
            from err
    relative = os.path.relpath(real, real_base).replace(os.sep, "/")
    return dict(path=relative, schema=PROTOCOL_SCHEMA,
                raw_sha256=PROTOCOL_RAW_SHA256,
                semantic_sha256=PROTOCOL_SEMANTIC_SHA256)


def _validate_protocol_row(value, label="protocol"):
    _exact_keys(value,
                {"path", "schema", "raw_sha256", "semantic_sha256"},
                label)
    if value != protocol_record():
        raise V2BError(f"{label} binding drift")
    return value


def _key_set(keys):
    ordered = sorted(keys)
    if len(ordered) != len(set(ordered)) \
            or any(not isinstance(key, str) or not key for key in ordered):
        raise V2BError("malformed key set")
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


def _validate_input_ledger(value, label="input_ledger"):
    _exact_keys(value, LEDGER_KEYS, label)
    entries = value["entries"]
    if value["algorithm"] != "sha256-sorted-json-file-ledger-v1" \
            or value["unchanged"] is not True \
            or not isinstance(entries, list) \
            or value["n_entries"] != len(entries):
        raise V2BError(f"{label} header drift")
    labels = []
    for index, row in enumerate(entries):
        _exact_keys(row, {"label", "bytes", "sha256"},
                    f"{label}.entries[{index}]")
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
        raise V2BError(f"{label} hash/equality drift")
    return value


def _validate_generator(value):
    _exact_keys(value, GENERATOR_KEYS, "assembly generator")
    if value["program"] != PROGRAM \
            or value["context_program"] != CONTEXT_PROGRAM \
            or value["renderer_program"] != RENDERER_PROGRAM \
            or any(not _hex(value[name]) for name in (
                "program_sha256", "context_program_sha256",
                "renderer_program_sha256", "source_tree_hash")) \
            or not _hex(value["source_commit"], 40):
        raise V2BError("assembly generator drift")
    return value


def _validate_freeze(value, binding, protocol):
    _artifact_row(binding, FREEZE_SCHEMA, "implementation freeze")
    if not isinstance(value, dict) or value.get("schema") != FREEZE_SCHEMA \
            or value.get("study_id") != protocol["study_id"]:
        raise V2BError("implementation freeze identity drift")
    _validate_protocol_row(value.get("protocol"),
                           "implementation freeze protocol")
    return value


def _validate_sample_ledger(value):
    # The sampler uses the same content contract but names its algorithm
    # explicitly.  Keeping this validator local prevents the assembler from
    # importing or calling the sampler production entry point.
    _exact_keys(value, LEDGER_KEYS, "sample input_ledger")
    entries = value["entries"]
    if value["algorithm"] != "sha256-sorted-json-file-ledger-v1" \
            or value["unchanged"] is not True \
            or not isinstance(entries, list) \
            or value["n_entries"] != len(entries):
        raise V2BError("sample input ledger header drift")
    labels = []
    for row in entries:
        _exact_keys(row, {"label", "bytes", "sha256"},
                    "sample input ledger entry")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError("malformed sample input ledger entry")
        labels.append(row["label"])
    digest = sha256_sorted_json(entries)
    if labels != sorted(labels) or len(labels) != len(set(labels)) \
            or any(value[name] != digest for name in (
                "entries_sha256", "pre_entries_sha256",
                "post_entries_sha256")):
        raise V2BError("sample input ledger hash/equality drift")


def _validate_sample(sample, protocol, sample_binding, source_gate_binding,
                     freeze_binding):
    _artifact_row(sample_binding, SAMPLE_SCHEMA_CONFIRMATION,
                  "confirmation sample")
    _exact_keys(sample, SAMPLE_TOP_KEYS, "confirmation sample")
    if sample["schema"] != SAMPLE_SCHEMA_CONFIRMATION \
            or sample["state"] != SAMPLE_STATE \
            or sample["study_id"] != protocol["study_id"] \
            or sample["repo"] != REPO or sample["language"] != LANGUAGE \
            or sample["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or sample["budget_bytes"] != BUDGET_BYTES \
            or sample["requested_n"] != N_TARGETS \
            or sample["realized_n"] != N_TARGETS:
        raise V2BError("confirmation sample identity/count drift")
    _validate_protocol_row(sample["protocol"], "sample protocol")
    bindings = sample["bindings"]
    _exact_keys(bindings,
                {"source_gate", "candidates", "pilot_sample",
                 "implementation_freeze"}, "sample bindings")
    _same_artifact(bindings["source_gate"], source_gate_binding,
                   SOURCE_GATE_SCHEMA, "sample source gate")
    _same_artifact(bindings["implementation_freeze"], freeze_binding,
                   FREEZE_SCHEMA, "sample implementation freeze")
    _artifact_row(bindings["candidates"],
                  protocol["inputs"]["candidates"]["schema"],
                  "sample candidates")
    _artifact_row(bindings["pilot_sample"],
                  protocol["inputs"]["pilot_sample"]["schema"],
                  "sample pilot sample")
    if bindings["candidates"]["sha256"] != \
            protocol["inputs"]["candidates"]["sha256"] \
            or bindings["pilot_sample"]["sha256"] != \
            protocol["inputs"]["pilot_sample"]["sha256"]:
        raise V2BError("sample candidate/pilot binding drift")

    exclusion_names = {
        "source_ineligible_keys", "pilot_target_keys", "pilot_modules",
        "pilot_module_candidate_keys", "union_excluded_keys",
        "post_pilot_eligible_keys",
    }
    _exact_keys(sample["exclusion_bindings"], exclusion_names,
                "sample exclusion bindings")
    exclusions = {
        name: set(_validate_key_set(sample["exclusion_bindings"][name],
                                    f"sample exclusion {name}"))
        for name in exclusion_names}

    plan = sample["plan"]
    _exact_keys(plan, PLAN_KEYS, "confirmation sample plan")
    targets = plan["targets"]
    if plan["schema"] != SAMPLE_SCHEMA or plan["repo"] != REPO \
            or plan["language"] != LANGUAGE \
            or plan["n_requested"] != N_TARGETS \
            or plan["n_selected"] != N_TARGETS \
            or not isinstance(targets, list) or len(targets) != N_TARGETS \
            or not isinstance(plan["n_excluded"], int) \
            or isinstance(plan["n_excluded"], bool) \
            or plan["n_excluded"] < 0 or not _hex(
                plan["excluded_keys_sha256"]):
        raise V2BError("confirmation sample plan header drift")
    keys, modules = [], []
    for index, row in enumerate(targets):
        _exact_keys(row, {"identity", "cell", "priority"},
                    f"sample target[{index}]")
        identity = validate_identity(LANGUAGE, row["identity"])
        if not isinstance(row["cell"], str) or not row["cell"] \
                or not _hex(row["priority"]):
            raise V2BError(f"malformed sample target[{index}]")
        keys.append(identity_key(LANGUAGE, identity))
        modules.append(identity[0])
    if len(keys) != len(set(keys)):
        raise V2BError("confirmation sample repeats a target")
    selected = _validate_key_set(sample["selected_keys"],
                                 "sample selected_keys")
    selected_modules = _validate_key_set(sample["selected_modules"],
                                         "sample selected_modules")
    if set(keys) != set(selected) or set(modules) != set(selected_modules) \
            or not set(keys) <= exclusions["post_pilot_eligible_keys"] \
            or set(keys) & exclusions["union_excluded_keys"]:
        raise V2BError("sample selected/exclusion relation drift")

    cluster = sample["cluster_support"]
    _exact_keys(cluster, CLUSTER_KEYS, "sample cluster support")
    counts = {}
    for module in modules:
        counts[module] = counts.get(module, 0) + 1
    rows = [[module, counts[module]] for module in sorted(counts)]
    denominator = sum(count * count for count in counts.values())
    effective = N_TARGETS * N_TARGETS / denominator
    if cluster["n_targets"] != N_TARGETS \
            or cluster["n_modules"] != len(counts) \
            or cluster["module_counts"] != rows \
            or cluster["module_counts_sha256"] != sha256_json(rows) \
            or cluster["effective_clusters"] != effective \
            or cluster["effective_clusters_numerator"] != N_TARGETS ** 2 \
            or cluster["effective_clusters_denominator"] != denominator \
            or cluster["minimum_modules"] != 20 \
            or cluster["minimum_effective_clusters"] != 10 \
            or cluster["passed"] is not True \
            or len(counts) < 20 or effective < 10:
        raise V2BError("sample pre-score cluster support drift")
    _validate_sample_ledger(sample["input_ledger"])
    generator = sample["generator"]
    _exact_keys(generator,
                {"program", "program_sha256", "source_commit",
                 "source_tree_hash"}, "sample generator")
    if not isinstance(generator["program"], str) or not generator["program"] \
            or not _hex(generator["program_sha256"]) \
            or not _hex(generator["source_commit"], 40) \
            or not _hex(generator["source_tree_hash"]):
        raise V2BError("sample generator drift")
    return targets


class _OrderIndex:
    """One SCC condensation reused across all 200 canonical k4 orders."""

    def __init__(self, units, edges):
        identities = {}
        for key, unit in units.items():
            identity = validate_identity(LANGUAGE, unit.get("identity"))
            if identity_key(LANGUAGE, identity) != key:
                raise V2BError(f"unit key/identity drift: {key}")
            identities[key] = identity
        normalized = set()
        for edge in edges:
            if not isinstance(edge, (list, tuple)) or len(edge) != 2:
                raise V2BError("malformed assembly graph edge")
            dependent = validate_identity(LANGUAGE, edge[0])
            dependency = validate_identity(LANGUAGE, edge[1])
            a = identity_key(LANGUAGE, dependent)
            b = identity_key(LANGUAGE, dependency)
            if a not in units or b not in units:
                raise V2BError("assembly graph endpoint outside universe")
            if a != b:
                normalized.add((dependent, dependency))
        components, component_of_identity = _components(
            set(identities.values()), normalized)
        self.components = components
        self.component_of = {
            key: component_of_identity[identity]
            for key, identity in identities.items()}
        self.direct = [set() for _ in components]
        self.dependents = [set() for _ in components]
        for dependent, dependency in normalized:
            a = component_of_identity[dependent]
            b = component_of_identity[dependency]
            if a != b:
                self.direct[a].add(b)
                self.dependents[b].add(a)

    def k4_order(self, target_identity):
        target = validate_identity(LANGUAGE, target_identity)
        target_key = identity_key(LANGUAGE, target)
        if target_key not in self.component_of:
            raise V2BError("sample target absent from assembly graph")
        root = self.component_of[target_key]
        distances = {root: 0}
        queue = deque([root])
        while queue:
            cid = queue.popleft()
            for dependency in sorted(self.direct[cid]):
                if dependency not in distances:
                    distances[dependency] = distances[cid] + 1
                    queue.append(dependency)
        closure = set(distances) - {root}
        indegree = {cid: 0 for cid in closure}
        downstream = {cid: set() for cid in closure}
        for dependent in closure:
            for dependency in self.direct[dependent]:
                if dependency in closure:
                    downstream[dependency].add(dependent)
                    indegree[dependent] += 1

        def ready_key(cid):
            unit_identity = self.components[cid][0]
            tie = seeded_hash("k4sel:v2b:20260808", REPO, *target,
                              *unit_identity)
            return -distances[cid], tie, cid

        ready = [ready_key(cid) for cid in closure
                 if indegree[cid] == 0]
        heapq.heapify(ready)
        component_order = []
        while ready:
            _, _, cid = heapq.heappop(ready)
            component_order.append(cid)
            for dependent in sorted(downstream[cid]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, ready_key(dependent))
        if len(component_order) != len(closure):
            raise AssertionError("assembly SCC condensation is cyclic")
        return [identity_key(LANGUAGE, identity)
                for cid in component_order
                for identity in self.components[cid]]


def _payload(unit, cache):
    source = unit.get("source")
    if not isinstance(source, str) or not source:
        raise V2BError("assembly unit lacks a source path")
    if source not in cache:
        try:
            with open(source, "rb") as handle:
                blob = handle.read()
        except OSError as err:
            raise V2BError(f"cannot read assembly source {source}: {err}") \
                from err
        if sha256_bytes(blob) != unit.get("source_sha256"):
            raise V2BError(f"assembly source hash drift: {source}")
        cache[source] = blob
    blob = cache[source]
    start, end = unit.get("start"), unit.get("end")
    if not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) \
            or not 0 <= start < end <= len(blob):
        raise V2BError(f"invalid assembly unit span: {unit.get('key')}")
    return blob[start:end]


def _target_blobs(unit, cache, candidate):
    payload = _payload(unit, cache)
    header = unit.get("header_bytes")
    if not isinstance(header, int) or isinstance(header, bool) \
            or not 0 < header < len(payload):
        raise V2BError(f"target lacks a header/body split: {unit['key']}")
    prefix, body = payload[:header], payload[header:]
    if prefix + body != payload \
            or candidate.get("source_rel") != unit.get("source_rel") \
            or candidate.get("body_bytes") != len(body):
        raise V2BError(f"sample/candidate/extraction target drift: "
                       f"{unit['key']}")
    return prefix, body


def _interface(unit, cache):
    payload = _payload(unit, cache)
    return interface_payload(LANGUAGE, payload, unit.get("header_bytes"))


def _ordered_unit_rows(ordered_keys, units, cache, interface=False):
    rows = []
    weights = {}
    for key in ordered_keys:
        unit = units[key]
        payload = _interface(unit, cache) if interface else \
            _payload(unit, cache)
        row = dict(identity=unit["identity"], relpath=unit["source_rel"],
                   payload=payload)
        chunk, _ = make_chunk(LANGUAGE, row["relpath"], payload)
        rows.append(row)
        weights[key] = len(chunk) + 1
    return rows, weights


def _minimal_tail_start(ordered_keys, weights, budget):
    total = 0
    start = len(ordered_keys)
    while start > 0 and total < budget:
        start -= 1
        total += weights[ordered_keys[start]]
    return start


def _context_cell(cell_id, ordered_keys, pool_keys, units, cache,
                  required, interface=False, known_rendering_bytes=None,
                  known_weights=None):
    rows = None
    if known_weights is None:
        rows, weights = _ordered_unit_rows(ordered_keys, units, cache,
                                           interface=interface)
    else:
        if interface:
            raise AssertionError("verbatim weights cannot render interfaces")
        try:
            weights = {key: known_weights[key] for key in ordered_keys}
        except KeyError as err:
            raise V2BError(f"{cell_id} lacks a frozen unit weight") from err
    rendering_bytes = sum(weights[key] for key in ordered_keys)
    if known_rendering_bytes is not None \
            and rendering_bytes != known_rendering_bytes:
        raise V2BError(f"{cell_id} renderer/source-gate byte mass drift")
    eligible = rendering_bytes >= BUDGET_BYTES
    context = None
    context_bytes = None
    context_sha = None
    utf8_shortfall = None
    if eligible:
        start = _minimal_tail_start(ordered_keys, weights, BUDGET_BYTES)
        if rows is None:
            tail, observed_weights = _ordered_unit_rows(
                ordered_keys[start:], units, cache, interface=False)
            if any(observed_weights[key] != weights[key]
                   for key in ordered_keys[start:]):
                raise V2BError(f"{cell_id} frozen/live unit-weight drift")
        else:
            tail = rows[start:]
        tail_rendering, spans = render_chunks(LANGUAGE, tail)
        suffix = utf8_budget_suffix(tail_rendering, spans, BUDGET_BYTES)
        if suffix["eligible"] is not True:
            raise AssertionError("minimal eligible tail is not eligible")
        context = suffix["context"]
        context_bytes = suffix["context_bytes"]
        context_sha = sha256_bytes(context)
        utf8_shortfall = suffix["utf8_shortfall_bytes"]
    if required and not eligible:
        raise V2BError(f"required confirmation cell is ineligible: {cell_id}")
    role = "required-primary" if required else "diagnostic"
    return dict(
        cell_id=cell_id, role=role, required_for_fixed_n=required,
        budget_bytes=BUDGET_BYTES, eligible=eligible,
        eligibility_basis="maximal-rendering-bytes-at-least-16384",
        ineligibility_reason=None if eligible else
        "maximal-rendering-below-16384-bytes",
        rendering_bytes=rendering_bytes,
        context_bytes=context_bytes, context_sha256=context_sha,
        utf8_shortfall_bytes=utf8_shortfall,
        n_ordered_units=len(ordered_keys),
        ordered_unit_keys_sha256=sha256_json(ordered_keys),
        unit_pool_keys_sha256=sha256_json(sorted(pool_keys))), context


def _k1_cell():
    empty_hash = sha256_bytes(b"")
    return dict(
        cell_id="k1", role="intrinsic-control",
        required_for_fixed_n=True, budget_bytes=None, eligible=True,
        eligibility_basis="intrinsic-empty-context",
        ineligibility_reason=None, rendering_bytes=0, context_bytes=0,
        context_sha256=empty_hash, utf8_shortfall_bytes=None,
        n_ordered_units=0, ordered_unit_keys_sha256=sha256_json([]),
        unit_pool_keys_sha256=sha256_json([]))


def _candidate_index(chain):
    rows = chain.get("candidates", {}).get("targets")
    if not isinstance(rows, list):
        raise V2BError("source chain lacks candidate rows")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise V2BError("candidate row is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if key in out:
            raise V2BError("duplicate candidate identity")
        out[key] = row
    return out


def _gate_index(source_gate, protocol):
    if not isinstance(source_gate, dict) \
            or source_gate.get("schema") != SOURCE_GATE_SCHEMA \
            or source_gate.get("study_id") != protocol["study_id"] \
            or source_gate.get("repo") != REPO \
            or source_gate.get("language") != LANGUAGE \
            or source_gate.get("budget_bytes") != BUDGET_BYTES:
        raise V2BError("source gate identity drift at assembly")
    rows = source_gate.get("rows")
    if not isinstance(rows, list) \
            or source_gate.get("n_rows") != len(rows):
        raise V2BError("source gate row table/count drift at assembly")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise V2BError("source gate row is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if row.get("key") != key or key in out:
            raise V2BError("source gate key/identity drift at assembly")
        out[key] = row
    return out


def _generator_record(commit, tree):
    return dict(
        program=PROGRAM,
        program_sha256=sha256_file(os.path.join(BASE, PROGRAM)),
        context_program=CONTEXT_PROGRAM,
        context_program_sha256=sha256_file(
            os.path.join(BASE, CONTEXT_PROGRAM)),
        renderer_program=RENDERER_PROGRAM,
        renderer_program_sha256=sha256_file(
            os.path.join(BASE, RENDERER_PROGRAM)),
        source_commit=commit, source_tree_hash=tree)


def capture_ledger(label_paths):
    rows = []
    seen = set()
    for label, path in label_paths:
        if not isinstance(label, str) or not label or label in seen:
            raise V2BError(f"duplicate/malformed ledger label {label!r}")
        seen.add(label)
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat assembly input {label}: {err}") \
                from err
        rows.append(dict(label=label, bytes=size, sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    return rows


def ledger_record(pre, post):
    if pre != post:
        raise V2BError("assembly input bytes drifted during materialization")
    digest = sha256_sorted_json(pre)
    return dict(algorithm="sha256-sorted-json-file-ledger-v1",
                n_entries=len(pre), entries=pre, entries_sha256=digest,
                pre_entries_sha256=digest, post_entries_sha256=digest,
                unchanged=True)


def verify_live_materialization_ledger(manifest, pre, post):
    """Require independently observed live bytes to equal the sealed ledger."""
    if not isinstance(manifest, dict) \
            or ledger_record(pre, post) != manifest.get("input_ledger"):
        raise V2BError("live assembly materialization ledger differs from "
                       "the sealed manifest")


def build_assembly_value(protocol, protocol_binding, sample, sample_binding,
                         implementation_freeze_binding, source_gate,
                         source_gate_binding, chain, input_ledger, generator,
                         collect=None):
    """Pure six-cell builder over already loaded, hash-bound values.

    ``collect`` receives exact bytes in the evaluator handoff shape:
    ``{target_key: {prefix, body, cells:{cell_id: bytes|None}}}``.
    """
    validate_protocol(protocol)
    _validate_protocol_row(protocol_binding)
    _artifact_row(source_gate_binding, SOURCE_GATE_SCHEMA, "source gate")
    _artifact_row(implementation_freeze_binding, FREEZE_SCHEMA,
                  "implementation freeze")
    plan_targets = _validate_sample(
        sample, protocol, sample_binding, source_gate_binding,
        implementation_freeze_binding)
    _validate_input_ledger(input_ledger)
    _validate_generator(generator)
    if chain.get("bindings") != \
            protocol["source_eligibility_gate"]["bindings"]:
        raise V2BError("assembly source-chain binding drift")
    units = chain.get("units")
    edges = chain.get("edges")
    adjacency = chain.get("adjacency")
    if not isinstance(units, dict) or not units \
            or not isinstance(edges, list) or not isinstance(adjacency, dict):
        raise V2BError("assembly source chain is incomplete")
    candidates = _candidate_index(chain)
    gate_rows = _gate_index(source_gate, protocol)
    cache = {}
    mass_index = ContextMassIndex(units, edges, adjacency,
                                  source_cache=cache)
    order_index = _OrderIndex(units, edges)
    verbatim_weights = {
        key: mass_index.weights[index]
        for index, key in enumerate(mass_index.keys)}

    targets = []
    if collect is not None:
        collect.clear()
    for sampled in plan_targets:
        identity = validate_identity(LANGUAGE, sampled["identity"])
        key = identity_key(LANGUAGE, identity)
        if key not in units or key not in candidates or key not in gate_rows:
            raise V2BError(f"sampled target missing from source chain: {key}")
        candidate = candidates[key]
        if sampled["cell"] != candidate.get("cell") \
                or sampled["priority"] != candidate.get("priority"):
            raise V2BError(f"sample/candidate strata-priority drift: {key}")
        gate = gate_rows[key]
        if gate.get("eligible") is not True \
                or gate.get("k4_eligible") is not True \
                or gate.get("k5_seed0_eligible") is not True:
            raise V2BError(f"sample contains source-ineligible target: {key}")
        prefix, body = _target_blobs(units[key], cache, candidate)

        k4_bits, k5_bits = mass_index.selected_bits(key)
        k4_pool = mass_index.keys_from_bits(k4_bits)
        k5_pool = mass_index.keys_from_bits(k5_bits)
        canonical = order_index.k4_order(identity)
        k4_set = set(k4_pool)
        k4_order = [unit_key for unit_key in canonical
                    if unit_key in k4_set]
        if len(k4_order) != len(k4_set) or set(k4_order) != k4_set:
            raise V2BError(f"k4 canonical order/set drift: {key}")
        k4_mass = sum(verbatim_weights[unit_key] for unit_key in k4_order)
        k5_mass = sum(verbatim_weights[unit_key] for unit_key in k5_pool)
        if gate.get("k4_rendering_bytes") != k4_mass \
                or gate.get("k5_seed0_rendering_bytes") != k5_mass:
            raise V2BError(f"source-gate/full-render mass mismatch: {key}")

        cells = [_k1_cell()]
        contexts = {"k1": b""}
        k3, k3_context = _context_cell(
            "k3:16384", k4_order, k4_pool, units, cache,
            required=False, interface=True)
        cells.append(k3)
        contexts["k3:16384"] = k3_context
        k4, k4_context = _context_cell(
            "k4:16384", k4_order, k4_pool, units, cache,
            required=True, known_rendering_bytes=k4_mass,
            known_weights=verbatim_weights)
        cells.append(k4)
        contexts["k4:16384"] = k4_context

        k5_records = []
        for seed in (0, 1, 2):
            ordered = [identity_key(LANGUAGE, row["identity"])
                       for row in k5_unit_order(
                           LANGUAGE, REPO, identity,
                           [units[pool_key]["identity"]
                            for pool_key in k5_pool], seed)]
            cell_id = f"k5:{seed}:16384"
            cell, context = _context_cell(
                cell_id, ordered, k5_pool, units, cache,
                required=seed == 0, known_rendering_bytes=k5_mass,
                known_weights=verbatim_weights)
            k5_records.append(cell)
            cells.append(cell)
            contexts[cell_id] = context
        first = k5_records[0]
        if any(row["n_ordered_units"] != first["n_ordered_units"]
               or row["unit_pool_keys_sha256"] !=
               first["unit_pool_keys_sha256"]
               or row["rendering_bytes"] != first["rendering_bytes"]
               or row["eligible"] is not first["eligible"]
               for row in k5_records[1:]):
            raise V2BError(f"k5 seed pool/rendering-length drift: {key}")
        if tuple(row["cell_id"] for row in cells) != CELL_ORDER:
            raise AssertionError("confirmation cell enumerator drift")
        target = dict(
            key=key, identity=list(identity), module=identity[0],
            source_rel=units[key]["source_rel"],
            sample_cell=sampled["cell"],
            sample_priority=sampled["priority"],
            prefix_bytes=len(prefix), prefix_sha256=sha256_bytes(prefix),
            body_bytes=len(body), body_sha256=sha256_bytes(body),
            cells=cells, cells_sha256=sha256_sorted_json(cells))
        targets.append(target)
        if collect is not None:
            collect[key] = dict(prefix=prefix, body=body, cells=contexts)

    ordered_keys = [row["key"] for row in targets]
    value = dict(
        schema=ASSEMBLY_SCHEMA, state=ASSEMBLY_STATE,
        study_id=protocol["study_id"], repo=REPO, language=LANGUAGE,
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=BUDGET_BYTES, protocol=dict(protocol_binding),
        bindings=dict(
            implementation_freeze=dict(implementation_freeze_binding),
            bound_sample=dict(sample_binding),
            source_gate=dict(source_gate_binding)),
        source_bindings=chain["bindings"], input_ledger=input_ledger,
        cell_order=list(CELL_ORDER), required_cells=list(REQUIRED_CELLS),
        diagnostic_cells=list(DIAGNOSTIC_CELLS), n_targets=len(targets),
        ordered_target_keys=dict(n=len(ordered_keys),
                                 sha256=sha256_json(ordered_keys),
                                 keys=ordered_keys),
        targets=targets, targets_sha256=sha256_sorted_json(targets),
        generator=generator)
    return validate_assembly(value, protocol, sample)


def _validate_cell(cell, expected_id):
    _exact_keys(cell, CELL_KEYS, f"assembly cell {expected_id}")
    if cell["cell_id"] != expected_id:
        raise V2BError("assembly cell order/id drift")
    if expected_id == "k1":
        expected = _k1_cell()
        if cell != expected:
            raise V2BError("k1 intrinsic-empty invariant drift")
        return
    required = expected_id in REQUIRED_CELLS
    if cell["role"] != ("required-primary" if required else "diagnostic") \
            or cell["required_for_fixed_n"] is not required \
            or cell["budget_bytes"] != BUDGET_BYTES \
            or cell["eligibility_basis"] != \
            "maximal-rendering-bytes-at-least-16384" \
            or not isinstance(cell["rendering_bytes"], int) \
            or isinstance(cell["rendering_bytes"], bool) \
            or cell["rendering_bytes"] < 0 \
            or not isinstance(cell["n_ordered_units"], int) \
            or isinstance(cell["n_ordered_units"], bool) \
            or cell["n_ordered_units"] < 0 \
            or not _hex(cell["ordered_unit_keys_sha256"]) \
            or not _hex(cell["unit_pool_keys_sha256"]):
        raise V2BError(f"assembly cell metadata drift: {expected_id}")
    eligible = cell["rendering_bytes"] >= BUDGET_BYTES
    if cell["eligible"] is not eligible:
        raise V2BError(f"assembly cell eligibility drift: {expected_id}")
    if eligible:
        if cell["ineligibility_reason"] is not None \
                or not isinstance(cell["context_bytes"], int) \
                or isinstance(cell["context_bytes"], bool) \
                or not BUDGET_BYTES - 3 <= cell["context_bytes"] <= \
                BUDGET_BYTES \
                or not _hex(cell["context_sha256"]) \
                or cell["utf8_shortfall_bytes"] != \
                BUDGET_BYTES - cell["context_bytes"]:
            raise V2BError(f"eligible cell context drift: {expected_id}")
    else:
        if required:
            raise V2BError(f"required assembly cell is ineligible: "
                           f"{expected_id}")
        if cell["ineligibility_reason"] != \
                "maximal-rendering-below-16384-bytes" \
                or any(cell[name] is not None for name in (
                    "context_bytes", "context_sha256",
                    "utf8_shortfall_bytes")):
            raise V2BError(f"ineligible diagnostic fabricated content: "
                           f"{expected_id}")


def validate_assembly(value, protocol, sample):
    validate_protocol(protocol)
    _exact_keys(value, TOP_KEYS, "confirmation assembly")
    if value["schema"] != ASSEMBLY_SCHEMA \
            or value["state"] != ASSEMBLY_STATE \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != REPO or value["language"] != LANGUAGE \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["budget_bytes"] != BUDGET_BYTES \
            or value["cell_order"] != list(CELL_ORDER) \
            or value["required_cells"] != list(REQUIRED_CELLS) \
            or value["diagnostic_cells"] != list(DIAGNOSTIC_CELLS) \
            or value["source_bindings"] != \
            protocol["source_eligibility_gate"]["bindings"]:
        raise V2BError("confirmation assembly identity/grid drift")
    _validate_protocol_row(value["protocol"])
    bindings = value["bindings"]
    _exact_keys(bindings,
                {"implementation_freeze", "bound_sample", "source_gate"},
                "assembly bindings")
    _artifact_row(bindings["implementation_freeze"], FREEZE_SCHEMA,
                  "assembly implementation freeze")
    _artifact_row(bindings["bound_sample"], SAMPLE_SCHEMA_CONFIRMATION,
                  "assembly bound sample")
    _artifact_row(bindings["source_gate"], SOURCE_GATE_SCHEMA,
                  "assembly source gate")
    _validate_input_ledger(value["input_ledger"])
    _validate_generator(value["generator"])
    targets = value["targets"]
    keys_row = value["ordered_target_keys"]
    _exact_keys(keys_row, {"n", "sha256", "keys"},
                "ordered target keys")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or value["n_targets"] != N_TARGETS \
            or value["targets_sha256"] != sha256_sorted_json(targets) \
            or not isinstance(keys_row["keys"], list) \
            or keys_row["n"] != N_TARGETS \
            or keys_row["sha256"] != sha256_json(keys_row["keys"]) \
            or len(keys_row["keys"]) != len(set(keys_row["keys"])):
        raise V2BError("confirmation assembly target table/hash drift")
    observed_keys = []
    for index, target in enumerate(targets):
        _exact_keys(target, TARGET_KEYS, f"assembly target[{index}]")
        identity = validate_identity(LANGUAGE, target["identity"])
        key = identity_key(LANGUAGE, identity)
        if target["key"] != key or target["module"] != identity[0] \
                or not isinstance(target["source_rel"], str) \
                or not target["source_rel"] \
                or not isinstance(target["sample_cell"], str) \
                or not target["sample_cell"] \
                or not _hex(target["sample_priority"]) \
                or not isinstance(target["prefix_bytes"], int) \
                or isinstance(target["prefix_bytes"], bool) \
                or target["prefix_bytes"] <= 0 \
                or not isinstance(target["body_bytes"], int) \
                or isinstance(target["body_bytes"], bool) \
                or target["body_bytes"] <= 0 \
                or not _hex(target["prefix_sha256"]) \
                or not _hex(target["body_sha256"]):
            raise V2BError(f"assembly target identity/body drift: {key}")
        cells = target["cells"]
        if not isinstance(cells, list) or len(cells) != len(CELL_ORDER) \
                or target["cells_sha256"] != sha256_sorted_json(cells):
            raise V2BError(f"assembly target cell table drift: {key}")
        for cell, expected_id in zip(cells, CELL_ORDER):
            _validate_cell(cell, expected_id)
        by_id = {cell["cell_id"]: cell for cell in cells}
        if len(by_id) != len(CELL_ORDER) \
                or by_id["k3:16384"]["n_ordered_units"] != \
                by_id["k4:16384"]["n_ordered_units"] \
                or by_id["k3:16384"]["ordered_unit_keys_sha256"] != \
                by_id["k4:16384"]["ordered_unit_keys_sha256"] \
                or by_id["k3:16384"]["unit_pool_keys_sha256"] != \
                by_id["k4:16384"]["unit_pool_keys_sha256"]:
            raise V2BError(f"k3/k4 dependency-set drift: {key}")
        k5 = [by_id[f"k5:{seed}:16384"] for seed in (0, 1, 2)]
        if any(row["n_ordered_units"] != k5[0]["n_ordered_units"]
               or row["unit_pool_keys_sha256"] !=
               k5[0]["unit_pool_keys_sha256"]
               or row["rendering_bytes"] != k5[0]["rendering_bytes"]
               or row["eligible"] is not k5[0]["eligible"]
               for row in k5[1:]):
            raise V2BError(f"k5 seed pool/rendering-length drift: {key}")
        observed_keys.append(key)
    if observed_keys != keys_row["keys"]:
        raise V2BError("ordered target-key ledger disagrees with targets")
    plan = sample.get("plan") if isinstance(sample, dict) else None
    plan_targets = plan.get("targets") if isinstance(plan, dict) else None
    if not isinstance(plan_targets, list):
        raise V2BError("bound sample lacks an ordered target plan")
    sample_order = [identity_key(
        LANGUAGE, validate_identity(LANGUAGE, row.get("identity")))
        for row in plan_targets if isinstance(row, dict)]
    if len(sample_order) != len(plan_targets) \
            or observed_keys != sample_order:
        raise V2BError("assembly target order differs from bound sample plan")
    return value


def materialize_from_values(manifest, protocol, sample, sample_binding,
                            implementation_freeze_binding, source_gate,
                            source_gate_binding, chain):
    """Rebuild one manifest and return exact bytes after full equality."""
    validate_assembly(manifest, protocol, sample)
    bindings = manifest["bindings"]
    _same_artifact(sample_binding, bindings["bound_sample"],
                   SAMPLE_SCHEMA_CONFIRMATION, "materializer sample")
    _same_artifact(implementation_freeze_binding,
                   bindings["implementation_freeze"], FREEZE_SCHEMA,
                   "materializer implementation freeze")
    _same_artifact(source_gate_binding, bindings["source_gate"],
                   SOURCE_GATE_SCHEMA, "materializer source gate")
    collect = {}
    rebuilt = build_assembly_value(
        protocol, manifest["protocol"], sample, bindings["bound_sample"],
        bindings["implementation_freeze"], source_gate,
        bindings["source_gate"], chain, manifest["input_ledger"],
        manifest["generator"], collect=collect)
    if rebuilt != manifest:
        raise V2BError("materialized confirmation assembly does not reproduce "
                       "the sealed manifest")
    return collect


def _load_production_inputs(sample_path, implementation_freeze_path,
                            source_gate_path, protocol_path):
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation assembly requires canonical protocol "
                       "path")
    protocol, protocol_digest = load_protocol(protocol_path)
    if protocol_digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation protocol raw digest drift")
    sample_binding, sample = artifact_binding(
        sample_path, SAMPLE_SCHEMA_CONFIRMATION)
    freeze_binding, freeze = artifact_binding(
        implementation_freeze_path, FREEZE_SCHEMA)
    _validate_freeze(freeze, freeze_binding, protocol)
    # Import only the reduced-gate validator.  Its production fragment/draw
    # entry points remain unreachable from this module.
    from prepare_v2b_nll_confirmation_gate import validate_reduced_gate
    gate_binding, gate = artifact_binding(source_gate_path,
                                          SOURCE_GATE_SCHEMA)
    validate_reduced_gate(gate, protocol)
    chain = load_source_chain(protocol)
    return (protocol, protocol_record(protocol_path), sample, sample_binding,
            freeze_binding, gate, gate_binding, chain)


def prepare(sample_path, implementation_freeze_path, source_gate_path,
            protocol_path=PROTOCOL_PATH):
    if not source_clean():
        raise V2BError("source tree is dirty before confirmation assembly")
    for path in (protocol_path, implementation_freeze_path,
                 source_gate_path, sample_path):
        require_committed(path)
    commit, tree = head_commit(), source_tree_hash()
    values = _load_production_inputs(
        sample_path, implementation_freeze_path, source_gate_path,
        protocol_path)
    chain = values[-1]
    ledger_paths = [
        ("input:protocol", protocol_path),
        ("input:implementation_freeze", implementation_freeze_path),
        ("input:source_gate", source_gate_path),
        ("input:sample", sample_path),
        *chain["ledger_paths"],
    ]
    pre = capture_ledger(ledger_paths)
    value = build_assembly_value(
        *values[:-1], chain,
        ledger_record(pre, capture_ledger(ledger_paths)),
        _generator_record(commit, tree))
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source tree drifted during confirmation assembly")
    return value


def materialize(manifest_path, sample_path, implementation_freeze_path,
                source_gate_path, protocol_path=PROTOCOL_PATH):
    """Committed scoring-boundary handoff; never calls legacy assembly."""
    if not source_clean():
        raise V2BError("source tree is dirty before assembly materialization")
    for path in (manifest_path, protocol_path, implementation_freeze_path,
                 source_gate_path, sample_path):
        require_committed(path)
    commit, tree = head_commit(), source_tree_hash()
    _, manifest = artifact_binding(manifest_path, ASSEMBLY_SCHEMA)
    values = _load_production_inputs(
        sample_path, implementation_freeze_path, source_gate_path,
        protocol_path)
    protocol, _, sample, sample_binding, freeze_binding, gate, \
        gate_binding, chain = values
    ledger_paths = [
        ("input:protocol", protocol_path),
        ("input:implementation_freeze", implementation_freeze_path),
        ("input:source_gate", source_gate_path),
        ("input:sample", sample_path),
        *chain["ledger_paths"],
    ]
    pre = capture_ledger(ledger_paths)
    blobs = materialize_from_values(
        manifest, protocol, sample, sample_binding, freeze_binding, gate,
        gate_binding, chain)
    verify_live_materialization_ledger(
        manifest, pre, capture_ledger(ledger_paths))
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source tree drifted during assembly materialization")
    return blobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--source-gate", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    value = prepare(args.sample, args.implementation_freeze,
                    args.source_gate, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-assembly] {value['n_targets']} targets x "
          f"{len(value['cell_order'])} cells -> {args.out} "
          f"({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        raise SystemExit(2)
