#!/usr/bin/env python3
"""Toolchain-free four-phase envelope coverage through the dry-run stub.

tests/test_run_v2b_s5_four_phase.py silently skips on hosts without the two
pinned elan toolchains, leaving the envelope with no executable coverage
there.  These tests drive the UNMODIFIED ``run_v2b_s5_four_phase`` machinery
— journaling, GO handshake, classification, summary derivation, byte-level
revalidation, immutability — through the protocol-faithful stub driver of
``v2b_s5_dryrun.py``.  They are envelope/protocol coverage, never
oracle-isolation evidence (that remains the pinned-toolchain suite plus the
cluster release gates)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v2b_s5_four_phase import (LEAN_DRIVER, MARKER_PREFIX, PHASES,
                                   build_plan, run_four_phase,
                                   validate_summary)
from v2b_common import V2BError, sha256_file
from v2b_s5_dryrun import (STUB_HARNESS_CRASH, STUB_SUFFIX_FAIL,
                           STUB_TARGET_FAIL, STUB_TYPE_DRIFT,
                           build_stub_toolchain, build_toy_workspace)
from v2b_s5_visibility import produce_visibility


def _case(root, body, *, original_body="41"):
    toolchain = build_stub_toolchain(root)
    workspace = build_toy_workspace(root, toolchain,
                                    original_body=original_body)
    visibility = produce_visibility(
        workspace["module"], workspace["source"], workspace["workspace"],
        toolchain["toolchain"], workspace["helper"], workspace["setup"],
        workspace["closure"], workspace["index"],
        workspace["runtime_paths"])
    original = workspace["original_text"].encode("utf-8")
    blob = body.encode("utf-8")
    candidate = (original[:workspace["header_end_byte"]] + blob
                 + original[workspace["target_end_byte"]:])
    plan = build_plan(
        original, candidate, logical_file=visibility["source"]["path"],
        target_name=workspace["target_name"],
        target_kind=workspace["target_kind"],
        target_start=workspace["target_start_byte"],
        header_end=workspace["header_end_byte"],
        baseline_retained_end=workspace["target_end_byte"],
        candidate_retained_end=workspace["header_end_byte"] + len(blob),
        visibility=visibility, driver_sha256=sha256_file(LEAN_DRIVER),
        allow_unisolated_test=True)
    return plan, visibility, original, candidate


def _run(root, body, *, original_body="41"):
    plan, visibility, original, candidate = _case(
        root, body, original_body=original_body)
    run_dir = os.path.join(root, "run")
    result = run_four_phase(plan, visibility, original, candidate, run_dir,
                            allow_unisolated_test=True)
    return result, (plan, visibility, original, candidate, run_dir)


def test_stub_truth_table_matches_the_frozen_summary_vocabulary():
    cases = (
        ("41 -- ok", "41", "verified-pass", 1, 4),
        (f"41 -- {STUB_TARGET_FAIL}", "41", "verification-failure", 0, 3),
        (f"41 -- {STUB_SUFFIX_FAIL}", "41", "verification-failure", 0, 4),
        (f"41 -- {STUB_TYPE_DRIFT}", "41", "candidate-type-drift", 0, 3),
        ("41 -- ok", f"41 -- {STUB_TARGET_FAIL}",
         "baseline-ineligible", None, 1),
        ("41 -- ok", f"41 -- {STUB_SUFFIX_FAIL}",
         "baseline-ineligible", None, 2),
        ("41 -- ok", f"41 -- {STUB_HARNESS_CRASH}",
         "harness-invalid", None, 1),
    )
    for body, original_body, classification, passed, n_phases in cases:
        with tempfile.TemporaryDirectory() as td:
            result, _ = _run(td, body, original_body=original_body)
            summary = result["summary"]
            assert summary["classification"] == classification, (
                body, original_body, summary)
            assert summary["pass"] == passed
            assert summary["completedPhases"] == list(PHASES[:n_phases])


def test_fresh_processes_and_immutable_evidence_reuse():
    with tempfile.TemporaryDirectory() as td:
        result, context = _run(td, "41 -- ok")
        plan, visibility, original, candidate, run_dir = context
        terminals = [result["phases"][phase]["terminal"] for phase in PHASES]
        assert len({row["pid"] for row in terminals}) == 4
        for left, right in zip(terminals, terminals[1:]):
            assert left["endedWallTimeNs"] <= right["startedWallTimeNs"]
        reused = run_four_phase(plan, visibility, original, candidate,
                                run_dir, allow_unisolated_test=True)
        assert reused["reused"] is True
        assert reused["summary"] == result["summary"]


def test_stub_transcript_tampering_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        result, context = _run(td, "41 -- ok")
        plan, visibility, original, candidate, run_dir = context
        target = result["phases"]["candidate-target"]
        stdout_path = os.path.join(target["directory"], "stdout.bin")
        stdout = open(stdout_path, "rb").read()
        assert MARKER_PREFIX.encode("ascii") in stdout
        forged = stdout.replace(b'"n_bundled_constants":1',
                                b'"n_bundled_constants":2', 1)
        assert forged != stdout
        with open(stdout_path, "wb") as handle:
            handle.write(forged)
        try:
            validate_summary(run_dir, plan, visibility, original, candidate,
                             allow_unisolated_test=True)
            assert False, "tampered stub transcript was accepted"
        except V2BError as err:
            assert "drift" in str(err), err


def test_candidate_bytes_never_reach_manifest_or_argv():
    marker = "S5_STUB_BODY_CANARY"
    with tempfile.TemporaryDirectory() as td:
        result, _ = _run(td, f"41 -- {marker}")
        for phase in PHASES:
            directory = result["phases"][phase]["directory"]
            manifest = open(os.path.join(directory, "manifest.json"),
                            "rb").read()
            assert marker.encode("ascii") not in manifest
        # The stub suffix phase sees only the masked body: the marker must
        # be absent from the candidate-suffix stdout evidence too.
        suffix_stdout = open(os.path.join(
            result["phases"]["candidate-suffix"]["directory"],
            "stdout.bin"), "rb").read()
        assert marker.encode("ascii") not in suffix_stdout


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
