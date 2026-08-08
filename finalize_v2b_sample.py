#!/usr/bin/env python3
"""Production bound V2-b pilot sampler (B0): the only draw entry point.

Seals one five-corpus sample artifact from exactly the five bound
candidate tables plus the committed A6 label outcome. The A6 outcome is
BOUND FOR SEQUENCING ONLY — no outcome value influences any draw (the
frozen §15.A1 population deliberately includes near-duplicate targets),
so sampling stays outcome-blind while the outcome-before-draw ordering
becomes structural rather than procedural. All candidate tables must
come from one generator source/tree cohort, bind the same sealed V2-a
structural cohort, and carry the frozen corpus revisions; the plans
themselves are produced by the already-hardened build_sample_plan,
which revalidates cohorts, terciles, cells, and priorities from raw
covariates. Running this CLI IS the pilot draw: it is gated behind the
PREREG boundary, a committed A6 outcome, and the frozen per-corpus
draw size, and has no dev bypass. Candidate tables are large POOL
evidence (mathlib ~2.2e5 targets), never git-tracked: their exact
SHA256 is sealed into this small sample artifact and later rechecked
by assembly, so require_committed applies to the outcome ONLY.
"""
import argparse
import sys

from finalize_v2a import EVIDENCE_SOURCE_COMMIT
from finalize_v2b_a6 import EXPECTED
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (A6_OUTCOME_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, V2BError, artifact_binding,
                        sha256_json, sha256_sorted_json, write_new_json)
from v2b_lean_boundaries import BOUNDARIES_SCHEMA
from v2b_metadata import COHORT_CUTOFF, build_sample_plan

N_PER_CORPUS = 20


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _validate_candidate_table(path):
    binding, table = artifact_binding(path, CANDIDATES_SCHEMA)
    repo = table.get("repo")
    if repo not in EXPECTED:
        raise V2BError(f"unexpected candidate corpus {repo!r}")
    language, corpus_sha = EXPECTED[repo]
    generator = table.get("generator")
    structural = table.get("structural_evidence")
    boundary = table.get("lean_boundaries")
    git_version = table.get("git_version")
    if table.get("language") != language \
            or table.get("corpus_git_sha") != corpus_sha \
            or not isinstance(git_version, str) or not git_version.strip() \
            or table.get("cohort_cutoff") != COHORT_CUTOFF.isoformat() \
            or not isinstance(generator, dict) \
            or generator.get("program") != "prepare_v2b_candidates.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")) \
            or not isinstance(structural, dict) \
            or structural.get("evidence_source_commit") != \
            EVIDENCE_SOURCE_COMMIT \
            or not isinstance(structural.get("cohort"), dict) \
            or not _hex(structural["cohort"].get("sha256")):
        raise V2BError(f"malformed/binding-drifted candidates for {repo}")
    structural_boundary = structural.get("lean_boundaries")
    if language == "lean":
        if not isinstance(boundary, dict) \
                or boundary.get("schema") != BOUNDARIES_SCHEMA \
                or not _hex(boundary.get("sha256")) \
                or not isinstance(boundary.get("path"), str) \
                or not boundary["path"] \
                or structural_boundary != boundary:
            raise V2BError(f"Lean candidates lack boundary binding for "
                           f"{repo}")
    elif boundary is not None or structural_boundary is not None:
        raise V2BError(f"Python candidates carry Lean boundaries for {repo}")
    provenance = dict(source_commit=generator["source_commit"],
                      source_tree_hash=generator["source_tree_hash"],
                      structural_cohort_sha256=structural["cohort"]["sha256"],
                      git_version=git_version.strip())
    return repo, binding, table, provenance


def _validate_outcome(path):
    binding, outcome = artifact_binding(path, A6_OUTCOME_SCHEMA)
    generator = outcome.get("generator")
    labels = outcome.get("labels")
    outcomes = outcome.get("outcomes")
    if outcome.get("label_state") != "unblinded-from-committed-labels" \
            or outcome.get("sampling_state") != "not-drawn" \
            or not isinstance(generator, dict) \
            or generator.get("program") != "finalize_v2b_a6_labels.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")) \
            or not isinstance(outcomes, dict) \
            or outcome.get("outcomes_sha256") != \
            sha256_sorted_json(outcomes) \
            or not isinstance(labels, dict) \
            or not _hex(labels.get("sha256")) \
            or not _hex(labels.get("introducing_commit"), 40) \
            or not isinstance(outcome.get("packet"), dict) \
            or not _hex(outcome["packet"].get("sha256")) \
            or not isinstance(outcome.get("presentation"), dict) \
            or not _hex(outcome["presentation"].get("sha256")) \
            or not isinstance(outcome.get("n_blind_pairs"), int) \
            or isinstance(outcome.get("n_blind_pairs"), bool) \
            or outcome["n_blind_pairs"] <= 0:
        raise V2BError("A6 outcome is malformed or not a sealed pre-draw "
                       "unblinding")
    return dict(binding, label_state=outcome["label_state"],
                labels_sha256=labels["sha256"],
                labels_introducing_commit=labels["introducing_commit"],
                packet_sha256=outcome["packet"]["sha256"],
                presentation_sha256=outcome["presentation"]["sha256"],
                n_blind_pairs=outcome["n_blind_pairs"])


