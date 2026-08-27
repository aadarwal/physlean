#!/usr/bin/env python3
"""Target-atomic scoring and exact reducers for the SymPy confirmation.

The three CLI modes are deliberately confirmation-specific:

``score``
    Materialize the sealed 200-target six-cell assembly, load one pinned
    checkpoint once, and atomically publish this deterministic shard's target
    records.  Compatible files are revalidated and resumed; incompatible
    files are never overwritten.

``reduce-model``
    Require the exact 200-file shard union for one checkpoint and publish a
    value-free model-completion manifest.

``reduce-study``
    Require exactly the four frozen checkpoint completions with one identical
    cohort and model-independent structural eligibility ledger.

No reducer computes or logs an effect, mean, delta, BPB, or p-value.  Numeric
NLL remains only in mode-0600 target artifacts for the later registered
masking stage.
"""
import argparse
import copy
import math
import os
import re
import stat
import subprocess
import sys

from provenance import (BASE, FREEZE_FILE, LOCK_FILE, env_fingerprint,
                        env_matches_freeze, env_matches_lock, harness_hash,
                        head_commit, source_clean, source_tree_hash)
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, canonical_json_bytes,
                        identity_key, load_json, sha256_bytes, sha256_file,
                        sha256_json, sha256_sorted_json, validate_identity,
                        write_new_json)
from v2b_nll_confirmation import (MODEL_ROWS, PROTOCOL_PATH,
                                  SCORED_CELLS, load_protocol)


TARGET_SCHEMA = "v2b_nll_e2_confirmation_target_score_v1"
MODEL_COMPLETE_SCHEMA = "v2b_nll_e2_confirmation_model_complete_v1"
STUDY_COMPLETE_SCHEMA = "v2b_nll_e2_confirmation_study_complete_v1"
FREEZE_SCHEMA = "v2b_nll_e2_confirmation_implementation_freeze_v1"
SOURCE_GATE_SCHEMA = "v2b_nll_e2_confirmation_source_gate_v1"
SAMPLE_SCHEMA = "v2b_nll_e2_confirmation_sample_v1"
ASSEMBLY_SCHEMA = "v2b_nll_e2_confirmation_assembly_v1"
BATTERY_SCHEMA = "v2b_nll_e2_confirmation_model_battery_v1"
SALT_SCHEMA = "v2b_nll_e2_confirmation_salt_commitment_v1"

PROGRAM = "eval_v2b_nll_confirmation.py"
N_TARGETS = 200
CELL_ORDER = tuple(SCORED_CELLS)
MODEL_IDS = tuple(row[0] for row in MODEL_ROWS)
MODEL_BY_ID = {
    row[0]: dict(id=row[0], name=row[1], revision=row[2],
                nominal_billions=row[3], role=row[4])
    for row in MODEL_ROWS}

STANDARD_BINDING_KEYS = {
    "implementation_freeze", "source_gate", "bound_sample", "assembly",
    "model_battery", "all_model_batteries", "salt_commitment",
}
ARTIFACT_SCHEMAS = {
    "implementation_freeze": FREEZE_SCHEMA,
    "source_gate": SOURCE_GATE_SCHEMA,
    "bound_sample": SAMPLE_SCHEMA,
    "assembly": ASSEMBLY_SCHEMA,
    "model_battery": BATTERY_SCHEMA,
    "salt_commitment": SALT_SCHEMA,
}
TARGET_TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "model", "execution",
    "salt_sequence", "shard", "ordered_target_keys_sha256",
    "target_index", "target_key", "target_identity", "module",
    "assembly_target_sha256", "prefix_sha256", "prefix_bytes",
    "body_sha256", "body_bytes", "cell_order", "n_cells", "cells",
    "cells_sha256", "generator",
}
ELIGIBLE_CELL_KEYS = {
    "cell_id", "assembly_cell_sha256", "structural_status", "status",
    "context_sha256", "context_bytes", "prompt_sha256", "prompt_bytes",
    "n_prompt_tokens", "body_layout_signature", "body_token_ledger",
    "scored_body_bytes", "n_scored_body_tokens", "nll_nats",
}
INELIGIBLE_CELL_KEYS = {
    "cell_id", "assembly_cell_sha256", "structural_status", "status",
    "eligibility_basis", "ineligibility_reason",
}
EXECUTION_KEYS = {
    "model_id", "model_name", "revision", "dtype", "device",
    "attention", "chunk_tokens", "max_position_embeddings",
    "environment_fingerprint", "source_tree_hash",
    "requirements_lock_sha256", "environment_freeze_sha256",
    "environment_lock_matches", "environment_freeze_matches",
    "measurement_harness_sha256", "numerical_harness_sha256",
    "battery_source_commit", "gpu", "model_snapshot_sha256",
    "tokenizer_snapshot_sha256", "model_class", "n_parameters",
    "tokenizer_class", "vocab_size",
}
GENERATOR_KEYS = {
    "program", "program_sha256", "source_commit", "source_tree_hash",
}
SALT_SEQUENCE_KEYS = {
    "salt_commitment_adoption_commit", "scoring_source_commit",
    "ancestry_verified",
}
SHARD_KEYS = {"index", "count", "target_index_start", "target_index_end"}
MODEL_COMPLETE_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "model", "execution",
    "salt_sequence", "shard_count", "cell_order", "n_targets",
    "ordered_target_keys", "cell_coverage", "shards", "target_artifacts",
    "target_artifacts_sha256", "generator",
}
STUDY_COMPLETE_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "salt_sequence",
    "cell_order", "n_models",
    "models", "n_targets", "ordered_target_keys", "cell_coverage",
    "model_artifacts", "model_artifacts_sha256", "generator",
}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _artifact_row(value, schema, label):
    _exact_keys(value, {"path", "schema", "sha256"}, label)
    if value["schema"] != schema or not _hex(value["sha256"]) \
            or not isinstance(value["path"], str) or not value["path"]:
        raise V2BError(f"malformed {label}")
    return value


def _same_artifact(left, right, schema, label):
    _artifact_row(left, schema, label)
    _artifact_row(right, schema, label)
    if left != right:
        raise V2BError(f"{label} binding drift")


def _protocol_record():
    # Reuse the one canonical prospective record.  Importing this helper does
    # not invoke the gate's census/reducer entry points.
    from prepare_v2b_nll_confirmation_gate import protocol_record
    return protocol_record()


def numerical_harness_hash(base=None):
    """Hash the exact numerical source files used by confirmation scoring."""
    root = os.path.abspath(base or BASE)
    rows = []
    for name in (PROGRAM, "eval_paired.py", "eval_incontext.py", "layout.py"):
        path = os.path.join(root, name)
        rows.append([name, sha256_file(path)])
    return sha256_bytes(canonical_json_bytes(rows))


confirmation_harness_hash = numerical_harness_hash


def _git_output(args):
    process = subprocess.run(["git", "-C", BASE, *args],
                             capture_output=True, text=True)
    if process.returncode != 0:
        raise V2BError(f"git {' '.join(args)} failed: "
                       f"{process.stderr.strip()[:200]}")
    return process.stdout


def salt_adoption_commit(path):
    """Return the public commitment's unique introducing/touching commit."""
    require_committed(path)
    real_base, real = os.path.realpath(BASE), os.path.realpath(path)
    try:
        if os.path.commonpath((real_base, real)) != real_base:
            raise V2BError("salt commitment path is outside checkout")
    except ValueError as err:
        raise V2BError(f"salt commitment path mismatch: {err}") from err
    relative = os.path.relpath(real, real_base).replace(os.sep, "/")
    commits = _git_output(["log", "--format=%H", "--", relative]).split()
    if len(commits) != 1 or not _hex(commits[0], 40):
        raise V2BError("salt commitment must have exactly one touching "
                       "adoption commit")
    return commits[0]


def git_is_ancestor(older, newer):
    if not _hex(older, 40) or not _hex(newer, 40):
        return False
    process = subprocess.run(
        ["git", "-C", BASE, "merge-base", "--is-ancestor", older, newer],
        capture_output=True)
    return process.returncode == 0


def salt_sequence(adoption_commit, source_commit,
                  ancestor_fn=git_is_ancestor):
    if not _hex(adoption_commit, 40) or not _hex(source_commit, 40) \
            or not ancestor_fn(adoption_commit, source_commit):
        raise V2BError("salt commitment adoption is not an ancestor of "
                       "the scoring generator")
    return dict(salt_commitment_adoption_commit=adoption_commit,
                scoring_source_commit=source_commit,
                ancestry_verified=True)


def _validate_sequence(value, ancestor_fn=None):
    _exact_keys(value, SALT_SEQUENCE_KEYS, "salt sequence")
    if not _hex(value["salt_commitment_adoption_commit"], 40) \
            or not _hex(value["scoring_source_commit"], 40) \
            or value["ancestry_verified"] is not True:
        raise V2BError("malformed salt sequence")
    if ancestor_fn is not None and not ancestor_fn(
            value["salt_commitment_adoption_commit"],
            value["scoring_source_commit"]):
        raise V2BError("recorded salt ancestry does not replay")
    return value


def _generator(commit=None, tree=None):
    return dict(program=PROGRAM,
                program_sha256=sha256_file(os.path.join(BASE, PROGRAM)),
                source_commit=head_commit() if commit is None else commit,
                source_tree_hash=source_tree_hash() if tree is None else tree)


