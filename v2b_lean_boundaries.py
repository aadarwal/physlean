#!/usr/bin/env python3
"""Prospective parser-witnessed Lean body-boundary artifact chain.

The frozen lexical split in the v3 Lean extraction is scientifically
unsafe (a real `:=` can occur inside a declaration type, e.g.
`def f : let n := 1; Nat := 0`), so raw v3 header_bytes/body_bytes/
split_kind are DIAGNOSTIC ONLY here.  This module owns the pure Python
half of the remediation:

  PLANNER   v3 extraction + explicit per-source setup mapping
            -> driver manifest {v2b_lean_boundary_manifest_v1}
  CONSUMER  manifest + driver result {v2b_lean_boundary_result_v1}
            -> boundary artifact {v2b_lean_body_boundaries_v1}

The per-module Lean driver (lean_drivers/V2BLeanBoundaryAudit.lean,
separately owned; sentinel machinery from V2BParseCommand.lean)
resolves the earliest sentinel-valid exact canonical token from
{:=, where, |} per unique span, or reports one of the FROZEN unsplit
reasons.  Because exact ModuleSetup differs per source and execution
must be parallel/requeue-safe, the driver consumes ONE per-module
manifest ({v2b_lean_boundary_driver_manifest_v1}) and emits
marker-prefixed module + span records
({v2b_lean_boundary_driver_output_v1}); this module builds those
per-module manifests from the global plan, strictly parses the
transcripts, and aggregates them back into the corpus result in GLOBAL
manifest order.  Every v3 extraction identity appears exactly once in
the boundary artifact; execution is deduplicated by (module,
source_sha256, start_byte, end_byte) with a stable SHA span id, sorted
members, and an identical old-split tuple required within a shared
span.

Effective resolved row: header_bytes = H - start, body_bytes = end - H,
split_kind = delimiter.  Unresolved row: header_bytes = end - start,
body_bytes = 0, split_kind = null, status recorded.  The consumer
recomputes a 64-hex invocation binding from the exact canonical
manifest plus the referenced file bytes and byte-checks every resolved
delimiter against the live source, so a fabricated or stale driver
result cannot bind.  Assembly integration is deliberately NOT here.
"""
import argparse
import copy
import json
import os
import sys

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, artifact_binding, identity_key,
                        load_json, sha256_bytes, sha256_file, sha256_json,
                        sha256_sorted_json, validate_identity,
                        write_new_json)
from v2b_neardup import LEAN_EXTRACT_SCHEMA

BOUNDARY_MANIFEST_SCHEMA = "v2b_lean_boundary_manifest_v1"
BOUNDARY_RESULT_SCHEMA = "v2b_lean_boundary_result_v1"
BOUNDARIES_SCHEMA = "v2b_lean_body_boundaries_v1"
DRIVER_MANIFEST_SCHEMA = "v2b_lean_boundary_driver_manifest_v1"
DRIVER_OUTPUT_SCHEMA = "v2b_lean_boundary_driver_output_v1"
BOUNDARY_MARKER = "@@V2B_LEAN_BOUNDARY@@"
CANONICAL_DELIMITERS = (":=", "where", "|")
OLD_SPLIT_KINDS = (":=", "where", "|", None)
UNSPLIT_REASONS = ("no-canonical-candidate",
                   "no-sentinel-valid-candidate",
                   "not-exact-command-span")
RESULT_ROW_KEYS = frozenset((
    "span_id", "status", "delimiter", "h_byte", "reason", "syntax_kind",
    "n_candidate_starts_total", "n_tested", "n_untested_after_choice",
    "rejected_starts"))
RESULT_TOP_KEYS = frozenset((
    "schema", "marker", "manifest_sha256", "driver_sha256", "toolchain",
    "invocation_sha256", "n_modules", "n_spans", "module_runs",
    "module_runs_sha256", "results", "runtime", "runtime_sha256",
    "generator"))
DRIVER_MANIFEST_KEYS = frozenset((
    "schema", "invocationBinding", "originalFile", "moduleSetupFile",
    "moduleName", "optionOverrides", "spans"))
DRIVER_MANIFEST_UNBOUND_KEYS = DRIVER_MANIFEST_KEYS - {
    "invocationBinding"}
DRIVER_MANIFEST_SPAN_KEYS = frozenset(("id", "startByte", "endByte"))
DRIVER_MANIFEST_OPTION_KEYS = frozenset(("name", "value"))
DRIVER_MODULE_RECORD_KEYS = frozenset((
    "schema", "record_type", "invocation_binding", "module_name",
    "n_spans", "n_commands_parsed", "trusted_original_commands_elaborated",
    "sentinels_elaborated"))
DRIVER_SPAN_RECORD_KEYS = frozenset((
    "schema", "record_type", "span_id", "status", "reason", "start_byte",
    "end_byte", "header_end_byte", "delimiter", "syntax_kind",
    "n_candidate_starts_total", "n_tested", "n_untested_after_choice",
    "rejected_starts", "sentinels_elaborated"))
MODULE_RUN_KEYS = frozenset((
    "module_name", "invocation_binding", "n_spans", "manifest_sha256",
    "stdout_sha256", "stderr_sha256", "exit_code", "rows_sha256",
    "evidence_sha256"))
RUN_ENVELOPE_KEYS = frozenset((
    "manifest", "manifest_sha256", "stdout", "stderr", "exit_code",
    "evidence_sha256"))
RUNTIME_KEYS = frozenset((
    "setup_index", "corpus_root", "corpus_git_sha", "toolchain", "lean",
    "driver", "cwd", "argv_template", "environment",
    "environment_sha256"))
