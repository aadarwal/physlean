#!/usr/bin/env python3
"""Conservative three-judge blinded A6 adjudication.

This evidence-only producer lives under results_v2 so adding it cannot change
the already battery-bound measurement source-tree projection. It never reads
model outcomes and never receives unblinded A6 roles in a judge input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from v2b_a6_blind import (BLIND_RUBRIC, _validate_presentation)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (A6_LABELS_SCHEMA, V2BError, artifact_binding,
                        sha256_file, sha256_sorted_json, write_new_json)
from v2b_neardup import (LEAN_SENTINEL, PYTHON_KEYWORDS,
                         PYTHON_SENTINELS, lean_identifier_spelling,
                         lex_unit, load_lean_keyword_freeze)


JUDGE_SCHEMA = "v2b_a6_blind_judgments_v1"
EVIDENCE_SCHEMA = "v2b_a6_automatic_adjudication_v1"
INDEPENDENCE = (
    "fresh-context;blind-presentation-only;no-packet-identities-statistics-"
    "roles-sample-salt-or-outcomes")


def _exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise V2BError(f"{where}: exact keys {set(keys)!r} required")


def _load_judge(path, presentation_sha, pair_ids):
    binding, value = artifact_binding(path, JUDGE_SCHEMA)
    _exact(value, {"schema", "adjudicator", "independence_declaration",
                   "presentation_sha256", "rubric", "judgments"},
           "A6 judge artifact")
    adjudicator = value["adjudicator"]
    _exact(adjudicator, {"id", "model", "fresh_context"},
           "A6 adjudicator")
    if not all(isinstance(adjudicator[key], str) and adjudicator[key].strip()
               for key in ("id", "model")) \
            or adjudicator["fresh_context"] is not True \
            or value["independence_declaration"] != INDEPENDENCE \
            or value["presentation_sha256"] != presentation_sha \
            or value["rubric"] != BLIND_RUBRIC \
            or not isinstance(value["judgments"], list):
        raise V2BError("A6 judge provenance/rubric drift")
    labels = {}
    for row in value["judgments"]:
        _exact(row, {"pair_id", "label", "reason"}, "A6 judgment")
        pair_id = row["pair_id"]
        if pair_id in labels \
                or row["label"] not in {"duplicate", "not-duplicate"} \
                or not isinstance(row["reason"], str) \
                or not 1 <= len(row["reason"].strip()) <= 500:
            raise V2BError("malformed/duplicate A6 judgment")
        labels[pair_id] = row["label"]
    if set(labels) != set(pair_ids):
        raise V2BError("A6 judge does not cover the exact presentation")
    return binding, adjudicator, labels


def _is_identifier(language, kind, value, lean_keywords):
    if language == "python":
        return kind == "NAME" and value not in PYTHON_KEYWORDS
    return kind == "IDENT" and value not in lean_keywords \
        and lean_identifier_spelling(value)


def lexical_gate(language, left, right, lean_keywords):
    """True only for exact lexical equality modulo <=1 identifier mapping."""
    left_records = lex_unit(language, left)
    right_records = lex_unit(language, right)
    if len(left_records) != len(right_records):
        return False
    layout = set(PYTHON_SENTINELS) | {LEAN_SENTINEL}
    forward, reverse = {}, {}
    for (left_kind, left_value), (right_kind, right_value) in zip(
            left_records, right_records):
        if left_kind != right_kind:
            return False
        if left_kind in layout:
            continue
        left_id = _is_identifier(
            language, left_kind, left_value, lean_keywords)
        right_id = _is_identifier(
            language, right_kind, right_value, lean_keywords)
        if left_id != right_id:
            return False
        if not left_id:
            if left_value != right_value:
                return False
            continue
        prior = forward.setdefault(left_value, right_value)
        inverse = reverse.setdefault(right_value, left_value)
        if prior != right_value or inverse != left_value:
            return False
    nonidentity = sum(1 for left, right in forward.items()
                      if left != right)
    return nonidentity <= 1


def prepare(args):
    if not source_clean():
        raise V2BError("measurement source dirty before A6 adjudication")
    source_commit = head_commit()
    source_tree = source_tree_hash()
    presentation_binding, presentation, _mapping, packet_binding, _packet = \
        _validate_presentation(args.presentation, args.packet)
    contract_sha = sha256_file(args.contract)
    if Path(args.contract).resolve() != Path(__file__).with_name(
            "AUTOMATED_ADJUDICATION_AMENDMENT.md").resolve():
        raise V2BError("A6 automatic contract must be the canonical amendment")
    lean_keywords, keyword_binding = load_lean_keyword_freeze(
        args.lean_keyword_freeze)
    pairs = presentation["pairs"]
    pair_ids = [row["pair_id"] for row in pairs]
    if len(pair_ids) != len(set(pair_ids)) \
            or presentation.get("rubric") != BLIND_RUBRIC:
        raise V2BError("A6 blind presentation pair/rubric drift")
    judges = [_load_judge(path, presentation_binding["sha256"], pair_ids)
              for path in args.judge]
    judge_ids = [row[1]["id"] for row in judges]
    judge_models = [row[1]["model"] for row in judges]
    if len(judges) != 3 or len(set(judge_ids)) != 3 \
            or len(set(judge_models)) < 2:
        raise V2BError("A6 requires three distinct judges and >=2 models")
    decisions = []
    label_rows = []
    for pair in pairs:
        pair_id = pair["pair_id"]
        mechanical = lexical_gate(pair["language"], pair["left"],
                                  pair["right"], lean_keywords)
        votes = [row[2][pair_id] for row in judges]
        label = "duplicate" if mechanical and votes.count("duplicate") >= 2 \
            else "not-duplicate"
        decisions.append({"pair_id": pair_id,
                          "lexical_gate": mechanical,
                          "judge_votes": votes, "label": label})
        label_rows.append({"pair_id": pair_id, "label": label, "note": ""})
    inputs = {
        "packet": packet_binding,
        "presentation": presentation_binding,
        "keyword_freeze": keyword_binding,
        "contract_sha256": contract_sha,
        "producer_sha256": sha256_file(__file__),
        "judges": [row[0] for row in judges],
    }
    decision_binding = sha256_sorted_json({"inputs": inputs,
                                           "decisions": decisions})
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "adjudication_state": "blind-complete-before-sampling",
        "rubric": BLIND_RUBRIC,
        "rule": ("duplicate-iff-lexical-gate-and-at-least-two-of-three-"
                 "blind-judge-votes-duplicate"),
        "inputs": inputs,
        "decisions": decisions,
        "decision_binding": decision_binding,
        "binding_rule": (
            "evidence_binding=sha256_sorted_json(root-before-inserting-"
            "evidence_binding)"),
        "generator": {"program": os.path.basename(__file__),
                      "source_commit": source_commit,
                      "source_tree_hash": source_tree},
    }
    evidence["evidence_binding"] = sha256_sorted_json(evidence)
    labels = {
        "schema": A6_LABELS_SCHEMA,
        "label_state": "blind-complete",
        "rubric": BLIND_RUBRIC,
        "labeler": f"automatic-majority-v1:{decision_binding}",
        "presentation_sha256": presentation_binding["sha256"],
        "labels": label_rows,
    }
    if not source_clean() or head_commit() != source_commit \
            or source_tree_hash() != source_tree:
        raise V2BError("measurement source drifted during A6 adjudication")
    return evidence, labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True)
    parser.add_argument("--presentation", required=True)
    parser.add_argument("--lean-keyword-freeze", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--judge", action="append", required=True)
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("--labels-out", required=True)
    args = parser.parse_args()
    if len(args.judge) != 3:
        raise V2BError("exactly three --judge inputs are required")
    evidence, labels = prepare(args)
    evidence_sha = write_new_json(args.evidence_out, evidence)
    labels_sha = write_new_json(args.labels_out, labels)
    print(f"[v2b-a6-auto] {len(labels['labels'])} blind pairs -> "
          f"evidence {evidence_sha[:12]} / labels {labels_sha[:12]}")


if __name__ == "__main__":
    main()
