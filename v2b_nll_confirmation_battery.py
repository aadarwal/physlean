#!/usr/bin/env python3
"""Per-model pre-score instrument battery for the SymPy NLL confirmation.

The battery performs no confirmation-target NLL evaluation.  It first
materializes and tokenizes every structurally eligible registered prompt,
aborting on any model-position overflow.  Only then does it execute a fixed,
target-free synthetic production-kernel probe for determinism, causal masking,
throughput, and peak memory.  One write-once artifact is produced per exact
protocol model; all four must exist before the scorer may run.
"""
import argparse
import copy
import math
import os
import sys
import time

from prepare_v2b_nll_confirmation_assembly import (
    ASSEMBLY_SCHEMA, CELL_KEYS, CELL_ORDER, DIAGNOSTIC_CELLS, REQUIRED_CELLS,
    SAMPLE_SCHEMA_CONFIRMATION, SOURCE_GATE_SCHEMA, TARGET_KEYS,
    materialize as materialize_confirmation,
    validate_assembly,
)
from prepare_v2b_nll_confirmation_gate import (
    GATE_SCHEMA, load_reduced_gate, protocol_record,
)
from layout import PRODUCTION_CHUNK_TOKENS
from provenance import (
    BASE, FREEZE_FILE, LOCK_FILE, env_fingerprint, env_matches_freeze,
    env_matches_lock, gpu_info, harness_hash, head_commit, source_clean,
    source_tree_hash,
)
from v2b_a6_blind import require_committed
from v2b_common import (
    V2BError, artifact_binding, canonical_json_bytes, identity_key,
    sha256_bytes, sha256_file, sha256_json, sha256_sorted_json,
    validate_identity, write_new_json,
)
from v2b_nll_confirmation import (
    MODEL_ROWS, PROTOCOL_PATH, PROTOCOL_RAW_SHA256, SCORED_CELLS,
    load_protocol, validate_protocol,
)


BATTERY_SCHEMA = "v2b_nll_e2_confirmation_model_battery_v1"
BATTERY_STATE = "complete-pre-score-model-instrument-and-tokenizer-fit"
TOKENIZER_FIT_SCHEMA = "v2b_nll_e2_confirmation_tokenizer_fit_v1"
TOKENIZER_FILES_SCHEMA = "v2b_nll_e2_confirmation_tokenizer_files_v1"
MODEL_FILES_SCHEMA = "v2b_nll_e2_confirmation_model_files_v1"
INSTRUMENT_SCHEMA = "v2b_nll_e2_confirmation_synthetic_instrument_v1"
IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_e2_confirmation_implementation_freeze_v1"

PROGRAM = "v2b_nll_confirmation_battery.py"
REPO = "sympy"
LANGUAGE = "python"
N_TARGETS = 200
N_CELLS = 6
DTYPE = "bfloat16"
DEVICE = "cuda"
CHUNK_TOKENS = PRODUCTION_CHUNK_TOKENS
SYNTHETIC_TOKENS = 2 * CHUNK_TOKENS + 2
CAUSAL_POSITION = 2047
REPEAT_MAX_ABS = 1e-6
CAUSAL_PROTECTED_MAX_ABS = 1e-6
CAUSAL_DOWNSTREAM_MIN_ABS = 1e-6
MAX_MEMORY_FRACTION = 0.95
SHARD_WALLTIME_BUDGET_SECONDS = 4 * 60 * 60
SHARD_RUNTIME_SAFETY_FACTOR = 1.5
MAX_RECOMMENDED_SHARDS = 32

NUMERICAL_HARNESS_FILES = (
    "eval_v2b_nll_confirmation.py",
    "eval_paired.py",
    "eval_incontext.py",
    "layout.py",
)

ARTIFACT_KEYS = {"path", "schema", "sha256"}
TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "model", "tokenizer",
    "execution", "tokenizer_fit", "synthetic_instrument", "sharding",
    "input_ledger", "generator",
}
FIT_ROW_KEYS = {
    "target_index", "target_key", "cell_index", "cell_id",
    "structurally_eligible", "status", "cell_manifest_sha256",
    "context_bytes", "context_sha256", "prefix_bytes", "prefix_sha256",
    "body_bytes", "body_sha256", "prompt_bytes", "prompt_sha256",
    "n_prompt_tokens", "max_position_embeddings", "exact_body_bytes",
    "scored_body_bytes", "straddled_body_bytes",
    "n_boundary_straddle_tokens", "boundary_signature",
}
LEDGER_KEYS = {
    "algorithm", "n_entries", "entries", "entries_sha256",
    "pre_entries_sha256", "post_entries_sha256", "unchanged",
}
EXECUTION_KEYS = {
    "dtype", "device", "attention", "chunk_tokens",
    "max_position_embeddings", "environment_fingerprint",
    "requirements_lock_sha256", "environment_freeze_sha256",
    "environment_lock_matches", "environment_freeze_matches",
    "measurement_harness_sha256", "numerical_harness_sha256",
    "source_commit", "source_tree_hash", "gpu",
}
FIT_KEYS = {
    "schema", "state", "cell_order", "ordered_target_keys", "n_targets",
    "n_cell_records", "n_tokenized_prompts",
    "n_structurally_ineligible_not_tokenized", "max_position_embeddings",
    "required_eligible_n_by_cell", "diagnostic_eligible_n_by_cell",
    "omitted_n_by_cell", "total_prompt_tokens", "total_prompt_bytes",
    "all_tokenized_prompts_within_limit", "token_byte_conservation",
    "rows", "rows_sha256",
}
SHARDING_KEYS = {
    "decision", "total_eligible_prompt_tokens", "benchmark_tokens",
    "benchmark_seconds", "benchmark_tokens_per_second",
    "raw_projected_score_seconds", "runtime_safety_factor",
    "safe_projected_score_seconds", "per_shard_walltime_budget_seconds",
    "recommended_shard_count", "maximum_recommended_shards",
    "decision_reason",
}
BATTERY_INPUT_LABELS = (
    "input:assembly",
    "input:bound_sample",
    "input:environment_freeze",
    "input:implementation_freeze",
    "input:protocol",
    "input:requirements_lock",
    "input:source_gate",
)


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _path_matches(left, right):
    if not isinstance(left, str) or not left \
            or not isinstance(right, str) or not right:
        return False
    a, b = os.path.normpath(left), os.path.normpath(right)
    return a == b or a.endswith(os.sep + b) or b.endswith(os.sep + a)


def _artifact_row(value, schema, label):
    _exact_keys(value, ARTIFACT_KEYS, label)
    if value["schema"] != schema or not _hex(value["sha256"]) \
            or not isinstance(value["path"], str) or not value["path"]:
        raise V2BError(f"malformed {label}")
    return value


def _same_artifact(left, right, schema, label):
    _artifact_row(left, schema, label)
    _artifact_row(right, schema, label)
    if left["sha256"] != right["sha256"] \
            or not _path_matches(left["path"], right["path"]):
        raise V2BError(f"{label} binding drift")


def numerical_harness_hash(base=None):
    """Hash the exact confirmation scoring numerical harness."""
    root = os.path.abspath(base or BASE)
    rows = []
    for name in NUMERICAL_HARNESS_FILES:
        path = os.path.join(root, name)
        try:
            digest = sha256_file(path)
        except OSError as err:
            raise V2BError(f"cannot hash numerical harness {path}: {err}") \
                from err
        rows.append([name, digest])
    return sha256_bytes(canonical_json_bytes(rows))


def _model_row(protocol, model_id):
    validate_protocol(protocol)
    expected = [dict(id=row[0], name=row[1], revision=row[2],
                     nominal_billions=row[3], role=row[4])
                for row in MODEL_ROWS]
    if protocol.get("models") != expected:
        raise V2BError("confirmation model ladder drift")
    matches = [row for row in expected if row["id"] == model_id]
    if len(matches) != 1:
        raise V2BError(f"unknown confirmation model id {model_id!r}")
    return matches[0]


