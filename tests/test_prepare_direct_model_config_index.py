#!/usr/bin/env python3
"""Focused model-ledger tests, including Qwen3.5's nested text config."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_direct_model_config_index import build_index
from v2b_common import V2BError


MODEL_ID = "Qwen/Qwen3.5-0.8B-Base"
REVISION = "d" * 40


def _fixture(tmp):
    tmp = Path(tmp)
    cache = tmp / "cache"
    snapshot = (cache / "models--Qwen--Qwen3.5-0.8B-Base" /
                "snapshots" / REVISION)
    snapshot.mkdir(parents=True)
    config = {
        "architectures": ["Qwen3_5ForConditionalGeneration"],
        "model_type": "qwen3_5",
        "text_config": {
            "model_type": "qwen3_5_text",
            "max_position_embeddings": 262144,
            "full_attention_interval": 4,
            "layer_types": ["linear_attention", "full_attention"],
            "linear_conv_kernel_dim": 4,
            "num_hidden_layers": 2,
            "rope_parameters": {"rope_type": "default"},
        },
    }
    (snapshot / "config.json").write_text(json.dumps(config))
    (snapshot / "tokenizer.json").write_text("{}")
    models = tmp / "models.json"
    models.write_text(json.dumps({MODEL_ID: {
        "created": "2026-02-28T23:57:45.000Z", "sha": REVISION}}))
    return cache, snapshot, models


def test_nested_text_config_is_the_causal_config():
    with tempfile.TemporaryDirectory() as tmp:
        cache, _, models = _fixture(tmp)
        index = build_index(models, cache, False, {"program": "fixture"})
    selected = index["models"][0]["selected_config"]
    assert selected["causal_config_path"] == ["text_config"]
    assert selected["max_position_embeddings"] == 262144
    assert selected["attention_class"] == "hybrid"
    assert selected["full_attention_interval"] == 4
    assert selected["layer_types"][-1] == "full_attention"


def test_disabled_sliding_window_is_not_misclassified():
    with tempfile.TemporaryDirectory() as tmp:
        cache, snapshot, models = _fixture(tmp)
        config_path = snapshot / "config.json"
        config = json.loads(config_path.read_text())
        text = config["text_config"]
        text.pop("full_attention_interval")
        text.pop("layer_types")
        text["sliding_window"] = 4096
        text["use_sliding_window"] = False
        config_path.write_text(json.dumps(config))
        index = build_index(models, cache, False, {"program": "fixture"})
    assert index["models"][0]["selected_config"][
        "attention_class"] == "native-full"


def test_config_symlink_escape_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        cache, snapshot, models = _fixture(tmp)
        outside = Path(tmp) / "outside.json"
        outside.write_text("{}")
        (snapshot / "config.json").unlink()
        (snapshot / "config.json").symlink_to(outside)
        try:
            build_index(models, cache, False, {"program": "fixture"})
        except V2BError as err:
            assert "escapes cache" in str(err)
        else:
            raise AssertionError("escaping config symlink accepted")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print("DIRECT MODEL CONFIG INDEX TESTS PASS")
