#!/usr/bin/env python3
"""GPU-free production-envelope and termination truth-table tests."""
import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_v2b_lean_verify as runner
from run_v2b_lean_verify import (
    CONTROL_HEADROOM_BYTES, RESOURCE_LIMITS, RUNTIME_SCHEMA,
    SANDBOX_CONTRACT_SHA256, _execute, _require_private_run_root,
    _validate_manifest_setup_join, classify_candidate,
    classify_execution, load_verified_baseline, run_fresh)
from provenance import source_tree_hash
from v2b_behavior_extract import LEAN_EXTRACTION_CONTRACT_SHA256
from v2b_behavior_verify import (
    LEAN_PARSE_DRIVER, LEAN_VERIFY_CONTRACT_SHA256, LEAN_VERIFY_DRIVER,
    LEAN_VERIFY_MANIFEST_SCHEMA, LEAN_VERIFY_OUTPUT_SCHEMA,
    bind_lean_verify_manifest, lean_verify_output_marker)
from v2b_common import V2BError, sha256_file, sha256_sorted_json


NONCE = "c" * 64
HEX = "a" * 64


def _rows(manifest):
    prevalidation = dict(
        schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="prevalidation",
        mode=manifest["mode"],
        invocation_binding=manifest["invocationBinding"],
        module_name=manifest["moduleName"],
        target_name=manifest["targetName"],
        target_kind=manifest["targetKind"],
        target_start_byte=manifest["targetStartByte"],
        target_end_byte=manifest["targetEndByte"],
        header_end_byte=manifest["headerEndByte"],
        body_delimiter=manifest["bodyDelimiter"],
        logical_filename=manifest["logicalFileName"],
        original_sha256=manifest["originalSha256"],
        module_setup_sha256=manifest["moduleSetupSha256"],
        boundary_artifact_sha256=manifest["boundaryArtifactSha256"],
        span_id=manifest["spanId"],
        s4_contract_sha256=manifest["s4ContractSha256"],
        s4_driver_sha256=manifest["s4DriverSha256"],
        s5_contract_sha256=manifest["s5ContractSha256"],
        s5_driver_sha256=manifest["s5DriverSha256"],
        semantic_context_binding=manifest["semanticContextBinding"],
        runtime_sha256=manifest["runtimeSha256"],
        baseline_evidence_sha256=manifest["baselineCertificate"][
            "baselineEvidenceSha256"],
        n_prior_commands=0)
    start = dict(
        schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="candidate-start",
        invocation_binding=manifest["invocationBinding"],
        sample_id=manifest["samples"][0]["id"],
        baseline_evidence_sha256=manifest["baselineCertificate"][
            "baselineEvidenceSha256"])
    acknowledged = dict(start, record_type="candidate-go-accepted")
    success = dict(
        schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="sample",
        status="verified", outcome_class="lean-def-typecheck",
        target_name=manifest["targetName"], target_info_kind="definition",
        type_fingerprint="Nat", type_expression=[
            "const", ["str", ["anonymous"], "Nat"], []],
        type_kernel_equal=True, n_level_params=0, n_new_constants=1,
        n_axioms=0, forbidden_surfaces=[], elaboration_attempted=True,
        elaboration_succeeded=True, sample_id=manifest["samples"][0]["id"])
    return prevalidation, start, acknowledged, success


def _transcript(rows):
    marker = lean_verify_output_marker(NONCE)
    return ("\n".join(marker + json.dumps(
        row, ensure_ascii=False, separators=(",", ":")) for row in rows) \
        + "\n").encode("utf-8")


