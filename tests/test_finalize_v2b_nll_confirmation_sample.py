#!/usr/bin/env python3
"""Pure synthetic tests for the fresh SymPy confirmation sampler.

No sealed candidate/gate/pilot artifact is read and no production draw is
performed.  The fixtures exercise the exact reduced-gate consumer contract,
the frozen full-table sampler, and the N/module-support aborts.
"""
import copy
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import finalize_v2b_nll_confirmation_sample as sample_module
from finalize_v2b_nll_confirmation_sample import (
    BUDGET_BYTES, CONFIRMATION_SAMPLE_SCHEMA,
    IMPLEMENTATION_FREEZE_SCHEMA, N_CONFIRMATION,
    build_confirmation_sample,
)
from prepare_v2b_nll_confirmation_gate import (
    AUDIT_DOMAIN, AUDIT_SCHEMA, CONTEXT_PROGRAM, FRAGMENT_SCHEMA,
    GATE_SCHEMA, GATE_STATE, PROGRAM, _audit_selection, key_set,
    protocol_record,
)
from v2b_common import (
    BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA, V2BError, identity_key,
    sha256_json, sha256_sorted_json,
)
from v2b_metadata import (
    SAMPLING_SEED, cohort_of, seeded_hash, tercile, tercile_cutpoints,
)
from v2b_nll_confirmation import (
    load_protocol,
)


