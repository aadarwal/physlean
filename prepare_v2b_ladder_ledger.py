#!/usr/bin/env python3
"""Write the committed ladder completion ledger (NLL_LADDER_EXPLORATORY_AMENDMENT).

One row per (repo, tier): the exact completion path, its sha256, and the
Slurm job id that produced it (executing GPU recorded informationally by
the battery/launcher logs; GPU is never gated). Sealed q25c-1.5b rows are
taken from the committed exploratory reveal's completion bindings, never
rediscovered. The analyzer accepts only completions equal to their ledger
row, so this file must be committed BEFORE any analyzer run — and exactly
one scoring submission per tier feeds it (amendment rule)."""
import argparse
import json
import os
import sys

from analyze_v2b_nll_exploratory import NLL_EXPLORATORY_REVEAL_SCHEMA
from analyze_v2b_nll_ladder import FULL_TIER_SET, LADDER_LEDGER_SCHEMA, \
    PINNED_REVEAL_SHA256, SEALED_TIER
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, sha256_file, \
    write_new_json

REPOS = ("mathlib4", "batteries", "physlib", "sympy", "astropy")
REPO_TASK = {repo: index for index, repo in enumerate(REPOS)}


def build_ledger(paired_root, reveal, tier_jobs, prior_repos=None,
                 sealed_from_scan=False, repos=REPOS):
    """sealed_from_scan (EPOCH2): for completion families with NO sealed
    member (e.g. interior scoring, where every tier including q25c-1.5b
    is a fresh epoch-2 run), all six rows come from the directory scan
    and tier_jobs must cover the FULL tier set; the reveal is then not
    consulted for rows."""
    expected = FULL_TIER_SET if sealed_from_scan \
        else FULL_TIER_SET - {SEALED_TIER}
    if set(tier_jobs) != expected:
        raise V2BError(
            f"tier job ids must cover exactly {sorted(expected)}; "
            f"got {sorted(tier_jobs)}")
    out_repos = {}
    for repo in repos:
        rows = {}
        for tier, job in sorted(tier_jobs.items()):
            tier_dir = os.path.join(paired_root, tier)
            matches = [d for d in sorted(os.listdir(tier_dir))
                       if d.endswith("-" + repo)] \
                if os.path.isdir(tier_dir) else []
            if len(matches) != 1:
                raise V2BError(
                    f"expected exactly one {tier} completion dir for "
                    f"{repo}; found {matches}")
            path = os.path.join(tier_dir, matches[0], "complete.json")
            if not os.path.isfile(path):
                raise V2BError(f"missing complete.json: {path}")
            suffix = "" if sealed_from_scan else f"_{REPO_TASK[repo]}"
            rows[tier] = dict(path=os.path.abspath(path),
                              sha256=sha256_file(path),
                              slurm_job_id=f"{job}{suffix}")
        if sealed_from_scan:
            if prior_repos is not None:
                prior_rows = prior_repos.get(repo)
                if not isinstance(prior_rows, dict):
                    raise V2BError(f"prior ledger lacks repository {repo}")
                for tier, prior_row in prior_rows.items():
                    if rows.get(tier) != prior_row:
                        raise V2BError(
                            f"re-derived ledger row differs from the "
                            f"committed prior ledger: {repo} {tier}")
            out_repos[repo] = rows
            continue
        sealed = (reveal.get("repos", {}).get(repo, {})
                  .get("bindings", {}).get("completion"))
        if not isinstance(sealed, dict) \
                or not isinstance(sealed.get("path"), str) \
                or not isinstance(sealed.get("sha256"), str):
            raise V2BError(f"reveal lacks a sealed completion binding: "
                           f"{repo}")
        if sha256_file(sealed["path"]) != sealed["sha256"]:
            raise V2BError(f"sealed completion drifted from the reveal "
                           f"binding: {repo}")
        rows[SEALED_TIER] = dict(path=sealed["path"],
                                 sha256=sealed["sha256"],
                                 slurm_job_id="sealed-pilot")
        if prior_repos is not None:
            prior_rows = prior_repos.get(repo)
            if not isinstance(prior_rows, dict):
                raise V2BError(f"prior ledger lacks repository {repo}")
            for tier, prior_row in prior_rows.items():
                if rows.get(tier) != prior_row:
                    raise V2BError(
                        f"re-derived ledger row differs from the committed "
                        f"prior ledger: {repo} {tier}")
        out_repos[repo] = rows
    return out_repos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paired-root", default="results_v2/v2b/paired")
    ap.add_argument("--reveal", required=True)
    ap.add_argument("--tier-job", action="append", metavar="TIER=JOBID",
                    required=True)
    ap.add_argument("--prior-ledger", default=None,
                    help="committed prior ledger whose rows must be "
                         "carried forward byte-identically")
    ap.add_argument("--repos", default=None,
                    help="comma repo subset (EPOCH2 interior: "
                         "mathlib4,sympy); default all five")
    ap.add_argument("--sealed-from-scan", action="store_true",
                    help="EPOCH2: no sealed member; all tiers scanned "
                         "and tier_jobs covers the full set")
    ap.add_argument("--first-ledger", action="store_true",
                    help="explicitly assert this is the campaign's first "
                         "completion ledger (otherwise --prior-ledger is "
                         "REQUIRED)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(args.reveal)
    reveal_binding, reveal = artifact_binding(
        args.reveal, NLL_EXPLORATORY_REVEAL_SCHEMA)
    if reveal_binding["sha256"] != PINNED_REVEAL_SHA256:
        raise V2BError("reveal does not match the pinned committed reveal")
    tier_jobs = {}
    for pair in args.tier_job:
        tier, sep, job = pair.partition("=")
        if not sep or tier in tier_jobs or not job.isdigit():
            raise V2BError(f"malformed --tier-job: {pair!r}")
        tier_jobs[tier] = job
    if args.prior_ledger is None and not args.first_ledger:
        raise V2BError("--prior-ledger is required unless --first-ledger "
                       "is explicitly asserted")
    prior_repos = None
    if args.prior_ledger is not None:
        require_committed(args.prior_ledger)
        _, prior = artifact_binding(args.prior_ledger, LADDER_LEDGER_SCHEMA)
        prior_repos = prior.get("repos")
    ledger = dict(
        schema=LADDER_LEDGER_SCHEMA,
        note=("one scoring submission per tier; sealed rows from the "
              "committed reveal; analyzer requires row equality"),
        reveal_sha256=PINNED_REVEAL_SHA256,
        repos=build_ledger(args.paired_root, reveal, tier_jobs,
                           prior_repos=prior_repos,
                           sealed_from_scan=args.sealed_from_scan,
                           repos=(tuple(args.repos.split(","))
                                  if args.repos else REPOS)),
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="prepare_v2b_ladder_ledger.py"))
    digest = write_new_json(args.out, ledger)
    print(f"V2B-LADDER-LEDGER {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
