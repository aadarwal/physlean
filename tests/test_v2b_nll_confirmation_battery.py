#!/usr/bin/env python3
"""Synthetic-only tests for the confirmation per-model battery."""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_v2b_nll_confirmation_assembly import (
    ASSEMBLY_SCHEMA, ASSEMBLY_STATE, CELL_ORDER, DIAGNOSTIC_CELLS,
    REQUIRED_CELLS, SAMPLE_SCHEMA_CONFIRMATION, SOURCE_GATE_SCHEMA,
)
from prepare_v2b_nll_confirmation_gate import protocol_record
from v2b_common import (
    V2BError, identity_key, sha256_bytes, sha256_json, sha256_sorted_json,
)
from v2b_nll_confirmation import load_protocol
from v2b_nll_confirmation_battery import (
    BATTERY_SCHEMA, BATTERY_STATE, CAUSAL_DOWNSTREAM_MIN_ABS,
    CAUSAL_POSITION, CAUSAL_PROTECTED_MAX_ABS, CHUNK_TOKENS, DEVICE, DTYPE,
    IMPLEMENTATION_FREEZE_SCHEMA, INSTRUMENT_SCHEMA, MODEL_FILES_SCHEMA,
    N_CELLS, N_TARGETS, PROGRAM, REPEAT_MAX_ABS, SYNTHETIC_TOKENS,
    TOKENIZER_FILES_SCHEMA, build_battery_value,
    build_tokenizer_fit_ledger, recommend_shards, snapshot_file_manifests,
    validate_battery,
)


