#!/usr/bin/env python3
"""Exact, model-free context-mass primitives for the SymPy confirmation.

The confirmation source gate and the later six-cell assembler must agree on
what the frozen k4 and k5:seed-0 maximal renderings contain.  This module is
their single implementation of the source-chain loader, SCC closures, and
renderer-equivalent byte masses.  It deliberately has no tokenizer, model,
NLL, BM25, sampling, or outcome-dependent code.

The optimization is exact: render length is additive over units because the
frozen renderer assigns one banner LF and one join/final-separator LF to every
unit.  SCC-condensed transitive closures and exclusion sets are represented as
Python-integer bitsets; order is immaterial to the *maximal* rendering length.
"""
import copy
import heapq
import os

from prepare_v2b_assembly import (_a6_exclusion_sets, _edges, _load_k7_order,
                                  _reverse_closure, _unit_index,
                                  _unit_payload)
from provenance import BASE
from v2b_assemble import (_components, canonical_dependency_order,
                          k5_unit_order, make_chunk, render_chunks)
from v2b_common import (A6_OUTCOME_SCHEMA, CANDIDATES_SCHEMA,
                        K7_ORDER_SCHEMA, NEARDUP_SCHEMA, V2BError,
                        identity_key, load_json, sha256_file,
                        sha256_json, sha256_sorted_json, validate_identity)
from v2b_metadata import PYTHON_EXTRACT_SCHEMA, corpus_git_identity


REPO = "sympy"
LANGUAGE = "python"
AUDIT_DOMAIN = "v2b-nll-e2-confirmation-source-gate-audit:v1"
AUDIT_N = 16
AUDIT_SCHEMA = "v2b_nll_e2_confirmation_source_gate_cross_check_v1"


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _safe_repo_path(relpath):
    """Resolve one protocol path under the source checkout, fail-closed."""
    if not isinstance(relpath, str) or not relpath \
            or os.path.isabs(relpath) or "\\" in relpath:
        raise V2BError(f"noncanonical protocol path {relpath!r}")
    normalized = os.path.normpath(relpath).replace(os.sep, "/")
    if normalized != relpath or relpath in (".", "..") \
            or relpath.startswith("../"):
        raise V2BError(f"protocol path escapes or normalizes: {relpath!r}")
    path = os.path.abspath(os.path.join(BASE, *relpath.split("/")))
    try:
        if os.path.commonpath((os.path.abspath(BASE), path)) != \
                os.path.abspath(BASE):
            raise V2BError(f"protocol path escapes checkout: {relpath!r}")
    except ValueError as err:
        raise V2BError(f"protocol path mismatch: {err}") from err
    return path


def _binding_row(value, label):
    if not isinstance(value, dict) or set(value) != \
            {"path", "schema", "sha256"} \
            or not isinstance(value.get("schema"), str) \
            or not value["schema"] or not _hex(value.get("sha256")):
        raise V2BError(f"malformed confirmation {label} binding")
    _safe_repo_path(value.get("path"))
    return dict(path=value["path"], schema=value["schema"],
                sha256=value["sha256"])


def load_bound_json(binding, label, expected_schema=None):
    """Load one exact protocol-bound JSON input and return value/path/row."""
    row = _binding_row(binding, label)
    if expected_schema is not None and row["schema"] != expected_schema:
        raise V2BError(f"{label} protocol schema drift")
    path = _safe_repo_path(row["path"])
    value, digest = load_json(path, row["schema"])
    if digest != row["sha256"]:
        raise V2BError(f"{label} raw artifact digest drift")
    return value, path, row


def _safe_corpus_rel(rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) \
            or "\\" in rel or "\n" in rel or "\r" in rel:
        raise V2BError(f"noncanonical extraction source_rel {rel!r}")
    normalized = os.path.normpath(rel).replace(os.sep, "/")
    if normalized != rel or rel in (".", "..") or rel.startswith("../"):
        raise V2BError(f"source_rel escapes or normalizes: {rel!r}")
    return rel