def _candidate_manifest(td, runtime_sha=HEX):
    original = b"import Lean\ndef target : Nat := 0\n"
    start = original.index(b"def target")
    header = original.index(b":=", start)
    end = original.index(b"\n", header)
    original_path = os.path.join(td, "Original.lean")
    reconstructed_path = os.path.join(td, "Candidate.lean")
    setup_path = os.path.join(td, "setup.json")
    open(original_path, "wb").write(original)
    reconstructed = original[:header] + b":= 1" + original[end:]
    open(reconstructed_path, "wb").write(reconstructed)
    with open(setup_path, "w", encoding="utf-8") as handle:
        json.dump(dict(dynlibs=[], importArts={}, isModule=False,
                       name="RunnerFixture", options={}, plugins=[]), handle)
    type_expression = ["const", ["str", ["anonymous"], "Nat"], []]
    certificate = dict(
        schema="v2b_lean_baseline_certificate_v1",
        baselineEvidenceSha256="d" * 64,
        baselineInvocationBinding="e" * 64,
        semanticContextBinding=None,
        baselineRuntimeSha256=runtime_sha,
        nPriorCommands=0, targetName="target", targetInfoKind="definition",
        nLevelParams=0, typeExpression=type_expression,
        typeExpressionSha256=sha256_sorted_json(type_expression))
    raw = dict(
        schema=LEAN_VERIFY_MANIFEST_SCHEMA, mode="candidate",
        originalFile=original_path, logicalFileName=original_path,
        originalSha256=sha256_file(original_path),
        moduleSetupFile=setup_path,
        moduleSetupSha256=sha256_file(setup_path),
        moduleName="RunnerFixture", targetName="target", targetKind="def",
        targetStartByte=start, targetEndByte=end, headerEndByte=header,
        bodyDelimiter=":=", boundaryArtifactSha256=HEX, spanId="b" * 64,
        s4ContractSha256=LEAN_EXTRACTION_CONTRACT_SHA256,
        s4DriverSha256=sha256_file(LEAN_PARSE_DRIVER),
        s5ContractSha256=LEAN_VERIFY_CONTRACT_SHA256,
        s5DriverSha256=sha256_file(LEAN_VERIFY_DRIVER),
        runtimeSha256=runtime_sha,
        optionOverrides=[], baselineCertificate=certificate,
        samples=[dict(
            id="sample", reconstructedFile=reconstructed_path,
            reconstructedSha256=sha256_file(reconstructed_path),
            retainedEndByte=header + len(b":= 1"),
            extractedBodySha256=sha256_file(reconstructed_path),
            s4EvidenceSha256=HEX)])
    # Correct the body hash and fill the semantic binding before invocation.
    import hashlib
    raw["samples"][0]["extractedBodySha256"] = hashlib.sha256(
        b":= 1").hexdigest()
    from v2b_behavior_verify import lean_verify_semantic_context_binding
    certificate["semanticContextBinding"] = \
        lean_verify_semantic_context_binding(raw)
    return bind_lean_verify_manifest(raw)


def _test_runtime(td):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lean = os.path.join(
        os.path.expanduser("~/.elan/toolchains"),
        "leanprover--lean4---v4.32.0", "bin", "lean")
    if not os.path.isfile(lean):
        return None
    bwrap = shutil.which("true") or "/usr/bin/true"
    setup_index = os.path.join(td, "setup-index.json")
    with open(setup_index, "w", encoding="utf-8") as handle:
        json.dump({"schema": "v2b_lean_setup_index_v2"}, handle)
    environment = {"HOME": "/tmp/home", "TMPDIR": "/tmp"}
    runtime = dict(
        schema=RUNTIME_SCHEMA, repo="fixture", corpus_git_sha="a" * 40,
        corpus_root=root, toolchain="leanprover/lean4:v4.32.0",
        harness_source_tree_sha256=source_tree_hash(),
        setup_index=dict(
            path=setup_index, sha256=sha256_file(setup_index),
            schema="v2b_lean_setup_index_v2"),
        lake_environment={
            "LEAN_PATH": None, "LEAN_SRC_PATH": None,
            "LD_LIBRARY_PATH": None, "DYLD_LIBRARY_PATH": None,
            "PATH": os.environ.get("PATH", "")},
        lake_environment_sha256=None,
        search_roots_sha256="a" * 64,
        search_directories_sha256="b" * 64,
        search_symlinks_sha256="c" * 64,
        artifacts_sha256="d" * 64,
        lean=dict(path=lean, sha256=sha256_file(lean), version="test"),
        driver=dict(path=LEAN_VERIFY_DRIVER,
                    sha256=sha256_file(LEAN_VERIFY_DRIVER)),
        wrapper=dict(
            path=os.path.join(root, "run_v2b_lean_verify.py"),
            sha256=sha256_file(os.path.join(
                root, "run_v2b_lean_verify.py"))),
        bwrap=dict(path=bwrap, sha256=sha256_file(bwrap), version="test"),
        system_ro_bindings=[],
        sandbox_contract_sha256=SANDBOX_CONTRACT_SHA256,
        child_environment=environment,
        child_environment_sha256=sha256_sorted_json(environment),
        resource_limits=copy.deepcopy(RESOURCE_LIMITS), cwd=root,
        inner_argv_template=[
            lean, "--run", LEAN_VERIFY_DRIVER, "<manifest.json>"],
    )
    runtime["lake_environment_sha256"] = sha256_sorted_json(
        runtime["lake_environment"])
    return runtime


