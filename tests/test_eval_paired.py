#!/usr/bin/env python3
"""Pure ledger/harness/cell-spec tests for the paired evaluator (no model
load, no GPU): boundary byte accounting, the frozen three-file harness
hash, exact target-cell coverage against a real materialized synthetic
manifest, materialization tamper refusal, and the fail-closed
context-length gate."""
import hashlib
import json
import math
import os
import sys
import tempfile
from types import SimpleNamespace

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_paired as paired
from eval_paired import (_model_max_positions, body_token_ledger,
                         empty_cell_arms, nll_rows_for_token_indices,
                         paired_harness_hash, score_prompt,
                         target_cell_specs)
from prepare_v2b_assembly import build_assembly, materialize
from test_prepare_v2b_assembly import (_build, _build_physlib, _lean_chain,
                                       _physlib_chain, _python_chain)
from v2b_common import V2BError, identity_key


def test_boundary_group_is_excluded_and_byte_ledger_is_exact():
    text = "abαβcd"
    # Token 1 crosses the boundary before β; α is known-prefix, β is body.
    offsets = [(0, 2), (2, 4), (4, 6)]
    ledger = body_token_ledger(text, offsets, body_start_char=3,
                               token_ids=[10, 11, 12])
    assert ledger["n_boundary_straddle_tokens"] == 1
    assert ledger["boundary_token_indices"] == [1]
    assert ledger["primary_token_indices"] == [2]
    assert ledger["straddled_body_bytes"] == len("β".encode())
    assert ledger["scored_body_bytes"] == len("cd".encode())
    assert ledger["straddled_body_codepoints"] == 1
    assert ledger["scored_body_codepoints"] == 2
    assert ledger["scored_body_bytes"] + ledger["straddled_body_bytes"] \
        == ledger["exact_body_bytes"]
    assert nll_rows_for_token_indices(ledger["primary_token_indices"]) == [1]


def test_overlapping_offset_tokens_stay_in_one_boundary_group():
    text = "xxprefixBODY"
    # Two tokenizer pieces overlap the same prefix/body character interval.
    offsets = [(0, 2), (2, 10), (8, 10), (10, 12)]
    ledger = body_token_ledger(text, offsets, body_start_char=8,
                               token_ids=[1, 2, 3, 4])
    assert ledger["boundary_token_indices"] == [1, 2]
    assert ledger["n_boundary_straddle_tokens"] == 2
    assert ledger["primary_token_indices"] == [3]
    assert len(ledger["boundary_groups"]) == 1


def test_no_straddle_uses_full_exact_body():
    text = "headBODY"
    offsets = [(0, 4), (4, 6), (6, 8)]
    ledger = body_token_ledger(text, offsets, body_start_char=4,
                               token_ids=[1, 2, 3])
    assert ledger["n_boundary_straddle_tokens"] == 0
    assert ledger["straddled_body_bytes"] == 0
    assert ledger["scored_body_bytes"] == ledger["exact_body_bytes"]
    assert ledger["primary_token_indices"] == [1, 2]


def test_paired_harness_hash_is_exact_canonical_three_file_binding():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for name in ("eval_paired.py", "eval_incontext.py", "layout.py"):
        digest = hashlib.sha256(open(os.path.join(root, name), "rb").read()
                                ).hexdigest()
        rows.append([name, digest])
    expected = hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert paired_harness_hash(root) == expected


def test_nll_row_mapping_rejects_token_zero():
    assert nll_rows_for_token_indices([3, 1]) == [2, 0]
    for bad in ([0], [-1], [True]):
        try:
            nll_rows_for_token_indices(bad)
            assert False, bad
        except V2BError:
            pass


def test_ledger_rejects_uncovered_offsets():
    try:
        body_token_ledger("abcd", [[0, 2]], body_start_char=1)
        assert False, "uncovered text accepted"
    except V2BError as err:
        assert "cover" in str(err)