def _validate_attention(value):
    if not isinstance(value, dict) or set(value) != {
            "implementation", "model_type", "sliding_window",
            "layer_types"} \
            or not isinstance(value["implementation"], str) \
            or not value["implementation"] \
            or not isinstance(value["model_type"], str) \
            or not value["model_type"] \
            or value["sliding_window"] is not None and (
                not isinstance(value["sliding_window"], int)
                or isinstance(value["sliding_window"], bool)
                or value["sliding_window"] <= 0) \
            or not isinstance(value["layer_types"], str):
        raise V2BError("runtime attention identity drift")
    return value


def _validate_freeze(value, binding, protocol):
    _artifact_row(binding, IMPLEMENTATION_FREEZE_SCHEMA,
                  "implementation freeze")
    if not isinstance(value, dict) \
            or value.get("schema") != IMPLEMENTATION_FREEZE_SCHEMA \
            or value.get("study_id") != protocol["study_id"] \
            or value.get("protocol") != protocol_record():
        raise V2BError("implementation freeze study/protocol drift")
    return value


def _validate_assembly_consumer(assembly, protocol, bindings):
    if not isinstance(assembly, dict) \
            or assembly.get("schema") != ASSEMBLY_SCHEMA \
            or assembly.get("study_id") != protocol["study_id"] \
            or assembly.get("repo") != REPO \
            or assembly.get("language") != LANGUAGE \
            or assembly.get("corpus_git_sha") != \
            protocol["scope"]["corpus_git_sha"] \
            or assembly.get("protocol") != protocol_record() \
            or assembly.get("cell_order") != list(SCORED_CELLS) \
            or assembly.get("required_cells") != list(REQUIRED_CELLS) \
            or assembly.get("diagnostic_cells") != list(DIAGNOSTIC_CELLS):
        raise V2BError("confirmation assembly consumer identity/grid drift")
    assembly_bindings = assembly.get("bindings")
    if not isinstance(assembly_bindings, dict) or set(assembly_bindings) != {
            "implementation_freeze", "bound_sample", "source_gate"}:
        raise V2BError("confirmation assembly predecessor binding drift")
    _same_artifact(
        assembly_bindings["implementation_freeze"],
        bindings["implementation_freeze"], IMPLEMENTATION_FREEZE_SCHEMA,
        "assembly implementation freeze")
    _same_artifact(assembly_bindings["bound_sample"],
                   bindings["bound_sample"], SAMPLE_SCHEMA_CONFIRMATION,
                   "assembly bound sample")
    _same_artifact(assembly_bindings["source_gate"],
                   bindings["source_gate"], SOURCE_GATE_SCHEMA,
                   "assembly source gate")
    targets = assembly.get("targets")
    key_record = assembly.get("ordered_target_keys")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or assembly.get("n_targets") != N_TARGETS \
            or assembly.get("targets_sha256") != sha256_sorted_json(targets) \
            or not isinstance(key_record, dict) \
            or set(key_record) != {"n", "sha256", "keys"} \
            or key_record.get("n") != N_TARGETS \
            or key_record.get("sha256") != \
            sha256_json(key_record.get("keys")):
        raise V2BError("confirmation assembly target ledger drift")
    observed = []
    for index, target in enumerate(targets):
        _exact_keys(target, TARGET_KEYS, f"assembly target[{index}]")
        identity = validate_identity(LANGUAGE, target["identity"])
        key = identity_key(LANGUAGE, identity)
        if target["key"] != key or target["module"] != identity[0] \
                or not isinstance(target["prefix_bytes"], int) \
                or target["prefix_bytes"] <= 0 \
                or not _hex(target["prefix_sha256"]) \
                or not isinstance(target["body_bytes"], int) \
                or target["body_bytes"] <= 0 \
                or not _hex(target["body_sha256"]):
            raise V2BError(f"assembly target identity/blob drift at {index}")
        cells = target.get("cells")
        if not isinstance(cells, list) or len(cells) != N_CELLS \
                or target.get("cells_sha256") != sha256_sorted_json(cells):
            raise V2BError(f"assembly target cell ledger drift at {index}")
        for cell, expected_id in zip(cells, CELL_ORDER):
            _exact_keys(cell, CELL_KEYS, f"assembly cell {expected_id}")
            if cell["cell_id"] != expected_id:
                raise V2BError("assembly cell order drift")
        observed.append(key)
    if observed != key_record["keys"] or len(observed) != len(set(observed)):
        raise V2BError("assembly ordered target keys drift")
    return targets


def _bound_blob(blob, n_bytes, digest, label):
    if not isinstance(blob, bytes) or len(blob) != n_bytes \
            or sha256_bytes(blob) != digest:
        raise V2BError(f"materialized {label} bytes/hash drift")


def _default_body_ledger(text, offsets, body_start, token_ids):
    # Lazy import keeps synthetic CPU-only tests independent of transformers.
    from eval_paired import body_token_ledger
    return body_token_ledger(text, offsets, body_start, token_ids=token_ids)


def _tokenize_one(tokenizer, context, prefix, body, max_positions,
                  vocab_size, body_ledger_fn, label):
    try:
        context_text = context.decode("utf-8")
        prefix_text = prefix.decode("utf-8")
        body_text = body.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"{label} prompt is not UTF-8: {err}") from err
    text = context_text + prefix_text + body_text
    body_start = len(context_text) + len(prefix_text)
    try:
        encoded = tokenizer(text, add_special_tokens=False,
                            return_offsets_mapping=True)
    except NotImplementedError as err:
        raise V2BError("confirmation requires a fast tokenizer with offsets") \
            from err
    if not isinstance(encoded, dict) and not hasattr(encoded, "keys"):
        raise V2BError("tokenizer returned a non-mapping encoding")
    if "input_ids" not in encoded or "offset_mapping" not in encoded:
        raise V2BError("tokenizer omitted ids or offset mapping")
    ids = list(encoded["input_ids"])
    offsets = [list(row) for row in encoded["offset_mapping"]]
    if len(ids) < 2 or len(ids) != len(offsets) \
            or any(not isinstance(token, int) or isinstance(token, bool)
                   or not 0 <= token < vocab_size for token in ids):
        raise V2BError("tokenizer returned malformed/short prompt encoding")
    if len(ids) > max_positions:
        raise V2BError(
            f"{label} has {len(ids)} tokens, exceeding model maximum "
            f"{max_positions}; confirmation aborts without redraw")
    ledger = body_ledger_fn(text, offsets, body_start, ids)
    required = {
        "exact_body_bytes", "scored_body_bytes", "straddled_body_bytes",
        "n_boundary_straddle_tokens", "boundary_signature",
    }
    if not isinstance(ledger, dict) or not required <= set(ledger) \
            or ledger["exact_body_bytes"] != len(body) \
            or ledger["scored_body_bytes"] + \
            ledger["straddled_body_bytes"] != len(body) \
            or not _hex(ledger["boundary_signature"]):
        raise V2BError(f"{label} tokenizer byte-conservation drift")
    prompt = context + prefix + body
    return dict(
        prompt_bytes=len(prompt), prompt_sha256=sha256_bytes(prompt),
        n_prompt_tokens=len(ids), exact_body_bytes=ledger["exact_body_bytes"],
        scored_body_bytes=ledger["scored_body_bytes"],
        straddled_body_bytes=ledger["straddled_body_bytes"],
        n_boundary_straddle_tokens=ledger["n_boundary_straddle_tokens"],
        boundary_signature=ledger["boundary_signature"])