def _baseline_manifest(candidate):
    raw = copy.deepcopy(candidate)
    raw.pop("invocationBinding")
    raw["mode"] = "baseline"
    raw["baselineCertificate"] = None
    raw["samples"] = []
    return bind_lean_verify_manifest(raw)


def _baseline_rows(manifest, verified=True):
    pre = dict(
        schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="prevalidation",
        mode="baseline", invocation_binding=manifest["invocationBinding"],
        module_name=manifest["moduleName"], target_name=manifest["targetName"],
        target_kind=manifest["targetKind"],
        target_start_byte=manifest["targetStartByte"],
        target_end_byte=manifest["targetEndByte"],
        header_end_byte=manifest["headerEndByte"],
        body_delimiter=manifest["bodyDelimiter"],
        logical_filename=manifest["logicalFileName"],
        original_sha256=manifest["originalSha256"],
        module_setup_sha256=manifest["moduleSetupSha256"],
        boundary_artifact_sha256=manifest["boundaryArtifactSha256"],
        span_id=manifest["spanId"],
        s4_contract_sha256=manifest["s4ContractSha256"],
        s4_driver_sha256=manifest["s4DriverSha256"],
        s5_contract_sha256=manifest["s5ContractSha256"],
        s5_driver_sha256=manifest["s5DriverSha256"],
        semantic_context_binding=manifest["semanticContextBinding"],
        runtime_sha256=manifest["runtimeSha256"],
        baseline_evidence_sha256=None, n_prior_commands=0)
    start = dict(
        schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="baseline-start",
        invocation_binding=manifest["invocationBinding"])
    acknowledged = dict(start, record_type="baseline-go-accepted")
    if verified:
        result = dict(
            schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="baseline",
            status="verified", outcome_class="lean-def-typecheck",
            target_name="target", target_info_kind="definition",
            type_fingerprint="Nat", type_expression=[
                "const", ["str", ["anonymous"], "Nat"], []],
            type_kernel_equal=None, n_level_params=0, n_new_constants=1,
            n_axioms=0, forbidden_surfaces=[], elaboration_attempted=True,
            elaboration_succeeded=True)
    else:
        result = dict(
            schema=LEAN_VERIFY_OUTPUT_SCHEMA, record_type="baseline",
            status="verification-failure", reason="sorry",
            outcome_class="lean-def-typecheck", forbidden_surfaces=["sorry"],
            elaboration_attempted=False, elaboration_succeeded=False)
    return pre, start, acknowledged, result


