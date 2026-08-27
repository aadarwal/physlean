#!/usr/bin/env python3
"""Adversarial tests for the S5 launcher and complete-artifact producer."""
import copy
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_v2b_s5_launcher as launcher_module
from run_v2b_s5_launcher import (ARMS, GENERATION_TABLE_SCHEMA,
                                 LAUNCH_SPEC_SCHEMA, dry_run_demo,
                                 run_launch, validate_generation_table,
                                 validate_launch_spec)
from run_v2b_s5_four_phase import LEAN_DRIVER, build_plan, run_four_phase
from v2b_common import V2BError, sha256_bytes, sha256_file
from v2b_s5_complete import (ACCOUNTING_CONTRACT_SHA256, cell_run_dir,
                             produce_complete, validate_complete)
from v2b_s5_dryrun import (STUB_HARNESS_CRASH, STUB_MODEL_BINDING,
                           STUB_SUFFIX_FAIL, STUB_TARGET_FAIL,
                           build_stub_toolchain, build_toy_workspace,
                           write_stub_generation_table)
from v2b_s5_visibility import produce_visibility


def _fixture(root, *, n_draws=1, original_body="41", body_for=None,
             reference_ok=True, feasible=True):
    toolchain = build_stub_toolchain(root)
    workspace = build_toy_workspace(root, toolchain,
                                    original_body=original_body)
    target_key = sha256_bytes(b"launcher-test-target")
    spec = dict(
        schema=LAUNCH_SPEC_SCHEMA, repo="toy-dry-run", language="lean",
        corpus=dict(workspace_root=workspace["workspace"],
                    toolchain_root=toolchain["toolchain"],
                    corpus_git_sha=workspace["corpus_git_sha"]),
        model_binding=dict(STUB_MODEL_BINDING),
        arms=list(ARMS), n_draws=n_draws,
        helper_path=workspace["helper"],
        targets=[dict(
            target_key=target_key,
            identity=[workspace["module"], workspace["target_name"]],
            module=workspace["module"], source_path=workspace["source"],
            original_sha256=workspace["source_sha256"],
            target_name=workspace["target_name"],
            target_kind=workspace["target_kind"],
            target_start_byte=workspace["target_start_byte"],
            header_end_byte=workspace["header_end_byte"],
            target_end_byte=workspace["target_end_byte"],
            boundary_artifact_sha256="0" * 64, span_id="toy-span-0",
            setup_path=workspace["setup"],
            import_closure_path=workspace["closure"],
            setup_index_path=workspace["index"],
            runtime_paths=workspace["runtime_paths"],
            reference_body_le_448_tokens=reference_ok,
            class_verifier_feasible=feasible)])
    if reference_ok and feasible:
        _, table = write_stub_generation_table(
            root, target_key, spec["arms"], n_draws, body_for=body_for)
    else:
        table = dict(schema=GENERATION_TABLE_SCHEMA, repo="toy-dry-run",
                     model_binding=dict(STUB_MODEL_BINDING),
                     generator_note="stub-not-a-model", n_rows=0, rows=[])
    return spec, table, workspace, toolchain, target_key


def _expect(call, fragment):
    try:
        call()
        assert False, f"accepted input expected to fail with {fragment!r}"
    except V2BError as err:
        assert fragment in str(err), str(err)


def test_spec_and_table_validation_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, target_key = _fixture(td)
        validate_launch_spec(spec)
        validate_generation_table(table, spec)

        forged = copy.deepcopy(spec)
        forged["arms"] = ["k1", "k3", "k4", "k5"]
        _expect(lambda: validate_launch_spec(forged), "arms")

        forged = copy.deepcopy(spec)
        forged["targets"][0]["target_kind"] = "example"
        _expect(lambda: validate_launch_spec(forged), "kind")

        forged = copy.deepcopy(spec)
        forged["targets"][0]["header_end_byte"] = 0
        _expect(lambda: validate_launch_spec(forged), "offsets")

        forged = copy.deepcopy(table)
        forged["rows"] = forged["rows"] + [dict(forged["rows"][0])]
        forged["n_rows"] = len(forged["rows"])
        _expect(lambda: validate_generation_table(forged, spec),
                "duplicate")

        forged = copy.deepcopy(table)
        removed = forged["rows"][:-1]
        forged["rows"] = removed
        forged["n_rows"] = len(removed)
        _expect(lambda: validate_generation_table(forged, spec), "lacks")

        forged = copy.deepcopy(table)
        foreign = dict(forged["rows"][0])
        foreign["arm"] = "k2"
        forged["rows"] = forged["rows"] + [foreign]
        forged["n_rows"] = len(forged["rows"])
        _expect(lambda: validate_generation_table(forged, spec),
                "outside the eligible")