def build_tokenizer_fit_ledger(assembly, materialized, tokenizer,
                               max_position_embeddings,
                               vocab_size,
                               body_ledger_fn=None):
    """Tokenize every and only structurally eligible registered prompt."""
    if not isinstance(max_position_embeddings, int) \
            or isinstance(max_position_embeddings, bool) \
            or max_position_embeddings <= 0:
        raise V2BError("model has no positive max-position limit")
    if not isinstance(vocab_size, int) or isinstance(vocab_size, bool) \
            or vocab_size < 2:
        raise V2BError("model has no positive tokenizer/model vocabulary")
    targets = assembly.get("targets")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or not isinstance(materialized, dict):
        raise V2BError("tokenizer fit received malformed assembly/materialization")
    expected_keys = [target.get("key") for target in targets]
    if set(materialized) != set(expected_keys) \
            or len(materialized) != len(expected_keys):
        raise V2BError("materialization target set differs from assembly")
    ledger_fn = body_ledger_fn or _default_body_ledger
    rows = []
    eligible_counts = {cell: 0 for cell in CELL_ORDER}
    omitted_counts = {cell: 0 for cell in CELL_ORDER}
    total_tokens = total_prompt_bytes = 0
    for target_index, target in enumerate(targets):
        target_key = target["key"]
        blobs = materialized[target_key]
        if not isinstance(blobs, dict) or set(blobs) != {
                "prefix", "body", "cells"} \
                or not isinstance(blobs["cells"], dict) \
                or set(blobs["cells"]) != set(CELL_ORDER):
            raise V2BError(f"malformed materialized target {target_key}")
        prefix, body = blobs["prefix"], blobs["body"]
        _bound_blob(prefix, target["prefix_bytes"], target["prefix_sha256"],
                    f"{target_key} prefix")
        _bound_blob(body, target["body_bytes"], target["body_sha256"],
                    f"{target_key} body")
        for cell_index, (cell, cell_id) in enumerate(
                zip(target["cells"], CELL_ORDER)):
            context = blobs["cells"][cell_id]
            eligible = cell.get("eligible") is True
            required = cell_id in REQUIRED_CELLS
            if cell_id == "k1":
                if cell.get("role") != "intrinsic-control" \
                        or cell.get("required_for_fixed_n") is not True \
                        or cell.get("eligibility_basis") != \
                        "intrinsic-empty-context" \
                        or cell.get("budget_bytes") is not None \
                        or not eligible or cell.get("context_bytes") != 0 \
                        or cell.get("context_sha256") != sha256_bytes(b"") \
                        or context != b"":
                    raise V2BError("k1 intrinsic-empty-context invariant drift")
            elif required and not eligible:
                raise V2BError(f"required cell {cell_id} is ineligible")

            common = dict(
                target_index=target_index, target_key=target_key,
                cell_index=cell_index, cell_id=cell_id,
                structurally_eligible=eligible,
                cell_manifest_sha256=sha256_json(cell),
                context_bytes=cell.get("context_bytes"),
                context_sha256=cell.get("context_sha256"),
                prefix_bytes=target["prefix_bytes"],
                prefix_sha256=target["prefix_sha256"],
                body_bytes=target["body_bytes"],
                body_sha256=target["body_sha256"],
                max_position_embeddings=max_position_embeddings)
            if eligible:
                if not isinstance(cell.get("context_bytes"), int) \
                        or isinstance(cell.get("context_bytes"), bool) \
                        or cell["context_bytes"] < 0 \
                        or not _hex(cell.get("context_sha256")):
                    raise V2BError(f"eligible {cell_id} lacks context binding")
                _bound_blob(context, cell["context_bytes"],
                            cell["context_sha256"],
                            f"{target_key}/{cell_id} context")
                tokenized = _tokenize_one(
                    tokenizer, context, prefix, body,
                    max_position_embeddings, vocab_size, ledger_fn,
                    f"{target_key}/{cell_id}")
                row = dict(common, status="tokenized", **tokenized)
                eligible_counts[cell_id] += 1
                total_tokens += tokenized["n_prompt_tokens"]
                total_prompt_bytes += tokenized["prompt_bytes"]
            else:
                if required or cell_id not in DIAGNOSTIC_CELLS \
                        or context is not None \
                        or any(cell.get(name) is not None for name in (
                            "context_bytes", "context_sha256",
                            "utf8_shortfall_bytes")):
                    raise V2BError(
                        f"ineligible {cell_id} fabricated a scoreable prompt")
                row = dict(
                    common, status="structurally-ineligible-not-tokenized",
                    prompt_bytes=None, prompt_sha256=None,
                    n_prompt_tokens=None, exact_body_bytes=None,
                    scored_body_bytes=None, straddled_body_bytes=None,
                    n_boundary_straddle_tokens=None,
                    boundary_signature=None)
                omitted_counts[cell_id] += 1
            _exact_keys(row, FIT_ROW_KEYS, "tokenizer-fit row")
            rows.append(row)
    required_counts = {cell: eligible_counts[cell] for cell in REQUIRED_CELLS}
    if any(value != N_TARGETS for value in required_counts.values()) \
            or len(rows) != N_TARGETS * N_CELLS:
        raise V2BError("required confirmation prompts are not exact N=200")
    n_tokenized = sum(eligible_counts.values())
    n_omitted = sum(omitted_counts.values())
    if n_tokenized + n_omitted != len(rows):
        raise AssertionError("tokenizer-fit row accounting drift")
    return dict(
        schema=TOKENIZER_FIT_SCHEMA,
        state="complete-all-eligible-prompts-before-score",
        cell_order=list(CELL_ORDER),
        ordered_target_keys=copy.deepcopy(assembly["ordered_target_keys"]),
        n_targets=N_TARGETS, n_cell_records=len(rows),
        n_tokenized_prompts=n_tokenized,
        n_structurally_ineligible_not_tokenized=n_omitted,
        max_position_embeddings=max_position_embeddings,
        required_eligible_n_by_cell=required_counts,
        diagnostic_eligible_n_by_cell={
            cell: eligible_counts[cell] for cell in DIAGNOSTIC_CELLS},
        omitted_n_by_cell=omitted_counts,
        total_prompt_tokens=total_tokens,
        total_prompt_bytes=total_prompt_bytes,
        all_tokenized_prompts_within_limit=True,
        token_byte_conservation=True,
        rows=rows, rows_sha256=sha256_sorted_json(rows))


