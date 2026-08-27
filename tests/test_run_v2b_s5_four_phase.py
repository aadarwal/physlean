#!/usr/bin/env python3
"""Adversarial tests for the four-fresh-process S5 execution envelope."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_v2b_s5_four_phase as four_phase
from run_v2b_s5_four_phase import (
    LEAN_DRIVER, PHASES, VisibilityLauncher, build_plan, run_four_phase,
    validate_summary, validate_visibility_artifact)
from v2b_common import V2BError, sha256_file, sha256_sorted_json
from v2b_s5_visibility import (IMPORT_CLOSURE_SCHEMA, produce_visibility,
                               validate_visibility)
from tests.test_v2b_s5_visibility import (
    _artifact, _broad_index, _setup, _write, _write_json)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = LEAN_DRIVER
TOOLCHAINS = (
    "leanprover/lean4:v4.32.0",
    "leanprover/lean4:v4.33.0-rc2",
)


def _elan(toolchain):
    elan = shutil.which("elan")
    if elan is None:
        return None
    listed = subprocess.run(
        [elan, "toolchain", "list"], capture_output=True, text=True,
        check=False)
    return elan if toolchain in listed.stdout else None


def _visibility(toolchain, root, source_text):
    elan = _elan(toolchain)
    if elan is None:
        return None
    root = os.path.realpath(root)
    prefix = subprocess.run(
        [elan, "run", toolchain, "lean", "--print-prefix"],
        capture_output=True, text=True, check=True).stdout.strip()
    workspace = os.path.realpath(os.path.join(root, "workspace"))
    os.makedirs(workspace)
    direct = os.path.join(
        workspace, ".lake", "build", "lib", "lean", "Probe",
        "Direct.olean")
    transitive = os.path.join(
        workspace, ".lake", "build", "lib", "lean", "Probe",
        "Transitive.olean")
    runtime = os.path.join(prefix, "lib", "lean", "libleanshared_1.dylib")
    if not os.path.isfile(runtime):
        candidates = [
            os.path.join(prefix, "lib", "lean", name)
            for name in os.listdir(os.path.join(prefix, "lib", "lean"))
            if name.startswith("libleanshared") and
            (name.endswith(".so") or name.endswith(".dylib"))]
        assert candidates, prefix
        runtime = sorted(candidates)[0]
    paths = dict(
        workspace=workspace, toolchain=prefix,
        source=os.path.join(workspace, "Probe", "Target.lean"),
        pin=os.path.join(workspace, "lean-toolchain"), direct=direct,
        transitive=transitive, runtime=runtime,
        lean=os.path.join(prefix, "bin", "lean"),
        lake=os.path.join(prefix, "bin", "lake"),
        helper=os.path.join(root, "v2bS5ExpandSetup"),
        setup=os.path.join(root, "expanded-setup.json"),
        closure=os.path.join(root, "closure.json"),
        index=os.path.join(root, "broad-index.json"),
        extraction=os.path.join(root, "extract.json"),
        probe="/usr/bin/env")
    _write(paths["source"], source_text)
    _write(paths["pin"], toolchain + "\n")
    _write(direct, b"direct-olean")
    _write(transitive, b"transitive-olean")
    _write(paths["helper"], b"setup-helper", executable=True)
    _write(paths["extraction"], "{}\n")
    setup = _setup(
        "Probe.Target", direct, transitive, runtime, grouped=True)
    _write_json(paths["setup"], setup)
    modules = ["Probe.Direct", "Probe.Transitive"]
    _write_json(paths["closure"], dict(
        schema=IMPORT_CLOSURE_SCHEMA, module="Probe.Target",
        source_sha256=sha256_file(paths["source"]), modules=modules,
        modules_sha256=sha256_sorted_json(modules)))
    artifacts = [
        _artifact(direct, "lean-search-artifact"),
        _artifact(transitive, "lean-search-artifact"),
        _artifact(runtime, "dynamic-search-artifact"),
    ]
    _broad_index(paths, artifacts, paths["setup"])
    index = json.load(open(paths["index"], encoding="utf-8"))
    index["toolchain"] = toolchain
    _write_json(paths["index"], index)
    value = produce_visibility(
        "Probe.Target", paths["source"], workspace, prefix,
        paths["helper"], paths["setup"], paths["closure"], paths["index"],
        [paths["lean"], runtime])
    assert validate_visibility(value, live_files=True) is value
    return value


def _case(toolchain, root, body, suffix, *, original_body="41",
          header="def target : Nat := ", target_kind="def"):
    prefix = "import Lean\n"
    original_text = prefix + header + original_body + suffix
    candidate_text = prefix + header + body + suffix
    target_start = len(prefix.encode("utf-8"))
    header_end = len((prefix + header).encode("utf-8"))
    baseline_end = len((prefix + header + original_body).encode("utf-8"))
    candidate_end = len((prefix + header + body).encode("utf-8"))
    original = original_text.encode("utf-8")
    candidate = candidate_text.encode("utf-8")
    visibility = _visibility(toolchain, root, original_text)
    if visibility is None:
        return None
    plan = build_plan(
        original, candidate, logical_file=visibility["source"]["path"],
        target_name="target", target_kind=target_kind,
        target_start=target_start, header_end=header_end,
        baseline_retained_end=baseline_end,
        candidate_retained_end=candidate_end, visibility=visibility,
        driver_sha256=sha256_file(DRIVER), allow_unisolated_test=True)
    return visibility, plan, original, candidate


ORACLE_BODY = (
    "by\n"
    "  run_tac\n"
    "    let source := (← Lean.getFileMap).source\n"
    "    let suffixCanary := \"S5_HELD_\" ++ \"OUT_CANARY\"\n"
    "    if source.contains suffixCanary then\n"
    "      throwError \"held-out suffix leaked through FileMap\"\n"
    "  exact 41\n"
    "-- S5_GENERATED_BODY_CANARY"
)

ORACLE_SUFFIX = (
    "\nrun_cmd do\n"
    "  let context ← readThe Lean.Elab.Command.Context\n"
    "  let generatedCanary := \"S5_GENERATED_\" ++ \"BODY_CANARY\"\n"
    "  if context.fileMap.source.contains generatedCanary then\n"
    "    throwError \"candidate syntax reached suffix FileMap\"\n"
    "theorem after : target = 41 := rfl\n"
    "-- S5_HELD_OUT_CANARY\n"
)


def _nonces(label):
    return [f"{index + 1:02x}" * 32 for index in range(4)]


def test_visibility_is_exact_and_refuses_declared_oracle_surfaces():
    with tempfile.TemporaryDirectory() as td:
        case = _case(TOOLCHAINS[0], td, "41", "\n#check target\n")
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, _ = case
        assert validate_visibility_artifact(
            visibility, live_files=True) is visibility
        assert visibility["schema"] == "v2b_s5_visibility_v1"
        assert plan["visibilityBinding"] == visibility["contract_sha256"]
        allowlisted = {row["path"] for row in visibility["allowlist"]}
        assert visibility["source"]["path"] not in allowlisted
        assert visibility["source"]["sha256"] == \
            sha256_file(visibility["source"]["path"])
        assert sha256_file(visibility["source"]["path"]) == \
            plan["originalModuleSha256"]
        assert visibility["mount_policy"] == {
            "mode": "exact-file-allowlist-v1",
            "source_transport": "framed-stdin",
            "bind_workspace_root": False,
            "bind_toolchain_root": False,
            "bind_search_roots": False,
        }
        forged = copy.deepcopy(visibility)
        forged["mount_policy"]["bind_workspace_root"] = True
        try:
            validate_visibility_artifact(forged, live_files=False)
            assert False, "runner accepted a broad workspace projection"
        except V2BError as err:
            assert "mount-policy" in str(err) or "contract" in str(err), err


def test_four_fresh_processes_hide_suffix_syntax_argv_and_files_cross_pin():
    for toolchain in TOOLCHAINS:
        with tempfile.TemporaryDirectory() as td:
            case = _case(toolchain, td, ORACLE_BODY, ORACLE_SUFFIX)
            if case is None:
                print(f"    [skip] {toolchain} is not installed")
                continue
            visibility, plan, original, candidate = case
            run_dir = os.path.join(td, "run")
            result = run_four_phase(
                plan, visibility, original, candidate, run_dir,
                allow_unisolated_test=True, nonce_sequence=_nonces(toolchain))
            assert result["summary"]["classification"] == "verified-pass"
            assert result["summary"]["pass"] == 1
            assert result["summary"]["completedPhases"] == list(PHASES)
            # The exact projection and direct argv contain no source file.
            assert visibility["source"]["path"] not in {
                row["path"] for row in visibility["allowlist"]}
            launcher = VisibilityLauncher(
                visibility, plan, allow_unisolated_test=True)
            for phase in PHASES:
                manifest_path = os.path.join(
                    result["phases"][phase]["directory"], "manifest.json")
                argv = launcher.prepare(phase, manifest_path).argv
                assert visibility["source"]["path"] not in argv
                assert not any("S5_HELD_OUT_CANARY" in arg or
                               "S5_GENERATED_BODY_CANARY" in arg
                               for arg in argv)
                manifest = open(manifest_path, "rb").read()
                assert b"S5_HELD_OUT_CANARY" not in manifest
                assert b"S5_GENERATED_BODY_CANARY" not in manifest
            terminals = [result["phases"][phase]["terminal"]
                         for phase in PHASES]
            # Each process exits before the next starts.  Unique PIDs make the
            # fresh-process property independently visible in the evidence.
            assert len({row["pid"] for row in terminals}) == 4, terminals
            for left, right in zip(terminals, terminals[1:]):
                assert left["endedWallTimeNs"] <= right["startedWallTimeNs"]
            # Requeue/re-entry validates and reuses exact immutable evidence.
            reused = run_four_phase(
                plan, visibility, original, candidate, run_dir,
                allow_unisolated_test=True, nonce_sequence=_nonces(toolchain))
            assert reused["reused"] is True
            assert reused["summary"] == result["summary"]


def test_candidate_process_global_state_does_not_cross_fresh_suffix():
    prefix = ("import Lean\naxiom bigVal : Nat\n"
              "axiom bigValEq : bigVal = 7\n")
    header = "def target : Nat := "
    body = (
        "by run_tac do\n"
        "  Lean.Meta.addSimpTheorem (Lean.Meta.simpExtension) `bigValEq "
        "true false .global 1000\n"
        "  Lean.Elab.Tactic.evalTactic (← `(tactic| exact 41))")
    suffix = (
        "\nrun_cmd do\n"
        "  let thms ← Lean.Elab.Command.liftCoreM <| "
        "Lean.Meta.simpExtension.getTheorems\n"
        "  if thms.isLemma (.decl `bigValEq) then\n"
        "    throwError \"candidate simp state leaked across process\"\n"
        "theorem targetOk : target = 41 := rfl\n")
    original_text = prefix + header + "41" + suffix
    candidate_text = prefix + header + body + suffix
    start = len(prefix.encode())
    header_end = len((prefix + header).encode())
    original_end = len((prefix + header + "41").encode())
    candidate_end = len((prefix + header + body).encode())
    original, candidate = original_text.encode(), candidate_text.encode()
    with tempfile.TemporaryDirectory() as td:
        visibility = _visibility(TOOLCHAINS[0], td, original_text)
        if visibility is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        plan = build_plan(
            original, candidate,
            logical_file=visibility["source"]["path"],
            target_name="target", target_kind="def", target_start=start,
            header_end=header_end, baseline_retained_end=original_end,
            candidate_retained_end=candidate_end, visibility=visibility,
            driver_sha256=sha256_file(DRIVER),
            allow_unisolated_test=True)
        result = run_four_phase(
            plan, visibility, original, candidate, os.path.join(td, "run"),
            allow_unisolated_test=True, nonce_sequence=_nonces("state"))
        assert result["summary"]["classification"] == "verified-pass"
        assert result["summary"]["pass"] == 1
        suffix_row = result["phases"]["candidate-suffix"]["parsed"][
            "terminal"]
        assert suffix_row["status"] == "verified"


def test_replayed_kernel_bundle_never_supplies_executable_runtime():
    suffix = "\n#eval target\n"
    with tempfile.TemporaryDirectory() as td:
        case = _case(
            TOOLCHAINS[0], td, "pure 41", suffix,
            original_body="pure 41", header="def target : IO Nat := ")
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, candidate = case
        result = run_four_phase(
            plan, visibility, original, candidate, os.path.join(td, "run"),
            allow_unisolated_test=True, nonce_sequence=_nonces("runtime"))
        assert result["summary"]["classification"] == "baseline-ineligible"
        assert result["summary"]["pass"] is None
        assert result["summary"]["completedPhases"] == [
            "baseline-target", "baseline-suffix"]
        row = result["phases"]["baseline-suffix"]["parsed"]["terminal"]
        assert row["reason"] == "suffix-elaboration-error"


def test_target_range_cannot_hide_a_second_generated_command():
    with tempfile.TemporaryDirectory() as td:
        case = _case(
            TOOLCHAINS[0], td, "41\n#check Nat", "\n#check target\n")
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, candidate = case
        result = run_four_phase(
            plan, visibility, original, candidate, os.path.join(td, "run"),
            allow_unisolated_test=True, nonce_sequence=_nonces("range"))
        assert result["summary"]["classification"] == \
            "verification-failure"
        assert result["summary"]["pass"] == 0
        assert result["summary"]["completedPhases"] == [
            "baseline-target", "baseline-suffix", "candidate-target"]
        row = result["phases"]["candidate-target"]["parsed"]["terminal"]
        assert row["reason"] == "trailing-target-syntax"


def test_unisolated_test_backend_trips_the_filesystem_oracle_sentinel():
    with tempfile.TemporaryDirectory() as td:
        source_path = os.path.realpath(os.path.join(
            td, "workspace", "Probe", "Target.lean"))
        body = (
            "by\n"
            "  run_tac\n"
            f"    let path : System.FilePath := {json.dumps(source_path)}\n"
            "    if ← path.pathExists then\n"
            "      throwError \"logical source path is visible\"\n"
            "  exact 41")
        case = _case(TOOLCHAINS[0], td, body, "\n#check target\n")
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, candidate = case
        assert visibility["source"]["path"] == source_path
        result = run_four_phase(
            plan, visibility, original, candidate, os.path.join(td, "run"),
            allow_unisolated_test=True, nonce_sequence=_nonces("fs-oracle"))
        assert result["summary"]["classification"] == \
            "verification-failure"
        assert result["summary"]["pass"] == 0
        row = result["phases"]["candidate-target"]["parsed"]["terminal"]
        assert row["reason"] == "elaboration-error"


def test_bundle_transcript_tampering_fails_closed_and_is_not_retried():
    with tempfile.TemporaryDirectory() as td:
        case = _case(
            TOOLCHAINS[0], td, ORACLE_BODY, ORACLE_SUFFIX)
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, candidate = case
        run_dir = os.path.join(td, "run")
        result = run_four_phase(
            plan, visibility, original, candidate, run_dir,
            allow_unisolated_test=True, nonce_sequence=_nonces("tamper"))
        target = result["phases"]["candidate-target"]
        stdout_path = os.path.join(target["directory"], "stdout.bin")
        stdout = open(stdout_path, "rb").read()
        assert b'"bundle"' in stdout
        forged = stdout.replace(b'[["s","target"]]',
                                b'[["s","forged"]]', 1)
        assert forged != stdout
        with open(stdout_path, "wb") as handle:
            handle.write(forged)
        try:
            validate_summary(
                run_dir, plan, visibility, original, candidate,
                allow_unisolated_test=True)
            assert False, "tampered outcome-bearing transcript was accepted"
        except V2BError as err:
            assert "terminal byte" in str(err) or "drift" in str(err), err
        # The committed GO journal remains, so execution cannot select a new
        # attempt after the tamper is detected.
        attempt_root = os.path.dirname(target["directory"])
        before = sorted(os.listdir(attempt_root))
        try:
            run_four_phase(
                plan, visibility, original, candidate, run_dir,
                allow_unisolated_test=True,
                nonce_sequence=["ab" * 32] * 4)
            assert False, "tampered committed run was rerun"
        except V2BError:
            pass
        assert sorted(os.listdir(attempt_root)) == before


def test_durable_write_failure_kills_and_reaps_the_waiting_child():
    with tempfile.TemporaryDirectory() as td:
        case = _case(TOOLCHAINS[0], td, "41", "\n#check target\n")
        if case is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        visibility, plan, original, candidate = case
        original_write = four_phase._write_new_json
        original_popen = four_phase.subprocess.Popen
        children = []

        def fail_go_intent(path, value):
            if os.path.basename(path) == "go-intent.json":
                raise V2BError("injected durable-write failure")
            return original_write(path, value)

        def record_popen(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(
                four_phase, "_write_new_json", side_effect=fail_go_intent), \
                mock.patch.object(
                    four_phase.subprocess, "Popen",
                    side_effect=record_popen), \
                mock.patch.object(
                    four_phase, "_kill_group",
                    wraps=four_phase._kill_group) as kill_group:
            try:
                run_four_phase(
                    plan, visibility, original, candidate,
                    os.path.join(td, "run"), allow_unisolated_test=True,
                    nonce_sequence=_nonces("write-failure"))
                assert False, "durable-write failure was ignored"
            except V2BError as err:
                assert "injected durable-write failure" in str(err), err
            assert kill_group.called
        assert children and all(child.poll() is not None for child in children)
        attempt_root = os.path.join(
            td, "run", "attempts", "baseline-target")
        attempts = os.listdir(attempt_root)
        assert len(attempts) == 1
        evidence = set(os.listdir(os.path.join(attempt_root, attempts[0])))
        assert "start-prefix.bin" in evidence
        assert "go-intent.json" not in evidence


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
    print("V2B S5 FOUR-PHASE RUNNER TESTS PASS")