RUNTIME_SETUP_KEYS = frozenset(("path", "sha256", "schema"))
RUNTIME_LEAN_KEYS = frozenset(("path", "sha256", "version"))
RUNTIME_DRIVER_KEYS = frozenset(("path", "sha256"))
RUNTIME_ENV_KEYS = frozenset((
    "ELAN_HOME", "ELAN_TOOLCHAIN", "LANG", "LC_ALL", "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH", "LIBRARY_PATH", "LEAN_CC", "LEAN_NUM_THREADS",
    "LEAN_PATH", "LEAN_SRC_PATH", "PATH", "TMPDIR", "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME"))
GENERATOR_KEYS = frozenset((
    "source_commit", "source_tree_hash", "program"))
GLOBAL_MANIFEST_KEYS = frozenset((
    "schema", "marker", "repo", "extraction", "n_spans",
    "n_identities", "spans"))
GLOBAL_MANIFEST_SPAN_KEYS = frozenset((
    "span_id", "module", "source", "source_sha256", "setup",
    "setup_sha256", "start_byte", "end_byte", "old_split", "members"))
OLD_SPLIT_KEYS = frozenset(("header_bytes", "body_bytes", "split_kind"))
EXTRACTION_BINDING_KEYS = frozenset(("sha256", "schema"))


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _nonneg(value, label):
    if type(value) is not int or value < 0:
        raise V2BError(f"{label} must be a nonnegative int: {value!r}")
    return value


def span_id_of(module, source_sha256, start_byte, end_byte):
    """Stable SHA identity of one unique executable span."""
    return sha256_json([BOUNDARY_MARKER, module, source_sha256,
                        start_byte, end_byte])


def _resolve_setup(setup_map, source):
    """Explicit source -> setup path; silent guessing is forbidden."""
    if callable(setup_map):
        setup = setup_map(source)
    elif isinstance(setup_map, dict):
        setup = setup_map.get(source)
    else:
        raise V2BError("setup_map must be a mapping or callable")
    if not isinstance(setup, str) or not setup:
        raise V2BError(f"no explicit module setup for source {source!r}")
    return setup


def build_boundary_manifest(extraction_path, setup_map):
    """Deduplicated driver worklist from one v3 Lean extraction."""
    extraction_binding, extraction = artifact_binding(extraction_path)
    if extraction.get("schema") != LEAN_EXTRACT_SCHEMA:
        raise V2BError("boundary planner requires a v3 Lean extraction")
    repo = extraction.get("repo")
    if not isinstance(repo, str) or not repo:
        raise V2BError("extraction lacks a repo tag")
    spans = {}
    n_identities = 0
    seen_identity = set()
    for file_row in extraction.get("files", []):
        module = file_row.get("module")
        source = file_row.get("source")
        source_sha = file_row.get("source_sha256")
        decls = file_row.get("decls")
        if not isinstance(module, str) or not module \
                or not isinstance(source, str) or not source \
                or not _hex(source_sha) or not isinstance(decls, dict):
            raise V2BError(f"malformed extraction file row: {module!r}")
        if sha256_file(source) != source_sha:
            raise V2BError(f"live source hash drift: {source}")
        setup = _resolve_setup(setup_map, source)
        setup_sha = sha256_file(setup)
        for decl_name, decl in decls.items():
            identity = validate_identity("lean", [module, decl_name])
            key = identity_key("lean", identity)
            if key in seen_identity:
                raise V2BError(f"duplicate extraction identity {key}")
            seen_identity.add(key)
            n_identities += 1
            start = _nonneg(decl.get("start_byte"), "start_byte")
            end = _nonneg(decl.get("end_byte"), "end_byte")
            if start >= end:
                raise V2BError(f"empty declaration span: {key}")
            header = decl.get("header_bytes")
            body = decl.get("body_bytes")
            kind = decl.get("split_kind")
            if kind not in OLD_SPLIT_KINDS:
                raise V2BError(f"unknown old split kind {kind!r}: {key}")
            _nonneg(header, "header_bytes")
            _nonneg(body, "body_bytes")
            if header + body != end - start:
                raise V2BError(f"old split does not partition span: {key}")
            old_split = dict(header_bytes=header, body_bytes=body,
                             split_kind=kind)
            sid = span_id_of(module, source_sha, start, end)
            row = spans.get(sid)
            if row is None:
                spans[sid] = dict(
                    span_id=sid, module=module, source=source,
                    source_sha256=source_sha, setup=setup,
                    setup_sha256=setup_sha, start_byte=start,
                    end_byte=end, old_split=old_split,
                    members=[list(identity)])
            else:
                if row["old_split"] != old_split:
                    raise V2BError(
                        f"identities sharing span {sid[:12]} disagree on "
                        f"the old split tuple")
                row["members"].append(list(identity))
    if not spans:
        raise V2BError("extraction contains no Lean declaration spans")
    ordered = []
    for sid in sorted(spans):
        row = dict(spans[sid])
        row["members"] = sorted(row["members"])
        ordered.append(row)
    return dict(
        schema=BOUNDARY_MANIFEST_SCHEMA, marker=BOUNDARY_MARKER,
        repo=repo,
        extraction=dict(sha256=extraction_binding["sha256"],
                        schema=LEAN_EXTRACT_SCHEMA),
        n_spans=len(ordered), n_identities=n_identities,
        spans=ordered)


