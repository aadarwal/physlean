#!/usr/bin/env python3
"""Paired fixed-target NLL evaluator for DESIGN_V2.

One invocation loads one frozen model once, re-materializes and rehashes one
corpus assembly manifest, then scores every missing target.  Each target is
published atomically as its own evidence artifact, so a preempted Slurm job
resumes without mixing partial cells or reloading the model once per prompt.

This driver implements the NLL portion of V2-b.  Behavioral generation,
verification, mutation probes, and the AST-class attribution are separate
gates; this artifact cannot silently claim that those stages ran.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import torch
from transformers import AutoTokenizer

# Hard imports required by DESIGN_V2 §15.A9: paired scoring must reuse the
# production model loader and chunked-NLL implementation, never fork copies.
from eval_incontext import eval_window, load_model  # noqa: F401
from layout import (PAIRED_SCHEMA_VERSION, PRODUCTION_CHUNK_TOKENS,
                    token_spans)
from v2b_common import (ASSEMBLY_SCHEMA, V2BError, artifact_binding,
                        canonical_json_bytes, load_json, sha256_bytes,
                        sha256_file, sha256_json, write_new_json)


TARGET_SCHEMA = "v2b_paired_nll_target_v1"
COMPLETE_SCHEMA = "v2b_paired_nll_complete_v1"
AST_CLASS_STATE = "not-run-separate-required-gate"
LOG = lambda *args: print(*args, file=sys.stderr, flush=True)


def paired_harness_hash(base_dir=None):
    """Hash the exact three-file paired numerical harness frozen in §15.A9."""
    base = os.path.abspath(base_dir or os.path.dirname(__file__))
    rows = []
    for name in ("eval_paired.py", "eval_incontext.py", "layout.py"):
        path = os.path.join(base, name)
        try:
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        except OSError as err:
            raise V2BError(f"cannot hash paired harness file {path}: {err}") \
                from err
        rows.append([name, digest])
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def body_token_ledger(text, offsets, body_start_char, token_ids=None):
    """Freeze §15.A11's body-only boundary convention before scoring.

    Returns token indices (indices in the full prompt tokenization, not NLL
    row indices) for the primary and boundary-inclusive sensitivity.  The
    caller maps token index j>0 to eval_window's NLL row j-1.
    """
    if not isinstance(text, str):
        raise V2BError("paired prompt must be text")
    if not isinstance(body_start_char, int) or isinstance(body_start_char, bool) \
            or not 0 < body_start_char < len(text):
        raise V2BError(f"invalid body character boundary {body_start_char!r}")
    if not isinstance(offsets, list) or not offsets:
        raise V2BError("tokenizer returned no offset mapping")
    if token_ids is not None and len(token_ids) != len(offsets):
        raise V2BError("token id/offset count mismatch")
    normalized_offsets = []
    for index, offset in enumerate(offsets):
        if not isinstance(offset, (list, tuple)) or len(offset) != 2:
            raise V2BError(f"bad token offset[{index}] {offset!r}")
        start, end = offset
        if not all(isinstance(x, int) and not isinstance(x, bool)
                   for x in (start, end)) \
                or not 0 <= start <= end <= len(text):
            raise V2BError(f"bad token offset[{index}] {offset!r}")
        normalized_offsets.append((start, end))

    byte_lengths, group_ids = token_spans(text, normalized_offsets)
    groups = {}
    previous_end = 0
    for index, ((start, end), byte_length, gid) in enumerate(
            zip(normalized_offsets, byte_lengths, group_ids)):
        charged_start = previous_end
        charged_end = max(previous_end, end)
        expected_bytes = (len(text[charged_start:charged_end].encode("utf-8"))
                          if charged_end > charged_start else 0)
        if expected_bytes != byte_length:
            raise AssertionError("layout.token_spans byte ledger diverged")
        row = groups.setdefault(gid, dict(token_indices=[], prefix_bytes=0,
                                          body_bytes=0,
                                          prefix_codepoints=0,
                                          body_codepoints=0,
                                          relative_offsets=[]))
        row["token_indices"].append(index)
        if charged_end > charged_start:
            prefix_end = min(charged_end, body_start_char)
            if prefix_end > charged_start:
                piece = text[charged_start:prefix_end]
                row["prefix_bytes"] += len(piece.encode("utf-8"))
                row["prefix_codepoints"] += len(piece)
            body_begin = max(charged_start, body_start_char)
            if charged_end > body_begin:
                piece = text[body_begin:charged_end]
                row["body_bytes"] += len(piece.encode("utf-8"))
                row["body_codepoints"] += len(piece)
        row["relative_offsets"].append([
            start - body_start_char, end - body_start_char,
            token_ids[index] if token_ids is not None else None])
        previous_end = max(previous_end, end)
    if previous_end != len(text):
        raise V2BError(f"token offsets cover {previous_end}/{len(text)} chars")
    if sum(byte_lengths) != len(text.encode("utf-8")):
        raise AssertionError("paired token byte ledger does not conserve text")

    primary, boundary = [], []
    scored_bytes = scored_codepoints = 0
    straddled_bytes = straddled_codepoints = 0
    boundary_rows = []
    for gid in sorted(groups):
        row = groups[gid]
        if row["body_bytes"] == 0:
            classification = "prefix"
        elif row["prefix_bytes"] == 0:
            classification = "body"
            primary.extend(row["token_indices"])
            scored_bytes += row["body_bytes"]
            scored_codepoints += row["body_codepoints"]
        else:
            classification = "boundary-straddle"
            boundary.extend(row["token_indices"])
            straddled_bytes += row["body_bytes"]
            straddled_codepoints += row["body_codepoints"]
            boundary_rows.append(dict(group_id=gid, **row))
        row["classification"] = classification
    if len(boundary_rows) > 1:
        raise V2BError("multiple token groups straddle one body boundary")
    if boundary and boundary[0] == 0:
        raise V2BError("first prompt token straddles body and has no NLL row")
    exact_body = text[body_start_char:]
    exact_body_bytes = len(exact_body.encode("utf-8"))
    exact_body_codepoints = len(exact_body)
    if scored_bytes + straddled_bytes != exact_body_bytes \
            or scored_codepoints + straddled_codepoints != \
            exact_body_codepoints:
        raise AssertionError("body boundary ledger does not conserve body")
    if not primary or primary[0] == 0:
        raise V2BError("body has no conditionally scoreable token")
    signature_rows = [row["relative_offsets"] for row in boundary_rows]
    signature = sha256_json(signature_rows)
    return dict(schema="v2b_body_token_ledger_v1",
                paired_schema_version=PAIRED_SCHEMA_VERSION,
                exact_body_bytes=exact_body_bytes,
                exact_body_codepoints=exact_body_codepoints,
                scored_body_bytes=scored_bytes,
                scored_body_codepoints=scored_codepoints,
                straddled_body_bytes=straddled_bytes,
                straddled_body_codepoints=straddled_codepoints,
                n_boundary_straddle_tokens=len(boundary),
                primary_token_indices=primary,
                boundary_token_indices=boundary,
                inclusive_token_indices=boundary + primary,
                boundary_groups=boundary_rows,
                boundary_signature=signature)


def nll_rows_for_token_indices(token_indices):
    """Map full-token indices j to eval_window's prediction row j-1."""
    if any(not isinstance(index, int) or isinstance(index, bool) or index <= 0
           for index in token_indices):
        raise V2BError("cannot score token zero/invalid token index")
    return [index - 1 for index in token_indices]


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _model_max_positions(config):
    """Fail-closed context-length gate. §14 freezes that a truncated cell
    is invalid and never silently clipped, so an UNKNOWN model ceiling is
    unscoreable — it must never default to permissive."""
    candidates = [config]
    text_config = getattr(config, "text_config", None)
    if text_config is not None:
        candidates.append(text_config)
    for row in candidates:
        for field in ("max_position_embeddings", "n_positions"):
            value = getattr(row, field, None)
            if isinstance(value, int) and not isinstance(value, bool) \
                    and value > 0:
                return value
    raise V2BError("model config exposes no positive context-length limit; "
                   "refusing to score without a truncation gate")