def test_dry_run_demo_end_to_end_produces_a_validating_artifact():
    with tempfile.TemporaryDirectory() as td:
        artifact, out_path = dry_run_demo(os.path.join(td, "demo"))
        assert validate_complete(artifact) is artifact
        assert artifact["execution_mode"] == "dry-run-stub-not-evidence"
        assert artifact["accounting_sha256"] == ACCOUNTING_CONTRACT_SHA256
        stored = json.load(open(out_path, encoding="utf-8"))
        assert stored == artifact
        row = artifact["rows"][0]
        assert row["eligible"] is True
        assert row["eligibility"] == dict(
            reference_body_le_448_tokens=True, baseline_pass=True,
            class_verifier_feasible=True)
        # The demo's frozen deterministic mix: k1/d1 target failure,
        # k3/d1 suffix failure, k6/d1 type drift, all else passes.
        assert row["outcomes"] == {"k1": [1, 0], "k3": [1, 0],
                                   "k4": [1, 1], "k5": [1, 1],
                                   "k6": [1, 0]}
        classifications = {
            (arm, cell["draw_index"]): cell["classification"]
            for arm in ARMS for cell in row["evidence"][arm]}
        assert classifications[("k1", 1)] == "verification-failure"
        assert classifications[("k6", 1)] == "candidate-type-drift"


def test_relaunch_reuses_immutable_evidence_and_rebinds_identically():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, _ = _fixture(td)
        run_root = os.path.join(td, "runs")
        first = run_launch(spec, table, run_root, dry_run=True)
        second = run_launch(spec, table, run_root, dry_run=True)
        assert first["binding"] == second["binding"]
        assert first["rows"] == second["rows"]


def test_baseline_ineligible_short_circuits_to_one_witness_cell():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, target_key = _fixture(
            td, original_body=f"41 -- {STUB_TARGET_FAIL}")
        run_root = os.path.join(td, "runs")
        artifact = run_launch(spec, table, run_root, dry_run=True)
        row = artifact["rows"][0]
        assert row["eligible"] is False
        assert row["eligibility"]["baseline_pass"] is False
        assert row["baseline"]["state"] == "baseline-ineligible"
        assert all(value is None for value in row["outcomes"].values())
        assert row["n_cells"] == 1
        ran = [directory for directory, _, files in os.walk(run_root)
               if "summary.json" in files]
        assert len(ran) == 1
        assert ran[0] == cell_run_dir(run_root, target_key, ARMS[0], 0)


def test_spec_ineligible_target_carries_no_cells_and_null_outcomes():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, _ = _fixture(td, feasible=False)
        run_root = os.path.join(td, "runs")
        artifact = run_launch(spec, table, run_root, dry_run=True)
        row = artifact["rows"][0]
        assert row["eligible"] is False
        assert row["eligibility"]["class_verifier_feasible"] is False
        assert row["n_cells"] == 0
        assert row["baseline"]["state"] == "not-run"
        assert all(value is None for value in row["outcomes"].values())
        assert not any("summary.json" in files
                       for _, _, files in os.walk(run_root))


def test_harness_invalid_witness_stops_the_launcher():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, _ = _fixture(
            td, original_body=f"41 -- {STUB_HARNESS_CRASH}")
        _expect(lambda: run_launch(spec, table, os.path.join(td, "runs"),
                                   dry_run=True), "harness-invalid")