def validate_boundary_manifest(manifest, live_files=True):
    """Validate the exact deduplicated global plan and identity coverage."""
    if not isinstance(manifest, dict) or set(manifest) not in (
            GLOBAL_MANIFEST_KEYS, GLOBAL_MANIFEST_KEYS | {"generator"}) \
            or manifest.get("schema") != BOUNDARY_MANIFEST_SCHEMA \
            or manifest.get("marker") != BOUNDARY_MARKER \
            or not isinstance(manifest.get("repo"), str) \
            or not manifest["repo"]:
        raise V2BError("global boundary manifest schema/key drift")
    extraction = manifest.get("extraction")
    if not isinstance(extraction, dict) \
            or set(extraction) != EXTRACTION_BINDING_KEYS \
            or extraction.get("schema") != LEAN_EXTRACT_SCHEMA \
            or not _hex(extraction.get("sha256")):
        raise V2BError("global boundary extraction binding drift")
    if "generator" in manifest:
        generator = manifest["generator"]
        if not isinstance(generator, dict) or set(generator) != GENERATOR_KEYS \
                or not _hex(generator.get("source_commit"), 40) \
                or not _hex(generator.get("source_tree_hash")) \
                or generator.get("program") != "v2b_lean_boundaries.py":
            raise V2BError("global boundary generator binding drift")
    spans = manifest.get("spans")
    if not isinstance(spans, list) or not spans \
            or type(manifest.get("n_spans")) is not int \
            or manifest["n_spans"] != len(spans) \
            or type(manifest.get("n_identities")) is not int \
            or manifest["n_identities"] < 1:
        raise V2BError("global boundary span/count drift")
    ids = []
    identity_keys = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict) \
                or set(span) != GLOBAL_MANIFEST_SPAN_KEYS:
            raise V2BError(f"global boundary span[{index}] key drift")
        module, source, setup = (span.get("module"), span.get("source"),
                                 span.get("setup"))
        if not all(isinstance(value, str) and value
                   for value in (module, source, setup)) \
                or not _hex(span.get("source_sha256")) \
                or not _hex(span.get("setup_sha256")):
            raise V2BError(f"global boundary span[{index}] context drift")
        start = _nonneg(span.get("start_byte"), "manifest start_byte")
        end = _nonneg(span.get("end_byte"), "manifest end_byte")
        if start >= end or span.get("span_id") != span_id_of(
                module, span["source_sha256"], start, end):
            raise V2BError(f"global boundary span[{index}] identity drift")
        old = span.get("old_split")
        if not isinstance(old, dict) or set(old) != OLD_SPLIT_KEYS \
                or old.get("split_kind") not in OLD_SPLIT_KINDS:
            raise V2BError(f"global boundary span[{index}] old split drift")
        header = _nonneg(old.get("header_bytes"), "old header_bytes")
        body = _nonneg(old.get("body_bytes"), "old body_bytes")
        if header + body != end - start:
            raise V2BError(f"global boundary span[{index}] split partition")
        members = span.get("members")
        if not isinstance(members, list) or not members \
                or members != sorted(members):
            raise V2BError(f"global boundary span[{index}] member order drift")
        local = []
        for member in members:
            identity = validate_identity("lean", member)
            if identity[0] != module:
                raise V2BError(f"global boundary span[{index}] module join")
            local.append(identity_key("lean", identity))
        if len(local) != len(set(local)):
            raise V2BError(f"global boundary span[{index}] duplicate member")
        identity_keys.extend(local)
        ids.append(span["span_id"])
        if live_files and (sha256_file(source) != span["source_sha256"]
                           or sha256_file(setup) != span["setup_sha256"]):
            raise V2BError(f"global boundary span[{index}] live-byte drift")
    if ids != sorted(ids) or len(ids) != len(set(ids)) \
            or len(identity_keys) != manifest["n_identities"] \
            or len(identity_keys) != len(set(identity_keys)):
        raise V2BError("global boundary order/identity coverage drift")
    return spans


def compute_invocation_sha256(manifest_file_sha256, driver_sha256,
                              manifest_value):
    """64-hex binding over the exact canonical manifest and every
    referenced file's bytes, in exact manifest order.

    The driver must compute the identical value; the consumer recomputes
    it from live bytes, so a result produced against different sources,
    setups, driver code, or manifest cannot bind."""
    if not _hex(manifest_file_sha256) or not _hex(driver_sha256):
        raise V2BError("invocation binding needs 64-hex file hashes")
    rows = []
    digest_cache = {}
    for span in manifest_value.get("spans", []):
        def digest(path):
            if path not in digest_cache:
                digest_cache[path] = sha256_file(path)
            return digest_cache[path]

        live_source = digest(span["source"])
        live_setup = digest(span["setup"])
        if live_source != span.get("source_sha256") \
                or live_setup != span.get("setup_sha256"):
            raise V2BError(f"referenced file bytes drifted for span "
                           f"{span.get('span_id', '?')[:12]}")
        rows.append([span["span_id"], live_source, live_setup])
    return sha256_json([BOUNDARY_MARKER, manifest_file_sha256,
                        driver_sha256, rows])


def driver_invocation_binding(manifest, driver_sha256, toolchain):
    """Bind one exact per-module manifest plus all executable input bytes."""
    if not isinstance(manifest, dict) or set(manifest) not in (
            DRIVER_MANIFEST_UNBOUND_KEYS, DRIVER_MANIFEST_KEYS):
        raise V2BError("boundary driver manifest key drift")
    if not _hex(driver_sha256) or not isinstance(toolchain, str) \
            or not toolchain.strip():
        raise V2BError("driver binding lacks driver/toolchain identity")
    unbound = copy.deepcopy(manifest)
    unbound.pop("invocationBinding", None)
    source = unbound.get("originalFile")
    setup = unbound.get("moduleSetupFile")
    if not isinstance(source, str) or not source \
            or not isinstance(setup, str) or not setup:
        raise V2BError("driver manifest source/setup path is empty")
    return sha256_json([
        "v2b-lean-boundary-driver-invocation-v1", unbound,
        sha256_file(source), sha256_file(setup), driver_sha256,
        toolchain.strip()])


