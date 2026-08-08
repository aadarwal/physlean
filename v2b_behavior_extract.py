#!/usr/bin/env python3
"""Frozen post-hoc body extraction for V2-b behavioral generations.

Generation is a continuation of the exact target prefix, not a standalone
declaration.  The Python extractor therefore tokenizes ``prefix + generation``
and stops lazily at the first complete target suite.  Text after that boundary
is never tokenized or parsed; malformed text before it is an ordinary
extraction failure.  Contract violations in trusted inputs fail closed with
``V2BError``.

The Lean parser driver is deliberately not approximated here.  It requires a
pinned-toolchain, real-file-context command parser and will be a separate
producer under the same artifact schema.
"""
import ast
import io
import tokenize

from v2b_common import (BEHAVIOR_EXTRACTED_SCHEMA, V2BError, sha256_bytes,
                        sha256_json)
from v2b_source_tokens import _line_data, _python_index


PYTHON_TARGET_KINDS = {
    "FunctionDef": ast.FunctionDef,
    "AsyncFunctionDef": ast.AsyncFunctionDef,
    "ClassDef": ast.ClassDef,
}
PYTHON_EXTRACTION_FAILURE_REASONS = (
    "generated-empty",
    "generated-cr",
    "generated-not-utf8",
    "token-straddles-prefix-boundary",
    "tokenize-before-boundary",
    "missing-suite",
    "compound-missing-indent",
    "compound-missing-dedent",
    "simple-missing-newline",
    "empty-extracted-body",
    "ast-parse-failed",
)
PYTHON_EXTRACTION_CONTRACT = dict(
    schema="v2b_python_body_extraction_contract_v1",
    artifact_schema=BEHAVIOR_EXTRACTED_SCHEMA,
    input="exact prefix ending at suite colon plus full generated continuation",
    line_endings="prefix is trusted LF-only; generated CR is failure",
    encoding="UTF-8; unencodable generated text is failure",
    prefix_boundary=("any stdlib-tokenize token crossing from prefix into "
                     "generation is failure"),
    dispatch=("first token at/after prefix after COMMENT/NL: NEWLINE means "
              "compound suite; otherwise simple-statement suite"),
    simple_boundary=("end of first logical NEWLINE; semicolons, implicit "
                     "joins, and backslash continuations remain inside"),
    compound_boundary=("start of matching DEDENT returning target suite "
                       "depth to zero; implicit EOF DEDENT maps to EOF"),
    lazy_stop=("tokens after the selected boundary are never requested; "
               "trailing declarations or malformed junk are discarded"),
    validation=("ast.parse(prefix + extracted body) yields exactly one "
                "module-level node of the frozen target kind/name"),
    position_units=("tokenizer row/Unicode-column positions map to character "
                    "indices with virtual EOF clamped; hashes use UTF-8"),
    ordinary_failure_reasons=list(PYTHON_EXTRACTION_FAILURE_REASONS),
)
PYTHON_EXTRACTION_CONTRACT_SHA256 = sha256_json(
    PYTHON_EXTRACTION_CONTRACT)


def _failure(reason):
    if reason not in PYTHON_EXTRACTION_FAILURE_REASONS:
        raise AssertionError(f"unfrozen Python extraction failure {reason}")
    return {"status": "extraction-failure", "reason": reason}


def _token_position(line_starts, document, token):
    virtual = token.string == ""
    start = _python_index(line_starts, document, token.start,
                          allow_virtual=virtual)
    end = _python_index(line_starts, document, token.end,
                        allow_virtual=virtual)
    if not 0 <= start <= end <= len(document):
        raise V2BError("Python extraction token position is out of bounds")
    return start, end