def _expect_error(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation sample input"
    except V2BError as err:
        if text is not None:
            assert text in str(err), str(err)


def _module(index, fresh_module_count=None):
    if index < 2:
        return "pilot_00"
    if index < 20:
        return f"pilot_{index - 1:02d}"
    suffix = index if fresh_module_count is None \
        else (index - 20) % fresh_module_count
    return f"fresh_{suffix:04d}"


def _candidate_table(n, fresh_module_count=None):
    rows = []
    for index in range(n):
        identity = [_module(index, fresh_module_count), f"f{index}", index]
        first_add = dict(
            timestamp_utc=("2023-05-01T00:00:00+00:00" if index % 2
                           else "2025-01-01T00:00:00+00:00"),
            provenance_mode="exact-add", exact_add_unresolved=False,
            n_add_records=1)
        rows.append(dict(
            identity=identity, body_bytes=100 + index,
            module_in_degree=index % 17,
            source_rel=f"sympy/synthetic_{index:04d}.py",
            first_add=first_add))
    length_cuts = tercile_cutpoints([row["body_bytes"] for row in rows])
    degree_cuts = tercile_cutpoints(
        [row["module_in_degree"] for row in rows])
    for row in rows:
        cohort = cohort_of(row["first_add"])
        length = tercile(row["body_bytes"], *length_cuts)
        degree = tercile(row["module_in_degree"], *degree_cuts)
        row["cohort"] = cohort
        row["strata"] = dict(length_tercile=length,
                             centrality_tercile=degree, cohort=cohort)
        row["cell"] = f"L{length}-D{degree}-C{cohort}"
        row["priority"] = seeded_hash(
            SAMPLING_SEED, "sympy", *row["identity"])
    return dict(
        schema=CANDIDATES_SCHEMA, repo="sympy", language="python",
        corpus_git_sha="c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
        git_version="git version synthetic",
        cohort_cutoff="2024-11-12T00:00:00+00:00",
        tercile_cutpoints=dict(body_bytes=list(length_cuts),
                               module_in_degree=list(degree_cuts)),
        first_add_provenance_file_counts={
            "exact-add": n, "no-add-pre-witness": 0},
        no_add_pre_witness_files=[], lean_boundaries=None,
        n_candidates=n, targets=rows,
        structural_evidence=dict(lean_boundaries=None),
        generator=dict(program="prepare_v2b_candidates.py",
                       source_commit="1" * 40,
                       source_tree_hash="2" * 64))


def _fixture(n=240, n_ineligible=10, fresh_module_count=None):
    if n < 20 or n_ineligible < 0 or n_ineligible > n - 20:
        raise AssertionError("invalid synthetic fixture size")
    protocol = copy.deepcopy(load_protocol()[0])
    candidates = _candidate_table(n, fresh_module_count)
    candidate_binding = dict(
        path="/tmp/synthetic/candidates.json", schema=CANDIDATES_SCHEMA,
        sha256="a" * 64)
    pilot_binding = dict(
        path="/tmp/synthetic/pilot.json", schema=BOUND_SAMPLE_SCHEMA,
        sha256="b" * 64)
    freeze_binding = dict(
        path="/tmp/synthetic/implementation-freeze.json",
        schema=IMPLEMENTATION_FREEZE_SCHEMA, sha256="c" * 64)
    gate_binding = dict(
        path="/tmp/synthetic/source-gate.json", schema=GATE_SCHEMA,
        sha256="d" * 64)
    protocol_binding = protocol_record()

    protocol["source_eligibility_gate"]["candidate_universe_n"] = n
    protocol["inputs"]["candidates"] = dict(
        path="synthetic/candidates.json", schema=CANDIDATES_SCHEMA,
        sha256=candidate_binding["sha256"])
    protocol["inputs"]["pilot_sample"] = dict(
        path="synthetic/pilot.json", schema=BOUND_SAMPLE_SCHEMA,
        sha256=pilot_binding["sha256"])
    protocol["source_eligibility_gate"]["bindings"]["candidates"] = \
        copy.deepcopy(protocol["inputs"]["candidates"])

    pilot_targets = [dict(identity=copy.deepcopy(
        candidates["targets"][index]["identity"])) for index in range(20)]
    pilot = dict(
        schema=BOUND_SAMPLE_SCHEMA, sampling_state="drawn",
        plans={"sympy": dict(
            repo="sympy", language="python",
            candidates_sha256=candidate_binding["sha256"],
            targets=pilot_targets)})
    pilot_keys = sorted(identity_key("python", row["identity"])
                        for row in pilot_targets)
    pilot_modules = sorted({row["identity"][0] for row in pilot_targets})
    protocol["inputs"]["pilot_sympy_target_count"] = len(pilot_keys)
    protocol["inputs"]["pilot_sympy_keys_sha256"] = sha256_json(pilot_keys)
    protocol["inputs"]["pilot_sympy_module_count"] = len(pilot_modules)
    protocol["inputs"]["pilot_sympy_modules_sha256"] = sha256_json(
        pilot_modules)

    by_key = {identity_key("python", row["identity"]): row
              for row in candidates["targets"]}
    ordered_keys = sorted(by_key)
    ineligible = set(sorted(
        (key for key in ordered_keys
         if by_key[key]["identity"][0] not in set(pilot_modules)),
        reverse=True)[:n_ineligible])
    rows = []
    for key in ordered_keys:
        identity = by_key[key]["identity"]
        eligible = key not in ineligible
        rows.append(dict(
            key=key, identity=copy.deepcopy(identity), module=identity[0],
            k4_rendering_bytes=(BUDGET_BYTES if eligible else 0),
            k4_eligible=eligible,
            k5_seed0_rendering_bytes=(BUDGET_BYTES if eligible else 0),
            k5_seed0_eligible=eligible, eligible=eligible,
            ineligibility_reasons=([] if eligible else [
                "k4-rendering-below-budget",
                "k5-seed0-rendering-below-budget"])))
    eligible_keys = [row["key"] for row in rows if row["eligible"]]
    pilot_intersection = sorted(set(eligible_keys) & set(pilot_keys))
    pilot_module_candidates = sorted(
        key for key, row in by_key.items()
        if row["identity"][0] in set(pilot_modules))
    post_pilot = sorted(set(eligible_keys) - set(pilot_module_candidates))
    protocol["source_eligibility_gate"][
        "pilot_intersection_expected_from_sealed_pilot_evidence"] = dict(
            count=len(pilot_intersection),
            keys_sha256=sha256_json(pilot_intersection),
            meaning="synthetic audit expectation")
    gate_bindings = dict(
        protocol["source_eligibility_gate"]["bindings"],
        pilot_sample=copy.deepcopy(protocol["inputs"]["pilot_sample"]))
    # The source-gate producer now binds this exact prospective freeze too.
    gate_bindings["implementation_freeze"] = dict(
        path=freeze_binding["path"],
        schema=IMPLEMENTATION_FREEZE_SCHEMA,
        sha256=freeze_binding["sha256"])
    ledger_digests = {
        "input:protocol": protocol_record()["raw_sha256"],
        "input:implementation_freeze": freeze_binding["sha256"],
        "input:candidates": candidate_binding["sha256"],
        "input:pilot_sample": pilot_binding["sha256"],
        "fragment:000000": "e" * 64,
    }
    ledger_entries = [dict(label=label, bytes=index + 1,
                           sha256=ledger_digests[label])
                      for index, label in enumerate(sorted(ledger_digests))]
    ledger_digest = sha256_sorted_json(ledger_entries)
    audit_keys = _audit_selection(ordered_keys)
    row_by_key = {row["key"]: row for row in rows}
    audit_rows = []
    for key in audit_keys:
        row = row_by_key[key]
        audit_rows.append(dict(
            key=key,
            bitset_k4_rendering_bytes=row["k4_rendering_bytes"],
            full_k4_rendering_bytes=row["k4_rendering_bytes"],
            bitset_k5_seed0_rendering_bytes=
            row["k5_seed0_rendering_bytes"],
            full_k5_seed0_rendering_bytes=
            row["k5_seed0_rendering_bytes"],
            k4_eligible=row["k4_eligible"],
            k5_seed0_eligible=row["k5_seed0_eligible"],
            eligible=row["eligible"], passed=True))
    cross_check = dict(
        schema=AUDIT_SCHEMA,
        selection=dict(domain=AUDIT_DOMAIN, n=len(audit_keys),
                       sha256=sha256_json(audit_keys), keys=audit_keys),
        reference=("canonical_dependency_order+k5_unit_order(seed=0)+"
                   "render_chunks"),
        rows=audit_rows, rows_sha256=sha256_sorted_json(audit_rows),
        passed=True)
    gate = dict(
        schema=GATE_SCHEMA, state=GATE_STATE,
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=BUDGET_BYTES, protocol=protocol_record(),
        bindings=gate_bindings,
        input_ledger=dict(
            algorithm="sha256", n_entries=len(ledger_entries),
            entries=ledger_entries, entries_sha256=ledger_digest,
            pre_entries_sha256=ledger_digest,
            post_entries_sha256=ledger_digest, unchanged=True),
        graph_index=dict(
            method="scc-condensation-python-int-bitset-additive-render-mass-v1",
            n_units=n, n_edges=0, n_scc=n, max_scc_size=1,
            n_source_files=1, source_labels_sha256="9" * 64),
        fragments=[dict(
            path="fragment-0.json", sha256="e" * 64,
            schema=FRAGMENT_SCHEMA, shard_index=0, shard_count=1,
            candidate_index_start=0, candidate_index_end=n,
            candidate_keys_sha256=sha256_json(ordered_keys),
            rows_sha256=sha256_sorted_json(rows))],
        candidate_keys=key_set(ordered_keys),
        eligible_keys=key_set(eligible_keys),
        source_ineligible_keys=key_set(ineligible),
        pilot_key_intersection=key_set(pilot_intersection),
        pilot_modules=key_set(pilot_modules),
        pilot_module_candidate_keys=key_set(pilot_module_candidates),
        post_pilot_eligible_keys=key_set(post_pilot),
        cross_check=cross_check, n_rows=n, rows=rows,
        rows_sha256=sha256_sorted_json(rows),
        generator=dict(
            program=PROGRAM, program_sha256="f" * 64,
            context_program=CONTEXT_PROGRAM,
            context_program_sha256="0" * 64,
            source_commit="1" * 40, source_tree_hash="2" * 64))
    freeze = dict(
        schema=IMPLEMENTATION_FREEZE_SCHEMA, study_id=protocol["study_id"],
        protocol=protocol_record(), files=[])
    return dict(
        protocol=protocol, protocol_binding=protocol_binding,
        gate=gate, gate_binding=gate_binding, candidates=candidates,
        candidate_binding=candidate_binding, pilot=pilot,
        pilot_binding=pilot_binding, implementation_freeze=freeze,
        implementation_freeze_binding=freeze_binding)


def test_exact_n_deterministic_module_disjoint_draw():
    fixture = _fixture()
    sample = build_confirmation_sample(**fixture)
    assert sample == build_confirmation_sample(**fixture)
    assert sample["schema"] == CONFIRMATION_SAMPLE_SCHEMA
    assert sample["protocol"] == protocol_record()
    assert sample["realized_n"] == sample["requested_n"] == N_CONFIRMATION
    assert sample["plan"]["n_selected"] == N_CONFIRMATION
    assert sample["plan"]["shortfalls"] == {}
    assert set(sample["selected_modules"]["keys"]).isdisjoint(
        sample["exclusion_bindings"]["pilot_modules"]["keys"])
    assert sample["cluster_support"]["n_modules"] >= 20
    assert sample["cluster_support"]["effective_clusters"] >= 10
    assert sample["cluster_support"]["passed"] is True
    assert set(sample["bindings"]) == {
        "source_gate", "candidates", "pilot_sample",
        "implementation_freeze"}
    assert all(set(row) == {"path", "schema", "sha256"}
               for row in sample["bindings"].values())
    assert all(set(row) == {"identity", "cell", "priority"}
               for row in sample["plan"]["targets"])


def test_gate_set_tamper_fails_closed():
    fixture = _fixture()
    bad = copy.deepcopy(fixture)
    bad["gate"]["source_ineligible_keys"] = key_set([])
    _expect_error(lambda: build_confirmation_sample(**bad), "partition")


def test_post_gate_shortfall_aborts_before_draw():
    fixture = _fixture(n=240, n_ineligible=21)
    assert fixture["gate"]["post_pilot_eligible_keys"]["n"] == 199
    _expect_error(lambda: build_confirmation_sample(**fixture),
                  "fewer than 200")


def test_inadequate_pre_score_cluster_support_aborts():
    fixture = _fixture(n=240, n_ineligible=0, fresh_module_count=10)
    _expect_error(lambda: build_confirmation_sample(**fixture),
                  "cluster support")


def test_sampler_rejects_forced_pilot_module_overlap():
    fixture = _fixture()
    good = build_confirmation_sample(**fixture)
    bad_plan = copy.deepcopy(good["plan"])
    selected = {identity_key("python", row["identity"])
                for row in bad_plan["targets"]}
    pilot_target = fixture["pilot"]["plans"]["sympy"]["targets"][0]
    pilot_key = identity_key("python", pilot_target["identity"])
    assert pilot_key not in selected
    bad_plan["targets"][0]["identity"] = copy.deepcopy(
        pilot_target["identity"])
    with mock.patch.object(sample_module, "build_sample_plan",
                           return_value=bad_plan):
        _expect_error(lambda: build_confirmation_sample(**fixture),
                      "post-pilot population")


def test_pilot_and_freeze_binding_tamper_fail_closed():
    fixture = _fixture()
    bad_pilot = copy.deepcopy(fixture)
    bad_pilot["pilot"]["plans"]["sympy"]["targets"][0]["identity"] = \
        copy.deepcopy(bad_pilot["candidates"]["targets"][25]["identity"])
    _expect_error(lambda: build_confirmation_sample(**bad_pilot),
                  "exclusion binding")
    bad_freeze = copy.deepcopy(fixture)
    bad_freeze["implementation_freeze"]["protocol"][
        "semantic_sha256"] = "9" * 64
    _expect_error(lambda: build_confirmation_sample(**bad_freeze),
                  "implementation freeze")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B CONFIRMATION SAMPLER TESTS PASS")