def bind_driver_manifest(manifest, driver_sha256, toolchain):
    if not isinstance(manifest, dict) \
            or set(manifest) != DRIVER_MANIFEST_UNBOUND_KEYS:
        raise V2BError("unbound boundary driver manifest key drift")
    bound = copy.deepcopy(manifest)
    bound["invocationBinding"] = driver_invocation_binding(
        bound, driver_sha256, toolchain)
    return bound


def canonical_driver_manifest_bytes(manifest):
    """Exact order-preserving UTF-8 encoding written for the Lean driver.

    ``sha256_json`` is intentionally order-preserving in this repository, so
    sorting object keys at publication would change the invocation preimage
    when the manifest is reloaded.  The validated producer order is itself
    part of the frozen wire format.
    """
    if not isinstance(manifest, dict):
        raise V2BError("boundary driver manifest is not an object")
    try:
        return (json.dumps(manifest, sort_keys=False, ensure_ascii=False,
                           allow_nan=False, separators=(",", ":")) +
                "\n").encode("utf-8")
    except (TypeError, ValueError) as err:
        raise V2BError(f"cannot encode boundary driver manifest: {err}") \
            from err


def _validate_driver_manifest(manifest, driver_sha256, toolchain):
    if not isinstance(manifest, dict) or set(manifest) != \
            DRIVER_MANIFEST_KEYS \
            or manifest.get("schema") != DRIVER_MANIFEST_SCHEMA:
        raise V2BError("boundary driver manifest schema/key drift")
    if manifest.get("invocationBinding") != driver_invocation_binding(
            manifest, driver_sha256, toolchain):
        raise V2BError("boundary driver manifest invocation drift")
    for key in ("originalFile", "moduleSetupFile", "moduleName"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise V2BError(f"boundary driver manifest {key} is empty")
    options = manifest.get("optionOverrides")
    if not isinstance(options, list):
        raise V2BError("boundary driver options are not a list")
    if options:
        raise V2BError("boundary audit optionOverrides must be empty")
    option_names = []
    for option in options:
        if not isinstance(option, dict) \
                or set(option) != DRIVER_MANIFEST_OPTION_KEYS \
                or not isinstance(option.get("name"), str) \
                or not option["name"] \
                or not isinstance(option.get("value"), str) \
                or not option["value"] \
                or option["name"] == "Elab.async":
            raise V2BError("boundary driver option override is malformed")
        option_names.append(option["name"])
    if len(set(option_names)) != len(option_names):
        raise V2BError("boundary driver option overrides are duplicated")
    spans = manifest.get("spans")
    if not isinstance(spans, list) or not spans:
        raise V2BError("boundary driver span list is empty")
    previous = None
    ids = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict) \
                or set(span) != DRIVER_MANIFEST_SPAN_KEYS \
                or not isinstance(span.get("id"), str) or not span["id"]:
            raise V2BError(f"boundary driver span[{index}] is malformed")
        start = _nonneg(span.get("startByte"), "driver startByte")
        end = _nonneg(span.get("endByte"), "driver endByte")
        if start >= end or previous is not None \
                and (start, end) <= previous:
            raise V2BError("boundary driver spans are not strictly ordered")
        previous = (start, end)
        ids.append(span["id"])
    if len(ids) != len(set(ids)):
        raise V2BError("boundary driver span ids are duplicated")
    return spans


def build_driver_manifests(global_manifest, driver_path, toolchain):
    """Project the global hash-ordered plan into exact source-ordered jobs."""
    validate_boundary_manifest(global_manifest)
    driver_sha256 = sha256_file(driver_path)
    groups = {}
    for index, span in enumerate(global_manifest.get("spans", [])):
        if not isinstance(span, dict):
            raise V2BError(f"global boundary span[{index}] is malformed")
        module = span.get("module")
        source = span.get("source")
        setup = span.get("setup")
        if not all(isinstance(value, str) and value
                   for value in (module, source, setup)):
            raise V2BError(f"global boundary span[{index}] lacks context")
        group = groups.setdefault(module, dict(source=source, setup=setup,
                                                spans=[]))
        if group["source"] != source or group["setup"] != setup:
            raise V2BError(f"module {module} maps to multiple sources/setups")
        group["spans"].append(dict(
            id=span["span_id"], startByte=span["start_byte"],
            endByte=span["end_byte"]))
    if not groups:
        raise V2BError("global boundary manifest has no module groups")
    manifests = {}
    for module in sorted(groups):
        group = groups[module]
        spans = sorted(group["spans"],
                       key=lambda row: (row["startByte"], row["endByte"],
                                        row["id"]))
        unbound = dict(
            schema=DRIVER_MANIFEST_SCHEMA,
            originalFile=group["source"],
            moduleSetupFile=group["setup"], moduleName=module,
            optionOverrides=[], spans=spans)
        bound = bind_driver_manifest(unbound, driver_sha256, toolchain)
        _validate_driver_manifest(bound, driver_sha256, toolchain)
        manifests[module] = bound
    return manifests


