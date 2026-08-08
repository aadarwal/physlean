#!/usr/bin/env python3
"""Create the exact human-facing A6 artifact from a committed packet."""
import argparse

from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import build_blind_core, require_committed
from v2b_common import V2BError, write_new_json


def prepare(packet_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    require_committed(packet_path)
    commit_start, tree_start = head_commit(), source_tree_hash()
    presentation, _, _, _ = build_blind_core(packet_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during blind presentation")
    presentation["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="prepare_v2b_a6_blind.py")
    return presentation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    presentation = prepare(args.packet)
    digest = write_new_json(args.out, presentation)
    print(f"[v2b-a6-blind] {presentation['n_pairs']} pairs -> {args.out} "
          f"({digest[:12]})")


if __name__ == "__main__":
    main()
