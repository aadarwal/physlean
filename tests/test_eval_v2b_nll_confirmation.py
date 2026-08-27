#!/usr/bin/env python3
"""Synthetic tests for target-atomic confirmation scoring and reducers."""
import copy
import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval_v2b_nll_confirmation import (
    ASSEMBLY_SCHEMA, BATTERY_SCHEMA, CELL_ORDER, FREEZE_SCHEMA,
    MODEL_BY_ID, MODEL_IDS, PROGRAM, SALT_SCHEMA, SAMPLE_SCHEMA,
    SOURCE_GATE_SCHEMA, _target_files, _write_new_0600, build_model_complete,
    build_study_complete, build_target_score, compatible_target,
    normalize_battery, salt_sequence, _verify_runtime)
from v2b_common import (V2BError, identity_key, sha256_bytes, sha256_json,
                        sha256_file, sha256_sorted_json)
from v2b_nll_confirmation import load_protocol


_BASE = None
_TARGET_CACHE = {}
_MODEL_CACHE = {}


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation scoring evidence"
    except V2BError as err:
        if text is not None:
            assert text in str(err), str(err)


def _binding(schema, digit):
    return dict(path=f"/sealed/{schema}-{digit}.json", schema=schema,
                sha256=digit * 64)


def _execution(model_id):
    model = MODEL_BY_ID[model_id]
    return dict(
        model_id=model_id, model_name=model["name"],
        revision=model["revision"], dtype="bfloat16", device="cuda",
        attention=dict(implementation="sdpa", model_type="qwen2",
                       sliding_window=None, layer_types=None),
        chunk_tokens=1024,
        max_position_embeddings=32768,
        environment_fingerprint="1" * 64, source_tree_hash="d" * 64,
        requirements_lock_sha256="5" * 64,
        environment_freeze_sha256="6" * 64,
        environment_lock_matches=True, environment_freeze_matches=True,
        measurement_harness_sha256="7" * 64,
        numerical_harness_sha256="2" * 64,
        battery_source_commit="8" * 40,
        gpu=dict(gpu_name="Synthetic GPU", gpu_driver="0"),
        model_snapshot_sha256="3" * 64,
        tokenizer_snapshot_sha256="4" * 64,
        model_class="SyntheticCausalLM", n_parameters=123456,
        tokenizer_class="SyntheticTokenizer", vocab_size=32000)


def _bindings(model_id):
    batteries = [_binding(BATTERY_SCHEMA, str(index + 5))
                 for index in range(4)]
    return dict(
        implementation_freeze=_binding(FREEZE_SCHEMA, "1"),
        source_gate=_binding(SOURCE_GATE_SCHEMA, "2"),
        bound_sample=_binding(SAMPLE_SCHEMA, "3"),
        assembly=_binding(ASSEMBLY_SCHEMA, "4"),
        model_battery=copy.deepcopy(
            batteries[MODEL_IDS.index(model_id)]),
        all_model_batteries=batteries,
        salt_commitment=_binding(SALT_SCHEMA, "9"))


def _generator():
    return dict(program=PROGRAM, program_sha256=sha256_file(
                    os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))), PROGRAM)),
                source_commit="b" * 40, source_tree_hash="d" * 64)


def _sequence():
    return salt_sequence("a" * 40, "b" * 40,
                         ancestor_fn=lambda _older, _newer: True)


