#!/usr/bin/env python3
"""Frozen post-hoc body extraction for V2-b behavioral generations.

Generation is a continuation of the exact target prefix, not a standalone
declaration.  The Python extractor therefore tokenizes ``prefix + generation``
and stops lazily at the first complete target suite.  Text after that boundary
is never tokenized or parsed; malformed text before it is an ordinary
extraction failure.  Contract violations in trusted inputs fail closed with
``V2BError``.

Lean uses the separate pinned-toolchain driver in
``lean_drivers/V2BParseCommand.lean``.  This module freezes and validates that
driver's exact manifest-bound transcript surface; the future file producer
will bind both language paths under the same artifact schema.
"""
import ast
import copy
import io
import json
import tokenize

from v2b_common import (BEHAVIOR_EXTRACTED_SCHEMA, V2BError, sha256_bytes,
                        sha256_file, sha256_json)
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


LEAN_DRIVER_MANIFEST_SCHEMA = "v2b_lean_parse_manifest_v1"
LEAN_DRIVER_OUTPUT_SCHEMA = "v2b_lean_parse_result_v1"
LEAN_DRIVER_OUTPUT_MARKER = "@@V2B_LEAN_PARSE@@"
LEAN_DRIVER_INVOCATION_BINDING_SCHEMA = \
    "v2b_lean_driver_invocation_binding_v1"
LEAN_EXTRACTION_FAILURE_REASONS = (
    "parse-error-in-target",
    "has-missing",
    "terminal-command",
    "missing-source-range",
    "target-start-drift",
    "syntax-kind-drift",
    "body-slot-drift",
    "token-crosses-header-boundary",
    "header-syntax-drift",
    "reconstructed-module-parse-drift",
    "empty-body",
    "end-beyond-generated-region",
)
LEAN_EXTRACTION_CONTRACT = dict(
    schema="v2b_lean_body_extraction_contract_v1",
    artifact_schema=BEHAVIOR_EXTRACTED_SCHEMA,
    manifest_schema=LEAN_DRIVER_MANIFEST_SCHEMA,
    driver_output_schema=LEAN_DRIVER_OUTPUT_SCHEMA,
    driver_output_marker=LEAN_DRIVER_OUTPUT_MARKER,
    driver_source="lean_drivers/V2BParseCommand.lean",
    position_units="raw UTF-8 byte offsets",
    boundary_provenance=("the producer must join the exact extraction identity "
                         "to one resolved row of the complete parser-backed "
                         "v2b_lean_body_boundaries_v1 artifact before "
                         "generation; H, delimiter, body bytes, source span, "
                         "and span_id come from that effective split, while "
                         "the legacy V2-a lexical split is diagnostic only; "
                         "the artifact SHA and span_id are bound again by the "
                         "combined S4/S5 evidence producer"),
    preparation=("load an exact Lake ModuleSetup (package, imports, import "
                 "artifacts, plugins, dynamic libraries, and options), "
                 "reparse imported/CLI option overrides using the pinned "
                 "frontend's exact async default and setup/file precedence, "
                 "then parse and elaborate only commands strictly before the "
                 "exact frozen target range; inside isolated streams, "
                 "synchronously force every complete residual snapshot-task "
                 "tree from each trusted command, reject asynchronous error "
                 "diagnostics, and clear settled tasks before continuing"),
    trusted_boundary=("the parser-backed effective original delimiter must "
                      "be an exact token "
                      "and replacing everything from its boundary with a "
                      "same-form minimal sentinel must parse one complete "
                      "declaration with the same start/kind/pre-boundary "
                      "projection; this distinguishes the declaration-value "
                      "slot from :=/|/where inside its statement/type"),
    generated_safety=("reuse the resulting parser/scope state to parse one "
                      "target command first in input truncated exactly at "
                      "the generated end and then in the reconstructed module "
                      "containing only the retained body plus the immutable "
                      "original suffix; never elaborate generated target "
                      "syntax"),
    splice_proof=("spliced bytes through the original header boundary and "
                  "after the generated region must equal the corresponding "
                  "original prefix and post-target suffix"),
    boundary=("the trusted original boundary must begin an exact canonical "
              "token whose spelling is its parser-backed effective delimiter; generated "
              "continuations may begin with parser-recognized trivia, after "
              "which their first unique canonical token must be an exact "
              "member of {:=, where, |}; this permits another verifier-valid "
              "Lean body form but forbids generated binders/type annotations "
              "from changing the frozen-header body task; retain from that boundary through "
              "the canonical tail of the first complete command in input "
              "truncated at generated end; "
              "Lean may lex trailing trivia/lookahead before returning, so a "
              "lexical error after the canonical tail is still a failure, but "
              "no byte after the generated end or in the suffix is visible"),
    validation=("reject any generated canonical syntax token crossing the "
                "header/body "
                "boundary; exact pre-boundary syntax projections (node kind, "
                "child index, token spelling/value, and byte range), command "
                "start, and outer syntax kind must match the unelaborated "
                "original; the retained-continuation reconstruction must be "
                "structurally/range-equal to the truncated syntax; recovery, diagnostics, "
                "missing nodes, terminal commands, and boundary crossing "
                "fail"),
    invocation_binding=("SHA256 of a canonical exact-manifest projection plus "
                        "the SHA256 of the original, ModuleSetup, and every "
                        "spliced file is echoed by the driver and recomputed "
                        "by the consumer"),
    stdout=("only marker-prefixed compact JSON is evidence; unrelated "
            "process output is ignored, while malformed, duplicate, missing, "
            "or extra marked records fail closed; the consumer requires the "
            "exact manifest and enforces a reason-field truth table"),
    ordinary_failure_reasons=list(LEAN_EXTRACTION_FAILURE_REASONS),
)
LEAN_EXTRACTION_CONTRACT_SHA256 = sha256_json(LEAN_EXTRACTION_CONTRACT)


