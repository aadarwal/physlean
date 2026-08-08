#!/usr/bin/env python3
"""Prospective source-span lexer for V2-b NLL attribution.

This is deliberately a SOURCE-TOKEN classifier, not an AST classifier.
It partitions every character and UTF-8 byte of an exact declaration body
into the frozen primary classes ``word``, ``literal``, ``symbol``,
``comment``, ``layout``, and ``other``.  Lean lexical-token projection must
equal the already-audited A6 lexer exactly; Python projection must equal the
same stdlib-tokenize projection used by A6.  A disagreement fails closed.

No model score is read here.  The complete partition can therefore be
generated and reviewed before any NLL contrast is opened.
"""
import hashlib
import io
import sys
import tokenize

from v2b_common import V2BError, sha256_json
from v2b_neardup import (
    LEAN_SENTINEL, _LeanCharMissingClose, _lean_id_first, _lean_id_rest,
    _scan_lean_char, _scan_lean_interpolated_string, _scan_lean_number,
    _scan_lean_raw_string, _scan_lean_string, lex_lean, lex_python)


SOURCE_TOKEN_LEDGER_SCHEMA = "v2b_source_token_ledger_v1"
SOURCE_CLASSES = ("word", "literal", "symbol", "comment", "layout",
                  "other")
CLASSIFIER_CONTRACT = dict(
    claim="source-token NLL attribution",
    ast_node_attribution=False,
    primary_source_classes=list(SOURCE_CLASSES),
    word_rule=("Lean IDENT or Python NAME, including keyword/tactic-head "
               "spellings; no semantic identifier claim"),
    literal_rule=("Lean NUM/STR/CHAR or Python NUMBER/STRING/f-string "
                  "literal piece"),
    symbol_rule="Lean/Python OP: operator, delimiter, or punctuation",
    comment_rule="language-lexer comment span",
    layout_rule="spaces, tabs, newlines, indentation, and token gaps",
    other_rule="positive-width source token outside the frozen mapping",
    lean_projection="exact equality with v2b_neardup.lex_lean",
    python_projection="exact equality with v2b_neardup.lex_python",
)
CLASSIFIER_CONTRACT_SHA256 = sha256_json(CLASSIFIER_CONTRACT)


def _utf8_prefix(text):
    prefix = [0]
    for ch in text:
        prefix.append(prefix[-1] + len(ch.encode("utf-8")))
    return prefix


def _line_data(text):
    starts = [0]
    for index, ch in enumerate(text):
        if ch == "\n":
            starts.append(index + 1)
    leading = []
    for start in starts:
        end = text.find("\n", start)
        if end < 0:
            end = len(text)
        line = text[start:end]
        width = 0
        while width < len(line) and line[width] in " \t":
            width += 1
        leading.append(line[:width])
    return starts, leading


def _span(text, byte_prefix, start, end, role, raw_kind, source_class,
          tags=None):
    if not 0 <= start < end <= len(text):
        raise V2BError(f"invalid positive source span [{start},{end})")
    spelling = text[start:end]
    return dict(
        start_char=start, end_char=end,
        start_byte=byte_prefix[start], end_byte=byte_prefix[end],
        role=role, raw_kind=raw_kind, source_class=source_class,
        spelling_sha256=(None if role == "layout" else
                         hashlib.sha256(spelling.encode("utf-8")).hexdigest()),
        sensitivity_tags=sorted(tags or []))


def _finish(text, spans, projection, expected):
    if not spans or spans[0]["start_char"] != 0 \
            or spans[-1]["end_char"] != len(text):
        raise V2BError("source spans do not cover the complete body")
    for index, row in enumerate(spans):
        if row.get("source_class") not in SOURCE_CLASSES \
                or row.get("start_char") >= row.get("end_char") \
                or row.get("start_byte") >= row.get("end_byte"):
            raise V2BError(f"malformed source span[{index}]")
        if index and spans[index - 1]["end_char"] != row["start_char"]:
            raise V2BError(f"source span partition gap/overlap at {index}")
        if index and spans[index - 1]["end_byte"] != row["start_byte"]:
            raise V2BError(f"UTF-8 source span gap/overlap at {index}")
    if spans[-1]["end_byte"] != len(text.encode("utf-8")):
        raise V2BError("source span byte partition does not conserve body")
    if projection != expected:
        raise V2BError("source-span lexer projection differs from A6 lexer")
    by_class = {name: 0 for name in SOURCE_CLASSES}
    for row in spans:
        by_class[row["source_class"]] += \
            row["end_byte"] - row["start_byte"]
    if sum(by_class.values()) != len(text.encode("utf-8")):
        raise AssertionError("source-class bytes do not conserve body")
    char_to_byte = _utf8_prefix(text)
    return dict(
        body_codepoints=len(text), body_bytes=len(text.encode("utf-8")),
        char_to_byte_prefix=char_to_byte,
        source_class_bytes=by_class, n_spans=len(spans), spans=spans,
        a6_projection_sha256=sha256_json(projection))


