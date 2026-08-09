#!/usr/bin/env python3
"""Publish the write-once direct-scaling P0 protocol."""
import argparse
import os

from direct_scaling_protocol import build_protocol
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import V2BError, write_new_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="DIRECT_SCALING_STUDY.md")
    ap.add_argument("--corpora-lock", default="corpora_lock.json")
    ap.add_argument("--models-lock", default="models.json")
    ap.add_argument("--model-config-index", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree must be clean before P0 publication")
    commit, tree = head_commit(), source_tree_hash()
    protocol = build_protocol(
        design_path=args.design,
        corpora_lock_path=args.corpora_lock,
        models_lock_path=args.models_lock,
        model_config_index_path=args.model_config_index,
        generator={"program": os.path.basename(__file__),
                   "source_commit": commit, "source_tree_hash": tree},
    )
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed during P0 protocol construction")
    digest = write_new_json(args.out, protocol)
    print(f"[v2c-p0] {len(protocol['panel']['repositories'])} repos / "
          f"{len(protocol['panel']['models'])} models -> {args.out} "
          f"({digest[:12]})")


if __name__ == "__main__":
    main()
