#!/usr/bin/env python3
"""Expansion consistency gate (DOSE_CURVE_EXPANSION amendment, review C2).

A tier-set-expanded rerun of a frozen consumer must reproduce every tier
block of the committed prior artifact EXACTLY — same rows, same inference,
same statuses — before the expanded artifact may be committed as evidence.
This script IS that enforcement: it deep-compares every common tier block
and writes a small verification artifact recording both hashes; the
expanded artifact's evidence commit must include this report.
"""
import argparse
import sys

from provenance import head_commit, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, write_new_json

KNOWN_SCHEMAS = ("v2b_nll_ladder_analysis_v1", "v2b_budget_response_v1",
                 "v2b_k4x_sensitivity_v1")
VERIFY_SCHEMA = "v2b_expansion_consistency_v1"


def verify(prior, current):
    if prior.get("schema") not in KNOWN_SCHEMAS:
        raise V2BError(f"unknown prior schema {prior.get('schema')!r}")
    for field in ("schema", "repo", "metric", "claim_status"):
        if prior.get(field) != current.get(field):
            raise V2BError(f"artifact {field} drift: "
                           f"{prior.get(field)!r} != {current.get(field)!r}")
    if current.get("mode") != prior.get("mode"):
        raise V2BError("artifact mode drift")
    prior_tiers = prior.get("tiers")
    current_tiers = current.get("tiers")
    if not isinstance(prior_tiers, dict) or not prior_tiers \
            or not isinstance(current_tiers, dict):
        raise V2BError("artifact tier tables malformed")
    if not set(prior_tiers) < set(current_tiers):
        raise V2BError(
            f"expanded artifact must strictly extend the prior tier set: "
            f"prior {sorted(prior_tiers)} vs current {sorted(current_tiers)}")
    mismatched = [tier for tier in sorted(prior_tiers)
                  if prior_tiers[tier] != current_tiers[tier]]
    if mismatched:
        raise V2BError(
            f"expanded artifact does not reproduce the committed prior "
            f"tier blocks exactly: {mismatched}")
    return sorted(prior_tiers), sorted(set(current_tiers) - set(prior_tiers))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prior", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    require_committed(args.prior)
    prior_binding, prior = artifact_binding(args.prior)
    current_binding, current = artifact_binding(args.current)
    reproduced, added = verify(prior, current)
    report = dict(
        schema=VERIFY_SCHEMA,
        repo=prior.get("repo"), artifact_schema=prior.get("schema"),
        mode=prior.get("mode"),
        prior=dict(path=prior_binding["path"],
                   sha256=prior_binding["sha256"]),
        current=dict(path=current_binding["path"],
                     sha256=current_binding["sha256"]),
        tiers_reproduced_exactly=reproduced,
        tiers_added=added,
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="verify_v2b_expansion_consistency.py"))
    digest = write_new_json(args.out, report)
    print(f"V2B-EXPANSION-CONSISTENT {prior.get('repo')} "
          f"reproduced={len(reproduced)} added={len(added)} "
          f"{args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
