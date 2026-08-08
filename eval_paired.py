#!/usr/bin/env python3
"""Paired fixed-target evaluator for DESIGN_V2.

The GPU CLI is completed only after the assembly manifest exists.  The
measurement-critical boundary ledger and harness identity live here now so
they are frozen and property-tested before any model score is produced.
"""
import hashlib
import os

# Hard imports required by DESIGN_V2 §15.A9: paired scoring must reuse the
# production model loader and chunked-NLL implementation, never fork copies.
from eval_incontext import eval_window, load_model  # noqa: F401
from layout import PAIRED_SCHEMA_VERSION, token_spans
from v2b_common import V2BError, canonical_json_bytes, sha256_json


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