def _validate_truth_table(status, reason, delimiter, h_byte, syntax_kind,
                          total, tested, untested, rejected, start, end,
                          where):
    """The driver's frozen status/reason/evidence truth table."""
    for count, label in ((total, "n_candidate_starts_total"),
                         (tested, "n_tested"),
                         (untested, "n_untested_after_choice")):
        if type(count) is not int or count < 0:
            raise V2BError(f"{label} malformed at {where}")
    if not isinstance(rejected, list) \
            or any(type(value) is not int for value in rejected) \
            or rejected != sorted(rejected) \
            or len(set(rejected)) != len(rejected) \
            or any(not start < value < end for value in rejected):
        raise V2BError(f"rejected_starts malformed at {where}")
    if status == "resolved":
        if reason is not None or delimiter not in CANONICAL_DELIMITERS \
                or type(h_byte) is not int or not start < h_byte < end \
                or not isinstance(syntax_kind, str) or not syntax_kind \
                or tested + untested != total \
                or len(rejected) != tested - 1 \
                or tested < 1 \
                or any(value >= h_byte for value in rejected):
            raise V2BError(f"resolved truth-table violation at {where}")
    elif status == "unsplit":
        if reason not in UNSPLIT_REASONS or delimiter is not None \
                or h_byte is not None:
            raise V2BError(f"unsplit truth-table violation at {where}")
        if reason == "no-canonical-candidate":
            if not isinstance(syntax_kind, str) or not syntax_kind \
                    or total or tested or untested or rejected:
                raise V2BError(f"no-canonical truth-table violation at "
                               f"{where}")
        elif reason == "no-sentinel-valid-candidate":
            if not isinstance(syntax_kind, str) or not syntax_kind \
                    or total < 1 or tested != total or untested != 0 \
                    or len(rejected) != total:
                raise V2BError(f"no-sentinel truth-table violation at "
                               f"{where}")
        else:                              # not-exact-command-span
            if syntax_kind is not None \
                    or total or tested or untested or rejected:
                raise V2BError(f"not-exact-command truth-table violation "
                               f"at {where}")
    else:
        raise V2BError(f"unknown boundary status {status!r} at {where}")


def parse_driver_stdout(stdout, driver_manifest, driver_sha256, toolchain):
    """Strictly consume one complete marker-only per-module transcript."""
    spans = _validate_driver_manifest(driver_manifest, driver_sha256,
                                      toolchain)
    if not isinstance(stdout, str):
        raise V2BError("boundary driver stdout must be text")

    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(f"duplicate boundary driver JSON key {key!r}")
            value[key] = item
        return value

    def no_nonfinite(value):
        raise V2BError(f"non-finite boundary driver number {value}")

    records = []
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.startswith(BOUNDARY_MARKER):
            continue
        payload = line[len(BOUNDARY_MARKER):]
        try:
            record = json.loads(payload, object_pairs_hook=no_duplicates,
                                parse_constant=no_nonfinite)
        except (json.JSONDecodeError, V2BError) as err:
            raise V2BError(f"malformed marked boundary record at line "
                           f"{line_number}: {err}") from err
        if not isinstance(record, dict):
            raise V2BError("marked boundary record is not an object")
        records.append(record)
    if len(records) != len(spans) + 1:
        raise V2BError("boundary transcript record count drift")
    module = records[0]
    if set(module) != DRIVER_MODULE_RECORD_KEYS \
            or module.get("schema") != DRIVER_OUTPUT_SCHEMA \
            or module.get("record_type") != "module" \
            or module.get("invocation_binding") != \
            driver_manifest["invocationBinding"] \
            or module.get("module_name") != driver_manifest["moduleName"] \
            or type(module.get("n_spans")) is not int \
            or module.get("n_spans") != len(spans) \
            or type(module.get("n_commands_parsed")) is not int \
            or module["n_commands_parsed"] < 1 \
            or module.get("trusted_original_commands_elaborated") is not True \
            or module.get("sentinels_elaborated") is not False:
        raise V2BError("boundary driver module record drift")
    rows = []
    for index, (record, span) in enumerate(zip(records[1:], spans)):
        if set(record) != DRIVER_SPAN_RECORD_KEYS \
                or record.get("schema") != DRIVER_OUTPUT_SCHEMA \
                or record.get("record_type") != "span" \
                or record.get("span_id") != span["id"] \
                or type(record.get("start_byte")) is not int \
                or record.get("start_byte") != span["startByte"] \
                or type(record.get("end_byte")) is not int \
                or record.get("end_byte") != span["endByte"] \
                or record.get("sentinels_elaborated") is not False:
            raise V2BError(f"boundary driver span record drift at row[{index}]")
        _validate_truth_table(
            record.get("status"), record.get("reason"),
            record.get("delimiter"), record.get("header_end_byte"),
            record.get("syntax_kind"),
            record.get("n_candidate_starts_total"),
            record.get("n_tested"),
            record.get("n_untested_after_choice"),
            record.get("rejected_starts"), span["startByte"],
            span["endByte"], f"driver span[{index}]")
        rows.append(dict(
            span_id=span["id"], status=record["status"],
            delimiter=record["delimiter"],
            h_byte=record["header_end_byte"], reason=record["reason"],
            syntax_kind=record["syntax_kind"],
            n_candidate_starts_total=record["n_candidate_starts_total"],
            n_tested=record["n_tested"],
            n_untested_after_choice=record["n_untested_after_choice"],
            rejected_starts=record["rejected_starts"]))
    return dict(module=module, rows=rows)