_LEAN_MANIFEST_KEYS = {
    "schema", "originalFile", "moduleSetupFile", "moduleName",
    "targetIdentity", "targetKind", "targetStartByte", "targetEndByte",
    "headerEndByte", "bodyDelimiter", "optionOverrides", "samples",
    "invocationBinding",
}
_LEAN_MANIFEST_UNBOUND_KEYS = _LEAN_MANIFEST_KEYS - {"invocationBinding"}
_LEAN_MANIFEST_SAMPLE_KEYS = {"id", "splicedFile", "generatedEndByte"}
_LEAN_MANIFEST_OPTION_KEYS = {"name", "value"}
_LEAN_PREVALIDATION_KEYS = {
    "schema", "record_type", "module_name", "target_identity",
    "target_kind", "target_start_byte", "target_end_byte",
    "header_end_byte", "body_delimiter", "syntax_kind",
    "header_syntax_projection", "n_prior_commands",
    "generated_target_elaborated", "invocation_binding",
    "body_boundary_probe_validated",
}
_LEAN_SUCCESS_KEYS = {
    "schema", "record_type", "sample_id", "status", "start_byte",
    "end_byte", "body_start_byte", "body_bytes", "syntax_kind",
    "n_parse_messages", "recovering", "has_missing",
    "generated_target_elaborated",
}
_LEAN_FAILURE_KEYS = {
    "schema", "record_type", "sample_id", "status", "reason",
    "n_parse_messages", "recovering", "has_missing",
    "generated_target_elaborated",
}


