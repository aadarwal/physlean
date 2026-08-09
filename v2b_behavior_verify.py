#!/usr/bin/env python3
"""Strict Lean S5 verifier contract and transcript consumer for V2-b.

The baseline file and each S4-retained reconstruction run in separate fresh OS
processes from the same exact pre-target frontend state.  The baseline process
emits a strict kernel-type certificate; one candidate process consumes that
certificate, independently kernel-replays its generated environment delta,
checks exact target identity/type and the frozen no-new-trusted-surface rule,
then elaborates the immutable suffix through EOF.  This module owns the
duplicate-key gate, exact manifest/content binding, certificate grammar, and
finite evidence surface.  It deliberately does not generate model output or
write a behavioral outcome artifact.
"""
import copy
import json
import os

from v2b_behavior_extract import (LEAN_DRIVER_MANIFEST_SCHEMA,
                                  LEAN_EXTRACTION_CONTRACT_SHA256)
from v2b_common import (V2BError, sha256_bytes, sha256_file,
                        sha256_sorted_json)
from v2b_lean_boundaries import BOUNDARIES_SCHEMA


LEAN_VERIFY_MANIFEST_SCHEMA = "v2b_lean_verify_manifest_v2"
LEAN_VERIFY_OUTPUT_SCHEMA = "v2b_lean_verify_result_v2"
LEAN_VERIFY_OUTPUT_MARKER = "@@V2B_LEAN_VERIFY@@"
LEAN_VERIFY_DRIVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "lean_drivers", "V2BVerifyCommand.lean")
LEAN_PARSE_DRIVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "lean_drivers", "V2BParseCommand.lean")

LEAN_VERIFY_FAILURE_REASONS = (
    "elaboration-exception",
    "elaboration-error",
    "target-name-drift",
    "target-kind-drift",
    "target-type-drift",
    "target-type-new-constant",
    "new-axiom",
    "unsafe-or-partial",
    "implemented-by",
    "sorry",
    "native-reflection",
    "new-axiom-dependency",
    "kernel-replay-failed",
    "kernel-defeq-error",
    "suffix-parse-error",
    "suffix-elaboration-exception",
    "suffix-elaboration-error",
)
FORBIDDEN_FAILURE_REASONS = frozenset((
    "new-axiom", "unsafe-or-partial", "implemented-by", "sorry",
    "native-reflection", "new-axiom-dependency",
))
TARGET_ELABORATION_FAILED_REASONS = frozenset((
    "elaboration-exception", "elaboration-error",
))
SUFFIX_FAILURE_REASONS = frozenset((
    "suffix-parse-error", "suffix-elaboration-exception",
    "suffix-elaboration-error",
))