def _validate_tokenizer_fit(value, assembly, max_positions):
    _exact_keys(value, FIT_KEYS, "tokenizer fit")
    rows = value.get("rows")
    target_keys = assembly.get("ordered_target_keys")
    if value.get("schema") != TOKENIZER_FIT_SCHEMA \
            or value.get("state") != \
            "complete-all-eligible-prompts-before-score" \
            or value.get("cell_order") != list(CELL_ORDER) \
            or value.get("ordered_target_keys") != target_keys \
            or value.get("n_targets") != N_TARGETS \
            or value.get("n_cell_records") != N_TARGETS * N_CELLS \
            or value.get("max_position_embeddings") != max_positions \
            or value.get("all_tokenized_prompts_within_limit") is not True \
            or value.get("token_byte_conservation") is not True \
            or not isinstance(rows, list) \
            or len(rows) != N_TARGETS * N_CELLS \
            or value.get("rows_sha256") != sha256_sorted_json(rows):
        raise V2BError("tokenizer-fit identity/count/hash drift")
    eligible_counts = {cell: 0 for cell in CELL_ORDER}
    omitted_counts = {cell: 0 for cell in CELL_ORDER}
    total_tokens = total_bytes = 0
    for flat_index, row in enumerate(rows):
        _exact_keys(row, FIT_ROW_KEYS, f"tokenizer-fit row[{flat_index}]")
        target_index, cell_index = divmod(flat_index, N_CELLS)
        target = assembly["targets"][target_index]
        cell = target["cells"][cell_index]
        cell_id = CELL_ORDER[cell_index]
        eligible = cell["eligible"] is True
        common_ok = row["target_index"] == target_index \
            and row["target_key"] == target["key"] \
            and row["cell_index"] == cell_index \
            and row["cell_id"] == cell_id \
            and row["structurally_eligible"] is eligible \
            and row["cell_manifest_sha256"] == sha256_json(cell) \
            and row["context_bytes"] == cell["context_bytes"] \
            and row["context_sha256"] == cell["context_sha256"] \
            and row["prefix_bytes"] == target["prefix_bytes"] \
            and row["prefix_sha256"] == target["prefix_sha256"] \
            and row["body_bytes"] == target["body_bytes"] \
            and row["body_sha256"] == target["body_sha256"] \
            and row["max_position_embeddings"] == max_positions
        if not common_ok:
            raise V2BError("tokenizer-fit assembly binding drift")
        prompt_fields = (
            "prompt_bytes", "prompt_sha256", "n_prompt_tokens",
            "exact_body_bytes", "scored_body_bytes",
            "straddled_body_bytes", "n_boundary_straddle_tokens",
            "boundary_signature")
        if eligible:
            if row["status"] != "tokenized" \
                    or not isinstance(row["prompt_bytes"], int) \
                    or row["prompt_bytes"] <= 0 \
                    or not _hex(row["prompt_sha256"]) \
                    or not isinstance(row["n_prompt_tokens"], int) \
                    or not 2 <= row["n_prompt_tokens"] <= max_positions \
                    or row["exact_body_bytes"] != target["body_bytes"] \
                    or not isinstance(row["scored_body_bytes"], int) \
                    or not isinstance(row["straddled_body_bytes"], int) \
                    or row["scored_body_bytes"] + \
                    row["straddled_body_bytes"] != target["body_bytes"] \
                    or not isinstance(row["n_boundary_straddle_tokens"], int) \
                    or row["n_boundary_straddle_tokens"] < 0 \
                    or not _hex(row["boundary_signature"]):
                raise V2BError("malformed tokenized tokenizer-fit row")
            eligible_counts[cell_id] += 1
            total_tokens += row["n_prompt_tokens"]
            total_bytes += row["prompt_bytes"]
        else:
            if row["status"] != \
                    "structurally-ineligible-not-tokenized" \
                    or cell_id not in DIAGNOSTIC_CELLS \
                    or any(row[name] is not None for name in prompt_fields):
                raise V2BError("ineligible diagnostic was silently tokenized")
            omitted_counts[cell_id] += 1
    required = {cell: eligible_counts[cell] for cell in REQUIRED_CELLS}
    diagnostics = {cell: eligible_counts[cell]
                   for cell in DIAGNOSTIC_CELLS}
    if value["required_eligible_n_by_cell"] != required \
            or any(count != N_TARGETS for count in required.values()) \
            or value["diagnostic_eligible_n_by_cell"] != diagnostics \
            or value["omitted_n_by_cell"] != omitted_counts \
            or value["n_tokenized_prompts"] != sum(eligible_counts.values()) \
            or value["n_structurally_ineligible_not_tokenized"] != \
            sum(omitted_counts.values()) \
            or value["total_prompt_tokens"] != total_tokens \
            or value["total_prompt_bytes"] != total_bytes:
        raise V2BError("tokenizer-fit aggregate drift")
    return value


def _validate_file_manifest(value, schema, revision, label):
    _exact_keys(value, {"schema", "revision", "n_files", "files",
                        "files_sha256"}, label)
    files = value["files"]
    if value["schema"] != schema or value["revision"] != revision \
            or not isinstance(files, list) or not files \
            or value["n_files"] != len(files) \
            or value["files_sha256"] != sha256_sorted_json(files):
        raise V2BError(f"{label} header/hash drift")
    rels = []
    for row in files:
        _exact_keys(row, {"path", "bytes", "sha256"}, f"{label} file")
        if not isinstance(row["path"], str) or not row["path"] \
                or os.path.isabs(row["path"]) \
                or os.path.normpath(row["path"]) != row["path"] \
                or row["path"] == ".." \
                or row["path"].startswith(".." + os.sep) \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError(f"malformed {label} file row")
        rels.append(row["path"])
    if rels != sorted(rels) or len(rels) != len(set(rels)):
        raise V2BError(f"{label} paths are not canonical unique order")
    return value


def _validate_snapshot_relation(model_files, tokenizer_files):
    model_by_path = {row["path"]: row for row in model_files["files"]}
    expected_tokenizer_files = [
        row for row in model_files["files"]
        if _is_tokenizer_snapshot_member(row["path"])]
    if "config.json" not in model_by_path \
            or not any(path.endswith((".safetensors", ".bin"))
                       for path in model_by_path) \
            or not any(row["path"].endswith("tokenizer_config.json")
                       for row in expected_tokenizer_files) \
            or tokenizer_files["files"] != expected_tokenizer_files:
        raise V2BError("model/tokenizer file manifests are incomplete or "
                       "not one exact snapshot")


def _is_tokenizer_snapshot_member(path):
    name = os.path.basename(path)
    return name in {
        "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
        "added_tokens.json", "vocab.json", "merges.txt", "tokenizer.model",
        "spiece.model", "chat_template.jinja",
    } or name.startswith("tokenizer") or name.endswith(".tiktoken")


def snapshot_file_manifests(snapshot_path, revision):
    """Hash the exact local revision snapshot and its tokenizer subset."""
    root = os.path.abspath(snapshot_path)
    if not os.path.isdir(root) or os.path.basename(root) != revision:
        raise V2BError("model snapshot path is not the exact pinned revision")
    rows = []
    tokenizer_rows = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                raise V2BError(f"model snapshot member is not regular: {path}")
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            row = dict(path=rel, bytes=os.path.getsize(path),
                       sha256=sha256_file(path))
            rows.append(row)
            if _is_tokenizer_snapshot_member(rel):
                tokenizer_rows.append(row)
    rows.sort(key=lambda row: row["path"])
    tokenizer_rows.sort(key=lambda row: row["path"])
    if not rows or not tokenizer_rows \
            or not any(row["path"].endswith("tokenizer_config.json")
                       for row in tokenizer_rows):
        raise V2BError("pinned snapshot lacks complete tokenizer files")
    return (
        dict(schema=MODEL_FILES_SCHEMA, revision=revision,
             n_files=len(rows), files=rows,
             files_sha256=sha256_sorted_json(rows)),
        dict(schema=TOKENIZER_FILES_SCHEMA, revision=revision,
             n_files=len(tokenizer_rows), files=tokenizer_rows,
             files_sha256=sha256_sorted_json(tokenizer_rows)))


