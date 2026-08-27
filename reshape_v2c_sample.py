#!/usr/bin/env python3
"""Reshape the committed V2-c draw into per-repo bound samples.

The combined draw records per-repo N (52/77), but the frozen assembly
chain validates `n_requested_per_corpus` as a single integer per
sample artifact. This tool emits one bound-sample-compatible artifact
per repo from the COMMITTED combined draw — the plan objects are
copied byte-identically (each output's plans_sha256 recomputed over
its single-repo dict), and every output binds the combined draw's
sha256 so the reshape can never smuggle a different draw."""
import argparse
import sys

from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, sha256_sorted_json, \
    write_new_json

BOUND_SAMPLE_SCHEMA = "v2b_bound_sample_v2"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--combined", required=True)
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(args.combined)
    binding, combined = artifact_binding(args.combined,
                                         BOUND_SAMPLE_SCHEMA)
    v2c = combined.get("v2c")
    if not isinstance(v2c, dict):
        raise V2BError("combined sample lacks the v2c provenance block")
    plans = combined.get("plans") or {}
    tables = {row["repo"]: row
              for row in combined.get("candidate_tables") or []}
    for repo in sorted(plans):
        plan = plans[repo]
        n_repo = (v2c.get("n_by_repo") or {}).get(repo, {}) \
            .get("requested")
        if not isinstance(n_repo, int):
            raise V2BError(f"combined draw lacks requested N for {repo}")
        single = {repo: plan}
        artifact = dict(
            schema=BOUND_SAMPLE_SCHEMA,
            sampling_state="drawn",
            n_requested_per_corpus=n_repo,
            candidate_tables=[tables[repo]],
            candidates_generator=combined.get("candidates_generator"),
            a6_outcome=combined.get("a6_outcome"),
            plans=single,
            n_selected_total=plan.get("n_selected"),
            n_shortfall_total=sum(
                (plan.get("shortfalls") or {}).values()),
            plans_sha256=sha256_sorted_json(single),
            v2c=dict(v2c, reshaped_from=dict(
                path=args.combined, sha256=binding["sha256"],
                repo=repo)),
            generator=dict(source_commit=head_commit(),
                           source_tree_hash=source_tree_hash(),
                           program="reshape_v2c_sample.py"))
        out = f"{args.out_prefix}_{repo}.json"
        digest = write_new_json(out, artifact)
        print(f"V2C-RESHAPED {repo} n={n_repo} {out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
