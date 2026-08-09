#!/usr/bin/env python3
"""Adversarial tests for the model-free confirmation source gate."""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_v2b_assembly import _reverse_closure, _unit_payload
from prepare_v2b_nll_confirmation_gate import (
    FRAGMENT_SCHEMA, build_fragment_value, capture_ledger, generator_record,
    key_set, ledger_record, protocol_record, reduce_gate_values,
    validate_reduced_gate)
from provenance import BASE
from v2b_assemble import (canonical_dependency_order, k5_unit_order,
                          render_chunks)
from v2b_common import (BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA, V2BError,
                        identity_key, sha256_bytes, sha256_file, sha256_json)
from v2b_common import sha256_sorted_json
from v2b_nll_confirmation import load_protocol
from v2b_nll_confirmation_context import (ContextMassIndex,
                                          load_bound_json)


def _expect_error(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation source-gate input"
    except V2BError as err:
        if text is not None:
            assert text in str(err), str(err)


def _fixture(td):
    """Graph includes SCC, diamond, reverse closure, same-file, and A6."""
    # label -> (module, name, relative source, payload).  Two target-module
    # rows deliberately share a file; payloads exercise zero/one/many final
    # LFs and UTF-8 path/payload accounting.
    specs = [
        ("t", "pkg.target", "target", "pkg/target.py", b"def target():\n    return 1\n"),
        ("same", "pkg.target", "same", "pkg/target.py", b"def same():\n    return 2\n\n\n"),
        ("d1", "pkg.dep1", "dep1", "pkg/dep1.py", b"def dep1():\n    return 'alpha'"),
        ("d2", "pkg.dep2", "dep2", "pkg/dép2.py",
         "def dep2():\n    return 'λ'\n".encode()),
        ("c1", "pkg.cycle", "cycle_a", "pkg/cycle.py", b"def cycle_a(): return 1\n"),
        ("c2", "pkg.cycle", "cycle_b", "pkg/cycle.py", b"def cycle_b(): return 2\n\n"),
        ("near", "pkg.near", "near", "pkg/near.py", b"def near():\n    return 'near'\n"),
        ("r1", "pkg.reverse", "reverse", "pkg/reverse.py", b"def reverse(): return target()\n"),
        ("r2", "pkg.reverse2", "reverse2", "pkg/reverse2.py", b"def reverse2(): return reverse()\n"),
        ("i1", "pkg.independent", "independent", "pkg/ind.py", b"x = 'independent payload 111111111111111111'"),
        ("i2", "pkg.independent2", "independent2", "pkg/ind2.py", b"y = 'independent payload 222222222222222222'\n"),
    ]
    by_rel = {}
    for spec in specs:
        by_rel.setdefault(spec[3], []).append(spec)
    units, labels = {}, {}
    for rel, rows in by_rel.items():
        path = os.path.join(td, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        blob = bytearray()
        spans = []
        for label, module, name, _, payload in rows:
            if blob:
                blob.extend(b"\n# fixture gap\n")
            start = len(blob)
            blob.extend(payload)
            spans.append((label, module, name, start, len(blob)))
        with open(path, "wb") as fh:
            fh.write(blob)
        digest = sha256_bytes(bytes(blob))
        for label, module, name, start, end in spans:
            identity = [module, name, start]
            key = identity_key("python", identity)
            units[key] = dict(identity=identity, key=key, source=path,
                              source_rel=rel, source_sha256=digest,
                              start=start, end=end)
            labels[label] = key
    def ident(label):
        return units[labels[label]]["identity"]
    # dependent -> dependency.  target has a diamond, a same-file dependency,
    # an A6-near dependency, and a dependency SCC.  r1/r2 are reverse-only.
    edge_labels = [
        ("t", "d1"), ("t", "d2"), ("d1", "d2"),
        ("d1", "c1"), ("d2", "c2"), ("c1", "c2"), ("c2", "c1"),
        ("t", "same"), ("t", "near"), ("r1", "t"), ("r2", "r1"),
    ]
    edges = [(ident(a), ident(b)) for a, b in edge_labels]
    adjacency = {labels["t"]: {labels["near"]},
                 labels["near"]: {labels["t"]}}
    return units, edges, adjacency, labels


def _legacy_full_renderer(units, edges, adjacency, target_key):
    """Frozen production set construction plus actual render_chunks bytes."""
    target = units[target_key]
    identity = target["identity"]
    near = set(adjacency.get(target_key, ()))
    reverse = _reverse_closure(edges, "python", target_key)
    same = {key for key, unit in units.items()
            if unit["source"] == target["source"] and key != target_key}
    universe = set(units) - {target_key} - same - near - reverse
    order = canonical_dependency_order(
        "python", "sympy", identity,
        [unit["identity"] for unit in units.values()],
        [[a, b] for a, b in edges])
    closure = [identity_key("python", row) for row in order["unit_order"]]
    k4 = [key for key in closure if key not in same and key not in near]
    pool = universe - set(closure)
    k5 = [identity_key("python", row["identity"])
          for row in k5_unit_order(
              "python", "sympy", identity,
              [units[key]["identity"] for key in pool], 0)]
    cache = {}
    def render(keys):
        value, _ = render_chunks("python", [
            dict(identity=units[key]["identity"],
                 relpath=units[key]["source_rel"],
                 payload=_unit_payload(units[key], cache))
            for key in keys])
        return len(value)
    return set(k4), set(k5), render(k4), render(k5)


def test_scc_bitset_masses_equal_full_frozen_renderer():
    with tempfile.TemporaryDirectory() as td:
        units, edges, adjacency, labels = _fixture(td)
        index = ContextMassIndex(units, edges, adjacency)
        assert index.stats["n_scc"] == len(units) - 1  # one two-node SCC
        for key in sorted(units):
            expected_k4, expected_k5, k4_bytes, k5_bytes = \
                _legacy_full_renderer(units, edges, adjacency, key)
            got_k4, got_k5 = index.selected_bits(key)
            assert set(index.keys_from_bits(got_k4)) == expected_k4
            assert set(index.keys_from_bits(got_k5)) == expected_k5
            row = index.row(units[key]["identity"], 128)
            assert row["k4_rendering_bytes"] == k4_bytes
            assert row["k5_seed0_rendering_bytes"] == k5_bytes
            assert row["k4_eligible"] is (k4_bytes >= 128)
            assert row["k5_seed0_eligible"] is (k5_bytes >= 128)
        t_k4, t_k5 = index.selected_bits(labels["t"])
        assert labels["same"] not in index.keys_from_bits(t_k4)
        assert labels["near"] not in index.keys_from_bits(t_k4)
        assert labels["r1"] not in index.keys_from_bits(t_k5)
        assert labels["r2"] not in index.keys_from_bits(t_k5)


def _synthetic_protocol(n, pilot_keys, pilot_modules):
    protocol = copy.deepcopy(load_protocol()[0])
    protocol["source_eligibility_gate"]["candidate_universe_n"] = n
    protocol["scope"]["budget_bytes"] = 128
    protocol["inputs"]["pilot_sympy_target_count"] = len(pilot_keys)
    protocol["inputs"]["pilot_sympy_keys_sha256"] = sha256_json(
        sorted(pilot_keys))
    protocol["inputs"]["pilot_sympy_module_count"] = len(pilot_modules)
    protocol["inputs"]["pilot_sympy_modules_sha256"] = sha256_json(
        sorted(pilot_modules))
    return protocol


def _ledger(digests):
    rows = [dict(label=label, bytes=index + 1, sha256=digests[label])
            for index, label in enumerate(sorted(digests))]
    return ledger_record(rows, copy.deepcopy(rows))


def _fragment_ledger(protocol, freeze, source_labels):
    bindings = protocol["source_eligibility_gate"]["bindings"]
    return _ledger({
        "input:protocol": protocol_record()["raw_sha256"],
        "input:implementation_freeze": freeze["sha256"],
        **{f"input:{name}": bindings[name]["sha256"] for name in (
            "candidates", "extraction", "k7_order", "neardup",
            "a6_outcome")},
        **{label: "b" * 64 for label in source_labels},
    })


def _reducer_ledger(protocol, freeze, fragments):
    return _ledger({
        "input:protocol": protocol_record()["raw_sha256"],
        "input:implementation_freeze": freeze["sha256"],
        "input:candidates": protocol["inputs"]["candidates"]["sha256"],
        "input:pilot_sample": protocol["inputs"]["pilot_sample"]["sha256"],
        **{f"fragment:{index:06d}": row["sha256"]
           for index, row in enumerate(fragments)},
    })


def _reduced_fixture(td, shard_count=3):
    units, edges, adjacency, labels = _fixture(td)
    index = ContextMassIndex(units, edges, adjacency)
    candidate_identities = {
        key: unit["identity"] for key, unit in units.items()}
    pilot_labels = ("t", "i1")
    pilot_keys = [labels[label] for label in pilot_labels]
    pilot_modules = sorted({units[key]["identity"][0] for key in pilot_keys})
    protocol = _synthetic_protocol(
        len(candidate_identities), pilot_keys, pilot_modules)
    generator = generator_record()
    freeze_binding = dict(
        path=os.path.abspath(os.path.join(td, "implementation-freeze.json")),
        schema="v2b_nll_e2_confirmation_implementation_freeze_v1",
        sha256="a" * 64)
    fragment_bindings = dict(
        protocol["source_eligibility_gate"]["bindings"],
        implementation_freeze=freeze_binding)
    fragments = [build_fragment_value(
        protocol, fragment_bindings,
        candidate_identities, index, shard, shard_count,
        _fragment_ledger(protocol, freeze_binding, index.source_labels),
        generator)
        for shard in range(shard_count)]
    eligible = {row["key"] for fragment in fragments
                for row in fragment["rows"] if row["eligible"]}
    intersection = sorted(eligible & set(pilot_keys))
    protocol["source_eligibility_gate"][
        "pilot_intersection_expected_from_sealed_pilot_evidence"] = dict(
            count=len(intersection), keys_sha256=sha256_json(intersection),
            meaning="synthetic audit expectation")
    candidates = dict(
        schema=CANDIDATES_SCHEMA, repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        n_candidates=len(candidate_identities),
        targets=[dict(identity=candidate_identities[key])
                 for key in sorted(candidate_identities)])
    pilot = dict(
        schema=BOUND_SAMPLE_SCHEMA, sampling_state="drawn",
        n_requested_per_corpus=len(pilot_labels),
        plans={"sympy": dict(
            candidates_sha256=protocol["inputs"]["candidates"]["sha256"],
            n_selected=len(pilot_labels),
            targets=[dict(identity=units[labels[label]]["identity"])
                     for label in pilot_labels])})
    pilot["plans_sha256"] = sha256_sorted_json(pilot["plans"])
    fragment_inputs = [dict(
        path=f"fragment-{i}.json", sha256=f"{i + 2:064x}", value=value)
        for i, value in enumerate(fragments)]
    reduced = reduce_gate_values(
        protocol, candidates, pilot, fragment_inputs, freeze_binding,
        _reducer_ledger(protocol, freeze_binding, fragment_inputs), generator)
    return (protocol, candidates, pilot, fragment_inputs, freeze_binding,
            reduced)


def test_fragment_reducer_exact_partition_and_consumer_contract():
    with tempfile.TemporaryDirectory() as td:
        protocol, _, _, fragments, _, reduced = _reduced_fixture(td)
        assert validate_reduced_gate(reduced, protocol) is reduced
        assert reduced["schema"] == \
            "v2b_nll_e2_confirmation_source_gate_v1"
        assert reduced["n_rows"] == reduced["candidate_keys"]["n"]
        assert reduced["eligible_keys"]["n"] + \
            reduced["source_ineligible_keys"]["n"] == reduced["n_rows"]
        pilot_modules = set(reduced["pilot_modules"]["keys"])
        row_by_key = {row["key"]: row for row in reduced["rows"]}
        assert all(row_by_key[key]["module"] not in pilot_modules
                   for key in reduced["post_pilot_eligible_keys"]["keys"])
        assert [row["shard_index"] for row in reduced["fragments"]] == \
            list(range(len(fragments)))


def test_reducer_rejects_missing_overlap_and_row_tamper():
    with tempfile.TemporaryDirectory() as td:
        protocol, candidates, pilot, fragments, freeze, _ = \
            _reduced_fixture(td)
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, fragments[:-1], freeze,
            _reducer_ledger(protocol, freeze, fragments[:-1]),
            generator_record()), "shards 0..count-1")
        duplicate = copy.deepcopy(fragments)
        duplicate[1]["value"]["shard_index"] = 0
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, duplicate, freeze,
            _reducer_ledger(protocol, freeze, duplicate),
            generator_record()), "shards 0..count-1")
        tampered = copy.deepcopy(fragments)
        tampered[0]["value"]["rows"][0]["k4_rendering_bytes"] += 1
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, tampered, freeze,
            _reducer_ledger(protocol, freeze, tampered),
            generator_record()), "row/range hash drift")
        mixed_code = copy.deepcopy(fragments)
        mixed_code[1]["value"]["generator"] = dict(
            mixed_code[1]["value"]["generator"],
            source_tree_hash="f" * 64)
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, mixed_code, freeze,
            _reducer_ledger(protocol, freeze, mixed_code),
            generator_record()), "provenance")
        omitted_source = copy.deepcopy(fragments)
        entries = omitted_source[0]["value"]["input_ledger"]["entries"]
        omitted = next(row["label"] for row in entries
                       if row["label"].startswith("corpus:"))
        entries = [row for row in entries if row["label"] != omitted]
        omitted_source[0]["value"]["input_ledger"] = ledger_record(
            entries, copy.deepcopy(entries))
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, omitted_source, freeze,
            _reducer_ledger(protocol, freeze, omitted_source),
            generator_record()), "complete exact source-file")
        extra_source = copy.deepcopy(fragments)
        entries = extra_source[0]["value"]["input_ledger"]["entries"] + [
            dict(label="corpus:invented.py", bytes=1, sha256="c" * 64)]
        entries.sort(key=lambda row: row["label"])
        extra_source[0]["value"]["input_ledger"] = ledger_record(
            entries, copy.deepcopy(entries))
        _expect_error(lambda: reduce_gate_values(
            protocol, candidates, pilot, extra_source, freeze,
            _reducer_ledger(protocol, freeze, extra_source),
            generator_record()), "complete exact source-file")