def empty_cell_arms(target):
    """Arms/seeds the sealed manifest left cell-less (empty pool, universe,
    or closure): recorded into the target artifact so a missing grid can
    never be read downstream as having been scored."""
    arms = target.get("arms") if isinstance(target, dict) else None
    if not isinstance(arms, dict):
        raise V2BError("assembly target lacks arms")
    empty = []
    for arm in ("k2", "k3", "k4", "k6", "k7") \
            + (("k4x",) if "k4x" in arms else ()):
        row = arms.get(arm)
        cells = row.get("cells") if arm in ("k6", "k7", "k4x") \
            and isinstance(row, dict) else row
        if isinstance(cells, dict) and not cells:
            empty.append(arm)
    k5 = arms.get("k5")
    if isinstance(k5, dict):
        for seed in sorted(k5):
            row = k5.get(seed)
            if isinstance(row, dict) and row.get("cells") == {}:
                empty.append(f"k5:{seed}")
    for arm in ("k3s", "k4s"):
        if arms.get(arm) == {}:
            empty.append(arm)
    return empty


def _budget_cells(cells, arm):
    """Validated (int, key) budget pairs; a malformed key must be a typed
    refusal, not an unlabeled ValueError."""
    if not isinstance(cells, dict):
        raise V2BError(f"assembly target lacks {arm} cells")
    out = []
    for key in cells:
        if not isinstance(key, str) or not key.isdigit():
            raise V2BError(f"non-numeric {arm} budget key {key!r}")
        out.append((int(key), key))
    return sorted(out)


