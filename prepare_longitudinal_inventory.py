#!/usr/bin/env python3
"""Model-free feasibility inventory for a future repo-growth arm.

This is deliberately NOT an outcome analyzer and never reads model scores.
Given a committed plan of calendar cutoffs and a full-history repository, it
selects the latest FIRST-PARENT snapshot whose entire reachable history has
committer dates at or before each cutoff, then records source-tree mass,
toolchain drift, and path-level interval churn directly from Git objects.  The
resulting artifact answers only whether the historical SNAPSHOT LADDER is
constructible.  It does not test availability or backportability of genuinely
future evaluation units and cannot estimate a scaling exponent.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import os
import re
import subprocess
import sys

from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, canonical_json_bytes,
                        sha256_bytes, sha256_file, write_new_json)


PLAN_SCHEMA = "longitudinal_inventory_plan_v2"
INVENTORY_SCHEMA = "longitudinal_snapshot_inventory_v2"
SELECTION = ("latest-first-parent-commit-with-no-reachable-committer-"
             "after-cutoff")
STATE = "snapshot-ladder-feasibility-only-no-model-scores"
_HEX40 = re.compile(r"[0-9a-f]{40}")
_CUTOFF = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _run(repo_root, *args, text=True, check=True, env=None):
    proc = subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=text, env=env)
    if check and proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode(
            "utf-8", errors="replace")
        raise V2BError(f"git {' '.join(args)} failed: {stderr[:300]}")
    return proc


def _parse_cutoff(value):
    if not isinstance(value, str) or not _CUTOFF.fullmatch(value):
        raise V2BError(f"cutoff is not strict UTC seconds: {value!r}")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as err:
        raise V2BError(f"invalid cutoff {value!r}: {err}") from err


def _validate_plan(plan):
    if plan.get("state") != STATE or plan.get("selection") != SELECTION:
        raise V2BError("longitudinal plan state/selection is not frozen")
    repo = plan.get("repo")
    language = plan.get("language")
    head = plan.get("expected_head")
    suffixes = plan.get("source_suffixes")
    cutoffs = plan.get("cutoffs_utc")
    limitations = plan.get("limitations")
    if not isinstance(repo, str) or not repo \
            or not isinstance(language, str) or not language \
            or not isinstance(head, str) or not _HEX40.fullmatch(head):
        raise V2BError("longitudinal plan repo/language/head is malformed")
    if not isinstance(suffixes, list) or not suffixes \
            or any(not isinstance(s, str) or not s.startswith(".")
                   or "/" in s or "\x00" in s for s in suffixes) \
            or len(set(suffixes)) != len(suffixes):
        raise V2BError("source_suffixes must be unique filename suffixes")
    if not isinstance(cutoffs, list) or len(cutoffs) < 2:
        raise V2BError("at least two calendar cutoffs are required")
    if not isinstance(limitations, list) or not limitations \
            or any(not isinstance(value, str) or not value.strip()
                   for value in limitations):
        raise V2BError("longitudinal plan needs non-empty limitations")
    parsed = [_parse_cutoff(value) for value in cutoffs]
    if parsed != sorted(set(parsed)):
        raise V2BError("calendar cutoffs must be strictly increasing")
    return (repo, language, head, tuple(suffixes), tuple(cutoffs),
            tuple(limitations))


def _blob_at(repo_root, commit, path):
    # `git show commit:path` returns the same nonzero class for an absent
    # path and many repository/read failures.  First query the exact tree
    # entry: absence is the one legitimate null case; every other failure
    # remains fatal.
    raw = _run(repo_root, "ls-tree", "-z", commit, "--", path,
               text=False).stdout
    if not raw:
        return None
    entries = [entry for entry in raw.split(b"\x00") if entry]
    if len(entries) != 1:
        raise V2BError(f"ambiguous Git tree entry for {path!r} at {commit}")
    try:
        metadata, path_bytes = entries[0].split(b"\t", 1)
        _mode, kind, oid = metadata.split()
        observed_path = path_bytes.decode("utf-8")
        digest = oid.decode("ascii")
    except (ValueError, UnicodeDecodeError) as err:
        raise V2BError(f"malformed Git tree entry for {path!r}") from err
    if observed_path != path or kind != b"blob" or not _HEX40.fullmatch(
            digest):
        raise V2BError(f"unexpected Git tree object for {path!r} at "
                       f"{commit}")
    return _run(repo_root, "cat-file", "blob", digest, text=False).stdout


def _commit_dates(repo_root, head):
    """Exact reachable commit -> author/committer times, without Git's date
    limiting heuristics.  Commit dates are not monotone along a DAG."""
    raw = _run(repo_root, "log", "--format=%H%x00%aI%x00%cI", head).stdout
    dates = {}
    for index, line in enumerate(raw.splitlines()):
        try:
            commit, author_stamp, committer_stamp = line.split("\x00", 2)
            author = datetime.fromisoformat(author_stamp)
            committer = datetime.fromisoformat(committer_stamp)
        except ValueError as err:
            raise V2BError(f"malformed reachable commit date row[{index}]") \
                from err
        if not _HEX40.fullmatch(commit) or author.tzinfo is None \
                or committer.tzinfo is None \
                or commit in dates:
            raise V2BError(f"malformed/duplicate reachable commit row: "
                           f"{line!r}")
        dates[commit] = dict(author=author, committer=committer)
    if head not in dates:
        raise V2BError("reachable commit-date inventory omitted HEAD")
    return dates


def _safe_snapshot(repo_root, first_parent_chain, dates, cutoff):
    """Latest mainline commit whose entire reachable history is no later
    than cutoff by COMMITTER date.

    Merely checking the candidate's own date is unsafe: a backdated child
    can contain a later-dated parent, and a backdated merge can contain a
    later-dated side branch.  Enumerating reachability without --before/
    --since avoids Git's date-pruning heuristics.
    """
    boundary = _parse_cutoff(cutoff)
    for commit in first_parent_chain:
        if dates.get(commit) is None:
            raise V2BError(f"first-parent commit lacks a date: {commit}")
        if dates[commit]["committer"] > boundary:
            continue
        reachable = _run(repo_root, "rev-list", commit).stdout.splitlines()
        if not reachable or reachable[0] != commit \
                or any(row not in dates for row in reachable):
            raise V2BError(f"malformed reachability set for {commit}")
        later = [row for row in reachable
                 if dates[row]["committer"] > boundary]
        if later:
            continue
        max_commit = max(reachable,
                         key=lambda row: dates[row]["committer"])
        max_author = max(reachable, key=lambda row: dates[row]["author"])
        return commit, dict(
            date_semantics="committer-date-at-or-before-inclusive",
            n_reachable_commits=len(reachable),
            n_reachable_commits_after_cutoff=0,
            max_reachable_committer_date=dates[max_commit][
                "committer"].isoformat(),
            max_reachable_committer_commit=max_commit,
            max_reachable_author_date=dates[max_author]["author"].isoformat(),
            max_reachable_author_commit=max_author,
            author_date_is_diagnostic_not_gate=True)
    raise V2BError(f"no calendar-safe first-parent snapshot at {cutoff}")


def _source_listing(repo_root, commit, suffixes):
    """Return path -> (blob SHA, byte size) from one Git tree.

    ``ls-tree -z`` is used so whitespace in paths is never parsed as a
    delimiter. Non-UTF-8 paths fail closed because the JSON evidence format
    cannot represent them canonically.
    """
    raw = _run(repo_root, "ls-tree", "-r", "-z", "-l", commit,
               text=False).stdout
    listing = {}
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        try:
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, oid, size = metadata.split()
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:
            raise V2BError("cannot parse canonical Git tree entry") from err
        if kind != b"blob" or mode not in (b"100644", b"100755") \
                or not any(path.endswith(s) for s in suffixes):
            continue
        try:
            n_bytes = int(size)
        except ValueError as err:
            raise V2BError(f"non-numeric blob size for {path}") from err
        digest = oid.decode("ascii")
        if not _HEX40.fullmatch(digest) or n_bytes < 0 or path in listing:
            raise V2BError(f"malformed/duplicate source tree row: {path}")
        listing[path] = (digest, n_bytes)
    if not listing:
        raise V2BError(f"snapshot {commit} has no matching source files")
    return listing


def _tree_summary(listing):
    rows = [[path, oid, size]
            for path, (oid, size) in sorted(listing.items())]
    by_top = defaultdict(lambda: [0, 0])
    for path, (_oid, size) in listing.items():
        top = path.split("/", 1)[0] if "/" in path else "<root>"
        by_top[top][0] += 1
        by_top[top][1] += size
    return dict(
        n_source_files=len(rows),
        source_bytes=sum(row[2] for row in rows),
        source_listing_sha256=hashlib.sha256(
            canonical_json_bytes(rows)).hexdigest(),
        top_level={key: dict(n_files=value[0], n_bytes=value[1])
                   for key, value in sorted(by_top.items())})


def _commit_row(repo_root, commit, cutoff, suffixes, temporal_proof):
    fmt = "%H%x00%T%x00%aI%x00%cI"
    fields = _run(repo_root, "show", "-s", f"--format={fmt}",
                  commit).stdout.rstrip("\n").split("\x00")
    if len(fields) != 4 or fields[0] != commit \
            or not _HEX40.fullmatch(fields[1]):
        raise V2BError(f"malformed commit metadata for {commit}")
    try:
        committed_at = datetime.fromisoformat(fields[3])
    except ValueError as err:
        raise V2BError(f"malformed committer date for {commit}") from err
    if committed_at > _parse_cutoff(cutoff):
        raise V2BError(f"snapshot {commit} is later than cutoff {cutoff}")
    listing = _source_listing(repo_root, commit, suffixes)
    toolchain = _blob_at(repo_root, commit, "lean-toolchain")
    manifest = _blob_at(repo_root, commit, "lake-manifest.json")
    try:
        toolchain_value = None if toolchain is None else \
            toolchain.decode("utf-8").strip()
    except UnicodeDecodeError as err:
        raise V2BError(f"lean-toolchain is not UTF-8 at {commit}") from err
    row = dict(cutoff_utc=cutoff, commit=commit, tree=fields[1],
               author_date=fields[2], committer_date=fields[3],
               temporal_proof=temporal_proof,
               source=_tree_summary(listing),
               lean_toolchain=(None if toolchain is None else dict(
                   sha256=sha256_bytes(toolchain),
                   value=toolchain_value)),
               lake_manifest=(None if manifest is None else dict(
                   sha256=sha256_bytes(manifest), n_bytes=len(manifest))))
    return row, listing


def _interval_row(repo_root, previous, current, left, right,
                  previous_snapshot, current_snapshot):
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    retained = sorted(set(previous) & set(current))
    modified = [path for path in retained
                if previous[path][0] != current[path][0]]
    unchanged = [path for path in retained
                 if previous[path][0] == current[path][0]]
    commits = _run(repo_root, "rev-list", "--first-parent", "--count",
                   f"{left}..{right}").stdout.strip()
    if not commits.isdigit():
        raise V2BError("first-parent interval commit count is malformed")
    return dict(
        from_commit=left, to_commit=right,
        n_first_parent_commits=int(commits),
        n_added_paths=len(added), n_removed_paths=len(removed),
        n_modified_paths=len(modified), n_unchanged_paths=len(unchanged),
        added_path_bytes=sum(current[p][1] for p in added),
        removed_path_bytes=sum(previous[p][1] for p in removed),
        modified_old_bytes=sum(previous[p][1] for p in modified),
        modified_new_bytes=sum(current[p][1] for p in modified),
        lean_toolchain_changed=(previous_snapshot.get("lean_toolchain") !=
                                current_snapshot.get("lean_toolchain")),
        lake_manifest_changed=(previous_snapshot.get("lake_manifest") !=
                               current_snapshot.get("lake_manifest")),
        net_source_bytes=(sum(size for _oid, size in current.values())
                          - sum(size for _oid, size in previous.values())))


def build_inventory(plan_path, repo_root):
    plan_binding, plan = artifact_binding(plan_path, PLAN_SCHEMA)
    (repo, language, expected_head, suffixes, cutoffs,
     limitations) = _validate_plan(plan)
    repo_root = os.path.realpath(repo_root)
    if not os.path.isdir(repo_root):
        raise V2BError(f"repository root does not exist: {repo_root}")
    actual_head = _run(repo_root, "rev-parse", "HEAD").stdout.strip()
    if actual_head != expected_head:
        raise V2BError(f"repository HEAD {actual_head} != {expected_head}")
    shallow = _run(repo_root, "rev-parse", "--is-shallow-repository") \
        .stdout.strip()
    if shallow != "false":
        raise V2BError("longitudinal inventory requires a full-history clone")
    promisor = _run(repo_root, "config", "--get-regexp",
                    r"^remote\..*\.promisor$", check=False).stdout.strip()
    partial = _run(repo_root, "config", "--get",
                   "extensions.partialClone", check=False).stdout.strip()
    if promisor or partial:
        raise V2BError("longitudinal inventory refuses a partial/promisor "
                       "clone")
    tracked_dirt = _run(repo_root, "status", "--porcelain",
                        "--untracked-files=no").stdout.strip()
    if tracked_dirt:
        raise V2BError("longitudinal repository has tracked-file drift")

    first_parent_chain = _run(repo_root, "rev-list", "--first-parent",
                              expected_head).stdout.splitlines()
    if not first_parent_chain or first_parent_chain[0] != expected_head \
            or any(not _HEX40.fullmatch(row)
                   for row in first_parent_chain):
        raise V2BError("malformed first-parent chain")
    dates = _commit_dates(repo_root, expected_head)

    snapshots = []
    listings = []
    for cutoff in cutoffs:
        commit, proof = _safe_snapshot(repo_root, first_parent_chain, dates,
                                       cutoff)
        row, listing = _commit_row(repo_root, commit, cutoff, suffixes,
                                   proof)
        snapshots.append(row)
        listings.append(listing)
    commits = [row["commit"] for row in snapshots]
    for left, right in zip(commits, commits[1:]):
        if _run(repo_root, "merge-base", "--is-ancestor", left, right,
                check=False).returncode != 0:
            raise V2BError("selected snapshots are not a nested mainline")
    intervals = [_interval_row(repo_root, old, new, left, right,
                               old_snapshot, new_snapshot)
                 for old, new, left, right, old_snapshot, new_snapshot in zip(
                     listings, listings[1:], commits, commits[1:],
                     snapshots, snapshots[1:])]
    git_version = _run(repo_root, "--version").stdout.strip()
    return dict(
        schema=INVENTORY_SCHEMA, state=STATE,
        scope="historical-snapshot-ladder-only",
        future_evaluation_unit_feasibility="not-assessed",
        repo=repo,
        language=language, expected_head=expected_head,
        selection=SELECTION, source_suffixes=list(suffixes),
        limitations=list(limitations),
        bindings=dict(plan=plan_binding), git_version=git_version,
        n_snapshots=len(snapshots),
        n_unique_snapshots=len(set(commits)), n_intervals=len(intervals),
        snapshots=snapshots, intervals=intervals)


def prepare(plan_path, repo_root):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    require_committed(plan_path)
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "corpora_lock.json")
    require_committed(lock_path)
    try:
        import json
        lock = json.load(open(lock_path, encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise V2BError(f"cannot load corpora lock: {err}") from err
    artifact = build_inventory(plan_path, repo_root)
    locked = lock.get("repos", {}).get(artifact["repo"])
    if not isinstance(locked, dict) \
            or locked.get("sha") != artifact["expected_head"]:
        raise V2BError("longitudinal plan head differs from corpora_lock.json")
    artifact["bindings"]["corpora_lock"] = dict(
        path=lock_path, sha256=sha256_file(lock_path))
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during inventory")
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="prepare_longitudinal_inventory.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        artifact = prepare(args.plan, args.repo_root)
        digest = write_new_json(args.out, artifact)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[longitudinal-inventory] {artifact['repo']}: "
          f"{artifact['n_snapshots']} snapshots -> {args.out} "
          f"({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