def test_empty_offset_token_at_boundary_charges_nothing():
    # A zero-width token exactly at the boundary opens its own group with
    # zero charged bytes: classified prefix, excluded from primary, and the
    # byte ledger still conserves the exact body.
    ledger = body_token_ledger("headBODY", [(0, 4), (4, 4), (4, 8)],
                               body_start_char=4, token_ids=[1, 2, 3])
    assert ledger["primary_token_indices"] == [2]
    assert ledger["n_boundary_straddle_tokens"] == 0
    assert ledger["scored_body_bytes"] == ledger["exact_body_bytes"]


def test_model_max_positions_gate_is_fail_closed():
    assert _model_max_positions(
        SimpleNamespace(max_position_embeddings=4096)) == 4096
    assert _model_max_positions(SimpleNamespace(n_positions=2048)) == 2048
    assert _model_max_positions(SimpleNamespace(
        text_config=SimpleNamespace(max_position_embeddings=8192))) == 8192
    for config in (SimpleNamespace(),
                   SimpleNamespace(max_position_embeddings=0),
                   SimpleNamespace(max_position_embeddings=-1),
                   SimpleNamespace(max_position_embeddings=True)):
        try:
            _model_max_positions(config)
            assert False, config
        except V2BError:
            pass


def test_score_prompt_maps_body_rows_and_denominators_exactly():
    class CharacterTokenizer:
        def __call__(self, text, **kwargs):
            return {"input_ids": [1000 + ord(ch) for ch in text],
                    "offset_mapping": [(i, i + 1)
                                       for i in range(len(text))]}

    original = paired.eval_window
    paired.eval_window = lambda model, ids, device, chunk: torch.ones(
        len(ids) - 1, dtype=torch.float32)
    try:
        result = score_prompt(None, CharacterTokenizer(), "cpu",
                              b"ctx\n", b"head", b"BODY", 128)
    finally:
        paired.eval_window = original
    assert result["primary"]["nll_nats"] == 4.0
    assert result["primary"]["n_tokens"] == 4
    assert result["boundary_inclusive_sensitivity"]["nll_nats"] == 4.0
    assert result["boundary_ledger"]["exact_body_bytes"] == 4
    assert result["boundary_ledger"]["scored_body_bytes"] == 4
    assert result["boundary_ledger"]["straddled_body_bytes"] == 0
    assert [row["start_char_relative_to_body"]
            for row in result["raw_body_token_rows"]] == [0, 1, 2, 3]
    expected = 1.0 / math.log(2)
    assert abs(result["primary"]["bpb"] - expected) < 1e-12
    assert result["primary"]["bpc"] == result["primary"]["bpb"]


def _materialized_fixture(td):
    chain = _lean_chain(td)
    manifest = _build(chain)
    manifest_path = os.path.join(td, "manifest.json")
    json.dump(manifest, open(manifest_path, "w"))
    blobs = materialize(manifest_path, chain["sample"], chain["repo"],
                        chain["candidates"], chain["extraction"],
                        chain["neardup"], chain["outcome"],
                        chain["freeze"], chain["k7"],
                        lean_boundaries_path=chain["boundaries"])
    target = manifest["targets"][0]
    return target, blobs[identity_key("lean", ["M.A", "M.A.t"])]


def test_target_cell_specs_exact_coverage_and_roles():
    with tempfile.TemporaryDirectory() as td:
        target, blob = _materialized_fixture(td)
        specs = target_cell_specs(target, blob)
        by_id = {spec["cell_id"]: spec for spec in specs}
        grid = ["4096", "16384", "65536"]
        expected = (["k1"]
                    + [f"{arm}:{b}" for arm in ("k2", "k3", "k4")
                       for b in grid]
                    + [f"k5:0:{b}" for b in grid]
                    + ["k5:1:16384", "k5:2:16384"]
                    + [f"{arm}:{b}" for arm in ("k6", "k7") for b in grid]
                    + ["k3s", "k4s"])
        assert sorted(by_id) == sorted(expected)
        assert len(specs) == 23
        assert by_id["k1"]["estimand_role"] == "absence"
        for cell_id in ("k5:1:16384", "k5:2:16384"):
            assert by_id[cell_id]["estimand_role"] == "seed-sensitivity"
        for cell_id in ("k3s", "k4s"):
            assert by_id[cell_id]["estimand_role"] == \
                "same-dependency-sensitivity"
        assert by_id["k5:0:16384"]["estimand_role"] == "primary-grid"
        for spec in specs:
            assert spec["context_bytes"] == len(spec["context"])
            assert hashlib.sha256(spec["context"]).hexdigest() == \
                spec["context_sha256"]
        assert by_id["k1"]["context"] == b""
        # no arm of this fixture is cell-less
        assert empty_cell_arms(target) == []


