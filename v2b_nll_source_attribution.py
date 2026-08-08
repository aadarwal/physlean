#!/usr/bin/env python3
"""Frozen, additive source-token attribution for V2-b paired NLL.

This module consumes the raw token NLL rows already emitted by
``eval_paired.py``.  It never calls a model.  Whole tokenizer overlap-groups
are the atomic primary units; their NLL is assigned to one lexical-core class
when possible, retained as ``mixed_core`` otherwise, and never redistributed.
Layout/comment byte-overlap apportionment is a separately named sensitivity.

The denominator for every primary class contribution is TOTAL scored-body
bytes.  Consequently class contributions plus an explicit floating-point
residual reconstruct the stored cell BPB and paired class contrasts are
additive.  This is source-token attribution, not AST-node attribution.
"""
import math

from prepare_v2b_masked_deltas import CONTRASTS, DELTA_BUDGET_BYTES
from v2b_common import V2BError, sha256_json
from v2b_source_tokens import SOURCE_CLASSES


ATTRIBUTION_TARGET_SCHEMA = "v2b_nll_source_attribution_target_v1"
ATTRIBUTION_COMPLETE_SCHEMA = "v2b_nll_source_attribution_complete_v1"
SOURCE_CONTRASTS_SCHEMA = "v2b_nll_source_contrasts_v1"
MODEL_GROUP_CLASSES = ("word", "literal", "symbol", "other",
                       "mixed_core", "comment", "layout",
                       "comment_layout")
CORE_CLASSES = frozenset(("word", "literal", "symbol", "other"))
ROUNDING_ULPS = 16
CONTRAST_ROUNDING_ULPS = 32


def _number(value, label, nonnegative=True):
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value) \
            or nonnegative and value < 0:
        raise V2BError(f"malformed {label}: {value!r}")
    return float(value)


def _layout_projection(rows):
    return [[row["token_id"], row["start_char_relative_to_body"],
             row["end_char_relative_to_body"], row["inclusion"]]
            for row in rows]


def _validate_source_row(body, source_row):
    if not isinstance(source_row, dict) \
            or source_row.get("body_codepoints") != len(body) \
            or source_row.get("body_bytes") != len(body.encode("utf-8")):
        raise V2BError("source-token ledger body dimensions drift")
    prefix = source_row.get("char_to_byte_prefix")
    expected = [0]
    for ch in body:
        expected.append(expected[-1] + len(ch.encode("utf-8")))
    if prefix != expected:
        raise V2BError("source-token ledger character/byte map drift")
    spans = source_row.get("spans")
    if not isinstance(spans, list) or not spans:
        raise V2BError("source-token ledger lacks spans")
    for index, row in enumerate(spans):
        if not isinstance(row, dict) \
                or row.get("source_class") not in SOURCE_CLASSES \
                or row.get("start_char") is None \
                or row.get("end_char") is None \
                or row.get("start_byte") != prefix[row["start_char"]] \
                or row.get("end_byte") != prefix[row["end_char"]]:
            raise V2BError(f"malformed source-token ledger span[{index}]")
        if index == 0:
            if row["start_char"] != 0:
                raise V2BError("source-token ledger starts after body zero")
        elif spans[index - 1]["end_char"] != row["start_char"]:
            raise V2BError("source-token ledger is not a partition")
    if spans[-1]["end_char"] != len(body):
        raise V2BError("source-token ledger ends before body end")
    return prefix, spans


def _source_overlap(start, end, prefix, spans):
    out = {name: 0 for name in SOURCE_CLASSES}
    for row in spans:
        left = max(start, row["start_char"])
        right = min(end, row["end_char"])
        if right > left:
            out[row["source_class"]] += prefix[right] - prefix[left]
    expected = prefix[end] - prefix[start]
    if sum(out.values()) != expected:
        raise V2BError("model/source overlap does not conserve charged bytes")
    return out


