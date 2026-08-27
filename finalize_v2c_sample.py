#!/usr/bin/env python3
"""V2-c confirmatory draw (V2C_FEASIBILITY_AMENDMENT, ADOPTED).

Draws the planned repos at their governance-plan N under the amended
draw law: `build_sample_plan(candidates, N_repo, exclude_keys=<the 20
committed pilot identities>, test_stratum=True)` — the frozen pilot
machinery plus the amendment's Problem-3 test-module stratum. Two
in-band gates run BEFORE any V2-c plan is drawn: (a) the DEFAULT-path
byte-identity proof — build_sample_plan with defaults must equal the
committed pilot sample's own plan for the repo exactly, so the stratum
extension provably changed nothing about the sealed pilot law; (b) the
governance plan's bindings must match the supplied candidate tables.
Pilot disjointness is re-verified on every output plan. One draw only;
the artifact is write-once and emitted in the bound-sample-compatible
shape so the frozen assembly driver consumes it via --expected-n."""
import argparse
import json
import sys

from finalize_v2b_sample import _validate_candidate_table, \
    _validate_outcome
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, identity_key, \
    sha256_sorted_json, validate_identity, write_new_json
from v2b_metadata import build_sample_plan
from v2b_v2c_governance import V2C_PLAN_SCHEMA, AMENDMENT_SHA256

BOUND_SAMPLE_SCHEMA = "v2b_bound_sample_v2"
PILOT_N = 20


def _keys(language, rows):
    return frozenset(
        identity_key(language, validate_identity(language,
                                                 row["identity"]))
        for row in rows)