def _base():
    global _BASE
    if _BASE is not None:
        return _BASE
    protocol, _ = load_protocol()
    targets = []
    materialized = {}
    fit = {}
    context_by_cell = {
        "k1": b"", "k3:16384": None, "k4:16384": b"dependency\n",
        "k5:0:16384": b"random-zero\n",
        "k5:1:16384": b"random-one\n",
        "k5:2:16384": b"random-two\n"}
    for index in range(200):
        identity = [f"pkg/m{index:03d}.py", f"target_{index}", 0]
        key = identity_key("python", identity)
        prefix = f"def target_{index}():".encode()
        body = f"\n    return {index}\n".encode()
        cells = []
        concrete = {}
        for cell_id in CELL_ORDER:
            context = context_by_cell[cell_id]
            eligible = context is not None
            cell = dict(
                cell_id=cell_id, eligible=eligible,
                eligibility_basis=("intrinsic-empty-context" if
                                   cell_id == "k1" else
                                   "maximal-rendering-bytes-at-least-16384"),
                ineligibility_reason=(None if eligible else
                                      "maximal-rendering-below-16384-bytes"),
                context_sha256=sha256_bytes(context) if eligible else None,
                context_bytes=len(context) if eligible else None)
            cells.append(cell)
            concrete[cell_id] = context
            prompt = context + prefix + body if eligible else None
            fit[(key, cell_id)] = dict(
                target_key=key, cell_id=cell_id, eligible=eligible,
                status=("tokenized" if eligible else
                        "structurally-ineligible-not-tokenized"),
                prompt_sha256=sha256_bytes(prompt) if eligible else None,
                prompt_bytes=len(prompt) if eligible else None,
                n_prompt_tokens=10 if eligible else None,
                exact_body_bytes=len(body) if eligible else None,
                scored_body_bytes=len(body) if eligible else None,
                straddled_body_bytes=0 if eligible else None,
                n_boundary_straddle_tokens=0 if eligible else None,
                boundary_signature="e" * 64 if eligible else None)
        targets.append(dict(
            key=key, identity=identity, module=identity[0],
            prefix_sha256=sha256_bytes(prefix), prefix_bytes=len(prefix),
            body_sha256=sha256_bytes(body), body_bytes=len(body), cells=cells))
        materialized[key] = dict(prefix=prefix, body=body, cells=concrete)
    keys = [row["key"] for row in targets]
    assembly = dict(
        schema=ASSEMBLY_SCHEMA, targets=targets,
        ordered_target_keys=dict(n=200, sha256=sha256_json(keys), keys=keys))
    _BASE = dict(protocol=protocol, assembly=assembly,
                 materialized=materialized, fit=fit)
    return _BASE


def _scorer(context, prefix, body, cell_id, _execution_identity):
    prompt = context + prefix + body
    body_chars = len(body.decode("utf-8"))
    ledger = dict(
        schema="v2b_body_token_ledger_v1", paired_schema_version="v4",
        exact_body_bytes=len(body), exact_body_codepoints=body_chars,
        scored_body_bytes=len(body), scored_body_codepoints=body_chars,
        straddled_body_bytes=0, straddled_body_codepoints=0,
        n_boundary_straddle_tokens=0, primary_token_indices=[1, 2],
        boundary_token_indices=[], inclusive_token_indices=[1, 2],
        boundary_groups=[], boundary_signature="e" * 64)
    return dict(
        prompt_sha256=sha256_bytes(prompt), prompt_bytes=len(prompt),
        n_prompt_tokens=10, body_layout_signature="f" * 64,
        body_token_ledger=ledger,
        nll_nats=float(len(prompt) + CELL_ORDER.index(cell_id)) / 10.0,
        n_scored_body_tokens=2)


def _target(model_id="q25c-1.5b", index=0, shard_count=2):
    cache_key = model_id, index, shard_count
    if cache_key not in _TARGET_CACHE:
        base = _base()
        key = base["assembly"]["targets"][index]["key"]
        _TARGET_CACHE[cache_key] = build_target_score(
            base["protocol"], _bindings(model_id), MODEL_BY_ID[model_id],
            _execution(model_id), _sequence(), base["assembly"], index,
            base["materialized"][key], base["fit"],
            0 if index < 100 else 1, shard_count, _scorer, _generator())
    return copy.deepcopy(_TARGET_CACHE[cache_key])