LEAN_VERIFY_CONTRACT = dict(
    schema="v2b_lean_semantic_verification_contract_v2",
    manifest_schema=LEAN_VERIFY_MANIFEST_SCHEMA,
    output_schema=LEAN_VERIFY_OUTPUT_SCHEMA,
    output_marker=LEAN_VERIFY_OUTPUT_MARKER,
    artifact_schema="v2b_behavior_verified_complete_v1",
    input=("baseline mode receives one trusted original module and no sample; "
           "candidate mode receives the same trusted original only for exact "
           "pre-target preparation plus exactly one reconstructed module "
           "containing the S4-retained continuation and immutable suffix"),
    logical_filename=("all original and reconstructed bytes are parsed and "
                      "elaborated under the original repository source path; "
                      "scratch paths never affect file-scoped names or "
                      "file-relative terms such as include_str"),
    preparation=("load exact ModuleSetup/import artifacts/plugins/dynlibs and "
                 "option order; elaborate only trusted commands before the "
                 "target synchronously; force Elab.async, "
                 "debug.skipKernelTC, and debug.proofAsSorry false before "
                 "every command"),
    process_isolation=("baseline and candidate modes are separate fresh OS "
                       "processes; candidate mode never elaborates the original "
                       "target or original suffix, preventing process-global "
                       "initializer/plugin/IO.Ref state leakage"),
    baseline=("in baseline mode elaborate the original target, verify it under "
              "the semantic gates, and elaborate its exact original suffix to "
              "EOF; failure is arm-independent HARNESS-INVALID; on success "
              "emit a strict canonical kernel-type expression certificate"),
    target=("the exact fully-qualified Name created by the candidate equals "
            "the independently elaborated baseline Name; theorem/lemma must "
            "produce thmInfo and def must produce defnInfo"),
    type_equality=("instantiate baseline and candidate universe parameters by "
                   "ordinal; strictly decode the locally owned tagged Expr/"
                   "Level/Name certificate grammar; reject free, meta, or loose "
                   "variables, noncanonical universe names, and constants or "
                   "projection type names outside the immutable pre-target "
                   "environment; Kernel.check the certificate as a type; "
                   "require equal universe-parameter arity and Kernel.isDefEq; "
                   "proof/value terms may differ"),
    kernel_certificate=("diff every post-target constant from the immutable "
                        "pre-target environment and replay the complete map "
                        "with Environment.replay; rejection is an ordinary "
                        "verification failure"),
    forbidden_surface=("for every new constant: reject axiomInfo, unsafe, "
                       "partial, any implemented_by extension drift, and any "
                       "transitive axiom not already an axiom in the "
                       "pre-target environment; sorryAx, Lean.ofReduceBool, "
                       "and Lean.ofReduceNat are forbidden absolutely"),
    suffix=("after target verification, parse and elaborate the exact original "
            "post-target suffix to terminal EOF from the candidate target "
            "state; any suffix failure is an ordinary model zero"),
    timeout=("the file producer enforces a separate fixed 300-second deadline "
             "for each fresh baseline or candidate process; baseline timeout "
             "is HARNESS-INVALID and candidate timeout is an ordinary zero"),
    invocation_binding=("recursively key-sorted SHA256 over the exact unbound "
                        "manifest (including mode and baseline certificate) plus "
                        "role-ordered live SHA256s of original, ModuleSetup, and "
                        "the one candidate reconstruction when present"),
    failure_reasons=list(LEAN_VERIFY_FAILURE_REASONS),
)
LEAN_VERIFY_CONTRACT_SHA256 = sha256_sorted_json(LEAN_VERIFY_CONTRACT)


_MANIFEST_KEYS = frozenset((
    "schema", "mode", "invocationBinding", "originalFile", "logicalFileName",
    "originalSha256",
    "moduleSetupFile", "moduleSetupSha256", "moduleName", "targetName",
    "targetKind", "targetStartByte", "targetEndByte", "headerEndByte",
    "bodyDelimiter", "boundaryArtifactSha256", "spanId",
    "s4ContractSha256", "s4DriverSha256", "s5ContractSha256",
    "s5DriverSha256", "semanticContextBinding", "runtimeSha256",
    "optionOverrides", "baselineCertificate", "samples",
))
_UNBOUND_MANIFEST_KEYS = _MANIFEST_KEYS - {"invocationBinding"}
_RAW_MANIFEST_KEYS = _UNBOUND_MANIFEST_KEYS - {"semanticContextBinding"}
_SAMPLE_KEYS = frozenset((
    "id", "reconstructedFile", "reconstructedSha256", "retainedEndByte",
    "extractedBodySha256", "s4EvidenceSha256",
))
_OPTION_KEYS = frozenset(("name", "value"))
_CERTIFICATE_KEYS = frozenset((
    "schema", "baselineEvidenceSha256", "baselineInvocationBinding",
    "semanticContextBinding", "baselineRuntimeSha256", "nPriorCommands",
    "targetName", "targetInfoKind", "nLevelParams", "typeExpression",
    "typeExpressionSha256",
))
_PREVALIDATION_KEYS = frozenset((
    "schema", "record_type", "mode", "invocation_binding", "module_name",
    "target_name", "target_kind", "target_start_byte", "target_end_byte",
    "header_end_byte", "body_delimiter", "logical_filename",
    "original_sha256",
    "module_setup_sha256", "boundary_artifact_sha256", "span_id",
    "s4_contract_sha256", "s4_driver_sha256", "s5_contract_sha256",
    "s5_driver_sha256", "semantic_context_binding",
    "runtime_sha256", "baseline_evidence_sha256",
    "n_prior_commands",
))
_CANDIDATE_START_KEYS = frozenset((
    "schema", "record_type", "invocation_binding", "sample_id",
    "baseline_evidence_sha256",
))
_BASELINE_SUCCESS_KEYS = frozenset((
    "schema", "record_type", "status", "outcome_class", "target_name",
    "target_info_kind", "type_fingerprint", "type_expression",
    "type_kernel_equal",
    "n_level_params", "n_new_constants", "n_axioms", "forbidden_surfaces",
    "elaboration_attempted", "elaboration_succeeded",
))
_SAMPLE_SUCCESS_KEYS = _BASELINE_SUCCESS_KEYS | {"sample_id"}
_BASELINE_FAILURE_KEYS = frozenset((
    "schema", "record_type", "status", "reason", "outcome_class",
    "forbidden_surfaces", "elaboration_attempted",
    "elaboration_succeeded",
))
_SAMPLE_FAILURE_KEYS = _BASELINE_FAILURE_KEYS | {"sample_id"}