def test_candidate_stage_and_termination_truth_table():
    with tempfile.TemporaryDirectory() as td:
        manifest = _candidate_manifest(td)
        pre, start, acknowledged, success = _rows(manifest)
        complete = _transcript([pre, start, acknowledged, success])
        value = classify_execution(manifest, complete, NONCE, 0)
        assert value["classification"] == "verified-pass"
        assert value["outcome_bearing"] is True

        started = _transcript([pre, start, acknowledged])
        for kwargs, expected in (
                ({"returncode": -9, "timed_out": True},
                 "candidate-timeout"),
                ({"returncode": -9, "output_limited": True},
                 "candidate-output-limit"),
                ({"returncode": -9}, "candidate-terminated")):
            value = classify_execution(manifest, started, NONCE, **kwargs)
            assert value["classification"] == expected
            assert value["outcome_bearing"] is True

        truncated = complete[:-25]
        for kwargs, expected in (
                ({"returncode": -9, "timed_out": True},
                 "candidate-timeout"),
                ({"returncode": -9, "output_limited": True},
                 "candidate-output-limit")):
            value = classify_execution(manifest, truncated, NONCE, **kwargs)
            assert value["classification"] == expected
            assert value["outcome_bearing"] is True
            assert value["protocol_valid"] is True

        before = classify_execution(manifest, b"", NONCE, -9)
        assert before["classification"] == "harness-invalid"
        assert before["outcome_bearing"] is False


def test_output_cap_after_ack_retains_durable_go_acceptance():
    """A partial oversized result cannot erase an already-flushed ack."""
    with tempfile.TemporaryDirectory() as td:
        manifest = _candidate_manifest(td)
        pre, start, acknowledged, _ = _rows(manifest)
        prestart = _transcript([pre, start])
        code = (
            "import json,sys\n"
            "rows=json.loads(sys.argv[1])\n"
            "nonce=sys.stdin.readline().rstrip('\\n')\n"
            "marker='@@V2B_LEAN_VERIFY:'+nonce+'@@'\n"
            "for row in rows[:2]:\n"
            " sys.stdout.write(marker+json.dumps(row,separators=(',',':'))+'\\n')\n"
            " sys.stdout.flush()\n"
            "go=sys.stdin.readline()\n"
            "extra=sys.stdin.read(1)\n"
            "if go != 'GO:'+nonce+'\\n' or extra != '': sys.exit(2)\n"
            "sys.stdout.write(marker+json.dumps(rows[2],separators=(',',':'))+'\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdout.write(marker+'{\\\"schema\\\":'+('x'*200000))\n"
            "sys.stdout.flush()\n")
        attempt_dir = os.path.join(td, "attempt")
        os.mkdir(attempt_dir)
        attempt = dict(directory=attempt_dir, attempt_id="f" * 64)
        limits = copy.deepcopy(RESOURCE_LIMITS)
        limits["timeout_seconds"] = 10
        limits["cpu_seconds"] = 15
        limits["stdout_bytes"] = len(prestart) + \
            CONTROL_HEADROOM_BYTES
        result = _execute(
            [sys.executable, "-c", code,
             json.dumps([pre, start, acknowledged])],
            td, os.environ.copy(), NONCE, manifest, limits, attempt,
            enforce_address_space=False)
        assert result["output_limited"] is True
        assert result["go_intent_sha256"] is not None
        assert result["go_accepted_sha256"] is not None
        assert os.path.isfile(os.path.join(attempt_dir, "go-accepted.json"))
        classified = classify_execution(
            manifest, result["stdout"], NONCE, result["returncode"],
            output_limited=True, authorization_committed=True)
        assert classified["authenticated_prefix_stage"] == \
            "candidate-started"
        assert classified["classification"] == "candidate-output-limit"
        assert classified["outcome_bearing"] is True


