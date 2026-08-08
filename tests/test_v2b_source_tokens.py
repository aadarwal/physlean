#!/usr/bin/env python3
"""Source-token partitions are exact, UTF-8 safe, and A6-equivalent."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError
import v2b_source_tokens as source_module
from v2b_source_tokens import (CLASSIFIER_CONTRACT,
                               CLASSIFIER_CONTRACT_SHA256,
                               SOURCE_CLASSES, lean_source_spans,
                               python_source_spans, source_spans)
from v2b_common import sha256_json


def _assert_partition(text, value):
    assert value["body_codepoints"] == len(text)
    assert value["body_bytes"] == len(text.encode("utf-8"))
    spans = value["spans"]
    assert spans[0]["start_char"] == spans[0]["start_byte"] == 0
    assert spans[-1]["end_char"] == len(text)
    assert spans[-1]["end_byte"] == len(text.encode("utf-8"))
    for left, right in zip(spans, spans[1:]):
        assert left["end_char"] == right["start_char"]
        assert left["end_byte"] == right["start_byte"]
    assert sum(value["source_class_bytes"].values()) == \
        len(text.encode("utf-8"))
    assert set(value["source_class_bytes"]) == set(SOURCE_CLASSES)


def test_contract_is_explicitly_non_ast_and_hash_bound():
    assert CLASSIFIER_CONTRACT["ast_node_attribution"] is False
    assert CLASSIFIER_CONTRACT["claim"] == "source-token NLL attribution"
    assert CLASSIFIER_CONTRACT_SHA256 == sha256_json(CLASSIFIER_CONTRACT)


def test_lean_partition_unicode_comments_literals_and_projection():
    text = (
        "  by -- line marker λ\n"
        "    let 𝔸 := r#\"raw -- /- x -/ λ\"#\n"
        "    /- outer /- inner -/ done -/\n"
        "    exact s!\"value {f \"{- not comment\"} λ\" ++ '\u03bb' ++ \"«q»\"")
    value = lean_source_spans(text)
    _assert_partition(text, value)
    classes = {row["source_class"] for row in value["spans"]}
    assert {"word", "literal", "symbol", "comment", "layout"} <= classes
    interpolated = [row for row in value["spans"]
                    if "compound_interpolated_literal" in
                    row["sensitivity_tags"]]
    assert len(interpolated) == 1
    # Every char endpoint converts to the exact UTF-8 prefix length.
    for row in value["spans"]:
        assert row["start_byte"] == len(
            text[:row["start_char"]].encode("utf-8"))
        assert row["end_byte"] == len(
            text[:row["end_char"]].encode("utf-8"))


def test_lean_primes_chars_and_multiline_layout():
    text = "by\n  exact F X⟦(1 : ℤ)⟧' ++ '\\u03bb' ++ ''"
    value = lean_source_spans(text)
    _assert_partition(text, value)
    prime_rows = [row for row in value["spans"]
                  if "apostrophe_operator" in row["sensitivity_tags"]]
    assert len(prime_rows) >= 3
    assert any(row["raw_kind"] == "CHAR" for row in value["spans"])


def test_python_partition_unicode_comments_strings_and_keywords():
    text = (
        "    # comment λ\n"
        "    match 𝔸:\n"
        "        case 1:\n"
        "            return f\"emoji 🙂 {𝔸 + 1}\"\n")
    value = python_source_spans(text)
    _assert_partition(text, value)
    assert any(row["raw_kind"] == "COMMENT" and
               row["source_class"] == "comment" for row in value["spans"])
    # Keywords deliberately remain lexical words in the primary contract.
    assert sum(row["source_class"] == "word" for row in value["spans"]) >= 4
    assert any(row["source_class"] == "literal" for row in value["spans"])
    for row in value["spans"]:
        assert row["start_byte"] == len(
            text[:row["start_char"]].encode("utf-8"))
        assert row["end_byte"] == len(
            text[:row["end_char"]].encode("utf-8"))


def test_python_tabs_blank_lines_and_no_terminal_newline():
    for text in ("\treturn 1\n\n", "x + 1"):
        value = python_source_spans(text)
        _assert_partition(text, value)
        assert value["a6_projection_sha256"]


def test_language_dispatch_and_fail_closed_inputs():
    assert source_spans("lean", "by trivial")["n_spans"] > 0
    assert source_spans("python", "return 1\n")["n_spans"] > 0
    for language, text in (("lean", "by\r\n trivial"),
                           ("python", "return 1\r\n")):
        try:
            source_spans(language, text)
            assert False, "CR input accepted"
        except V2BError as err:
            assert "CR" in str(err)
    try:
        source_spans("cpp", "return 1;")
        assert False, "unsupported language accepted"
    except V2BError as err:
        assert "classifier" in str(err)


def test_python_tokenizer_value_error_is_typed_fail_closed():
    original = source_module.tokenize.generate_tokens

    def broken(_readline):
        raise ValueError("malformed f-string fixture")

    source_module.tokenize.generate_tokens = broken
    try:
        try:
            python_source_spans("f'{x}'")
            assert False, "bare tokenizer ValueError escaped"
        except V2BError as err:
            assert "does not tokenize" in str(err)
    finally:
        source_module.tokenize.generate_tokens = original


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B SOURCE TOKEN TESTS PASS")