def _hex(value):
    return isinstance(value, str) and len(value) == 64 \
        and all(char in "0123456789abcdef" for char in value)


def _nonnegative(value, label):
    if type(value) is not int or value < 0:
        raise V2BError(f"{label} must be a nonnegative integer")
    return value


def _validate_type_expression(value, n_level_params):
    """Validate the exact locally owned tagged Lean Expr certificate grammar."""
    _nonnegative(n_level_params, "certificate nLevelParams")
    stack = [("expr", value)]
    nodes = 0
    max_nodes = 1_000_000
    binder_infos = frozenset((
        "default", "implicit", "strict-implicit", "instance-implicit"))

    def array(node, tag, length):
        if not isinstance(node, list) or len(node) != length \
                or node[0] != tag:
            raise V2BError(f"invalid Lean S5 certificate {tag} node")

    while stack:
        kind, node = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise V2BError("Lean S5 certificate exceeds the node cap")
        if kind == "name":
            if node == ["anonymous"]:
                continue
            if not isinstance(node, list) or len(node) != 3:
                raise V2BError("invalid Lean S5 certificate Name")
            if node[0] == "str" and isinstance(node[2], str):
                stack.append(("name", node[1]))
            elif node[0] == "num":
                _nonnegative(node[2], "certificate Name numeral")
                stack.append(("name", node[1]))
            else:
                raise V2BError("invalid Lean S5 certificate Name")
        elif kind == "level":
            if node == ["zero"]:
                continue
            if not isinstance(node, list) or not node:
                raise V2BError("invalid Lean S5 certificate Level")
            if node[0] == "succ":
                array(node, "succ", 2)
                stack.append(("level", node[1]))
            elif node[0] in ("max", "imax"):
                array(node, node[0], 3)
                stack.extend((("level", node[1]), ("level", node[2])))
            elif node[0] == "param":
                array(node, "param", 2)
                name = node[1]
                if not isinstance(name, list) or len(name) != 3 \
                        or name[0] != "num" \
                        or type(name[2]) is not int \
                        or not 0 <= name[2] < n_level_params \
                        or name[1] != [
                            "str", ["anonymous"], "v2b_universe"]:
                    raise V2BError(
                        "Lean S5 certificate universe name is noncanonical")
            else:
                raise V2BError("invalid Lean S5 certificate Level")
        elif kind == "levels":
            if not isinstance(node, list):
                raise V2BError("invalid Lean S5 certificate level array")
            stack.extend(("level", level) for level in node)
        else:
            if not isinstance(node, list) or not node \
                    or not isinstance(node[0], str):
                raise V2BError("invalid Lean S5 certificate Expr")
            tag = node[0]
            if tag == "bvar":
                array(node, tag, 2)
                _nonnegative(node[1], "certificate bound-variable index")
            elif tag == "sort":
                array(node, tag, 2)
                stack.append(("level", node[1]))
            elif tag == "const":
                array(node, tag, 3)
                stack.extend((("name", node[1]), ("levels", node[2])))
            elif tag == "app":
                array(node, tag, 3)
                stack.extend((("expr", node[1]), ("expr", node[2])))
            elif tag in ("lam", "forall"):
                array(node, tag, 4)
                if node[3] not in binder_infos:
                    raise V2BError(
                        "invalid Lean S5 certificate BinderInfo")
                stack.extend((("expr", node[1]), ("expr", node[2])))
            elif tag == "let":
                array(node, tag, 5)
                if type(node[4]) is not bool:
                    raise V2BError(
                        "invalid Lean S5 certificate let-dependency flag")
                stack.extend((
                    ("expr", node[1]), ("expr", node[2]),
                    ("expr", node[3])))
            elif tag == "lit-nat":
                array(node, tag, 2)
                _nonnegative(node[1], "certificate natural literal")
            elif tag == "lit-string":
                array(node, tag, 2)
                if not isinstance(node[1], str):
                    raise V2BError(
                        "invalid Lean S5 certificate string literal")
            elif tag == "proj":
                array(node, tag, 4)
                _nonnegative(node[2], "certificate projection index")
                stack.extend((("name", node[1]), ("expr", node[3])))
            else:
                raise V2BError("invalid Lean S5 certificate Expr tag")
    return nodes