def test_reduced_validator_rejects_set_and_reason_tamper():
    with tempfile.TemporaryDirectory() as td:
        protocol, _, _, _, _, reduced = _reduced_fixture(td)
        bad = copy.deepcopy(reduced)
        bad["source_ineligible_keys"]["keys"] = []
        bad["source_ineligible_keys"]["n"] = 0
        bad["source_ineligible_keys"]["sha256"] = sha256_json([])
        _expect_error(lambda: validate_reduced_gate(bad, protocol),
                      "partition")
        bad = copy.deepcopy(reduced)
        bad["rows"][0]["ineligibility_reasons"] = ["invented"]
        bad["rows_sha256"] = sha256_sorted_json(bad["rows"])
        _expect_error(lambda: validate_reduced_gate(bad, protocol),
                      "eligibility/reason")
        bad = copy.deepcopy(reduced)
        bad["cross_check"]["rows"][0]["full_k4_rendering_bytes"] += 1
        bad["cross_check"]["rows_sha256"] = sha256_sorted_json(
            bad["cross_check"]["rows"])
        _expect_error(lambda: validate_reduced_gate(bad, protocol),
                      "optimized/full-render")
        bad = copy.deepcopy(reduced)
        audit_key = bad["cross_check"]["selection"]["keys"][0]
        row = next(row for row in bad["rows"] if row["key"] == audit_key)
        row["k4_rendering_bytes"] += 1
        row["k4_eligible"] = row["k4_rendering_bytes"] >= \
            bad["budget_bytes"]
        row["eligible"] = row["k4_eligible"] and row["k5_seed0_eligible"]
        row["ineligibility_reasons"] = \
            ([] if row["k4_eligible"] else ["k4-rendering-below-budget"]) + \
            ([] if row["k5_seed0_eligible"] else
             ["k5-seed0-rendering-below-budget"])
        bad["rows_sha256"] = sha256_sorted_json(bad["rows"])
        _expect_error(lambda: validate_reduced_gate(bad, protocol),
                      "disagrees with census")


