#!/usr/bin/env python3
"""Pure tests for the write-once blind-review label projection."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from review_v2b_a6 import final_labels, validate_answers
from v2b_a6_blind import BLIND_RUBRIC
from v2b_common import A6_LABELS_SCHEMA, V2BError


def _presentation():
    return dict(n_pairs=2, pairs=[
        dict(pair_id="P-a", language="lean", left="a", right="b"),
        dict(pair_id="P-b", language="python", left="c", right="d")])


def test_final_labels_are_complete_and_presentation_ordered():
    answers = {"P-b": dict(label="not-duplicate", note="different call"),
               "P-a": dict(label="duplicate", note="")}
    value = final_labels(_presentation(), "f" * 64, "human", answers)
    assert value["schema"] == A6_LABELS_SCHEMA
    assert value["label_state"] == "blind-complete"
    assert value["rubric"] == BLIND_RUBRIC
    assert [row["pair_id"] for row in value["labels"]] == ["P-a", "P-b"]
    assert value["presentation_sha256"] == "f" * 64


def test_review_rejects_incomplete_foreign_and_malformed_answers():
    presentation = _presentation()
    cases = [
        ({"P-a": dict(label="duplicate", note="")}, True),
        ({"P-x": dict(label="duplicate", note="")}, False),
        ({"P-a": dict(label="maybe", note="")}, False),
        ({"P-a": dict(label="duplicate")}, False),
    ]
    for answers, complete in cases:
        try:
            validate_answers(presentation, answers, complete=complete)
            assert False, answers
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B BLIND REVIEWER TESTS PASS")
