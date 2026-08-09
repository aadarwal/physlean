#!/usr/bin/env python3
"""Pure fail-closed tests for the direct-scaling P0 packet."""
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from direct_scaling_protocol import (MODEL_CONFIG_INDEX_SCHEMA,
                                     PRIMARY_MODELS, build_protocol,
                                     systematic_indices,
                                     systematic_seed_u64,
                                     validate_protocol)
from simulate_direct_scaling_power import build_power
from v2b_common import V2BError, sha256_file, sha256_sorted_json


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _index(path, models_path):
    models = json.loads(models_path.read_text())
    rows = []
    for model_id in sorted(models):
        rows.append({
            "model_id": model_id, "revision": models[model_id]["sha"],
            "config_sha256": "1" * 64,
            "tokenizer_files": [{"name": "tokenizer.json",
                                 "sha256": "2" * 64}],
            "selected_config": {
                "architectures": ["FixtureForCausalLM"],
                "attention_class": "native-full",
                "causal_config_path": [],
                "causal_model_type": "fixture",
                "full_attention_interval": None,
                "layer_types": None,
                "linear_conv_kernel_dim": None,
                "max_position_embeddings": 262144,
                "max_window_layers": None,
                "model_type": "fixture", "num_hidden_layers": 24,
                "rope_parameters": None, "rope_scaling": None,
                "sliding_window": None, "use_sliding_window": False,
            },
        })
    value = {"schema": MODEL_CONFIG_INDEX_SCHEMA,
             "models_lock_sha256": sha256_file(str(models_path)),
             "models": rows, "generator": {"program": "fixture"}}
    value["index_binding"] = sha256_sorted_json(value)
    _write(path, value)


def _build(tmp):
    index = Path(tmp) / "configs.json"
    _index(index, ROOT / "models.json")
    return build_protocol(
        design_path=ROOT / "DIRECT_SCALING_STUDY.md",
        corpora_lock_path=ROOT / "corpora_lock.json",
        models_lock_path=ROOT / "models.json",
        model_config_index_path=index,
        generator={"program": "fixture", "source_commit": "a" * 40,
                   "source_tree_hash": "b" * 64},
    )


def test_round_trip_and_frozen_ladder():
    with tempfile.TemporaryDirectory() as tmp:
        protocol = _build(tmp)
    validate_protocol(protocol)
    assert protocol["panel"]["primary_models"] == list(PRIMARY_MODELS)
    assert len(protocol["panel"]["repositories"]) == 14
    assert protocol["context"]["grid_bytes"][-1] == 1024 * 1024
    assert protocol["study_status"] == "prospective-exploratory-follow-up"
    sampling = protocol["sampling"]
    assert sampling["seed_u64_rule"]["preimage"] == [
        "v2c-systematic-offset-v1", "$seed_sha256", "$repo", "$arm"]
    assert sampling["seed_u64_rule"]["arm_enum"] == ["a0", "a1"]
    assert sampling["systematic_index_formula"] == (
        "i_j=floor(P*(u+j*2^64)/(n*2^64));"
        "j=0,...,n-1;n=min(planned_per_repo,P)")
    assert sampling["a0_origin_rule"]["raw_index"] == (
        "systematic_index_formula")
    assert sampling["a1_coordinate_rule"]["slot_population"] == (
        "P=floor(axis_bytes/target_block_bytes)")


def test_binding_tamper_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        protocol = _build(tmp)
    bad = deepcopy(protocol)
    bad["analysis"]["rope_beta"] = 0.2
    try:
        validate_protocol(bad)
    except V2BError as err:
        assert "binding" in str(err)
    else:
        raise AssertionError("tampered protocol accepted")


def test_nonascending_power_grid_rejected_even_when_resigned():
    with tempfile.TemporaryDirectory() as tmp:
        protocol = _build(tmp)
    bad = deepcopy(protocol)
    bad["power"]["sensitivity_grid"]["repo_slope_sd"] = [
        0.01, 0.005, 0.02, 0.03]
    bad["protocol_binding"] = sha256_sorted_json({
        key: value for key, value in bad.items()
        if key != "protocol_binding"})
    try:
        validate_protocol(bad)
    except V2BError as err:
        assert "strictly ascending" in str(err)
    else:
        raise AssertionError("nonascending power grid accepted")


def test_systematic_seed_and_indices_have_frozen_vector():
    seed = systematic_seed_u64("0" * 64, "fixture", "a0")
    assert seed == 4726835686534094915
    assert systematic_indices(1000, 7, seed) == [36, 179, 322, 465,
                                                  608, 750, 893]
    assert systematic_indices(3, 200, seed) == [0, 1, 2]
    assert systematic_indices(0, 200, seed) == []


def test_missing_config_row_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        index = tmp / "configs.json"
        _index(index, ROOT / "models.json")
        value = json.loads(index.read_text())
        value["models"].pop()
        value["index_binding"] = sha256_sorted_json({
            k: v for k, v in value.items() if k != "index_binding"})
        _write(index, value)
        try:
            build_protocol(
                design_path=ROOT / "DIRECT_SCALING_STUDY.md",
                corpora_lock_path=ROOT / "corpora_lock.json",
                models_lock_path=ROOT / "models.json",
                model_config_index_path=index,
                generator={"program": "fixture"},
            )
        except V2BError as err:
            assert "cover models.json exactly" in str(err)
        else:
            raise AssertionError("incomplete config index accepted")


def test_power_simulation_is_deterministic_and_maps_adequacy_boundary():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocol = _build(tmp)
        protocol_path = tmp / "protocol.json"
        _write(protocol_path, protocol)
        first = build_power(str(protocol_path), {"program": "fixture"})
        second = build_power(str(protocol_path), {"program": "fixture"})
    assert first == second
    assert first["decision"]["central_scenario_power_ok"] is True
    assert first["decision"][
        "power_simulation_ok_at_declared_assumption"] is True
    assert first["decision"]["variance_calibration_required"] is True
    assert first["decision"][
        "language_general_scoring_authorized"] is False
    assert first["decision"]["structural_census_authorized"] is True
    assert len(first["primary_rows"]) == 3
    assert len(first["adequacy_boundaries"]) == 6
    assert first["boundary_coverage_diagnostic"]["gating"] is False
    assert next(row for row in first["primary_rows"]
                if row["scenario"] == "boundary")[
                    "scenario_role"] == "coverage-diagnostic-only"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print("DIRECT SCALING PROTOCOL TESTS PASS")