def aggregate_driver_runs(global_manifest_path, driver_path, toolchain,
                          runs):
    """Aggregate validated per-module run bytes into global plan order.

    ``runs`` maps each module to its semantic manifest, exact raw manifest
    digest, stdout/stderr text, exit code, and immutable run-evidence digest.
    The runner may load these bytes from immutable files; this pure layer
    binds their exact hashes and rejects any missing, extra, or failed run.
    """
    manifest_binding, global_manifest = artifact_binding(
        global_manifest_path, BOUNDARY_MANIFEST_SCHEMA)
    driver_sha256 = sha256_file(driver_path)
    expected = build_driver_manifests(global_manifest, driver_path,
                                      toolchain)
    if not isinstance(runs, dict) or set(runs) != set(expected):
        raise V2BError("boundary module-run membership drift")
    by_span = {}
    module_rows = []
    for module in sorted(expected):
        run = runs[module]
        if not isinstance(run, dict) or set(run) != RUN_ENVELOPE_KEYS:
            raise V2BError(f"boundary run envelope drift for {module}")
        if run["manifest"] != expected[module]:
            raise V2BError(f"boundary driver manifest drift for {module}")
        if type(run["exit_code"]) is not int or run["exit_code"] != 0 \
                or not isinstance(run["stdout"], str) \
                or not isinstance(run["stderr"], str) \
                or not _hex(run["manifest_sha256"]) \
                or not _hex(run["evidence_sha256"]):
            raise V2BError(f"boundary driver process failed for {module}")
        parsed = parse_driver_stdout(
            run["stdout"], run["manifest"], driver_sha256, toolchain)
        for row in parsed["rows"]:
            if row["span_id"] in by_span:
                raise V2BError(f"duplicate boundary driver span "
                               f"{row['span_id']}")
            by_span[row["span_id"]] = row
        module_rows.append(dict(
            module_name=module,
            invocation_binding=run["manifest"]["invocationBinding"],
            n_spans=len(parsed["rows"]),
            manifest_sha256=run["manifest_sha256"],
            stdout_sha256=sha256_bytes(run["stdout"].encode("utf-8")),
            stderr_sha256=sha256_bytes(run["stderr"].encode("utf-8")),
            exit_code=run["exit_code"],
            rows_sha256=sha256_sorted_json(parsed["rows"]),
            evidence_sha256=run["evidence_sha256"]))
    expected_ids = [span["span_id"] for span in global_manifest["spans"]]
    if set(by_span) != set(expected_ids) or len(by_span) != len(expected_ids):
        raise V2BError("aggregated boundary span membership drift")
    rows = [by_span[span_id] for span_id in expected_ids]
    invocation = compute_invocation_sha256(
        manifest_binding["sha256"], driver_sha256, global_manifest)
    return dict(
        schema=BOUNDARY_RESULT_SCHEMA, marker=BOUNDARY_MARKER,
        manifest_sha256=manifest_binding["sha256"],
        driver_sha256=driver_sha256, toolchain=toolchain,
        invocation_sha256=invocation,
        n_modules=len(module_rows), n_spans=len(rows),
        module_runs=module_rows,
        module_runs_sha256=sha256_sorted_json(module_rows), results=rows)