def _model_class(overlap):
    core = [name for name in CORE_CLASSES if overlap[name] > 0]
    if len(core) == 1:
        return core[0]
    if len(core) > 1:
        return "mixed_core"
    has_comment = overlap["comment"] > 0
    has_layout = overlap["layout"] > 0
    if has_comment and has_layout:
        return "comment_layout"
    if has_comment:
        return "comment"
    if has_layout:
        return "layout"
    raise V2BError("model group has no source-class support")


def attribute_cell(body, cell, source_row):
    """Validate and attribute one exact paired cell.

    Returned ``model_groups`` retain atomic group NLLs and their complete
    source-byte overlap vectors.  No mixed group is split in the primary.
    """
    if not isinstance(body, str) or not body:
        raise V2BError("attribution body must be non-empty text")
    if not isinstance(cell, dict):
        raise V2BError("attribution cell is malformed")
    prefix, source_spans = _validate_source_row(body, source_row)
    ledger = cell.get("boundary_ledger")
    raw = cell.get("raw_body_token_rows")
    if not isinstance(ledger, dict) or not isinstance(raw, list) or not raw:
        raise V2BError("cell lacks raw token rows/boundary ledger")
    if ledger.get("exact_body_codepoints") != len(body) \
            or ledger.get("exact_body_bytes") != prefix[-1]:
        raise V2BError("paired boundary ledger body dimensions drift")

    normalized = []
    seen = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise V2BError(f"malformed raw token row[{index}]")
        token_index = row.get("token_index")
        token_id = row.get("token_id")
        start = row.get("start_char_relative_to_body")
        end = row.get("end_char_relative_to_body")
        inclusion = row.get("inclusion")
        nll = _number(row.get("nll_nats"), f"raw token nll[{index}]")
        if not isinstance(token_index, int) or isinstance(token_index, bool) \
                or token_index <= 0 or token_index in seen \
                or not isinstance(token_id, int) or isinstance(token_id, bool) \
                or not isinstance(start, int) or isinstance(start, bool) \
                or not isinstance(end, int) or isinstance(end, bool) \
                or start > end or end > len(body) \
                or inclusion not in ("primary",
                                     "boundary-sensitivity-only"):
            raise V2BError(f"malformed raw token metadata[{index}]")
        seen.add(token_index)
        normalized.append(dict(
            token_index=token_index, token_id=token_id,
            start_char_relative_to_body=start,
            end_char_relative_to_body=end, inclusion=inclusion,
            nll_nats=nll))
    if normalized != sorted(normalized, key=lambda row: row["token_index"]):
        raise V2BError("raw body token rows are not token-index sorted")
    if cell.get("body_layout_signature") != sha256_json(
            _layout_projection(normalized)):
        raise V2BError("raw body token layout signature drift")

    primary_rows = [row for row in normalized
                    if row["inclusion"] == "primary"]
    boundary_rows = [row for row in normalized
                     if row["inclusion"] == "boundary-sensitivity-only"]
    primary_indices = [row["token_index"] for row in primary_rows]
    boundary_indices = [row["token_index"] for row in boundary_rows]
    if primary_indices != ledger.get("primary_token_indices") \
            or boundary_indices != ledger.get("boundary_token_indices") \
            or boundary_indices + primary_indices != \
            ledger.get("inclusive_token_indices") \
            or len(boundary_indices) != \
            ledger.get("n_boundary_straddle_tokens"):
        raise V2BError("raw rows do not equal boundary-ledger token lists")
    q = ledger.get("straddled_body_codepoints")
    q_bytes = ledger.get("straddled_body_bytes")
    if not isinstance(q, int) or isinstance(q, bool) or not 0 <= q <= len(body) \
            or not isinstance(q_bytes, int) or isinstance(q_bytes, bool) \
            or q_bytes != prefix[q]:
        raise V2BError("malformed boundary-straddled body extent")
    if boundary_rows:
        if q <= 0 or max(row["end_char_relative_to_body"]
                         for row in boundary_rows) != q:
            raise V2BError("boundary rows do not explain straddled extent")
    elif q != 0:
        raise V2BError("straddled extent exists without boundary rows")

    groups = []
    current = None
    previous_end = q
    for row in primary_rows:
        start = row["start_char_relative_to_body"]
        end = row["end_char_relative_to_body"]
        if start < q:
            raise V2BError("primary token overlaps excluded boundary span")
        if current is None or start >= previous_end:
            if current is not None:
                groups.append(current)
            current = dict(start_char=previous_end, end_char=None,
                           token_rows=[])
        current["token_rows"].append(row)
        previous_end = max(previous_end, end)
        current["end_char"] = previous_end
    if current is not None:
        groups.append(current)
    if not groups or previous_end != len(body):
        raise V2BError("primary model groups do not reach body end")

    model_groups = []
    for index, group in enumerate(groups):
        start, end = group["start_char"], group["end_char"]
        if not 0 <= start < end <= len(body):
            raise V2BError(f"zero/malformed primary model group[{index}]")
        overlap = _source_overlap(start, end, prefix, source_spans)
        charged_bytes = prefix[end] - prefix[start]
        nll = math.fsum(row["nll_nats"] for row in group["token_rows"])
        model_groups.append(dict(
            group_index=index,
            token_indices=[row["token_index"]
                           for row in group["token_rows"]],
            start_char=start, end_char=end,
            start_byte=prefix[start], end_byte=prefix[end],
            charged_bytes=charged_bytes,
            model_group_class=_model_class(overlap),
            source_overlap_bytes=overlap, nll_nats=nll))

    scored_bytes = ledger.get("scored_body_bytes")
    scored_codepoints = ledger.get("scored_body_codepoints")
    if scored_bytes != prefix[-1] - prefix[q] \
            or scored_codepoints != len(body) - q \
            or sum(row["charged_bytes"] for row in model_groups) != \
            scored_bytes:
        raise V2BError("primary model groups do not conserve scored body")
    primary = cell.get("primary")
    if not isinstance(primary, dict):
        raise V2BError("cell lacks primary NLL summary")
    stored_nll = _number(primary.get("nll_nats"), "stored primary NLL")
    raw_nll = math.fsum(row["nll_nats"] for row in primary_rows)
    if stored_nll != raw_nll:
        raise V2BError("stored primary NLL does not equal raw token rows")
    stored_bpb = _number(primary.get("bpb"), "stored primary BPB")
    recomputed_bpb = stored_nll / math.log(2) / scored_bytes
    if stored_bpb != recomputed_bpb:
        raise V2BError("stored primary BPB does not recompute")

    class_nll = {name: math.fsum(
        row["nll_nats"] for row in model_groups
        if row["model_group_class"] == name)
        for name in MODEL_GROUP_CLASSES}
    class_bpb = {name: value / math.log(2) / scored_bytes
                 for name, value in class_nll.items()}
    class_sum = math.fsum(class_nll.values())
    residual_nll = stored_nll - class_sum
    tolerance = ROUNDING_ULPS * math.ulp(max(abs(stored_nll), 1.0))
    if abs(residual_nll) > tolerance:
        raise V2BError("class NLL reconstruction exceeds frozen ulp bound")
    residual_bpb = residual_nll / math.log(2) / scored_bytes

    overlap_by_model = {name: {source: 0 for source in SOURCE_CLASSES}
                        for name in MODEL_GROUP_CLASSES}
    class_charged_bytes = {name: 0 for name in MODEL_GROUP_CLASSES}
    byte_sensitivity_nll = {name: 0.0 for name in SOURCE_CLASSES}
    byte_terms = {name: [] for name in SOURCE_CLASSES}
    for row in model_groups:
        label = row["model_group_class"]
        class_charged_bytes[label] += row["charged_bytes"]
        for source, n_bytes in row["source_overlap_bytes"].items():
            overlap_by_model[label][source] += n_bytes
            if n_bytes:
                byte_terms[source].append(
                    row["nll_nats"] * n_bytes / row["charged_bytes"])
    byte_sensitivity_nll = {name: math.fsum(byte_terms[name])
                            for name in SOURCE_CLASSES}
    byte_sensitivity_bpb = {
        name: value / math.log(2) / scored_bytes
        for name, value in byte_sensitivity_nll.items()}
    byte_residual = stored_nll - math.fsum(byte_sensitivity_nll.values())
    if abs(byte_residual) > tolerance:
        raise V2BError("byte-apportioned NLL exceeds frozen ulp bound")

    structural_groups = [
        {key: value for key, value in row.items() if key != "nll_nats"}
        for row in model_groups]
    return dict(
        cell_id=cell.get("cell_id"), arm=cell.get("arm"),
        budget_bytes=cell.get("budget_bytes"), seed=cell.get("seed"),
        estimand_role=cell.get("estimand_role"),
        eligible=cell.get("eligible"),
        cell_manifest_sha256=cell.get("cell_manifest_sha256"),
        primary_nll_nats=stored_nll, primary_bpb=stored_bpb,
        scored_body_bytes=scored_bytes, boundary_excluded_bytes=q_bytes,
        model_group_layout_sha256=sha256_json(structural_groups),
        n_model_groups=len(model_groups), model_groups=model_groups,
        model_group_class_counts={name: sum(
            row["model_group_class"] == name for row in model_groups)
            for name in MODEL_GROUP_CLASSES},
        model_group_class_charged_bytes=class_charged_bytes,
        source_overlap_bytes_by_model_class=overlap_by_model,
        class_nll_nats=class_nll,
        class_contribution_bpb=class_bpb,
        roundoff_residual_nats=residual_nll,
        roundoff_residual_bpb=residual_bpb,
        roundoff_bound_ulps=ROUNDING_ULPS,
        byte_overlap_apportionment_sensitivity=dict(
            class_nll_nats=byte_sensitivity_nll,
            class_contribution_bpb=byte_sensitivity_bpb,
            roundoff_residual_nats=byte_residual,
            claim="noncausal byte-overlap apportionment sensitivity"))