def _validate_instrument(value, execution, maximum_prompt_tokens):
    expected = {
        "schema", "state", "domain", "contains_confirmation_target_bytes",
        "dtype", "device", "chunk_tokens", "n_tokens", "seconds",
        "tokens_per_second", "repeat_max_abs", "repeat_threshold",
        "causal_position", "causal_n_protected", "causal_n_downstream",
        "causal_protected_max_abs", "causal_protected_threshold",
        "causal_downstream_max_abs", "causal_downstream_minimum",
        "token_byte_conservation", "peak_memory_allocated_bytes",
        "peak_memory_reserved_bytes", "device_total_memory_bytes",
        "peak_reserved_fraction", "maximum_memory_fraction",
        "maximum_eligible_prompt_tokens",
        "covers_maximum_eligible_prompt_tokens", "passed",
    }
    _exact_keys(value, expected, "synthetic instrument")
    finite = (value.get("seconds"), value.get("tokens_per_second"),
              value.get("repeat_max_abs"),
              value.get("causal_protected_max_abs"),
              value.get("causal_downstream_max_abs"),
              value.get("peak_reserved_fraction"))
    integer_fields = (
        "n_tokens", "causal_position", "causal_n_protected",
        "causal_n_downstream", "maximum_eligible_prompt_tokens")
    if value["schema"] != INSTRUMENT_SCHEMA \
            or value["state"] != "complete-target-free-production-path" \
            or value["domain"] != \
            "v2b-nll-confirmation-target-free-synthetic-v1" \
            or value["contains_confirmation_target_bytes"] is not False \
            or value["dtype"] != execution["dtype"] \
            or value["device"] != execution["device"] \
            or value["chunk_tokens"] != execution["chunk_tokens"] \
            or any(not isinstance(value[name], int)
                   or isinstance(value[name], bool) for name in integer_fields) \
            or value["n_tokens"] != max(
                SYNTHETIC_TOKENS, maximum_prompt_tokens) \
            or value["n_tokens"] > execution["max_position_embeddings"] \
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       and math.isfinite(x) for x in finite) \
            or value["seconds"] <= 0 or value["tokens_per_second"] <= 0 \
            or value["tokens_per_second"] != \
            (value["n_tokens"] - 1) / value["seconds"] \
            or value["repeat_max_abs"] < 0 \
            or value["repeat_threshold"] != REPEAT_MAX_ABS \
            or value["repeat_max_abs"] > REPEAT_MAX_ABS \
            or value["causal_position"] != CAUSAL_POSITION \
            or value["causal_n_protected"] != CAUSAL_POSITION - 1 \
            or value["causal_n_downstream"] != \
            value["n_tokens"] - 1 - CAUSAL_POSITION \
            or value["causal_protected_threshold"] != \
            CAUSAL_PROTECTED_MAX_ABS \
            or value["causal_protected_max_abs"] < 0 \
            or value["causal_protected_max_abs"] > \
            CAUSAL_PROTECTED_MAX_ABS \
            or value["causal_downstream_minimum"] != \
            CAUSAL_DOWNSTREAM_MIN_ABS \
            or value["causal_downstream_max_abs"] <= \
            CAUSAL_DOWNSTREAM_MIN_ABS \
            or value["token_byte_conservation"] is not True \
            or not all(isinstance(value[name], int)
                       and not isinstance(value[name], bool)
                       and value[name] > 0 for name in (
                           "peak_memory_allocated_bytes",
                           "peak_memory_reserved_bytes",
                           "device_total_memory_bytes")) \
            or value["peak_memory_allocated_bytes"] > \
            value["peak_memory_reserved_bytes"] \
            or value["peak_reserved_fraction"] != \
            value["peak_memory_reserved_bytes"] / \
            value["device_total_memory_bytes"] \
            or value["maximum_memory_fraction"] != MAX_MEMORY_FRACTION \
            or value["peak_reserved_fraction"] > MAX_MEMORY_FRACTION \
            or value["maximum_eligible_prompt_tokens"] != \
            maximum_prompt_tokens \
            or value["covers_maximum_eligible_prompt_tokens"] is not True \
            or value["passed"] is not True:
        raise V2BError("synthetic instrument gate failed")
    return value


def recommend_shards(total_prompt_tokens, instrument):
    if not isinstance(total_prompt_tokens, int) \
            or isinstance(total_prompt_tokens, bool) \
            or total_prompt_tokens <= 0:
        raise V2BError("invalid tokenizer-fit token total for sharding")
    rate = instrument.get("tokens_per_second")
    n_tokens = instrument.get("n_tokens")
    seconds = instrument.get("seconds")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) \
            or not math.isfinite(rate) or rate <= 0 \
            or not isinstance(n_tokens, int) or isinstance(n_tokens, bool) \
            or n_tokens <= 1 \
            or not isinstance(seconds, (int, float)) \
            or isinstance(seconds, bool) or not math.isfinite(seconds) \
            or seconds <= 0 or rate != (n_tokens - 1) / seconds:
        raise V2BError("invalid synthetic throughput for sharding")
    raw_seconds = total_prompt_tokens / rate
    projected = raw_seconds * SHARD_RUNTIME_SAFETY_FACTOR
    count = max(1, math.ceil(projected / SHARD_WALLTIME_BUDGET_SECONDS))
    if count > MAX_RECOMMENDED_SHARDS:
        raise V2BError("synthetic benchmark requires more than 32 shards")
    return dict(
        decision="target-free-throughput-projection-v1",
        total_eligible_prompt_tokens=total_prompt_tokens,
        benchmark_tokens=n_tokens - 1,
        benchmark_seconds=seconds,
        benchmark_tokens_per_second=rate,
        raw_projected_score_seconds=raw_seconds,
        runtime_safety_factor=SHARD_RUNTIME_SAFETY_FACTOR,
        safe_projected_score_seconds=projected,
        per_shard_walltime_budget_seconds=SHARD_WALLTIME_BUDGET_SECONDS,
        recommended_shard_count=count,
        maximum_recommended_shards=MAX_RECOMMENDED_SHARDS,
        decision_reason=("single-shard-within-safety-budget" if count == 1
                         else "benchmark-requires-multiple-shards"))


def _validate_input_ledger(value, protocol_binding, bindings, execution):
    _exact_keys(value, LEDGER_KEYS, "battery input ledger")
    entries = value.get("entries")
    if value.get("algorithm") != "sha256-sorted-json-file-ledger-v1" \
            or value.get("unchanged") is not True \
            or not isinstance(entries, list) \
            or value.get("n_entries") != len(entries):
        raise V2BError("battery input ledger header drift")
    labels = []
    for row in entries:
        _exact_keys(row, {"label", "bytes", "sha256"},
                    "battery input ledger row")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError("malformed battery input ledger row")
        labels.append(row["label"])
    digest = sha256_sorted_json(entries)
    if labels != list(BATTERY_INPUT_LABELS) \
            or any(value.get(name) != digest for name in (
                "entries_sha256", "pre_entries_sha256",
                "post_entries_sha256")):
        raise V2BError("battery input ledger hash/equality drift")
    by_label = {row["label"]: row for row in entries}
    expected_digests = {
        "input:assembly": bindings["assembly"]["sha256"],
        "input:bound_sample": bindings["bound_sample"]["sha256"],
        "input:environment_freeze": execution[
            "environment_freeze_sha256"],
        "input:implementation_freeze": bindings[
            "implementation_freeze"]["sha256"],
        "input:protocol": protocol_binding["raw_sha256"],
        "input:requirements_lock": execution["requirements_lock_sha256"],
        "input:source_gate": bindings["source_gate"]["sha256"],
    }
    if any(by_label[label]["sha256"] != expected
           for label, expected in expected_digests.items()):
        raise V2BError("battery input ledger disagrees with bound inputs")
    return value