def _utf8(blob, label):
    if not isinstance(blob, bytes):
        raise V2BError(f"{label} is not bytes")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"{label} is not UTF-8: {err}") from err


def _bound_blob(blob, row, label):
    """Rehash one materialized component against its manifest row."""
    if not isinstance(row, dict) \
            or not isinstance(row.get("context_bytes"), int) \
            or isinstance(row.get("context_bytes"), bool) \
            or not _hex(row.get("context_sha256")):
        raise V2BError(f"malformed assembly context row for {label}")
    if len(blob) != row["context_bytes"] \
            or sha256_bytes(blob) != row["context_sha256"]:
        raise V2BError(f"materialized context drift for {label}")


def target_cell_specs(target, blobs):
    """Return every NLL cell frozen into one assembly target.

    The concrete byte strings come only from ``materialize``.  The manifest
    supplies their identity/eligibility metadata and is independently hashed
    into every output row.
    """
    if not isinstance(target, dict) or not isinstance(blobs, dict):
        raise V2BError("malformed assembly target/materialization")
    arms = target.get("arms")
    if not isinstance(arms, dict):
        raise V2BError("assembly target lacks arms")
    specs = []

    def add(arm, collect_key, row, budget=None, seed=None,
            estimand_role="primary-grid"):
        if collect_key not in blobs:
            raise V2BError(f"materialization lacks {collect_key}")
        context = blobs[collect_key]
        _bound_blob(context, row, collect_key)
        specs.append(dict(
            cell_id=collect_key, arm=arm, budget_bytes=budget, seed=seed,
            estimand_role=estimand_role, context=context,
            context_sha256=row["context_sha256"],
            context_bytes=row["context_bytes"],
            eligible=row.get("eligible"),
            cell_manifest_sha256=sha256_json(row)))

    add("k1", "k1", arms.get("k1"), estimand_role="absence")
    for arm in ("k2", "k3", "k4"):
        for budget, key in _budget_cells(arms.get(arm), arm):
            add(arm, f"{arm}:{key}", arms[arm][key], budget=budget)

    k5 = arms.get("k5")
    if not isinstance(k5, dict):
        raise V2BError("assembly target lacks k5 seeds")
    for seed in sorted(k5, key=int):
        seed_row = k5[seed]
        cells = seed_row.get("cells") if isinstance(seed_row, dict) else None
        role = "primary-grid" if int(seed) == 0 else "seed-sensitivity"
        for budget, key in _budget_cells(cells, f"k5:{seed}"):
            add("k5", f"k5:{seed}:{key}", cells[key],
                budget=budget, seed=int(seed), estimand_role=role)

    # k4x is physlib-only (§14.20/§15.A13): present iff the manifest
    # carries it; when present it contributes its full budget grid.
    for arm in ("k6", "k7") + (("k4x",) if "k4x" in arms else ()):
        arm_row = arms.get(arm)
        cells = arm_row.get("cells") if isinstance(arm_row, dict) else None
        for budget, key in _budget_cells(cells, arm):
            add(arm, f"{arm}:{key}", cells[key], budget=budget)

    for arm in ("k3s", "k4s"):
        row = arms.get(arm)
        if row:
            add(arm, arm, row, estimand_role="same-dependency-sensitivity")
    if len({row["cell_id"] for row in specs}) != len(specs):
        raise AssertionError("duplicate paired cell id")
    return specs


