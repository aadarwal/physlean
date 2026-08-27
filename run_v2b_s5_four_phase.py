#!/usr/bin/env python3
"""Four-fresh-process execution envelope for the oracle-safe Lean S5 probe.

This module is deliberately narrower than a corpus/runtime producer.  It owns
the outcome-bearing boundary after a row-specific visibility projection has
already been constructed:

* source bytes remain in the parent and cross into a child only as framed
  stdin payloads;
* the four phases are fresh processes in the fixed order baseline-target,
  baseline-suffix, candidate-target, candidate-suffix;
* a nonce-authenticated ``phase-start`` is durably journalled before GO;
* no attempt that reached durable GO intent is ever retried; and
* every completed attempt is revalidated byte-for-byte before reuse.

The visibility projection is a required, strict artifact.  Production accepts
only its bubblewrap backend.  The ``none-test-only`` backend exists solely for
real-Lean integration tests and is rejected by default.  This file does not
discover imports, broaden visibility, generate candidates, or write behavioral
outcomes.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import resource
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from v2b_common import (V2BError, canonical_json_bytes, sha256_bytes,
                        sha256_file, sha256_sorted_json)
from v2b_lean_frames import (SUFFIX_FRAME_ROLES, TARGET_FRAME_ROLES,
                             build_views, channel_payload, frame_digests)
from v2b_s5_visibility import (VISIBILITY_SCHEMA,
                               validate_visibility as validate_visibility_artifact)


PLAN_SCHEMA = "v2b_s5_four_phase_plan_v1"
LEAN_MANIFEST_SCHEMA = "v2b_lean_oracle_probe_manifest_v2"
LEAN_OUTPUT_SCHEMA = "v2b_lean_oracle_probe_result_v2"
LEAN_BUNDLE_SCHEMA = "v2b_lean_constant_bundle_v1"
RUN_SUMMARY_SCHEMA = "v2b_s5_four_phase_summary_v1"
ATTEMPT_OPEN_SCHEMA = "v2b_s5_four_phase_attempt_open_v1"
GO_INTENT_SCHEMA = "v2b_s5_four_phase_go_intent_v1"
GO_ACCEPTED_SCHEMA = "v2b_s5_four_phase_go_accepted_v1"
ATTEMPT_TERMINAL_SCHEMA = "v2b_s5_four_phase_attempt_terminal_v1"

PHASES = (
    "baseline-target",
    "baseline-suffix",
    "candidate-target",
    "candidate-suffix",
)
TARGET_PHASES = frozenset(("baseline-target", "candidate-target"))
SUFFIX_PHASES = frozenset(("baseline-suffix", "candidate-suffix"))
MARKER_PREFIX = "@@V2B_ORACLE_PROBE:"
MARKER_SUFFIX = "@@"
LEAN_DRIVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lean_drivers",
    "V2BOracleSafeProbe.lean")
RUNNER_PATH = os.path.abspath(__file__)
PYTHON_SOURCE_FILES = tuple(os.path.join(
    os.path.dirname(RUNNER_PATH), name) for name in (
        "run_v2b_s5_four_phase.py", "v2b_common.py",
        "v2b_lean_frames.py", "v2b_s5_visibility.py",
        "prepare_v2b_lean_setups.py", "provenance.py", "v2b_neardup.py"))

# These are engineering caps, not learned from model outcomes.  Lean repeats
# the semantic/kernel checks; this host grammar bounds allocation before a
# target bundle is sent to a suffix process.
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_CONSTANTS = 4096
MAX_BUNDLE_NODES = 2_000_000
MAX_BUNDLE_DEPTH = 4096
MAX_PRESTART_ATTEMPTS = 2
CONTROL_HEADROOM_BYTES = 64 * 1024

RESOURCE_LIMITS = dict(
    timeout_seconds=300,
    address_space_bytes=64 * 1024**3,
    cpu_seconds=305,
    n_processes=64,
    n_open_files=256,
    file_size_bytes=32 * 1024**2,
    core_size_bytes=0,
    stdout_bytes=16 * 1024**2,
    stderr_bytes=8 * 1024**2,
)

SANDBOX_CONTRACT = dict(
    schema="v2b_s5_four_phase_bwrap_contract_v1",
    backend="bubblewrap",
    namespaces=["user", "pid", "network", "ipc", "uts", "cgroup"],
    lifecycle=["die-with-parent", "new-session"],
    network=False,
    proc=False,
    sys=False,
    capabilities="drop-all",
    root="empty",
    writable=["private tmpfs /tmp"],
    devices="private minimal /dev mount, remounted read-only",
    inputs=("one hash-only phase manifest, the bound driver, exact "
            "visibility-artifact toolchain/import/runtime files, and "
            "nonce-framed stdin"),
    forbidden=("corpus root, original/reconstructed source files, current "
               "module artifacts, search-root contents, private attempt "
               "journal, and any prior/future phase manifest"),
)
SANDBOX_CONTRACT_SHA256 = sha256_sorted_json(SANDBOX_CONTRACT)

FOUR_PHASE_CONTRACT = dict(
    schema="v2b_s5_four_phase_execution_contract_v1",
    order=list(PHASES),
    target_frames=list(TARGET_FRAME_ROLES),
    suffix_frames=list(SUFFIX_FRAME_ROLES),
    transport="nonce, frames, ENDFRAMES; durable GO intent; GO:nonce; EOF",
    source_residency=("candidate target receives trusted prefix/header and "
                      "candidate bytes only through retainedEnd; candidate "
                      "suffix receives masked-body suffix view plus canonical "
                      "kernel bundle, never candidate syntax or runtime IR"),
    retry=("at most two pre-GO attempts; no retry after durable GO intent; "
           "candidate termination after GO is an immutable zero"),
    resource_limits=dict(RESOURCE_LIMITS),
    max_prestart_attempts=MAX_PRESTART_ATTEMPTS,
    control_headroom_bytes=CONTROL_HEADROOM_BYTES,
    bundle_caps=dict(bytes=MAX_BUNDLE_BYTES,
                     constants=MAX_BUNDLE_CONSTANTS,
                     nodes=MAX_BUNDLE_NODES,
                     depth=MAX_BUNDLE_DEPTH),
    sandbox_contract_sha256=SANDBOX_CONTRACT_SHA256,
)
FOUR_PHASE_CONTRACT_SHA256 = sha256_sorted_json(FOUR_PHASE_CONTRACT)


_PLAN_KEYS = frozenset((
    "schema", "invocationBinding", "logicalFile", "targetName",
    "targetKind", "moduleName", "targetStartByte", "headerEndByte",
    "baselineRetainedEndByte", "candidateRetainedEndByte",
    "prefixSha256", "headerSha256", "originalModuleSha256",
    "originalBodySha256", "immutableSuffixSha256",
    "candidateModuleSha256", "candidateBodySha256", "visibilityBinding",
    "driverSha256", "executionBinding", "contractSha256",
))
_UNBOUND_PLAN_KEYS = _PLAN_KEYS - {"invocationBinding"}
_LEAN_MANIFEST_KEYS = frozenset((
    "schema", "mode", "logicalFile", "targetName", "targetKind",
    "targetStartByte", "headerEndByte", "retainedEndByte",
))

_ATTEMPT_OPEN_KEYS = frozenset((
    "schema", "attemptId", "attemptOrdinal", "invocationBinding", "phase",
    "nonceSha256", "planSha256", "visibilitySha256", "frameDigests",
    "frameBytes", "manifestSha256", "backend", "openedWallTimeNs",
))
_GO_INTENT_KEYS = frozenset((
    "schema", "attemptId", "invocationBinding", "phase", "nonceSha256",
    "authenticatedStage", "stdoutPrefixSha256", "stdoutPrefixBytes",
    "committedWallTimeNs",
))
_GO_ACCEPTED_KEYS = frozenset((
    "schema", "attemptId", "invocationBinding", "phase",
    "authenticatedStage", "stdoutPrefixSha256", "stdoutPrefixBytes",
    "observedWallTimeNs",
))
_ATTEMPT_TERMINAL_KEYS = frozenset((
    "schema", "attemptId", "invocationBinding", "phase", "nonce",
    "nonceSha256", "manifestSha256", "planSha256", "visibilitySha256",
    "frameDigests", "frameBytes", "backend", "argvSha256", "pid",
    "startedWallTimeNs", "endedWallTimeNs", "wallTimeNs", "returncode",
    "timedOut", "outputLimited", "stdoutSha256", "stdoutBytes",
    "stderrSha256", "stderrBytes", "goIntentSha256", "goAcceptedSha256",
    "attemptOpenSha256",
    "authenticatedStage", "protocolValid", "protocolErrorSha256",
    "classification", "outcomeBearing",
))
_SUMMARY_KEYS = frozenset((
    "schema", "invocationBinding", "planSha256", "visibilitySha256",
    "contractSha256", "classification", "pass", "completedPhases",
    "phaseEvidenceSha256", "baselineBundleSha256",
    "candidateBundleSha256", "baselineTargetCertificateSha256",
    "candidateTargetCertificateSha256",
))


def _hex(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


def _nat(value, label, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise V2BError(f"{label} must be a {qualifier} integer")
    return value


def _module_name(value):
    return (isinstance(value, str) and bool(value)
            and all(part and part not in (".", "..")
                    and "/" not in part and "\\" not in part
                    and "\x00" not in part for part in value.split(".")))


def _strict_json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode(
                              "utf-8")
    except (TypeError, ValueError) as err:
        raise V2BError(f"cannot encode strict S5 JSON: {err}") from err


def _loads_strict(blob, where):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise V2BError(f"{where}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def no_nonfinite(value):
        raise V2BError(f"{where}: non-finite JSON number {value}")

    try:
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8", errors="strict")
        return json.loads(blob, object_pairs_hook=no_duplicates,
                          parse_constant=no_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError,
            V2BError) as err:
        if isinstance(err, V2BError):
            raise
        raise V2BError(f"{where}: malformed strict JSON: {err}") from err


def _fsync_directory(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as err:
        raise V2BError(f"cannot fsync S5 directory {path}: {err}") from err


def _write_new_bytes(path, blob):
    if not isinstance(blob, bytes):
        raise V2BError("immutable S5 writer requires bytes")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(blob)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short immutable write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(parent)
    except OSError as err:
        raise V2BError(f"cannot write immutable S5 file {path}: {err}") \
            from err


def _write_new_json(path, value):
    if not isinstance(value, dict):
        raise V2BError("immutable S5 JSON root must be an object")
    blob = _strict_json_bytes(value) + b"\n"
    _write_new_bytes(path, blob)
    return sha256_bytes(blob)


def _read_strict_json(path, schema=None, keys=None):
    try:
        blob = open(path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read S5 artifact {path}: {err}") from err
    value = _loads_strict(blob, path)
    if not isinstance(value, dict):
        raise V2BError(f"{path}: JSON root is not an object")
    if schema is not None and value.get("schema") != schema:
        raise V2BError(f"{path}: schema drift")
    if keys is not None and set(value) != set(keys):
        raise V2BError(f"{path}: exact key drift")
    return value, sha256_bytes(blob)


def _utf8(blob, label):
    if not isinstance(blob, bytes):
        raise V2BError(f"{label} must be raw UTF-8 bytes")
    try:
        text = blob.decode("utf-8", errors="strict")
    except UnicodeError as err:
        raise V2BError(f"{label} is not strict UTF-8: {err}") from err
    if text.encode("utf-8") != blob:
        raise V2BError(f"{label} does not round-trip UTF-8")
    return text


def _plan_payload(raw):
    if not isinstance(raw, dict) or set(raw) not in (
            _UNBOUND_PLAN_KEYS, _PLAN_KEYS):
        raise V2BError("S5 four-phase plan key drift")
    value = copy.deepcopy(raw)
    value.pop("invocationBinding", None)
    return value


def bind_plan(raw):
    value = _plan_payload(raw)
    supplied = raw.get("invocationBinding")
    binding = sha256_sorted_json(value)
    if supplied is not None and supplied != binding:
        raise V2BError("S5 four-phase invocation binding drift")
    value["invocationBinding"] = binding
    validate_plan(value)
    return value


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS \
            or plan.get("schema") != PLAN_SCHEMA:
        raise V2BError("S5 four-phase plan schema/key drift")
    if plan.get("invocationBinding") != sha256_sorted_json(
            _plan_payload(plan)):
        raise V2BError("S5 four-phase plan binding drift")
    for field in (
            "prefixSha256", "headerSha256", "originalModuleSha256",
            "originalBodySha256", "immutableSuffixSha256",
            "candidateModuleSha256", "candidateBodySha256",
            "visibilityBinding", "driverSha256", "executionBinding",
            "contractSha256"):
        if not _hex(plan.get(field)):
            raise V2BError(f"S5 plan {field} is malformed")
    if plan["contractSha256"] != FOUR_PHASE_CONTRACT_SHA256:
        raise V2BError("S5 four-phase contract drift")
    if not isinstance(plan.get("logicalFile"), str) \
            or not os.path.isabs(plan["logicalFile"]) \
            or not _module_name(plan.get("targetName")) \
            or not _module_name(plan.get("moduleName")) \
            or plan.get("targetKind") not in ("def", "theorem", "lemma"):
        raise V2BError("S5 plan target identity drift")
    start = _nat(plan.get("targetStartByte"), "targetStartByte")
    header = _nat(plan.get("headerEndByte"), "headerEndByte")
    baseline_end = _nat(plan.get("baselineRetainedEndByte"),
                        "baselineRetainedEndByte")
    candidate_end = _nat(plan.get("candidateRetainedEndByte"),
                         "candidateRetainedEndByte")
    if not start < header < baseline_end or not header < candidate_end:
        raise V2BError("S5 plan offsets are not strictly ordered")
    return plan


def build_plan(original, candidate, *, logical_file, target_name,
               target_kind, target_start, header_end,
               baseline_retained_end, candidate_retained_end,
               visibility, driver_sha256, allow_unisolated_test=False):
    """Construct the hash-only plan after proving exact source decomposition."""
    original_text = _utf8(original, "original module")
    candidate_text = _utf8(candidate, "candidate reconstruction")
    validate_visibility_artifact(visibility, live_files=False)
    original_blob = original_text.encode("utf-8")
    candidate_blob = candidate_text.encode("utf-8")
    if not 0 <= target_start < header_end < baseline_retained_end \
            <= len(original_blob) \
            or not header_end < candidate_retained_end <= len(candidate_blob):
        raise V2BError("S5 plan source offsets are out of range")
    for label, blob, offset in (
            ("target start", original_blob, target_start),
            ("header end", original_blob, header_end),
            ("baseline retained end", original_blob, baseline_retained_end),
            ("candidate target start", candidate_blob, target_start),
            ("candidate header end", candidate_blob, header_end),
            ("candidate retained end", candidate_blob,
             candidate_retained_end)):
        try:
            blob[:offset].decode("utf-8", errors="strict")
        except UnicodeError as err:
            raise V2BError(f"S5 {label} splits UTF-8: {err}") from err
    prefix = original_blob[:target_start]
    header = original_blob[:header_end]
    immutable_suffix = original_blob[baseline_retained_end:]
    if candidate_blob[:header_end] != header:
        raise V2BError("candidate does not preserve the exact trusted header")
    if candidate_blob[candidate_retained_end:] != immutable_suffix:
        raise V2BError("candidate does not append the exact immutable suffix")
    if driver_sha256 != sha256_file(LEAN_DRIVER):
        raise V2BError("S5 plan driver is not the canonical oracle driver")
    if visibility["source"]["sha256"] != sha256_bytes(original_blob) \
            or logical_file != visibility["source"]["path"]:
        raise V2BError("S5 plan original/logical source disagrees with visibility")
    raw = dict(
        schema=PLAN_SCHEMA, logicalFile=logical_file,
        moduleName=visibility["module"], targetName=target_name,
        targetKind=target_kind,
        targetStartByte=target_start, headerEndByte=header_end,
        baselineRetainedEndByte=baseline_retained_end,
        candidateRetainedEndByte=candidate_retained_end,
        prefixSha256=sha256_bytes(prefix),
        headerSha256=sha256_bytes(header),
        originalModuleSha256=sha256_bytes(original_blob),
        originalBodySha256=sha256_bytes(
            original_blob[header_end:baseline_retained_end]),
        immutableSuffixSha256=sha256_bytes(immutable_suffix),
        candidateModuleSha256=sha256_bytes(candidate_blob),
        candidateBodySha256=sha256_bytes(
            candidate_blob[header_end:candidate_retained_end]),
        visibilityBinding=visibility["contract_sha256"],
        driverSha256=driver_sha256,
        executionBinding=execution_binding(
            visibility, allow_unisolated_test=allow_unisolated_test),
        contractSha256=FOUR_PHASE_CONTRACT_SHA256)
    return bind_plan(raw)


def validate_plan_sources(plan, visibility, original, candidate, *,
                          allow_unisolated_test=False):
    validate_plan(plan)
    validate_visibility_artifact(visibility, live_files=False)
    if plan["visibilityBinding"] != visibility["contract_sha256"] \
            or plan["logicalFile"] != visibility["source"]["path"] \
            or plan["moduleName"] != visibility["module"]:
        raise V2BError("S5 plan/visibility join drift")
    rebuilt = build_plan(
        original, candidate, logical_file=plan["logicalFile"],
        target_name=plan["targetName"], target_kind=plan["targetKind"],
        target_start=plan["targetStartByte"],
        header_end=plan["headerEndByte"],
        baseline_retained_end=plan["baselineRetainedEndByte"],
        candidate_retained_end=plan["candidateRetainedEndByte"],
        visibility=visibility, driver_sha256=plan["driverSha256"],
        allow_unisolated_test=allow_unisolated_test)
    if rebuilt != plan:
        raise V2BError("S5 plan/source byte binding drift")
    return plan


def phase_manifest(plan, phase):
    if phase not in PHASES:
        raise V2BError(f"unknown S5 phase {phase}")
    retained = (plan["baselineRetainedEndByte"]
                if phase.startswith("baseline-")
                else plan["candidateRetainedEndByte"])
    value = dict(
        schema=LEAN_MANIFEST_SCHEMA,
        mode="target" if phase in TARGET_PHASES else "suffix",
        logicalFile=plan["logicalFile"], targetName=plan["targetName"],
        targetKind=plan["targetKind"],
        targetStartByte=plan["targetStartByte"],
        headerEndByte=plan["headerEndByte"], retainedEndByte=retained)
    validate_phase_manifest(value)
    return value


def validate_phase_manifest(value):
    if not isinstance(value, dict) or set(value) != _LEAN_MANIFEST_KEYS \
            or value.get("schema") != LEAN_MANIFEST_SCHEMA \
            or value.get("mode") not in ("target", "suffix") \
            or value.get("targetKind") not in ("def", "theorem", "lemma"):
        raise V2BError("S5 Lean phase manifest schema/key drift")
    start = _nat(value.get("targetStartByte"), "phase targetStartByte")
    header = _nat(value.get("headerEndByte"), "phase headerEndByte")
    retained = _nat(value.get("retainedEndByte"), "phase retainedEndByte")
    if not start < header < retained:
        raise V2BError("S5 Lean phase manifest offset drift")
    if not isinstance(value.get("logicalFile"), str) \
            or not os.path.isabs(value["logicalFile"]) \
            or not _module_name(value.get("targetName")):
        raise V2BError("S5 Lean phase manifest identity drift")
    return value


def _phase_sources(plan, original, candidate, phase, bundle=None):
    original_text = _utf8(original, "original module")
    candidate_text = _utf8(candidate, "candidate reconstruction")
    baseline_views = build_views(
        original_text, plan["targetStartByte"], plan["headerEndByte"],
        plan["baselineRetainedEndByte"])
    candidate_views = build_views(
        candidate_text, plan["targetStartByte"], plan["headerEndByte"],
        plan["candidateRetainedEndByte"])
    views = baseline_views if phase.startswith("baseline-") else candidate_views
    if phase in TARGET_PHASES:
        sources = {role: views[role].encode("utf-8")
                   for role in TARGET_FRAME_ROLES}
    else:
        if bundle is None:
            raise V2BError(f"S5 suffix phase {phase} requires a bundle")
        validate_bundle(bundle, plan["targetName"])
        bundle_blob = canonical_json_bytes(bundle)
        sources = {
            "prefix": views["prefix"].encode("utf-8"),
            "header": views["header"].encode("utf-8"),
            "suffix": views["suffix"].encode("utf-8"),
            "bundle": bundle_blob,
        }
    roles = TARGET_FRAME_ROLES if phase in TARGET_PHASES \
        else SUFFIX_FRAME_ROLES
    if tuple(sources) != tuple(roles):
        raise AssertionError("internal S5 frame order drift")
    # Candidate target is intentionally truncated.  Candidate suffix includes
    # only its masked body; the generated body text is absent byte-for-byte.
    if phase == "candidate-target" \
            and len(sources["target"]) != plan["candidateRetainedEndByte"]:
        raise AssertionError("candidate target frame is not truncated")
    return sources


def _name(value, label):
    if not isinstance(value, list):
        raise V2BError(f"S5 bundle {label} Name is not an array")
    for part in value:
        if not isinstance(part, list) or len(part) != 2 \
                or part[0] not in ("s", "n"):
            raise V2BError(f"S5 bundle {label} Name component drift")
        if part[0] == "s" and (not isinstance(part[1], str) or not part[1]):
            raise V2BError(f"S5 bundle {label} Name string drift")
        if part[0] == "n":
            _nat(part[1], f"S5 bundle {label} Name numeral")
    return tuple((part[0], part[1]) for part in value)


def _validate_bundle_tree(root):
    stack = [("expr", root, 1)]
    nodes = 0
    while stack:
        kind, value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_BUNDLE_NODES:
            raise V2BError("S5 bundle exceeds the node cap")
        if depth > MAX_BUNDLE_DEPTH:
            raise V2BError("S5 bundle exceeds the depth cap")
        if kind == "name":
            _name(value, "expression")
            continue
        if kind == "levels":
            if not isinstance(value, list):
                raise V2BError("S5 bundle level vector drift")
            stack.extend(("level", item, depth + 1) for item in value)
            continue
        if kind == "level":
            if not isinstance(value, list) or not value \
                    or not isinstance(value[0], str):
                raise V2BError("S5 bundle Level drift")
            tag = value[0]
            if tag == "zero" and len(value) == 1:
                continue
            if tag == "succ" and len(value) == 2:
                stack.append(("level", value[1], depth + 1))
                continue
            if tag in ("max", "imax") and len(value) == 3:
                stack.extend((
                    ("level", value[1], depth + 1),
                    ("level", value[2], depth + 1)))
                continue
            if tag == "param" and len(value) == 2:
                stack.append(("name", value[1], depth + 1))
                continue
            raise V2BError("S5 bundle Level tag/arity drift")
        if not isinstance(value, list) or not value \
                or not isinstance(value[0], str):
            raise V2BError("S5 bundle Expr drift")
        tag = value[0]
        if tag == "bvar" and len(value) == 2:
            _nat(value[1], "S5 bundle bound variable")
        elif tag == "sort" and len(value) == 2:
            stack.append(("level", value[1], depth + 1))
        elif tag == "const" and len(value) == 3:
            stack.extend((
                ("name", value[1], depth + 1),
                ("levels", value[2], depth + 1)))
        elif tag == "app" and len(value) == 3:
            stack.extend((
                ("expr", value[1], depth + 1),
                ("expr", value[2], depth + 1)))
        elif tag in ("lam", "forall") and len(value) == 5:
            stack.extend((
                ("name", value[1], depth + 1),
                ("expr", value[2], depth + 1),
                ("expr", value[3], depth + 1)))
            _nat(value[4], "S5 bundle BinderInfo")
            if value[4] > 3:
                raise V2BError("S5 bundle BinderInfo drift")
        elif tag == "let" and len(value) == 6:
            stack.extend((
                ("name", value[1], depth + 1),
                ("expr", value[2], depth + 1),
                ("expr", value[3], depth + 1),
                ("expr", value[4], depth + 1)))
            if type(value[5]) is not bool:
                raise V2BError("S5 bundle let dependency flag drift")
        elif tag == "lit" and len(value) == 3 and value[1] == "nat":
            _nat(value[2], "S5 bundle natural literal")
        elif tag == "lit" and len(value) == 3 and value[1] == "str" \
                and isinstance(value[2], str):
            pass
        elif tag == "proj" and len(value) == 4:
            stack.extend((
                ("name", value[1], depth + 1),
                ("expr", value[3], depth + 1)))
            _nat(value[2], "S5 bundle projection index")
        else:
            raise V2BError(f"S5 bundle Expr tag/arity drift: {tag}")
    return nodes


def validate_bundle(bundle, target_name):
    try:
        blob = canonical_json_bytes(bundle)
    except (RecursionError, V2BError) as err:
        raise V2BError(f"S5 bundle is not bounded canonical JSON: {err}") \
            from err
    if len(blob) > MAX_BUNDLE_BYTES:
        raise V2BError("S5 bundle exceeds the byte cap")
    if not isinstance(bundle, list) or len(bundle) != 3 \
            or bundle[0] != LEAN_BUNDLE_SCHEMA \
            or bundle[1] != target_name \
            or not isinstance(bundle[2], list) \
            or not 0 < len(bundle[2]) <= MAX_BUNDLE_CONSTANTS:
        raise V2BError("S5 bundle top-level schema/target/count drift")
    seen = set()
    target_rows = []
    nodes = 0
    for row in bundle[2]:
        if not isinstance(row, list) or not row \
                or row[0] not in ("defn", "thm", "opaque"):
            raise V2BError("S5 bundle constant tag drift")
        expected = 6 if row[0] == "defn" else 5
        if len(row) != expected:
            raise V2BError("S5 bundle constant arity drift")
        name_key = _name(row[1], "constant")
        if not name_key or name_key in seen:
            raise V2BError("S5 bundle anonymous/duplicate constant")
        seen.add(name_key)
        if not isinstance(row[2], list):
            raise V2BError("S5 bundle universe-parameter vector drift")
        params = [_name(param, "universe parameter") for param in row[2]]
        if any(not param for param in params) or len(set(params)) != len(params):
            raise V2BError("S5 bundle universe parameter drift")
        nodes += _validate_bundle_tree(row[3])
        nodes += _validate_bundle_tree(row[4])
        if nodes > MAX_BUNDLE_NODES:
            raise V2BError("S5 bundle exceeds the aggregate node cap")
        if row[0] == "defn":
            hints = row[5]
            if not isinstance(hints, list) or hints not in (
                    ["abbrev"], ["opaque"]) and not (
                        len(hints) == 2 and hints[0] == "regular"
                        and type(hints[1]) is int
                        and 0 <= hints[1] <= 2**32 - 1):
                raise V2BError("S5 bundle reducibility hint drift")
        if name_key == tuple(("s", part) for part in target_name.split(".")):
            target_rows.append(row)
    if len(target_rows) != 1:
        raise V2BError("S5 bundle lacks one exact committed target")
    return bundle


def target_certificate(bundle, target_name):
    validate_bundle(bundle, target_name)
    target_key = tuple(("s", part) for part in target_name.split("."))
    rows = [row for row in bundle[2]
            if _name(row[1], "target") == target_key]
    row = rows[0]
    certificate = dict(
        schema="v2b_s5_canonical_target_certificate_v1",
        targetName=target_name, kind=row[0], levelParams=copy.deepcopy(row[2]),
        typeExpression=copy.deepcopy(row[3]))
    certificate["sha256"] = sha256_sorted_json(certificate)
    return certificate


@dataclass(frozen=True)
class LaunchSpec:
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    backend: str


def _derived_child_environment(visibility):
    # Even the explicitly non-production backend receives only this frozen
    # environment.  It must never inherit credentials or caller-controlled
    # LEAN_PATH/plugin variables merely because it is being used by a test.
    lean_path = visibility["toolchain"]["lean"]["path"]
    import_roots = set()
    library_roots = set()
    for row in visibility["allowlist"]:
        path = row["path"]
        for module in row["modules"]:
            suffix = module.replace(".", os.sep) + ".olean"
            marker = path.find(suffix)
            if marker >= 0:
                import_roots.add(path[:marker].rstrip(os.sep))
        if any(role in row["roles"] for role in (
                "dynamic-library", "plugin")):
            library_roots.add(os.path.dirname(path))
    env = {
        "HOME": "/tmp/home",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/tmp/xdg-cache",
        "XDG_CONFIG_HOME": "/tmp/xdg-config",
        "XDG_DATA_HOME": "/tmp/xdg-data",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "LEAN_NUM_THREADS": "1",
        "PATH": os.path.dirname(lean_path),
    }
    if import_roots:
        env["LEAN_PATH"] = os.pathsep.join(sorted(import_roots))
    if library_roots:
        joined = os.pathsep.join(sorted(library_roots))
        env["LD_LIBRARY_PATH"] = joined
        env["DYLD_LIBRARY_PATH"] = joined
    return {key: env[key] for key in sorted(env)}


def _resolve_bwrap():
    candidate = os.path.realpath("/usr/bin/bwrap")
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise V2BError("production S5 requires canonical executable bubblewrap")
    return candidate


def execution_record(visibility, *, allow_unisolated_test=False):
    validate_visibility_artifact(visibility, live_files=False)
    driver_sha = sha256_file(LEAN_DRIVER)
    backend = "none-test-only" if allow_unisolated_test else "bubblewrap"
    bwrap = None
    if not allow_unisolated_test:
        path = _resolve_bwrap()
        bwrap = dict(path=path, sha256=sha256_file(path))
    environment = _derived_child_environment(visibility)
    source_closure = [dict(path=path, sha256=sha256_file(path))
                      for path in PYTHON_SOURCE_FILES]
    python_path = os.path.realpath(sys.executable)
    return dict(
        schema="v2b_s5_four_phase_execution_binding_v1",
        backend=backend,
        visibilityContractSha256=visibility["contract_sha256"],
        lean=copy.deepcopy(visibility["toolchain"]["lean"]),
        driver=dict(path=LEAN_DRIVER, sha256=driver_sha),
        runner=dict(
            path=RUNNER_PATH, sha256=sha256_file(RUNNER_PATH),
            sourceClosure=source_closure,
            sourceClosureSha256=sha256_sorted_json(source_closure)),
        python=dict(
            path=python_path, sha256=sha256_file(python_path),
            version=sys.version,
            implementation=sys.implementation.name,
            cacheTag=sys.implementation.cache_tag),
        bubblewrap=bwrap,
        resourceLimits=dict(RESOURCE_LIMITS),
        childEnvironmentSha256=sha256_sorted_json(environment),
        sandboxContractSha256=SANDBOX_CONTRACT_SHA256)


def execution_binding(visibility, *, allow_unisolated_test=False):
    return sha256_sorted_json(execution_record(
        visibility, allow_unisolated_test=allow_unisolated_test))


class VisibilityLauncher:
    """Build a child invocation from the already-bound visibility artifact."""

    def __init__(self, visibility, plan, *, allow_unisolated_test=False):
        self.visibility = validate_visibility_artifact(
            visibility, live_files=True)
        self.allow_unisolated_test = allow_unisolated_test
        self.record = execution_record(
            visibility, allow_unisolated_test=allow_unisolated_test)
        if plan["executionBinding"] != sha256_sorted_json(self.record) \
                or plan["driverSha256"] != self.record["driver"]["sha256"]:
            raise V2BError("S5 plan/execution runtime binding drift")
        self.environment = _derived_child_environment(visibility)

    def assert_live(self):
        validate_visibility_artifact(self.visibility, live_files=True)
        current = execution_record(
            self.visibility,
            allow_unisolated_test=self.allow_unisolated_test)
        if current != self.record:
            raise V2BError("S5 execution inputs drifted after plan binding")
        return self

    def prepare(self, phase, manifest_path):
        self.assert_live()
        if phase not in PHASES:
            raise V2BError(f"unknown S5 launch phase {phase}")
        if self.allow_unisolated_test:
            argv = (self.record["lean"]["path"], "--run", LEAN_DRIVER,
                    manifest_path)
            return LaunchSpec(argv=argv,
                              cwd=os.path.dirname(os.path.abspath(__file__)),
                              env=dict(self.environment),
                              backend="none-test-only")
        return self._bubblewrap(manifest_path)

    def _bubblewrap(self, manifest_path):
        bwrap = self.record["bubblewrap"]["path"]
        manifest_inside = "/v2b-input/phase-manifest.json"
        paths = [manifest_inside, LEAN_DRIVER,
                 self.record["lean"]["path"],
                 self.visibility["source"]["path"]]
        paths.extend(item["path"] for item in self.visibility["allowlist"])
        paths.extend(item["resolved_path"]
                     for item in self.visibility["allowlist"])
        directories = {"/dev", "/tmp", "/v2b-input"}
        for path in paths:
            current = os.path.dirname(path)
            while current not in ("", "/"):
                directories.add(current)
                current = os.path.dirname(current)
        argv = [bwrap, "--unshare-all", "--die-with-parent",
                "--new-session", "--cap-drop", "ALL", "--clearenv"]
        for directory in sorted(directories, key=lambda item:
                                (item.count("/"), item)):
            argv.extend(("--dir", directory))
        for item in self.visibility["allowlist"]:
            if item["kind"] == "file":
                argv.extend(("--ro-bind", item["path"], item["path"]))
            else:
                argv.extend(("--symlink", item["link_target"], item["path"]))
        argv.extend(("--ro-bind", LEAN_DRIVER, LEAN_DRIVER))
        argv.extend(("--ro-bind", manifest_path, manifest_inside))
        # Freeze every logical/search-directory skeleton after installing the
        # exact files.  Only the subsequently over-mounted private /tmp is
        # writable; /dev exposes devices but no mutable directory surface.
        argv.extend(("--remount-ro", "/", "--tmpfs", "/tmp"))
        for directory in ("/tmp/home", "/tmp/xdg-cache",
                          "/tmp/xdg-config", "/tmp/xdg-data"):
            argv.extend(("--dir", directory))
        argv.extend(("--dev", "/dev", "--remount-ro", "/dev"))
        for key, value in self.environment.items():
            argv.extend(("--setenv", key, value))
        argv.extend(("--chdir", "/tmp", "--",
                     self.record["lean"]["path"], "--run", LEAN_DRIVER,
                     manifest_inside))
        return LaunchSpec(argv=tuple(argv), cwd="/",
                          env={}, backend="bubblewrap")


class _BoundedReader(threading.Thread):
    def __init__(self, stream, limit, exceeded):
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.exceeded = exceeded
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.error = None

    def snapshot(self):
        with self.lock:
            return bytes(self.buffer)

    def run(self):
        try:
            while True:
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk:
                    break
                with self.lock:
                    remaining = self.limit - len(self.buffer)
                    if remaining > 0:
                        self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded.set()
        except OSError as err:
            self.error = err
        finally:
            self.stream.close()


def _set_resource_limits(limits, enforce_address_space):
    if enforce_address_space:
        resource.setrlimit(resource.RLIMIT_AS, (
            limits["address_space_bytes"], limits["address_space_bytes"]))
    resource.setrlimit(resource.RLIMIT_CPU, (
        limits["cpu_seconds"], limits["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_NPROC, (
        limits["n_processes"], limits["n_processes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (
        limits["n_open_files"], limits["n_open_files"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (
        limits["file_size_bytes"], limits["file_size_bytes"]))
    resource.setrlimit(resource.RLIMIT_CORE, (
        limits["core_size_bytes"], limits["core_size_bytes"]))


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _record_keys(record_type, status=None):
    if record_type == "prevalidation":
        return frozenset((
            "schema", "record_type", "mode", "n_prior_commands",
            "prefix_view_bytes", "header_view_bytes", "target_view_bytes")) \
            if status == "target" else frozenset((
                "schema", "record_type", "mode", "n_prior_commands",
                "n_decoded_constants"))
    if record_type in ("phase-start", "phase-go-accepted"):
        return frozenset(("schema", "record_type", "mode"))
    if record_type == "target":
        if status == "verified":
            return frozenset((
                "schema", "record_type", "status", "n_bundled_constants",
                "bundle"))
        return None
    if record_type == "suffix":
        if status == "verified":
            return frozenset((
                "schema", "record_type", "status", "n_replayed_constants",
                "n_suffix_commands"))
        return None
    return None


def _validate_record(row, manifest, ordinal):
    if not isinstance(row, dict) or row.get("schema") != LEAN_OUTPUT_SCHEMA:
        raise V2BError("S5 authenticated record schema drift")
    mode = manifest["mode"]
    expected_types = ["prevalidation", "phase-start",
                      "phase-go-accepted", mode]
    if ordinal >= len(expected_types) \
            or row.get("record_type") != expected_types[ordinal]:
        raise V2BError("S5 authenticated record order/type drift")
    record_type = row["record_type"]
    if record_type == "prevalidation":
        keys = _record_keys(record_type, mode)
        if set(row) != keys or row.get("mode") != mode:
            raise V2BError("S5 prevalidation record key/value drift")
        _nat(row.get("n_prior_commands"), "S5 n_prior_commands")
        if mode == "target":
            expected = {
                "prefix_view_bytes": manifest["targetStartByte"],
                "header_view_bytes": manifest["headerEndByte"],
                "target_view_bytes": manifest["retainedEndByte"],
            }
            if any(row.get(key) != value for key, value in expected.items()):
                raise V2BError("S5 target prevalidation byte drift")
        else:
            _nat(row.get("n_decoded_constants"),
                 "S5 n_decoded_constants", positive=True)
    elif record_type in ("phase-start", "phase-go-accepted"):
        if set(row) != _record_keys(record_type) or row.get("mode") != mode:
            raise V2BError("S5 phase control record drift")
    else:
        status = row.get("status")
        if status not in ("verified", "verification-failure"):
            raise V2BError("S5 phase terminal status drift")
        if record_type == "target":
            if status == "verified":
                if set(row) != _record_keys(record_type, status):
                    raise V2BError("S5 target success key drift")
                _nat(row.get("n_bundled_constants"),
                     "S5 n_bundled_constants", positive=True)
                validate_bundle(row.get("bundle"), manifest["targetName"])
                if row["n_bundled_constants"] != len(row["bundle"][2]):
                    raise V2BError("S5 target bundle count drift")
                certificate = target_certificate(
                    row["bundle"], manifest["targetName"])
                expected_kind = ("defn" if manifest["targetKind"] == "def"
                                 else "thm")
                if certificate["kind"] != expected_kind:
                    raise V2BError("S5 target bundle kind drift")
            else:
                allowed = (
                    frozenset(("schema", "record_type", "status", "reason")),
                    frozenset(("schema", "record_type", "status", "reason",
                               "detail")),
                )
                if frozenset(row) not in allowed \
                        or not isinstance(row.get("reason"), str) \
                        or not row["reason"] \
                        or "detail" in row and not isinstance(
                            row["detail"], str):
                    raise V2BError("S5 target failure key/value drift")
        elif status == "verified":
            if set(row) != _record_keys(record_type, status):
                raise V2BError("S5 suffix success key drift")
            _nat(row.get("n_replayed_constants"),
                 "S5 n_replayed_constants", positive=True)
            _nat(row.get("n_suffix_commands"), "S5 n_suffix_commands")
        else:
            allowed = (
                frozenset(("schema", "record_type", "status", "reason")),
                frozenset(("schema", "record_type", "status", "reason",
                           "n_replayed_constants")),
            )
            if frozenset(row) not in allowed \
                    or not isinstance(row.get("reason"), str) \
                    or not row["reason"]:
                raise V2BError("S5 suffix failure key/value drift")
            if "n_replayed_constants" in row:
                _nat(row.get("n_replayed_constants"),
                     "S5 n_replayed_constants")
    return row


def parse_transcript_prefix(stdout, manifest, nonce):
    """Parse newline-complete authenticated rows, rejecting any corruption."""
    if not isinstance(stdout, bytes) or not _hex(nonce):
        raise V2BError("S5 transcript/nonce type drift")
    marker = (MARKER_PREFIX + nonce + MARKER_SUFFIX).encode("ascii")
    records = []
    for line in stdout.splitlines():
        if not line.startswith(marker):
            continue
        value = _loads_strict(line[len(marker):], "authenticated S5 stdout")
        _validate_record(value, manifest, len(records))
        records.append(value)
        if len(records) > 4:
            raise V2BError("S5 authenticated record count exceeds protocol")
    stages = ("before-prevalidation", "prevalidated",
              "awaiting-authorization", "go-accepted", "complete")
    return dict(stage=stages[len(records)], records=records,
                terminal=records[3] if len(records) == 4 else None)


def _complete_line_prefix(blob):
    index = blob.rfind(b"\n")
    return blob[:index + 1] if index >= 0 else b""


def _prefix_through_stage(stdout, manifest, nonce, wanted):
    complete = _complete_line_prefix(stdout)
    offset = 0
    for line in complete.splitlines(keepends=True):
        offset += len(line)
        parsed = parse_transcript_prefix(complete[:offset], manifest, nonce)
        if parsed["stage"] in (wanted, "complete"):
            return complete[:offset]
    return None


def _classify_phase(phase, manifest, stdout, nonce, returncode, *,
                    timed_out=False, output_limited=False,
                    authorization_committed=False):
    evidence = (_complete_line_prefix(stdout)
                if timed_out or output_limited else stdout)
    try:
        parsed = parse_transcript_prefix(evidence, manifest, nonce)
        protocol_valid = True
        protocol_error = None
    except V2BError as err:
        parsed = dict(stage="unknown", records=[], terminal=None)
        protocol_valid = False
        protocol_error = err
    stage = parsed["stage"]
    after_go = stage in ("go-accepted", "complete") \
        or authorization_committed
    baseline = phase.startswith("baseline-")
    if not protocol_valid:
        classification = ("harness-invalid" if baseline
                          else "candidate-terminated")
        outcome_bearing = after_go
    elif not after_go:
        classification = "harness-invalid"
        outcome_bearing = False
    elif timed_out:
        classification = ("harness-invalid" if baseline
                          else "candidate-timeout")
        outcome_bearing = not baseline
    elif output_limited:
        classification = ("harness-invalid" if baseline
                          else "candidate-output-limit")
        outcome_bearing = not baseline
    elif returncode != 0 or stage != "complete":
        classification = ("harness-invalid" if baseline
                          else "candidate-terminated")
        outcome_bearing = not baseline
    elif parsed["terminal"]["status"] == "verified":
        classification = "phase-verified"
        outcome_bearing = True
    else:
        classification = ("baseline-ineligible" if baseline
                          else "verification-failure")
        outcome_bearing = True
    return dict(
        authenticatedStage=stage, protocolValid=protocol_valid,
        protocolErrorSha256=(
            sha256_bytes(str(protocol_error).encode("utf-8"))
            if protocol_error is not None else None),
        classification=classification, outcomeBearing=outcome_bearing,
        parsed=parsed if protocol_valid else None)


def _attempt_root(run_dir, phase):
    return os.path.join(run_dir, "attempts", phase)


def _require_private_directory(path, label):
    try:
        info = os.lstat(path)
    except OSError as err:
        raise V2BError(f"cannot inspect {label} {path}: {err}") from err
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
            or info.st_uid != os.getuid() \
            or stat.S_IMODE(info.st_mode) & 0o077:
        raise V2BError(f"{label} is not one owner-private real directory")
    return path


def _require_private_file(path, label):
    try:
        info = os.lstat(path)
    except OSError as err:
        raise V2BError(f"cannot inspect {label} {path}: {err}") from err
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) \
            or info.st_nlink != 1 or info.st_uid != os.getuid() \
            or stat.S_IMODE(info.st_mode) & 0o077:
        raise V2BError(f"{label} is not one owner-private regular file")
    return path


def _attempt_directories(root, phase):
    if not os.path.exists(root):
        return []
    _require_private_directory(root, f"S5 {phase} attempt root")
    result = []
    for name in sorted(os.listdir(root)):
        if not _hex(name):
            raise V2BError(f"S5 {phase} attempt root has a foreign entry")
        directory = os.path.join(root, name)
        _require_private_directory(directory, f"S5 {phase} attempt")
        result.append(directory)
    return result


def _prepare_attempt(run_dir, plan, phase, nonce, plan_sha, visibility_sha,
                     manifest_blob, sources, backend):
    root = _attempt_root(run_dir, phase)
    os.makedirs(root, mode=0o700, exist_ok=True)
    directories = _attempt_directories(root, phase)
    for prior in directories:
        intent_path = os.path.join(prior, "go-intent.json")
        if os.path.lexists(intent_path):
            _require_private_file(intent_path, f"S5 {phase} GO intent")
            return dict(directory=prior, attemptId=os.path.basename(prior),
                        prior=True)
    if len(directories) >= MAX_PRESTART_ATTEMPTS:
        raise V2BError(f"S5 {phase} pre-GO retry limit is exhausted")
    ordinal = len(directories)
    nonce_sha = sha256_bytes(nonce.encode("ascii"))
    attempt_id = sha256_sorted_json(dict(
        schema="v2b_s5_four_phase_attempt_id_v1",
        invocationBinding=plan["invocationBinding"], phase=phase,
        ordinal=ordinal, nonceSha256=nonce_sha))
    directory = os.path.join(root, attempt_id)
    try:
        os.mkdir(directory, 0o700)
        _fsync_directory(root)
    except OSError as err:
        raise V2BError(f"cannot create S5 {phase} attempt: {err}") from err
    digests = frame_digests(sources)
    opened = dict(
        schema=ATTEMPT_OPEN_SCHEMA, attemptId=attempt_id,
        attemptOrdinal=ordinal,
        invocationBinding=plan["invocationBinding"], phase=phase,
        nonceSha256=nonce_sha, planSha256=plan_sha,
        visibilitySha256=visibility_sha,
        frameDigests=digests,
        frameBytes={role: len(blob) for role, blob in sources.items()},
        manifestSha256=sha256_bytes(manifest_blob), backend=backend,
        openedWallTimeNs=time.time_ns())
    if set(opened) != _ATTEMPT_OPEN_KEYS:
        raise AssertionError("internal S5 attempt-open schema drift")
    opened_sha = _write_new_json(
        os.path.join(directory, "attempt-open.json"), opened)
    _write_new_bytes(os.path.join(directory, "manifest.json"), manifest_blob)
    return dict(directory=directory, attemptId=attempt_id, prior=False,
                openedSha256=opened_sha)


def _abort_child(process, readers):
    """Best-effort cleanup that never masks the original host exception."""
    _kill_group(process)
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        _kill_group(process)
        try:
            process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    for reader in readers:
        if reader.ident is not None:
            reader.join(timeout=10)


def _drive_phase_process(process, stdout_reader, stderr_reader, exceeded,
                         nonce, manifest, sources, attempt, limits,
                         started_mono, started_wall, deadline):
    roles = TARGET_FRAME_ROLES if manifest["mode"] == "target" \
        else SUFFIX_FRAME_ROLES
    preauthorization = channel_payload(
        nonce, sources, roles, authorize=False)
    try:
        process.stdin.write(preauthorization)
        process.stdin.flush()
    except OSError:
        _kill_group(process)
    timed_out = False
    authorized = False
    go_intent_sha = None
    go_accepted_sha = None
    while process.poll() is None:
        snapshot = stdout_reader.snapshot()
        complete = _complete_line_prefix(snapshot)
        if authorized and go_accepted_sha is None:
            try:
                accepted_prefix = _prefix_through_stage(
                    complete, manifest, nonce, "go-accepted")
            except V2BError:
                accepted_prefix = None
            if accepted_prefix is not None:
                accepted = dict(
                    schema=GO_ACCEPTED_SCHEMA,
                    attemptId=attempt["attemptId"],
                    invocationBinding=attempt["invocationBinding"],
                    phase=attempt["phase"], authenticatedStage="go-accepted",
                    stdoutPrefixSha256=sha256_bytes(accepted_prefix),
                    stdoutPrefixBytes=len(accepted_prefix),
                    observedWallTimeNs=time.time_ns())
                if set(accepted) != _GO_ACCEPTED_KEYS:
                    raise AssertionError("internal S5 GO-accepted drift")
                go_accepted_sha = _write_new_json(
                    os.path.join(attempt["directory"], "go-accepted.json"),
                    accepted)
        if exceeded.is_set():
            _kill_group(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_group(process)
            break
        if not authorized:
            try:
                parsed = parse_transcript_prefix(complete, manifest, nonce)
            except V2BError:
                _kill_group(process)
                break
            if parsed["stage"] == "awaiting-authorization":
                if len(snapshot) > limits["stdout_bytes"] - \
                        CONTROL_HEADROOM_BYTES:
                    exceeded.set()
                    continue
                start_prefix = _prefix_through_stage(
                    complete, manifest, nonce, "awaiting-authorization")
                _write_new_bytes(os.path.join(
                    attempt["directory"], "start-prefix.bin"), start_prefix)
                intent = dict(
                    schema=GO_INTENT_SCHEMA,
                    attemptId=attempt["attemptId"],
                    invocationBinding=attempt["invocationBinding"],
                    phase=attempt["phase"],
                    nonceSha256=sha256_bytes(nonce.encode("ascii")),
                    authenticatedStage="awaiting-authorization",
                    stdoutPrefixSha256=sha256_bytes(start_prefix),
                    stdoutPrefixBytes=len(start_prefix),
                    committedWallTimeNs=time.time_ns())
                if set(intent) != _GO_INTENT_KEYS:
                    raise AssertionError("internal S5 GO-intent drift")
                go_intent_sha = _write_new_json(
                    os.path.join(attempt["directory"], "go-intent.json"),
                    intent)
                if exceeded.is_set() or time.monotonic() >= deadline \
                        or process.poll() is not None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                    _kill_group(process)
                    break
                try:
                    process.stdin.write(("GO:" + nonce + "\n").encode(
                        "ascii"))
                    process.stdin.flush()
                    process.stdin.close()
                    authorized = True
                except OSError:
                    _kill_group(process)
                    break
        time.sleep(.01)
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        returncode = process.wait(timeout=10)
    stdout_reader.join(timeout=10)
    stderr_reader.join(timeout=10)
    if stdout_reader.is_alive() or stderr_reader.is_alive() \
            or stdout_reader.error is not None \
            or stderr_reader.error is not None:
        raise V2BError("S5 bounded output reader failed to terminate")
    stdout = stdout_reader.snapshot()
    stderr = stderr_reader.snapshot()
    if authorized and go_accepted_sha is None:
        try:
            accepted_prefix = _prefix_through_stage(
                stdout, manifest, nonce, "go-accepted")
        except V2BError:
            accepted_prefix = None
        if accepted_prefix is not None:
            accepted = dict(
                schema=GO_ACCEPTED_SCHEMA,
                attemptId=attempt["attemptId"],
                invocationBinding=attempt["invocationBinding"],
                phase=attempt["phase"], authenticatedStage="go-accepted",
                stdoutPrefixSha256=sha256_bytes(accepted_prefix),
                stdoutPrefixBytes=len(accepted_prefix),
                observedWallTimeNs=time.time_ns())
            go_accepted_sha = _write_new_json(
                os.path.join(attempt["directory"], "go-accepted.json"),
                accepted)
    return dict(
        stdout=stdout, stderr=stderr, returncode=returncode, pid=process.pid,
        startedWallTimeNs=started_wall, endedWallTimeNs=time.time_ns(),
        wallTimeNs=time.monotonic_ns() - started_mono,
        timedOut=timed_out, outputLimited=exceeded.is_set(),
        goIntentSha256=go_intent_sha, goAcceptedSha256=go_accepted_sha)


def _execute_phase(spec, nonce, manifest, sources, attempt, limits,
                   *, enforce_address_space=True):
    exceeded = threading.Event()
    started_mono = time.monotonic_ns()
    started_wall = time.time_ns()
    deadline = time.monotonic() + limits["timeout_seconds"]
    try:
        process = subprocess.Popen(
            list(spec.argv), cwd=spec.cwd, env=spec.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
            preexec_fn=lambda: _set_resource_limits(
                limits, enforce_address_space))
    except (OSError, subprocess.SubprocessError) as err:
        raise V2BError(f"cannot start S5 child: {err}") from err
    stdout_reader = _BoundedReader(
        process.stdout, limits["stdout_bytes"], exceeded)
    stderr_reader = _BoundedReader(
        process.stderr, limits["stderr_bytes"], exceeded)
    readers = (stdout_reader, stderr_reader)
    try:
        stdout_reader.start()
        stderr_reader.start()
        return _drive_phase_process(
            process, stdout_reader, stderr_reader, exceeded, nonce,
            manifest, sources, attempt, limits, started_mono, started_wall,
            deadline)
    except BaseException:
        _abort_child(process, readers)
        raise


def _read_attempt(directory, plan, phase, manifest, sources, plan_sha,
                  visibility_sha, launcher):
    _require_private_directory(directory, f"S5 {phase} attempt")
    paths = {name: os.path.join(directory, name) for name in (
        "attempt-open.json", "manifest.json", "start-prefix.bin",
        "go-intent.json", "go-accepted.json", "stdout.bin", "stderr.bin",
        "terminal.json")}
    required = ["attempt-open.json", "manifest.json", "start-prefix.bin",
                "go-intent.json", "stdout.bin", "stderr.bin",
                "terminal.json"]
    expected_entries = set(required)
    if os.path.lexists(paths["go-accepted.json"]):
        expected_entries.add("go-accepted.json")
    if set(os.listdir(directory)) != expected_entries:
        raise V2BError(
            f"S5 {phase} has partial outcome-bearing attempt {directory}")
    for name in expected_entries:
        _require_private_file(paths[name], f"S5 {phase} {name}")
    opened, opened_sha = _read_strict_json(
        paths["attempt-open.json"], ATTEMPT_OPEN_SCHEMA, _ATTEMPT_OPEN_KEYS)
    intent, intent_sha = _read_strict_json(
        paths["go-intent.json"], GO_INTENT_SCHEMA, _GO_INTENT_KEYS)
    accepted = accepted_sha = None
    if os.path.exists(paths["go-accepted.json"]):
        accepted, accepted_sha = _read_strict_json(
            paths["go-accepted.json"], GO_ACCEPTED_SCHEMA,
            _GO_ACCEPTED_KEYS)
    terminal, terminal_sha = _read_strict_json(
        paths["terminal.json"], ATTEMPT_TERMINAL_SCHEMA,
        _ATTEMPT_TERMINAL_KEYS)
    try:
        manifest_blob = open(paths["manifest.json"], "rb").read()
        start_prefix = open(paths["start-prefix.bin"], "rb").read()
        stdout = open(paths["stdout.bin"], "rb").read()
        stderr = open(paths["stderr.bin"], "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read S5 attempt bytes: {err}") from err
    stored_manifest = _loads_strict(manifest_blob, "S5 phase manifest")
    validate_phase_manifest(stored_manifest)
    spec = launcher.prepare(phase, paths["manifest.json"])
    backend = launcher.record["backend"]
    nonce = terminal.get("nonce")
    nonce_sha = sha256_bytes(nonce.encode("ascii")) \
        if _hex(nonce) else None
    expected_digests = frame_digests(sources)
    expected_bytes = {role: len(blob) for role, blob in sources.items()}
    attempt_id = os.path.basename(directory)
    ordinal = opened.get("attemptOrdinal")
    expected_attempt_id = (sha256_sorted_json(dict(
        schema="v2b_s5_four_phase_attempt_id_v1",
        invocationBinding=plan["invocationBinding"], phase=phase,
        ordinal=ordinal, nonceSha256=nonce_sha))
        if type(ordinal) is int and ordinal >= 0 and nonce_sha is not None
        else None)
    identity = (_hex(attempt_id)
                and attempt_id == expected_attempt_id
                and opened.get("attemptId") == attempt_id
                and intent.get("attemptId") == attempt_id
                and terminal.get("attemptId") == attempt_id
                and opened.get("invocationBinding") ==
                plan["invocationBinding"]
                and intent.get("invocationBinding") ==
                plan["invocationBinding"]
                and terminal.get("invocationBinding") ==
                plan["invocationBinding"]
                and opened.get("phase") == phase
                and intent.get("phase") == phase
                and terminal.get("phase") == phase)
    if accepted is not None:
        identity = identity and accepted.get("attemptId") == attempt_id \
            and accepted.get("invocationBinding") == plan["invocationBinding"] \
            and accepted.get("phase") == phase
    if not identity or nonce_sha is None \
            or opened.get("nonceSha256") != nonce_sha \
            or intent.get("nonceSha256") != nonce_sha \
            or terminal.get("nonceSha256") != nonce_sha \
            or opened.get("planSha256") != plan_sha \
            or terminal.get("planSha256") != plan_sha \
            or opened.get("visibilitySha256") != visibility_sha \
            or terminal.get("visibilitySha256") != visibility_sha \
            or opened.get("frameDigests") != expected_digests \
            or terminal.get("frameDigests") != expected_digests \
            or opened.get("frameBytes") != expected_bytes \
            or terminal.get("frameBytes") != expected_bytes \
            or opened.get("backend") != backend \
            or terminal.get("backend") != backend \
            or opened.get("manifestSha256") != sha256_bytes(manifest_blob) \
            or terminal.get("manifestSha256") != sha256_bytes(manifest_blob) \
            or terminal.get("attemptOpenSha256") != opened_sha \
            or terminal.get("argvSha256") != \
            sha256_sorted_json(list(spec.argv)) \
            or spec.backend != backend or stored_manifest != manifest:
        raise V2BError(f"S5 {phase} attempt identity/input drift")
    if intent.get("stdoutPrefixSha256") != sha256_bytes(start_prefix) \
            or intent.get("stdoutPrefixBytes") != len(start_prefix) \
            or terminal.get("goIntentSha256") != intent_sha \
            or terminal.get("goAcceptedSha256") != accepted_sha:
        raise V2BError(f"S5 {phase} durable GO journal drift")
    times = (
        opened.get("openedWallTimeNs"), terminal.get("startedWallTimeNs"),
        intent.get("committedWallTimeNs"),
        accepted.get("observedWallTimeNs") if accepted is not None else None,
        terminal.get("endedWallTimeNs"))
    if any(type(value) is not int or value < 0
           for value in times if value is not None) \
            or not times[0] <= times[1] <= times[2] <= times[4] \
            or times[3] is not None and not times[2] <= times[3] <= times[4]:
        raise V2BError(f"S5 {phase} durable journal time order drift")
    try:
        start_parsed = parse_transcript_prefix(start_prefix, manifest, nonce)
    except V2BError as err:
        raise V2BError(f"S5 {phase} start-prefix drift: {err}") from err
    if start_parsed["stage"] != "awaiting-authorization" \
            or intent.get("authenticatedStage") != "awaiting-authorization":
        raise V2BError(f"S5 {phase} GO intent stage drift")
    if accepted is not None:
        accepted_bytes = accepted.get("stdoutPrefixBytes")
        if type(accepted_bytes) is not int or accepted_bytes <= 0 \
                or accepted_bytes > len(stdout) \
                or accepted.get("stdoutPrefixSha256") != \
                sha256_bytes(stdout[:accepted_bytes]) \
                or accepted.get("authenticatedStage") != "go-accepted":
            raise V2BError(f"S5 {phase} GO acceptance drift")
        accepted_parsed = parse_transcript_prefix(
            stdout[:accepted_bytes], manifest, nonce)
        if accepted_parsed["stage"] not in ("go-accepted", "complete"):
            raise V2BError(f"S5 {phase} GO acceptance transcript drift")
    if terminal.get("stdoutSha256") != sha256_bytes(stdout) \
            or terminal.get("stdoutBytes") != len(stdout) \
            or terminal.get("stderrSha256") != sha256_bytes(stderr) \
            or terminal.get("stderrBytes") != len(stderr) \
            or type(terminal.get("returncode")) is not int \
            or type(terminal.get("timedOut")) is not bool \
            or type(terminal.get("outputLimited")) is not bool \
            or type(terminal.get("wallTimeNs")) is not int \
            or terminal["wallTimeNs"] < 0 \
            or type(terminal.get("startedWallTimeNs")) is not int \
            or type(terminal.get("endedWallTimeNs")) is not int \
            or terminal.get("startedWallTimeNs") > \
            terminal.get("endedWallTimeNs") \
            or type(terminal.get("pid")) is not int \
            or terminal["pid"] <= 0:
        raise V2BError(f"S5 {phase} terminal byte/time drift")
    classified = _classify_phase(
        phase, manifest, stdout, nonce, terminal.get("returncode"),
        timed_out=terminal.get("timedOut"),
        output_limited=terminal.get("outputLimited"),
        authorization_committed=True)
    for key in ("authenticatedStage", "protocolValid",
                "protocolErrorSha256", "classification", "outcomeBearing"):
        if terminal.get(key) != classified[key]:
            raise V2BError(f"S5 {phase} terminal classification drift")
    if classified["authenticatedStage"] == "complete" \
            and accepted is None:
        raise V2BError(f"S5 {phase} complete transcript lacks durable GO ack")
    return dict(
        directory=directory, attemptId=attempt_id,
        attemptOpenSha256=opened_sha, goIntentSha256=intent_sha,
        goAcceptedSha256=accepted_sha, terminalSha256=terminal_sha,
        terminal=terminal, stdout=stdout, stderr=stderr,
        parsed=classified["parsed"], reused=True)


def _run_phase(run_dir, plan, visibility, launcher, phase, original,
               candidate, plan_sha, visibility_sha, bundle=None,
               nonce_for_test=None):
    manifest = phase_manifest(plan, phase)
    manifest_blob = _strict_json_bytes(manifest)
    sources = _phase_sources(
        plan, original, candidate, phase, bundle=bundle)
    root = _attempt_root(run_dir, phase)
    if os.path.exists(root):
        committed = []
        for directory in _attempt_directories(root, phase):
            intent_path = os.path.join(directory, "go-intent.json")
            if os.path.lexists(intent_path):
                _require_private_file(intent_path, f"S5 {phase} GO intent")
                committed.append(directory)
        if len(committed) > 1:
            raise V2BError(f"S5 {phase} has multiple GO-committed attempts")
        if committed:
            return _read_attempt(
                committed[0], plan, phase, manifest, sources, plan_sha,
                visibility_sha, launcher)
    nonce = nonce_for_test or secrets.token_hex(32)
    if not _hex(nonce) or nonce_for_test is not None \
            and launcher.record["backend"] != "none-test-only":
        raise V2BError("fixed S5 nonce is allowed only in unisolated tests")
    # Stage the hash-only manifest before asking the launcher to bind it.
    provisional = _prepare_attempt(
        run_dir, plan, phase, nonce, plan_sha, visibility_sha,
        manifest_blob, sources, launcher.record["backend"])
    if provisional.get("prior"):
        return _read_attempt(
            provisional["directory"], plan, phase, manifest, sources,
            plan_sha, visibility_sha, launcher)
    manifest_path = os.path.join(provisional["directory"], "manifest.json")
    spec = launcher.prepare(phase, manifest_path)
    if spec.backend != launcher.record["backend"]:
        raise V2BError("S5 launcher/visibility backend drift")
    attempt = dict(
        directory=provisional["directory"],
        attemptId=provisional["attemptId"],
        invocationBinding=plan["invocationBinding"], phase=phase)
    result = _execute_phase(
        spec, nonce, manifest, sources, attempt, RESOURCE_LIMITS,
        enforce_address_space=spec.backend == "bubblewrap")
    launcher.assert_live()
    classified = _classify_phase(
        phase, manifest, result["stdout"], nonce, result["returncode"],
        timed_out=result["timedOut"],
        output_limited=result["outputLimited"],
        authorization_committed=result["goIntentSha256"] is not None)
    _write_new_bytes(os.path.join(attempt["directory"], "stdout.bin"),
                     result["stdout"])
    _write_new_bytes(os.path.join(attempt["directory"], "stderr.bin"),
                     result["stderr"])
    terminal = dict(
        schema=ATTEMPT_TERMINAL_SCHEMA, attemptId=attempt["attemptId"],
        invocationBinding=plan["invocationBinding"], phase=phase,
        nonce=nonce, nonceSha256=sha256_bytes(nonce.encode("ascii")),
        manifestSha256=sha256_bytes(manifest_blob), planSha256=plan_sha,
        visibilitySha256=visibility_sha,
        frameDigests=frame_digests(sources),
        frameBytes={role: len(blob) for role, blob in sources.items()},
        backend=spec.backend,
        argvSha256=sha256_sorted_json(list(spec.argv)), pid=result["pid"],
        startedWallTimeNs=result["startedWallTimeNs"],
        endedWallTimeNs=result["endedWallTimeNs"],
        wallTimeNs=result["wallTimeNs"], returncode=result["returncode"],
        timedOut=result["timedOut"], outputLimited=result["outputLimited"],
        stdoutSha256=sha256_bytes(result["stdout"]),
        stdoutBytes=len(result["stdout"]),
        stderrSha256=sha256_bytes(result["stderr"]),
        stderrBytes=len(result["stderr"]),
        goIntentSha256=result["goIntentSha256"],
        goAcceptedSha256=result["goAcceptedSha256"],
        attemptOpenSha256=provisional["openedSha256"],
        authenticatedStage=classified["authenticatedStage"],
        protocolValid=classified["protocolValid"],
        protocolErrorSha256=classified["protocolErrorSha256"],
        classification=classified["classification"],
        outcomeBearing=classified["outcomeBearing"])
    if set(terminal) != _ATTEMPT_TERMINAL_KEYS:
        raise AssertionError("internal S5 terminal schema drift")
    _write_new_json(os.path.join(attempt["directory"], "terminal.json"),
                    terminal)
    if result["goIntentSha256"] is None:
        raise V2BError(
            f"S5 {phase} stopped before GO; retained pre-outcome attempt")
    return _read_attempt(
        attempt["directory"], plan, phase, manifest, sources, plan_sha,
        visibility_sha, launcher)


def _phase_terminal(envelope):
    return envelope["parsed"]["terminal"]


def _phase_bundle(envelope):
    if envelope["terminal"]["classification"] != "phase-verified":
        return None
    row = _phase_terminal(envelope)
    return row.get("bundle") if row and row.get("status") == "verified" \
        else None


def _summary_path(run_dir):
    return os.path.join(run_dir, "summary.json")


def _make_summary(plan, plan_sha, visibility_sha, classification, passed,
                  phases, baseline_bundle=None, candidate_bundle=None):
    baseline_certificate = (target_certificate(
        baseline_bundle, plan["targetName"])
        if baseline_bundle is not None else None)
    candidate_certificate = (target_certificate(
        candidate_bundle, plan["targetName"])
        if candidate_bundle is not None else None)
    value = dict(
        schema=RUN_SUMMARY_SCHEMA,
        invocationBinding=plan["invocationBinding"], planSha256=plan_sha,
        visibilitySha256=visibility_sha,
        contractSha256=FOUR_PHASE_CONTRACT_SHA256,
        classification=classification,
        completedPhases=list(phases),
        phaseEvidenceSha256={
            phase: envelope["terminalSha256"]
            for phase, envelope in phases.items()},
        baselineBundleSha256=(
            sha256_bytes(canonical_json_bytes(baseline_bundle))
            if baseline_bundle is not None else None),
        candidateBundleSha256=(
            sha256_bytes(canonical_json_bytes(candidate_bundle))
            if candidate_bundle is not None else None),
        baselineTargetCertificateSha256=(
            baseline_certificate["sha256"]
            if baseline_certificate is not None else None),
        candidateTargetCertificateSha256=(
            candidate_certificate["sha256"]
            if candidate_certificate is not None else None))
    value["pass"] = passed
    if set(value) != _SUMMARY_KEYS:
        raise AssertionError("internal S5 summary schema drift")
    return value


def _derive_summary_outcome(plan, phases, baseline_bundle, candidate_bundle):
    completed = list(phases)
    if completed != list(PHASES[:len(completed)]) or not completed:
        raise V2BError("S5 phase prefix cannot derive an outcome")
    classifications = {
        phase: envelope["terminal"]["classification"]
        for phase, envelope in phases.items()}
    baseline_target = classifications["baseline-target"]
    if baseline_target != "phase-verified":
        if completed == ["baseline-target"] \
                and baseline_target == "baseline-ineligible" \
                and baseline_bundle is None:
            return "baseline-ineligible", None
        if completed == ["baseline-target"] \
                and baseline_target == "harness-invalid" \
                and baseline_bundle is None:
            return "harness-invalid", None
        raise V2BError("S5 invalid baseline-target summary boundary")
    if baseline_bundle is None or len(completed) < 2:
        raise V2BError("S5 verified baseline target lacks suffix/bundle")
    baseline_suffix = classifications["baseline-suffix"]
    if baseline_suffix != "phase-verified":
        if completed == ["baseline-target", "baseline-suffix"] \
                and baseline_suffix == "baseline-ineligible":
            return "baseline-ineligible", None
        if completed == ["baseline-target", "baseline-suffix"] \
                and baseline_suffix == "harness-invalid":
            return "harness-invalid", None
        raise V2BError("S5 invalid baseline-suffix summary boundary")
    if len(completed) < 3:
        raise V2BError("S5 eligible baseline lacks candidate target")
    candidate_target = classifications["candidate-target"]
    candidate_zeros = (
        "verification-failure", "candidate-timeout",
        "candidate-output-limit", "candidate-terminated")
    if candidate_target != "phase-verified":
        if completed == list(PHASES[:3]) \
                and candidate_target in candidate_zeros \
                and candidate_bundle is None:
            return candidate_target, 0
        raise V2BError("S5 invalid candidate-target summary boundary")
    if candidate_bundle is None:
        raise V2BError("S5 verified candidate target lacks a bundle")
    baseline_certificate = target_certificate(
        baseline_bundle, plan["targetName"])
    candidate_certificate = target_certificate(
        candidate_bundle, plan["targetName"])
    types_equal = (
        baseline_certificate["kind"] == candidate_certificate["kind"]
        and baseline_certificate["levelParams"] ==
        candidate_certificate["levelParams"]
        and baseline_certificate["typeExpression"] ==
        candidate_certificate["typeExpression"])
    if not types_equal:
        if completed == list(PHASES[:3]):
            return "candidate-type-drift", 0
        raise V2BError("S5 type-drift summary continued to the suffix")
    if len(completed) != 4:
        raise V2BError("S5 verified candidate target lacks candidate suffix")
    candidate_suffix = classifications["candidate-suffix"]
    if candidate_suffix == "phase-verified":
        return "verified-pass", 1
    if candidate_suffix in candidate_zeros:
        return candidate_suffix, 0
    raise V2BError("S5 invalid candidate-suffix summary boundary")


def validate_summary(run_dir, plan, visibility, original, candidate,
                     *, allow_unisolated_test=False):
    validate_plan_sources(
        plan, visibility, original, candidate,
        allow_unisolated_test=allow_unisolated_test)
    plan_path = os.path.join(run_dir, "plan.json")
    visibility_path = os.path.join(run_dir, "visibility.json")
    stored_plan, plan_sha = _read_strict_json(
        plan_path, PLAN_SCHEMA, _PLAN_KEYS)
    stored_visibility, visibility_sha = _read_strict_json(
        visibility_path, VISIBILITY_SCHEMA)
    summary, summary_sha = _read_strict_json(
        _summary_path(run_dir), RUN_SUMMARY_SCHEMA, _SUMMARY_KEYS)
    if stored_plan != plan or stored_visibility != visibility \
            or summary.get("invocationBinding") != plan["invocationBinding"] \
            or summary.get("planSha256") != plan_sha \
            or summary.get("visibilitySha256") != visibility_sha \
            or summary.get("contractSha256") != FOUR_PHASE_CONTRACT_SHA256:
        raise V2BError("S5 summary input/binding drift")
    completed = summary.get("completedPhases")
    if not isinstance(completed, list) \
            or completed != list(PHASES[:len(completed)]) \
            or not completed:
        raise V2BError("S5 summary phase order drift")
    if not isinstance(summary.get("phaseEvidenceSha256"), dict) \
            or set(summary["phaseEvidenceSha256"]) != set(completed):
        raise V2BError("S5 summary phase-evidence membership drift")
    launcher = VisibilityLauncher(
        visibility, plan, allow_unisolated_test=allow_unisolated_test)
    envelopes = {}
    baseline_bundle = candidate_bundle = None
    previous_end = None
    seen_pids = set()
    for phase in completed:
        bundle = (baseline_bundle if phase == "baseline-suffix"
                  else candidate_bundle if phase == "candidate-suffix"
                  else None)
        manifest = phase_manifest(plan, phase)
        sources = _phase_sources(
            plan, original, candidate, phase, bundle=bundle)
        root = _attempt_root(run_dir, phase)
        committed = []
        for directory in _attempt_directories(root, phase):
            intent_path = os.path.join(directory, "go-intent.json")
            if os.path.lexists(intent_path):
                _require_private_file(intent_path, f"S5 {phase} GO intent")
                committed.append(directory)
        if len(committed) != 1:
            raise V2BError(f"S5 summary phase {phase} attempt count drift")
        envelope = _read_attempt(
            committed[0], plan, phase, manifest, sources, plan_sha,
            visibility_sha, launcher)
        if envelope["terminalSha256"] != \
                summary["phaseEvidenceSha256"][phase]:
            raise V2BError(f"S5 summary phase {phase} evidence drift")
        terminal = envelope["terminal"]
        if terminal["pid"] in seen_pids \
                or previous_end is not None and \
                terminal["startedWallTimeNs"] < previous_end:
            raise V2BError("S5 phases are not ordered fresh processes")
        seen_pids.add(terminal["pid"])
        previous_end = terminal["endedWallTimeNs"]
        envelopes[phase] = envelope
        if phase == "baseline-target":
            baseline_bundle = _phase_bundle(envelope)
        elif phase == "candidate-target":
            candidate_bundle = _phase_bundle(envelope)
    for phase in PHASES[len(completed):]:
        root = _attempt_root(run_dir, phase)
        if _attempt_directories(root, phase):
            raise V2BError(
                f"S5 summary has evidence after terminal phase {phase}")
    derived_classification, derived_pass = _derive_summary_outcome(
        plan, envelopes, baseline_bundle, candidate_bundle)
    expected = _make_summary(
        plan, plan_sha, visibility_sha, derived_classification,
        derived_pass, envelopes, baseline_bundle, candidate_bundle)
    if expected != summary:
        raise V2BError("S5 summary derived-content drift")
    classification = summary.get("classification")
    valid_pass = summary.get("pass")
    if classification == "verified-pass":
        if completed != list(PHASES) or valid_pass != 1:
            raise V2BError("S5 verified-pass summary is incomplete")
    elif classification in (
            "baseline-ineligible", "harness-invalid", "verification-failure",
            "candidate-timeout", "candidate-output-limit",
            "candidate-terminated", "candidate-type-drift"):
        if valid_pass is not None and valid_pass != 0:
            raise V2BError("S5 zero summary pass field drift")
    else:
        raise V2BError("S5 summary classification drift")
    return dict(summary=summary, summarySha256=summary_sha,
                phases=envelopes, reused=True)


def _stage_run_inputs(run_dir, plan, visibility):
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    plan_blob = _strict_json_bytes(plan) + b"\n"
    visibility_blob = _strict_json_bytes(visibility) + b"\n"
    for name, blob in (("plan.json", plan_blob),
                       ("visibility.json", visibility_blob)):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            try:
                existing = open(path, "rb").read()
            except OSError as err:
                raise V2BError(f"cannot read staged S5 input: {err}") from err
            if existing != blob:
                raise V2BError(f"staged S5 {name} disagrees")
        else:
            _write_new_bytes(path, blob)
    return sha256_bytes(plan_blob), sha256_bytes(visibility_blob)


def _run_lock_path(run_dir):
    return os.path.join(run_dir, "run.lock")


def run_four_phase(plan, visibility, original, candidate, run_dir, *,
                   allow_unisolated_test=False, nonce_sequence=None):
    """Run or revalidate one exact four-phase invocation.

    ``nonce_sequence`` is accepted only by the explicit unisolated test
    backend.  Production obtains a fresh 256-bit nonce independently for each
    fresh process.
    """
    validate_plan_sources(
        plan, visibility, original, candidate,
        allow_unisolated_test=allow_unisolated_test)
    validate_visibility_artifact(visibility, live_files=True)
    if nonce_sequence is not None and (
            not allow_unisolated_test or len(nonce_sequence) != len(PHASES)
            or any(not _hex(nonce) for nonce in nonce_sequence)):
        raise V2BError("S5 fixed nonce sequence is malformed/test-only")
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    lock_path = _run_lock_path(run_dir)
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as err:
        raise V2BError(f"cannot open S5 run lock: {err}") from err
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if os.path.exists(_summary_path(run_dir)):
            return validate_summary(
                run_dir, plan, visibility, original, candidate,
                allow_unisolated_test=allow_unisolated_test)
        plan_sha, visibility_sha = _stage_run_inputs(
            run_dir, plan, visibility)
        launcher = VisibilityLauncher(
            visibility, plan,
            allow_unisolated_test=allow_unisolated_test)
        phases = {}

        def execute(phase, bundle=None):
            index = PHASES.index(phase)
            envelope = _run_phase(
                run_dir, plan, visibility, launcher, phase, original,
                candidate, plan_sha, visibility_sha, bundle=bundle,
                nonce_for_test=(nonce_sequence[index]
                                if nonce_sequence is not None else None))
            phases[phase] = envelope
            return envelope

        def finalize(classification, passed, baseline_bundle=None,
                     candidate_bundle=None):
            summary = _make_summary(
                plan, plan_sha, visibility_sha, classification, passed,
                phases, baseline_bundle, candidate_bundle)
            summary_sha = _write_new_json(_summary_path(run_dir), summary)
            validated = validate_summary(
                run_dir, plan, visibility, original, candidate,
                allow_unisolated_test=allow_unisolated_test)
            validated["reused"] = False
            validated["summarySha256"] = summary_sha
            return validated

        baseline_target = execute("baseline-target")
        classification = baseline_target["terminal"]["classification"]
        baseline_bundle = _phase_bundle(baseline_target)
        if classification != "phase-verified" or baseline_bundle is None:
            if classification == "baseline-ineligible":
                return finalize("baseline-ineligible", None)
            if classification == "harness-invalid":
                return finalize("harness-invalid", None)
            raise V2BError(
                "baseline target did not yield valid outcome evidence")

        baseline_suffix = execute("baseline-suffix", baseline_bundle)
        classification = baseline_suffix["terminal"]["classification"]
        if classification != "phase-verified":
            if classification == "baseline-ineligible":
                return finalize(
                    "baseline-ineligible", None, baseline_bundle)
            if classification == "harness-invalid":
                return finalize(
                    "harness-invalid", None, baseline_bundle)
            raise V2BError(
                "baseline suffix did not yield valid outcome evidence")

        candidate_target = execute("candidate-target")
        classification = candidate_target["terminal"]["classification"]
        candidate_bundle = _phase_bundle(candidate_target)
        if classification != "phase-verified" or candidate_bundle is None:
            if classification in (
                    "verification-failure", "candidate-timeout",
                    "candidate-output-limit", "candidate-terminated"):
                return finalize(
                    classification, 0, baseline_bundle, candidate_bundle)
            raise V2BError(
                "candidate target did not yield valid outcome evidence")

        baseline_certificate = target_certificate(
            baseline_bundle, plan["targetName"])
        candidate_certificate = target_certificate(
            candidate_bundle, plan["targetName"])
        # The target header is byte-identical, so exact canonical type equality
        # is the conservative host gate before suffix replay.  A future wider
        # header surface would need the already-designed in-Lean Kernel.isDefEq
        # certificate check rather than weakening this comparison.
        if (baseline_certificate["kind"] != candidate_certificate["kind"]
                or baseline_certificate["levelParams"] !=
                candidate_certificate["levelParams"]
                or baseline_certificate["typeExpression"] !=
                candidate_certificate["typeExpression"]):
            return finalize(
                "candidate-type-drift", 0, baseline_bundle,
                candidate_bundle)

        candidate_suffix = execute("candidate-suffix", candidate_bundle)
        classification = candidate_suffix["terminal"]["classification"]
        if classification == "phase-verified":
            final_classification, passed = "verified-pass", 1
        elif classification in (
                "verification-failure", "candidate-timeout",
                "candidate-output-limit", "candidate-terminated"):
            final_classification, passed = classification, 0
        else:
            raise V2BError(
                "candidate suffix did not yield valid outcome evidence")
        return finalize(
            final_classification, passed, baseline_bundle,
            candidate_bundle)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def load_visibility(path, *, live_files=False):
    value, digest = _read_strict_json(path, VISIBILITY_SCHEMA)
    validate_visibility_artifact(value, live_files=live_files)
    return value, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--visibility", required=True)
    parser.add_argument("--original", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    try:
        plan, _ = _read_strict_json(args.plan, PLAN_SCHEMA, _PLAN_KEYS)
        validate_plan(plan)
        visibility, _ = load_visibility(
            args.visibility, live_files=True)
        original = open(args.original, "rb").read()
        candidate = open(args.candidate, "rb").read()
        result = run_four_phase(
            plan, visibility, original, candidate, args.run_dir)
    except (OSError, V2BError) as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[v2b-s5-four-phase] {result['summary']['classification']} "
          f"{result['summarySha256'][:12]}")


if __name__ == "__main__":
    main()


__all__ = [
    "FOUR_PHASE_CONTRACT", "FOUR_PHASE_CONTRACT_SHA256", "LaunchSpec",
    "PHASES", "PLAN_SCHEMA", "RESOURCE_LIMITS", "RUN_SUMMARY_SCHEMA",
    "SANDBOX_CONTRACT", "SANDBOX_CONTRACT_SHA256", "VISIBILITY_SCHEMA",
    "VisibilityLauncher", "bind_plan",
    "build_plan", "load_visibility", "parse_transcript_prefix",
    "phase_manifest", "run_four_phase", "target_certificate",
    "validate_bundle", "validate_plan", "validate_plan_sources",
    "validate_summary", "validate_visibility_artifact",
]