def build_battery_value(protocol, protocol_binding, freeze, bindings,
                        assembly, materialized, model_id, tokenizer,
                        model_files, tokenizer_files, runtime,
                        synthetic_instrument, execution_provenance,
                        input_ledger, generator, body_ledger_fn=None,
                        tokenizer_fit=None):
    """Pure constructor; callers provide already measured runtime evidence."""
    model_spec = _model_row(protocol, model_id)
    if protocol_binding != protocol_record():
        raise V2BError("battery protocol binding drift")
    _exact_keys(bindings, {"implementation_freeze", "bound_sample",
                           "source_gate", "assembly"}, "battery bindings")
    _artifact_row(bindings["implementation_freeze"],
                  IMPLEMENTATION_FREEZE_SCHEMA, "implementation freeze")
    _artifact_row(bindings["bound_sample"], SAMPLE_SCHEMA_CONFIRMATION,
                  "bound sample")
    _artifact_row(bindings["source_gate"], GATE_SCHEMA, "source gate")
    _artifact_row(bindings["assembly"], ASSEMBLY_SCHEMA, "assembly")
    _validate_freeze(freeze, bindings["implementation_freeze"], protocol)
    _validate_assembly_consumer(assembly, protocol, bindings)

    _exact_keys(runtime, {"model_id", "model_name", "revision",
                          "model_class", "n_parameters", "tokenizer_class",
                          "vocab_size", "max_position_embeddings",
                          "attention"}, "model runtime")
    if runtime["model_id"] != model_spec["id"] \
            or runtime["model_name"] != model_spec["name"] \
            or runtime["revision"] != model_spec["revision"] \
            or not isinstance(runtime["model_class"], str) \
            or not runtime["model_class"] \
            or not isinstance(runtime["tokenizer_class"], str) \
            or not runtime["tokenizer_class"] \
            or not isinstance(runtime["n_parameters"], int) \
            or isinstance(runtime["n_parameters"], bool) \
            or runtime["n_parameters"] <= 0 \
            or not isinstance(runtime["vocab_size"], int) \
            or isinstance(runtime["vocab_size"], bool) \
            or runtime["vocab_size"] < 2 \
            or not isinstance(runtime["max_position_embeddings"], int) \
            or runtime["max_position_embeddings"] < SYNTHETIC_TOKENS \
            or not isinstance(runtime["attention"], dict):
        raise V2BError("runtime model/revision/config drift")
    _validate_attention(runtime["attention"])
    _validate_file_manifest(
        model_files, MODEL_FILES_SCHEMA, model_spec["revision"],
        "model files")
    _validate_file_manifest(
        tokenizer_files, TOKENIZER_FILES_SCHEMA, model_spec["revision"],
        "tokenizer files")
    _validate_snapshot_relation(model_files, tokenizer_files)

    _exact_keys(execution_provenance, {
        "environment_fingerprint", "requirements_lock_sha256",
        "environment_freeze_sha256", "environment_lock_matches",
        "environment_freeze_matches", "measurement_harness_sha256",
        "numerical_harness_sha256", "source_commit", "source_tree_hash",
        "gpu"}, "execution provenance")
    if not _hex(execution_provenance["environment_fingerprint"]) \
            or not _hex(execution_provenance["requirements_lock_sha256"]) \
            or not _hex(execution_provenance["environment_freeze_sha256"]) \
            or execution_provenance["environment_lock_matches"] is not True \
            or execution_provenance["environment_freeze_matches"] is not True \
            or not _hex(execution_provenance["measurement_harness_sha256"]) \
            or not _hex(execution_provenance["numerical_harness_sha256"]) \
            or not _hex(execution_provenance["source_commit"], 40) \
            or not _hex(execution_provenance["source_tree_hash"]) \
            or not isinstance(execution_provenance["gpu"], dict):
        raise V2BError("execution provenance is incomplete")
    execution = dict(
        dtype=DTYPE, device=DEVICE,
        attention=copy.deepcopy(runtime["attention"]),
        chunk_tokens=CHUNK_TOKENS,
        max_position_embeddings=runtime["max_position_embeddings"],
        environment_fingerprint=
        execution_provenance["environment_fingerprint"],
        requirements_lock_sha256=
        execution_provenance["requirements_lock_sha256"],
        environment_freeze_sha256=
        execution_provenance["environment_freeze_sha256"],
        environment_lock_matches=True, environment_freeze_matches=True,
        measurement_harness_sha256=
        execution_provenance["measurement_harness_sha256"],
        numerical_harness_sha256=
        execution_provenance["numerical_harness_sha256"],
        source_commit=execution_provenance["source_commit"],
        source_tree_hash=execution_provenance["source_tree_hash"],
        gpu=copy.deepcopy(execution_provenance["gpu"]))
    if tokenizer_fit is None:
        tokenizer_fit = build_tokenizer_fit_ledger(
            assembly, materialized, tokenizer,
            runtime["max_position_embeddings"], runtime["vocab_size"],
            body_ledger_fn)
    _validate_tokenizer_fit(
        tokenizer_fit, assembly, runtime["max_position_embeddings"])
    maximum_prompt_tokens = max(
        row["n_prompt_tokens"] for row in tokenizer_fit["rows"]
        if row["status"] == "tokenized")
    _validate_instrument(
        synthetic_instrument, execution, maximum_prompt_tokens)
    sharding = recommend_shards(
        tokenizer_fit["total_prompt_tokens"], synthetic_instrument)
    _validate_input_ledger(
        input_ledger, protocol_binding, bindings, execution)
    _exact_keys(generator, {"program", "program_sha256", "source_commit",
                            "source_tree_hash"}, "battery generator")
    if generator["program"] != PROGRAM \
            or not _hex(generator["program_sha256"]) \
            or generator["source_commit"] != \
            execution_provenance["source_commit"] \
            or generator["source_tree_hash"] != \
            execution_provenance["source_tree_hash"]:
        raise V2BError("battery generator/provenance drift")
    value = dict(
        schema=BATTERY_SCHEMA, state=BATTERY_STATE,
        study_id=protocol["study_id"], repo=REPO, language=LANGUAGE,
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=copy.deepcopy(protocol_binding),
        bindings=copy.deepcopy(bindings), model=dict(
            **copy.deepcopy(model_spec), model_class=runtime["model_class"],
            n_parameters=runtime["n_parameters"],
            files=copy.deepcopy(model_files)),
        tokenizer=dict(
            tokenizer_class=runtime["tokenizer_class"],
            vocab_size=runtime["vocab_size"],
            files=copy.deepcopy(tokenizer_files)),
        execution=execution, tokenizer_fit=tokenizer_fit,
        synthetic_instrument=copy.deepcopy(synthetic_instrument),
        sharding=sharding, input_ledger=copy.deepcopy(input_ledger),
        generator=copy.deepcopy(generator))
    return validate_battery(value, protocol, bindings, assembly)