def score_prompt(model, tokenizer, device, context, prefix, body,
                 max_position_embeddings,
                 chunk=PRODUCTION_CHUNK_TOKENS):
    """Score one exact context+prefix+body prompt and return raw body NLL."""
    context_text = _utf8(context, "paired context")
    prefix_text = _utf8(prefix, "paired prefix")
    body_text = _utf8(body, "paired body")
    text = context_text + prefix_text + body_text
    body_start = len(context_text) + len(prefix_text)
    try:
        encoded = tokenizer(text, add_special_tokens=False,
                            return_offsets_mapping=True)
    except NotImplementedError as err:
        raise V2BError("tokenizer cannot produce offset mappings (a fast "
                       "tokenizer is required for the byte ledger)") from err
    if "offset_mapping" not in encoded:
        raise V2BError("tokenizer returned no offset mapping")
    ids = list(encoded["input_ids"])
    offsets = [list(row) for row in encoded["offset_mapping"]]
    if len(ids) < 2 or len(ids) != len(offsets):
        raise V2BError("paired tokenizer returned malformed/short encoding")
    if len(ids) > max_position_embeddings:
        raise V2BError(
            f"paired prompt has {len(ids)} tokens, exceeding model maximum "
            f"{max_position_embeddings}")
    ledger = body_token_ledger(text, offsets, body_start, token_ids=ids)
    tensor = torch.tensor(ids, dtype=torch.long)
    nll = eval_window(model, tensor, device, chunk)
    if len(nll) != len(ids) - 1:
        raise AssertionError("eval_window returned the wrong NLL row count")

    primary_indices = ledger["primary_token_indices"]
    boundary_indices = ledger["boundary_token_indices"]
    primary_rows = nll_rows_for_token_indices(primary_indices)
    boundary_rows = nll_rows_for_token_indices(boundary_indices)
    primary_nll = math.fsum(float(nll[index]) for index in primary_rows)
    boundary_nll = math.fsum(float(nll[index]) for index in boundary_rows)
    if not math.isfinite(primary_nll) or primary_nll < 0 \
            or not math.isfinite(boundary_nll) or boundary_nll < 0:
        raise AssertionError("paired body NLL summary is non-finite/negative")

    primary_set = set(primary_indices)
    boundary_set = set(boundary_indices)
    raw = []
    for token_index in sorted(primary_set | boundary_set):
        start, end = offsets[token_index]
        raw.append(dict(
            token_index=token_index,
            token_id=ids[token_index],
            start_char_relative_to_body=start - body_start,
            end_char_relative_to_body=end - body_start,
            nll_nats=float(nll[token_index - 1]),
            inclusion=("primary" if token_index in primary_set else
                       "boundary-sensitivity-only")))
    layout_signature = sha256_json([
        [row["token_id"], row["start_char_relative_to_body"],
         row["end_char_relative_to_body"], row["inclusion"]]
        for row in raw])
    primary_bpb = primary_nll / math.log(2) / ledger["scored_body_bytes"]
    primary_bpc = (primary_nll / math.log(2)
                   / ledger["scored_body_codepoints"])
    inclusive_nll = primary_nll + boundary_nll
    inclusive_bpb = inclusive_nll / math.log(2) / ledger["exact_body_bytes"]
    inclusive_bpc = (inclusive_nll / math.log(2)
                     / ledger["exact_body_codepoints"])
    metrics = (primary_bpb, primary_bpc, inclusive_bpb, inclusive_bpc)
    if not all(math.isfinite(value) and value >= 0 for value in metrics):
        raise AssertionError("paired BPB/BPC metric is non-finite/negative")
    return dict(
        prompt_bytes=len(text.encode("utf-8")),
        n_prompt_tokens=len(ids),
        body_layout_signature=layout_signature,
        boundary_ledger=ledger,
        primary=dict(nll_nats=primary_nll, bpb=primary_bpb,
                     bpc=primary_bpc,
                     n_tokens=len(primary_indices)),
        boundary_inclusive_sensitivity=dict(
            nll_nats=inclusive_nll, bpb=inclusive_bpb,
            bpc=inclusive_bpc,
            boundary_nll_nats=boundary_nll,
            n_tokens=len(primary_indices) + len(boundary_indices)),
        raw_body_token_rows=raw)