def _validate_result(result, manifest_file_sha256, driver_sha256,
                     manifest_value, driver_path):
    if result.get("schema") != BOUNDARY_RESULT_SCHEMA \
            or result.get("marker") != BOUNDARY_MARKER \
            or set(result) != RESULT_TOP_KEYS:
        raise V2BError("boundary driver result schema/key drift")
    if result.get("manifest_sha256") != manifest_file_sha256:
        raise V2BError("driver result is not bound to this manifest")
    if result.get("driver_sha256") != driver_sha256:
        raise V2BError("driver result is not bound to this driver source")
    if not isinstance(result.get("toolchain"), str) \
            or not result["toolchain"].strip():
        raise V2BError("driver result lacks a toolchain identity")
    runtime = result.get("runtime")
    setup_binding = runtime.get("setup_index") \
        if isinstance(runtime, dict) else None
    lean = runtime.get("lean") if isinstance(runtime, dict) else None
    driver = runtime.get("driver") if isinstance(runtime, dict) else None
    environment = runtime.get("environment") \
        if isinstance(runtime, dict) else None
    if not isinstance(runtime, dict) or set(runtime) != RUNTIME_KEYS \
            or result.get("runtime_sha256") != sha256_sorted_json(runtime) \
            or not isinstance(setup_binding, dict) \
            or set(setup_binding) != RUNTIME_SETUP_KEYS \
            or not isinstance(setup_binding.get("path"), str) \
            or not setup_binding["path"] \
            or not _hex(setup_binding.get("sha256")) \
            or setup_binding.get("schema") != \
            "v2b_lean_setup_index_v1" \
            or not isinstance(lean, dict) or set(lean) != RUNTIME_LEAN_KEYS \
            or not isinstance(lean.get("path"), str) or not lean["path"] \
            or not _hex(lean.get("sha256")) \
            or not isinstance(lean.get("version"), str) \
            or not lean["version"] \
            or not isinstance(driver, dict) \
            or set(driver) != RUNTIME_DRIVER_KEYS \
            or not isinstance(driver.get("path"), str) or not driver["path"] \
            or os.path.abspath(driver["path"]) != os.path.abspath(driver_path) \
            or driver.get("sha256") != driver_sha256 \
            or runtime.get("toolchain") != result["toolchain"] \
            or not isinstance(runtime.get("corpus_root"), str) \
            or not runtime["corpus_root"] \
            or not _hex(runtime.get("corpus_git_sha"), 40) \
            or runtime.get("cwd") != runtime["corpus_root"] \
            or not isinstance(runtime.get("argv_template"), list) \
            or runtime["argv_template"] != [
                lean["path"], "--run", driver["path"],
                "<module-manifest.json>"] \
            or not isinstance(environment, dict) \
            or set(environment) != RUNTIME_ENV_KEYS \
            or environment.get("ELAN_TOOLCHAIN") != result["toolchain"] \
            or runtime.get("environment_sha256") != \
            sha256_sorted_json(environment):
        raise V2BError("boundary runtime binding drift")
    generator = result.get("generator")
    if not isinstance(generator, dict) or set(generator) != GENERATOR_KEYS \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")) \
            or generator.get("program") != \
            "run_v2b_lean_boundary_audit.py":
        raise V2BError("boundary result generator binding drift")
    module_runs = result.get("module_runs")
    if not isinstance(module_runs, list) or not module_runs \
            or result.get("n_modules") != len(module_runs) \
            or result.get("module_runs_sha256") != \
            sha256_sorted_json(module_runs):
        raise V2BError("boundary module-run binding drift")
    expected_manifests = build_driver_manifests(
        manifest_value, driver_path, result["toolchain"])
    module_names = []
    n_run_spans = 0
    for index, run in enumerate(module_runs):
        if not isinstance(run, dict) or set(run) != MODULE_RUN_KEYS \
                or not isinstance(run.get("module_name"), str) \
                or not run["module_name"] \
                or not _hex(run.get("invocation_binding")) \
                or type(run.get("n_spans")) is not int \
                or run["n_spans"] < 1 \
                or any(not _hex(run.get(field)) for field in (
                    "manifest_sha256", "stdout_sha256", "stderr_sha256",
                    "rows_sha256", "evidence_sha256")) \
                or type(run.get("exit_code")) is not int \
                or run["exit_code"] != 0:
            raise V2BError(f"malformed boundary module_run[{index}]")
        expected_manifest = expected_manifests.get(run["module_name"])
        if expected_manifest is None \
                or run["invocation_binding"] != \
                expected_manifest["invocationBinding"] \
                or run["n_spans"] != len(expected_manifest["spans"]) \
                or run["manifest_sha256"] != sha256_bytes(
                    canonical_driver_manifest_bytes(expected_manifest)):
            raise V2BError(f"boundary module_run[{index}] manifest drift")
        module_names.append(run["module_name"])
        n_run_spans += run["n_spans"]
    if module_names != sorted(expected_manifests) \
            or len(module_names) != len(set(module_names)) \
            or n_run_spans != result.get("n_spans"):
        raise V2BError("boundary module-run order/span count drift")
    expected_invocation = compute_invocation_sha256(
        manifest_file_sha256, driver_sha256, manifest_value)
    if result.get("invocation_sha256") != expected_invocation:
        raise V2BError("driver invocation binding does not recompute")
    rows = result.get("results")
    manifest_spans = manifest_value["spans"]
    if not isinstance(rows, list) \
            or result.get("n_spans") != len(rows) \
            or len(rows) != len(manifest_spans):
        raise V2BError("driver result span table is malformed")
    for index, (row, span) in enumerate(zip(rows, manifest_spans)):
        if not isinstance(row, dict) or set(row) != RESULT_ROW_KEYS:
            raise V2BError(f"driver result row[{index}] key drift")
        if row.get("span_id") != span["span_id"]:
            raise V2BError(f"driver result order/membership drift at "
                           f"row[{index}]")
        _validate_truth_table(
            row.get("status"), row.get("reason"), row.get("delimiter"),
            row.get("h_byte"), row.get("syntax_kind"),
            row.get("n_candidate_starts_total"), row.get("n_tested"),
            row.get("n_untested_after_choice"),
            row.get("rejected_starts"), span["start_byte"],
            span["end_byte"], f"result row[{index}]")
        if row["status"] == "resolved":
            token = row["delimiter"].encode("utf-8")
            with open(span["source"], "rb") as handle:
                handle.seek(row["h_byte"])
                found = handle.read(len(token))
            if found != token:
                raise V2BError(
                    f"resolved boundary does not point at an exact "
                    f"{row['delimiter']!r} token: span "
                    f"{span['span_id'][:12]}")
            if row["h_byte"] + len(token) > span["end_byte"]:
                raise V2BError(f"resolved delimiter exceeds span end at "
                               f"row[{index}]")
    row_by_span = {row["span_id"]: row for row in rows}
    runs_by_module = {run["module_name"]: run for run in module_runs}
    for module, expected_manifest in expected_manifests.items():
        module_rows = [row_by_span[span["id"]]
                       for span in expected_manifest["spans"]]
        if runs_by_module[module]["rows_sha256"] != \
                sha256_sorted_json(module_rows):
            raise V2BError(f"boundary module_run rows drift for {module}")
    return rows