def validate_battery(value, protocol, expected_bindings, assembly):
    """Strict public scorer gate for one committed per-model battery."""
    validate_protocol(protocol)
    _exact_keys(value, TOP_KEYS, "confirmation model battery")
    if value.get("schema") != BATTERY_SCHEMA \
            or value.get("state") != BATTERY_STATE \
            or value.get("study_id") != protocol["study_id"] \
            or value.get("repo") != REPO or value.get("language") != LANGUAGE \
            or value.get("corpus_git_sha") != \
            protocol["scope"]["corpus_git_sha"] \
            or value.get("protocol") != protocol_record():
        raise V2BError("confirmation model battery identity drift")
    _exact_keys(expected_bindings, {
        "implementation_freeze", "bound_sample", "source_gate", "assembly"},
        "expected battery bindings")
    bindings = value["bindings"]
    _exact_keys(bindings, set(expected_bindings), "battery bindings")
    schemas = {
        "implementation_freeze": IMPLEMENTATION_FREEZE_SCHEMA,
        "bound_sample": SAMPLE_SCHEMA_CONFIRMATION,
        "source_gate": SOURCE_GATE_SCHEMA,
        "assembly": ASSEMBLY_SCHEMA,
    }
    for name, schema in schemas.items():
        _same_artifact(bindings[name], expected_bindings[name], schema,
                       f"battery {name}")
    _validate_assembly_consumer(assembly, protocol, bindings)

    model = value["model"]
    _exact_keys(model, {"id", "name", "revision", "nominal_billions",
                        "role", "model_class", "n_parameters", "files"},
                "battery model")
    spec = _model_row(protocol, model.get("id"))
    if any(model.get(name) != spec[name] for name in (
            "id", "name", "revision", "nominal_billions", "role")) \
            or not isinstance(model.get("model_class"), str) \
            or not model["model_class"] \
            or not isinstance(model.get("n_parameters"), int) \
            or isinstance(model["n_parameters"], bool) \
            or model["n_parameters"] <= 0:
        raise V2BError("battery model identity/runtime drift")
    _validate_file_manifest(model["files"], MODEL_FILES_SCHEMA,
                            spec["revision"], "battery model files")
    tokenizer = value["tokenizer"]
    _exact_keys(tokenizer, {"tokenizer_class", "vocab_size", "files"},
                "battery tokenizer")
    if not isinstance(tokenizer["tokenizer_class"], str) \
            or not tokenizer["tokenizer_class"] \
            or not isinstance(tokenizer["vocab_size"], int) \
            or isinstance(tokenizer["vocab_size"], bool) \
            or tokenizer["vocab_size"] < 2:
        raise V2BError("battery tokenizer identity drift")
    _validate_file_manifest(tokenizer["files"], TOKENIZER_FILES_SCHEMA,
                            spec["revision"], "battery tokenizer files")
    _validate_snapshot_relation(model["files"], tokenizer["files"])

    execution = value["execution"]
    _exact_keys(execution, EXECUTION_KEYS, "battery execution")
    attention = execution.get("attention")
    if execution.get("dtype") != DTYPE or execution.get("device") != DEVICE \
            or execution.get("chunk_tokens") != CHUNK_TOKENS \
            or not isinstance(execution.get("max_position_embeddings"), int) \
            or execution["max_position_embeddings"] < SYNTHETIC_TOKENS \
            or not isinstance(attention, dict) \
            or execution.get("environment_lock_matches") is not True \
            or execution.get("environment_freeze_matches") is not True \
            or any(not _hex(execution.get(name)) for name in (
                "environment_fingerprint", "requirements_lock_sha256",
                "environment_freeze_sha256", "measurement_harness_sha256",
                "numerical_harness_sha256", "source_tree_hash")) \
            or not _hex(execution.get("source_commit"), 40) \
            or not isinstance(execution.get("gpu"), dict) \
            or set(execution["gpu"]) != {"gpu_name", "gpu_driver"}:
        raise V2BError("battery execution identity/provenance drift")
    _validate_attention(attention)
    _validate_tokenizer_fit(
        value["tokenizer_fit"], assembly,
        execution["max_position_embeddings"])
    maximum_prompt_tokens = max(
        row["n_prompt_tokens"] for row in value["tokenizer_fit"]["rows"]
        if row["status"] == "tokenized")
    _validate_instrument(
        value["synthetic_instrument"], execution, maximum_prompt_tokens)
    expected_sharding = recommend_shards(
        value["tokenizer_fit"]["total_prompt_tokens"],
        value["synthetic_instrument"])
    _exact_keys(value["sharding"], SHARDING_KEYS, "battery sharding")
    if value["sharding"] != expected_sharding:
        raise V2BError("battery shard recommendation drift")
    _validate_input_ledger(
        value["input_ledger"], value["protocol"], bindings, execution)
    generator = value["generator"]
    _exact_keys(generator, {"program", "program_sha256", "source_commit",
                            "source_tree_hash"}, "battery generator")
    if generator["program"] != PROGRAM \
            or not _hex(generator["program_sha256"]) \
            or generator["source_commit"] != execution["source_commit"] \
            or generator["source_tree_hash"] != \
            execution["source_tree_hash"]:
        raise V2BError("battery generator drift")
    return value


