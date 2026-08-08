#!/usr/bin/env python3
"""Model-free longitudinal inventory: mainline selection and Git evidence."""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_longitudinal_inventory import (PLAN_SCHEMA, SELECTION, STATE,
                                            _blob_at, _source_listing,
                                            _validate_plan,
                                            build_inventory)
from v2b_common import V2BError


def _git(repo, *args, env=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, env=merged)
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)


def _commit(repo, message, stamp):
    _git(repo, "add", "-A")
    env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    _git(repo, "commit", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD")


def _fixture(td):
    repo = os.path.join(td, "repo")
    os.makedirs(repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Inventory Test")
    _git(repo, "config", "user.email", "inventory@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _write(os.path.join(repo, "A.lean"), "def a := 1\n")
    _write(os.path.join(repo, "lean-toolchain"), "leanprover/lean4:v1\n")
    _write(os.path.join(repo, "lake-manifest.json"), "{}\n")
    first = _commit(repo, "first", "2021-01-01T00:00:00Z")

    _write(os.path.join(repo, "A.lean"), "def a := 2\n")
    _write(os.path.join(repo, "Pkg", "B.lean"), "def b := 1\n")
    _write(os.path.join(repo, "lean-toolchain"), "leanprover/lean4:v2\n")
    second = _commit(repo, "second", "2021-06-01T00:00:00Z")

    _git(repo, "checkout", "-b", "side")
    _write(os.path.join(repo, "Side.lean"), "def side := 1\n")
    _commit(repo, "side", "2021-08-01T00:00:00Z")
    _git(repo, "checkout", "main")

    _write(os.path.join(repo, "Pkg", "B.lean"), "def b := 200\n")
    _write(os.path.join(repo, "Pkg", "C.lean"), "def c := 3\n")
    _write(os.path.join(repo, "lean-toolchain"), "leanprover/lean4:v3\n")
    third = _commit(repo, "third", "2022-06-01T00:00:00Z")
    env = {"GIT_AUTHOR_DATE": "2022-09-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2022-09-01T00:00:00Z"}
    _git(repo, "merge", "--no-ff", "side", "-m", "merge side", env=env)
    head = _git(repo, "rev-parse", "HEAD")

    plan = dict(schema=PLAN_SCHEMA, state=STATE, repo="synthetic",
                language="lean", expected_head=head,
                selection=SELECTION, source_suffixes=[".lean"],
                limitations=["feasibility only", "no scale estimate"],
                cutoffs_utc=["2021-09-01T00:00:00Z",
                             "2022-07-01T00:00:00Z"])
    plan_path = os.path.join(td, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh)
    return repo, plan_path, plan, first, second, third, head


def test_first_parent_calendar_selection_and_inventory():
    with tempfile.TemporaryDirectory() as td:
        repo, plan_path, _plan, _first, second, third, head = _fixture(td)
        out = build_inventory(plan_path, repo)
        assert out["state"] == STATE
        assert out["scope"] == "historical-snapshot-ladder-only"
        assert out["future_evaluation_unit_feasibility"] == "not-assessed"
        assert out["limitations"] == ["feasibility only",
                                       "no scale estimate"]
        assert out["expected_head"] == head
        assert out["n_snapshots"] == 2 and out["n_intervals"] == 1
        assert [row["commit"] for row in out["snapshots"]] == [second, third]
        # The August side-branch commit is reachable from HEAD, but must not
        # become the September snapshot because selection is first-parent.
        assert out["snapshots"][0]["source"]["n_source_files"] == 2
        assert out["snapshots"][1]["source"]["n_source_files"] == 3
        assert out["snapshots"][0]["lean_toolchain"]["value"].endswith("v2")
        assert out["snapshots"][1]["lean_toolchain"]["value"].endswith("v3")
        for snapshot in out["snapshots"]:
            proof = snapshot["temporal_proof"]
            assert proof["date_semantics"] == \
                "committer-date-at-or-before-inclusive"
            assert proof["n_reachable_commits_after_cutoff"] == 0
            assert proof["n_reachable_commits"] >= 1
        interval = out["intervals"][0]
        assert interval["n_added_paths"] == 1
        assert interval["n_removed_paths"] == 0
        assert interval["n_modified_paths"] == 1
        assert interval["n_unchanged_paths"] == 1
        assert interval["lean_toolchain_changed"] is True
        assert isinstance(interval["lake_manifest_changed"], bool)
        assert interval["net_source_bytes"] == (
            out["snapshots"][1]["source"]["source_bytes"]
            - out["snapshots"][0]["source"]["source_bytes"])


def test_plan_and_repository_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        repo, plan_path, plan, *_ = _fixture(td)
        bad = dict(plan)
        bad["cutoffs_utc"] = [plan["cutoffs_utc"][0]] * 2
        try:
            _validate_plan(bad)
            assert False, "duplicate cutoffs accepted"
        except V2BError as err:
            assert "strictly increasing" in str(err)

        bad = dict(plan, expected_head="0" * 40)
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        try:
            build_inventory(plan_path, repo)
            assert False, "wrong expected HEAD accepted"
        except V2BError as err:
            assert "repository HEAD" in str(err)

        bad = dict(plan)
        bad.pop("limitations")
        try:
            _validate_plan(bad)
            assert False, "plan without limitations accepted"
        except V2BError as err:
            assert "limitations" in str(err)


def test_backdated_descendant_cannot_import_future_history():
    """c3's own date is before the first cutoff, but its parent c2 is
    later.  The unsafe --before-only rule selected c3; the frozen rule must
    retreat to c1 until the whole reachable history is calendar-safe."""
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Inventory Test")
        _git(repo, "config", "user.email", "inventory@example.invalid")
        _git(repo, "config", "commit.gpgsign", "false")
        _write(os.path.join(repo, "A.lean"), "def a := 1\n")
        first = _commit(repo, "first", "2021-01-01T00:00:00Z")
        _write(os.path.join(repo, "A.lean"), "def a := 2\n")
        _commit(repo, "future parent", "2021-06-01T00:00:00Z")
        _write(os.path.join(repo, "A.lean"), "def a := 3\n")
        backdated = _commit(repo, "backdated child",
                            "2021-03-01T00:00:00Z")
        plan = dict(
            schema=PLAN_SCHEMA, state=STATE, repo="nonmonotonic",
            language="lean", expected_head=backdated,
            selection=SELECTION, source_suffixes=[".lean"],
            limitations=["feasibility only"],
            cutoffs_utc=["2021-04-01T00:00:00Z",
                         "2021-07-01T00:00:00Z"])
        path = os.path.join(td, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        out = build_inventory(path, repo)
        assert [row["commit"] for row in out["snapshots"]] == \
            [first, backdated]
        assert out["snapshots"][0]["source"]["source_bytes"] == \
            len("def a := 1\n".encode())


def test_optional_blob_absence_is_distinct_from_git_failure():
    with tempfile.TemporaryDirectory() as td:
        repo, _plan_path, _plan, _first, second, *_ = _fixture(td)
        assert _blob_at(repo, second, "does-not-exist") is None
        try:
            _blob_at(repo, "0" * 40, "lean-toolchain")
            assert False, "bad Git revision was reported as absent blob"
        except V2BError as err:
            assert "ls-tree" in str(err)


def test_toolchain_tree_is_not_accepted_as_a_blob():
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Inventory Test")
        _git(repo, "config", "user.email", "inventory@example.invalid")
        _git(repo, "config", "commit.gpgsign", "false")
        _write(os.path.join(repo, "A.lean"), "def a := 1\n")
        _write(os.path.join(repo, "lean-toolchain", "inner"), "bad\n")
        head = _commit(repo, "tree toolchain", "2021-01-01T00:00:00Z")
        try:
            _blob_at(repo, head, "lean-toolchain")
            assert False, "tree accepted as a toolchain blob"
        except V2BError as err:
            assert "unexpected Git tree object" in str(err)


def test_symlink_source_is_excluded_and_root_bucket_is_explicit():
    with tempfile.TemporaryDirectory() as td:
        repo, _plan_path, _plan, *_ = _fixture(td)
        os.symlink("A.lean", os.path.join(repo, "Link.lean"))
        head = _commit(repo, "symlink", "2023-01-01T00:00:00Z")
        listing = _source_listing(repo, head, (".lean",))
        assert "Link.lean" not in listing
        assert "A.lean" in listing


def test_quiet_calendar_interval_is_recorded_not_rejected():
    with tempfile.TemporaryDirectory() as td:
        repo, plan_path, plan, *_ = _fixture(td)
        plan["cutoffs_utc"] = ["2022-07-01T00:00:00Z",
                               "2022-08-01T00:00:00Z"]
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        out = build_inventory(plan_path, repo)
        assert out["n_snapshots"] == 2 and out["n_unique_snapshots"] == 1
        interval = out["intervals"][0]
        assert interval["from_commit"] == interval["to_commit"]
        assert interval["n_first_parent_commits"] == 0
        assert interval["net_source_bytes"] == 0


def test_partial_clone_marker_and_tracked_dirt_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        repo, plan_path, _plan, *_ = _fixture(td)
        _git(repo, "config", "extensions.partialClone", "origin")
        try:
            build_inventory(plan_path, repo)
            assert False, "partial-clone marker accepted"
        except V2BError as err:
            assert "partial/promisor" in str(err)
        _git(repo, "config", "--unset", "extensions.partialClone")
        _write(os.path.join(repo, "A.lean"), "def a := dirty\n")
        try:
            build_inventory(plan_path, repo)
            assert False, "tracked corpus drift accepted"
        except V2BError as err:
            assert "tracked-file drift" in str(err)


def test_non_source_plan_fails_instead_of_emitting_empty_snapshot():
    with tempfile.TemporaryDirectory() as td:
        repo, plan_path, plan, *_ = _fixture(td)
        plan["source_suffixes"] = [".does-not-exist"]
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        try:
            build_inventory(plan_path, repo)
            assert False, "empty source inventory accepted"
        except V2BError as err:
            assert "no matching source files" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("LONGITUDINAL INVENTORY TESTS PASS")
