#!/usr/bin/env python3
"""Adversarial boundary tests for behavioral Python body extraction."""
import os
import json
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import v2b_behavior_extract as behavior_extract
from v2b_behavior_extract import (
    LEAN_DRIVER_INVOCATION_BINDING_SCHEMA,
    LEAN_DRIVER_MANIFEST_SCHEMA, LEAN_DRIVER_OUTPUT_MARKER,
    LEAN_DRIVER_OUTPUT_SCHEMA, LEAN_EXTRACTION_CONTRACT,
    LEAN_EXTRACTION_CONTRACT_SHA256, LEAN_EXTRACTION_FAILURE_REASONS,
    PYTHON_EXTRACTION_CONTRACT, PYTHON_EXTRACTION_CONTRACT_SHA256,
    PYTHON_EXTRACTION_FAILURE_REASONS, extract_python_body,
    bind_lean_driver_manifest, lean_driver_invocation_binding,
    parse_lean_driver_stdout)
from v2b_common import (BEHAVIOR_EXTRACTED_SCHEMA, V2BError, sha256_bytes,
                        sha256_json)


SUCCESS_KEYS = {
    "status", "suite_form", "body", "body_sha256", "boundary_char",
    "n_discarded_chars", "node_kind",
}
FAILURE_KEYS = {"status", "reason"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAN_DRIVER = os.path.join(ROOT, "lean_drivers", "V2BParseCommand.lean")


def _success(prefix, generation, kind="FunctionDef", name="f"):
    value = extract_python_body(prefix, generation, kind, name)
    assert set(value) == SUCCESS_KEYS
    assert value["status"] == "extracted"
    body = value["body"]
    assert value["boundary_char"] == len(body)
    assert value["n_discarded_chars"] == len(generation) - len(body)
    assert value["body_sha256"] == sha256_bytes(body.encode("utf-8"))
    return value


def _failure(prefix, generation, expected_reason, kind="FunctionDef",
             name="f"):
    value = extract_python_body(prefix, generation, kind, name)
    assert set(value) == FAILURE_KEYS
    assert value == {"status": "extraction-failure",
                     "reason": expected_reason}


def test_compound_keeps_complete_suite_and_discards_second_declaration():
    generation = (
        "\n"
        "    y = x + 1\n"
        "    if y > 2:\n"
        "        y += 3\n"
        "    return y\n"
        "def trailing():\n"
        "    pass\n"
    )
    value = _success("def f(x):", generation)
    assert value["suite_form"] == "compound"
    assert value["body"] == (
        "\n"
        "    y = x + 1\n"
        "    if y > 2:\n"
        "        y += 3\n"
        "    return y\n"
    )
    assert "trailing" not in value["body"]


def test_lazy_stop_ignores_unlexable_junk_only_after_boundary():
    generation = "\n    return x\ndef broken(: '''unterminated"
    value = _success("def f(x):", generation)
    assert value["body"] == "\n    return x\n"
    assert value["n_discarded_chars"] > 0

    _failure("def f(x):", "\n    value = '''unterminated",
             "tokenize-before-boundary")


def test_eof_implicitly_closes_complete_compound_suite():
    value = _success("def f(x):", "\n    x += 1\n    return x")
    assert value["body"] == "\n    x += 1\n    return x"
    assert value["n_discarded_chars"] == 0


def test_simple_suite_uses_first_logical_not_physical_newline():
    generation = (
        " return (x +\n"
        "         1); y = x \\\n"
        "             + 2\n"
        "this is trailing junk '''"
    )
    value = _success("def f(x):", generation)
    assert value["suite_form"] == "simple-statement"
    assert value["body"] == generation.split("this is", 1)[0]
    assert "trailing" not in value["body"]


def test_simple_one_line_class_and_semicolon_suite():
    value = _success("class C:", " value = 1; other = 2\nBAD @@@",
                     kind="ClassDef", name="C")
    assert value["suite_form"] == "simple-statement"
    assert value["body"] == " value = 1; other = 2\n"
    assert value["node_kind"] == "ClassDef"

    eof = _success("def f(x):", " return x")
    assert eof["body"] == " return x"
    assert eof["n_discarded_chars"] == 0


def test_decorated_async_multiline_unicode_prefix_is_one_node():
    prefix = (
        "@decorator(\n"
        "    1,\n"
        ")\n"
        "async def café(\n"
        "    x: int,\n"
        "):"
    )
    generation = "\n    π = x + 1\n    return π\nnot valid '''"
    value = _success(prefix, generation, kind="AsyncFunctionDef",
                     name="café")
    assert value["node_kind"] == "AsyncFunctionDef"
    assert value["boundary_char"] == len("\n    π = x + 1\n    return π\n")
    assert len(value["body"].encode("utf-8")) > len(value["body"])


def test_leading_comments_and_blank_lines_before_indent_are_retained():
    generation = (
        " # header-line comment\n"
        "\n"
        "# generated comment before the suite indent\n"
        "    return x\n"
    )
    value = _success("def f(x):", generation)
    assert value["body"] == generation


def test_missing_or_malformed_suite_is_an_ordinary_failure():
    _failure("def f(x):", "", "generated-empty")
    _failure("def f(x):", "\nreturn x\n", "compound-missing-indent")
    _failure("def f(x):", " # only a comment\n", "compound-missing-indent")
    _failure("def f(x):", ": return x\n", "ast-parse-failed")


def test_boundary_token_straddle_and_line_encoding_fail_closed():
    # ':' belongs to the trusted header; an immediately generated '=' would
    # merge it into the ':=' token, so it cannot silently redefine the split.
    _failure("def f(x):", "= x\n", "token-straddles-prefix-boundary")
    _failure("def f(x):", "\r\n    return x\n", "generated-cr")
    _failure("def f(x):", "\ud800", "generated-not-utf8")


def test_kind_name_and_node_count_drift_are_trusted_input_errors():
    bad_calls = (
        lambda: extract_python_body("def f(x):", " return x\n",
                                    "ClassDef", "f"),
        lambda: extract_python_body("def f(x):", " return x\n",
                                    "FunctionDef", "g"),
        lambda: extract_python_body("x = 1\ndef f(x):", " return x\n",
                                    "FunctionDef", "f"),
    )
    for call in bad_calls:
        try:
            call()
            assert False, "trusted prefix provenance drift became a zero"
        except V2BError:
            pass


def test_parser_resource_exceptions_are_recorded_not_raised():
    original = behavior_extract.ast.parse
    try:
        for exception in (RecursionError("deep"), MemoryError("large")):
            n_calls = 0
            def fail_second(candidate, error=exception):
                nonlocal n_calls
                n_calls += 1
                if n_calls == 1:
                    return original(candidate)
                raise error
            behavior_extract.ast.parse = fail_second
            _failure("def f(x):", " return x\n", "ast-parse-failed")
    finally:
        behavior_extract.ast.parse = original


def test_trusted_contract_input_violations_raise_not_measure_failure():
    bad_calls = (
        lambda: extract_python_body("", " return 1\n", "FunctionDef"),
        lambda: extract_python_body("def f()", " return 1\n",
                                    "FunctionDef"),
        lambda: extract_python_body("def f():\r", " return 1\n",
                                    "FunctionDef"),
        lambda: extract_python_body("def f():", 3, "FunctionDef"),
        lambda: extract_python_body("def f():", " return 1\n", "lambda"),
        lambda: extract_python_body("def f():", " return 1\n",
                                    "FunctionDef", ""),
    )
    for call in bad_calls:
        try:
            call()
            assert False, "malformed trusted extraction input accepted"
        except V2BError:
            pass


def test_contract_and_failure_surface_are_hash_frozen():
    assert BEHAVIOR_EXTRACTED_SCHEMA == "v2b_behavior_extracted_v1"
    assert PYTHON_EXTRACTION_CONTRACT["artifact_schema"] == \
        BEHAVIOR_EXTRACTED_SCHEMA
    assert PYTHON_EXTRACTION_CONTRACT_SHA256 == \
        sha256_json(PYTHON_EXTRACTION_CONTRACT)
    assert PYTHON_EXTRACTION_CONTRACT["ordinary_failure_reasons"] == \
        list(PYTHON_EXTRACTION_FAILURE_REASONS)
    assert len(set(PYTHON_EXTRACTION_FAILURE_REASONS)) == \
        len(PYTHON_EXTRACTION_FAILURE_REASONS)
    assert LEAN_DRIVER_MANIFEST_SCHEMA == "v2b_lean_parse_manifest_v1"
    assert LEAN_DRIVER_INVOCATION_BINDING_SCHEMA == \
        "v2b_lean_driver_invocation_binding_v1"
    assert LEAN_DRIVER_OUTPUT_SCHEMA == "v2b_lean_parse_result_v1"
    assert LEAN_EXTRACTION_CONTRACT["artifact_schema"] == \
        BEHAVIOR_EXTRACTED_SCHEMA
    assert LEAN_EXTRACTION_CONTRACT_SHA256 == \
        sha256_json(LEAN_EXTRACTION_CONTRACT)
    assert LEAN_EXTRACTION_CONTRACT["ordinary_failure_reasons"] == \
        list(LEAN_EXTRACTION_FAILURE_REASONS)
    assert len(set(LEAN_EXTRACTION_FAILURE_REASONS)) == \
        len(LEAN_EXTRACTION_FAILURE_REASONS)


def _marked(value):
    return LEAN_DRIVER_OUTPUT_MARKER + json.dumps(
        value, ensure_ascii=False, separators=(",", ":"))


def _synthetic_lean_transcript(td):
    paths = {}
    for name in ("original", "setup", "a", "b", "alternate"):
        path = os.path.join(td, name)
        open(path, "wb").write((name.encode("utf-8") + b"-") * 16)
        paths[name] = path
    manifest = bind_lean_driver_manifest(dict(
        schema=LEAN_DRIVER_MANIFEST_SCHEMA,
        originalFile=paths["original"],
        moduleSetupFile=paths["setup"], moduleName="T",
        targetIdentity="T.target", targetKind="theorem",
        targetStartByte=10, targetEndByte=30, headerEndByte=20,
        bodyDelimiter=":=", optionOverrides=[],
        samples=[
            dict(id="a", splicedFile=paths["a"], generatedEndByte=25),
            dict(id="b", splicedFile=paths["b"], generatedEndByte=25),
        ]))
    pre = dict(
        schema=LEAN_DRIVER_OUTPUT_SCHEMA, record_type="prevalidation",
        invocation_binding=manifest["invocationBinding"],
        module_name="T", target_identity="T.target", target_kind="theorem",
        target_start_byte=10, target_end_byte=30, header_end_byte=20,
        body_delimiter=":=", syntax_kind="Lean.Parser.Command.declaration",
        header_syntax_projection="[\"frozen\"]",
        n_prior_commands=2, generated_target_elaborated=False,
        body_boundary_probe_validated=True)
    success = dict(
        schema=LEAN_DRIVER_OUTPUT_SCHEMA, record_type="sample",
        sample_id="a", status="extracted", start_byte=10, end_byte=25,
        body_start_byte=20, body_bytes=5,
        syntax_kind="Lean.Parser.Command.declaration", n_parse_messages=0,
        recovering=False, has_missing=False,
        generated_target_elaborated=False)
    failure = dict(
        schema=LEAN_DRIVER_OUTPUT_SCHEMA, record_type="sample",
        sample_id="b", status="extraction-failure", reason="has-missing",
        n_parse_messages=0, recovering=False, has_missing=True,
        generated_target_elaborated=False)
    return manifest, pre, success, failure, paths


def test_lean_stdout_consumer_is_exact_and_ignores_only_unmarked_noise():
    with tempfile.TemporaryDirectory() as td:
        manifest, pre, success, failure, paths = \
            _synthetic_lean_transcript(td)
        stdout = "trusted #check noise\n" + "\n".join(
            _marked(row) for row in (pre, success, failure)) + "\n"
        value = parse_lean_driver_stdout(stdout, manifest)
        assert value["prevalidation"] == pre
        assert value["samples"] == [success, failure]
        assert manifest["invocationBinding"] == \
            lean_driver_invocation_binding(manifest)

        duplicate_key = (LEAN_DRIVER_OUTPUT_MARKER +
                         '{"schema":"x","schema":"y"}')
        try:
            parse_lean_driver_stdout(duplicate_key, manifest)
            assert False, "duplicate marked JSON key accepted"
        except V2BError:
            pass

        mutations = []
        leaked = dict(success, named_arm="k4")
        mutations.append((pre, leaked, failure))
        elaborated = dict(success, generated_target_elaborated=True)
        mutations.append((pre, elaborated, failure))
        alien_reason = dict(failure, reason="looks-bad")
        mutations.append((pre, success, alien_reason))
        bad_math = dict(success, body_bytes=4)
        mutations.append((pre, bad_math, failure))
        wrong_module = dict(pre, module_name="WRONG")
        mutations.append((wrong_module, success, failure))
        unprobed = dict(pre, body_boundary_probe_validated=False)
        mutations.append((unprobed, success, failure))
        impossible = dict(failure, reason="terminal-command",
                          n_parse_messages=99, recovering=False,
                          has_missing=True)
        mutations.append((pre, success, impossible))
        for rows in mutations:
            try:
                parse_lean_driver_stdout(
                    "\n".join(_marked(row) for row in rows), manifest)
                assert False, "drifted Lean driver record accepted"
            except V2BError:
                pass

        transcript = "\n".join(
            _marked(row) for row in (pre, success, failure))

        def rebound(**updates):
            changed = json.loads(json.dumps(manifest))
            changed.pop("invocationBinding")
            changed.update(updates)
            return bind_lean_driver_manifest(changed)

        changed_samples = json.loads(json.dumps(manifest["samples"]))
        changed_samples[0]["splicedFile"] = paths["alternate"]
        bad_manifests = (
            dict(manifest, named_arm="k4"),
            dict(manifest, moduleName="WRONG"),
            rebound(moduleName="WRONG"),
            rebound(originalFile=paths["alternate"]),
            rebound(moduleSetupFile=paths["alternate"]),
            rebound(optionOverrides=[dict(name="pp.universes",
                                                  value="true")]),
            rebound(samples=changed_samples),
        )
        for bad_manifest in bad_manifests:
            try:
                parse_lean_driver_stdout(transcript, bad_manifest)
                assert False, "drifted Lean driver manifest accepted"
            except V2BError:
                pass

        # Content drift at an unchanged path is equally invocation-bound.
        open(paths["b"], "ab").write(b"drift")
        try:
            parse_lean_driver_stdout(transcript, manifest)
            assert False, "drifted Lean input bytes accepted"
        except V2BError:
            pass


def test_real_lean_driver_reconstructs_prefix_and_never_elaborates_target():
    elan = shutil.which("elan")
    if elan is None:
        print("    [skip] elan is not installed")
        return
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if "leanprover/lean4:v4.32.0" not in listed.stdout:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    original = (
        "import Lean\n"
        "namespace 𝔸\n"
        "syntax \"v2btwice \" term : term\n"
        "macro_rules | `(v2btwice $x) => `($x + $x)\n"
        "def prior (x : Nat) : Nat := v2btwice x\n"
        "run_cmd IO.println \"@@V2B_LEAN_PARSE@@{spoof}\"\n"
        "/-- target documentation -/\n"
        "@[simp] theorem target (x : Nat) : x = x:= by\n"
        "  rfl\n"
        "def after : Nat := 7\n"
        "end 𝔸\n"
    ).encode("utf-8")
    target_start = original.index(b"/-- target documentation")
    header_end = original.index(b":=", target_start)
    target_end = original.index(b"\ndef after", header_end)
    generations = {
        "good": b":= by\n  rfl",
        "leading_trivia": b"/- generated trivia -/ := by\n  rfl",
        "elab_bomb": (
            b":= by\n"
            b"  run_tac\n"
            b"    throwError \"GENERATED_TARGET_WAS_ELABORATED\"\n"
            b"  rfl"),
        "lazy": b":= by\n  rfl\n#eval 123\nthis is trailing junk '''",
        "unterminated_tail": b":= by\n  rfl\n/- unterminated",
        "header_merge": b"Bar := by\n  rfl",
        "malformed": b":= by\n  exact",
        "empty": b"",
    }
    with tempfile.TemporaryDirectory() as td:
        original_path = os.path.join(td, "Original.lean")
        open(original_path, "wb").write(original)
        setup_path = os.path.join(td, "setup.json")
        json.dump(dict(
            dynlibs=[], importArts={}, isModule=False, name="V2BFixture",
            options={}, plugins=[]),
            open(setup_path, "w", encoding="utf-8"))
        samples = []
        for sample_id, generation in generations.items():
            spliced = (original[:header_end] + generation +
                       original[target_end:])
            path = os.path.join(td, sample_id + ".lean")
            open(path, "wb").write(spliced)
            samples.append(dict(
                id=sample_id, splicedFile=path,
                generatedEndByte=header_end + len(generation)))
        manifest = bind_lean_driver_manifest(dict(
            schema=LEAN_DRIVER_MANIFEST_SCHEMA,
            originalFile=original_path, moduleSetupFile=setup_path,
            moduleName="V2BFixture", targetIdentity="𝔸.target",
            targetKind="theorem",
            targetStartByte=target_start, targetEndByte=target_end,
            headerEndByte=header_end, bodyDelimiter=":=",
            optionOverrides=[], samples=samples))
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"),
                  ensure_ascii=False)
        result = subprocess.run(
            [elan, "run", "leanprover/lean4:v4.32.0", "lean", "--run",
             LEAN_DRIVER, manifest_path], cwd=ROOT, capture_output=True,
            text=True, timeout=180, check=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        parsed = parse_lean_driver_stdout(result.stdout, manifest)
    rows = {row["sample_id"]: row for row in parsed["samples"]}
    assert parsed["prevalidation"]["generated_target_elaborated"] is False
    assert parsed["prevalidation"]["header_syntax_projection"]
    assert parsed["prevalidation"]["n_prior_commands"] >= 3
    assert rows["good"]["status"] == "extracted"
    assert rows["good"]["end_byte"] == header_end + len(generations["good"])
    assert rows["elab_bomb"]["status"] == "extracted"
    assert rows["elab_bomb"]["generated_target_elaborated"] is False
    assert rows["lazy"]["status"] == "extracted"
    assert rows["leading_trivia"]["status"] == "extracted"
    assert rows["lazy"]["end_byte"] == rows["good"]["end_byte"]
    assert rows["lazy"]["end_byte"] < header_end + len(generations["lazy"])
    # Lean's top-level command parser lexes trailing trivia while selecting a
    # command, so an unterminated block comment is prospectively a failure even
    # though the canonical command tail precedes it.
    assert rows["unterminated_tail"]["status"] == "extraction-failure"
    assert rows["unterminated_tail"]["reason"] == "parse-error-in-target"
    assert rows["header_merge"]["status"] == "extraction-failure"
    assert rows["header_merge"]["reason"] in \
        LEAN_EXTRACTION_FAILURE_REASONS
    assert rows["malformed"]["status"] == "extraction-failure"
    assert rows["malformed"]["reason"] in LEAN_EXTRACTION_FAILURE_REASONS
    assert rows["empty"]["status"] == "extraction-failure"
    assert rows["empty"]["reason"] == "empty-body"


def test_real_lean_driver_accepts_alternate_valid_body_form():
    elan = shutil.which("elan")
    if elan is None:
        print("    [skip] elan is not installed")
        return
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if "leanprover/lean4:v4.32.0" not in listed.stdout:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    original = (
        "import Lean\n"
        "namespace Alternate\n"
        "def target : Nat → Nat\n"
        "  | x => x\n"
        "def after : Nat := 7\n"
        "end Alternate\n"
    ).encode("utf-8")
    target_start = original.index(b"def target")
    header_end = original.index(b"|", target_start)
    target_end = original.index(b"\ndef after", header_end)
    generations = {
        "same_equation": b"| x => x",
        "term_body": b":= fun x => x",
        "trivia_term_body": b"/- generated trivia -/ := fun x => x",
        # A header extension must not be mistaken for a valid alternate body.
        # The exact S5 name/type comparison remains mandatory even though this
        # example is already rejected syntactically.
        "changed_type_for_s5": "→ Nat := fun x _ => x".encode("utf-8"),
    }
    with tempfile.TemporaryDirectory() as td:
        original_path = os.path.join(td, "Original.lean")
        open(original_path, "wb").write(original)
        setup_path = os.path.join(td, "setup.json")
        json.dump(dict(
            dynlibs=[], importArts={}, isModule=False, name="V2BAlternate",
            options={}, plugins=[]),
            open(setup_path, "w", encoding="utf-8"))
        samples = []
        for sample_id, generation in generations.items():
            spliced = (original[:header_end] + generation +
                       original[target_end:])
            path = os.path.join(td, sample_id + ".lean")
            open(path, "wb").write(spliced)
            samples.append(dict(
                id=sample_id, splicedFile=path,
                generatedEndByte=header_end + len(generation)))
        manifest = bind_lean_driver_manifest(dict(
            schema=LEAN_DRIVER_MANIFEST_SCHEMA,
            originalFile=original_path, moduleSetupFile=setup_path,
            moduleName="V2BAlternate", targetIdentity="Alternate.target",
            targetKind="def", targetStartByte=target_start,
            targetEndByte=target_end, headerEndByte=header_end,
            bodyDelimiter="|", optionOverrides=[], samples=samples))
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"),
                  ensure_ascii=False)
        result = subprocess.run(
            [elan, "run", "leanprover/lean4:v4.32.0", "lean", "--run",
             LEAN_DRIVER, manifest_path], cwd=ROOT, capture_output=True,
            text=True, timeout=180, check=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        parsed = parse_lean_driver_stdout(result.stdout, manifest)
    rows = {row["sample_id"]: row for row in parsed["samples"]}
    assert all(rows[sample_id]["status"] == "extracted"
               for sample_id in ("same_equation", "term_body",
                                 "trivia_term_body")), rows
    assert rows["changed_type_for_s5"]["status"] == "extraction-failure"
    assert rows["changed_type_for_s5"]["reason"] == "body-slot-drift"


def test_real_lean_driver_rejects_type_internal_delimiter_boundary():
    elan = shutil.which("elan")
    if elan is None:
        print("    [skip] elan is not installed")
        return
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if "leanprover/lean4:v4.32.0" not in listed.stdout:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    original = (
        "import Lean\n"
        "def target : let n := 1; Nat := 0\n"
        "def after : Nat := 7\n"
    ).encode("utf-8")
    target_start = original.index(b"def target")
    inner_boundary = original.index(b":=", target_start)
    body_boundary = original.index(b":=", inner_boundary + 2)
    target_end = original.index(b"\ndef after", body_boundary)
    with tempfile.TemporaryDirectory() as td:
        original_path = os.path.join(td, "Original.lean")
        open(original_path, "wb").write(original)
        setup_path = os.path.join(td, "setup.json")
        json.dump(dict(
            dynlibs=[], importArts={}, isModule=False, name="V2BInner",
            options={}, plugins=[]),
            open(setup_path, "w", encoding="utf-8"))

        def invoke(boundary, generation, label):
            spliced = (original[:boundary] + generation +
                       original[target_end:])
            spliced_path = os.path.join(td, label + ".lean")
            open(spliced_path, "wb").write(spliced)
            manifest = bind_lean_driver_manifest(dict(
                schema=LEAN_DRIVER_MANIFEST_SCHEMA,
                originalFile=original_path, moduleSetupFile=setup_path,
                moduleName="V2BInner", targetIdentity="target",
                targetKind="def", targetStartByte=target_start,
                targetEndByte=target_end, headerEndByte=boundary,
                bodyDelimiter=":=", optionOverrides=[], samples=[dict(
                    id=label, splicedFile=spliced_path,
                    generatedEndByte=boundary + len(generation))]))
            manifest_path = os.path.join(td, label + ".json")
            json.dump(manifest,
                      open(manifest_path, "w", encoding="utf-8"))
            result = subprocess.run(
                [elan, "run", "leanprover/lean4:v4.32.0", "lean",
                 "--run", LEAN_DRIVER, manifest_path], cwd=ROOT,
                capture_output=True, text=True, timeout=180, check=False)
            return result, manifest

        wrong, _ = invoke(inner_boundary, b":= 0", "wrong")
        assert wrong.returncode != 0
        assert "declaration-body sentinel parse" in wrong.stderr

        correct, manifest = invoke(body_boundary, b":= 1", "correct")
        assert correct.returncode == 0, (correct.stdout, correct.stderr)
        parsed = parse_lean_driver_stdout(correct.stdout, manifest)
        assert parsed["samples"][0]["status"] == "extracted"


def test_real_lean_driver_settles_scoped_async_prefix_tasks():
    elan = shutil.which("elan")
    if elan is None:
        print("    [skip] elan is not installed")
        return
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if "leanprover/lean4:v4.32.0" not in listed.stdout:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    original = (
        "import Lean\n"
        "set_option Elab.async true in\n"
        "theorem prior : True := by trivial\n"
        "theorem target : True := by trivial\n"
    ).encode("utf-8")
    target_start = original.index(b"theorem target")
    header_end = original.index(b":=", target_start)
    target_end = len(original.rstrip(b"\n"))
    generation = b":= by trivial"
    with tempfile.TemporaryDirectory() as td:
        original_path = os.path.join(td, "Original.lean")
        open(original_path, "wb").write(original)
        setup_path = os.path.join(td, "setup.json")
        json.dump(dict(
            dynlibs=[], importArts={}, isModule=False, name="V2BAsync",
            options={}, plugins=[]),
            open(setup_path, "w", encoding="utf-8"))
        spliced = original[:header_end] + generation + original[target_end:]
        spliced_path = os.path.join(td, "spliced.lean")
        open(spliced_path, "wb").write(spliced)
        manifest = bind_lean_driver_manifest(dict(
            schema=LEAN_DRIVER_MANIFEST_SCHEMA,
            originalFile=original_path, moduleSetupFile=setup_path,
            moduleName="V2BAsync", targetIdentity="target",
            targetKind="theorem", targetStartByte=target_start,
            targetEndByte=target_end, headerEndByte=header_end,
            bodyDelimiter=":=", optionOverrides=[], samples=[dict(
                id="scoped", splicedFile=spliced_path,
                generatedEndByte=header_end + len(generation))]))
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"))
        result = subprocess.run(
            [elan, "run", "leanprover/lean4:v4.32.0", "lean", "--run",
            LEAN_DRIVER, manifest_path], cwd=ROOT, capture_output=True,
            text=True, timeout=180, check=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        parsed = parse_lean_driver_stdout(result.stdout, manifest)
        assert parsed["samples"][0]["status"] == "extracted"


def test_real_lean_driver_rejects_type_preserving_header_continuation():
    elan = shutil.which("elan")
    if elan is None:
        print("    [skip] elan is not installed")
        return
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    if "leanprover/lean4:v4.32.0" not in listed.stdout:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    original = (
        "import Lean\n"
        "def target (x : Nat) := x\n"
        "def after : Nat := 7\n"
    ).encode("utf-8")
    target_start = original.index(b"def target")
    header_end = original.index(b":=", target_start)
    target_end = original.index(b"\ndef after", header_end)
    generations = {
        "good": b":= x",
        "trivia_good": b"/- c -/ := x",
        # Both can preserve the elaborated FQN/type, but neither is a body
        # continuation of the frozen header and so S4 must reject it before S5.
        "explicit_type": b": Nat := x",
        "extra_binder": b"(y : Nat) := x",
    }
    with tempfile.TemporaryDirectory() as td:
        original_path = os.path.join(td, "Original.lean")
        open(original_path, "wb").write(original)
        setup_path = os.path.join(td, "setup.json")
        json.dump(dict(
            dynlibs=[], importArts={}, isModule=False, name="V2BHeader",
            options={}, plugins=[]),
            open(setup_path, "w", encoding="utf-8"))
        samples = []
        for sample_id, generation in generations.items():
            spliced = original[:header_end] + generation + original[target_end:]
            path = os.path.join(td, sample_id + ".lean")
            open(path, "wb").write(spliced)
            samples.append(dict(
                id=sample_id, splicedFile=path,
                generatedEndByte=header_end + len(generation)))
        manifest = bind_lean_driver_manifest(dict(
            schema=LEAN_DRIVER_MANIFEST_SCHEMA,
            originalFile=original_path, moduleSetupFile=setup_path,
            moduleName="V2BHeader", targetIdentity="target",
            targetKind="def", targetStartByte=target_start,
            targetEndByte=target_end, headerEndByte=header_end,
            bodyDelimiter=":=", optionOverrides=[], samples=samples))
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"))
        result = subprocess.run(
            [elan, "run", "leanprover/lean4:v4.32.0", "lean", "--run",
             LEAN_DRIVER, manifest_path], cwd=ROOT, capture_output=True,
            text=True, timeout=180, check=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        parsed = parse_lean_driver_stdout(result.stdout, manifest)
    rows = {row["sample_id"]: row for row in parsed["samples"]}
    assert rows["good"]["status"] == "extracted"
    assert rows["trivia_good"]["status"] == "extracted"
    assert rows["explicit_type"]["reason"] == "body-slot-drift"
    assert rows["extra_binder"]["reason"] == "body-slot-drift"


if __name__ == "__main__":
    for test_name, fn in sorted(globals().items()):
        if test_name.startswith("test_"):
            fn()
            print(f"[ok] {test_name}")
    print("V2B BEHAVIOR PYTHON EXTRACTION TESTS PASS")
