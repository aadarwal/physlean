#!/usr/bin/env python3
"""Pure source-span layout logic (PREREG §4) — no ML dependencies, so the
unit tests and the evaluator share one implementation."""

# Bump ONLY on semantic changes to the measurement (schema, grouping,
# windowing, ledger) — analysis-only commits must not invalidate dumps.
# v3: per-group doc attribution over the charged byte interval (straddling
# groups are doc=-1 uniformly).
MEASUREMENT_SCHEMA_VERSION = 3


def token_spans(text, offsets):
    """(byte_len, group_id) per token from char-offset intervals.

    A token OPENS a new source-span group iff its span starts at/after all
    chars covered so far (a >= prev_end); any overlap — including partial
    overlap that still adds new bytes — joins the current group (groups are
    transitive overlap of offset intervals). Byte length charges exactly
    the newly covered chars (offset gaps charge to the next opener), so
    group byte sums partition the text.
    """
    lens, grps = [], []
    prev_end = 0
    gid = -1
    for a, e in offsets:
        if a >= prev_end:
            gid += 1
        grps.append(gid)
        lens.append(len(text[prev_end:e].encode("utf-8")) if e > prev_end
                    else 0)
        prev_end = max(prev_end, e)
    return lens, grps


def windows_of(n_tok, ctx, grps, min_tail=1024):
    """Consecutive windows whose boundaries never split a source-span
    group. Nominal ends snap BACK to a group boundary; if a single group
    spans the whole window (pathological, tiny-ctx only) the window is
    EXTENDED past the group rather than splitting it, so every window
    start is a group opener — universally."""
    spans = []
    ws = 0
    while ws < n_tok:
        we = min(ws + ctx, n_tok)
        if we < n_tok and grps[we] == grps[we - 1]:  # nominal end mid-group
            wb = we
            while wb > ws + 1 and grps[wb] == grps[wb - 1]:
                wb -= 1
            if grps[wb] != grps[wb - 1]:
                we = wb                     # snapped back to a boundary
            else:                           # one group fills the window:
                while we < n_tok and grps[we] == grps[we - 1]:
                    we += 1                 # extend past the group
        spans.append((ws, we))
        ws = we
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_tail:
        spans = spans[:-1]
    return spans


def snap_phase(grps, phase):
    """Advance a window-phase offset so it never starts mid-group."""
    ph = max(0, phase)
    while 0 < ph < len(grps) and grps[ph] == grps[ph - 1]:
        ph += 1
    return ph
