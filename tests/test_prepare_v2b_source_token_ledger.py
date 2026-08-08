#!/usr/bin/env python3
"""Source-only ledger reproduces exact assembly bodies without scores."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prepare_v2b_source_token_ledger import (STATE,
                                              _manifest_paths,
                                              build_source_token_ledger,
                                              source_token_harness_hash)
from test_prepare_v2b_assembly import (_build, _lean_chain, _python_chain)
from v2b_common import V2BError, sha256_json
from v2b_source_tokens import (CLASSIFIER_CONTRACT_SHA256,
                               SOURCE_TOKEN_LEDGER_SCHEMA)


def _fixture(td, language):
    chain = _lean_chain(td) if language == "lean" else _python_chain(td)
    manifest = _build(chain)
    path = os.path.join(td, "assembly.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    artifact = build_source_token_ledger(
        path, chain["sample"], chain["repo"], chain["candidates"],
        chain["extraction"], chain["neardup"], chain["outcome"],
        chain["freeze"], chain["k7"], chain.get("k4x"),
        chain.get("external"), chain.get("boundaries"))
    return chain, manifest, path, artifact


def test_lean_source_only_ledger_exact_body_binding():
    with tempfile.TemporaryDirectory() as td:
        _chain, manifest, _path, value = _fixture(td, "lean")
        assert value["schema"] == SOURCE_TOKEN_LEDGER_SCHEMA
        assert value["state"] == STATE
        assert value["claim"] == "source-token NLL attribution"
        assert value["ast_node_attribution"] is False
        assert value["language"] == "lean" and value["n_targets"] == 1
        assert value["classifier_contract_sha256"] == \
            CLASSIFIER_CONTRACT_SHA256
        assert value["source_token_harness_sha256"] == \
            source_token_harness_hash()
        assert value["targets_sha256"] == sha256_json(value["targets"])
        row = value["targets"][0]
        target = manifest["targets"][0]
        assert row["body_sha256"] == target["body_sha256"]
        assert row["body_bytes"] == target["body_bytes"]
        assert row["assembly_target_sha256"] == sha256_json(target)
        assert row["char_to_byte_prefix"][-1] == row["body_bytes"]
        assert sum(row["source_class_bytes"].values()) == row["body_bytes"]


def test_python_source_only_ledger_uses_same_contract():
    with tempfile.TemporaryDirectory() as td:
        _chain, _manifest, _path, value = _fixture(td, "python")
        assert value["language"] == "python"
        row = value["targets"][0]
        assert row["n_spans"] == len(row["spans"])
        assert any(span["source_class"] == "word" for span in row["spans"])
        assert any(span["source_class"] == "layout"
                   for span in row["spans"])
        assert value["runtime"]["python_executable_sha256"]
        assert value["runtime"]["tokenize_file_sha256"]


def test_manifest_paths_recover_the_exact_bound_chain():
    with tempfile.TemporaryDirectory() as td:
        chain, manifest, path, _value = _fixture(td, "lean")
        loaded, paths = _manifest_paths(path, {})
        assert loaded["targets_sha256"] == manifest["targets_sha256"]
        assert paths["sample"] == chain["sample"]
        assert paths["candidates"] == chain["candidates"]
        assert paths["lean_keyword_freeze"] == chain["freeze"]
        assert paths["lean_boundaries"] == chain["boundaries"]
        assert paths["k7_order"] == chain["k7"]


def test_wrong_repo_and_body_binding_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        manifest = _build(chain)
        path = os.path.join(td, "assembly.json")
        json.dump(manifest, open(path, "w"))
        try:
            build_source_token_ledger(
                path, chain["sample"], "physlib", chain["candidates"],
                chain["extraction"], chain["neardup"], chain["outcome"],
                chain["freeze"], chain["k7"],
                lean_boundaries_path=chain["boundaries"])
            assert False, "wrong corpus tag accepted"
        except V2BError as err:
            assert "repo mismatch" in str(err)

        # A restamped manifest row that lies about body bytes cannot survive
        # materialization/rebinding.
        manifest["targets"][0]["body_sha256"] = "0" * 64
        json.dump(manifest, open(path, "w"))
        try:
            build_source_token_ledger(
                path, chain["sample"], chain["repo"], chain["candidates"],
                chain["extraction"], chain["neardup"], chain["outcome"],
                chain["freeze"], chain["k7"],
                lean_boundaries_path=chain["boundaries"])
            assert False, "tampered body binding accepted"
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B SOURCE TOKEN LEDGER TESTS PASS")
