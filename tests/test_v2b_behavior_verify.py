#!/usr/bin/env python3
"""Lean S5 semantic-verifier contract and pinned-driver tests."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_behavior_extract import (LEAN_EXTRACTION_CONTRACT_SHA256,
                                  LEAN_DRIVER_MANIFEST_SCHEMA)
from v2b_behavior_verify import (
    LEAN_PARSE_DRIVER, LEAN_VERIFY_CONTRACT_SHA256, LEAN_VERIFY_DRIVER,
    LEAN_VERIFY_MANIFEST_SCHEMA, bind_lean_verify_manifest,
    lean_baseline_certificate, lean_verify_output_marker,
    parse_lean_verify_prefix,
    parse_lean_verify_stdout)
from v2b_common import V2BError, sha256_bytes, sha256_file


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX = "a" * 64
CHANNEL_NONCE = "c" * 64


def _assert_live_consumer_hardening(stdout, manifest):
    marker = lean_verify_output_marker(CHANNEL_NONCE)
    marked = [line for line in stdout.splitlines()
              if line.startswith(marker)]
    expected_count = 2 if manifest["mode"] == "baseline" else 3
    assert len(marked) == expected_count
    assert parse_lean_verify_prefix("", manifest, CHANNEL_NONCE)["stage"] == \
        "before-prevalidation"
    assert parse_lean_verify_prefix(marked[0], manifest, CHANNEL_NONCE)["stage"] == \
        "prevalidated"
    if manifest["mode"] == "candidate":
        assert parse_lean_verify_prefix(
            "\n".join(marked[:2]), manifest, CHANNEL_NONCE)[
            "stage"] == "candidate-started"
    assert parse_lean_verify_prefix(
        "\n".join(marked), manifest, CHANNEL_NONCE)[
        "stage"] == "complete"
    raw_with_untrusted_noise = b"\xff\xfe untrusted child output\n" + \
        "\n".join(marked).encode("utf-8")
    assert parse_lean_verify_prefix(
        raw_with_untrusted_noise, manifest, CHANNEL_NONCE)["stage"] == \
        "complete"
    try:
        parse_lean_verify_prefix(
            marker.encode("ascii") + b"\xff", manifest, CHANNEL_NONCE)
        assert False, "non-UTF8 authenticated S5 payload accepted"
    except V2BError:
        pass
    try:
        parse_lean_verify_stdout(
            "\n".join(marked[:-1]), manifest, CHANNEL_NONCE)
        assert False, "partial S5 transcript accepted as complete"
    except V2BError:
        pass

    sample = json.loads(marked[-1][len(marker):])
    sample["status"] = "arbitrary-status"
    corrupted = "\n".join(marked[:-1] + [
        marker + json.dumps(sample, separators=(",", ":"))])
    try:
        parse_lean_verify_stdout(corrupted, manifest, CHANNEL_NONCE)
        assert False, "unfrozen S5 status accepted"
    except V2BError:
        pass

    if manifest["mode"] == "candidate":
        unbound = copy.deepcopy(manifest)
        unbound.pop("invocationBinding")
        unbound["samples"][0]["extractedBodySha256"] = "0" * 64
        try:
            bind_lean_verify_manifest(unbound)
            assert False, "wrong retained-body hash accepted"
        except V2BError:
            pass

        multi = copy.deepcopy(manifest)
        multi.pop("invocationBinding")
        second = dict(multi["samples"][0], id="second")
        multi["samples"].append(second)
        try:
            bind_lean_verify_manifest(multi)
            assert False, "multi-sample S5 process accepted"
        except V2BError:
            pass

        wrong_runtime = copy.deepcopy(manifest)
        wrong_runtime.pop("invocationBinding")
        wrong_runtime["baselineCertificate"]["baselineRuntimeSha256"] = \
            "0" * 64
        try:
            bind_lean_verify_manifest(wrong_runtime)
            assert False, "cross-runtime S5 certificate accepted"
        except V2BError:
            pass

        malformed_type = copy.deepcopy(manifest)
        malformed_type.pop("invocationBinding")
        malformed_type["baselineCertificate"]["typeExpression"] = ["mvar"]
        try:
            bind_lean_verify_manifest(malformed_type)
            assert False, "malformed S5 type certificate accepted"
        except V2BError:
            pass


def _toolchain_available(toolchain="leanprover/lean4:v4.32.0"):
    elan = shutil.which("elan")
    if elan is None:
        return None
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if toolchain not in listed.stdout:
        return None
    return elan


def _invoke(original, module_name, target_name, target_kind, generations,
            logical_filename=None, logical_include_text=None,
            target_end_marker=None,
            toolchain="leanprover/lean4:v4.32.0", is_module=False):
    elan = _toolchain_available(toolchain)
    if elan is None:
        return None, None, None
    original = original.encode("utf-8")
    target_start = original.index(
        ("theorem target" if target_kind == "theorem"
         else "def target").encode("utf-8"))
    header_end = original.index(b":=", target_start)
    target_end = (original.index(target_end_marker.encode("utf-8"), header_end)
                  if target_end_marker is not None
                  else original.index(b"\n", header_end))
    with tempfile.TemporaryDirectory() as td:
        if logical_include_text is not None:
            logical_dir = os.path.join(td, "logical-source")
            os.makedirs(logical_dir)
            logical_filename = os.path.join(logical_dir, "Original.lean")
            with open(os.path.join(logical_dir, "data.txt"), "w",
                      encoding="utf-8") as handle:
                handle.write(logical_include_text)
        original_path = os.path.join(td, "Original.lean")
        with open(original_path, "wb") as handle:
            handle.write(original)
        setup_path = os.path.join(td, "setup.json")
        with open(setup_path, "w", encoding="utf-8") as handle:
            json.dump(dict(dynlibs=[], importArts={}, isModule=is_module,
                           name=module_name, options={}, plugins=[]), handle)
        samples = []
        for sample_id, generation in generations.items():
            generation = generation.encode("utf-8")
            reconstructed = (original[:header_end] + generation
                             + original[target_end:])
            reconstructed_path = os.path.join(td, sample_id + ".lean")
            with open(reconstructed_path, "wb") as handle:
                handle.write(reconstructed)
            samples.append(dict(
                id=sample_id, reconstructedFile=reconstructed_path,
                reconstructedSha256=sha256_file(reconstructed_path),
                retainedEndByte=header_end + len(generation),
                extractedBodySha256=sha256_bytes(generation),
                s4EvidenceSha256=HEX))
        common = dict(
            schema=LEAN_VERIFY_MANIFEST_SCHEMA,
            originalFile=original_path,
            logicalFileName=logical_filename or original_path,
            originalSha256=sha256_file(original_path),
            moduleSetupFile=setup_path,
            moduleSetupSha256=sha256_file(setup_path),
            moduleName=module_name,
            targetName=target_name,
            targetKind=target_kind,
            targetStartByte=target_start,
            targetEndByte=target_end,
            headerEndByte=header_end,
            bodyDelimiter=":=",
            boundaryArtifactSha256=HEX,
            spanId="b" * 64,
            s4ContractSha256=LEAN_EXTRACTION_CONTRACT_SHA256,
            s4DriverSha256=sha256_file(LEAN_PARSE_DRIVER),
            s5ContractSha256=LEAN_VERIFY_CONTRACT_SHA256,
            s5DriverSha256=sha256_file(LEAN_VERIFY_DRIVER),
            runtimeSha256=HEX,
            optionOverrides=[],
        )

        def run_manifest(manifest, label):
            manifest_path = os.path.join(td, label + "-manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, sort_keys=True)
            result = subprocess.run(
                [elan, "run", toolchain, "lean", "--run",
                 LEAN_VERIFY_DRIVER, manifest_path],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
                input=CHANNEL_NONCE + "\n", check=False)
            parsed = (parse_lean_verify_stdout(
                result.stdout, manifest, CHANNEL_NONCE)
                      if result.returncode == 0 else None)
            if parsed is not None:
                _assert_live_consumer_hardening(result.stdout, manifest)
            return result, parsed

        baseline_manifest = bind_lean_verify_manifest(dict(
            common, mode="baseline", baselineCertificate=None, samples=[]))
        baseline_result, baseline_parsed = run_manifest(
            baseline_manifest, "baseline")
        if baseline_parsed is None \
                or baseline_parsed["baseline"]["status"] != "verified":
            return baseline_result, baseline_parsed, baseline_manifest
        evidence_sha = sha256_bytes(json.dumps(dict(
            exit_code=baseline_result.returncode,
            stdout=baseline_result.stdout,
            stderr=baseline_result.stderr), ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8"))
        certificate = lean_baseline_certificate(
            baseline_parsed, baseline_manifest, evidence_sha)
        assert len(samples) == 1
        candidate_manifest = bind_lean_verify_manifest(dict(
            common, mode="candidate", baselineCertificate=certificate,
            samples=samples))
        candidate_result, candidate_parsed = run_manifest(
            candidate_manifest, "candidate")
        combined = (None if candidate_parsed is None else dict(
            prevalidation=candidate_parsed["prevalidation"],
            baseline=baseline_parsed["baseline"],
            samples=candidate_parsed["samples"]))
        return candidate_result, combined, candidate_manifest


def test_contract_names_are_separate_from_s4():
    assert LEAN_DRIVER_MANIFEST_SCHEMA == "v2b_lean_parse_manifest_v1"
    assert LEAN_VERIFY_MANIFEST_SCHEMA == "v2b_lean_verify_manifest_v2"
    assert len(LEAN_VERIFY_CONTRACT_SHA256) == 64


def test_trusted_semantic_verification_is_outside_model_exception_catch():
    source = open(LEAN_VERIFY_DRIVER, encoding="utf-8").read()
    start = source.index("def elaborateTarget")
    end = source.index("partial def elaborateSuffix", start)
    function = source[start:end]
    catch = function.index("catch _ =>")
    verify = function.index("verifyEnvironment")
    assert catch < verify
    assert "stdout.flush" in source


def test_real_driver_accepts_kernel_valid_body_and_rejects_trust_escapes():
    source = ("import Lean\n"
              "namespace T\n"
              "def prior : Nat := 1\n"
              "theorem target : True := by trivial\n"
              "theorem after : True := target\n"
              "end T\n")
    rows = {}
    for sample_id, generation in {
            "good": ":= by trivial", "sorry": ":= by sorry",
            "native": ":= by native_decide"}.items():
        result, parsed, _ = _invoke(
            source, "S5Theorem", "T.target", "theorem",
            {sample_id: generation})
        if result is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert parsed["baseline"]["status"] == "verified"
        rows[sample_id] = parsed["samples"][0]
    assert rows["good"]["status"] == "verified"
    assert rows["sorry"]["status"] == "verification-failure"
    assert rows["sorry"]["reason"] == "sorry"
    assert rows["sorry"]["elaboration_attempted"] is False
    assert rows["native"]["status"] == "verification-failure"
    assert rows["native"]["reason"] in (
        "new-axiom", "new-axiom-dependency", "native-reflection")
    assert rows["native"]["elaboration_attempted"] is False


def test_s5_preserves_module_private_prefix_semantics():
    toolchain = "leanprover/lean4:v4.33.0-rc2"
    source = (
        "module\n"
        "public import Lean\n"
        "@[expose] public section\n"
        "structure Witness where\n"
        "  proof : True\n"
        "set_option backward.privateInPublic true in\n"
        "private theorem hidden : True := by trivial\n"
        "set_option backward.privateInPublic true in\n"
        "set_option backward.privateInPublic.warn false in\n"
        "def pairing : Witness where\n"
        "  proof := hidden\n"
        "theorem target : True := pairing.proof\n"
    )
    result, parsed, _ = _invoke(
        source, "V2BS5Private", "target", "theorem",
        {"module-private": ":= pairing.proof"}, toolchain=toolchain,
        is_module=True)
    if result is None:
        print("    [skip] pinned Lean 4.33 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["baseline"]["status"] == "verified"
    assert parsed["samples"][0]["status"] == "verified"


def test_forbidden_words_in_comments_and_strings_are_not_tokens():
    result, parsed, _ = _invoke(
        "import Lean\n"
        "theorem target : True := by trivial\n",
        "S5TokenScan", "target", "theorem",
        {"words_are_data":
         ":= by\n"
         "  let message := \"sorry admit native_decide unsafe "
         "implemented_by\"\n"
         "  -- sorry native_decide unsafe implemented_by\n"
         "  trivial"})
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["samples"][0]["status"] == "verified"


def test_real_driver_checks_the_immutable_suffix_not_only_target_type():
    source = ("import Lean\n"
              "namespace Suffix\n"
              "def target : Nat := 0\n"
              "theorem after : target = 0 := rfl\n"
              "end Suffix\n")
    rows = {}
    for sample_id, generation in {
            "same": ":= 0", "breaks_suffix": ":= 1"}.items():
        result, parsed, _ = _invoke(
            source, "S5Suffix", "Suffix.target", "def",
            {sample_id: generation})
        if result is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        assert result.returncode == 0, (result.stdout, result.stderr)
        rows[sample_id] = parsed["samples"][0]
    assert rows["same"]["status"] == "verified"
    assert rows["breaks_suffix"]["status"] == "verification-failure"
    assert rows["breaks_suffix"]["reason"] == "suffix-elaboration-error"


def test_baseline_and_candidate_are_isolated_fresh_processes():
    source = ("import Lean\n"
              "namespace Leak\n"
              "theorem target : True := by\n"
              "  run_tac\n"
              "    Lean.Core.liftIOCore <|\n"
              "      Lean.Parser.builtinSyntaxNodeKindSetRef.modify fun s =>\n"
              "        s.insert `v2b_s5_process_isolation_marker\n"
              "  trivial\n"
              "theorem after : True := by trivial\n"
              "end Leak\n")
    generation = (":= by\n"
                  "  run_tac\n"
                  "    let kinds ← Lean.Core.liftIOCore\n"
                  "      Lean.Parser.builtinSyntaxNodeKindSetRef.get\n"
                  "    unless kinds.contains "
                  "`v2b_s5_process_isolation_marker do\n"
                  "      throwError \"baseline state absent\"\n"
                  "  trivial")
    result, parsed, _ = _invoke(
        source, "S5ProcessIsolation", "Leak.target", "theorem",
        {"leak_probe": generation}, target_end_marker="\ntheorem after")
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["baseline"]["status"] == "verified"
    row = parsed["samples"][0]
    assert row["status"] == "verification-failure"
    assert row["reason"] == "elaboration-error"


def test_inherited_stdout_child_cannot_forge_authenticated_records():
    forged = '@@V2B_LEAN_VERIFY@@' + json.dumps(dict(
        schema="v2b_lean_verify_result_v2", record_type="sample",
        status="verified"), separators=(",", ":")) + "\n"
    lean_literal = json.dumps(forged)
    generation = (
        ":= by\n"
        "  run_tac\n"
        "    let child ← Lean.Core.liftIOCore <| IO.Process.spawn {\n"
        "      cmd := \"/usr/bin/printf\",\n"
        f"      args := #[{lean_literal}],\n"
        "      stdin := .null, stdout := .inherit, stderr := .inherit,\n"
        "      inheritEnv := false\n"
        "    }\n"
        "    let _ ← Lean.Core.liftIOCore child.wait\n"
        "  trivial")
    result, parsed, _ = _invoke(
        "import Lean\n"
        "theorem target : True := by trivial\n",
        "S5StdoutAuthentication", "target", "theorem",
        {"inherited_stdout": generation})
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert forged.strip() in result.stdout
    assert parsed["baseline"]["status"] == "verified"
    assert parsed["samples"][0]["status"] == "verified"


def test_staged_bytes_use_the_bound_original_logical_filename():
    result, parsed, _ = _invoke(
        "import Lean\n"
        "def target : String := include_str \"data.txt\"\n"
        "theorem after : target = \"logical payload\" := rfl\n",
        "S5Logical", "target", "def",
        {"same": ":= include_str \"data.txt\""},
        logical_include_text="logical payload")
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["baseline"]["status"] == "verified"
    assert parsed["samples"][0]["status"] == "verified"


def test_s5_settles_trusted_async_prefix_and_suffix_on_lean433():
    source = ("import Lean\n"
              "set_option Elab.async true in\n"
              "theorem prior : True := by trivial\n"
              "def target : Nat := 0\n"
              "set_option Elab.async true in\n"
              "theorem after : True := by trivial\n")
    result, parsed, _ = _invoke(
        source, "S5AsyncTrusted", "target", "def",
        {"same": ":= 0"},
        toolchain="leanprover/lean4:v4.33.0-rc2")
    if result is None:
        print("    [skip] pinned Lean 4.33-rc2 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["baseline"]["status"] == "verified"
    assert parsed["samples"][0]["status"] == "verified"


def test_inferred_definition_uses_kernel_definitional_type_equality():
    source = ("import Lean\n"
              "abbrev Alias := Nat\n"
              "def target := (0 : Nat)\n")
    rows = {}
    for sample_id, generation in {
            "defeq_alias": ":= (0 : Alias)", "wrong": ":= true"}.items():
        result, parsed, _ = _invoke(
            source, "S5DefEq", "target", "def",
            {sample_id: generation})
        if result is None:
            print("    [skip] pinned Lean 4.32 toolchain is not installed")
            return
        assert result.returncode == 0, (result.stdout, result.stderr)
        rows[sample_id] = parsed["samples"][0]
    assert rows["defeq_alias"]["status"] == "verified"
    assert rows["defeq_alias"]["type_kernel_equal"] is True
    assert rows["wrong"]["status"] == "verification-failure"
    assert rows["wrong"]["reason"] == "target-type-drift"


def test_polymorphic_dependent_projection_certificate_on_both_toolchains():
    source = ("import Lean\n"
              "universe u\n"
              "structure Pack where\n"
              "  α : Type u\n"
              "  x : α\n"
              "def target (p : Pack) := p.x\n")
    for toolchain in (
            "leanprover/lean4:v4.32.0",
            "leanprover/lean4:v4.33.0-rc2"):
        result, parsed, _ = _invoke(
            source, "S5Polymorphic", "target", "def",
            {"projection": ":= p.x"}, toolchain=toolchain)
        if result is None:
            print(f"    [skip] pinned {toolchain} is not installed")
            continue
        assert result.returncode == 0, (
            toolchain, result.stdout, result.stderr)
        assert parsed["baseline"]["status"] == "verified"
        row = parsed["samples"][0]
        assert row["status"] == "verified", (toolchain, row)
        assert row["n_level_params"] == 1
        assert row["type_kernel_equal"] is True


def test_invalid_baseline_is_arm_independent_and_candidate_is_not_launched():
    result, parsed, _ = _invoke(
        "import Lean\n"
        "theorem target : False := by sorry\n",
        "S5Baseline", "target", "theorem",
        {"would_be_good": ":= by contradiction"})
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert parsed["baseline"]["status"] == "verification-failure"
    assert parsed["baseline"]["reason"] == "sorry"
    assert parsed["samples"] == []


def test_manifest_file_mutation_and_duplicate_output_keys_fail_closed():
    result, parsed, manifest = _invoke(
        "import Lean\n"
        "def target : Nat := 0\n",
        "S5Binding", "target", "def", {"same": ":= 0"})
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode == 0 and parsed["samples"][0]["status"] == \
        "verified"
    drifted = dict(manifest, targetName="wrong")
    try:
        parse_lean_verify_stdout(result.stdout, drifted, CHANNEL_NONCE)
        assert False, "drifted manifest accepted"
    except V2BError:
        pass
    marker = lean_verify_output_marker(CHANNEL_NONCE)
    duplicate = marker + '{"schema":"v2b_lean_verify_result_v2",' \
        '"schema":"v2b_lean_verify_result_v2"}'
    try:
        parse_lean_verify_stdout(duplicate, manifest, CHANNEL_NONCE)
        assert False, "duplicate marked key accepted"
    except V2BError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN S5 VERIFIER TESTS PASS")