def _lean_output_int(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise V2BError(f"Lean driver {label} is not a nonnegative integer")
    return value


def _lean_binding_payload(manifest):
    """Return the frozen exact-manifest/content invocation projection.

    ``invocationBinding`` is deliberately excluded from its own preimage.
    Paths remain in the manifest projection and each referenced file is also
    content-addressed in role/sample order.  This makes changing a path,
    option, byte boundary, sample, setup, or any referenced file change the
    binding even when the visible target/sample IDs stay fixed.
    """
    if not isinstance(manifest, dict) or set(manifest) not in (
            _LEAN_MANIFEST_UNBOUND_KEYS, _LEAN_MANIFEST_KEYS):
        raise V2BError("Lean driver manifest has schema/key drift")
    unbound = copy.deepcopy(manifest)
    unbound.pop("invocationBinding", None)
    for key in ("originalFile", "moduleSetupFile"):
        if not isinstance(unbound.get(key), str) or not unbound[key]:
            raise V2BError(f"Lean driver manifest {key} is empty")
    samples = unbound.get("samples")
    if not isinstance(samples, list) or not samples:
        raise V2BError("Lean driver manifest samples are empty/non-list")
    files = [
        dict(role="original", path=unbound["originalFile"],
             sha256=sha256_file(unbound["originalFile"])),
        dict(role="module-setup", path=unbound["moduleSetupFile"],
             sha256=sha256_file(unbound["moduleSetupFile"])),
    ]
    for sample in samples:
        if not isinstance(sample, dict) \
                or not isinstance(sample.get("id"), str) \
                or not sample["id"] \
                or not isinstance(sample.get("splicedFile"), str) \
                or not sample["splicedFile"]:
            raise V2BError("Lean driver manifest sample is malformed")
        files.append(dict(role="sample", sample_id=sample["id"],
                          path=sample["splicedFile"],
                          sha256=sha256_file(sample["splicedFile"])))
    return dict(schema=LEAN_DRIVER_INVOCATION_BINDING_SCHEMA,
                manifest=unbound, files=files)


def lean_driver_invocation_binding(manifest):
    """Hash the exact semantic manifest and bytes it instructs Lean to read."""
    return sha256_json(_lean_binding_payload(manifest))


def bind_lean_driver_manifest(manifest):
    """Return a copy with its prospective invocation binding filled in."""
    if not isinstance(manifest, dict) \
            or set(manifest) != _LEAN_MANIFEST_UNBOUND_KEYS:
        raise V2BError("unbound Lean driver manifest has schema/key drift")
    bound = copy.deepcopy(manifest)
    bound["invocationBinding"] = lean_driver_invocation_binding(bound)
    return bound


def _validate_lean_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != _LEAN_MANIFEST_KEYS:
        raise V2BError("Lean driver manifest has schema/key drift")
    if manifest.get("schema") != LEAN_DRIVER_MANIFEST_SCHEMA:
        raise V2BError("Lean driver manifest schema drift")
    binding = manifest.get("invocationBinding")
    if not isinstance(binding, str) or len(binding) != 64 \
            or any(char not in "0123456789abcdef" for char in binding):
        raise V2BError("Lean driver invocation binding is malformed")
    if binding != lean_driver_invocation_binding(manifest):
        raise V2BError("Lean driver invocation binding/content drift")
    for key in ("originalFile", "moduleSetupFile", "moduleName",
                "targetIdentity"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise V2BError(f"Lean driver manifest {key} is empty")
    if manifest.get("targetKind") not in ("theorem", "lemma", "def"):
        raise V2BError("Lean driver manifest targetKind is unsupported")
    if manifest.get("bodyDelimiter") not in (":=", "where", "|"):
        raise V2BError("Lean driver manifest bodyDelimiter is unsupported")
    start = _lean_output_int(manifest.get("targetStartByte"),
                             "manifest targetStartByte")
    header = _lean_output_int(manifest.get("headerEndByte"),
                              "manifest headerEndByte")
    end = _lean_output_int(manifest.get("targetEndByte"),
                           "manifest targetEndByte")
    if not start < header < end:
        raise V2BError("Lean driver manifest byte order is invalid")
    options = manifest.get("optionOverrides")
    if not isinstance(options, list):
        raise V2BError("Lean driver optionOverrides is not a list")
    option_names = []
    for option in options:
        if not isinstance(option, dict) \
                or set(option) != _LEAN_MANIFEST_OPTION_KEYS \
                or not isinstance(option.get("name"), str) \
                or not option["name"] \
                or not isinstance(option.get("value"), str) \
                or not option["value"] \
                or option["name"] == "Elab.async":
            raise V2BError("Lean driver option override is malformed")
        option_names.append(option["name"])
    if len(set(option_names)) != len(option_names):
        raise V2BError("Lean driver option overrides are duplicated")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise V2BError("Lean driver manifest samples are empty/non-list")
    ids = []
    generated_ends = {}
    for sample in samples:
        if not isinstance(sample, dict) \
                or set(sample) != _LEAN_MANIFEST_SAMPLE_KEYS \
                or not isinstance(sample.get("id"), str) or not sample["id"] \
                or not isinstance(sample.get("splicedFile"), str) \
                or not sample["splicedFile"]:
            raise V2BError("Lean driver manifest sample is malformed")
        generated_end = _lean_output_int(sample.get("generatedEndByte"),
                                         "manifest generatedEndByte")
        if generated_end < header:
            raise V2BError("Lean generated end precedes header boundary")
        ids.append(sample["id"])
        generated_ends[sample["id"]] = generated_end
    if len(set(ids)) != len(ids):
        raise V2BError("Lean driver manifest sample ids are duplicated")
    return ids, generated_ends, start, header, end


def parse_lean_driver_stdout(stdout, manifest):
    """Validate and return one complete marked Lean-driver transcript.

    Output from elaborated trusted prefix commands is deliberately ignored.
    Every marker-prefixed line, however, is evidence and must decode under an
    exact whitelist and must bind the exact already duplicate-key-validated
    manifest.  The returned object has ``prevalidation`` and ``samples`` in
    manifest order.
    """
    if not isinstance(stdout, str):
        raise V2BError("Lean driver stdout must be text")
    expected, generated_ends, manifest_start, manifest_header, manifest_end = \
        _validate_lean_manifest(manifest)

    def object_no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(f"duplicate Lean driver JSON key {key!r}")
            value[key] = item
        return value

    def reject_nonfinite(value):
        raise V2BError(f"non-finite Lean driver number {value}")

    records = []
    for line in stdout.splitlines():
        if not line.startswith(LEAN_DRIVER_OUTPUT_MARKER):
            continue
        payload = line[len(LEAN_DRIVER_OUTPUT_MARKER):]
        try:
            value = json.loads(payload, object_pairs_hook=object_no_duplicates,
                               parse_constant=reject_nonfinite)
        except (json.JSONDecodeError, UnicodeError, V2BError) as err:
            raise V2BError(f"malformed marked Lean driver record: {err}") \
                from err
        if not isinstance(value, dict):
            raise V2BError("marked Lean driver record is not an object")
        records.append(value)
    if len(records) != len(expected) + 1:
        raise V2BError("Lean driver marked record count does not match manifest")

    prevalidation = records[0]
    if set(prevalidation) != _LEAN_PREVALIDATION_KEYS \
            or prevalidation.get("schema") != LEAN_DRIVER_OUTPUT_SCHEMA \
            or prevalidation.get("record_type") != "prevalidation":
        raise V2BError("Lean driver prevalidation schema/key drift")
    if not isinstance(prevalidation["syntax_kind"], str) \
            or not prevalidation["syntax_kind"] \
            or not isinstance(prevalidation["header_syntax_projection"], str) \
            or not prevalidation["header_syntax_projection"]:
        raise V2BError("Lean driver prevalidation string field is empty")
    start = _lean_output_int(prevalidation["target_start_byte"],
                             "target_start_byte")
    header = _lean_output_int(prevalidation["header_end_byte"],
                              "header_end_byte")
    end = _lean_output_int(prevalidation["target_end_byte"],
                           "target_end_byte")
    _lean_output_int(prevalidation["n_prior_commands"], "n_prior_commands")
    if not start < header < end:
        raise V2BError("Lean driver prevalidation byte order is invalid")
    expected_prevalidation = {
        "module_name": manifest["moduleName"],
        "target_identity": manifest["targetIdentity"],
        "target_kind": manifest["targetKind"],
        "target_start_byte": manifest_start,
        "target_end_byte": manifest_end,
        "header_end_byte": manifest_header,
        "body_delimiter": manifest["bodyDelimiter"],
        "invocation_binding": manifest["invocationBinding"],
    }
    for key, value in expected_prevalidation.items():
        if prevalidation[key] != value:
            raise V2BError(f"Lean prevalidation {key} is not manifest-bound")
    if prevalidation["generated_target_elaborated"] is not False:
        raise V2BError("Lean driver claims generated target elaboration")
    if prevalidation["body_boundary_probe_validated"] is not True:
        raise V2BError("Lean driver did not validate the original body slot")

    sample_records = records[1:]
    if [row.get("sample_id") for row in sample_records] != expected:
        raise V2BError("Lean driver sample id membership/order drift")
    for row in sample_records:
        if row.get("schema") != LEAN_DRIVER_OUTPUT_SCHEMA \
                or row.get("record_type") != "sample" \
                or row.get("generated_target_elaborated") is not False:
            raise V2BError("Lean driver sample schema/safety drift")
        if not isinstance(row.get("has_missing"), bool) \
                or not isinstance(row.get("recovering"), bool):
            raise V2BError("Lean driver parser-state flags are not boolean")
        n_messages = _lean_output_int(row.get("n_parse_messages"),
                                      "n_parse_messages")
        if row.get("status") == "extracted":
            if set(row) != _LEAN_SUCCESS_KEYS:
                raise V2BError("Lean driver success key drift")
            row_start = _lean_output_int(row["start_byte"], "start_byte")
            row_end = _lean_output_int(row["end_byte"], "end_byte")
            body_start = _lean_output_int(row["body_start_byte"],
                                          "body_start_byte")
            body_bytes = _lean_output_int(row["body_bytes"], "body_bytes")
            if row_start != start or body_start != header \
                    or not body_start < row_end \
                    or body_bytes != row_end - body_start \
                    or row_end > generated_ends[row["sample_id"]] \
                    or row["syntax_kind"] != prevalidation["syntax_kind"] \
                    or n_messages != 0 or row["recovering"] is not False \
                    or row["has_missing"] is not False:
                raise V2BError("Lean driver success invariant drift")
        elif row.get("status") == "extraction-failure":
            if set(row) != _LEAN_FAILURE_KEYS:
                raise V2BError("Lean driver failure key drift")
            if row.get("reason") not in LEAN_EXTRACTION_FAILURE_REASONS:
                raise V2BError("unfrozen Lean extraction failure reason")
            reason = row["reason"]
            if reason == "parse-error-in-target":
                if n_messages == 0 and row["recovering"] is not True:
                    raise V2BError("Lean parse-error lacks diagnostics/recovery")
            elif reason == "has-missing":
                if n_messages != 0 or row["recovering"] is not False \
                        or row["has_missing"] is not True:
                    raise V2BError("Lean has-missing failure field drift")
            elif reason != "reconstructed-module-parse-drift":
                if n_messages != 0 or row["recovering"] is not False \
                        or row["has_missing"] is not False:
                    raise V2BError("Lean clean failure carries parser errors")
        else:
            raise V2BError("Lean driver sample status is unfrozen")
    return {"prevalidation": prevalidation, "samples": sample_records}


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
