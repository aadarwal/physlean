#!/usr/bin/env python3
"""Synthetic fail-closed tests for the confirmation six-cell assembler."""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_v2b_nll_confirmation_assembly import (
    ASSEMBLY_SCHEMA, CELL_ORDER, CONTEXT_PROGRAM, FREEZE_SCHEMA, PROGRAM,
    RENDERER_PROGRAM, SAMPLE_SCHEMA_CONFIRMATION, SOURCE_GATE_SCHEMA,
    _OrderIndex, _payload, build_assembly_value, materialize_from_values,
    protocol_record, verify_live_materialization_ledger)
from v2b_assemble import (canonical_dependency_order, render_chunks,
                          utf8_budget_suffix)
from v2b_common import (SAMPLE_SCHEMA, V2BError, identity_key, sha256_bytes,
                        sha256_json, sha256_sorted_json)
from v2b_nll_confirmation import load_protocol
from v2b_nll_confirmation_context import ContextMassIndex


_FIXTURE = None
_BUILT = None


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation assembly input"
    except V2BError as err:
        if text is not None:
            assert text in str(err), str(err)


def _key_set(keys):
    ordered = sorted(keys)
    return dict(n=len(ordered), sha256=sha256_json(ordered), keys=ordered)


def _ledger():
    entries = []
    digest = sha256_sorted_json(entries)
    return dict(algorithm="sha256-sorted-json-file-ledger-v1",
                n_entries=0, entries=entries, entries_sha256=digest,
                pre_entries_sha256=digest, post_entries_sha256=digest,
                unchanged=True)


def _fixture():
    global _FIXTURE
    if _FIXTURE is not None:
        return _FIXTURE
    temporary = tempfile.TemporaryDirectory()
    root = temporary.name
    protocol, _ = load_protocol()
    units = {}
    candidates = []
    identities = []

    def add_unit(rel, identity, payload, header):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        key = identity_key("python", identity)
        units[key] = dict(
            identity=list(identity), key=key, source=path, source_rel=rel,
            source_sha256=sha256_bytes(payload), start=0, end=len(payload),
            header_bytes=header, body_bytes=len(payload) - header,
            split_kind="python-colon")
        return key

    for index in range(200):
        rel = f"selected/m{index:03d}.py"
        identity = [rel, f"target_{index}", 0]
        header_blob = f"def target_{index}():".encode()
        payload = header_blob + f"\n    return {index}\n".encode()
        key = add_unit(rel, identity, payload, len(header_blob))
        priority = sha256_json(["synthetic-confirmation-priority", index])
        candidates.append(dict(
            identity=list(identity), source_rel=rel,
            body_bytes=len(payload) - len(header_blob),
            cell="L1-D1-Cmiddle", priority=priority))
        identities.append((identity, key, priority))

    dep_identity = ["context/dependency.py", "shared_dependency", 0]
    dep_header = b"def shared_dependency():"
    dep_payload = (dep_header + b"\n    value = '" + b"d" * 17000
                   + b"'\n    return value\n")
    dep_key = add_unit(dep_identity[0], dep_identity, dep_payload,
                       len(dep_header))
    pool_identity = ["context/random_pool.py", "random_pool", 0]
    pool_header = b"def random_pool():"
    pool_payload = (pool_header + b"\n    value = '" + b"r" * 17000
                    + b"'\n    return value\n")
    add_unit(pool_identity[0], pool_identity, pool_payload, len(pool_header))
    edges = [(list(identity), list(dep_identity))
             for identity, _, _ in identities]
    chain = dict(
        bindings=copy.deepcopy(
            protocol["source_eligibility_gate"]["bindings"]),
        candidates=dict(targets=candidates), units=units, edges=edges,
        adjacency={})

    gate_binding = dict(path="/synthetic/source_gate.json",
                        schema=SOURCE_GATE_SCHEMA, sha256="1" * 64)
    freeze_binding = dict(path="/synthetic/implementation_freeze.json",
                          schema=FREEZE_SCHEMA, sha256="2" * 64)
    sample_binding = dict(path="/synthetic/sample.json",
                          schema=SAMPLE_SCHEMA_CONFIRMATION,
                          sha256="3" * 64)
    plan_targets = [dict(identity=list(identity), cell="L1-D1-Cmiddle",
                         priority=priority)
                    for identity, _, priority in identities]
    keys = [key for _, key, _ in identities]
    modules = [identity[0] for identity, _, _ in identities]
    module_counts = [[module, 1] for module in sorted(modules)]
    exclusions = dict(
        source_ineligible_keys=_key_set([]),
        pilot_target_keys=_key_set([]), pilot_modules=_key_set([]),
        pilot_module_candidate_keys=_key_set([]),
        union_excluded_keys=_key_set([]),
        post_pilot_eligible_keys=_key_set(keys))
    plan = dict(
        schema=SAMPLE_SCHEMA, repo="sympy", language="python",
        n_requested=200, n_excluded=0,
        excluded_keys_sha256=sha256_json([]), n_selected=200,
        quota_table={}, cell_populations={}, cell_fills={}, shortfalls={},
        unsampled_cells=[], targets=plan_targets)
    sample = dict(
        schema=SAMPLE_SCHEMA_CONFIRMATION,
        state="drawn-source-gated-module-disjoint-pre-score",
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=16384, requested_n=200, realized_n=200,
        protocol=protocol_record(),
        bindings=dict(
            source_gate=copy.deepcopy(gate_binding),
            candidates=dict(
                path=protocol["inputs"]["candidates"]["path"],
                schema=protocol["inputs"]["candidates"]["schema"],
                sha256=protocol["inputs"]["candidates"]["sha256"]),
            pilot_sample=dict(
                path=protocol["inputs"]["pilot_sample"]["path"],
                schema=protocol["inputs"]["pilot_sample"]["schema"],
                sha256=protocol["inputs"]["pilot_sample"]["sha256"]),
            implementation_freeze=copy.deepcopy(freeze_binding)),
        exclusion_bindings=exclusions, plan=plan,
        selected_keys=_key_set(keys), selected_modules=_key_set(modules),
        cluster_support=dict(
            n_targets=200, n_modules=200, module_counts=module_counts,
            module_counts_sha256=sha256_json(module_counts),
            effective_clusters=200.0,
            effective_clusters_numerator=40000,
            effective_clusters_denominator=200,
            minimum_modules=20, minimum_effective_clusters=10,
            passed=True),
        input_ledger=_ledger(),
        generator=dict(
            program="finalize_v2b_nll_confirmation_sample.py",
            program_sha256="4" * 64, source_commit="5" * 40,
            source_tree_hash="6" * 64))

    mass = ContextMassIndex(units, edges, {}, source_cache={})
    gate_rows = [mass.row(identity, 16384)
                 for identity, _, _ in identities]
    source_gate = dict(
        schema=SOURCE_GATE_SCHEMA, study_id=protocol["study_id"],
        repo="sympy", language="python", budget_bytes=16384,
        n_rows=len(gate_rows), rows=gate_rows)
    generator = dict(
        program=PROGRAM, program_sha256="7" * 64,
        context_program=CONTEXT_PROGRAM, context_program_sha256="8" * 64,
        renderer_program=RENDERER_PROGRAM,
        renderer_program_sha256="9" * 64,
        source_commit="a" * 40, source_tree_hash="b" * 64)
    _FIXTURE = dict(
        temporary=temporary, protocol=protocol, sample=sample,
        sample_binding=sample_binding, freeze_binding=freeze_binding,
        source_gate=source_gate, gate_binding=gate_binding, chain=chain,
        input_ledger=_ledger(), generator=generator, dep_key=dep_key)
    return _FIXTURE