def build_v2c_sample(plan_path, repo_inputs, outcome_path,
                     pilot_sample_path):
    plan_binding, v2c_plan = artifact_binding(plan_path, V2C_PLAN_SCHEMA)
    if (v2c_plan.get("amendment") or {}).get("sha256") != AMENDMENT_SHA256:
        raise V2BError("governance plan does not bind the adopted "
                       "amendment")
    planned = v2c_plan.get("planned_repos") or []
    if sorted(repo_inputs) != sorted(planned):
        raise V2BError(f"draw inputs must cover exactly the planned "
                       f"repos {planned}; got {sorted(repo_inputs)}")
    outcome_row = _validate_outcome(outcome_path)
    _, pilot = artifact_binding(pilot_sample_path, BOUND_SAMPLE_SCHEMA)

    plans = {}
    candidate_rows = []
    provenances = {}
    n_by_repo = {}
    for repo in sorted(repo_inputs):
        candidates_path = repo_inputs[repo]
        table_repo, binding, table, provenance = _validate_candidate_table(
            candidates_path)
        if table_repo != repo:
            raise V2BError(f"candidate table repo mismatch for {repo}")
        row = v2c_plan["repos"][repo]
        if (row.get("bindings") or {}).get("candidates", {}) \
                .get("sha256") != binding["sha256"]:
            raise V2BError(f"candidate table is not the governance "
                           f"plan's sealed input: {repo}")
        n_repo = row.get("repo_n")
        if not isinstance(n_repo, int) or n_repo <= 0:
            raise V2BError(f"governance plan lacks a positive N: {repo}")
        pilot_plan = (pilot.get("plans") or {}).get(repo)
        if not isinstance(pilot_plan, dict):
            raise V2BError(f"committed pilot sample lacks a {repo} plan")
        if pilot_plan.get("candidates_sha256") != binding["sha256"]:
            raise V2BError(f"pilot plan came from a different candidate "
                           f"table: {repo}")
        language = table.get("language")
        pilot_targets = pilot_plan.get("targets") or []
        if len(pilot_targets) != PILOT_N:
            raise V2BError(f"pilot {repo} plan holds "
                           f"{len(pilot_targets)} targets; expected "
                           f"{PILOT_N}")
        # Gate (a): the DEFAULT sampler path must still reproduce the
        # committed pilot plan byte-for-byte — proof the test-stratum
        # extension changed nothing about the sealed law.
        rebuilt = build_sample_plan(table, PILOT_N)
        rebuilt["candidates_sha256"] = binding["sha256"]
        if rebuilt != pilot_plan:
            raise V2BError(f"default-path sampler drift: the rebuilt "
                           f"pilot plan differs from the committed one "
                           f"for {repo}")
        exclude = _keys(language, pilot_targets)
        if len(exclude) != PILOT_N:
            raise V2BError(f"pilot identities are not {PILOT_N} "
                           f"distinct keys: {repo}")
        plan = build_sample_plan(table, n_repo, exclude_keys=exclude,
                                 test_stratum=True)
        plan["candidates_sha256"] = binding["sha256"]
        drawn = _keys(language, plan.get("targets") or [])
        if drawn & exclude:
            raise V2BError(f"V2-c draw overlaps pilot identities: "
                           f"{sorted(drawn & exclude)[:3]}")
        plans[repo] = plan
        n_by_repo[repo] = dict(requested=n_repo,
                               selected=plan.get("n_selected"))
        candidate_rows.append(dict(
            binding, repo=repo, language=language,
            corpus_git_sha=table.get("corpus_git_sha"),
            n_candidates=table.get("n_candidates"),
            lean_boundaries=table.get("lean_boundaries")))
        provenances[repo] = provenance

    provenance = provenances[sorted(provenances)[0]]
    requested = {repo: row["requested"] for repo, row in n_by_repo.items()}
    return dict(
        schema=BOUND_SAMPLE_SCHEMA,
        sampling_state="drawn",
        # Per-repo N differs by design; the assembly driver receives the
        # per-repo value via --expected-n at submission.
        n_requested_per_corpus=requested[sorted(requested)[0]]
        if len(set(requested.values())) == 1 else None,
        n_requested_by_repo=requested,
        candidate_tables=candidate_rows,
        candidates_generator=dict(
            source_commit=provenance["source_commit"],
            source_tree_hash=provenance["source_tree_hash"],
            structural_cohort_sha256=provenance[
                "structural_cohort_sha256"],
            git_version=provenance["git_version"]),
        a6_outcome=outcome_row,
        plans=plans,
        n_selected_total=sum(row["selected"] or 0
                             for row in n_by_repo.values()),
        n_shortfall_total=sum(
            sum((plans[repo].get("shortfalls") or {}).values())
            for repo in plans),
        plans_sha256=sha256_sorted_json(plans),
        v2c=dict(
            kind="v2c-confirmatory-draw",
            claim_label=v2c_plan.get("claim_label"),
            amendment_sha256=AMENDMENT_SHA256,
            governance_plan_sha256=plan_binding["sha256"],
            n_by_repo=n_by_repo,
            test_stratum=True,
            pilot_sample_sha256=artifact_binding(
                pilot_sample_path, BOUND_SAMPLE_SCHEMA)[0]["sha256"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", action="append", required=True,
                    metavar="REPO=CANDIDATES_PATH")
    ap.add_argument("--a6-outcome", required=True)
    ap.add_argument("--pilot-sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    for path in (args.plan, args.a6_outcome, args.pilot_sample):
        require_committed(path)
    repo_inputs = {}
    for spec in args.repo:
        repo, sep, path = spec.partition("=")
        if not sep or repo in repo_inputs:
            raise V2BError(f"malformed --repo spec: {spec!r}")
        repo_inputs[repo] = path
    artifact = build_v2c_sample(args.plan, repo_inputs, args.a6_outcome,
                                args.pilot_sample)
    artifact["generator"] = dict(source_commit=head_commit(),
                                 source_tree_hash=source_tree_hash(),
                                 program="finalize_v2c_sample.py")
    digest = write_new_json(args.out, artifact)
    totals = json.dumps(artifact["v2c"]["n_by_repo"], sort_keys=True)
    print(f"V2C-DRAWN {totals} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