def _validate_certificate(certificate, manifest):
    if not isinstance(certificate, dict) \
            or set(certificate) != _CERTIFICATE_KEYS \
            or certificate.get("schema") != \
            "v2b_lean_baseline_certificate_v1":
        raise V2BError("Lean S5 baseline certificate schema/key drift")
    for field in (
            "baselineEvidenceSha256", "baselineInvocationBinding",
            "semanticContextBinding", "baselineRuntimeSha256",
            "typeExpressionSha256"):
        if not _hex(certificate.get(field)):
            raise V2BError(f"Lean S5 certificate {field} is malformed")
    expected_kind = ("definition" if manifest["targetKind"] == "def"
                     else "theorem")
    if certificate.get("targetName") != manifest["targetName"] \
            or certificate.get("targetInfoKind") != expected_kind \
            or certificate.get("semanticContextBinding") != \
            manifest["semanticContextBinding"] \
            or certificate.get("baselineRuntimeSha256") != \
            manifest["runtimeSha256"]:
        raise V2BError("Lean S5 certificate semantic identity drift")
    _nonnegative(certificate.get("nPriorCommands"),
                 "certificate nPriorCommands")
    _validate_type_expression(certificate.get("typeExpression"),
                              certificate.get("nLevelParams"))
    if certificate["typeExpressionSha256"] != sha256_sorted_json(
            certificate["typeExpression"]):
        raise V2BError("Lean S5 certificate type-expression hash drift")
    return certificate


def _outcome_class(target_kind):
    return ("lean-def-typecheck" if target_kind == "def"
            else "lean-theorem-proof")


def _read_bytes(path, label):
    try:
        return open(path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read Lean S5 {label}: {err}") from err


def lean_verify_semantic_context_binding(manifest):
    """Hash the exact semantic projection shared by fresh baseline/candidate runs."""
    fields = (
        "originalFile", "logicalFileName", "originalSha256",
        "moduleSetupFile", "moduleSetupSha256", "moduleName", "targetName",
        "targetKind", "targetStartByte", "targetEndByte", "headerEndByte",
        "bodyDelimiter", "boundaryArtifactSha256", "spanId",
        "s4ContractSha256", "s4DriverSha256", "s5ContractSha256",
        "s5DriverSha256", "runtimeSha256", "optionOverrides",
    )
    if not isinstance(manifest, dict) or any(
            field not in manifest for field in fields):
        raise V2BError("Lean S5 semantic-context input is incomplete")
    return sha256_sorted_json(dict(
        schema="v2b_lean_verify_semantic_context_v1",
        **{field: copy.deepcopy(manifest[field]) for field in fields}))


def _binding_payload(manifest):
    if not isinstance(manifest, dict) or set(manifest) not in (
            _UNBOUND_MANIFEST_KEYS, _MANIFEST_KEYS):
        raise V2BError("Lean S5 manifest key drift")
    unbound = copy.deepcopy(manifest)
    unbound.pop("invocationBinding", None)
    files = [
        dict(role="original", path=unbound["originalFile"],
             sha256=sha256_file(unbound["originalFile"])),
        dict(role="module-setup", path=unbound["moduleSetupFile"],
             sha256=sha256_file(unbound["moduleSetupFile"])),
    ]
    for sample in unbound["samples"]:
        files.append(dict(
            role="reconstructed", sample_id=sample["id"],
            path=sample["reconstructedFile"],
            sha256=sha256_file(sample["reconstructedFile"])))
    return dict(schema="v2b_lean_verify_invocation_binding_v2",
                manifest=unbound, files=files)


def lean_verify_invocation_binding(manifest):
    return sha256_sorted_json(_binding_payload(manifest))


def bind_lean_verify_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) not in (
            _RAW_MANIFEST_KEYS, _UNBOUND_MANIFEST_KEYS):
        raise V2BError("unbound Lean S5 manifest key drift")
    bound = copy.deepcopy(manifest)
    semantic_context = lean_verify_semantic_context_binding(bound)
    if "semanticContextBinding" in bound \
            and bound["semanticContextBinding"] != semantic_context:
        raise V2BError("Lean S5 semantic-context binding drift")
    bound["semanticContextBinding"] = semantic_context
    bound["invocationBinding"] = lean_verify_invocation_binding(bound)
    _validate_manifest(bound)
    return bound


