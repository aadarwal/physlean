#!/usr/bin/env python3
"""Unblind committed complete A6 labels and run the frozen gates."""
import argparse

from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import (build_outcome, require_committed,
                          require_single_commit)
from v2b_common import V2BError, write_new_json


def prepare(packet_path, presentation_path, labels_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    for path in (packet_path, presentation_path, labels_path):
        require_committed(path)
    labels_commit = require_single_commit(labels_path)
    commit_start, tree_start = head_commit(), source_tree_hash()
    outcome = build_outcome(packet_path, presentation_path, labels_path)
    outcome["labels"]["introducing_commit"] = labels_commit
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during A6 unblinding")
    outcome["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="finalize_v2b_a6_labels.py")
    return outcome


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet", required=True)
    ap.add_argument("--presentation", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    outcome = prepare(args.packet, args.presentation, args.labels)
    digest = write_new_json(args.out, outcome)
    print(f"[v2b-a6-outcome] {outcome['n_blind_pairs']} blind pairs / "
          f"{outcome['n_projected_roles']} roles -> {args.out} "
          f"({digest[:12]})")


if __name__ == "__main__":
    main()