def validate_cross_cell_layout(attributed_cells):
    """Every arm for one target must share one tokenizer/source layout."""
    if not isinstance(attributed_cells, list) or not attributed_cells:
        raise V2BError("cross-cell layout check needs attributed cells")
    signatures = {row.get("model_group_layout_sha256")
                  for row in attributed_cells if isinstance(row, dict)}
    if len(signatures) != 1:
        raise V2BError("arm-specific model/source group layout")
    return next(iter(signatures))


def source_class_contrasts(attributed_targets):
    """Frozen B* E1a/E1b/E2 additive source-class contrasts.

    Every class uses the same contrast-specific complete-case target set;
    classes never create their own exclusions.  Effect shares are not
    computed because ratios are unstable near zero and under cancellation.
    """
    if not isinstance(attributed_targets, list) or not attributed_targets:
        raise V2BError("source contrasts need attributed targets")
    output = {}
    seen_targets = set()
    for target_index, target in enumerate(attributed_targets):
        if not isinstance(target, dict):
            raise V2BError(f"malformed attributed target[{target_index}]")
        key = target.get("target_key")
        cells = target.get("cells")
        if not isinstance(key, str) or not key or key in seen_targets \
                or not isinstance(cells, list) or not cells:
            raise V2BError(f"malformed/duplicate attributed target {key!r}")
        seen_targets.add(key)
        by_id = {row.get("cell_id"): row for row in cells
                 if isinstance(row, dict)}
        if len(by_id) != len(cells):
            raise V2BError(f"duplicate/malformed attributed cells: {key}")
        for name, minuend, subtrahend, eligibility in CONTRASTS:
            missing = [cell_id for cell_id in (minuend, subtrahend,
                                                *eligibility)
                       if cell_id not in by_id]
            if missing:
                raise V2BError(f"source contrast cells missing for {key}: "
                               f"{missing}")
            if not all(by_id[cell_id].get("eligible") is True
                       for cell_id in eligibility):
                continue
            left, right = by_id[minuend], by_id[subtrahend]
            if left.get("scored_body_bytes") != \
                    right.get("scored_body_bytes"):
                raise V2BError(f"contrast scored-body denominator drift: "
                               f"{key} {name}")
            left_classes = left.get("class_contribution_bpb")
            right_classes = right.get("class_contribution_bpb")
            if not isinstance(left_classes, dict) \
                    or not isinstance(right_classes, dict) \
                    or set(left_classes) != set(MODEL_GROUP_CLASSES) \
                    or set(right_classes) != set(MODEL_GROUP_CLASSES) \
                    or any(not isinstance(value, (int, float))
                           or isinstance(value, bool)
                           or not math.isfinite(value)
                           for value in (*left_classes.values(),
                                         *right_classes.values())):
                raise V2BError(f"malformed class contribution: {key} {name}")
            scalar_values = (left.get("roundoff_residual_bpb"),
                             right.get("roundoff_residual_bpb"),
                             left.get("primary_bpb"),
                             right.get("primary_bpb"))
            if not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and math.isfinite(value) for value in scalar_values):
                raise V2BError(f"malformed total contribution: {key} {name}")
            class_delta = {label: left_classes[label] - right_classes[label]
                           for label in MODEL_GROUP_CLASSES}
            residual_delta = scalar_values[0] - scalar_values[1]
            total_delta = scalar_values[2] - scalar_values[3]
            reconstructed = math.fsum(class_delta.values()) + residual_delta
            tolerance = CONTRAST_ROUNDING_ULPS * math.ulp(
                max(abs(total_delta), abs(left["primary_bpb"]),
                    abs(right["primary_bpb"]), 1.0))
            reconstruction_residual = total_delta - reconstructed
            if abs(reconstruction_residual) > tolerance:
                raise V2BError(f"source contrast does not reconstruct: "
                               f"{key} {name}")
            output.setdefault(name, []).append(dict(
                target_key=key,
                source_class_delta_bpb=class_delta,
                cell_roundoff_delta_bpb=residual_delta,
                contrast_reconstruction_residual_bpb=(
                    reconstruction_residual),
                total_delta_bpb=total_delta,
                scored_body_bytes=left["scored_body_bytes"]))

    rows_out = {}
    for name, _minuend, _subtrahend, _eligibility in CONTRASTS:
        rows = sorted(output.get(name, []), key=lambda row: row["target_key"])
        if not rows:
            rows_out[name] = dict(
                budget_bytes=DELTA_BUDGET_BYTES, n_targets=0, rows=[],
                target_equal_mean_total_delta_bpb=None,
                target_equal_mean_source_class_delta_bpb=None,
                target_equal_mean_cell_roundoff_delta_bpb=None,
                target_equal_mean_reconstruction_residual_bpb=None)
            continue
        n_rows = len(rows)
        class_means = {
            label: math.fsum(
                row["source_class_delta_bpb"][label] for row in rows) /
            n_rows for label in MODEL_GROUP_CLASSES}
        residual_mean = math.fsum(
            row["cell_roundoff_delta_bpb"] for row in rows) / n_rows
        total_mean = (math.fsum(row["total_delta_bpb"] for row in rows) /
                      n_rows)
        reconstructed_mean = math.fsum(class_means.values()) + residual_mean
        mean_reconstruction_residual = total_mean - reconstructed_mean
        tolerance = CONTRAST_ROUNDING_ULPS * math.ulp(
            max(abs(total_mean), 1.0))
        if abs(mean_reconstruction_residual) > tolerance:
            raise V2BError(f"repo source contrast mean does not reconstruct: "
                           f"{name}")
        rows_out[name] = dict(
            budget_bytes=DELTA_BUDGET_BYTES, n_targets=n_rows, rows=rows,
            target_equal_mean_total_delta_bpb=total_mean,
            target_equal_mean_source_class_delta_bpb=class_means,
            target_equal_mean_cell_roundoff_delta_bpb=residual_mean,
            target_equal_mean_reconstruction_residual_bpb=(
                mean_reconstruction_residual))
    return dict(
        schema=SOURCE_CONTRASTS_SCHEMA,
        claim="additive source-token NLL attribution",
        ast_node_attribution=False,
        denominator="total-scored-body-bytes",
        effect_shares_computed=False,
        contrast_roundoff_bound_ulps=CONTRAST_ROUNDING_ULPS,
        contrasts=rows_out)