def test_input_ledger_detects_toctou():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "input.bin")
        with open(path, "wb") as fh:
            fh.write(b"before")
        pre = capture_ledger([("input", path)])
        with open(path, "wb") as fh:
            fh.write(b"after")
        post = capture_ledger([("input", path)])
        _expect_error(lambda: ledger_record(pre, post), "drifted")


def test_bound_loader_rejects_raw_hash_and_schema_drift():
    # load_bound_json intentionally resolves only repo-contained paths.
    with tempfile.TemporaryDirectory(dir=BASE) as td:
        path = os.path.join(td, "bound.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "fixture_v1", "value": 1}, fh)
        rel = os.path.relpath(path, BASE).replace(os.sep, "/")
        binding = dict(path=rel, schema="fixture_v1",
                       sha256=sha256_file(path))
        value, observed, row = load_bound_json(
            binding, "fixture", "fixture_v1")
        assert value["value"] == 1 and observed == path and row == binding
        bad = dict(binding, sha256="0" * 64)
        _expect_error(lambda: load_bound_json(bad, "fixture", "fixture_v1"),
                      "digest drift")
        bad = dict(binding, schema="fixture_v2")
        _expect_error(lambda: load_bound_json(bad, "fixture", "fixture_v1"),
                      "schema drift")


def test_gate_source_has_no_model_bm25_or_sample_execution():
    for name in ("prepare_v2b_nll_confirmation_gate.py",
                 "v2b_nll_confirmation_context.py"):
        text = open(os.path.join(BASE, name), encoding="utf-8").read()
        assert "import torch" not in text
        assert "transformers" not in text
        assert "bm25_scores(" not in text
        assert "build_sample_plan(" not in text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