def _validate_generator(value):
    _exact_keys(value, GENERATOR_KEYS, "generator")
    if value["program"] != PROGRAM \
            or value["program_sha256"] != \
            sha256_file(os.path.join(BASE, PROGRAM)) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("malformed scoring generator")
    return value


def _model_row(value, expected_id=None):
    _exact_keys(value,
                {"id", "name", "revision", "nominal_billions", "role"},
                "model row")
    model_id = value["id"]
    if model_id not in MODEL_BY_ID or value != MODEL_BY_ID[model_id] \
            or expected_id is not None and model_id != expected_id:
        raise V2BError("confirmation model identity/revision drift")
    return value


def _execution(value, model_id=None):
    _exact_keys(value, EXECUTION_KEYS, "execution identity")
    model = MODEL_BY_ID.get(value.get("model_id"))
    attention = value.get("attention")
    gpu = value.get("gpu")
    if model is None or model_id is not None and value["model_id"] != model_id \
            or value["model_name"] != model["name"] \
            or value["revision"] != model["revision"] \
            or value["dtype"] != "bfloat16" or value["device"] != "cuda" \
            or not isinstance(attention, dict) \
            or set(attention) != {"implementation", "model_type",
                                  "sliding_window", "layer_types"} \
            or not isinstance(attention["implementation"], str) \
            or not attention["implementation"] \
            or not isinstance(value["chunk_tokens"], int) \
            or isinstance(value["chunk_tokens"], bool) \
            or value["chunk_tokens"] <= 0 \
            or not isinstance(value["max_position_embeddings"], int) \
            or isinstance(value["max_position_embeddings"], bool) \
            or value["max_position_embeddings"] <= 0 \
            or any(not _hex(value[name]) for name in (
                "environment_fingerprint", "source_tree_hash",
                "requirements_lock_sha256", "environment_freeze_sha256",
                "measurement_harness_sha256", "numerical_harness_sha256",
                "model_snapshot_sha256", "tokenizer_snapshot_sha256")) \
            or not _hex(value["battery_source_commit"], 40) \
            or value["environment_lock_matches"] is not True \
            or value["environment_freeze_matches"] is not True \
            or not isinstance(gpu, dict) \
            or set(gpu) != {"gpu_name", "gpu_driver"} \
            or not isinstance(value["model_class"], str) \
            or not value["model_class"] \
            or not isinstance(value["n_parameters"], int) \
            or isinstance(value["n_parameters"], bool) \
            or value["n_parameters"] <= 0 \
            or not isinstance(value["tokenizer_class"], str) \
            or not value["tokenizer_class"] \
            or not isinstance(value["vocab_size"], int) \
            or isinstance(value["vocab_size"], bool) \
            or value["vocab_size"] < 2:
        raise V2BError("malformed confirmation execution identity")
    return value


def _bindings(value, model_id=None):
    _exact_keys(value, STANDARD_BINDING_KEYS, "score bindings")
    for name in STANDARD_BINDING_KEYS - {"all_model_batteries"}:
        _artifact_row(value[name], ARTIFACT_SCHEMAS[name], name)
    batteries = value["all_model_batteries"]
    if not isinstance(batteries, list) or len(batteries) != 4:
        raise V2BError("score bindings lack exact four model batteries")
    for row in batteries:
        _artifact_row(row, BATTERY_SCHEMA, "all-model battery")
    if model_id is not None:
        if model_id not in MODEL_IDS:
            raise V2BError("unknown confirmation model in score bindings")
        if value["model_battery"] != batteries[MODEL_IDS.index(model_id)]:
            raise V2BError(
                "selected model battery differs from four-battery gate")
    return value


def _ordered_keys(keys):
    if not isinstance(keys, list) or len(keys) != N_TARGETS \
            or len(keys) != len(set(keys)) \
            or any(not isinstance(key, str) or not key for key in keys):
        raise V2BError("ordered target-key cohort drift")
    return dict(n=N_TARGETS, sha256=sha256_json(keys), keys=list(keys))