def test_pre_go_headroom_refusal_never_starts_candidate():
    """One byte below reserved headroom must refuse GO and remain retryable."""
    with tempfile.TemporaryDirectory() as td:
        manifest = _candidate_manifest(td)
        pre, start, _, _ = _rows(manifest)
        prestart = _transcript([pre, start])
        sentinel = os.path.join(td, "post-go")
        code = (
            "import json,sys\n"
            "rows=json.loads(sys.argv[1])\n"
            "nonce=sys.stdin.readline().rstrip('\\n')\n"
            "marker='@@V2B_LEAN_VERIFY:'+nonce+'@@'\n"
            "for row in rows:\n"
            " sys.stdout.write(marker+json.dumps(row,separators=(',',':'))+'\\n')\n"
            " sys.stdout.flush()\n"
            "go=sys.stdin.readline()\n"
            "if go: open(sys.argv[2],'w').write('started')\n")
        attempt_dir = os.path.join(td, "attempt")
        os.mkdir(attempt_dir)
        attempt = dict(directory=attempt_dir, attempt_id="f" * 64)
        limits = copy.deepcopy(RESOURCE_LIMITS)
        limits["timeout_seconds"] = 10
        limits["cpu_seconds"] = 15
        limits["stdout_bytes"] = len(prestart) + \
            CONTROL_HEADROOM_BYTES - 1
        result = _execute(
            [sys.executable, "-c", code, json.dumps([pre, start]), sentinel],
            td, os.environ.copy(), NONCE, manifest, limits, attempt,
            enforce_address_space=False)
        assert result["output_limited"] is True
        assert result["go_intent_sha256"] is None
        assert result["go_accepted_sha256"] is None
        assert not os.path.exists(sentinel)
        classified = classify_execution(
            manifest, result["stdout"], NONCE, result["returncode"],
            output_limited=True, authorization_committed=False)
        assert classified["authenticated_prefix_stage"] == \
            "candidate-awaiting-authorization"
        assert classified["classification"] == "harness-invalid"
        assert classified["outcome_bearing"] is False


def test_baseline_truth_table_never_makes_a_model_zero():
    with tempfile.TemporaryDirectory() as td:
        baseline = _baseline_manifest(_candidate_manifest(td))
        pre, start, acknowledged, verified = _baseline_rows(
            baseline, verified=True)
        value = classify_execution(
            baseline, _transcript([pre, start, acknowledged, verified]),
            NONCE, 0)
        assert value["classification"] == "baseline-verified"
        assert value["outcome_bearing"] is True
        pre, start, acknowledged, failed = _baseline_rows(
            baseline, verified=False)
        value = classify_execution(
            baseline, _transcript([pre, start, acknowledged, failed]),
            NONCE, 0)
        assert value["classification"] == "baseline-ineligible"
        assert value["outcome_bearing"] is True
        timeout = classify_execution(
            baseline, _transcript([pre]), NONCE, -9, timed_out=True)
        assert timeout["classification"] == "harness-invalid"
        assert timeout["outcome_bearing"] is False
        started_timeout = classify_execution(
            baseline, _transcript([pre, start, acknowledged]), NONCE, -9,
            timed_out=True)
        assert started_timeout["classification"] == "harness-invalid"
        assert started_timeout["outcome_bearing"] is False


def test_authenticated_corruption_is_hard_and_untrusted_noise_is_ignored():
    with tempfile.TemporaryDirectory() as td:
        manifest = _candidate_manifest(td)
        pre, start, acknowledged, success = _rows(manifest)
        marker = lean_verify_output_marker(NONCE).encode("ascii")
        forged = b"@@V2B_LEAN_VERIFY@@{\"status\":\"verified\"}\n"
        valid = classify_execution(
            manifest,
            forged + _transcript([pre, start, acknowledged, success]),
            NONCE, 0)
        assert valid["classification"] == "verified-pass"
        corrupted = classify_execution(
            manifest, _transcript([pre, start, acknowledged]) + b"\n"
            + marker + b"\xff",
            NONCE, 0)
        assert corrupted["classification"] == "evidence-invalid"
        assert corrupted["outcome_bearing"] is True
        assert corrupted["protocol_valid"] is False


def test_frozen_resource_and_sandbox_bindings():
    assert RESOURCE_LIMITS["timeout_seconds"] == 300
    assert RESOURCE_LIMITS["stdout_bytes"] == 8 * 1024**2
    assert RESOURCE_LIMITS["stderr_bytes"] == 8 * 1024**2
    assert len(SANDBOX_CONTRACT_SHA256) == 64


def test_nonce_journal_must_be_outside_broad_child_visible_mounts():
    with tempfile.TemporaryDirectory() as td:
        runtime = _test_runtime(td)
        if runtime is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        for unsafe in (
                runtime["corpus_root"],
                os.path.join(runtime["corpus_root"], "private-s5-runs")):
            try:
                _require_private_run_root(unsafe, runtime)
                assert False, "child-visible S5 nonce journal was accepted"
            except V2BError as err:
                assert "child-visible" in str(err)
        assert _require_private_run_root(td, runtime) == os.path.realpath(td)


