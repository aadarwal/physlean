#!/usr/bin/env python3
"""Synthetic tests for the production bound V2-b sampler (B0).

No real candidate artifact, A6 outcome, or study sample is read or
drawn; fixtures are built from the frozen constants so every binding
check is exercised fail-closed.
Run: python3 tests/test_finalize_v2b_sample.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2a import EVIDENCE_SOURCE_COMMIT
from finalize_v2b_a6 import EXPECTED
from finalize_v2b_sample import (BOUND_SAMPLE_SCHEMA, build_bound_sample)
from v2b_common import (A6_OUTCOME_SCHEMA, CANDIDATES_SCHEMA, V2BError,
                        sha256_sorted_json)
from v2b_lean_boundaries import BOUNDARIES_SCHEMA
from v2b_metadata import (COHORT_CUTOFF, SAMPLING_SEED, cohort_of,
                          seeded_hash, tercile, tercile_cutpoints)

PRE = "2023-05-01T00:00:00+00:00"
POST = "2025-01-01T00:00:00+00:00"


def _target(language, repo, index, body_bytes, degree, stamp):
    if language == "lean":
        identity = [f"M{index}", f"M{index}.t"]
    else:
        identity = [f"m{index}", "f", index]
    row = dict(identity=identity, body_bytes=body_bytes,
                module_in_degree=degree,
                source_rel=f"src/{repo}/u{index}.txt",
                first_add=dict(timestamp_utc=stamp,
                               provenance_mode="exact-add",
                               exact_add_unresolved=False,
                               n_add_records=1))
    if language == "lean":
        row["span_id"] = f"{index + 1:064x}"
    return row


def _candidates(td, repo):
    language, corpus_sha = EXPECTED[repo]
    boundary = (dict(path=f"/x/{repo}-boundaries.json",
                     sha256=(repo.encode().hex() + "0" * 64)[:64],
                     schema=BOUNDARIES_SCHEMA)
                if language == "lean" else None)
    targets = [
        _target(language, repo, 0, 40, 0, PRE),
        _target(language, repo, 1, 80, 1, PRE),
        _target(language, repo, 2, 120, 2, POST),
        _target(language, repo, 3, 200, 3, POST),
        _target(language, repo, 4, 300, 5, PRE),
        _target(language, repo, 5, 500, 9, POST),
    ]
    cuts_len = tercile_cutpoints([t["body_bytes"] for t in targets])
    cuts_deg = tercile_cutpoints([t["module_in_degree"] for t in targets])
    for t in targets:
        cohort = cohort_of(t["first_add"])
        lt = tercile(t["body_bytes"], *cuts_len)
        ct = tercile(t["module_in_degree"], *cuts_deg)
        t["cohort"] = cohort
        t["strata"] = dict(length_tercile=lt, centrality_tercile=ct,
                           cohort=cohort)
        t["cell"] = f"L{lt}-D{ct}-C{cohort}"
        t["priority"] = seeded_hash(SAMPLING_SEED, repo, *t["identity"])
    value = dict(
        schema=CANDIDATES_SCHEMA, repo=repo, language=language,
        corpus_git_sha=corpus_sha,
        git_version="git version 2.44.0",
        cohort_cutoff=COHORT_CUTOFF.isoformat(),
        tercile_cutpoints=dict(body_bytes=list(cuts_len),
                               module_in_degree=list(cuts_deg)),
        first_add_provenance_file_counts={
            "exact-add": len(targets), "no-add-pre-witness": 0},
        no_add_pre_witness_files=[],
        lean_boundaries=boundary,
        n_candidates=len(targets), targets=targets,
        structural_evidence=dict(
            evidence_source_commit=EVIDENCE_SOURCE_COMMIT,
            cohort=dict(path="/x/cohort.json", sha256="c" * 64,
                        schema="v2a_structural_cohort_v1"),
            lean_boundaries=boundary),
        generator=dict(source_commit="a" * 40, source_tree_hash="b" * 64,
                       program="prepare_v2b_candidates.py"))
    path = os.path.join(td, f"candidates_{repo}.json")
    json.dump(value, open(path, "w"), sort_keys=True)
    return path


def _outcome(td, sampling_state="not-drawn", break_sha=False,
             name="outcome.json"):
    outcomes = dict(jaccard=dict(lean=dict(outcome="0.80")),
                    collision_activation=dict(lean={}))
    value = dict(
        schema=A6_OUTCOME_SCHEMA,
        label_state="unblinded-from-committed-labels",
        sampling_state=sampling_state,
        packet=dict(path="/x/p.json", sha256="1" * 64),
        presentation=dict(path="/x/b.json", sha256="2" * 64),
        labels=dict(path="/x/l.json", sha256="3" * 64,
                    introducing_commit="d" * 40),
        labeler="aadarwal",
        n_blind_pairs=12,
        n_projected_roles=14,
        outcomes=outcomes,
        outcomes_sha256=("0" * 64 if break_sha else
                         sha256_sorted_json(outcomes)),
        generator=dict(source_commit="e" * 40, source_tree_hash="f" * 64,
                       program="finalize_v2b_a6_labels.py"))
    path = os.path.join(td, name)
    json.dump(value, open(path, "w"), sort_keys=True)
    return path


def _all_candidates(td):
    return [_candidates(td, repo) for repo in sorted(EXPECTED)]


def test_bound_sample_is_deterministic_and_fully_bound():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        outcome = _outcome(td)
        sample = build_bound_sample(list(reversed(paths)), outcome, n=3)
        assert sample["schema"] == BOUND_SAMPLE_SCHEMA
        assert sample["sampling_state"] == "drawn"
        assert sorted(sample["plans"]) == sorted(EXPECTED)
        assert [row["repo"] for row in sample["candidate_tables"]] == \
            sorted(EXPECTED)
        for row in sample["candidate_tables"]:
            plan = sample["plans"][row["repo"]]
            assert plan["candidates_sha256"] == row["sha256"]
            assert plan["n_selected"] == 3
        assert sample["a6_outcome"]["labels_introducing_commit"] == "d" * 40
        assert sample["n_selected_total"] == 15
        assert sample == build_bound_sample(paths, outcome, n=3)
        published = os.path.join(td, "published-sample.json")
        json.dump(sample, open(published, "w"), sort_keys=True)
        reloaded = json.load(open(published))
        assert reloaded["plans_sha256"] == \
            sha256_sorted_json(reloaded["plans"])
        # population-limited draw records shortfall, never rebalances
        big = build_bound_sample(paths, outcome, n=20)
        assert big["plans"]["sympy"]["n_selected"] == 6
        assert sum(big["plans"]["sympy"]["shortfalls"].values()) == 14
        assert big["n_shortfall_total"] == 5 * 14


def test_mixed_generator_cohort_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        outcome = _outcome(td)
        value = json.load(open(paths[0]))
        value["generator"]["source_commit"] = "9" * 40
        json.dump(value, open(paths[0], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "mixed candidate cohort accepted"
        except V2BError as err:
            assert "cohort" in str(err)


def test_missing_duplicate_and_foreign_corpora_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        outcome = _outcome(td)
        try:
            build_bound_sample(paths[:4], outcome, n=2)
            assert False
        except V2BError:
            pass
        try:
            build_bound_sample(paths[:4] + [paths[0]], outcome, n=2)
            assert False, "duplicate corpus substituted for missing one"
        except V2BError:
            pass
        value = json.load(open(paths[0]))
        value["repo"] = "qutip"
        json.dump(value, open(paths[0], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "foreign corpus accepted"
        except V2BError:
            pass


def test_outcome_state_and_hash_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        for bad in (_outcome(td, sampling_state="drawn",
                             name="bad_state.json"),
                    _outcome(td, break_sha=True, name="bad_hash.json")):
            try:
                build_bound_sample(paths, bad, n=2)
                assert False, "drifted A6 outcome accepted"
            except V2BError:
                pass
        good = json.load(open(_outcome(td)))
        good["label_state"] = "unlabeled"
        bad_path = os.path.join(td, "bad_outcome.json")
        json.dump(good, open(bad_path, "w"), sort_keys=True)
        try:
            build_bound_sample(paths, bad_path, n=2)
            assert False, "pre-unblind outcome accepted"
        except V2BError:
            pass


def test_candidate_priority_and_structural_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        outcome = _outcome(td)
        value = json.load(open(paths[1]))
        value["targets"][0]["priority"] = "0" * 64
        json.dump(value, open(paths[1], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "priority drift accepted"
        except V2BError:
            pass
        paths = _all_candidates(td + "")  # rebuild clean set in same dir
        value = json.load(open(paths[2]))
        value["structural_evidence"]["evidence_source_commit"] = "9" * 40
        json.dump(value, open(paths[2], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "structural evidence drift accepted"
        except V2BError:
            pass


def test_mixed_git_version_and_cutoff_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)
        outcome = _outcome(td)
        value = json.load(open(paths[3]))
        value["git_version"] = "git version 2.39.5"
        json.dump(value, open(paths[3], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "mixed git versions accepted"
        except V2BError as err:
            assert "git_version" in str(err)
        paths = _all_candidates(td)
        value = json.load(open(paths[1]))
        value["git_version"] = "   "
        json.dump(value, open(paths[1], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "blank git version accepted"
        except V2BError:
            pass
        paths = _all_candidates(td)
        value = json.load(open(paths[2]))
        value["cohort_cutoff"] = "2024-11-12T00:00:00+00:00"
        json.dump(value, open(paths[2], "w"), sort_keys=True)
        try:
            build_bound_sample(paths, outcome, n=2)
            assert False, "cohort cutoff drift accepted"
        except V2BError:
            pass


def test_production_draw_size_is_frozen_at_twenty():
    import finalize_v2b_sample as fs
    for bad in (3, 0, 19, 21, True, None):
        try:
            fs.prepare(["x"] * 5, "/nonexistent", n=bad)
            assert False, f"non-frozen draw size accepted: {bad!r}"
        except V2BError as err:
            assert "frozen" in str(err)
    assert fs.N_PER_CORPUS == 20


def test_schema_constant_has_single_source():
    import v2b_common
    import finalize_v2b_sample as fs
    assert fs.BOUND_SAMPLE_SCHEMA is v2b_common.BOUND_SAMPLE_SCHEMA
    assert v2b_common.BOUND_SAMPLE_SCHEMA == "v2b_bound_sample_v2"


def test_untracked_candidates_accepted_but_outcome_commit_enforced():
    """Candidates are large POOL evidence, never git-tracked: prepare must
    accept untracked candidate paths (their SHA is sealed instead) while
    still refusing an uncommitted A6 outcome."""
    import finalize_v2b_sample as fs
    with tempfile.TemporaryDirectory() as td:
        paths = _all_candidates(td)          # tempdir = untracked by nature
        outcome = _outcome(td)
        saved = (fs.source_clean, fs.head_commit, fs.source_tree_hash,
                 fs.require_committed)
        calls = []
        try:
            fs.source_clean = lambda: True
            fs.head_commit = lambda: "1" * 40
            fs.source_tree_hash = lambda: "2" * 64
            fs.require_committed = lambda path: calls.append(path)
            sample = fs.prepare(paths, outcome, n=20)
            assert calls == [outcome]        # outcome only, no candidates
            assert sample["sampling_state"] == "drawn"
            assert sample["generator"]["source_commit"] == "1" * 40

            def refuse(path):
                raise V2BError(f"blind boundary input is not tracked: {path}")
            fs.require_committed = refuse
            try:
                fs.prepare(paths, outcome, n=20)
                assert False, "uncommitted outcome accepted"
            except V2BError as err:
                assert "not tracked" in str(err)
        finally:
            (fs.source_clean, fs.head_commit, fs.source_tree_hash,
             fs.require_committed) = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B BOUND SAMPLER TESTS PASS")