def _build(values=None):
    values = _fixture() if values is None else values
    collected = {}
    manifest = build_assembly_value(
        values["protocol"], protocol_record(), values["sample"],
        values["sample_binding"], values["freeze_binding"],
        values["source_gate"], values["gate_binding"], values["chain"],
        values["input_ledger"], values["generator"], collect=collected)
    return manifest, collected


def _built():
    global _BUILT
    if _BUILT is None:
        _BUILT = _build()
    return _BUILT


def test_exact_cell_order_no_extra_arms_and_intrinsic_k1():
    manifest, collected = _built()
    assert manifest["schema"] == ASSEMBLY_SCHEMA
    assert manifest["cell_order"] == list(CELL_ORDER)
    assert manifest["n_targets"] == 200
    for target in manifest["targets"]:
        assert [cell["cell_id"] for cell in target["cells"]] == \
            list(CELL_ORDER)
        k1 = target["cells"][0]
        assert k1["eligible"] is True
        assert k1["eligibility_basis"] == "intrinsic-empty-context"
        assert k1["context_bytes"] == 0
        assert k1["context_sha256"] == sha256_bytes(b"")
        assert collected[target["key"]]["cells"]["k1"] == b""
    encoded = json.dumps(manifest)
    for forbidden in ('"k2"', '"k6"', '"k7"', '"bm25"', '"k3s"',
                      '"k4s"'):
        assert forbidden not in encoded


def test_required_primary_cell_ineligibility_aborts_whole_assembly():
    base = _fixture()
    values = dict(base)
    chain = dict(base["chain"])
    first_identity = base["sample"]["plan"]["targets"][0]["identity"]
    chain["edges"] = [edge for edge in chain["edges"]
                      if edge[0] != first_identity]
    values["chain"] = chain
    mass = ContextMassIndex(chain["units"], chain["edges"], {},
                            source_cache={})
    gate = copy.deepcopy(base["source_gate"])
    by_key = {row["key"]: row for row in gate["rows"]}
    key = identity_key("python", first_identity)
    by_key[key] = mass.row(first_identity, 16384)
    gate["rows"] = [by_key[row["key"]] for row in gate["rows"]]
    values["source_gate"] = gate
    _reject(lambda: _build(values), "source-ineligible target")