def _capture_ledger(label_paths):
    rows = []
    for label, path in label_paths:
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat battery input {label}: {err}") \
                from err
        rows.append(dict(label=label, bytes=size, sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    if len({row["label"] for row in rows}) != len(rows):
        raise V2BError("duplicate battery input-ledger label")
    return rows


def _ledger_record(pre, post):
    if pre != post:
        raise V2BError("battery inputs changed during execution")
    digest = sha256_sorted_json(pre)
    return dict(
        algorithm="sha256-sorted-json-file-ledger-v1",
        n_entries=len(pre), entries=pre, entries_sha256=digest,
        pre_entries_sha256=digest, post_entries_sha256=digest,
        unchanged=True)


def _runtime_environment(commit, tree):
    lock_ok, lock_detail = env_matches_lock()
    freeze_ok, freeze_detail = env_matches_freeze()
    if not lock_ok or not freeze_ok:
        raise V2BError(
            f"environment does not match lock/freeze: "
            f"{lock_detail[:4] if isinstance(lock_detail, list) else lock_detail}; "
            f"{freeze_detail}")
    return dict(
        environment_fingerprint=env_fingerprint(),
        requirements_lock_sha256=sha256_file(LOCK_FILE),
        environment_freeze_sha256=sha256_file(FREEZE_FILE),
        environment_lock_matches=True, environment_freeze_matches=True,
        measurement_harness_sha256=harness_hash(),
        numerical_harness_sha256=numerical_harness_hash(),
        source_commit=commit, source_tree_hash=tree, gpu=gpu_info())


def _load_model_and_tokenizer(model_spec):
    try:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoConfig, AutoTokenizer
        from eval_incontext import load_model
        from eval_paired import _model_max_positions
    except ImportError as err:
        raise V2BError(f"confirmation model runtime unavailable: {err}") \
            from err
    if not torch.cuda.is_available():
        raise V2BError("confirmation battery requires CUDA")
    snapshot = snapshot_download(
        repo_id=model_spec["name"], revision=model_spec["revision"],
        local_files_only=True)
    model_files, tokenizer_files = snapshot_file_manifests(
        snapshot, model_spec["revision"])
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot, revision=model_spec["revision"], local_files_only=True,
        use_fast=True)
    if getattr(tokenizer, "is_fast", False) is not True:
        raise V2BError("confirmation requires a fast tokenizer")
    pre_config = AutoConfig.from_pretrained(
        snapshot, revision=model_spec["revision"], local_files_only=True)
    pre_max = _model_max_positions(pre_config)
    model, config, identity = load_model(
        model_spec["name"], torch.bfloat16, DEVICE, random_init=False,
        revision=model_spec["revision"], local_only=True)
    parameter_devices = {parameter.device.type
                         for parameter in model.parameters()}
    floating_dtypes = {parameter.dtype for parameter in model.parameters()
                       if parameter.is_floating_point()}
    if parameter_devices != {DEVICE} or floating_dtypes != {torch.bfloat16}:
        raise V2BError("loaded model parameters differ from frozen "
                       "CUDA/bfloat16 execution identity")
    max_positions = _model_max_positions(config)
    if max_positions != pre_max:
        raise V2BError("pre/post-load model max-position drift")
    attention = identity.get("attn_note")
    vocab = getattr(config, "vocab_size", None)
    runtime = dict(
        model_id=model_spec["id"], model_name=model_spec["name"],
        revision=model_spec["revision"],
        model_class=identity.get("model_class"),
        n_parameters=identity.get("n_params"),
        tokenizer_class=type(tokenizer).__name__, vocab_size=vocab,
        max_position_embeddings=max_positions, attention=attention)
    return (model, tokenizer, runtime, model_files, tokenizer_files,
            snapshot)


def run_synthetic_instrument(model, tokenizer, runtime,
                             maximum_eligible_prompt_tokens):
    """Execute only fixed synthetic token IDs; never a target prompt."""
    try:
        import torch
        from eval_incontext import eval_window
        from eval_paired import body_token_ledger
    except ImportError as err:
        raise V2BError(f"confirmation instrument runtime unavailable: {err}") \
            from err
    vocab = runtime["vocab_size"]
    n_tokens = max(SYNTHETIC_TOKENS, maximum_eligible_prompt_tokens)
    if runtime["max_position_embeddings"] < n_tokens:
        raise V2BError("model cannot fit the fixed synthetic instrument")
    ids = [((index * 1103515245 + 12345) % vocab)
           for index in range(n_tokens)]
    tensor = torch.tensor(ids, dtype=torch.long)
    perturbed = list(ids)
    perturbed[CAUSAL_POSITION] = (perturbed[CAUSAL_POSITION] + 1) % vocab
    perturbed_tensor = torch.tensor(perturbed, dtype=torch.long)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    first = eval_window(model, tensor, DEVICE, CHUNK_TOKENS)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    second = eval_window(model, tensor, DEVICE, CHUNK_TOKENS)
    changed = eval_window(model, perturbed_tensor, DEVICE, CHUNK_TOKENS)
    torch.cuda.synchronize()
    repeat = float((first - second).abs().max())
    delta = (first.double() - changed.double()).abs()
    protected = list(range(0, CAUSAL_POSITION - 1))
    downstream = list(range(CAUSAL_POSITION, len(first)))
    protected_max = float(delta[protected].max())
    downstream_max = float(delta[downstream].max())

    synthetic_text = ("def synthetic_probe(x):\n    return x + 1\n" * 8)
    encoded = tokenizer(synthetic_text, add_special_tokens=False,
                        return_offsets_mapping=True)
    synthetic_ids = list(encoded["input_ids"])
    synthetic_offsets = [list(row) for row in encoded["offset_mapping"]]
    split = synthetic_text.index("return")
    conservation = body_token_ledger(
        synthetic_text, synthetic_offsets, split,
        token_ids=synthetic_ids)
    conservation_ok = conservation["exact_body_bytes"] == len(
        synthetic_text[split:].encode("utf-8"))
    total_memory = int(torch.cuda.get_device_properties(0).total_memory)
    allocated = int(torch.cuda.max_memory_allocated())
    reserved = int(torch.cuda.max_memory_reserved())
    fraction = reserved / total_memory
    passed = repeat <= REPEAT_MAX_ABS \
        and protected_max <= CAUSAL_PROTECTED_MAX_ABS \
        and downstream_max > CAUSAL_DOWNSTREAM_MIN_ABS \
        and conservation_ok and fraction <= MAX_MEMORY_FRACTION
    return dict(
        schema=INSTRUMENT_SCHEMA,
        state="complete-target-free-production-path",
        domain="v2b-nll-confirmation-target-free-synthetic-v1",
        contains_confirmation_target_bytes=False,
        dtype=DTYPE, device=DEVICE, chunk_tokens=CHUNK_TOKENS,
        n_tokens=n_tokens, seconds=elapsed,
        tokens_per_second=(n_tokens - 1) / elapsed,
        repeat_max_abs=repeat, repeat_threshold=REPEAT_MAX_ABS,
        causal_position=CAUSAL_POSITION,
        causal_n_protected=len(protected),
        causal_n_downstream=len(downstream),
        causal_protected_max_abs=protected_max,
        causal_protected_threshold=CAUSAL_PROTECTED_MAX_ABS,
        causal_downstream_max_abs=downstream_max,
        causal_downstream_minimum=CAUSAL_DOWNSTREAM_MIN_ABS,
        token_byte_conservation=conservation_ok,
        peak_memory_allocated_bytes=allocated,
        peak_memory_reserved_bytes=reserved,
        device_total_memory_bytes=total_memory,
        peak_reserved_fraction=fraction,
        maximum_memory_fraction=MAX_MEMORY_FRACTION,
        maximum_eligible_prompt_tokens=maximum_eligible_prompt_tokens,
        covers_maximum_eligible_prompt_tokens=True,
        passed=passed)


def prepare(model_id, assembly_path, sample_path, implementation_freeze_path,
            source_gate_path, protocol_path=PROTOCOL_PATH):
    """Production battery entry point for one exact protocol model."""
    if not source_clean():
        raise V2BError("source tree dirty before confirmation battery")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation battery requires canonical protocol")
    paths = (protocol_path, implementation_freeze_path, sample_path,
             source_gate_path, assembly_path)
    for path in paths:
        require_committed(path)
    commit, tree = head_commit(), source_tree_hash()
    protocol, digest = load_protocol(protocol_path)
    if digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation protocol raw digest drift")
    freeze_binding, freeze = artifact_binding(
        implementation_freeze_path, IMPLEMENTATION_FREEZE_SCHEMA)
    from freeze_v2b_nll_confirmation import validate_live_freeze
    validate_live_freeze(freeze, implementation_freeze_path)
    sample_binding, sample = artifact_binding(
        sample_path, SAMPLE_SCHEMA_CONFIRMATION)
    gate, gate_digest = load_reduced_gate(source_gate_path, protocol_path)
    gate_binding = dict(
        path=os.path.abspath(source_gate_path), schema=GATE_SCHEMA,
        sha256=gate_digest)
    assembly_binding, assembly = artifact_binding(
        assembly_path, ASSEMBLY_SCHEMA)
    _validate_freeze(freeze, freeze_binding, protocol)
    _same_artifact(
        gate["bindings"]["implementation_freeze"], freeze_binding,
        IMPLEMENTATION_FREEZE_SCHEMA,
        "source-gate implementation freeze")
    validate_assembly(assembly, protocol, sample)
    bindings = dict(
        implementation_freeze=freeze_binding, bound_sample=sample_binding,
        source_gate=gate_binding, assembly=assembly_binding)
    _validate_assembly_consumer(assembly, protocol, bindings)
    ledger_paths = (
        ("input:protocol", protocol_path),
        ("input:implementation_freeze", implementation_freeze_path),
        ("input:bound_sample", sample_path),
        ("input:source_gate", source_gate_path),
        ("input:assembly", assembly_path),
        ("input:requirements_lock", LOCK_FILE),
        ("input:environment_freeze", FREEZE_FILE),
    )
    pre = _capture_ledger(ledger_paths)
    provenance = _runtime_environment(commit, tree)
    materialized = materialize_confirmation(
        assembly_path, sample_path, implementation_freeze_path,
        source_gate_path, protocol_path)
    model_spec = _model_row(protocol, model_id)
    model, tokenizer, runtime, model_files, tokenizer_files, snapshot = \
        _load_model_and_tokenizer(model_spec)
    # The all-target tokenizer fit precedes the only numerical forward pass.
    fit = build_tokenizer_fit_ledger(
        assembly, materialized, tokenizer,
        runtime["max_position_embeddings"], runtime["vocab_size"])
    maximum_prompt_tokens = max(
        row["n_prompt_tokens"] for row in fit["rows"]
        if row["status"] == "tokenized")
    instrument = run_synthetic_instrument(
        model, tokenizer, runtime, maximum_prompt_tokens)
    post = _capture_ledger(ledger_paths)
    if _runtime_environment(commit, tree) != provenance:
        raise V2BError("execution environment changed during confirmation "
                       "battery")
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed during confirmation battery")
    # The local model/tokenizer snapshot is an uncommitted input: guard its
    # complete bytes explicitly before and after the GPU run.
    model_files_post, tokenizer_files_post = snapshot_file_manifests(
        snapshot, model_spec["revision"])
    if model_files_post != model_files \
            or tokenizer_files_post != tokenizer_files:
        raise V2BError("model/tokenizer snapshot changed during battery")
    generator = dict(
        program=PROGRAM, program_sha256=sha256_file(__file__),
        source_commit=commit, source_tree_hash=tree)
    value = build_battery_value(
        protocol, protocol_record(), freeze, bindings, assembly,
        materialized, model_id, tokenizer, model_files, tokenizer_files,
        runtime, instrument, provenance, _ledger_record(pre, post), generator,
        tokenizer_fit=fit)
    del model
    try:
        import torch
        torch.cuda.empty_cache()
    except ImportError:
        pass
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", required=True,
                        choices=[row[0] for row in MODEL_ROWS])
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--source-gate", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    value = prepare(
        args.model_id, args.assembly, args.sample,
        args.implementation_freeze, args.source_gate, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-battery] {args.model_id} "
          f"{value['tokenizer_fit']['n_tokenized_prompts']} prompts / "
          f"shards={value['sharding']['recommended_shard_count']} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