def build_bound_sample(candidate_paths, outcome_path, n=N_PER_CORPUS):
    """Pure five-corpus draw construction; callers gate real execution."""
    if not isinstance(candidate_paths, (list, tuple)) \
            or len(candidate_paths) != 5:
        raise V2BError("bound sampler requires exactly five candidate tables")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise V2BError(f"invalid per-corpus sample size {n!r}")
    outcome_row = _validate_outcome(outcome_path)
    rows = {}
    tables = {}
    provenances = {}
    for path in candidate_paths:
        repo, binding, table, provenance = _validate_candidate_table(path)
        if repo in rows:
            raise V2BError(f"duplicate candidate table for {repo}")
        rows[repo] = dict(binding, repo=repo,
                          language=EXPECTED[repo][0],
                          corpus_git_sha=EXPECTED[repo][1],
                          n_candidates=table.get("n_candidates"),
                          lean_boundaries=table.get("lean_boundaries"))
        tables[repo] = table
        provenances[repo] = provenance
    if set(rows) != set(EXPECTED):
        raise V2BError("candidate tables do not cover the exact corpus set")
    for field in ("source_commit", "source_tree_hash",
                  "structural_cohort_sha256", "git_version"):
        values = {provenance[field] for provenance in provenances.values()}
        if len(values) != 1:
            raise V2BError(f"candidate tables mix generator cohorts: {field}")
    plans = {}
    for repo in sorted(rows):
        plan = build_sample_plan(tables[repo], n)
        plan["candidates_sha256"] = rows[repo]["sha256"]
        plans[repo] = plan
    return dict(
        schema=BOUND_SAMPLE_SCHEMA,
        sampling_state="drawn",
        n_requested_per_corpus=n,
        candidate_tables=[rows[repo] for repo in sorted(rows)],
        candidates_generator=dict(
            source_commit=next(iter(provenances.values()))["source_commit"],
            source_tree_hash=next(
                iter(provenances.values()))["source_tree_hash"],
            structural_cohort_sha256=next(
                iter(provenances.values()))["structural_cohort_sha256"],
            git_version=next(iter(provenances.values()))["git_version"]),
        a6_outcome=outcome_row,
        plans=plans,
        n_selected_total=sum(plan["n_selected"] for plan in plans.values()),
        n_shortfall_total=sum(sum(plan["shortfalls"].values())
                              for plan in plans.values()),
        plans_sha256=sha256_sorted_json(plans))


def prepare(candidate_paths, outcome_path, n=N_PER_CORPUS):
    # The production draw is frozen at N_PER_CORPUS; only the pure
    # build_bound_sample helper accepts other sizes, for synthetic tests.
    if n != N_PER_CORPUS:
        raise V2BError(f"production pilot draw is frozen at "
                       f"n={N_PER_CORPUS} per corpus; got {n!r}")
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    # Only the SMALL A6 outcome must be a committed HEAD blob. Candidate
    # tables are large POOL evidence (mathlib ~2.2e5 targets) that is never
    # git-tracked; their exact SHA256 is sealed into this small sample
    # artifact instead, and assembly later rechecks those hashes.
    require_committed(outcome_path)
    commit_start, tree_start = head_commit(), source_tree_hash()
    sample = build_bound_sample(candidate_paths, outcome_path, n)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during the pilot draw")
    sample["generator"] = dict(source_commit=commit_start,
                               source_tree_hash=tree_start,
                               program="finalize_v2b_sample.py")
    return sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", action="append", required=True,
                    help="one bound candidates artifact; repeat five times")
    ap.add_argument("--a6-outcome", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sample = prepare(args.candidates, args.a6_outcome)
    digest = write_new_json(args.out, sample)
    print(f"[v2b-sample] drawn {sample['n_selected_total']} targets "
          f"({sample['n_shortfall_total']} shortfall) -> {args.out} "
          f"({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