def _model_revision(model_name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "models.json")
    try:
        row = json.load(open(path, encoding="utf-8")).get(model_name)
    except (OSError, json.JSONDecodeError) as err:
        raise V2BError(f"cannot read models.json: {err}") from err
    revision = row.get("sha") if isinstance(row, dict) else None
    if not _hex(revision, 40):
        raise V2BError(f"model lacks a pinned revision: {model_name}")
    return revision


def _chain_paths(manifest, args):
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise V2BError("assembly manifest lacks input bindings")
    names = {
        "sample": "sample",
        "candidates": "candidates",
        "extraction": "extraction",
        "neardup": "neardup",
        "a6_outcome": "a6_outcome",
        "keyword_freeze": "lean_keyword_freeze",
        "k7_order": "k7_order",
        "k4x_graph": "k4x_graph",
        "k4x_external_extraction": "k4x_external_extraction",
    }
    k4x_applicable = isinstance(manifest.get("k4x"), dict) \
        and manifest["k4x"].get("applicable") is True
    out = {}
    for binding_name, arg_name in names.items():
        override = getattr(args, arg_name)
        binding = bindings.get(binding_name)
        path = override or (binding.get("path")
                            if isinstance(binding, dict) else None)
        if binding_name == "keyword_freeze" \
                and manifest.get("language") != "lean":
            out[binding_name] = None
            continue
        if binding_name in ("k4x_graph", "k4x_external_extraction") \
                and not k4x_applicable:
            out[binding_name] = None
            continue
        if not isinstance(path, str) or not path:
            raise V2BError(f"assembly input path unavailable: {binding_name}")
        out[binding_name] = path
    return out


def _check_guard(source_hash, harness, environment, manifest_path,
                 manifest_sha):
    from provenance import env_fingerprint, source_clean, source_tree_hash
    if not source_clean() or source_tree_hash() != source_hash:
        raise V2BError("measurement source changed during paired evaluation")
    if paired_harness_hash() != harness:
        raise V2BError("paired numerical harness changed during evaluation")
    if env_fingerprint() != environment:
        raise V2BError("software environment changed during evaluation")
    if sha256_file(manifest_path) != manifest_sha:
        raise V2BError("assembly manifest changed during evaluation")


def _existing_target(path, run_sha, target_key):
    value, digest = load_json(path, TARGET_SCHEMA)
    if value.get("run_identity_sha256") != run_sha \
            or value.get("target_key") != target_key \
            or value.get("n_cells") != len(value.get("cells", [])) \
            or value.get("ast_class_state") != AST_CLASS_STATE:
        raise V2BError(f"existing paired target artifact is incompatible: "
                       f"{path}")
    return dict(path=os.path.abspath(path), sha256=digest,
                target_key=target_key, n_cells=value["n_cells"])