def test_setup_join_requires_exact_original_logical_filename():
    with tempfile.TemporaryDirectory() as td:
        manifest = _candidate_manifest(td)
        row = dict(
            source=manifest["originalFile"],
            source_sha256=manifest["originalSha256"],
            module=manifest["moduleName"],
            setup=manifest["moduleSetupFile"],
            setup_sha256=manifest["moduleSetupSha256"])
        runtime = {"setup_index": {"path": os.path.join(td, "index.json"),
                                    "sha256": HEX}}
        real_load = runner.load_json
        real_validate = runner.validate_setup_index
        runner.load_json = lambda path, schema: ({}, HEX)
        runner.validate_setup_index = lambda *args, **kwargs: [row]
        try:
            assert _validate_manifest_setup_join(manifest, runtime) == row
            for field, value in (
                    ("logicalFileName", os.path.join(td, "Alias.lean")),
                    ("originalFile", os.path.join(
                        td, ".", "Original.lean"))):
                altered = copy.deepcopy(manifest)
                altered[field] = value
                try:
                    _validate_manifest_setup_join(altered, runtime)
                    assert False, "aliased S5 logical source path was accepted"
                except V2BError as err:
                    assert "source/module/setup" in str(err)
        finally:
            runner.load_json = real_load
            runner.validate_setup_index = real_validate


def test_only_the_canonical_s5_driver_can_execute():
    with tempfile.TemporaryDirectory() as td:
        runtime = _test_runtime(td)
        if runtime is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        rogue = os.path.join(td, "Rogue.lean")
        shutil.copyfile(LEAN_VERIFY_DRIVER, rogue)
        with open(rogue, "a", encoding="utf-8") as handle:
            handle.write("\n-- rogue verifier\n")
        runtime["driver"] = dict(
            path=rogue, sha256=sha256_file(rogue))
        manifest = _baseline_manifest(_candidate_manifest(
            td, sha256_sorted_json(runtime)))
        try:
            run_fresh(
                manifest, runtime, os.path.join(td, "runs"),
                allow_unisolated_test=True, _nonce_for_test="1" * 64)
            assert False, "noncanonical S5 driver executed"
        except V2BError as err:
            assert "canonical" in str(err)


def test_fresh_process_bundle_and_baseline_certificate_reuse():
    with tempfile.TemporaryDirectory() as td:
        runtime = _test_runtime(td)
        if runtime is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        runtime_sha = sha256_sorted_json(runtime)
        candidate_seed = _candidate_manifest(td, runtime_sha)
        baseline_manifest = _baseline_manifest(candidate_seed)
        run_dir = os.path.join(td, "runs")
        baseline = run_fresh(
            baseline_manifest, runtime, run_dir,
            allow_unisolated_test=True, _nonce_for_test="1" * 64)
        assert baseline["evidence"]["classification"] == \
            "baseline-verified"
        certified = load_verified_baseline(
            baseline["directory"], runtime, require_production=False)
        candidate_raw = copy.deepcopy(candidate_seed)
        candidate_raw.pop("invocationBinding")
        candidate_raw["baselineCertificate"] = certified["certificate"]
        candidate = bind_lean_verify_manifest(candidate_raw)
        completed = run_fresh(
            candidate, runtime, run_dir,
            baseline_directory=baseline["directory"],
            allow_unisolated_test=True, _nonce_for_test="2" * 64)
        assert completed["evidence"]["classification"] == "verified-pass"
        assert os.path.isfile(os.path.join(
            completed["directory"], "runtime.json"))
        assert os.path.isfile(os.path.join(
            run_dir, "locks", candidate["invocationBinding"] + ".lock"))
        classified = classify_candidate(
            completed["directory"], runtime, require_production=False)
        assert classified["pass"] == 1
        reused = run_fresh(
            candidate, runtime, run_dir,
            baseline_directory=baseline["directory"],
            allow_unisolated_test=True, _nonce_for_test="3" * 64)
        assert reused["directory"] == completed["directory"]
        assert reused["evidence_sha256"] == completed["evidence_sha256"]
        altered = copy.deepcopy(candidate)
        altered.pop("invocationBinding")
        altered["baselineCertificate"]["baselineEvidenceSha256"] = "9" * 64
        altered = bind_lean_verify_manifest(altered)
        try:
            run_fresh(
                altered, runtime, run_dir,
                baseline_directory=baseline["directory"],
                allow_unisolated_test=True, _nonce_for_test="5" * 64)
            assert False, "a caller-supplied baseline certificate was used"
        except V2BError:
            pass

        with open(os.path.join(completed["directory"], "stdout.bin"),
                  "ab") as handle:
            handle.write(b"tamper")
        try:
            classify_candidate(
                completed["directory"], runtime,
                require_production=False)
            assert False, "tampered raw execution bytes were accepted"
        except V2BError:
            pass