def _expect_error(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation battery input"
    except V2BError as err:
        if text is not None:
            assert text in str(err), str(err)


class FakeTokenizer:
    def __init__(self):
        self.calls = []

    def __call__(self, text, add_special_tokens=False,
                 return_offsets_mapping=False):
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        self.calls.append(text)
        return {
            "input_ids": [ord(char) % 251 for char in text],
            "offset_mapping": [[index, index + 1]
                               for index in range(len(text))],
        }


def _body_ledger(text, offsets, body_start, token_ids):
    assert len(offsets) == len(token_ids) == len(text)
    body_bytes = len(text[body_start:].encode("utf-8"))
    return dict(
        exact_body_bytes=body_bytes, scored_body_bytes=body_bytes,
        straddled_body_bytes=0, n_boundary_straddle_tokens=0,
        boundary_signature=sha256_json([]))


def _artifact(name, schema, digit):
    return dict(path=f"/synthetic/{name}.json", schema=schema,
                sha256=digit * 64)


def _cell(cell_id, context, eligible=True):
    if cell_id == "k1":
        return dict(
            cell_id="k1", role="intrinsic-control",
            required_for_fixed_n=True, budget_bytes=None, eligible=True,
            eligibility_basis="intrinsic-empty-context",
            ineligibility_reason=None, rendering_bytes=0, context_bytes=0,
            context_sha256=sha256_bytes(b""), utf8_shortfall_bytes=None,
            n_ordered_units=0,
            ordered_unit_keys_sha256=sha256_json([]),
            unit_pool_keys_sha256=sha256_json([]))
    required = cell_id in REQUIRED_CELLS
    assert not (required and not eligible)
    return dict(
        cell_id=cell_id,
        role="required-primary" if required else "diagnostic",
        required_for_fixed_n=required, budget_bytes=16384,
        eligible=eligible,
        eligibility_basis="maximal-rendering-bytes-at-least-16384",
        ineligibility_reason=(None if eligible else
                              "maximal-rendering-below-16384-bytes"),
        rendering_bytes=16384 if eligible else 10,
        context_bytes=len(context) if eligible else None,
        context_sha256=sha256_bytes(context) if eligible else None,
        utf8_shortfall_bytes=0 if eligible else None,
        n_ordered_units=1,
        ordered_unit_keys_sha256=sha256_json(["u"]),
        unit_pool_keys_sha256=sha256_json(["u"]))


def _assembly_fixture():
    bindings = dict(
        implementation_freeze=_artifact(
            "freeze", IMPLEMENTATION_FREEZE_SCHEMA, "a"),
        bound_sample=_artifact(
            "sample", SAMPLE_SCHEMA_CONFIRMATION, "b"),
        source_gate=_artifact("gate", SOURCE_GATE_SCHEMA, "c"),
        assembly=_artifact("assembly", ASSEMBLY_SCHEMA, "d"))
    targets, materialized = [], {}
    for index in range(N_TARGETS):
        identity = [f"sympy.synthetic.m{index:03d}", f"f{index}", index]
        key = identity_key("python", identity)
        prefix = b"P"
        body = b"B"
        contexts = {}
        cells = []
        for cell_id in CELL_ORDER:
            eligible = not (cell_id == "k3:16384" and index % 7 == 0)
            context = b"" if cell_id == "k1" else \
                f"C:{cell_id}:{index}".encode()
            cell = _cell(cell_id, context, eligible)
            cells.append(cell)
            contexts[cell_id] = context if cell["eligible"] else None
        target = dict(
            key=key, identity=identity, module=identity[0],
            source_rel=f"sympy/synthetic/m{index:03d}.py",
            sample_cell="L1-D1-Cpre", sample_priority=f"{index + 1:064x}",
            prefix_bytes=len(prefix), prefix_sha256=sha256_bytes(prefix),
            body_bytes=len(body), body_sha256=sha256_bytes(body),
            static_reference_coverage=dict(
                n_refs=0, n_resolved_decl=0, n_module_fallback=0,
                n_external=0, n_unresolved=0, resolved_fraction=None,
                coverage_bin="no-references"),
            cells=cells, cells_sha256=sha256_sorted_json(cells))
        targets.append(target)
        materialized[key] = dict(prefix=prefix, body=body, cells=contexts)
    keys = [target["key"] for target in targets]
    protocol = load_protocol()[0]
    assembly = dict(
        schema=ASSEMBLY_SCHEMA, state=ASSEMBLY_STATE,
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        budget_bytes=16384, protocol=protocol_record(),
        bindings={name: copy.deepcopy(bindings[name]) for name in (
            "implementation_freeze", "bound_sample", "source_gate")},
        cell_order=list(CELL_ORDER), required_cells=list(REQUIRED_CELLS),
        diagnostic_cells=list(DIAGNOSTIC_CELLS), n_targets=N_TARGETS,
        ordered_target_keys=dict(n=N_TARGETS, sha256=sha256_json(keys),
                                 keys=keys),
        targets=targets, targets_sha256=sha256_sorted_json(targets))
    return protocol, bindings, assembly, materialized


def _file_manifests(revision):
    tokenizer_rows = sorted([
        dict(path="tokenizer.json", bytes=13, sha256="d" * 64),
        dict(path="tokenizer_config.json", bytes=7, sha256="e" * 64),
    ], key=lambda row: row["path"])
    model_rows = sorted([
        dict(path="config.json", bytes=5, sha256="a" * 64),
        dict(path="model.safetensors", bytes=11, sha256="b" * 64),
        *copy.deepcopy(tokenizer_rows),
    ], key=lambda row: row["path"])
    return (
        dict(schema=MODEL_FILES_SCHEMA, revision=revision,
             n_files=len(model_rows), files=model_rows,
             files_sha256=sha256_sorted_json(model_rows)),
        dict(schema=TOKENIZER_FILES_SCHEMA, revision=revision,
             n_files=len(tokenizer_rows), files=tokenizer_rows,
             files_sha256=sha256_sorted_json(tokenizer_rows)))


def _instrument(rate=4097.0, maximum_prompt_tokens=1):
    n_tokens = max(SYNTHETIC_TOKENS, maximum_prompt_tokens)
    seconds = (n_tokens - 1) / rate
    return dict(
        schema=INSTRUMENT_SCHEMA,
        state="complete-target-free-production-path",
        domain="v2b-nll-confirmation-target-free-synthetic-v1",
        contains_confirmation_target_bytes=False,
        dtype=DTYPE, device=DEVICE, chunk_tokens=CHUNK_TOKENS,
        n_tokens=n_tokens, seconds=seconds,
        tokens_per_second=rate,
        repeat_max_abs=0.0, repeat_threshold=REPEAT_MAX_ABS,
        causal_position=CAUSAL_POSITION,
        causal_n_protected=CAUSAL_POSITION - 1,
        causal_n_downstream=n_tokens - 1 - CAUSAL_POSITION,
        causal_protected_max_abs=0.0,
        causal_protected_threshold=CAUSAL_PROTECTED_MAX_ABS,
        causal_downstream_max_abs=1e-3,
        causal_downstream_minimum=CAUSAL_DOWNSTREAM_MIN_ABS,
        token_byte_conservation=True,
        peak_memory_allocated_bytes=100,
        peak_memory_reserved_bytes=200,
        device_total_memory_bytes=1000,
        peak_reserved_fraction=0.2, maximum_memory_fraction=0.95,
        maximum_eligible_prompt_tokens=maximum_prompt_tokens,
        covers_maximum_eligible_prompt_tokens=True,
        passed=True)


def _ledger(protocol_binding, bindings, provenance):
    digests = {
        "input:assembly": bindings["assembly"]["sha256"],
        "input:bound_sample": bindings["bound_sample"]["sha256"],
        "input:environment_freeze": provenance[
            "environment_freeze_sha256"],
        "input:implementation_freeze": bindings[
            "implementation_freeze"]["sha256"],
        "input:protocol": protocol_binding["raw_sha256"],
        "input:requirements_lock": provenance[
            "requirements_lock_sha256"],
        "input:source_gate": bindings["source_gate"]["sha256"],
    }
    entries = [dict(label=label, bytes=index + 1, sha256=digests[label])
               for index, label in enumerate(sorted(digests))]
    digest = sha256_sorted_json(entries)
    return dict(
        algorithm="sha256-sorted-json-file-ledger-v1",
        n_entries=len(entries), entries=entries, entries_sha256=digest,
        pre_entries_sha256=digest, post_entries_sha256=digest,
        unchanged=True)


def _build_args(model_id="q25c-1.5b"):
    protocol, bindings, assembly, materialized = _assembly_fixture()
    model = next(row for row in protocol["models"] if row["id"] == model_id)
    runtime = dict(
        model_id=model["id"], model_name=model["name"],
        revision=model["revision"], model_class="Qwen2ForCausalLM",
        n_parameters=1_500_000_000, tokenizer_class="Qwen2TokenizerFast",
        vocab_size=151936, max_position_embeddings=32768,
        attention=dict(implementation="sdpa", model_type="qwen2",
                       sliding_window=None, layer_types="None"))
    provenance = dict(
        environment_fingerprint="1" * 64,
        requirements_lock_sha256="2" * 64,
        environment_freeze_sha256="3" * 64,
        environment_lock_matches=True, environment_freeze_matches=True,
        measurement_harness_sha256="4" * 64,
        numerical_harness_sha256="5" * 64,
        source_commit="6" * 40, source_tree_hash="7" * 64,
        gpu=dict(gpu_name="Synthetic L40S", gpu_driver="synthetic"))
    freeze = dict(
        schema=IMPLEMENTATION_FREEZE_SCHEMA,
        study_id=protocol["study_id"], protocol=protocol_record(), files=[])
    generator = dict(
        program=PROGRAM, program_sha256="8" * 64,
        source_commit=provenance["source_commit"],
        source_tree_hash=provenance["source_tree_hash"])
    model_files, tokenizer_files = _file_manifests(model["revision"])
    maximum_prompt_tokens = max(
        len(blobs["prefix"] + blobs["body"] + context)
        for blobs in materialized.values()
        for context in blobs["cells"].values() if context is not None)
    return dict(
        protocol=protocol, protocol_binding=protocol_record(), freeze=freeze,
        bindings=bindings, assembly=assembly, materialized=materialized,
        model_id=model_id, tokenizer=FakeTokenizer(),
        model_files=model_files, tokenizer_files=tokenizer_files,
        runtime=runtime,
        synthetic_instrument=_instrument(
            maximum_prompt_tokens=maximum_prompt_tokens),
        execution_provenance=provenance,
        input_ledger=_ledger(protocol_record(), bindings, provenance),
        generator=generator, body_ledger_fn=_body_ledger)


def test_exact_four_models_grid_bindings_and_determinism():
    protocol = load_protocol()[0]
    expected_ids = [row["id"] for row in protocol["models"]]
    assert expected_ids == ["q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b"]
    for model_id in expected_ids:
        args = _build_args(model_id)
        value = build_battery_value(**args)
        again_args = _build_args(model_id)
        again = build_battery_value(**again_args)
        assert value == again
        assert value["schema"] == BATTERY_SCHEMA
        assert value["state"] == BATTERY_STATE
        assert value["model"]["id"] == model_id
        assert set(value["bindings"]) == {
            "implementation_freeze", "bound_sample", "source_gate",
            "assembly"}
        assert value["tokenizer_fit"]["n_targets"] == N_TARGETS
        assert value["tokenizer_fit"]["n_cell_records"] == \
            N_TARGETS * N_CELLS
        assert value["sharding"]["recommended_shard_count"] == 1
        assert validate_battery(
            value, args["protocol"], args["bindings"], args["assembly"]) \
            is value


def test_token_overflow_aborts_without_redraw():
    _, _, assembly, materialized = _assembly_fixture()
    _expect_error(lambda: build_tokenizer_fit_ledger(
        assembly, materialized, FakeTokenizer(), 2, 251, _body_ledger),
        "exceeding model maximum")

    class OutOfVocabularyTokenizer(FakeTokenizer):
        def __call__(self, text, add_special_tokens=False,
                     return_offsets_mapping=False):
            value = super().__call__(
                text, add_special_tokens=add_special_tokens,
                return_offsets_mapping=return_offsets_mapping)
            value["input_ids"][0] = 251
            return value

    _expect_error(lambda: build_tokenizer_fit_ledger(
        assembly, materialized, OutOfVocabularyTokenizer(), 32768, 251,
        _body_ledger), "malformed")


def test_ineligible_diagnostic_is_recorded_but_never_tokenized():
    args = _build_args()
    tokenizer = args["tokenizer"]
    value = build_battery_value(**args)
    fit = value["tokenizer_fit"]
    omitted = [row for row in fit["rows"]
               if row["status"] ==
               "structurally-ineligible-not-tokenized"]
    assert omitted
    assert all(row["cell_id"] == "k3:16384" for row in omitted)
    assert all(row["prompt_sha256"] is None
               and row["n_prompt_tokens"] is None for row in omitted)
    assert len(tokenizer.calls) == fit["n_tokenized_prompts"]


def test_k1_is_intrinsic_valid_for_all_200_targets():
    value = build_battery_value(**_build_args())
    k1 = [row for row in value["tokenizer_fit"]["rows"]
          if row["cell_id"] == "k1"]
    assert len(k1) == N_TARGETS
    assert all(row["status"] == "tokenized"
               and row["context_bytes"] == 0
               and row["context_sha256"] == sha256_bytes(b"")
               for row in k1)
    assert value["tokenizer_fit"]["required_eligible_n_by_cell"] == {
        cell: N_TARGETS for cell in REQUIRED_CELLS}


def test_stale_assembly_freeze_and_model_fail_closed():
    bad = _build_args()
    bad["assembly"]["bindings"]["implementation_freeze"]["sha256"] = \
        "0" * 64
    _expect_error(lambda: build_battery_value(**bad), "freeze")
    bad = _build_args()
    bad["freeze"]["protocol"]["semantic_sha256"] = "0" * 64
    _expect_error(lambda: build_battery_value(**bad), "freeze")
    bad = _build_args()
    bad["runtime"]["revision"] = "0" * 40
    _expect_error(lambda: build_battery_value(**bad), "runtime")
    bad = _build_args()
    bad["tokenizer_files"]["files"][0]["sha256"] = "0" * 64
    _expect_error(lambda: build_battery_value(**bad), "hash drift")
    bad = _build_args()
    bad["tokenizer_files"]["files"] = bad["tokenizer_files"]["files"][1:]
    bad["tokenizer_files"]["n_files"] = len(
        bad["tokenizer_files"]["files"])
    bad["tokenizer_files"]["files_sha256"] = sha256_sorted_json(
        bad["tokenizer_files"]["files"])
    _expect_error(lambda: build_battery_value(**bad), "not one exact")
    bad = _build_args()
    bad["input_ledger"]["entries"] = bad["input_ledger"]["entries"][1:]
    bad["input_ledger"]["n_entries"] -= 1
    digest = sha256_sorted_json(bad["input_ledger"]["entries"])
    for name in ("entries_sha256", "pre_entries_sha256",
                 "post_entries_sha256"):
        bad["input_ledger"][name] = digest
    _expect_error(lambda: build_battery_value(**bad), "ledger")
    bad = _build_args()
    bad["synthetic_instrument"]["tokens_per_second"] *= 2
    _expect_error(lambda: build_battery_value(**bad), "instrument")
    bad = _build_args()
    key = bad["assembly"]["targets"][0]["key"]
    bad["materialized"][key]["cells"]["k4:16384"] = b"tampered"
    _expect_error(lambda: build_battery_value(**bad), "bytes/hash drift")


def test_snapshot_and_tokenizer_file_hash_manifests_are_exact():
    revision = "a" * 40
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, revision)
        os.mkdir(root)
        files = {
            "config.json": b"{}",
            "model.safetensors": b"model",
            "tokenizer_config.json": b"{}",
            "tokenizer.json": b"tokenizer",
        }
        for name, blob in files.items():
            with open(os.path.join(root, name), "wb") as handle:
                handle.write(blob)
        model, tokenizer = snapshot_file_manifests(root, revision)
        assert model == snapshot_file_manifests(root, revision)[0]
        assert model["n_files"] == 4
        assert tokenizer["n_files"] == 2
        by_path = {row["path"]: row for row in model["files"]}
        assert by_path["model.safetensors"]["sha256"] == \
            sha256_bytes(b"model")
        assert all(row == by_path[row["path"]]
                   for row in tokenizer["files"])


def test_shard_recommendation_is_deterministic_and_benchmark_bound():
    one = recommend_shards(1_000_000, _instrument(rate=10_000.0))
    assert one == recommend_shards(1_000_000, _instrument(rate=10_000.0))
    assert one["recommended_shard_count"] == 1
    many = recommend_shards(1_000_000, _instrument(rate=10.0))
    assert many["recommended_shard_count"] > 1
    assert many["decision_reason"] == "benchmark-requires-multiple-shards"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B CONFIRMATION BATTERY TESTS PASS")
