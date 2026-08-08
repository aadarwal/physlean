#!/usr/bin/env python3
"""Write a deliberately incomplete human label template for one blind pack."""
import argparse

from v2b_a6_blind import BLIND_RUBRIC, require_committed
from v2b_common import (A6_BLIND_SCHEMA, A6_LABELS_SCHEMA, artifact_binding,
                        write_new_json)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presentation", required=True)
    ap.add_argument("--labeler", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    require_committed(args.presentation)
    binding, presentation = artifact_binding(
        args.presentation, A6_BLIND_SCHEMA)
    value = dict(
        schema=A6_LABELS_SCHEMA,
        label_state="INCOMPLETE-change-to-blind-complete",
        rubric=BLIND_RUBRIC,
        labeler=args.labeler,
        presentation_sha256=binding["sha256"],
        labels=[dict(pair_id=row["pair_id"], label="UNLABELED", note="")
                for row in presentation["pairs"]])
    digest = write_new_json(args.out, value)
    print(f"[v2b-a6-label-template] {len(value['labels'])} unlabeled pairs -> "
          f"{args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