def test_producer_refuses_harness_invalid_and_missing_cells():
    with tempfile.TemporaryDirectory() as td:
        spec, table, workspace, toolchain, target_key = _fixture(
            td, original_body=f"41 -- {STUB_HARNESS_CRASH}")
        target = spec["targets"][0]
        visibility = produce_visibility(
            target["module"], target["source_path"],
            spec["corpus"]["workspace_root"],
            spec["corpus"]["toolchain_root"], spec["helper_path"],
            target["setup_path"], target["import_closure_path"],
            target["setup_index_path"], target["runtime_paths"])
        original = workspace["original_text"].encode("utf-8")
        witness = next(row for row in table["rows"]
                       if row["arm"] == ARMS[0] and row["draw_index"] == 0)
        body = open(witness["body_path"], "rb").read()
        candidate = (original[:target["header_end_byte"]] + body
                     + original[target["target_end_byte"]:])
        plan = build_plan(
            original, candidate, logical_file=target["source_path"],
            target_name=target["target_name"],
            target_kind=target["target_kind"],
            target_start=target["target_start_byte"],
            header_end=target["header_end_byte"],
            baseline_retained_end=target["target_end_byte"],
            candidate_retained_end=target["header_end_byte"] + len(body),
            visibility=visibility, driver_sha256=sha256_file(LEAN_DRIVER),
            allow_unisolated_test=True)
        run_root = os.path.join(td, "runs")
        result = run_four_phase(
            plan, visibility, original, candidate,
            cell_run_dir(run_root, target_key, ARMS[0], 0),
            allow_unisolated_test=True)
        assert result["summary"]["classification"] == "harness-invalid"
        try:
            produce_complete(spec, table, run_root,
                             {target_key: visibility},
                             execution_mode="dry-run-stub-not-evidence",
                             allow_unisolated_test=True)
            assert False, "harness-invalid evidence was finalized"
        except V2BError as err:
            message = str(err)
            assert "unresolved" in message
            assert "harness-invalid" in message
            assert "missing-run" in message
            assert ACCOUNTING_CONTRACT_SHA256[:12] in message

    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, tk = _fixture(td)
        target = spec["targets"][0]
        visibility = produce_visibility(
            target["module"], target["source_path"],
            spec["corpus"]["workspace_root"],
            spec["corpus"]["toolchain_root"], spec["helper_path"],
            target["setup_path"], target["import_closure_path"],
            target["setup_index_path"], target["runtime_paths"])
        _expect(lambda: produce_complete(
            spec, table, os.path.join(td, "runs"), {tk: visibility},
            execution_mode="dry-run-stub-not-evidence",
            allow_unisolated_test=True), "missing-run")


def test_producer_rejects_tampered_run_evidence_and_forged_binding():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, target_key = _fixture(td)
        run_root = os.path.join(td, "runs")
        artifact = run_launch(spec, table, run_root, dry_run=True)

        forged = copy.deepcopy(artifact)
        assert forged["rows"][0]["outcomes"]["k4"] == [1]
        forged["rows"][0]["outcomes"]["k4"] = [0]
        _expect(lambda: validate_complete(forged), "binding drift")

        forged = copy.deepcopy(artifact)
        forged["execution_mode"] = "production-bubblewrap"
        _expect(lambda: validate_complete(forged), "binding drift")

        stdout_path = os.path.join(
            cell_run_dir(run_root, target_key, "k4", 0),
            "attempts", "candidate-target")
        attempt = os.path.join(stdout_path,
                               sorted(os.listdir(stdout_path))[0],
                               "stdout.bin")
        blob = open(attempt, "rb").read()
        with open(attempt, "wb") as handle:
            handle.write(blob.replace(b'"status":"verified"',
                                      b'"status":"verifie1"', 1))
        _expect(lambda: run_launch(spec, table, run_root, dry_run=True),
                "drift")


def test_execution_mode_and_backend_seams_cannot_disagree():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, tk = _fixture(td)
        run_root = os.path.join(td, "runs")
        run_launch(spec, table, run_root, dry_run=True)
        target = spec["targets"][0]
        visibility = produce_visibility(
            target["module"], target["source_path"],
            spec["corpus"]["workspace_root"],
            spec["corpus"]["toolchain_root"], spec["helper_path"],
            target["setup_path"], target["import_closure_path"],
            target["setup_index_path"], target["runtime_paths"])
        _expect(lambda: produce_complete(
            spec, table, run_root, {tk: visibility},
            execution_mode="production-bubblewrap",
            allow_unisolated_test=True), "seam disagreement")
        _expect(lambda: produce_complete(
            spec, table, run_root, {tk: visibility},
            execution_mode="dry-run-stub-not-evidence",
            allow_unisolated_test=False), "seam disagreement")


def test_production_mode_requires_canonical_bubblewrap():
    with tempfile.TemporaryDirectory() as td:
        spec, table, _, _, _ = _fixture(td)
        with mock.patch.object(launcher_module, "CANONICAL_BWRAP",
                               os.path.join(td, "no-such-bwrap")):
            _expect(lambda: run_launch(spec, table,
                                       os.path.join(td, "runs"),
                                       dry_run=False), "bubblewrap")


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