def extract_python_body(prefix_text, generated_text, target_kind,
                        expected_name=None):
    """Extract one complete generated Python body.

    Success has exactly the keys ``status``, ``suite_form``, ``body``,
    ``body_sha256``, ``boundary_char``, ``n_discarded_chars``, and
    ``node_kind``.  ``boundary_char`` is relative to ``generated_text``.
    Ordinary generated-output failures have only ``status`` and ``reason``.
    """
    if not isinstance(prefix_text, str) or not prefix_text:
        raise V2BError("Python extraction prefix must be non-empty text")
    if "\r" in prefix_text:
        raise V2BError("Python extraction prefix is not LF-only")
    try:
        prefix_text.encode("utf-8")
    except UnicodeEncodeError as err:
        raise V2BError("Python extraction prefix is not UTF-8 encodable") \
            from err
    if not prefix_text.endswith(":"):
        raise V2BError("Python extraction prefix does not end at suite colon")
    if not isinstance(generated_text, str):
        raise V2BError("Python generated continuation must be text")
    if target_kind not in PYTHON_TARGET_KINDS:
        raise V2BError(f"unsupported Python target kind {target_kind!r}")
    if expected_name is not None \
            and (not isinstance(expected_name, str) or not expected_name):
        raise V2BError("expected Python target name is malformed")
    # Kind and name live wholly inside trusted P.  Validate them before
    # looking at G so provenance drift cannot be counted as a model failure.
    try:
        prefix_tree = ast.parse(prefix_text + " pass")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as err:
        raise V2BError("Python extraction prefix is not a standalone target "
                       "header") from err
    if len(prefix_tree.body) != 1 \
            or not isinstance(prefix_tree.body[0],
                              PYTHON_TARGET_KINDS[target_kind]) \
            or expected_name is not None \
            and getattr(prefix_tree.body[0], "name", None) != expected_name:
        raise V2BError("Python extraction prefix kind/name provenance drift")
    if not generated_text:
        return _failure("generated-empty")
    if "\r" in generated_text:
        return _failure("generated-cr")
    try:
        generated_text.encode("utf-8")
    except UnicodeEncodeError:
        return _failure("generated-not-utf8")

    document = prefix_text + generated_text
    prefix_end = len(prefix_text)
    line_starts, _ = _line_data(document)
    tokens = iter(tokenize.generate_tokens(io.StringIO(document).readline))

    def positioned(token):
        start, end = _token_position(line_starts, document, token)
        if start < prefix_end < end:
            return None, _failure("token-straddles-prefix-boundary")
        return (token, start, end), None

    try:
        first = None
        for token in tokens:
            positioned_token, failure = positioned(token)
            if failure is not None:
                return failure
            token, start, end = positioned_token
            if end <= prefix_end:
                continue
            if start < prefix_end:
                # Only a positive-width crossing can reach here, and that was
                # rejected above.  A zero-width prefix token is not a suite.
                continue
            if token.type in (tokenize.COMMENT, tokenize.NL):
                continue
            first = (token, start, end)
            break
        if first is None or first[0].type == tokenize.ENDMARKER:
            return _failure("missing-suite")

        if first[0].type == tokenize.NEWLINE:
            suite_form = "compound"
            depth = 0
            for token in tokens:
                positioned_token, failure = positioned(token)
                if failure is not None:
                    return failure
                token, start, end = positioned_token
                if token.type in (tokenize.COMMENT, tokenize.NL):
                    continue
                if token.type != tokenize.INDENT:
                    return _failure("compound-missing-indent")
                depth = 1
                break
            if depth == 0:
                return _failure("compound-missing-indent")
            boundary_document = None
            for token in tokens:
                positioned_token, failure = positioned(token)
                if failure is not None:
                    return failure
                token, start, end = positioned_token
                if token.type == tokenize.INDENT:
                    depth += 1
                elif token.type == tokenize.DEDENT:
                    depth -= 1
                    if depth == 0:
                        boundary_document = start
                        break
                    if depth < 0:
                        return _failure("compound-missing-dedent")
                elif token.type == tokenize.ENDMARKER:
                    return _failure("compound-missing-dedent")
            if boundary_document is None:
                return _failure("compound-missing-dedent")
        else:
            if first[0].type in (tokenize.INDENT, tokenize.DEDENT):
                return _failure("missing-suite")
            suite_form = "simple-statement"
            boundary_document = None
            token, start, end = first
            while True:
                if token.type == tokenize.NEWLINE:
                    boundary_document = end
                    break
                if token.type == tokenize.ENDMARKER:
                    return _failure("simple-missing-newline")
                token = next(tokens)
                positioned_token, failure = positioned(token)
                if failure is not None:
                    return failure
                token, start, end = positioned_token
            if boundary_document is None:
                return _failure("simple-missing-newline")
    except (tokenize.TokenError, IndentationError, TabError, SyntaxError,
            ValueError, StopIteration, V2BError):
        return _failure("tokenize-before-boundary")

    boundary_char = boundary_document - prefix_end
    if not 0 < boundary_char <= len(generated_text):
        return _failure("empty-extracted-body")
    body = generated_text[:boundary_char]
    candidate = prefix_text + body
    if candidate != document[:boundary_document]:
        raise AssertionError("Python extraction boundary does not slice D")
    try:
        tree = ast.parse(candidate)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _failure("ast-parse-failed")
    if len(tree.body) != 1:
        raise AssertionError("Python extraction produced multiple AST nodes")
    node = tree.body[0]
    if not isinstance(node, PYTHON_TARGET_KINDS[target_kind]):
        raise AssertionError("Python extraction changed trusted target kind")
    if expected_name is not None and getattr(node, "name", None) != \
            expected_name:
        raise AssertionError("Python extraction changed trusted target name")
    if not getattr(node, "body", None):
        return _failure("ast-parse-failed")
    body_bytes = body.encode("utf-8")
    return dict(
        status="extracted", suite_form=suite_form, body=body,
        body_sha256=sha256_bytes(body_bytes), boundary_char=boundary_char,
        n_discarded_chars=len(generated_text) - boundary_char,
        node_kind=type(node).__name__)