def build_boundary_artifact(manifest_path, result_path, driver_path):
    """Fold one validated driver result into per-identity boundaries."""
    manifest_binding, manifest = artifact_binding(
        manifest_path, BOUNDARY_MANIFEST_SCHEMA)
    result_binding, result = artifact_binding(result_path,
                                              BOUNDARY_RESULT_SCHEMA)
    driver_sha256 = sha256_file(driver_path)
    rows = _validate_result(result, manifest_binding["sha256"],
                            driver_sha256, manifest, driver_path)
    boundaries = {}
    n_resolved = n_unsplit = n_changed = 0
    for row, span in zip(rows, manifest["spans"]):
        start, end = span["start_byte"], span["end_byte"]
        if row["status"] == "resolved":
            effective = dict(header_bytes=row["h_byte"] - start,
                             body_bytes=end - row["h_byte"],
                             split_kind=row["delimiter"])
            n_resolved += 1
        else:
            effective = dict(header_bytes=end - start, body_bytes=0,
                             split_kind=None)
            n_unsplit += 1
        changed = effective != span["old_split"]
        if changed:
            n_changed += 1
        for identity in span["members"]:
            key = identity_key("lean", identity)
            if key in boundaries:
                raise V2BError(f"duplicate boundary identity {key}")
            boundaries[key] = dict(
                identity=list(identity), span_id=span["span_id"],
                module=span["module"], source_sha256=span["source_sha256"],
                start_byte=start, end_byte=end,
                header_bytes=effective["header_bytes"],
                body_bytes=effective["body_bytes"],
                split_kind=effective["split_kind"],
                status=row["status"], reason=row["reason"],
                syntax_kind=row["syntax_kind"],
                n_candidate_starts_total=row[
                    "n_candidate_starts_total"],
                n_tested=row["n_tested"],
                n_untested_after_choice=row[
                    "n_untested_after_choice"],
                rejected_starts=list(row["rejected_starts"]),
                old_split=dict(span["old_split"]),
                changed_vs_v3=changed)
    if len(boundaries) != manifest["n_identities"]:
        raise V2BError("boundary artifact does not cover every extraction "
                       "identity exactly once")
    ordered = {key: boundaries[key] for key in sorted(boundaries)}
    return dict(
        schema=BOUNDARIES_SCHEMA, marker=BOUNDARY_MARKER,
        repo=manifest["repo"],
        extraction=dict(manifest["extraction"]),
        manifest=dict(sha256=manifest_binding["sha256"],
                      schema=BOUNDARY_MANIFEST_SCHEMA),
        result=dict(sha256=result_binding["sha256"],
                    schema=BOUNDARY_RESULT_SCHEMA),
        driver_sha256=driver_sha256,
        invocation_sha256=result["invocation_sha256"],
        toolchain=result["toolchain"],
        n_identities=len(ordered), n_spans=manifest["n_spans"],
        n_resolved_spans=n_resolved, n_unsplit_spans=n_unsplit,
        n_changed_spans_vs_v3=n_changed,
        n_unchanged_spans_vs_v3=manifest["n_spans"] - n_changed,
        boundaries=ordered,
        boundaries_sha256=sha256_sorted_json(ordered))


def replay_equal(artifact_a, artifact_b):
    """Deterministic replay comparison: equality minus generator stamps."""
    def stripped(value):
        if not isinstance(value, dict):
            raise V2BError("replay comparison needs artifact objects")
        return {key: item for key, item in value.items()
                if key != "generator"}
    return stripped(artifact_a) == stripped(artifact_b)


def _stamp(artifact, commit_start, tree_start):
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="v2b_lean_boundaries.py")
    return artifact


def prepare_manifest(extraction_path, setup_map):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    manifest = build_boundary_manifest(extraction_path, setup_map)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during boundary plan")
    return _stamp(manifest, commit_start, tree_start)


def prepare_boundaries(manifest_path, result_path, driver_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    artifact = build_boundary_artifact(manifest_path, result_path,
                                       driver_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during boundary fold")
    return _stamp(artifact, commit_start, tree_start)


def _load_setup_map(path):
    binding, value = artifact_binding(path)
    mapping = value.get("setups")
    if not isinstance(mapping, dict) or not mapping \
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or not k or not v for k, v in mapping.items()):
        raise V2BError("setup map file must carry a nonempty "
                       "source->setup object under 'setups'")
    return binding, mapping


def _publish_resume(path, value):
    """Publish new evidence or require exact deterministic replay."""
    if os.path.exists(path):
        existing, digest = load_json(path, value.get("schema"))
        if existing != value:
            raise V2BError(f"existing boundary artifact disagrees: {path}")
        return digest, True
    return write_new_json(path, value), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("plan", "finalize"))
    ap.add_argument("--extraction")
    ap.add_argument("--setup-map",
                    help="JSON file with {'setups': {source: setup}}; "
                         "setup paths are always explicit, never guessed")
    ap.add_argument("--manifest")
    ap.add_argument("--result")
    ap.add_argument("--driver")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        if args.command == "plan":
            if not args.extraction or not args.setup_map:
                raise V2BError("plan requires --extraction and --setup-map")
            _, mapping = _load_setup_map(args.setup_map)
            artifact = prepare_manifest(args.extraction, mapping)
            done = (f"[v2b-lean-boundaries] plan {artifact['repo']}: "
                    f"{artifact['n_spans']} spans / "
                    f"{artifact['n_identities']} identities")
        else:
            if not args.manifest or not args.result or not args.driver:
                raise V2BError(
                    "finalize requires --manifest, --result, --driver")
            artifact = prepare_boundaries(args.manifest, args.result,
                                          args.driver)
            done = (f"[v2b-lean-boundaries] finalize {artifact['repo']}: "
                    f"{artifact['n_resolved_spans']} resolved / "
                    f"{artifact['n_unsplit_spans']} unsplit / "
                    f"{artifact['n_changed_spans_vs_v3']} changed")
        digest, reused = _publish_resume(args.out, artifact)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"{done} -> {args.out} ({digest[:12]}, "
          f"{'reused' if reused else 'new'})")
    sys.exit(0)


if __name__ == "__main__":
    main()