def _live_extraction(extraction, corpus_root):
    """Rebind old absolute extraction paths to the exact live git tree."""
    root = os.path.realpath(corpus_root)
    if not os.path.isdir(root) or root == os.path.sep:
        raise V2BError("confirmation corpus root is missing or too broad")
    live = copy.deepcopy(extraction)
    source_paths = []
    seen_rel = set()
    files = live.get("files")
    if not isinstance(files, list) or not files:
        raise V2BError("confirmation extraction has no files")
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise V2BError(f"extraction file[{index}] is not an object")
        rel = _safe_corpus_rel(row.get("rel"))
        if rel in seen_rel:
            raise V2BError(f"duplicate extraction source_rel {rel}")
        seen_rel.add(rel)
        recorded = row.get("source")
        if not isinstance(recorded, str) or not os.path.isabs(recorded) \
                or os.path.normpath(recorded) != recorded \
                or not (recorded == rel or recorded.endswith("/" + rel)):
            raise V2BError(f"extraction absolute source/rel drift: {rel}")
        lexical = os.path.abspath(os.path.join(root, *rel.split("/")))
        real = os.path.realpath(lexical)
        try:
            if real == root or os.path.commonpath((root, real)) != root:
                raise V2BError(f"live corpus source escapes root: {rel}")
        except ValueError as err:
            raise V2BError(f"live corpus path mismatch: {err}") from err
        if not os.path.isfile(real) or not _hex(row.get("source_sha256")) \
                or sha256_file(real) != row["source_sha256"]:
            raise V2BError(f"live corpus source hash drift: {rel}")
        row["source"] = real
        source_paths.append((f"corpus:{rel}", real))
    return live, root, source_paths


def load_source_chain(protocol):
    """Load and validate the exact model-free source chain in *protocol*.

    The returned ``ledger_paths`` enumerates every JSON and corpus source
    byte string that can affect the census.  The caller must capture it both
    before and after computation.
    """
    gate = protocol.get("source_eligibility_gate")
    scope = protocol.get("scope")
    if not isinstance(gate, dict) or not isinstance(scope, dict):
        raise V2BError("confirmation protocol lacks source gate/scope")
    raw_bindings = gate.get("bindings")
    expected_keys = {"candidates", "extraction", "k7_order", "neardup",
                     "a6_outcome", "corpus_root", "corpus_git_sha",
                     "renderer_and_seed_code"}
    if not isinstance(raw_bindings, dict) \
            or set(raw_bindings) != expected_keys:
        raise V2BError("confirmation source binding key drift")
    if raw_bindings.get("corpus_git_sha") != scope.get("corpus_git_sha"):
        raise V2BError("source-gate/scope corpus revision drift")
    if raw_bindings.get("renderer_and_seed_code") != \
            "bound by the later confirmation implementation-freeze artifact":
        raise V2BError("renderer/seed implementation binding drift")

    candidates, candidates_path, candidates_binding = load_bound_json(
        raw_bindings["candidates"], "candidates", CANDIDATES_SCHEMA)
    extraction, extraction_path, extraction_binding = load_bound_json(
        raw_bindings["extraction"], "extraction", PYTHON_EXTRACT_SCHEMA)
    k7, k7_path, k7_binding = load_bound_json(
        raw_bindings["k7_order"], "k7_order", K7_ORDER_SCHEMA)
    neardup, neardup_path, neardup_binding = load_bound_json(
        raw_bindings["neardup"], "neardup", NEARDUP_SCHEMA)
    outcome, outcome_path, outcome_binding = load_bound_json(
        raw_bindings["a6_outcome"], "a6_outcome", A6_OUTCOME_SCHEMA)

    corpus_rel = raw_bindings.get("corpus_root")
    corpus_root = _safe_repo_path(corpus_rel)
    corpus_revision = raw_bindings["corpus_git_sha"]
    corpus_identity = corpus_git_identity(corpus_root, corpus_revision)
    live_extraction, real_root, source_paths = _live_extraction(
        extraction, corpus_root)

    if candidates.get("repo") != REPO \
            or candidates.get("language") != LANGUAGE \
            or candidates.get("corpus_git_sha") != corpus_revision \
            or candidates.get("extraction", {}).get("sha256") != \
            extraction_binding["sha256"]:
        raise V2BError("confirmation candidate-table binding drift")
    targets = candidates.get("targets")
    expected_n = gate.get("candidate_universe_n")
    if not isinstance(targets, list) or candidates.get("n_candidates") != \
            len(targets) or len(targets) != expected_n:
        raise V2BError("confirmation candidate universe count drift")
    if extraction.get("repo") != REPO:
        raise V2BError("confirmation extraction repo drift")
    if neardup.get("repo") != REPO \
            or neardup.get("language") != LANGUAGE \
            or neardup.get("extraction", {}).get("sha256") != \
            extraction_binding["sha256"]:
        raise V2BError("confirmation near-duplicate binding drift")
    outcomes = outcome.get("outcomes")
    if not isinstance(outcomes, dict) or outcome.get("outcomes_sha256") != \
            sha256_sorted_json(outcomes):
        raise V2BError("confirmation A6 outcome content/hash drift")
    # This also validates the exact frozen order rule, repo, language, SHA,
    # and every k7 row even though k7 bytes are intentionally not rendered.
    checked_k7_binding, _ = _load_k7_order(
        k7_path, REPO, LANGUAGE, corpus_revision)
    if checked_k7_binding["sha256"] != k7_binding["sha256"] \
            or k7 is None:
        raise V2BError("confirmation k7 binding drift")

    units, sources = _unit_index(live_extraction, LANGUAGE,
                                 corpus_root=real_root)
    candidate_identities = {}
    for index, row in enumerate(targets):
        if not isinstance(row, dict):
            raise V2BError(f"candidate target[{index}] is not an object")
        identity = validate_identity(LANGUAGE, row.get("identity"))
        key = identity_key(LANGUAGE, identity)
        if key in candidate_identities or key not in units \
                or identity[0] != units[key]["identity"][0]:
            raise V2BError(f"candidate identity is duplicate/unresolved: {key}")
        candidate_identities[key] = list(identity)
    if len(candidate_identities) != expected_n:
        raise V2BError("candidate key universe count drift")

    edges = _edges(live_extraction, LANGUAGE)
    adjacency = _a6_exclusion_sets(neardup, outcome, LANGUAGE, set(units))
    bindings = {
        "candidates": candidates_binding,
        "extraction": extraction_binding,
        "k7_order": k7_binding,
        "neardup": neardup_binding,
        "a6_outcome": outcome_binding,
        "corpus_root": corpus_rel,
        "corpus_git_sha": corpus_revision,
        "renderer_and_seed_code": raw_bindings["renderer_and_seed_code"],
    }
    ledger_paths = [
        ("input:candidates", candidates_path),
        ("input:extraction", extraction_path),
        ("input:k7_order", k7_path),
        ("input:neardup", neardup_path),
        ("input:a6_outcome", outcome_path),
        *source_paths,
    ]
    return dict(bindings=bindings, candidates=candidates,
                candidate_identities=candidate_identities,
                extraction=live_extraction, units=units, sources=sources,
                edges=edges, adjacency=adjacency, corpus_root=real_root,
                corpus_identity=corpus_identity,
                source_labels=sorted(label for label, _ in source_paths),
                ledger_paths=ledger_paths)