def test_target_cell_specs_fails_on_missing_or_tampered_blob():
    with tempfile.TemporaryDirectory() as td:
        target, blob = _materialized_fixture(td)
        short = dict(blob)
        del short["k6:4096"]
        try:
            target_cell_specs(target, short)
            assert False, "missing materialized cell accepted"
        except V2BError as err:
            assert "materialization lacks" in str(err)
        tampered = dict(blob)
        tampered["k4:16384"] = b"x" + tampered["k4:16384"][1:]
        try:
            target_cell_specs(target, tampered)
            assert False, "tampered materialized cell accepted"
        except V2BError as err:
            assert "drift" in str(err)


def test_target_cell_specs_rejects_negative_context_bytes():
    with tempfile.TemporaryDirectory() as td:
        target, blob = _materialized_fixture(td)
        target["arms"]["k1"]["context_bytes"] = -1
        try:
            target_cell_specs(target, blob)
            assert False, "negative manifest byte count accepted"
        except V2BError as err:
            assert "malformed assembly context row" in str(err)


def test_existing_target_requires_exact_resume_identity_and_cell_grid():
    """A pre-existing atomic target is reusable only when it is the exact
    artifact this invocation would have produced. In particular, a copied
    target with the same key/run SHA but a drifted cell grid must not be
    silently counted as complete."""
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        manifest = _build(chain)
        target = manifest["targets"][0]
        run_identity = {"z_field": "test-run", "a_field": 2}
        run_sha = paired.sha256_sorted_json(run_identity)
        manifest_binding = dict(
            path=os.path.join(td, "manifest.json"),
            sha256="a" * 64, schema=paired.ASSEMBLY_SCHEMA)
        source_commit = "b" * 40
        source_hash = "c" * 64
        cells = []
        for description in paired._target_cell_rows(target):
            cells.append({key: value for key, value in description.items()
                          if key != "row"})
        artifact = dict(
            schema=paired.TARGET_SCHEMA,
            paired_schema_version=paired.PAIRED_SCHEMA_VERSION,
            run_identity=run_identity, run_identity_sha256=run_sha,
            repo=manifest["repo"], language=manifest["language"],
            corpus_git_sha=manifest["corpus_git_sha"],
            assembly_manifest=manifest_binding,
            assembly_target_sha256=paired.sha256_json(target),
            target_index=0, target_identity=target["identity"],
            target_key=target["key"],
            prefix_sha256=target["prefix_sha256"],
            prefix_bytes=target["prefix_bytes"],
            body_sha256=target["body_sha256"],
            body_bytes=target["body_bytes"],
            boundary_signature="d" * 64,
            body_layout_signature="e" * 64,
            ast_class_state=paired.AST_CLASS_STATE,
            empty_cell_arms=empty_cell_arms(target),
            n_cells=len(cells), cells=cells,
            generator=dict(source_commit=source_commit,
                           source_tree_hash=source_hash,
                           program="eval_paired.py"))
        path = os.path.join(td, "target.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(artifact, fh, sort_keys=True)

        accepted = paired._existing_target(
            path, run_identity, run_sha, manifest, manifest_binding,
            target, 0, source_commit, source_hash)
        assert accepted["target_key"] == target["key"]
        assert accepted["n_cells"] == len(cells)

        artifact["cells"][0]["context_sha256"] = "f" * 64
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(artifact, fh)
        try:
            paired._existing_target(
                path, run_identity, run_sha, manifest, manifest_binding,
                target, 0, source_commit, source_hash)
            assert False, "drifted resume cell grid accepted"
        except V2BError as err:
            assert "cell grid is incompatible" in str(err)


def test_target_cell_specs_includes_ineligible_empty_cells():
    """The §3/§15.A4 empty-rendering representation reaches the evaluator:
    empty arms contribute their full budget grid as eligible=false cells
    with empty contexts, so they are scoreable-in-form but excluded from
    complete-case contrasts by the eligibility flag, never absent."""
    with tempfile.TemporaryDirectory() as td:
        chain = _python_chain(td)
        manifest = build_assembly(chain["sample"], chain["repo"],
                                  chain["candidates"], chain["extraction"],
                                  chain["neardup"], chain["outcome"],
                                  None, chain["k7"])
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w"))
        blobs = materialize(manifest_path, chain["sample"], chain["repo"],
                            chain["candidates"], chain["extraction"],
                            chain["neardup"], chain["outcome"],
                            None, chain["k7"])
        target = manifest["targets"][0]
        specs = target_cell_specs(
            target, blobs[identity_key("python", ["pkg.a", "f", 0])])
        by_id = {spec["cell_id"]: spec for spec in specs}
        assert len(specs) == 23                 # full grid, nothing absent
        for cell_id in ("k5:0:4096", "k5:0:16384", "k5:0:65536",
                        "k5:1:16384", "k5:2:16384"):
            spec = by_id[cell_id]
            assert spec["eligible"] is False
            assert spec["context"] == b""
            assert spec["context_bytes"] == 0
            assert spec["context_sha256"] == \
                hashlib.sha256(b"").hexdigest()
        # arms with content keep real cells alongside
        assert by_id["k4:4096"]["context_bytes"] > 0
        assert empty_cell_arms(target) == []    # grid never cell-less now


def test_target_cell_specs_covers_k4x_when_applicable():
    """physlib manifests carry the §15.A13 k4x grid: 23 base cells + 3
    k4x budget cells, materialized and hash-bound like every other arm;
    non-physlib manifests carry no k4x cell at all (already pinned by the
    23-cell lean/python coverage tests)."""
    with tempfile.TemporaryDirectory() as td:
        chain = _physlib_chain(td)
        manifest = _build_physlib(chain)
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w"))
        blobs = materialize(manifest_path, chain["sample"], chain["repo"],
                            chain["candidates"], chain["extraction"],
                            chain["neardup"], chain["outcome"],
                            chain["freeze"], chain["k7"],
                            chain["k4x"], chain["external"],
                            chain["boundaries"])
        target = manifest["targets"][0]
        specs = target_cell_specs(
            target,
            blobs[identity_key("lean", ["Physlib.P", "Physlib.P.t"])])
        by_id = {spec["cell_id"]: spec for spec in specs}
        assert len(specs) == 26
        for budget in ("4096", "16384", "65536"):
            spec = by_id[f"k4x:{budget}"]
            assert spec["arm"] == "k4x"
            assert spec["context_bytes"] == len(spec["context"])
            assert hashlib.sha256(spec["context"]).hexdigest() == \
                spec["context_sha256"]
        assert by_id["k4x:65536"]["context_bytes"] > 0
        # the empty-grid representation means no arm is ever cell-less
        assert empty_cell_arms(target) == []


def test_empty_cell_arms_recorded_not_silent():
    target = dict(arms=dict(
        k1=dict(context_sha256="0" * 64, context_bytes=0),
        k2={}, k3={}, k4={},
        k5={"0": dict(cells={}), "1": dict(cells={}), "2": dict(cells={})},
        k6=dict(cells={}), k7=dict(cells={}), k3s={}, k4s={}))
    assert empty_cell_arms(target) == \
        ["k2", "k3", "k4", "k6", "k7", "k5:0", "k5:1", "k5:2", "k3s", "k4s"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PAIRED EVALUATOR TESTS PASS")