def shard_bounds(shard_index, shard_count, n_targets=N_TARGETS):
    if not isinstance(shard_count, int) or isinstance(shard_count, bool) \
            or shard_count <= 0 or shard_count > n_targets \
            or not isinstance(shard_index, int) \
            or isinstance(shard_index, bool) \
            or not 0 <= shard_index < shard_count:
        raise V2BError("invalid deterministic shard index/count")
    return (n_targets * shard_index // shard_count,
            n_targets * (shard_index + 1) // shard_count)


def _shard_row(index, count):
    start, end = shard_bounds(index, count)
    return dict(index=index, count=count, target_index_start=start,
                target_index_end=end)


def _validate_shard(value, target_index=None, expected_count=None):
    _exact_keys(value, SHARD_KEYS, "target shard")
    start, end = shard_bounds(value["index"], value["count"])
    if value["target_index_start"] != start \
            or value["target_index_end"] != end \
            or expected_count is not None and value["count"] != expected_count \
            or target_index is not None and not start <= target_index < end:
        raise V2BError("target shard assignment drift")
    return value


def _body_ledger(value, body_bytes):
    required = {
        "schema", "paired_schema_version", "exact_body_bytes",
        "exact_body_codepoints", "scored_body_bytes",
        "scored_body_codepoints", "straddled_body_bytes",
        "straddled_body_codepoints", "n_boundary_straddle_tokens",
        "primary_token_indices", "boundary_token_indices",
        "inclusive_token_indices", "boundary_groups", "boundary_signature",
    }
    _exact_keys(value, required, "body token ledger")
    primary = value["primary_token_indices"]
    boundary = value["boundary_token_indices"]
    if value["schema"] != "v2b_body_token_ledger_v1" \
            or value["exact_body_bytes"] != body_bytes \
            or not isinstance(value["scored_body_bytes"], int) \
            or isinstance(value["scored_body_bytes"], bool) \
            or not 0 < value["scored_body_bytes"] <= body_bytes \
            or not isinstance(primary, list) or not primary \
            or not isinstance(boundary, list) \
            or value["inclusive_token_indices"] != boundary + primary \
            or any(not isinstance(index, int) or isinstance(index, bool)
                   or index <= 0 for index in primary + boundary) \
            or len(primary + boundary) != len(set(primary + boundary)) \
            or value["n_boundary_straddle_tokens"] != len(boundary) \
            or not isinstance(value["boundary_groups"], list) \
            or not _hex(value["boundary_signature"]):
        raise V2BError("body token ledger drift")
    for name in ("exact_body_codepoints", "scored_body_codepoints",
                 "straddled_body_bytes", "straddled_body_codepoints"):
        if not isinstance(value[name], int) or isinstance(value[name], bool) \
                or value[name] < 0:
            raise V2BError("body token ledger count drift")
    if value["scored_body_bytes"] + value["straddled_body_bytes"] != \
            body_bytes:
        raise V2BError("body token byte conservation drift")
    return value


def _score_result(value, prompt, body_bytes):
    expected = {
        "prompt_sha256", "prompt_bytes", "n_prompt_tokens",
        "body_layout_signature", "body_token_ledger", "nll_nats",
        "n_scored_body_tokens",
    }
    _exact_keys(value, expected, "injected/production score result")
    ledger = _body_ledger(value["body_token_ledger"], body_bytes)
    if value["prompt_sha256"] != sha256_bytes(prompt) \
            or value["prompt_bytes"] != len(prompt) \
            or not isinstance(value["n_prompt_tokens"], int) \
            or isinstance(value["n_prompt_tokens"], bool) \
            or value["n_prompt_tokens"] < 2 \
            or not _hex(value["body_layout_signature"]) \
            or not isinstance(value["nll_nats"], (int, float)) \
            or isinstance(value["nll_nats"], bool) \
            or not math.isfinite(value["nll_nats"]) \
            or value["nll_nats"] < 0 \
            or value["n_scored_body_tokens"] != \
            len(ledger["primary_token_indices"]):
        raise V2BError("scored prompt/token/NLL result drift")
    return value


def _fit_row(fit_by_pair, target_key, cell_id, eligible, prompt=None):
    row = fit_by_pair.get((target_key, cell_id))
    if not isinstance(row, dict) or row.get("target_key") != target_key \
            or row.get("cell_id") != cell_id:
        raise V2BError("battery tokenizer-fit row missing/mismatched")
    if eligible:
        if row.get("status") != "tokenized" \
                or row.get("eligible") is not True \
                or row.get("prompt_sha256") != sha256_bytes(prompt) \
                or row.get("prompt_bytes") != len(prompt) \
                or not isinstance(row.get("n_prompt_tokens"), int) \
                or isinstance(row.get("n_prompt_tokens"), bool) \
                or row["n_prompt_tokens"] < 2:
            raise V2BError("battery tokenized prompt binding drift")
    else:
        if row.get("status") != "structurally-ineligible-not-tokenized" \
                or row.get("eligible") is not False \
                or any(row.get(name) is not None for name in (
                    "prompt_sha256", "prompt_bytes", "n_prompt_tokens")):
            raise V2BError("battery ineligible tokenizer-fit row drift")
    return row


def _fit_ledger_matches(row, ledger):
    """Bind the numerical ledger to the battery's tokenizer-only preview."""
    for name in (
            "exact_body_bytes", "scored_body_bytes", "straddled_body_bytes",
            "n_boundary_straddle_tokens", "boundary_signature"):
        if row.get(name) != ledger.get(name):
            raise V2BError("scorer/body ledger differs from battery fit gate")


def normalize_battery(value, protocol, assembly, expected_core_bindings):
    """Validate one public battery and derive the scorer's exact contract."""
    from v2b_nll_confirmation_battery import validate_battery

    validate_battery(value, protocol, expected_core_bindings, assembly)
    model = value["model"]
    model_id = model["id"]
    protocol_model = MODEL_BY_ID.get(model_id)
    if protocol_model is None or any(model.get(name) != protocol_model[name]
                                     for name in protocol_model):
        raise V2BError("battery model differs from protocol ladder")
    execution = value["execution"]
    model_files = model.get("files")
    tokenizer_files = value.get("tokenizer", {}).get("files")
    if not isinstance(model_files, dict) \
            or not _hex(model_files.get("files_sha256")) \
            or not isinstance(tokenizer_files, dict) \
            or not _hex(tokenizer_files.get("files_sha256")):
        raise V2BError("battery lacks model/tokenizer snapshot manifests")
    normalized_execution = dict(
        model_id=model_id, model_name=model["name"],
        revision=model["revision"], dtype=execution["dtype"],
        device=execution["device"], attention=copy.deepcopy(
            execution["attention"]), chunk_tokens=execution["chunk_tokens"],
        max_position_embeddings=execution["max_position_embeddings"],
        environment_fingerprint=execution["environment_fingerprint"],
        source_tree_hash=execution["source_tree_hash"],
        requirements_lock_sha256=execution["requirements_lock_sha256"],
        environment_freeze_sha256=execution[
            "environment_freeze_sha256"],
        environment_lock_matches=execution["environment_lock_matches"],
        environment_freeze_matches=execution[
            "environment_freeze_matches"],
        measurement_harness_sha256=execution[
            "measurement_harness_sha256"],
        numerical_harness_sha256=execution["numerical_harness_sha256"],
        battery_source_commit=execution["source_commit"],
        gpu=copy.deepcopy(execution["gpu"]),
        model_snapshot_sha256=model_files["files_sha256"],
        tokenizer_snapshot_sha256=tokenizer_files["files_sha256"],
        model_class=model["model_class"],
        n_parameters=model["n_parameters"],
        tokenizer_class=value["tokenizer"]["tokenizer_class"],
        vocab_size=value["tokenizer"]["vocab_size"])
    _execution(normalized_execution, model_id)
    fit = value["tokenizer_fit"]
    rows = fit.get("rows") if isinstance(fit, dict) else None
    if not isinstance(rows, list) or len(rows) != N_TARGETS * len(CELL_ORDER):
        raise V2BError("battery tokenizer fit lacks exact 1200 rows")
    fit_by_pair = {}
    expected_pairs = []
    for target_index, target in enumerate(assembly["targets"]):
        for cell_index, cell_id in enumerate(CELL_ORDER):
            expected_pairs.append((target_index, target["key"],
                                   cell_index, cell_id))
    for raw, expected in zip(rows, expected_pairs):
        target_index, target_key, cell_index, cell_id = expected
        if raw.get("target_index") != target_index \
                or raw.get("target_key") != target_key \
                or raw.get("cell_index") != cell_index \
                or raw.get("cell_id") != cell_id:
            raise V2BError("battery tokenizer-fit target/cell order drift")
        eligible = raw.get("structurally_eligible") is True
        fit_by_pair[(target_key, cell_id)] = dict(
            target_key=target_key, cell_id=cell_id, eligible=eligible,
            status=raw.get("status"), prompt_sha256=raw.get("prompt_sha256"),
            prompt_bytes=raw.get("prompt_bytes"),
            n_prompt_tokens=raw.get("n_prompt_tokens"),
            exact_body_bytes=raw.get("exact_body_bytes"),
            scored_body_bytes=raw.get("scored_body_bytes"),
            straddled_body_bytes=raw.get("straddled_body_bytes"),
            n_boundary_straddle_tokens=raw.get(
                "n_boundary_straddle_tokens"),
            boundary_signature=raw.get("boundary_signature"))
    sharding = value.get("sharding")
    shard_count = sharding.get("recommended_shard_count") \
        if isinstance(sharding, dict) else None
    shard_bounds(0, shard_count)
    return dict(model=copy.deepcopy(protocol_model),
                execution=normalized_execution, fit_by_pair=fit_by_pair,
                shard_count=shard_count)


def _target_filename(index):
    return f"target-{index:04d}.json"


def build_target_score(protocol, bindings, model, execution, sequence,
                       assembly, target_index, materialized_target,
                       fit_by_pair, shard_index, shard_count, scorer,
                       generator):
    """Build one deterministic target value using an injected scorer."""
    if protocol.get("schema") != "v2b_nll_e2_confirmation_protocol_v1":
        raise V2BError("not the confirmation protocol")
    model = _model_row(model)
    _bindings(bindings, model["id"])
    execution = _execution(execution, model["id"])
    _validate_sequence(sequence)
    _validate_generator(generator)
    if generator["source_commit"] != sequence["scoring_source_commit"] \
            or generator["source_tree_hash"] != execution["source_tree_hash"]:
        raise V2BError("target generator/execution/sequence drift")
    targets = assembly.get("targets")
    ordered_keys = assembly.get("ordered_target_keys", {}).get("keys")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or not isinstance(ordered_keys, list) \
            or len(ordered_keys) != N_TARGETS \
            or not isinstance(target_index, int) \
            or isinstance(target_index, bool) \
            or not 0 <= target_index < N_TARGETS:
        raise V2BError("assembly target table/index drift")
    target = targets[target_index]
    key = target.get("key")
    if ordered_keys[target_index] != key:
        raise V2BError("assembly target order drift")
    identity = validate_identity("python", target.get("identity"))
    if identity_key("python", identity) != key \
            or not isinstance(materialized_target, dict):
        raise V2BError("assembly/materialized target identity drift")
    prefix, body = materialized_target.get("prefix"), \
        materialized_target.get("body")
    concrete_cells = materialized_target.get("cells")
    if not isinstance(prefix, bytes) or not isinstance(body, bytes) \
            or not isinstance(concrete_cells, dict) \
            or set(concrete_cells) != set(CELL_ORDER) \
            or len(prefix) != target.get("prefix_bytes") \
            or sha256_bytes(prefix) != target.get("prefix_sha256") \
            or len(body) != target.get("body_bytes") \
            or sha256_bytes(body) != target.get("body_sha256"):
        raise V2BError("materialized target byte binding drift")
    assembly_cells = target.get("cells")
    if not isinstance(assembly_cells, list) \
            or [row.get("cell_id") for row in assembly_cells] != \
            list(CELL_ORDER):
        raise V2BError("assembly six-cell order drift")

    scored_cells = []
    for assembly_cell in assembly_cells:
        cell_id = assembly_cell["cell_id"]
        cell_hash = sha256_sorted_json(assembly_cell)
        eligible = assembly_cell.get("eligible") is True
        context = concrete_cells[cell_id]
        if eligible:
            if not isinstance(context, bytes) \
                    or len(context) != assembly_cell.get("context_bytes") \
                    or sha256_bytes(context) != \
                    assembly_cell.get("context_sha256"):
                raise V2BError(f"eligible context drift: {cell_id}")
            prompt = context + prefix + body
            fit = _fit_row(fit_by_pair, key, cell_id, True, prompt)
            result = _score_result(
                scorer(context, prefix, body, cell_id, execution),
                prompt, len(body))
            if result["n_prompt_tokens"] != fit["n_prompt_tokens"]:
                raise V2BError("scorer/tokenizer-fit token count drift")
            ledger = result["body_token_ledger"]
            _fit_ledger_matches(fit, ledger)
            scored_cells.append(dict(
                cell_id=cell_id, assembly_cell_sha256=cell_hash,
                structural_status="eligible", status="scored",
                context_sha256=assembly_cell["context_sha256"],
                context_bytes=assembly_cell["context_bytes"],
                prompt_sha256=result["prompt_sha256"],
                prompt_bytes=result["prompt_bytes"],
                n_prompt_tokens=result["n_prompt_tokens"],
                body_layout_signature=result["body_layout_signature"],
                body_token_ledger=ledger,
                scored_body_bytes=ledger["scored_body_bytes"],
                n_scored_body_tokens=result["n_scored_body_tokens"],
                nll_nats=float(result["nll_nats"])))
        else:
            if context is not None:
                raise V2BError(f"ineligible cell exposed context: {cell_id}")
            _fit_row(fit_by_pair, key, cell_id, False)
            if cell_id in ("k1", "k4:16384", "k5:0:16384"):
                raise V2BError("required cell is structurally ineligible")
            scored_cells.append(dict(
                cell_id=cell_id, assembly_cell_sha256=cell_hash,
                structural_status="ineligible",
                status="structurally-ineligible-not-scored",
                eligibility_basis=assembly_cell.get("eligibility_basis"),
                ineligibility_reason=assembly_cell.get(
                    "ineligibility_reason")))
    value = dict(
        schema=TARGET_SCHEMA, state="target-atomic-complete",
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=_protocol_record(), bindings=copy.deepcopy(bindings),
        model=copy.deepcopy(model), execution=copy.deepcopy(execution),
        salt_sequence=copy.deepcopy(sequence),
        shard=_shard_row(shard_index, shard_count),
        ordered_target_keys_sha256=sha256_json(ordered_keys),
        target_index=target_index, target_key=key,
        target_identity=list(identity), module=identity[0],
        assembly_target_sha256=sha256_sorted_json(target),
        prefix_sha256=target["prefix_sha256"],
        prefix_bytes=target["prefix_bytes"],
        body_sha256=target["body_sha256"], body_bytes=target["body_bytes"],
        cell_order=list(CELL_ORDER), n_cells=len(scored_cells),
        cells=scored_cells, cells_sha256=sha256_sorted_json(scored_cells),
        generator=copy.deepcopy(generator))
    return validate_target_score(
        value, protocol, assembly, target_index, bindings, model, execution,
        shard_count)


def _validate_eligible_cell(cell, assembly_cell, body_bytes):
    _exact_keys(cell, ELIGIBLE_CELL_KEYS,
                f"scored cell {assembly_cell.get('cell_id')}")
    ledger = _body_ledger(cell["body_token_ledger"], body_bytes)
    if cell["cell_id"] != assembly_cell.get("cell_id") \
            or cell["assembly_cell_sha256"] != \
            sha256_sorted_json(assembly_cell) \
            or cell["structural_status"] != "eligible" \
            or cell["status"] != "scored" \
            or cell["context_sha256"] != \
            assembly_cell.get("context_sha256") \
            or cell["context_bytes"] != assembly_cell.get("context_bytes") \
            or not _hex(cell["prompt_sha256"]) \
            or not isinstance(cell["prompt_bytes"], int) \
            or isinstance(cell["prompt_bytes"], bool) \
            or cell["prompt_bytes"] <= body_bytes \
            or not isinstance(cell["n_prompt_tokens"], int) \
            or isinstance(cell["n_prompt_tokens"], bool) \
            or cell["n_prompt_tokens"] < 2 \
            or not _hex(cell["body_layout_signature"]) \
            or cell["scored_body_bytes"] != ledger["scored_body_bytes"] \
            or cell["n_scored_body_tokens"] != \
            len(ledger["primary_token_indices"]) \
            or not isinstance(cell["nll_nats"], (int, float)) \
            or isinstance(cell["nll_nats"], bool) \
            or not math.isfinite(cell["nll_nats"]) \
            or cell["nll_nats"] < 0:
        raise V2BError("eligible target-score cell drift")


def _validate_ineligible_cell(cell, assembly_cell):
    _exact_keys(cell, INELIGIBLE_CELL_KEYS,
                f"ineligible cell {assembly_cell.get('cell_id')}")
    if cell["cell_id"] != assembly_cell.get("cell_id") \
            or cell["assembly_cell_sha256"] != \
            sha256_sorted_json(assembly_cell) \
            or cell["structural_status"] != "ineligible" \
            or cell["status"] != "structurally-ineligible-not-scored" \
            or cell["eligibility_basis"] != \
            assembly_cell.get("eligibility_basis") \
            or cell["ineligibility_reason"] != \
            assembly_cell.get("ineligibility_reason"):
        raise V2BError("ineligible target-score cell drift")


def validate_target_score(value, protocol, assembly, target_index,
                          expected_bindings, expected_model,
                          expected_execution, expected_shard_count,
                          ancestor_fn=None):
    _exact_keys(value, TARGET_TOP_KEYS, "target score")
    if value["schema"] != TARGET_SCHEMA \
            or value["state"] != "target-atomic-complete" \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != "sympy" or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != _protocol_record() \
            or value["cell_order"] != list(CELL_ORDER) \
            or value["n_cells"] != len(CELL_ORDER):
        raise V2BError("target-score identity/cell-grid drift")
    if value["bindings"] != expected_bindings:
        raise V2BError("target-score predecessor binding drift")
    if value["model"] != expected_model \
            or value["execution"] != expected_execution:
        raise V2BError("target-score model/execution drift")
    _model_row(value["model"])
    _execution(value["execution"], value["model"]["id"])
    _bindings(value["bindings"], value["model"]["id"])
    _validate_sequence(value["salt_sequence"], ancestor_fn)
    _validate_generator(value["generator"])
    if value["generator"]["source_commit"] != \
            value["salt_sequence"]["scoring_source_commit"] \
            or value["generator"]["source_tree_hash"] != \
            value["execution"]["source_tree_hash"]:
        raise V2BError("target-score generator ancestry/source drift")
    targets = assembly.get("targets")
    keys = assembly.get("ordered_target_keys", {}).get("keys")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or not isinstance(keys, list) or len(keys) != N_TARGETS \
            or target_index != value["target_index"] \
            or not 0 <= target_index < N_TARGETS:
        raise V2BError("target-score target index drift")
    target = targets[target_index]
    identity = validate_identity("python", target.get("identity"))
    if value["ordered_target_keys_sha256"] != sha256_json(keys) \
            or keys[target_index] != target.get("key") \
            or value["target_key"] != target.get("key") \
            or value["target_identity"] != list(identity) \
            or value["module"] != identity[0] \
            or value["assembly_target_sha256"] != \
            sha256_sorted_json(target) \
            or value["prefix_sha256"] != target.get("prefix_sha256") \
            or value["prefix_bytes"] != target.get("prefix_bytes") \
            or value["body_sha256"] != target.get("body_sha256") \
            or value["body_bytes"] != target.get("body_bytes"):
        raise V2BError("target-score assembly target binding drift")
    _validate_shard(value["shard"], target_index, expected_shard_count)
    assembly_cells = target.get("cells")
    cells = value["cells"]
    if not isinstance(assembly_cells, list) \
            or not isinstance(cells, list) \
            or [row.get("cell_id") for row in assembly_cells] != \
            list(CELL_ORDER) \
            or [row.get("cell_id") for row in cells] != list(CELL_ORDER) \
            or value["cells_sha256"] != sha256_sorted_json(cells):
        raise V2BError("target-score six-cell table/hash drift")
    for cell, assembly_cell in zip(cells, assembly_cells):
        eligible = assembly_cell.get("eligible") is True
        if eligible:
            _validate_eligible_cell(cell, assembly_cell,
                                    target["body_bytes"])
        else:
            if assembly_cell.get("cell_id") in (
                    "k1", "k4:16384", "k5:0:16384"):
                raise V2BError("required assembly cell is ineligible")
            _validate_ineligible_cell(cell, assembly_cell)
    return value


def validate_target_fit(value, fit_by_pair):
    """Replay the committed battery's tokenizer-fit gate on a score file."""
    target_key = value.get("target_key")
    cells = value.get("cells")
    if not isinstance(target_key, str) or not isinstance(cells, list) \
            or len(cells) != len(CELL_ORDER):
        raise V2BError("target score lacks battery-fit replay material")
    for cell in cells:
        cell_id = cell.get("cell_id")
        eligible = cell.get("status") == "scored"
        fit = _fit_row(fit_by_pair, target_key, cell_id, eligible,
                       prompt=None) if not eligible else \
            fit_by_pair.get((target_key, cell_id))
        if eligible:
            if not isinstance(fit, dict) or fit.get("eligible") is not True \
                    or fit.get("status") != "tokenized" \
                    or cell.get("prompt_sha256") != \
                    fit.get("prompt_sha256") \
                    or cell.get("prompt_bytes") != fit.get("prompt_bytes") \
                    or cell.get("n_prompt_tokens") != \
                    fit.get("n_prompt_tokens"):
                raise V2BError("target score differs from battery prompt fit")
            _fit_ledger_matches(fit, cell.get("body_token_ledger", {}))
    return value


def _coverage(assembly):
    targets = assembly.get("targets")
    if not isinstance(targets, list) or len(targets) != N_TARGETS:
        raise V2BError("assembly coverage lacks exact 200 targets")
    rows = []
    for cell_index, cell_id in enumerate(CELL_ORDER):
        eligible = [target["key"] for target in targets
                    if target["cells"][cell_index].get("eligible") is True]
        rows.append(dict(cell_id=cell_id, n_eligible=len(eligible),
                         n_ineligible=N_TARGETS - len(eligible),
                         eligible_target_keys_sha256=sha256_json(eligible)))
    return rows


def _target_binding(path, digest, value):
    return dict(target_index=value["target_index"],
                target_key=value["target_key"], path=os.path.abspath(path),
                schema=TARGET_SCHEMA, sha256=digest,
                shard_index=value["shard"]["index"])


def build_model_complete(protocol, assembly, bindings, model, execution,
                         sequence, shard_count, target_inputs, generator,
                         ancestor_fn=None, fit_by_pair=None):
    """Reduce exactly 200 validated target values without reading outcomes."""
    model = _model_row(model)
    _bindings(bindings, model["id"])
    execution = _execution(execution, model["id"])
    _validate_sequence(sequence, ancestor_fn)
    _validate_generator(generator)
    if generator["source_tree_hash"] != execution["source_tree_hash"] \
            or generator["source_commit"] != \
            sequence["scoring_source_commit"]:
        raise V2BError("model reducer source/execution drift")
    if not isinstance(target_inputs, list) or len(target_inputs) != N_TARGETS:
        raise V2BError("model reducer lacks exact 200 target inputs")
    by_index = {}
    for record in target_inputs:
        _exact_keys(record, {"path", "sha256", "value"},
                    "model reducer target input")
        if not isinstance(record["path"], str) or not record["path"] \
                or not _hex(record["sha256"]):
            raise V2BError("malformed model reducer target input")
        value = validate_target_score(
            record["value"], protocol, assembly,
            record["value"].get("target_index"), bindings, model, execution,
            shard_count, ancestor_fn)
        if fit_by_pair is not None:
            validate_target_fit(value, fit_by_pair)
        if os.path.basename(record["path"]) != \
                _target_filename(value["target_index"]):
            raise V2BError("target artifact filename/index drift")
        if value["salt_sequence"]["salt_commitment_adoption_commit"] != \
                sequence["salt_commitment_adoption_commit"]:
            raise V2BError("target/model salt adoption mismatch")
        index = value["target_index"]
        if index in by_index:
            raise V2BError("overlapping/duplicate target shard union")
        by_index[index] = record
    if sorted(by_index) != list(range(N_TARGETS)):
        raise V2BError("missing/noncontiguous target shard union")
    ordered_keys = assembly["ordered_target_keys"]["keys"]
    target_artifacts = []
    shards = []
    for shard_index in range(shard_count):
        start, end = shard_bounds(shard_index, shard_count)
        shard_records = []
        for index in range(start, end):
            record = by_index[index]
            value = record["value"]
            if value["target_key"] != ordered_keys[index] \
                    or value["shard"]["index"] != shard_index:
                raise V2BError("target key/shard union drift")
            binding = _target_binding(record["path"], record["sha256"],
                                      value)
            target_artifacts.append(binding)
            shard_records.append(binding)
        shards.append(dict(
            shard_index=shard_index, shard_count=shard_count,
            target_index_start=start, target_index_end=end,
            n_targets=end - start,
            ordered_target_keys_sha256=sha256_json(ordered_keys[start:end]),
            target_artifacts_sha256=sha256_sorted_json(shard_records)))
    coverage = _coverage(assembly)
    value = dict(
        schema=MODEL_COMPLETE_SCHEMA, state="model-exact-200-complete",
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=_protocol_record(), bindings=copy.deepcopy(bindings),
        model=copy.deepcopy(model), execution=copy.deepcopy(execution),
        salt_sequence=copy.deepcopy(sequence), shard_count=shard_count,
        cell_order=list(CELL_ORDER), n_targets=N_TARGETS,
        ordered_target_keys=_ordered_keys(ordered_keys),
        cell_coverage=coverage, shards=shards,
        target_artifacts=target_artifacts,
        target_artifacts_sha256=sha256_sorted_json(target_artifacts),
        generator=copy.deepcopy(generator))
    return validate_model_complete(value, protocol, assembly, bindings,
                                   model, execution, ancestor_fn)


def validate_model_complete(value, protocol, assembly, expected_bindings,
                            expected_model=None, expected_execution=None,
                            ancestor_fn=None):
    _exact_keys(value, MODEL_COMPLETE_KEYS, "model completion")
    if value["schema"] != MODEL_COMPLETE_SCHEMA \
            or value["state"] != "model-exact-200-complete" \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != "sympy" or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != _protocol_record() \
            or value["bindings"] != expected_bindings \
            or value["cell_order"] != list(CELL_ORDER) \
            or value["n_targets"] != N_TARGETS:
        raise V2BError("model-completion identity/binding drift")
    model = _model_row(value["model"])
    _bindings(value["bindings"], model["id"])
    execution = _execution(value["execution"], model["id"])
    if expected_model is not None and model != expected_model \
            or expected_execution is not None and execution != \
            expected_execution:
        raise V2BError("model-completion model/execution mismatch")
    _validate_sequence(value["salt_sequence"], ancestor_fn)
    _validate_generator(value["generator"])
    if value["generator"]["source_tree_hash"] != \
            execution["source_tree_hash"] \
            or value["generator"]["source_commit"] != \
            value["salt_sequence"]["scoring_source_commit"]:
        raise V2BError("model-completion generator/source drift")
    keys = assembly.get("ordered_target_keys", {}).get("keys")
    if value["ordered_target_keys"] != _ordered_keys(keys) \
            or value["cell_coverage"] != _coverage(assembly):
        raise V2BError("model-completion cohort/eligibility drift")
    count = value["shard_count"]
    shard_bounds(0, count)
    shards = value["shards"]
    artifacts = value["target_artifacts"]
    if not isinstance(shards, list) or len(shards) != count \
            or not isinstance(artifacts, list) \
            or len(artifacts) != N_TARGETS \
            or value["target_artifacts_sha256"] != \
            sha256_sorted_json(artifacts):
        raise V2BError("model-completion shard/artifact count drift")
    cursor = 0
    for shard_index, shard in enumerate(shards):
        expected_shard_keys = {
            "shard_index", "shard_count", "target_index_start",
            "target_index_end", "n_targets",
            "ordered_target_keys_sha256", "target_artifacts_sha256"}
        _exact_keys(shard, expected_shard_keys,
                    f"model completion shard[{shard_index}]")
        start, end = shard_bounds(shard_index, count)
        slice_rows = artifacts[start:end]
        if shard["shard_index"] != shard_index \
                or shard["shard_count"] != count \
                or shard["target_index_start"] != start \
                or shard["target_index_end"] != end \
                or shard["n_targets"] != end - start \
                or shard["ordered_target_keys_sha256"] != \
                sha256_json(keys[start:end]) \
                or shard["target_artifacts_sha256"] != \
                sha256_sorted_json(slice_rows):
            raise V2BError("model-completion shard partition drift")
        cursor = end
    if cursor != N_TARGETS:
        raise V2BError("model-completion shard union incomplete")
    for index, row in enumerate(artifacts):
        _exact_keys(row, {"target_index", "target_key", "path", "schema",
                          "sha256", "shard_index"},
                    f"target artifact[{index}]")
        expected_shard = next(
            shard for shard in range(count)
            if shard_bounds(shard, count)[0] <= index <
            shard_bounds(shard, count)[1])
        if row["target_index"] != index or row["target_key"] != keys[index] \
                or row["schema"] != TARGET_SCHEMA \
                or not isinstance(row["path"], str) or not row["path"] \
                or not _hex(row["sha256"]) \
                or row["shard_index"] != expected_shard:
            raise V2BError("model-completion target artifact order drift")
    return value


def _study_bindings(model_values):
    common_names = {
        "implementation_freeze", "source_gate", "bound_sample", "assembly",
        "salt_commitment", "all_model_batteries"}
    first = model_values[0]["bindings"]
    common = {name: copy.deepcopy(first[name]) for name in common_names}
    for value in model_values[1:]:
        if any(value["bindings"][name] != common[name]
               for name in common_names):
            raise V2BError("model completions have different study bindings")
    batteries = [copy.deepcopy(value["bindings"]["model_battery"])
                 for value in model_values]
    return dict(**common, model_batteries=batteries)


def _validate_shared_model_execution(values, generator=None):
    names = (
        "environment_fingerprint", "requirements_lock_sha256",
        "environment_freeze_sha256", "environment_lock_matches",
        "environment_freeze_matches", "measurement_harness_sha256",
        "numerical_harness_sha256", "source_tree_hash")
    first = values[0]["execution"]
    if any(any(value["execution"][name] != first[name] for name in names)
           for value in values[1:]):
        raise V2BError("study models have different shared execution source")
    if generator is not None \
            and generator.get("source_tree_hash") != \
            first["source_tree_hash"]:
        raise V2BError("study generator tree differs from model execution")


def build_study_complete(protocol, assembly, model_inputs, generator,
                         ancestor_fn=None):
    if not isinstance(model_inputs, list) or len(model_inputs) != 4:
        raise V2BError("study reducer requires exactly four model inputs")
    by_model = {}
    for record in model_inputs:
        _exact_keys(record, {"path", "sha256", "value"},
                    "study reducer model input")
        value = record["value"]
        model_id = value.get("model", {}).get("id") \
            if isinstance(value, dict) else None
        if model_id not in MODEL_BY_ID or model_id in by_model \
                or not isinstance(record["path"], str) \
                or not record["path"] or not _hex(record["sha256"]):
            raise V2BError("duplicate/malformed study model input")
        bindings = value.get("bindings")
        validate_model_complete(
            value, protocol, assembly, bindings,
            MODEL_BY_ID[model_id], value.get("execution"), ancestor_fn)
        by_model[model_id] = record
    if tuple(by_model) != MODEL_IDS and set(by_model) != set(MODEL_IDS):
        raise V2BError("study reducer model ladder mismatch")
    ordered = [by_model[model_id] for model_id in MODEL_IDS]
    values = [record["value"] for record in ordered]
    _validate_shared_model_execution(values, generator)
    cohort = values[0]["ordered_target_keys"]
    coverage = values[0]["cell_coverage"]
    adoption = values[0]["salt_sequence"][
        "salt_commitment_adoption_commit"]
    if any(value["ordered_target_keys"] != cohort \
           or value["cell_coverage"] != coverage \
           or value["cell_order"] != list(CELL_ORDER) \
           or value["salt_sequence"][
               "salt_commitment_adoption_commit"] != adoption
           for value in values[1:]):
        raise V2BError("study models differ in cohort/structural eligibility")
    bindings = _study_bindings(values)
    models = []
    artifacts = []
    for model_id, record in zip(MODEL_IDS, ordered):
        value = record["value"]
        binding = dict(
            model_id=model_id, path=os.path.abspath(record["path"]),
            schema=MODEL_COMPLETE_SCHEMA, sha256=record["sha256"])
        models.append(dict(model=copy.deepcopy(value["model"]),
                           execution=copy.deepcopy(value["execution"]),
                           model_complete=copy.deepcopy(binding)))
        artifacts.append(binding)
    _validate_generator(generator)
    study_sequence = salt_sequence(
        adoption, generator["source_commit"],
        ancestor_fn=ancestor_fn or git_is_ancestor)
    value = dict(
        schema=STUDY_COMPLETE_SCHEMA, state="exact-four-model-study-complete",
        study_id=protocol["study_id"], repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=_protocol_record(), bindings=bindings,
        salt_sequence=study_sequence,
        cell_order=list(CELL_ORDER), n_models=4, models=models,
        n_targets=N_TARGETS, ordered_target_keys=copy.deepcopy(cohort),
        cell_coverage=copy.deepcopy(coverage),
        model_artifacts=artifacts,
        model_artifacts_sha256=sha256_sorted_json(artifacts),
        generator=copy.deepcopy(generator))
    return validate_study_complete(value, protocol, assembly, ancestor_fn)


def validate_study_complete(value, protocol, assembly, ancestor_fn=None):
    _exact_keys(value, STUDY_COMPLETE_KEYS, "study completion")
    if value["schema"] != STUDY_COMPLETE_SCHEMA \
            or value["state"] != "exact-four-model-study-complete" \
            or value["study_id"] != protocol["study_id"] \
            or value["repo"] != "sympy" or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != _protocol_record() \
            or value["cell_order"] != list(CELL_ORDER) \
            or value["n_models"] != 4 or value["n_targets"] != N_TARGETS \
            or value["ordered_target_keys"] != \
            _ordered_keys(assembly["ordered_target_keys"]["keys"]) \
            or value["cell_coverage"] != _coverage(assembly):
        raise V2BError("study-completion identity/cohort drift")
    _validate_generator(value["generator"])
    _validate_sequence(value["salt_sequence"], ancestor_fn)
    if value["generator"]["source_commit"] != \
            value["salt_sequence"]["scoring_source_commit"]:
        raise V2BError("study reducer generator/salt sequence drift")
    bindings = value["bindings"]
    expected_binding_names = {
        "implementation_freeze", "source_gate", "bound_sample", "assembly",
        "salt_commitment", "model_batteries", "all_model_batteries"}
    _exact_keys(bindings, expected_binding_names, "study bindings")
    for name in expected_binding_names - {
            "model_batteries", "all_model_batteries"}:
        _artifact_row(bindings[name], ARTIFACT_SCHEMAS[name], name)
    batteries = bindings["model_batteries"]
    if not isinstance(batteries, list) or len(batteries) != 4:
        raise V2BError("study lacks exact four battery bindings")
    for row in batteries:
        _artifact_row(row, BATTERY_SCHEMA, "study model battery")
    if bindings["all_model_batteries"] != batteries:
        raise V2BError("study all-model battery gate/order drift")
    models = value["models"]
    artifacts = value["model_artifacts"]
    if not isinstance(models, list) or len(models) != 4 \
            or not isinstance(artifacts, list) or len(artifacts) != 4 \
            or value["model_artifacts_sha256"] != \
            sha256_sorted_json(artifacts):
        raise V2BError("study model/artifact table drift")
    for index, (row, artifact, model_id) in enumerate(
            zip(models, artifacts, MODEL_IDS)):
        _exact_keys(row, {"model", "execution", "model_complete"},
                    f"study model[{index}]")
        _model_row(row["model"], model_id)
        _execution(row["execution"], model_id)
        _exact_keys(artifact, {"model_id", "path", "schema", "sha256"},
                    f"study model artifact[{index}]")
        if artifact != row["model_complete"] \
                or artifact["model_id"] != model_id \
                or artifact["schema"] != MODEL_COMPLETE_SCHEMA \
                or not isinstance(artifact["path"], str) \
                or not artifact["path"] or not _hex(artifact["sha256"]):
            raise V2BError("study model artifact/order drift")
    _validate_shared_model_execution(models, value["generator"])
    return value


def _write_new_0600(path, value):
    digest = write_new_json(path, value)
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    if not stat.S_ISREG(os.lstat(path).st_mode) or mode != 0o600:
        raise V2BError("atomic target/completion file is not regular mode0600")
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        parent_descriptor = os.open(os.path.dirname(os.path.abspath(path)),
                                    os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError as err:
        raise V2BError(f"cannot durably sync target artifact: {err}") from err
    return digest


def _target_files(directory):
    try:
        names = sorted(os.listdir(directory))
    except OSError as err:
        raise V2BError(f"cannot list target directory: {err}") from err
    expected = [_target_filename(index) for index in range(N_TARGETS)]
    if names != expected:
        missing = sorted(set(expected) - set(names))[:4]
        extra = sorted(set(names) - set(expected))[:4]
        raise V2BError(f"target directory is not exact: missing={missing}, "
                       f"extra={extra}")
    return [os.path.join(directory, name) for name in expected]


def _load_target_inputs(directory):
    inputs = []
    for index, path in enumerate(_target_files(directory)):
        value, digest = load_json(path, TARGET_SCHEMA)
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        if not stat.S_ISREG(os.lstat(path).st_mode) or mode != 0o600:
            raise V2BError("target artifact is not regular mode0600")
        if value.get("target_index") != index:
            raise V2BError("target filename/index drift")
        inputs.append(dict(path=os.path.abspath(path), sha256=digest,
                           value=value))
    return inputs


def compatible_target(path, protocol, assembly, target_index, bindings,
                      model, execution, shard_count, ancestor_fn=None,
                      fit_by_pair=None):
    """Validate one atomic resume file; stale/tampered means hard failure."""
    value, digest = load_json(path, TARGET_SCHEMA)
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    if not stat.S_ISREG(os.lstat(path).st_mode) or mode != 0o600:
        raise V2BError("resume target is not regular mode0600")
    validate_target_score(value, protocol, assembly, target_index, bindings,
                          model, execution, shard_count, ancestor_fn)
    if fit_by_pair is not None:
        validate_target_fit(value, fit_by_pair)
    return _target_binding(path, digest, value)


def _real_scorer(model, tokenizer, device, max_positions, chunk_tokens):
    """Adapt the safe paired prompt scorer to the confirmation schema."""
    from eval_paired import score_prompt

    def score(context, prefix, body, _cell_id, _execution_identity):
        result = score_prompt(
            model, tokenizer, device, context, prefix, body, max_positions,
            chunk=chunk_tokens)
        prompt = context + prefix + body
        ledger = result["boundary_ledger"]
        return dict(
            prompt_sha256=sha256_bytes(prompt),
            prompt_bytes=result["prompt_bytes"],
            n_prompt_tokens=result["n_prompt_tokens"],
            body_layout_signature=result["body_layout_signature"],
            body_token_ledger=ledger,
            nll_nats=result["primary"]["nll_nats"],
            n_scored_body_tokens=result["primary"]["n_tokens"])
    return score


def _validate_protocol_contract(protocol):
    contracts = protocol.get("execution_schema_contracts")
    expected = {
        "implementation_freeze": FREEZE_SCHEMA,
        "source_gate_reduced": SOURCE_GATE_SCHEMA,
        "bound_sample": SAMPLE_SCHEMA,
        "assembly": ASSEMBLY_SCHEMA,
        "model_battery": BATTERY_SCHEMA,
        "salt_commitment": SALT_SCHEMA,
        "target_score": TARGET_SCHEMA,
        "model_complete": MODEL_COMPLETE_SCHEMA,
        "study_complete": STUDY_COMPLETE_SCHEMA,
    }
    if not isinstance(contracts, dict) \
            or any(contracts.get(name) != schema
                   for name, schema in expected.items()):
        raise V2BError("confirmation execution schema contract drift")


def _environment_identity():
    lock_ok, lock_detail = env_matches_lock()
    freeze_ok, freeze_detail = env_matches_freeze()
    if not lock_ok or not freeze_ok:
        raise V2BError(
            "confirmation scoring environment differs from lock/freeze: "
            f"{lock_detail}; {freeze_detail}")
    return dict(
        environment_fingerprint=env_fingerprint(),
        requirements_lock_sha256=sha256_file(LOCK_FILE),
        environment_freeze_sha256=sha256_file(FREEZE_FILE),
        measurement_harness_sha256=harness_hash(),
        numerical_harness_sha256=numerical_harness_hash())


def _path_digest_table(paths):
    table = []
    seen = set()
    for label, path in paths:
        absolute = os.path.abspath(path)
        if label in seen:
            raise V2BError(f"duplicate scorer input label: {label}")
        seen.add(label)
        table.append(dict(label=label, path=absolute,
                          sha256=sha256_file(absolute)))
    return table


def _score_bindings(common, model_id):
    batteries = common["battery_bindings"]
    return dict(
        implementation_freeze=copy.deepcopy(common["freeze_binding"]),
        source_gate=copy.deepcopy(common["gate_binding"]),
        bound_sample=copy.deepcopy(common["sample_binding"]),
        assembly=copy.deepcopy(common["assembly_binding"]),
        model_battery=copy.deepcopy(
            batteries[MODEL_IDS.index(model_id)]),
        all_model_batteries=copy.deepcopy(batteries),
        salt_commitment=copy.deepcopy(common["salt_binding"]))


def _load_common_inputs(protocol_path, freeze_path, gate_path, sample_path,
                        assembly_path, battery_paths, salt_path):
    """Load every committed, pre-score boundary and freeze live identity."""
    if not source_clean():
        raise V2BError("source tree dirty before confirmation scoring")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation scoring requires canonical protocol")
    if not isinstance(battery_paths, (list, tuple)) \
            or len(battery_paths) != len(MODEL_IDS) \
            or len({os.path.realpath(path) for path in battery_paths}) != \
            len(MODEL_IDS):
        raise V2BError("scoring requires exactly four distinct batteries")
    program_path = os.path.join(BASE, PROGRAM)
    battery_program_path = os.path.join(
        BASE, "v2b_nll_confirmation_battery.py")
    committed_paths = [
        protocol_path, freeze_path, gate_path, sample_path, assembly_path,
        *battery_paths, salt_path, program_path, battery_program_path,
        os.path.join(BASE, "eval_paired.py"),
        os.path.join(BASE, "eval_incontext.py"),
        os.path.join(BASE, "layout.py"), LOCK_FILE, FREEZE_FILE]
    for path in committed_paths:
        require_committed(path)
    commit, tree = head_commit(), source_tree_hash()
    if not _hex(commit, 40) or not _hex(tree):
        raise V2BError("cannot establish confirmation scoring source")

    protocol, _ = load_protocol(protocol_path)
    _validate_protocol_contract(protocol)
    from prepare_v2b_nll_confirmation_gate import (
        load_implementation_freeze, validate_reduced_gate)
    from prepare_v2b_nll_confirmation_assembly import (
        _validate_sample, validate_assembly)
    freeze, freeze_binding = load_implementation_freeze(
        freeze_path, protocol)
    gate_binding, gate = artifact_binding(gate_path, SOURCE_GATE_SCHEMA)
    sample_binding, sample = artifact_binding(sample_path, SAMPLE_SCHEMA)
    assembly_binding, assembly = artifact_binding(
        assembly_path, ASSEMBLY_SCHEMA)
    validate_reduced_gate(gate, protocol)
    _validate_sample(sample, protocol, sample_binding, gate_binding,
                     freeze_binding)
    validate_assembly(assembly, protocol, sample)
    expected_assembly_bindings = dict(
        implementation_freeze=freeze_binding,
        bound_sample=sample_binding, source_gate=gate_binding)
    if gate.get("bindings", {}).get("implementation_freeze") != \
            freeze_binding \
            or assembly.get("bindings") != expected_assembly_bindings:
        raise V2BError("gate/sample/assembly predecessor binding drift")

    core_bindings = dict(
        implementation_freeze=freeze_binding,
        bound_sample=sample_binding, source_gate=gate_binding,
        assembly=assembly_binding)
    battery_by_model = {}
    for path in battery_paths:
        binding, battery = artifact_binding(path, BATTERY_SCHEMA)
        model_id = battery.get("model", {}).get("id") \
            if isinstance(battery, dict) else None
        if model_id not in MODEL_IDS or model_id in battery_by_model:
            raise V2BError("battery ladder has an unknown/duplicate model")
        normalized = normalize_battery(
            battery, protocol, assembly, core_bindings)
        battery_by_model[model_id] = dict(
            path=os.path.abspath(path), binding=binding, value=battery,
            normalized=normalized)
    if set(battery_by_model) != set(MODEL_IDS):
        raise V2BError("battery ladder is incomplete")
    ordered_batteries = [battery_by_model[model_id]
                         for model_id in MODEL_IDS]
    battery_bindings = [row["binding"] for row in ordered_batteries]

    from prepare_v2b_nll_confirmation_salt import load_commitment
    salt, salt_digest = load_commitment(salt_path)
    salt_binding = dict(path=os.path.abspath(salt_path), schema=SALT_SCHEMA,
                        sha256=salt_digest)
    expected_salt_bindings = dict(
        implementation_freeze=freeze_binding,
        bound_sample=sample_binding, assembly=assembly_binding)
    if salt.get("bindings") != expected_salt_bindings:
        raise V2BError("salt commitment predecessor binding drift")
    adoption = salt_adoption_commit(salt_path)
    sequence = salt_sequence(adoption, commit)

    environment = _environment_identity()
    battery_program_sha = sha256_file(battery_program_path)
    for row in ordered_batteries:
        battery = row["value"]
        execution = row["normalized"]["execution"]
        for name, observed in environment.items():
            if execution[name] != observed:
                raise V2BError(
                    f"battery/live environment drift for {name}")
        if execution["source_tree_hash"] != tree \
                or not git_is_ancestor(
                    execution["battery_source_commit"], commit) \
                or battery["generator"]["program_sha256"] != \
                battery_program_sha:
            raise V2BError("battery source/harness is not the scoring source")
    shared_fields = (
        "environment_fingerprint", "requirements_lock_sha256",
        "environment_freeze_sha256", "measurement_harness_sha256",
        "numerical_harness_sha256", "source_tree_hash")
    first_execution = ordered_batteries[0]["normalized"]["execution"]
    if any(any(row["normalized"]["execution"][name] !=
               first_execution[name] for name in shared_fields)
           for row in ordered_batteries[1:]):
        raise V2BError("four batteries disagree on shared execution identity")

    digest_paths = [
        ("protocol", protocol_path), ("implementation_freeze", freeze_path),
        ("source_gate", gate_path), ("bound_sample", sample_path),
        ("assembly", assembly_path),
        *((f"battery:{model_id}", battery_by_model[model_id]["path"])
          for model_id in MODEL_IDS),
        ("salt_commitment", salt_path),
        ("requirements_lock", LOCK_FILE),
        ("environment_freeze", FREEZE_FILE),
    ]
    common = dict(
        protocol=protocol, protocol_path=os.path.abspath(protocol_path),
        freeze=freeze, freeze_path=os.path.abspath(freeze_path),
        freeze_binding=freeze_binding, gate=gate,
        gate_path=os.path.abspath(gate_path), gate_binding=gate_binding,
        sample=sample, sample_path=os.path.abspath(sample_path),
        sample_binding=sample_binding, assembly=assembly,
        assembly_path=os.path.abspath(assembly_path),
        assembly_binding=assembly_binding,
        battery_by_model=battery_by_model,
        battery_bindings=battery_bindings, salt=salt,
        salt_path=os.path.abspath(salt_path), salt_binding=salt_binding,
        adoption_commit=adoption, sequence=sequence,
        source_commit=commit, source_tree_hash=tree,
        environment=environment,
        input_digests=_path_digest_table(digest_paths))
    for model_id in MODEL_IDS:
        _bindings(_score_bindings(common, model_id), model_id)
    _guard_common(common)
    return common


def _guard_common(common):
    """Recheck bytes, HEAD/tree, harness, and software after long work."""
    if not source_clean() \
            or head_commit() != common["source_commit"] \
            or source_tree_hash() != common["source_tree_hash"]:
        raise V2BError("confirmation scoring source changed during execution")
    for row in common["input_digests"]:
        if sha256_file(row["path"]) != row["sha256"]:
            raise V2BError(
                f"confirmation scoring input changed: {row['label']}")
    if _environment_identity() != common["environment"]:
        raise V2BError("confirmation scoring environment changed")
    salt_sequence(common["adoption_commit"], common["source_commit"])


def _assert_evidence_path(path):
    """Prevent outputs inside tracked source outside the evidence subtree."""
    absolute = os.path.abspath(path)
    real_base = os.path.realpath(BASE)
    real = os.path.realpath(absolute)
    try:
        inside = os.path.commonpath((real_base, real)) == real_base
    except ValueError:
        inside = False
    if inside:
        relative = os.path.relpath(real, real_base).replace(os.sep, "/")
        if relative != "results_v2" \
                and not relative.startswith("results_v2/"):
            raise V2BError("confirmation evidence output lies in source tree")
    return absolute


def _prepare_target_directory(directory):
    directory = _assert_evidence_path(directory)
    existed = os.path.exists(directory)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as err:
        raise V2BError(f"cannot create target directory: {err}") from err
    if not os.path.isdir(directory) or os.path.islink(directory):
        raise V2BError("target directory is not a real directory")
    if not existed:
        os.chmod(directory, 0o700)
    return directory


def _partial_target_files(directory):
    try:
        names = sorted(os.listdir(directory))
    except OSError as err:
        raise V2BError(f"cannot list target directory: {err}") from err
    found = {}
    for name in names:
        # Atomic publishers briefly expose their private hard-link source;
        # the exact model reducer permits no such residue after scoring.
        if name.startswith(".v2b-") and name.endswith(".json"):
            continue
        match = re.fullmatch(r"target-(\d{4})\.json", name)
        if match is None:
            raise V2BError(f"unexpected file in target directory: {name}")
        index = int(match.group(1))
        if index >= N_TARGETS or name != _target_filename(index):
            raise V2BError(f"out-of-cohort target artifact: {name}")
        found[index] = os.path.join(directory, name)
    return found


def _verify_runtime(battery, normalized, runtime, model_files,
                    tokenizer_files):
    expected_execution = normalized["execution"]
    expected_runtime = {
        "model_id": expected_execution["model_id"],
        "model_name": expected_execution["model_name"],
        "revision": expected_execution["revision"],
        "model_class": expected_execution["model_class"],
        "n_parameters": expected_execution["n_parameters"],
        "tokenizer_class": expected_execution["tokenizer_class"],
        "vocab_size": expected_execution["vocab_size"],
        "max_position_embeddings":
            expected_execution["max_position_embeddings"],
        "attention": expected_execution["attention"],
    }
    if runtime != expected_runtime \
            or model_files != battery["model"]["files"] \
            or tokenizer_files != battery["tokenizer"]["files"]:
        raise V2BError("loaded model/tokenizer differs from sealed battery")


def score(common, model_id, shard_index, target_directory,
          shard_count=None):
    """Production target-atomic scorer for one battery-selected shard."""
    if model_id not in MODEL_IDS:
        raise V2BError("unknown confirmation model")
    battery_row = common["battery_by_model"][model_id]
    normalized = battery_row["normalized"]
    expected_count = normalized["shard_count"]
    if shard_count is not None and shard_count != expected_count:
        raise V2BError("CLI shard count differs from sealed battery")
    shard_count = expected_count
    start, end = shard_bounds(shard_index, shard_count)
    directory = _prepare_target_directory(target_directory)
    bindings = _score_bindings(common, model_id)
    existing = _partial_target_files(directory)
    for index, path in existing.items():
        compatible_target(
            path, common["protocol"], common["assembly"], index, bindings,
            normalized["model"], normalized["execution"], shard_count,
            ancestor_fn=git_is_ancestor,
            fit_by_pair=normalized["fit_by_pair"])
    missing = [index for index in range(start, end) if index not in existing]
    if not missing:
        _guard_common(common)
        return dict(n_assigned=end - start, n_scored=0,
                    n_resumed=end - start, shard_index=shard_index,
                    shard_count=shard_count, target_directory=directory)

    from prepare_v2b_nll_confirmation_assembly import materialize
    materialized = materialize(
        common["assembly_path"], common["sample_path"],
        common["freeze_path"], common["gate_path"],
        common["protocol_path"])
    ordered_keys = common["assembly"]["ordered_target_keys"]["keys"]
    if list(materialized) != ordered_keys:
        raise V2BError("materializer target order differs from assembly")
    from v2b_nll_confirmation_battery import (
        _load_model_and_tokenizer, snapshot_file_manifests)
    model, tokenizer, runtime, model_files, tokenizer_files, snapshot = \
        _load_model_and_tokenizer(normalized["model"])
    scored = resumed = 0
    try:
        _verify_runtime(battery_row["value"], normalized, runtime,
                        model_files, tokenizer_files)
        scorer = _real_scorer(
            model, tokenizer, normalized["execution"]["device"],
            normalized["execution"]["max_position_embeddings"],
            normalized["execution"]["chunk_tokens"])
        generator = _generator(common["source_commit"],
                               common["source_tree_hash"])
        for index in range(start, end):
            path = os.path.join(directory, _target_filename(index))
            if os.path.exists(path):
                compatible_target(
                    path, common["protocol"], common["assembly"], index,
                    bindings, normalized["model"], normalized["execution"],
                    shard_count, ancestor_fn=git_is_ancestor,
                    fit_by_pair=normalized["fit_by_pair"])
                resumed += 1
                continue
            target_key = ordered_keys[index]
            value = build_target_score(
                common["protocol"], bindings, normalized["model"],
                normalized["execution"], common["sequence"],
                common["assembly"], index, materialized[target_key],
                normalized["fit_by_pair"], shard_index, shard_count, scorer,
                generator)
            try:
                _write_new_0600(path, value)
                scored += 1
            except V2BError as err:
                if "refusing to overwrite evidence artifact" not in \
                        str(err) or not os.path.exists(path):
                    raise
                compatible_target(
                    path, common["protocol"], common["assembly"], index,
                    bindings, normalized["model"], normalized["execution"],
                    shard_count, ancestor_fn=git_is_ancestor,
                    fit_by_pair=normalized["fit_by_pair"])
                resumed += 1
        model_files_post, tokenizer_files_post = snapshot_file_manifests(
            snapshot, normalized["model"]["revision"])
        if model_files_post != model_files \
                or tokenizer_files_post != tokenizer_files:
            raise V2BError("model/tokenizer snapshot changed during scoring")
        _guard_common(common)
    finally:
        del model
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
    return dict(n_assigned=end - start, n_scored=scored,
                n_resumed=resumed, shard_index=shard_index,
                shard_count=shard_count, target_directory=directory)


def reduce_model(common, model_id, target_directory, out_path):
    if model_id not in MODEL_IDS:
        raise V2BError("unknown confirmation model")
    normalized = common["battery_by_model"][model_id]["normalized"]
    bindings = _score_bindings(common, model_id)
    target_inputs = _load_target_inputs(target_directory)
    value = build_model_complete(
        common["protocol"], common["assembly"], bindings,
        normalized["model"], normalized["execution"], common["sequence"],
        normalized["shard_count"], target_inputs,
        _generator(common["source_commit"], common["source_tree_hash"]),
        ancestor_fn=git_is_ancestor,
        fit_by_pair=normalized["fit_by_pair"])
    _guard_common(common)
    out_path = _assert_evidence_path(out_path)
    digest = _write_new_0600(out_path, value)
    return value, digest


def _load_model_inputs(paths, common):
    if not isinstance(paths, (list, tuple)) or len(paths) != len(MODEL_IDS) \
            or len({os.path.realpath(path) for path in paths}) != \
            len(MODEL_IDS):
        raise V2BError("study reducer requires four distinct model files")
    by_model = {}
    for path in paths:
        value, digest = load_json(path, MODEL_COMPLETE_SCHEMA)
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        if not stat.S_ISREG(os.lstat(path).st_mode) or mode != 0o600:
            raise V2BError("model completion is not regular mode0600")
        model_id = value.get("model", {}).get("id") \
            if isinstance(value, dict) else None
        if model_id not in MODEL_IDS or model_id in by_model:
            raise V2BError("study model-completion ladder drift")
        normalized = common["battery_by_model"][model_id]["normalized"]
        validate_model_complete(
            value, common["protocol"], common["assembly"],
            _score_bindings(common, model_id), normalized["model"],
            normalized["execution"], ancestor_fn=git_is_ancestor)
        by_model[model_id] = dict(path=os.path.abspath(path), sha256=digest,
                                  value=value)
    if set(by_model) != set(MODEL_IDS):
        raise V2BError("study model-completion ladder is incomplete")
    return [by_model[model_id] for model_id in MODEL_IDS]


def reduce_study(common, model_complete_paths, out_path):
    model_inputs = _load_model_inputs(model_complete_paths, common)
    value = build_study_complete(
        common["protocol"], common["assembly"], model_inputs,
        _generator(common["source_commit"], common["source_tree_hash"]),
        ancestor_fn=git_is_ancestor)
    _guard_common(common)
    out_path = _assert_evidence_path(out_path)
    digest = _write_new_0600(out_path, value)
    return value, digest


def _add_common_arguments(parser):
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--source-gate", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument(
        "--battery", action="append", required=True,
        help="repeat exactly four times; model order is validated")
    parser.add_argument("--salt-commitment", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)


def _common_from_args(args):
    return _load_common_inputs(
        args.protocol, args.implementation_freeze, args.source_gate,
        args.sample, args.assembly, args.battery, args.salt_commitment)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    score_parser = modes.add_parser("score")
    _add_common_arguments(score_parser)
    score_parser.add_argument("--model-id", choices=MODEL_IDS, required=True)
    score_parser.add_argument("--shard-index", type=int, required=True)
    score_parser.add_argument(
        "--shard-count", type=int,
        help="optional assertion; must equal the battery recommendation")
    score_parser.add_argument("--target-dir", required=True)

    model_parser = modes.add_parser("reduce-model")
    _add_common_arguments(model_parser)
    model_parser.add_argument("--model-id", choices=MODEL_IDS, required=True)
    model_parser.add_argument("--target-dir", required=True)
    model_parser.add_argument("--out", required=True)

    study_parser = modes.add_parser("reduce-study")
    _add_common_arguments(study_parser)
    study_parser.add_argument(
        "--model-complete", action="append", required=True,
        help="repeat exactly four times; canonical model order is validated")
    study_parser.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    common = _common_from_args(args)
    if args.mode == "score":
        result = score(common, args.model_id, args.shard_index,
                       args.target_dir, args.shard_count)
        print("[v2b-confirmation-score] "
              f"{args.model_id} shard={result['shard_index']}/"
              f"{result['shard_count']} scored={result['n_scored']} "
              f"resumed={result['n_resumed']} -> "
              f"{result['target_directory']}")
    elif args.mode == "reduce-model":
        value, digest = reduce_model(
            common, args.model_id, args.target_dir, args.out)
        print("[v2b-confirmation-reduce-model] "
              f"{args.model_id} targets={value['n_targets']} -> "
              f"{args.out} ({digest[:12]})")
    else:
        value, digest = reduce_study(
            common, args.model_complete, args.out)
        print("[v2b-confirmation-reduce-study] "
              f"models={value['n_models']} targets={value['n_targets']} -> "
              f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        raise SystemExit(2)
