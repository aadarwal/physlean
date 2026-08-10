#!/usr/bin/env python3
"""Deep-closure mathlib4 supplement draw (EPOCH2_NIGHT_AMENDMENT, Part C).

n=120 additional mathlib4 targets under the UNCHANGED pilot draw law —
`build_sample_plan(candidates, 120, exclude_keys=<the 20 committed pilot
identities>)` — from the same committed candidate table and sealed A6
outcome. No closure precondition enters the draw; deep-closure membership
materializes at assembly exactly as in the pilot. The committed pilot
sample is a REQUIRED input: its mathlib4 plan supplies the exclusion set,
and disjointness is re-verified on the output. One draw only; the
artifact is write-once."""
import argparse
import sys

from finalize_v2b_sample import _validate_candidate_table, \
    _validate_outcome
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, identity_key, \
    sha256_sorted_json, validate_identity, write_new_json
from v2b_metadata import build_sample_plan

SUPPLEMENT_SCHEMA = "v2b_supplement_sample_v1"
BOUND_SAMPLE_SCHEMA = "v2b_bound_sample_v2"
SUPPLEMENT_N = 120
REPO = "mathlib4"


def build_supplement(candidates_path, outcome_path, pilot_sample_path):
    outcome_row = _validate_outcome(outcome_path)
    repo, binding, table, provenance = _validate_candidate_table(
        candidates_path)
    if repo != REPO:
        raise V2BError(f"supplement is mathlib4-only; got {repo!r}")
    _, pilot = artifact_binding(pilot_sample_path, BOUND_SAMPLE_SCHEMA)
    pilot_plan = (pilot.get("plans") or {}).get(REPO)
    if not isinstance(pilot_plan, dict):
        raise V2BError("committed pilot sample lacks a mathlib4 plan")
    if pilot_plan.get("candidates_sha256") != binding["sha256"]:
        raise V2BError("pilot plan was drawn from a different candidate "
                       "table than the one supplied")
    pilot_targets = pilot_plan.get("targets")
    if not isinstance(pilot_targets, list) or len(pilot_targets) != 20:
        raise V2BError(
            f"pilot mathlib4 plan holds "
            f"{len(pilot_targets) if isinstance(pilot_targets, list) else '?'} "
            f"targets; expected the committed 20")
    exclude = frozenset(
        identity_key("lean", validate_identity("lean", row["identity"]))
        for row in pilot_targets)
    if len(exclude) != 20:
        raise V2BError("pilot identities are not 20 distinct keys")
    plan = build_sample_plan(table, SUPPLEMENT_N, exclude_keys=exclude)
    plan["candidates_sha256"] = binding["sha256"]
    drawn_keys = {
        identity_key("lean", validate_identity("lean", row["identity"]))
        for row in (plan.get("targets") or [])}
    overlap = drawn_keys & exclude
    if overlap:
        raise V2BError(f"supplement draw overlaps pilot identities: "
                       f"{sorted(overlap)[:3]}")
    return dict(
        schema=SUPPLEMENT_SCHEMA,
        sampling_state="drawn",
        repo=REPO,
        n_requested=SUPPLEMENT_N,
        n_selected=plan.get("n_selected"),
        excluded_pilot_identities=sorted(exclude),
        candidate_table=dict(binding, repo=REPO),
        a6_outcome=outcome_row,
        pilot_sample_sha256=artifact_binding(
            pilot_sample_path, BOUND_SAMPLE_SCHEMA)[0]["sha256"],
        plan=plan,
        plan_sha256=sha256_sorted_json(plan),
        candidates_generator=dict(
            source_commit=provenance["source_commit"],
            source_tree_hash=provenance["source_tree_hash"],
            structural_cohort_sha256=provenance[
                "structural_cohort_sha256"],
            git_version=provenance["git_version"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--a6-outcome", required=True)
    ap.add_argument("--pilot-sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(args.a6_outcome)
    require_committed(args.pilot_sample)
    artifact = build_supplement(args.candidates, args.a6_outcome,
                                args.pilot_sample)
    artifact["generator"] = dict(source_commit=head_commit(),
                                 source_tree_hash=source_tree_hash(),
                                 program="finalize_v2b_supplement_sample.py")
    digest = write_new_json(args.out, artifact)
    print(f"V2B-SUPPLEMENT-DRAWN {artifact['n_selected']}/{SUPPLEMENT_N} "
          f"{args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
