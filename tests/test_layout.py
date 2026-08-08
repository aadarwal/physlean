#!/usr/bin/env python3
"""Dependency-free unit tests for source-span grouping + window snapping
(PREREG §4). Run: python tests/test_layout.py"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from layout import token_spans, windows_of, snap_phase


def test_reviewer_synthetic():
    # "a∀b": a=1B, ∀=3B, b=1B; ∀ split across 3 tokens sharing span (1,2)
    text = "a∀b"
    offsets = [(0, 1), (1, 2), (1, 2), (1, 2), (2, 3)]
    lens, grps = token_spans(text, offsets)
    assert lens == [1, 3, 0, 0, 1], lens
    assert grps == [0, 1, 1, 1, 2], grps          # sequential ids, same partition
    assert sum(lens) == len(text.encode()) == 5


def test_partial_overlap_adds_bytes():
    text = "abcd"
    offsets = [(0, 2), (1, 4)]  # t1 overlaps but extends coverage
    lens, grps = token_spans(text, offsets)
    assert lens == [2, 2] and grps == [0, 0], (lens, grps)  # SAME group


def test_gap_charged_to_next_group():
    text = "abcd"
    offsets = [(0, 1), (3, 4)]
    lens, grps = token_spans(text, offsets)
    assert lens == [1, 3] and grps == [0, 1], (lens, grps)
    assert sum(lens) == 4


def test_leading_middle_trailing_multibyte():
    text = "∀x∈ℝ!∎"  # ∀ x ∈ ℝ ! ∎
    # simulate each multibyte char split into 2 tokens sharing its span
    offsets, c = [], 0
    for ch in text:
        offsets.append((c, c + 1))
        if len(ch.encode()) > 1:
            offsets.append((c, c + 1))
        c += 1
    lens, grps = token_spans(text, offsets)
    assert sum(lens) == len(text.encode())
    # every group opener has bytes; zero-byte rows never open groups
    seen = set()
    for L, g in zip(lens, grps):
        if g not in seen:
            assert L > 0, (lens, grps)
            seen.add(g)
        else:
            assert L == 0


def test_window_never_splits_group():
    grps = [0, 0, 1, 2, 2, 2, 3, 4, 4, 5]
    for ctx in (2, 3, 4, 5):
        spans = windows_of(len(grps), ctx, grps, min_tail=1)
        cover = []
        for ws, we in spans:
            assert ws == 0 or grps[ws] != grps[ws - 1], (ctx, spans)
            cover.extend(range(ws, we))
        assert cover == list(range(len(grps))), (ctx, spans)


def test_giant_group_extends_window():
    grps = [0, 1, 1, 1, 1, 1, 2, 3]  # group 1 wider than ctx=3
    spans = windows_of(len(grps), 3, grps, min_tail=1)
    for ws, we in spans:
        assert ws == 0 or grps[ws] != grps[ws - 1], spans
    assert [i for s, e in spans for i in range(s, e)] == list(range(len(grps)))


def test_window_phase_snap_logic():
    grps = [0, 1, 1, 1, 2, 3]
    assert snap_phase(grps, 2) == 4          # mid-group -> next boundary
    assert snap_phase(grps, 1) == 1          # already a boundary
    assert snap_phase(grps, 0) == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("LAYOUT TESTS PASS")