def test_durable_candidate_go_intent_without_ack_is_an_immutable_zero():
    """A kill after committed GO cannot disappear or become retryable."""
    with tempfile.TemporaryDirectory() as td:
        runtime = _test_runtime(td)
        if runtime is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        runtime_sha = sha256_sorted_json(runtime)
        candidate_seed = _candidate_manifest(td, runtime_sha)
        baseline_manifest = _baseline_manifest(candidate_seed)
        run_dir = os.path.join(td, "runs")
        baseline = run_fresh(
            baseline_manifest, runtime, run_dir,
            allow_unisolated_test=True, _nonce_for_test="1" * 64)
        certified = load_verified_baseline(
            baseline["directory"], runtime, require_production=False)
        raw = copy.deepcopy(candidate_seed)
        raw.pop("invocationBinding")
        raw["baselineCertificate"] = certified["certificate"]
        candidate = bind_lean_verify_manifest(raw)

        real_execute = runner._execute

        def killed_after_intent(
                argv, cwd, env, nonce, manifest, limits, attempt,
                enforce_address_space=True):
            assert nonce == NONCE
            pre, start, _, _ = _rows(manifest)
            prefix = _transcript([pre, start])
            runner._write_new_bytes(os.path.join(
                attempt["directory"], "start-prefix.bin"), prefix)
            intent = dict(
                schema=runner.GO_INTENT_SCHEMA,
                attempt_id=attempt["attempt_id"],
                invocation_binding=manifest["invocationBinding"],
                mode="candidate",
                authenticated_stage="candidate-awaiting-authorization",
                stdout_prefix_sha256=runner.sha256_bytes(prefix),
                stdout_prefix_bytes=len(prefix),
                nonce_sha256=runner.sha256_bytes(
                    nonce.encode("ascii")))
            intent_sha = runner._write_new_durable_json(os.path.join(
                attempt["directory"], "go-intent.json"), intent)
            return dict(
                stdout=prefix, stderr=b"", returncode=-9,
                timed_out=True, output_limited=False, wall_time_ns=1,
                go_intent_sha256=intent_sha,
                go_accepted_sha256=None)

        runner._execute = killed_after_intent
        try:
            completed = run_fresh(
                candidate, runtime, run_dir,
                baseline_directory=baseline["directory"],
                allow_unisolated_test=True, _nonce_for_test=NONCE)
            assert completed["evidence"]["classification"] == \
                "candidate-timeout"
            assert completed["evidence"]["outcome_bearing"] is True
            assert completed["evidence"]["go_accepted_sha256"] is None
            attempt_dir = os.path.join(
                run_dir, "attempts", candidate["invocationBinding"],
                completed["evidence"]["attempt_id"])
            assert not os.path.exists(os.path.join(
                attempt_dir, "go-accepted.json"))
            classified = classify_candidate(
                completed["directory"], runtime,
                require_production=False)
            assert classified["pass"] == 0
            reused = run_fresh(
                candidate, runtime, run_dir,
                baseline_directory=baseline["directory"],
                allow_unisolated_test=True,
                _nonce_for_test="9" * 64)
            assert reused["directory"] == completed["directory"]
        finally:
            runner._execute = real_execute


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN S5 RUNNER TESTS PASS")