def validate_lean_verify_manifest(manifest):
    """Validate and return the ordered zero-or-one sample id list."""
    return _validate_manifest(manifest)


def _validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS \
            or manifest.get("schema") != LEAN_VERIFY_MANIFEST_SCHEMA:
        raise V2BError("Lean S5 manifest schema/key drift")
    for field in (
            "invocationBinding", "originalSha256", "moduleSetupSha256",
            "boundaryArtifactSha256", "spanId", "s4ContractSha256",
            "s4DriverSha256", "s5ContractSha256", "s5DriverSha256",
            "semanticContextBinding", "runtimeSha256"):
        if not _hex(manifest.get(field)):
            raise V2BError(f"Lean S5 manifest {field} is malformed")
    for field in ("originalFile", "logicalFileName", "moduleSetupFile",
                  "moduleName", "targetName"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise V2BError(f"Lean S5 manifest {field} is empty")
    if manifest["targetKind"] not in ("theorem", "lemma", "def") \
            or manifest["bodyDelimiter"] not in (":=", "where", "|"):
        raise V2BError("Lean S5 target kind/body delimiter is unsupported")
    if manifest.get("mode") not in ("baseline", "candidate"):
        raise V2BError("Lean S5 mode is unsupported")
    start = _nonnegative(manifest["targetStartByte"], "targetStartByte")
    header = _nonnegative(manifest["headerEndByte"], "headerEndByte")
    end = _nonnegative(manifest["targetEndByte"], "targetEndByte")
    if not start < header < end:
        raise V2BError("Lean S5 target byte order is invalid")
    if manifest["originalSha256"] != sha256_file(manifest["originalFile"]):
        raise V2BError("Lean S5 original source hash drift")
    if manifest["moduleSetupSha256"] != \
            sha256_file(manifest["moduleSetupFile"]):
        raise V2BError("Lean S5 ModuleSetup hash drift")
    if manifest["s4ContractSha256"] != LEAN_EXTRACTION_CONTRACT_SHA256 \
            or manifest["s4DriverSha256"] != sha256_file(LEAN_PARSE_DRIVER) \
            or manifest["s5ContractSha256"] != \
            LEAN_VERIFY_CONTRACT_SHA256 \
            or manifest["s5DriverSha256"] != sha256_file(
                LEAN_VERIFY_DRIVER):
        raise V2BError("Lean S4/S5 contract or driver drift")
    if manifest["semanticContextBinding"] != \
            lean_verify_semantic_context_binding(manifest):
        raise V2BError("Lean S5 semantic-context binding drift")
    options = manifest["optionOverrides"]
    if not isinstance(options, list):
        raise V2BError("Lean S5 optionOverrides is not a list")
    option_names = []
    for option in options:
        if not isinstance(option, dict) or set(option) != _OPTION_KEYS \
                or not isinstance(option.get("name"), str) \
                or not option["name"] \
                or not isinstance(option.get("value"), str) \
                or not option["value"] \
                or option["name"] in (
                    "Elab.async", "debug.skipKernelTC",
                    "debug.proofAsSorry"):
            raise V2BError("Lean S5 option override is malformed")
        option_names.append(option["name"])
    if len(option_names) != len(set(option_names)):
        raise V2BError("Lean S5 option overrides are duplicated")
    samples = manifest["samples"]
    certificate = manifest["baselineCertificate"]
    if manifest["mode"] == "baseline":
        if certificate is not None or samples != []:
            raise V2BError(
                "Lean S5 baseline mode requires no certificate/sample")
    elif not isinstance(samples, list) or len(samples) != 1 \
            or certificate is None:
        raise V2BError(
            "Lean S5 candidate mode requires one certificate/sample")
    if not isinstance(samples, list):
        raise V2BError("Lean S5 samples is not a list")
    original_blob = _read_bytes(manifest["originalFile"], "original")
    ids = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != _SAMPLE_KEYS \
                or not isinstance(sample.get("id"), str) or not sample["id"] \
                or not isinstance(sample.get("reconstructedFile"), str) \
                or not sample["reconstructedFile"]:
            raise V2BError("Lean S5 sample is malformed")
        retained = _nonnegative(sample["retainedEndByte"],
                                "retainedEndByte")
        if retained <= header:
            raise V2BError("Lean S5 retained body is empty")
        for field in ("reconstructedSha256", "extractedBodySha256",
                      "s4EvidenceSha256"):
            if not _hex(sample.get(field)):
                raise V2BError(f"Lean S5 sample {field} is malformed")
        if sample["reconstructedSha256"] != \
                sha256_file(sample["reconstructedFile"]):
            raise V2BError("Lean S5 reconstructed source hash drift")
        reconstructed_blob = _read_bytes(sample["reconstructedFile"],
                                         "reconstruction")
        if retained > len(reconstructed_blob) \
                or reconstructed_blob[:header] != original_blob[:header] \
                or reconstructed_blob[retained:] != original_blob[end:] \
                or sample["extractedBodySha256"] != sha256_bytes(
                    reconstructed_blob[header:retained]):
            raise V2BError("Lean S5 reconstructed splice/body binding drift")
        ids.append(sample["id"])
    if len(ids) != len(set(ids)):
        raise V2BError("Lean S5 sample ids are duplicated")
    if manifest["mode"] == "candidate":
        _validate_certificate(certificate, manifest)
    if manifest["invocationBinding"] != \
            lean_verify_invocation_binding(manifest):
        raise V2BError("Lean S5 invocation/content binding drift")
    return ids


def _json_no_duplicates(text):
    def object_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(f"duplicate Lean S5 JSON key {key!r}")
            value[key] = item
        return value

    def reject_nonfinite(value):
        raise V2BError(f"non-finite Lean S5 number {value}")

    try:
        return json.loads(text, object_pairs_hook=object_hook,
                          parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, UnicodeError, V2BError) as err:
        raise V2BError(f"malformed marked Lean S5 record: {err}") from err


def _validate_result_record(row, record_type, target_kind, target_name,
                            sample_id=None):
    if not isinstance(row, dict) \
            or row.get("schema") != LEAN_VERIFY_OUTPUT_SCHEMA \
            or row.get("record_type") != record_type:
        raise V2BError("Lean S5 result schema/record type drift")
    if row.get("status") not in ("verified", "verification-failure"):
        raise V2BError("Lean S5 result status is unfrozen")
    success = row["status"] == "verified"
    expected_keys = (
        _SAMPLE_SUCCESS_KEYS if success and sample_id is not None else
        _BASELINE_SUCCESS_KEYS if success else
        _SAMPLE_FAILURE_KEYS if sample_id is not None else
        _BASELINE_FAILURE_KEYS)
    if set(row) != expected_keys:
        raise V2BError("Lean S5 result key drift")
    if sample_id is not None and row.get("sample_id") != sample_id:
        raise V2BError("Lean S5 sample membership/order drift")
    if row.get("outcome_class") != _outcome_class(target_kind) \
            or type(row.get("elaboration_attempted")) is not bool \
            or type(row.get("elaboration_succeeded")) is not bool:
        raise V2BError("Lean S5 outcome class/elaboration flags drift")
    if success:
        if not row["elaboration_attempted"] \
                or not row["elaboration_succeeded"] \
                or not isinstance(row.get("target_name"), str) \
                or row["target_name"] != target_name \
                or row.get("target_info_kind") != (
                    "definition" if target_kind == "def" else "theorem") \
                or not isinstance(row.get("type_fingerprint"), str) \
                or not row["type_fingerprint"] \
                or type(row.get("n_level_params")) is not int \
                or row["n_level_params"] < 0 \
                or type(row.get("n_new_constants")) is not int \
                or row["n_new_constants"] < 1 \
                or type(row.get("n_axioms")) is not int \
                or row["n_axioms"] < 0 \
                or row.get("forbidden_surfaces") != []:
            raise V2BError("Lean S5 verified-row invariant drift")
        _validate_type_expression(row.get("type_expression"),
                                  row["n_level_params"])
        expected_type_flag = True if sample_id is not None else None
        if row.get("type_kernel_equal") is not expected_type_flag:
            raise V2BError("Lean S5 type-equality flag drift")
    else:
        reason = row.get("reason")
        if reason not in LEAN_VERIFY_FAILURE_REASONS:
            raise V2BError("Lean S5 failure reason is unfrozen")
        expected_forbidden = [reason] if reason in \
            FORBIDDEN_FAILURE_REASONS else []
        if row.get("forbidden_surfaces") != expected_forbidden:
            raise V2BError("Lean S5 forbidden-surface truth table drift")
        if not row["elaboration_attempted"]:
            if reason not in FORBIDDEN_FAILURE_REASONS \
                    or row["elaboration_succeeded"]:
                raise V2BError(
                    "Lean S5 pre-elaboration rejection truth table drift")
        else:
            expected_succeeded = reason not in \
                TARGET_ELABORATION_FAILED_REASONS
            if row["elaboration_succeeded"] is not expected_succeeded:
                raise V2BError(
                    "Lean S5 elaboration-success truth table drift")


def _marked_records(stdout):
    if not isinstance(stdout, str):
        raise V2BError("Lean S5 stdout must be text")
    records = []
    for line in stdout.splitlines():
        if line.startswith(LEAN_VERIFY_OUTPUT_MARKER):
            value = _json_no_duplicates(
                line[len(LEAN_VERIFY_OUTPUT_MARKER):])
            if not isinstance(value, dict):
                raise V2BError("marked Lean S5 record is not an object")
            records.append(value)
    return records


def _validate_prevalidation(prevalidation, manifest):
    if not isinstance(prevalidation, dict) \
            or set(prevalidation) != _PREVALIDATION_KEYS \
            or prevalidation.get("schema") != LEAN_VERIFY_OUTPUT_SCHEMA \
            or prevalidation.get("record_type") != "prevalidation":
        raise V2BError("Lean S5 prevalidation schema/key drift")
    expected_prevalidation = dict(
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
        baseline_evidence_sha256=(
            manifest["baselineCertificate"]["baselineEvidenceSha256"]
            if manifest["mode"] == "candidate" else None),
    )
    for key, value in expected_prevalidation.items():
        if prevalidation.get(key) != value:
            raise V2BError(f"Lean S5 prevalidation {key} drift")
    _nonnegative(prevalidation.get("n_prior_commands"), "n_prior_commands")
    return prevalidation


def _validate_candidate_start(row, manifest):
    certificate = manifest["baselineCertificate"]
    sample_id = manifest["samples"][0]["id"]
    if not isinstance(row, dict) or set(row) != _CANDIDATE_START_KEYS \
            or row.get("schema") != LEAN_VERIFY_OUTPUT_SCHEMA \
            or row.get("record_type") != "candidate-start" \
            or row.get("invocation_binding") != \
            manifest["invocationBinding"] \
            or row.get("sample_id") != sample_id \
            or row.get("baseline_evidence_sha256") != \
            certificate["baselineEvidenceSha256"]:
        raise V2BError("Lean S5 candidate-start marker drift")
    return row


def lean_baseline_certificate(parsed, manifest, baseline_evidence_sha256):
    """Derive a candidate certificate from a complete verified baseline run.

    ``baseline_evidence_sha256`` must name the immutable, exit-0 production
    execution-evidence object that binds the raw transcript; the production
    wrapper revalidates that object before candidate reuse.
    """
    _validate_manifest(manifest)
    if manifest["mode"] != "baseline" or not _hex(
            baseline_evidence_sha256):
        raise V2BError("Lean S5 baseline certificate input is malformed")
    if not isinstance(parsed, dict) or set(parsed) != {
            "prevalidation", "baseline", "samples"} \
            or parsed.get("samples") != []:
        raise V2BError("Lean S5 baseline parsed evidence is malformed")
    _validate_prevalidation(parsed["prevalidation"], manifest)
    baseline = parsed["baseline"]
    _validate_result_record(baseline, "baseline", manifest["targetKind"],
                            manifest["targetName"])
    if baseline["status"] != "verified":
        raise V2BError("Lean S5 cannot certify a failed baseline")
    expression = copy.deepcopy(baseline["type_expression"])
    return dict(
        schema="v2b_lean_baseline_certificate_v1",
        semanticContextBinding=manifest["semanticContextBinding"],
        baselineInvocationBinding=manifest["invocationBinding"],
        baselineEvidenceSha256=baseline_evidence_sha256,
        baselineRuntimeSha256=manifest["runtimeSha256"],
        nPriorCommands=parsed["prevalidation"]["n_prior_commands"],
        targetName=baseline["target_name"],
        targetInfoKind=baseline["target_info_kind"],
        nLevelParams=baseline["n_level_params"],
        typeExpression=expression,
        typeExpressionSha256=sha256_sorted_json(expression),
    )


def parse_lean_verify_prefix(stdout, manifest):
    """Validate a flushed marker prefix from a possibly timed-out process."""
    expected_ids = _validate_manifest(manifest)
    records = _marked_records(stdout)
    expected_count = 2 if manifest["mode"] == "baseline" else 3
    if len(records) > expected_count:
        raise V2BError("Lean S5 marked record count exceeds manifest")
    if not records:
        return dict(stage="before-prevalidation", prevalidation=None,
                    baseline=None, samples=[])
    prevalidation = _validate_prevalidation(records[0], manifest)
    if len(records) == 1:
        return dict(stage="prevalidated", prevalidation=prevalidation,
                    baseline=None, samples=[])
    if manifest["mode"] == "baseline":
        baseline = records[1]
        _validate_result_record(baseline, "baseline", manifest["targetKind"],
                                manifest["targetName"])
        return dict(stage="complete", prevalidation=prevalidation,
                    baseline=baseline, samples=[])
    candidate_start = _validate_candidate_start(records[1], manifest)
    if len(records) == 2:
        return dict(stage="candidate-started", prevalidation=prevalidation,
                    candidate_start=candidate_start, baseline=None, samples=[])
    sample_id = expected_ids[0]
    sample = records[2]
    _validate_result_record(sample, "sample", manifest["targetKind"],
                            manifest["targetName"], sample_id)
    certificate = manifest["baselineCertificate"]
    if sample["status"] == "verified" and (
            sample["target_info_kind"] != certificate["targetInfoKind"]
            or sample["n_level_params"] != certificate["nLevelParams"]):
        raise V2BError("Lean S5 certificate/sample identity drift")
    return dict(stage="complete", prevalidation=prevalidation,
                candidate_start=candidate_start, baseline=None,
                samples=[sample])


def parse_lean_verify_stdout(stdout, manifest):
    """Validate one complete manifest-bound marker transcript."""
    parsed = parse_lean_verify_prefix(stdout, manifest)
    if parsed["stage"] != "complete":
        raise V2BError("Lean S5 marked record count does not match manifest")
    return {key: parsed[key] for key in
            ("prevalidation", "baseline", "samples")}


__all__ = [
    "BOUNDARIES_SCHEMA", "LEAN_DRIVER_MANIFEST_SCHEMA",
    "LEAN_VERIFY_CONTRACT", "LEAN_VERIFY_CONTRACT_SHA256",
    "LEAN_VERIFY_DRIVER", "LEAN_VERIFY_FAILURE_REASONS",
    "LEAN_VERIFY_MANIFEST_SCHEMA", "LEAN_VERIFY_OUTPUT_MARKER",
    "LEAN_VERIFY_OUTPUT_SCHEMA", "bind_lean_verify_manifest",
    "lean_baseline_certificate", "lean_verify_invocation_binding",
    "lean_verify_semantic_context_binding", "parse_lean_verify_prefix",
    "parse_lean_verify_stdout", "validate_lean_verify_manifest",
]