def _model_complete(model_id):
    if model_id not in _MODEL_CACHE:
        base = _base()
        values = [_target(model_id, index) for index in range(200)]
        inputs = [dict(path=f"/scores/{model_id}/target-{index:04d}.json",
                       sha256=sha256_sorted_json(value), value=value)
                  for index, value in enumerate(values)]
        _MODEL_CACHE[model_id] = build_model_complete(
            base["protocol"], base["assembly"], _bindings(model_id),
            MODEL_BY_ID[model_id], _execution(model_id), _sequence(), 2,
            inputs, _generator(), ancestor_fn=lambda _a, _b: True)
    return copy.deepcopy(_MODEL_CACHE[model_id])


def test_exact_six_cells_ineligible_has_no_numeric_fields_and_ledger_binds():
    value = _target()
    assert value["cell_order"] == list(CELL_ORDER)
    assert [row["cell_id"] for row in value["cells"]] == list(CELL_ORDER)
    by_id = {row["cell_id"]: row for row in value["cells"]}
    k3 = by_id["k3:16384"]
    assert k3["status"] == "structurally-ineligible-not-scored"
    assert all(name not in k3 for name in (
        "nll_nats", "context_bytes", "prompt_bytes", "n_prompt_tokens",
        "scored_body_bytes", "n_scored_body_tokens"))
    k4 = by_id["k4:16384"]
    assert k4["status"] == "scored"
    assert k4["scored_body_bytes"] == value["body_bytes"]
    assert k4["n_scored_body_tokens"] == 2
    assert k4["body_token_ledger"]["exact_body_bytes"] == \
        value["body_bytes"]


def test_ineligible_context_and_token_conservation_refuse_scoring():
    base = _base()
    index = 0
    key = base["assembly"]["targets"][index]["key"]
    concrete = copy.deepcopy(base["materialized"][key])
    concrete["cells"]["k3:16384"] = b"fabricated-short-context"
    _reject(lambda: build_target_score(
        base["protocol"], _bindings("q25c-1.5b"),
        MODEL_BY_ID["q25c-1.5b"], _execution("q25c-1.5b"), _sequence(),
        base["assembly"], index, concrete, base["fit"], 0, 2, _scorer,
        _generator()), "ineligible cell exposed context")

    def bad_scorer(*args):
        result = _scorer(*args)
        result["body_token_ledger"]["scored_body_bytes"] -= 1
        return result
    _reject(lambda: build_target_score(
        base["protocol"], _bindings("q25c-1.5b"),
        MODEL_BY_ID["q25c-1.5b"], _execution("q25c-1.5b"), _sequence(),
        base["assembly"], index, base["materialized"][key], base["fit"],
        0, 2, bad_scorer, _generator()), "conservation")


def test_atomic_mode0600_resume_and_tamper_refusal():
    base = _base()
    value = _target()
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "target-0000.json")
        first_digest = _write_new_0600(path, value)
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
        binding = compatible_target(
            path, base["protocol"], base["assembly"], 0,
            _bindings("q25c-1.5b"), MODEL_BY_ID["q25c-1.5b"],
            _execution("q25c-1.5b"), 2,
            ancestor_fn=lambda _a, _b: True,
            fit_by_pair=base["fit"])
        assert binding["sha256"] == first_digest
        tampered = copy.deepcopy(value)
        tampered["cells"][2]["nll_nats"] += 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(tampered, handle, sort_keys=True)
        os.chmod(path, 0o600)
        _reject(lambda: compatible_target(
            path, base["protocol"], base["assembly"], 0,
            _bindings("q25c-1.5b"), MODEL_BY_ID["q25c-1.5b"],
            _execution("q25c-1.5b"), 2,
            ancestor_fn=lambda _a, _b: True), "cell table/hash drift")

        fit_tampered = copy.deepcopy(value)
        fit_tampered["cells"][2]["prompt_sha256"] = "0" * 64
        fit_tampered["cells_sha256"] = sha256_sorted_json(
            fit_tampered["cells"])
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(fit_tampered, handle, sort_keys=True)
        os.chmod(path, 0o600)
        _reject(lambda: compatible_target(
            path, base["protocol"], base["assembly"], 0,
            _bindings("q25c-1.5b"), MODEL_BY_ID["q25c-1.5b"],
            _execution("q25c-1.5b"), 2,
            ancestor_fn=lambda _a, _b: True,
            fit_by_pair=base["fit"]), "battery prompt fit")