def _sum_bits(bits, weights):
    total = 0
    while bits:
        low = bits & -bits
        total += weights[low.bit_length() - 1]
        bits ^= low
    return total


def _popcount(bits):
    """Python 3.9-compatible population count (cluster 3.12 uses C API)."""
    method = getattr(bits, "bit_count", None)
    return method() if method is not None else bin(bits).count("1")


class ContextMassIndex:
    """Exact SCC/bitset index for k4 and k5:seed-0 maximal byte masses."""

    def __init__(self, units, edges, adjacency, source_cache=None,
                 source_labels=None):
        if not isinstance(units, dict) or not units:
            raise V2BError("empty confirmation unit universe")
        self.keys = sorted(units)
        self.index = {key: i for i, key in enumerate(self.keys)}
        self.units = units
        self.adjacency = {key: set(value) for key, value in adjacency.items()}
        self.all_bits = (1 << len(self.keys)) - 1
        self.source_cache = {} if source_cache is None else source_cache

        identities = {}
        for key in self.keys:
            identity = validate_identity(LANGUAGE, units[key].get("identity"))
            if identity_key(LANGUAGE, identity) != key:
                raise V2BError(f"unit key/identity drift: {key}")
            identities[key] = identity
        normalized_edges = set()
        for index, edge in enumerate(edges):
            if not isinstance(edge, (list, tuple)) or len(edge) != 2:
                raise V2BError(f"graph edge[{index}] is malformed")
            dependent = validate_identity(LANGUAGE, edge[0])
            dependency = validate_identity(LANGUAGE, edge[1])
            a, b = identity_key(LANGUAGE, dependent), \
                identity_key(LANGUAGE, dependency)
            if a not in units or b not in units:
                raise V2BError("graph edge endpoint outside unit universe")
            if a != b:
                normalized_edges.add((dependent, dependency))
        self.edges = [(list(a), list(b))
                      for a, b in sorted(normalized_edges)]

        components, component_of_identity = _components(
            set(identities.values()), normalized_edges)
        self.component_of = {
            key: component_of_identity[identities[key]] for key in self.keys}
        n_components = len(components)
        component_bits = [0] * n_components
        for key, cid in self.component_of.items():
            component_bits[cid] |= 1 << self.index[key]
        direct = [set() for _ in components]
        dependents = [set() for _ in components]
        for dependent, dependency in normalized_edges:
            a, b = component_of_identity[dependent], \
                component_of_identity[dependency]
            if a != b:
                direct[a].add(b)
                dependents[b].add(a)

        # Dependency-first topological order of the condensed DAG.
        remaining = [len(row) for row in direct]
        ready = list(cid for cid, n in enumerate(remaining) if n == 0)
        heapq.heapify(ready)
        topo = []
        while ready:
            cid = heapq.heappop(ready)
            topo.append(cid)
            for dependent in sorted(dependents[cid]):
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    heapq.heappush(ready, dependent)
        if len(topo) != n_components:
            raise AssertionError("SCC condensation is cyclic")

        forward = [0] * n_components
        for cid in topo:
            bits = 0
            for dep in direct[cid]:
                bits |= component_bits[dep] | forward[dep]
            forward[cid] = bits
        reverse = [0] * n_components
        for cid in reversed(topo):
            bits = 0
            for dependent in dependents[cid]:
                bits |= component_bits[dependent] | reverse[dependent]
            reverse[cid] = bits
        self.component_bits = component_bits
        self.forward_bits = forward
        self.reverse_bits = reverse

        file_bits = {}
        weights = []
        for key in self.keys:
            unit = units[key]
            rel = unit.get("source_rel")
            payload = _unit_payload(unit, self.source_cache)
            chunk, _ = make_chunk(LANGUAGE, rel, payload)
            # Frozen render_chunks adds exactly one LF after each chunk,
            # either as an inter-unit join or final query separator.
            weights.append(len(chunk) + 1)
            source = unit.get("source")
            file_bits[source] = file_bits.get(source, 0) | \
                (1 << self.index[key])
        self.weights = weights
        self.total_weight = sum(weights)
        self.file_bits = file_bits

        self.near_bits = {}
        for key, neighbors in adjacency.items():
            if key not in self.index:
                raise V2BError("near-duplicate key outside unit universe")
            bits = 0
            for other in neighbors:
                if other not in self.index or other == key:
                    raise V2BError("near-duplicate neighbor invalid")
                bits |= 1 << self.index[other]
            self.near_bits[key] = bits
        for key, neighbors in adjacency.items():
            if any(key not in adjacency.get(other, ()) for other in neighbors):
                raise V2BError("near-duplicate adjacency is not symmetric")
        if source_labels is None:
            source_labels = sorted({
                f"corpus:{unit['source_rel']}" for unit in units.values()})
        if not isinstance(source_labels, list) \
                or source_labels != sorted(source_labels) \
                or len(source_labels) != len(set(source_labels)) \
                or any(not isinstance(label, str)
                       or not label.startswith("corpus:")
                       for label in source_labels):
            raise V2BError("context source-label projection is malformed")
        unit_source_labels = {
            f"corpus:{unit['source_rel']}" for unit in units.values()}
        if not unit_source_labels <= set(source_labels):
            raise V2BError("context source-label projection omits a unit file")
        self.source_labels = source_labels
        self.stats = dict(
            method="scc-condensation-python-int-bitset-additive-render-mass-v1",
            n_units=len(self.keys), n_edges=len(normalized_edges),
            n_scc=n_components,
            max_scc_size=max(len(members) for members in components),
            n_source_files=len(source_labels),
            source_labels_sha256=sha256_json(source_labels))

    def selected_bits(self, key):
        """Return exact k4 and k5:seed-0 identity sets as bitsets."""
        if key not in self.index:
            raise V2BError(f"target absent from context index: {key}")
        unit = self.units[key]
        cid = self.component_of[key]
        same = self.file_bits[unit["source"]]
        near = self.near_bits.get(key, 0)
        k4 = self.forward_bits[cid] & ~same & ~near & self.all_bits
        forbidden = (self.component_bits[cid] | self.forward_bits[cid]
                     | self.reverse_bits[cid] | same | near)
        k5 = self.all_bits & ~forbidden
        if k4 & k5 or (k4 | k5) & (1 << self.index[key]):
            raise AssertionError("confirmation context sets overlap target")
        return k4, k5

    def keys_from_bits(self, bits):
        return [self.keys[index] for index in range(len(self.keys))
                if bits & (1 << index)]

    def row(self, identity, budget_bytes):
        identity = validate_identity(LANGUAGE, identity)
        key = identity_key(LANGUAGE, identity)
        if not isinstance(budget_bytes, int) \
                or isinstance(budget_bytes, bool) or budget_bytes <= 0:
            raise V2BError("confirmation budget must be a positive integer")
        k4_bits, k5_bits = self.selected_bits(key)
        k4_bytes = self.mass(k4_bits)
        k5_bytes = self.mass(k5_bits)
        k4_ok, k5_ok = k4_bytes >= budget_bytes, k5_bytes >= budget_bytes
        reasons = []
        if not k4_ok:
            reasons.append("k4-rendering-below-budget")
        if not k5_ok:
            reasons.append("k5-seed0-rendering-below-budget")
        return dict(
            key=key, identity=list(identity), module=identity[0],
            k4_rendering_bytes=k4_bytes, k4_eligible=k4_ok,
            k5_seed0_rendering_bytes=k5_bytes,
            k5_seed0_eligible=k5_ok, eligible=k4_ok and k5_ok,
            ineligibility_reasons=reasons)

    def mass(self, bits):
        """Exact mass, summing whichever side of the partition is smaller."""
        bits &= self.all_bits
        complement = self.all_bits ^ bits
        if _popcount(bits) <= _popcount(complement):
            return _sum_bits(bits, self.weights)
        return self.total_weight - _sum_bits(complement, self.weights)

    def _full_renderer_totals(self, target_key):
        """Independent frozen-order/render reference used only by the audit."""
        target = self.units[target_key]
        identity = target["identity"]
        near = set(self.adjacency.get(target_key, ()))
        reverse = _reverse_closure(self.edges, LANGUAGE, target_key)
        same = {key for key, unit in self.units.items()
                if unit["source"] == target["source"] and key != target_key}
        universe = set(self.units) - {target_key} - same - near - reverse
        order = canonical_dependency_order(
            LANGUAGE, REPO, identity,
            [unit["identity"] for unit in self.units.values()],
            [[a, b] for a, b in self.edges])
        closure = [identity_key(LANGUAGE, row)
                   for row in order["unit_order"]]
        k4 = [key for key in closure if key not in same and key not in near]
        pool = universe - set(closure)
        k5 = [identity_key(LANGUAGE, row["identity"])
              for row in k5_unit_order(
                  LANGUAGE, REPO, identity,
                  [self.units[key]["identity"] for key in pool], 0)]

        def full(keys):
            rendering, _ = render_chunks(LANGUAGE, [
                dict(identity=self.units[key]["identity"],
                     relpath=self.units[key]["source_rel"],
                     payload=_unit_payload(self.units[key], self.source_cache))
                for key in keys])
            return len(rendering)

        return set(k4), set(k5), full(k4), full(k5)

    def cross_check(self, candidate_identities, budget_bytes):
        """Hash-selected real-source equality audit against full rendering."""
        candidate_keys = sorted(candidate_identities)
        if any(key not in self.index for key in candidate_keys):
            raise V2BError("audit candidate absent from context index")
        selected = sorted(sorted(
            candidate_keys,
            key=lambda key: (sha256_json([AUDIT_DOMAIN, key]), key)
        )[:min(AUDIT_N, len(candidate_keys))])
        rows = []
        for key in selected:
            bit_k4, bit_k5 = self.selected_bits(key)
            full_k4_set, full_k5_set, full_k4, full_k5 = \
                self._full_renderer_totals(key)
            bit_k4_set = set(self.keys_from_bits(bit_k4))
            bit_k5_set = set(self.keys_from_bits(bit_k5))
            bit_k4_bytes, bit_k5_bytes = self.mass(bit_k4), self.mass(bit_k5)
            passed = bit_k4_set == full_k4_set \
                and bit_k5_set == full_k5_set \
                and bit_k4_bytes == full_k4 \
                and bit_k5_bytes == full_k5
            if not passed:
                raise V2BError(f"full-render source audit mismatch: {key}")
            rows.append(dict(
                key=key,
                bitset_k4_rendering_bytes=bit_k4_bytes,
                full_k4_rendering_bytes=full_k4,
                bitset_k5_seed0_rendering_bytes=bit_k5_bytes,
                full_k5_seed0_rendering_bytes=full_k5,
                k4_eligible=full_k4 >= budget_bytes,
                k5_seed0_eligible=full_k5 >= budget_bytes,
                eligible=full_k4 >= budget_bytes and full_k5 >= budget_bytes,
                passed=True))
        return dict(
            schema=AUDIT_SCHEMA,
            selection=dict(domain=AUDIT_DOMAIN, n=len(selected),
                           sha256=sha256_json(selected), keys=selected),
            reference=("canonical_dependency_order+k5_unit_order(seed=0)+"
                       "render_chunks"),
            rows=rows, rows_sha256=sha256_sorted_json(rows), passed=True)
