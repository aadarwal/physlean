#!/usr/bin/env python3
"""Adversarial boundary tests for behavioral Python body extraction."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import v2b_behavior_extract as behavior_extract
from v2b_behavior_extract import (
    PYTHON_EXTRACTION_CONTRACT, PYTHON_EXTRACTION_CONTRACT_SHA256,
    PYTHON_EXTRACTION_FAILURE_REASONS, extract_python_body)
from v2b_common import (BEHAVIOR_EXTRACTED_SCHEMA, V2BError, sha256_bytes,
                        sha256_json)


SUCCESS_KEYS = {
    "status", "suite_form", "body", "body_sha256", "boundary_char",
    "n_discarded_chars", "node_kind",
}
FAILURE_KEYS = {"status", "reason"}


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


if __name__ == "__main__":
    for test_name, fn in sorted(globals().items()):
        if test_name.startswith("test_"):
            fn()
            print(f"[ok] {test_name}")
    print("V2B BEHAVIOR PYTHON EXTRACTION TESTS PASS")