def test_model_reducer_rejects_missing_overlap_shard_and_model_mismatch():
    base = _base()
    model_id = "q25c-1.5b"
    values = [_target(model_id, index) for index in range(200)]
    inputs = [dict(path=f"/scores/target-{index:04d}.json",
                   sha256=sha256_sorted_json(value), value=value)
              for index, value in enumerate(values)]
    args = (base["protocol"], base["assembly"], _bindings(model_id),
            MODEL_BY_ID[model_id], _execution(model_id), _sequence(), 2)
    _reject(lambda: build_model_complete(
        *args, inputs[:-1], _generator(),
        ancestor_fn=lambda _a, _b: True), "exact 200")
    overlap = inputs[:-1] + [copy.deepcopy(inputs[0])]
    _reject(lambda: build_model_complete(
        *args, overlap, _generator(),
        ancestor_fn=lambda _a, _b: True), "duplicate")
    wrong_model = copy.deepcopy(inputs)
    wrong_model[0]["value"]["model"] = MODEL_BY_ID["q25c-3b"]
    _reject(lambda: build_model_complete(
        *args, wrong_model, _generator(),
        ancestor_fn=lambda _a, _b: True), "model/execution")
    swapped_path = copy.deepcopy(inputs)
    swapped_path[0]["path"] = "/scores/target-0001.json"
    _reject(lambda: build_model_complete(
        *args, swapped_path, _generator(),
        ancestor_fn=lambda _a, _b: True), "filename/index")


def test_salt_ancestry_and_binding_tamper_fail_closed():
    _reject(lambda: salt_sequence(
        "a" * 40, "b" * 40, ancestor_fn=lambda _a, _b: False),
        "not an ancestor")
    base = _base()
    value = _target()
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "target-0000.json")
        _write_new_0600(path, value)
        bad_bindings = _bindings("q25c-1.5b")
        bad_bindings["salt_commitment"]["sha256"] = "0" * 64
        _reject(lambda: compatible_target(
            path, base["protocol"], base["assembly"], 0, bad_bindings,
            MODEL_BY_ID["q25c-1.5b"], _execution("q25c-1.5b"), 2,
            ancestor_fn=lambda _a, _b: True), "predecessor binding")


def test_study_exact_four_models_same_cohort_and_deterministic_bytes():
    base = _base()
    inputs = []
    for index, model_id in enumerate(MODEL_IDS):
        value = _model_complete(model_id)
        inputs.append(dict(path=f"/complete/{model_id}.json",
                           sha256=str(index + 1) * 64, value=value))
    first = build_study_complete(
        base["protocol"], base["assembly"], inputs, _generator(),
        ancestor_fn=lambda _a, _b: True)
    second = build_study_complete(
        base["protocol"], base["assembly"], copy.deepcopy(inputs),
        _generator(), ancestor_fn=lambda _a, _b: True)
    assert first == second
    assert sha256_sorted_json(first) == sha256_sorted_json(second)
    assert [row["model"]["id"] for row in first["models"]] == \
        list(MODEL_IDS)
    assert first["ordered_target_keys"] == \
        inputs[0]["value"]["ordered_target_keys"]
    _reject(lambda: build_study_complete(
        base["protocol"], base["assembly"], inputs[:3], _generator(),
        ancestor_fn=lambda _a, _b: True), "exactly four")
    duplicate = copy.deepcopy(inputs)
    duplicate[-1] = copy.deepcopy(duplicate[0])
    _reject(lambda: build_study_complete(
        base["protocol"], base["assembly"], duplicate, _generator(),
        ancestor_fn=lambda _a, _b: True), "duplicate")


def test_model_target_directory_rejects_missing_and_extra_files():
    with tempfile.TemporaryDirectory() as directory:
        for index in range(200):
            open(os.path.join(directory, f"target-{index:04d}.json"),
                 "wb").close()
        assert len(_target_files(directory)) == 200
        os.unlink(os.path.join(directory, "target-0199.json"))
        _reject(lambda: _target_files(directory), "missing")
        open(os.path.join(directory, "target-0199.json"), "wb").close()
        open(os.path.join(directory, "extra.json"), "wb").close()
        _reject(lambda: _target_files(directory), "extra")