def evaluate(args):
    from prepare_v2b_assembly import materialize
    from provenance import (env_fingerprint, env_matches_freeze,
                            env_matches_lock, gpu_info, head_commit,
                            source_clean, source_tree_hash)
    from v2b_a6_blind import require_committed

    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(args.manifest)
    manifest_binding, manifest = artifact_binding(args.manifest,
                                                  ASSEMBLY_SCHEMA)
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets \
            or manifest.get("n_targets") != len(targets):
        raise V2BError("assembly manifest target table is malformed")
    if [row.get("key") for row in targets] != sorted(
            row.get("key") for row in targets):
        raise V2BError("assembly manifest targets are not canonically sorted")

    source_hash = source_tree_hash()
    source_commit = head_commit()
    harness = paired_harness_hash()
    environment = env_fingerprint()
    lock_ok, lock_problems = env_matches_lock()
    freeze_ok, freeze_detail = env_matches_freeze()
    if not lock_ok or not freeze_ok:
        raise V2BError(
            f"environment does not match lock/freeze: "
            f"{lock_problems[:4] or 'lock-ok'}; {freeze_detail}")
    revision = _model_revision(args.model)
    run_identity = dict(
        paired_schema_version=PAIRED_SCHEMA_VERSION,
        manifest_sha256=manifest_binding["sha256"],
        model=args.model, revision=revision, dtype=args.dtype,
        chunk_tokens=PRODUCTION_CHUNK_TOKENS,
        paired_harness_hash=harness, env_fingerprint=environment,
        ast_class_state=AST_CLASS_STATE)
    run_sha = sha256_json(run_identity)
    os.makedirs(args.out_dir, exist_ok=True)

    bindings = {}
    missing = []
    for index, target in enumerate(targets):
        key = target.get("key")
        if not isinstance(key, str) or not key:
            raise V2BError(f"manifest target[{index}] lacks a key")
        path = os.path.join(args.out_dir, f"target-{index:04d}.json")
        if os.path.exists(path):
            bindings[index] = _existing_target(path, run_sha, key)
        else:
            missing.append((index, target, path))

    complete_path = os.path.join(args.out_dir, "complete.json")
    if os.path.exists(complete_path):
        complete, _ = load_json(complete_path, COMPLETE_SCHEMA)
        if missing or complete.get("run_identity_sha256") != run_sha \
                or complete.get("n_targets") != len(targets) \
                or complete.get("target_artifacts") != [
                    bindings[index] for index in sorted(bindings)]:
            raise V2BError("existing paired completion artifact is stale")
        LOG(f"[skip] paired corpus already complete: {complete_path}")
        return complete

    if missing:
        paths = _chain_paths(manifest, args)
        materialized = materialize(
            args.manifest, paths["sample"], manifest["repo"],
            paths["candidates"], paths["extraction"], paths["neardup"],
            paths["a6_outcome"], paths["keyword_freeze"],
            paths["k7_order"], paths["k4x_graph"],
            paths["k4x_external_extraction"])
        if set(materialized) != {row["key"] for row in targets}:
            raise V2BError("materialization target set differs from manifest")
        _check_guard(source_hash, harness, environment, args.manifest,
                     manifest_binding["sha256"])

        device = args.device or ("cuda" if torch.cuda.is_available() else
                                 "mps" if torch.backends.mps.is_available()
                                 else "cpu")
        dtype = getattr(torch, args.dtype, None)
        if not isinstance(dtype, torch.dtype):
            raise V2BError(f"unknown torch dtype {args.dtype!r}")
        tokenizer = AutoTokenizer.from_pretrained(
            args.model, revision=revision, local_files_only=True)
        model, config, model_identity = load_model(
            args.model, dtype, device, random_init=False,
            revision=revision, local_only=True)
        max_positions = _model_max_positions(config)
        hardware = gpu_info()
        LOG(f"[paired] {manifest['repo']} / {args.model}: "
            f"{len(missing)} missing targets, max_pos={max_positions}")

        for index, target, path in missing:
            started = time.time()
            concrete = materialized[target["key"]]
            prefix, body = concrete.get("prefix"), concrete.get("body")
            if not isinstance(prefix, bytes) or not isinstance(body, bytes) \
                    or len(prefix) != target.get("prefix_bytes") \
                    or sha256_bytes(prefix) != target.get("prefix_sha256") \
                    or len(body) != target.get("body_bytes") \
                    or sha256_bytes(body) != target.get("body_sha256"):
                raise V2BError(f"prefix/body binding drift for "
                               f"{target['key']}")
            cells = []
            boundary_signature = body_layout_signature = None
            for spec in target_cell_specs(target, concrete):
                score = score_prompt(
                    model, tokenizer, device, spec.pop("context"), prefix,
                    body, max_positions, PRODUCTION_CHUNK_TOKENS)
                observed_boundary = sha256_json([
                    score["boundary_ledger"]["boundary_signature"],
                    score["boundary_ledger"]["exact_body_bytes"],
                    score["boundary_ledger"]["exact_body_codepoints"],
                    score["boundary_ledger"]["scored_body_bytes"],
                    score["boundary_ledger"]["scored_body_codepoints"],
                    score["boundary_ledger"]["straddled_body_bytes"],
                    score["boundary_ledger"]["straddled_body_codepoints"]])
                if boundary_signature is None:
                    boundary_signature = observed_boundary
                    body_layout_signature = score["body_layout_signature"]
                elif observed_boundary != boundary_signature \
                        or score["body_layout_signature"] != \
                        body_layout_signature:
                    raise V2BError(
                        f"arm-specific body token boundary for "
                        f"{target['key']} at {spec['cell_id']}")
                cells.append(dict(spec, **score))
            if not cells:
                raise V2BError(f"target has no scoreable cells: "
                               f"{target['key']}")
            empty_arms = empty_cell_arms(target)
            artifact = dict(
                schema=TARGET_SCHEMA,
                paired_schema_version=PAIRED_SCHEMA_VERSION,
                run_identity=run_identity,
                run_identity_sha256=run_sha,
                repo=manifest["repo"], language=manifest["language"],
                corpus_git_sha=manifest["corpus_git_sha"],
                assembly_manifest=manifest_binding,
                assembly_target_sha256=sha256_json(target),
                target_index=index, target_identity=target["identity"],
                target_key=target["key"],
                prefix_sha256=target["prefix_sha256"],
                prefix_bytes=target["prefix_bytes"],
                body_sha256=target["body_sha256"],
                body_bytes=target["body_bytes"],
                boundary_signature=boundary_signature,
                body_layout_signature=body_layout_signature,
                ast_class_state=AST_CLASS_STATE,
                empty_cell_arms=empty_arms,
                n_cells=len(cells), cells=cells,
                model_identity=model_identity,
                hardware=hardware,
                generator=dict(source_commit=source_commit,
                               source_tree_hash=source_hash,
                               program="eval_paired.py"),
                wall_s=time.time() - started)
            _check_guard(source_hash, harness, environment, args.manifest,
                         manifest_binding["sha256"])
            digest = write_new_json(path, artifact)
            bindings[index] = dict(path=os.path.abspath(path), sha256=digest,
                                   target_key=target["key"],
                                   n_cells=len(cells))
            LOG(f"[paired target {index + 1}/{len(targets)}] "
                f"{len(cells)} cells -> {path} ({digest[:12]})")

    ordered_bindings = [bindings[index] for index in sorted(bindings)]
    if len(ordered_bindings) != len(targets):
        raise AssertionError("paired completion missing target bindings")
    _check_guard(source_hash, harness, environment, args.manifest,
                 manifest_binding["sha256"])
    complete = dict(
        schema=COMPLETE_SCHEMA,
        paired_schema_version=PAIRED_SCHEMA_VERSION,
        run_identity=run_identity, run_identity_sha256=run_sha,
        repo=manifest["repo"], language=manifest["language"],
        corpus_git_sha=manifest["corpus_git_sha"],
        assembly_manifest=manifest_binding,
        n_targets=len(targets),
        n_cells=sum(row["n_cells"] for row in ordered_bindings),
        target_artifacts=ordered_bindings,
        ast_class_state=AST_CLASS_STATE,
        behavioral_state="not-run-separate-required-gate",
        mutation_state="not-run-separate-required-gate",
        generator=dict(source_commit=source_commit,
                       source_tree_hash=source_hash,
                       program="eval_paired.py"))
    write_new_json(complete_path, complete)
    LOG(f"[paired complete] {complete['n_targets']} targets / "
        f"{complete['n_cells']} cells -> {complete_path}")
    return complete


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--device")
    ap.add_argument("--sample")
    ap.add_argument("--candidates")
    ap.add_argument("--extraction")
    ap.add_argument("--neardup")
    ap.add_argument("--a6-outcome")
    ap.add_argument("--lean-keyword-freeze")
    ap.add_argument("--k7-order")
    ap.add_argument("--k4x-graph")
    ap.add_argument("--k4x-external-extraction")
    args = ap.parse_args()
    try:
        evaluate(args)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err


if __name__ == "__main__":
    main()
