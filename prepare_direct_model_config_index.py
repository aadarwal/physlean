#!/usr/bin/env python3
"""Seal pinned model/config/tokenizer bytes for the direct-scaling P0 gate."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from direct_scaling_protocol import MODEL_CONFIG_INDEX_SCHEMA
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, load_json, sha256_file,
                        sha256_sorted_json, write_new_json)


TOKENIZER_NAMES = (
    "added_tokens.json", "merges.txt", "preprocessor_config.json",
    "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json",
    "tokenizer.model", "tokenizer_config.json", "vocab.json",
)


def _cache_dir(cache_root: Path, model_id: str, revision: str) -> Path:
    slug = "models--" + model_id.replace("/", "--")
    return cache_root / slug / "snapshots" / revision


def _ensure_snapshot(cache_root: Path, model_id: str, revision: str,
                     download_missing: bool) -> Path:
    snapshot = _cache_dir(cache_root, model_id, revision)
    if snapshot.joinpath("config.json").is_file():
        return snapshot
    if not download_missing:
        raise V2BError(f"missing pinned config snapshot: {snapshot}")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as err:
        raise V2BError("huggingface_hub is required for --download-missing") \
            from err
    resolved = Path(snapshot_download(
        repo_id=model_id, revision=revision, cache_dir=str(cache_root),
        allow_patterns=["config.json", *TOKENIZER_NAMES],
    ))
    if resolved.resolve() != snapshot.resolve():
        raise V2BError(f"snapshot resolver drift for {model_id}: {resolved}")
    return snapshot


def build_index(models_path: str | Path, cache_root: str | Path,
                download_missing: bool, generator: dict) -> dict:
    models, models_sha = load_json(str(models_path))
    root = Path(cache_root).resolve()
    if not root.is_dir():
        raise V2BError(f"model cache root is not a directory: {root}")
    rows = []
    for model_id in sorted(models):
        locked = models[model_id]
        if set(locked) != {"created", "sha"}:
            raise V2BError(f"malformed model lock row {model_id}")
        revision = locked["sha"]
        snapshot = _ensure_snapshot(root, model_id, revision,
                                    download_missing)
        config_path = snapshot / "config.json"
        try:
            config_path.resolve().relative_to(root)
        except ValueError as err:
            raise V2BError(f"config symlink escapes cache: {config_path}") \
                from err
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            raise V2BError(f"cannot read pinned config {config_path}: {err}") \
                from err
        if not isinstance(config, dict):
            raise V2BError(f"model config is not an object: {config_path}")
        tokenizer_files = []
        for name in TOKENIZER_NAMES:
            path = snapshot / name
            if path.is_file():
                real = path.resolve()
                try:
                    real.relative_to(root)
                except ValueError as err:
                    raise V2BError(f"tokenizer symlink escapes cache: {path}") \
                        from err
                tokenizer_files.append({"name": name,
                                        "sha256": sha256_file(str(path))})
        if not tokenizer_files:
            raise V2BError(f"no tokenizer files found for {model_id}")
        causal_path = ["text_config"] if isinstance(
            config.get("text_config"), dict) else []
        causal = config["text_config"] if causal_path else config
        max_positions = causal.get("max_position_embeddings")
        sliding = causal.get("sliding_window")
        layer_types = causal.get("layer_types")
        if causal.get("rope_scaling") is not None:
            attention_class = "rope-extended"
        elif isinstance(layer_types, list) \
                and len(set(layer_types)) > 1:
            attention_class = "hybrid"
        elif causal.get("full_attention_interval") not in (None, 0, 1):
            attention_class = "hybrid"
        elif causal.get("use_sliding_window") is not False \
                and isinstance(sliding, int) \
                and isinstance(max_positions, int) \
                and sliding < max_positions:
            attention_class = "sliding-window"
        else:
            attention_class = "native-full"
        selected = {
            "architectures": config.get("architectures") or [],
            "attention_class": attention_class,
            "causal_config_path": causal_path,
            "causal_model_type": causal.get("model_type"),
            "full_attention_interval": causal.get("full_attention_interval"),
            "layer_types": causal.get("layer_types"),
            "linear_conv_kernel_dim": causal.get("linear_conv_kernel_dim"),
            "max_position_embeddings": max_positions,
            "max_window_layers": causal.get("max_window_layers"),
            "model_type": config.get("model_type"),
            "num_hidden_layers": causal.get("num_hidden_layers"),
            "rope_parameters": causal.get("rope_parameters"),
            "rope_scaling": causal.get("rope_scaling"),
            "sliding_window": causal.get("sliding_window"),
            "use_sliding_window": causal.get("use_sliding_window"),
        }
        rows.append({"model_id": model_id, "revision": revision,
                     "config_sha256": sha256_file(str(config_path)),
                     "tokenizer_files": tokenizer_files,
                     "selected_config": selected})
    index = {"schema": MODEL_CONFIG_INDEX_SCHEMA,
             "models_lock_sha256": models_sha, "models": rows,
             "generator": generator}
    index["index_binding"] = sha256_sorted_json(index)
    return index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models.json")
    ap.add_argument("--cache-root", required=True)
    ap.add_argument("--download-missing", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree must be clean before config-index seal")
    commit, tree = head_commit(), source_tree_hash()
    index = build_index(
        args.models, args.cache_root, args.download_missing,
        {"program": os.path.basename(__file__), "source_commit": commit,
         "source_tree_hash": tree},
    )
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed during config-index construction")
    digest = write_new_json(args.out, index)
    print(f"[v2c-model-configs] {len(index['models'])} checkpoints -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