def test_battery_normalization_and_injected_runtime_are_exact():
    """Exercise the scorer/battery handoff without loading a real model."""
    import v2b_nll_confirmation_battery as battery_module

    base = _base()
    model_id = "q25c-1.5b"
    execution = _execution(model_id)
    model_files = dict(files_sha256=execution["model_snapshot_sha256"])
    tokenizer_files = dict(
        files_sha256=execution["tokenizer_snapshot_sha256"])
    rows = []
    for target_index, target in enumerate(base["assembly"]["targets"]):
        for cell_index, cell_id in enumerate(CELL_ORDER):
            fit = base["fit"][(target["key"], cell_id)]
            rows.append(dict(
                target_index=target_index, target_key=target["key"],
                cell_index=cell_index, cell_id=cell_id,
                structurally_eligible=fit["eligible"],
                status=fit["status"],
                prompt_sha256=fit["prompt_sha256"],
                prompt_bytes=fit["prompt_bytes"],
                n_prompt_tokens=fit["n_prompt_tokens"],
                exact_body_bytes=fit["exact_body_bytes"],
                scored_body_bytes=fit["scored_body_bytes"],
                straddled_body_bytes=fit["straddled_body_bytes"],
                n_boundary_straddle_tokens=fit[
                    "n_boundary_straddle_tokens"],
                boundary_signature=fit["boundary_signature"]))
    protocol_model = MODEL_BY_ID[model_id]
    battery = dict(
        model=dict(**copy.deepcopy(protocol_model),
                   model_class=execution["model_class"],
                   n_parameters=execution["n_parameters"],
                   files=model_files),
        tokenizer=dict(tokenizer_class=execution["tokenizer_class"],
                       vocab_size=execution["vocab_size"],
                       files=tokenizer_files),
        execution=dict(
            dtype=execution["dtype"], device=execution["device"],
            attention=execution["attention"],
            chunk_tokens=execution["chunk_tokens"],
            max_position_embeddings=execution[
                "max_position_embeddings"],
            environment_fingerprint=execution["environment_fingerprint"],
            requirements_lock_sha256=execution[
                "requirements_lock_sha256"],
            environment_freeze_sha256=execution[
                "environment_freeze_sha256"],
            environment_lock_matches=True,
            environment_freeze_matches=True,
            measurement_harness_sha256=execution[
                "measurement_harness_sha256"],
            numerical_harness_sha256=execution[
                "numerical_harness_sha256"],
            source_commit=execution["battery_source_commit"],
            source_tree_hash=execution["source_tree_hash"],
            gpu=execution["gpu"]),
        tokenizer_fit=dict(rows=rows),
        sharding=dict(recommended_shard_count=2))
    original = battery_module.validate_battery
    battery_module.validate_battery = lambda *args: args[0]
    try:
        normalized = normalize_battery(
            battery, base["protocol"], base["assembly"], {})
    finally:
        battery_module.validate_battery = original
    assert normalized["execution"] == execution
    assert normalized["shard_count"] == 2
    runtime = dict(
        model_id=execution["model_id"], model_name=execution["model_name"],
        revision=execution["revision"], model_class=execution["model_class"],
        n_parameters=execution["n_parameters"],
        tokenizer_class=execution["tokenizer_class"],
        vocab_size=execution["vocab_size"],
        max_position_embeddings=execution["max_position_embeddings"],
        attention=execution["attention"])
    _verify_runtime(battery, normalized, runtime, model_files,
                    tokenizer_files)
    bad_runtime = copy.deepcopy(runtime)
    bad_runtime["revision"] = "0" * 40
    _reject(lambda: _verify_runtime(
        battery, normalized, bad_runtime, model_files, tokenizer_files),
        "loaded model/tokenizer")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