def lean_source_spans(text):
    """Complete Lean body partition with exact A6 lexical projection."""
    if not isinstance(text, str) or not text:
        raise V2BError("lean body must be non-empty text")
    if "\r" in text:
        raise V2BError("lean body contains CR — LF-only corpora expected")
    byte_prefix = _utf8_prefix(text)
    line_starts, leading = _line_data(text)
    spans, lexical = [], []
    n = len(text)

    def token(start, end, kind, source_class, tags=None):
        spans.append(_span(text, byte_prefix, start, end, "token", kind,
                           source_class, tags))
        lexical.append((start, kind, text[start:end]))

    index = 0
    while index < n:
        ch = text[index]
        if ch in " \t\n":
            end = index + 1
            while end < n and text[end] in " \t\n":
                end += 1
            spans.append(_span(text, byte_prefix, index, end, "layout",
                               "WHITESPACE", "layout"))
            index = end
            continue
        if text.startswith("--", index):
            end = text.find("\n", index)
            end = n if end < 0 else end
            spans.append(_span(text, byte_prefix, index, end, "comment",
                               "LINE_COMMENT", "comment"))
            index = end
            continue
        if text.startswith("/-", index):
            depth, end = 1, index + 2
            while end < n and depth:
                if text.startswith("/-", end):
                    depth, end = depth + 1, end + 2
                elif text.startswith("-/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
            if depth:
                raise V2BError("unterminated lean block comment")
            spans.append(_span(text, byte_prefix, index, end, "comment",
                               "BLOCK_COMMENT", "comment"))
            index = end
            continue
        start = index
        if ch == "«":
            end = text.find("»", index + 1)
            if end < 0:
                raise V2BError("unterminated « quoted identifier")
            end += 1
            token(start, end, "IDENT", "word", ["quoted_identifier"])
            index = end
            continue
        if ch == "r":
            end = _scan_lean_raw_string(text, index)
            if end is not None:
                token(start, end, "STR", "literal", ["raw_string"])
                index = end
                continue
        if _lean_id_first(ch):
            end = index + 1
            while end < n and _lean_id_rest(text[end]):
                end += 1
            if end < n and text[end] == '"' \
                    and text[index:end].endswith("!"):
                end = _scan_lean_interpolated_string(text, end)
                token(start, end, "STR", "literal",
                      ["compound_interpolated_literal"])
            else:
                token(start, end, "IDENT", "word")
            index = end
            continue
        if ch.isascii() and ch.isdigit():
            end = _scan_lean_number(text, index)
            token(start, end, "NUM", "literal")
            index = end
            continue
        if ch == '"':
            end = _scan_lean_string(text, index)
            token(start, end, "STR", "literal")
            index = end
            continue
        if ch == "'":
            if index + 1 < n and text[index + 1] == "'":
                while index < n and text[index] == "'":
                    token(index, index + 1, "OP", "symbol",
                          ["apostrophe_operator"])
                    index += 1
                continue
            try:
                end = _scan_lean_char(text, index)
            except _LeanCharMissingClose:
                if index > 0 and not text[index - 1].isspace():
                    token(start, start + 1, "OP", "symbol",
                          ["apostrophe_operator"])
                    index += 1
                    continue
                raise
            token(start, end, "CHAR", "literal")
            index = end
            continue
        token(start, start + 1, "OP", "symbol")
        index += 1

    # Reconstruct the exact A6 layout-sentinel stream from lexical starts.
    projection = []
    previous_line = None
    line_index = 0
    for start, kind, spelling in lexical:
        while line_index + 1 < len(line_starts) \
                and line_starts[line_index + 1] <= start:
            line_index += 1
        if previous_line is not None and line_index > previous_line:
            projection.append((LEAN_SENTINEL, leading[line_index]))
        previous_line = line_index
        projection.append((kind, spelling))
    return _finish(text, spans, projection, lex_lean(text))


def _python_index(line_starts, text, position, allow_virtual=False):
    row, column = position
    if not isinstance(row, int) or not isinstance(column, int) \
            or row < 1 or column < 0:
        raise V2BError(f"malformed Python token position {position!r}")
    # tokenize represents an EOF following a non-newline-terminated line as
    # the beginning of one virtual next line.
    if row == len(line_starts) + 1 and column == 0 \
            and not text.endswith("\n"):
        return len(text)
    if row > len(line_starts):
        raise V2BError(f"malformed Python token position {position!r}")
    index = line_starts[row - 1] + column
    # It also emits a zero-spelling implicit NEWLINE whose end column is one
    # virtual character past EOF.  It has no source bytes to classify.
    if allow_virtual and index == len(text) + 1:
        return len(text)
    if index > len(text):
        raise V2BError(f"Python token position exceeds body {position!r}")
    return index


def python_source_spans(text):
    """Complete Python body partition using the frozen stdlib tokenizer."""
    if not isinstance(text, str) or not text:
        raise V2BError("python body must be non-empty text")
    if "\r" in text:
        raise V2BError("python body contains CR — LF-only corpora expected")
    byte_prefix = _utf8_prefix(text)
    line_starts, _ = _line_data(text)
    fstrings = {getattr(tokenize, name, None): name
                for name in ("FSTRING_START", "FSTRING_MIDDLE",
                             "FSTRING_END")}
    spans, projection = [], []
    cursor = 0
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError,
            ValueError) as err:
        raise V2BError(f"python body does not tokenize: {err}") from err
    for token_index, tok in enumerate(tokens):
        virtual = tok.string == ""
        start = _python_index(line_starts, text, tok.start,
                              allow_virtual=virtual)
        end = _python_index(line_starts, text, tok.end,
                            allow_virtual=virtual)
        if start < cursor:
            raise V2BError(f"overlapping Python source token[{token_index}]")
        if start > cursor:
            spans.append(_span(text, byte_prefix, cursor, start, "layout",
                               "TOKEN_GAP", "layout"))
        positive = end > start
        if positive and text[start:end] != tok.string:
            raise V2BError(f"Python token slice drift[{token_index}]")

        if tok.type in (tokenize.ENCODING, tokenize.ENDMARKER):
            pass
        elif tok.type == tokenize.COMMENT:
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "comment",
                                   "COMMENT", "comment"))
        elif tok.type == tokenize.NL:
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "layout",
                                   "NL", "layout"))
        elif tok.type == tokenize.NEWLINE:
            projection.append(("NEWLINE", ""))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "layout",
                                   "NEWLINE", "layout"))
        elif tok.type == tokenize.INDENT:
            projection.append(("INDENT", tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "layout",
                                   "INDENT", "layout"))
        elif tok.type == tokenize.DEDENT:
            projection.append(("DEDENT", ""))
        elif tok.type == tokenize.NAME:
            projection.append(("NAME", tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "token",
                                   "NAME", "word"))
        elif tok.type == tokenize.OP:
            projection.append(("OP", tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "token",
                                   "OP", "symbol"))
        elif tok.type == tokenize.NUMBER:
            projection.append(("NUMBER", tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "token",
                                   "NUMBER", "literal"))
        elif tok.type == tokenize.STRING:
            projection.append(("STRING", tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "token",
                                   "STRING", "literal"))
        elif tok.type in fstrings and fstrings[tok.type]:
            kind = fstrings[tok.type]
            projection.append((kind, tok.string))
            if positive:
                spans.append(_span(text, byte_prefix, start, end, "token",
                                   kind, "literal",
                                   ["fstring_literal_piece"]))
        else:
            raise V2BError(
                f"unexpected python token {tokenize.tok_name[tok.type]}")
        cursor = max(cursor, end)
    if cursor < len(text):
        spans.append(_span(text, byte_prefix, cursor, len(text), "layout",
                           "TRAILING_GAP", "layout"))
    return _finish(text, spans, projection, lex_python(text))


def source_spans(language, text):
    if language == "lean":
        return lean_source_spans(text)
    if language == "python":
        return python_source_spans(text)
    raise V2BError(f"no source-token classifier for {language!r}")


def runtime_provenance():
    """Runtime facts relevant to Python tokenization, recorded in ledger."""
    return dict(
        python_version=sys.version.split()[0],
        tokenize_file_sha256=hashlib.sha256(
            open(tokenize.__file__, "rb").read()).hexdigest())
