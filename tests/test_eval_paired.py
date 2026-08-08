#!/usr/bin/env python3
"""Pure ledger/harness tests for the paired evaluator (no model load)."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval_paired import (body_token_ledger, nll_rows_for_token_indices,
                         paired_harness_hash)


def test_boundary_group_is_excluded_and_byte_ledger_is_exact():
    text = "abαβcd"
    # Token 1 crosses the boundary before β; α is known-prefix, β is body.
    offsets = [(0, 2), (2, 4), (4, 6)]
    ledger = body_token_ledger(text, offsets, body_start_char=3,
                               token_ids=[10, 11, 12])
    assert ledger["n_boundary_straddle_tokens"] == 1
    assert ledger["boundary_token_indices"] == [1]
    assert ledger["primary_token_indices"] == [2]
    assert ledger["straddled_body_bytes"] == len("β".encode())
    assert ledger["scored_body_bytes"] == len("cd".encode())
    assert ledger["straddled_body_codepoints"] == 1
    assert ledger["scored_body_codepoints"] == 2
    assert ledger["scored_body_bytes"] + ledger["straddled_body_bytes"] \
        == ledger["exact_body_bytes"]
    assert nll_rows_for_token_indices(ledger["primary_token_indices"]) == [1]


def test_overlapping_offset_tokens_stay_in_one_boundary_group():
    text = "xxprefixBODY"
    # Two tokenizer pieces overlap the same prefix/body character interval.
    offsets = [(0, 2), (2, 10), (8, 10), (10, 12)]
    ledger = body_token_ledger(text, offsets, body_start_char=8,
                               token_ids=[1, 2, 3, 4])
    assert ledger["boundary_token_indices"] == [1, 2]
    assert ledger["n_boundary_straddle_tokens"] == 2
    assert ledger["primary_token_indices"] == [3]
    assert len(ledger["boundary_groups"]) == 1


def test_no_straddle_uses_full_exact_body():
    text = "headBODY"
    offsets = [(0, 4), (4, 6), (6, 8)]
    ledger = body_token_ledger(text, offsets, body_start_char=4,
                               token_ids=[1, 2, 3])
    assert ledger["n_boundary_straddle_tokens"] == 0
    assert ledger["straddled_body_bytes"] == 0
    assert ledger["scored_body_bytes"] == ledger["exact_body_bytes"]
    assert ledger["primary_token_indices"] == [1, 2]


def test_paired_harness_hash_is_exact_canonical_three_file_binding():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for name in ("eval_paired.py", "eval_incontext.py", "layout.py"):
        digest = hashlib.sha256(open(os.path.join(root, name), "rb").read()
                                ).hexdigest()
        rows.append([name, digest])
    expected = hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert paired_harness_hash(root) == expected


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PAIRED EVALUATOR TESTS PASS")