def test_diagnostic_ineligibility_is_explicit_without_fake_context():
    manifest, collected = _built()
    for target in manifest["targets"]:
        by_id = {cell["cell_id"]: cell for cell in target["cells"]}
        k3 = by_id["k3:16384"]
        assert k3["eligible"] is False
        assert k3["ineligibility_reason"] == \
            "maximal-rendering-below-16384-bytes"
        assert k3["context_bytes"] is None
        assert k3["context_sha256"] is None
        assert k3["utf8_shortfall_bytes"] is None
        assert collected[target["key"]]["cells"]["k3:16384"] is None
        assert by_id["k4:16384"]["eligible"] is True
        assert by_id["k5:0:16384"]["eligible"] is True


def test_all_k5_seeds_share_pool_and_maximal_rendering_length():
    manifest, _ = _built()
    for target in manifest["targets"]:
        by_id = {cell["cell_id"]: cell for cell in target["cells"]}
        seeds = [by_id[f"k5:{seed}:16384"] for seed in (0, 1, 2)]
        assert len({cell["unit_pool_keys_sha256"] for cell in seeds}) == 1
        assert len({cell["n_ordered_units"] for cell in seeds}) == 1
        assert len({cell["rendering_bytes"] for cell in seeds}) == 1
        assert all(cell["eligible"] for cell in seeds)


def test_deterministic_rebuild_materialization_and_full_renderer_equivalence():
    values = _fixture()
    manifest, first = _built()
    rebuilt, second = _build()
    assert rebuilt == manifest
    assert second == first
    replay = materialize_from_values(
        manifest, values["protocol"], values["sample"],
        values["sample_binding"], values["freeze_binding"],
        values["source_gate"], values["gate_binding"], values["chain"])
    assert replay == first

    target = manifest["targets"][0]
    identity = target["identity"]
    chain = values["chain"]
    mass = ContextMassIndex(chain["units"], chain["edges"], {},
                            source_cache={})
    k4_bits, _ = mass.selected_bits(target["key"])
    pool = set(mass.keys_from_bits(k4_bits))
    ordered = [key for key in _OrderIndex(
        chain["units"], chain["edges"]).k4_order(identity) if key in pool]
    reference = canonical_dependency_order(
        "python", "sympy", identity,
        [unit["identity"] for unit in chain["units"].values()],
        [[left, right] for left, right in chain["edges"]])
    reference_order = [identity_key("python", row)
                       for row in reference["unit_order"]
                       if identity_key("python", row) in pool]
    assert ordered == reference_order
    cache = {}
    rows = [dict(identity=chain["units"][key]["identity"],
                 relpath=chain["units"][key]["source_rel"],
                 payload=_payload(chain["units"][key], cache))
            for key in ordered]
    rendering, spans = render_chunks("python", rows)
    expected = utf8_budget_suffix(rendering, spans, 16384)["context"]
    assert replay[target["key"]]["cells"]["k4:16384"] == expected
    by_id = {cell["cell_id"]: cell for cell in target["cells"]}
    assert by_id["k4:16384"]["rendering_bytes"] == len(rendering)
    assert by_id["k4:16384"]["context_sha256"] == sha256_bytes(expected)


def test_materializer_rejects_manifest_cell_tampering():
    values = _fixture()
    manifest, _ = _built()
    bad = copy.deepcopy(manifest)
    bad["targets"][0]["cells"][0]["eligible"] = False
    bad["targets"][0]["cells_sha256"] = sha256_sorted_json(
        bad["targets"][0]["cells"])
    bad["targets_sha256"] = sha256_sorted_json(bad["targets"])
    _reject(lambda: materialize_from_values(
        bad, values["protocol"], values["sample"],
        values["sample_binding"], values["freeze_binding"],
        values["source_gate"], values["gate_binding"], values["chain"]),
        "k1 intrinsic-empty invariant")


def test_protocol_path_target_order_and_live_ledger_tampering_fail_closed():
    values = _fixture()
    manifest, _ = _built()

    bad_sample = copy.deepcopy(values["sample"])
    bad_sample["protocol"]["path"] = "/tmp/lookalike-protocol.json"
    bad_values = dict(values, sample=bad_sample)
    _reject(lambda: _build(bad_values), "sample protocol binding drift")

    reordered = copy.deepcopy(manifest)
    reordered["targets"][0], reordered["targets"][1] = \
        reordered["targets"][1], reordered["targets"][0]
    reordered["ordered_target_keys"]["keys"][0], \
        reordered["ordered_target_keys"]["keys"][1] = \
        reordered["ordered_target_keys"]["keys"][1], \
        reordered["ordered_target_keys"]["keys"][0]
    reordered["ordered_target_keys"]["sha256"] = sha256_json(
        reordered["ordered_target_keys"]["keys"])
    reordered["targets_sha256"] = sha256_sorted_json(reordered["targets"])
    _reject(lambda: materialize_from_values(
        reordered, values["protocol"], values["sample"],
        values["sample_binding"], values["freeze_binding"],
        values["source_gate"], values["gate_binding"], values["chain"]),
        "differs from bound sample plan")

    verify_live_materialization_ledger(manifest, [], [])
    changed = [dict(label="new-byte", bytes=1, sha256="c" * 64)]
    _reject(lambda: verify_live_materialization_ledger(
        manifest, changed, changed), "live assembly materialization ledger")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
