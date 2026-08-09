#!/usr/bin/env python3
"""Prospective NLL-only exploratory reveal, separate from formal V2-b.

The reveal is deliberately two-phase.  Before the private salt is read, the
exact five masked and N-governance HEAD blobs are copied into immutable local
snapshots, their corpus identities are joined, and every governance object is
recomputed.  Only that prevalidated object can reach the salt-reading phase.
The post-salt phase then reuses the formal unblinder's full determinism proof.

The replay policy is the exact source tree used for scoring, masking, and blind
N governance.  Only the four newly adopted reveal implementation/test files
may differ; every older tracked source blob must retain the pre-score tree
hash.  Formal V2-b unblinding remains behavior-gated and disabled.
"""
import argparse
import copy
import hashlib
import os
import subprocess
import sys
import tempfile

from finalize_v2b_a6 import EXPECTED
from finalize_v2b_unblinding import verify_repo_unblinding
from prepare_v2b_masked_deltas import SALT_ALGORITHM, _read_salt
from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (MASKED_DELTAS_SCHEMA, N_GOVERNANCE_SCHEMA,
                        SALT_COMMITMENT_SCHEMA, V2BError,
                        artifact_binding, load_json, sha256_bytes, sha256_file,
                        write_new_json)
from v2b_n_governance import analyze as governance_analyze


NLL_EXPLORATORY_REVEAL_SCHEMA = "v2b_nll_exploratory_reveal_v1"
IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_exploratory_reveal_implementation_freeze_v1"
AMENDMENT_PATH = \
    "results_v2/v2b/NLL_ONLY_EXPLORATORY_REVEAL_AMENDMENT.md"
AMENDMENT_SHA256 = \
    "5b9053e00f081ff0e614375bb6eae2c4a93a8330cd2d67257889cf080b6982e2"
AMENDMENT_ADOPTION_COMMIT = \
    "eccd7638f39c34739273a70a82243546968dea14"
REPLAY_SOURCE_TREE_SHA256 = \
    "85dd90e8529ba9380669ffff6a3d9f396e5c65f5f2ef4d08128c920ecdb498c0"
REVEAL_SOURCE_EXCLUSIONS = (
    "finalize_v2b_nll_exploratory_reveal.py",
    "slurm/v2b_nll_exploratory_reveal.sbatch",
    "tests/test_finalize_v2b_nll_exploratory_reveal.py",
    "tests/test_v2b_nll_exploratory_reveal_job.py",
)
IMPLEMENTATION_FREEZE_PATH = (
    "results_v2/v2b/"
    "NLL_ONLY_EXPLORATORY_REVEAL_IMPLEMENTATION_FREEZE.json")
ENTRY_KEYS = frozenset(("masked_path", "governance_path", "complete_path",
                        "manifest_path", "sample_path", "candidates_path"))


def _replay_source_tree_hash():
    """Old source projection, excluding only this prospective reveal code."""
    pathspecs = [".", ":(exclude)results_v2"] + [
        f":(exclude){path}" for path in REVEAL_SOURCE_EXCLUSIONS]
    proc = subprocess.run(
        ["git", "-C", BASE, "ls-files", "-s", "--", *pathspecs],
        capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise V2BError("cannot compute the frozen NLL replay source tree")
    return hashlib.sha256(proc.stdout.encode()).hexdigest()


def _require_replay_source_tree():
    digest = _replay_source_tree_hash()
    if digest != REPLAY_SOURCE_TREE_SHA256:
        raise V2BError("scoring/masking/governance replay source differs "
                       "from the prospective pre-score tree")
    return digest


def _amendment_binding(path=AMENDMENT_PATH,
                       require_committed_fn=require_committed):
    """Exact amendment bytes and its single pre-score adoption commit."""
    absolute = os.path.join(BASE, path)
    require_committed_fn(absolute)
    digest = sha256_file(absolute)
    if digest != AMENDMENT_SHA256:
        raise V2BError("NLL-only amendment bytes differ from the prospective "
                       "pre-score adoption")
    proc = subprocess.run(
        ["git", "-C", BASE, "log", "--format=%H", "--", path],
        capture_output=True, text=True, errors="replace")
    history = proc.stdout.splitlines() if proc.returncode == 0 else []
    if history != [AMENDMENT_ADOPTION_COMMIT]:
        raise V2BError("NLL-only amendment history differs from the single "
                       "prospective pre-score adoption")
    return dict(path=absolute, sha256=digest,
                adoption_commit=AMENDMENT_ADOPTION_COMMIT)


def _implementation_freeze_binding(
        path=IMPLEMENTATION_FREEZE_PATH,
        require_committed_fn=require_committed):
    """One-touch public freeze of the newly added reveal implementation."""
    absolute = os.path.join(BASE, path)
    committed = require_committed_fn(absolute)
    artifact, digest = load_json(absolute, IMPLEMENTATION_FREEZE_SCHEMA)
    expected_keys = {"schema", "state", "implementation_commit",
                     "source_tree_sha256", "replay_source_tree_sha256",
                     "amendment", "files"}
    amendment = artifact.get("amendment")
    files = artifact.get("files")
    implementation_commit = artifact.get("implementation_commit")
    if set(artifact) != expected_keys \
            or artifact.get("state") != "frozen-before-score-inspection" \
            or not isinstance(implementation_commit, str) \
            or len(implementation_commit) != 40 \
            or any(ch not in "0123456789abcdef"
                   for ch in implementation_commit) \
            or artifact.get("source_tree_sha256") != source_tree_hash() \
            or artifact.get("replay_source_tree_sha256") != \
            REPLAY_SOURCE_TREE_SHA256 \
            or amendment != dict(
                sha256=AMENDMENT_SHA256,
                adoption_commit=AMENDMENT_ADOPTION_COMMIT) \
            or not isinstance(files, dict) \
            or set(files) != set(REVEAL_SOURCE_EXCLUSIONS):
        raise V2BError("NLL-only reveal implementation freeze is malformed "
                       "or does not bind the current source tree")
    for source_path, expected_sha in files.items():
        if not isinstance(expected_sha, str) or len(expected_sha) != 64 \
                or sha256_file(os.path.join(BASE, source_path)) != \
                expected_sha:
            raise V2BError(f"frozen reveal implementation drift: "
                           f"{source_path}")
        proc = subprocess.run(
            ["git", "-C", BASE, "log", "-1", "--format=%H", "--",
             source_path], capture_output=True, text=True,
            errors="replace")
        if proc.returncode != 0 \
                or proc.stdout.strip() != implementation_commit:
            raise V2BError(f"reveal implementation commit drift: "
                           f"{source_path}")
    proc = subprocess.run(
        ["git", "-C", BASE, "log", "--format=%H", "--", path],
        capture_output=True, text=True, errors="replace")
    history = proc.stdout.splitlines() if proc.returncode == 0 else []
    if len(history) != 1:
        raise V2BError("implementation freeze must have exactly one touching "
                       "commit")
    ancestry = subprocess.run(
        ["git", "-C", BASE, "merge-base", "--is-ancestor",
         implementation_commit, history[0]], capture_output=True)
    if ancestry.returncode != 0 or implementation_commit == history[0]:
        raise V2BError("implementation freeze does not postdate its bound "
                       "implementation commit")
    if not isinstance(committed, dict) \
            or committed.get("sha256") != digest:
        raise V2BError("implementation freeze HEAD binding drift")
    return dict(path=absolute, sha256=digest,
                schema=IMPLEMENTATION_FREEZE_SCHEMA,
                adoption_commit=history[0],
                implementation_commit=implementation_commit,
                source_tree_sha256=artifact["source_tree_sha256"])


def _write_snapshot(path, blob):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)


def _snapshot_bound_file(path, expected_sha256, snapshot_path):
    """One-read copy of hash-bound untracked evidence."""
    try:
        blob = open(path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot snapshot hash-bound input {path}: {err}") \
            from err
    if sha256_bytes(blob) != expected_sha256:
        raise V2BError(f"hash-bound input differs before reveal: {path}")
    _write_snapshot(snapshot_path, blob)
    return snapshot_path


def _target_source_path(recorded_path, complete_path):
    """Mirror the frozen producer's recorded-path then sibling fallback."""
    if os.path.exists(recorded_path):
        return recorded_path
    sibling = os.path.join(os.path.dirname(os.path.abspath(complete_path)),
                           os.path.basename(recorded_path))
    if os.path.exists(sibling):
        return sibling
    raise V2BError(f"paired target artifact missing: {recorded_path}")


def _snapshot_head_file(path, commit, snapshot_dir, label,
                        require_committed_fn=require_committed):
    """Copy exactly commit:path, never mutable post-check working bytes."""
    committed = require_committed_fn(path)
    if not isinstance(committed, dict) \
            or not isinstance(committed.get("path"), str) \
            or not isinstance(committed.get("sha256"), str):
        raise V2BError(f"committed binding is malformed: {path}")
    root = os.path.realpath(BASE)
    real = os.path.realpath(committed["path"])
    try:
        if os.path.commonpath((root, real)) != root:
            raise V2BError(f"committed reveal input is outside repo: {path}")
    except ValueError as err:
        raise V2BError(f"committed reveal input path mismatch: {err}") \
            from err
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    proc = subprocess.run(
        ["git", "-C", root, "show", f"{commit}:{rel}"],
        capture_output=True)
    if proc.returncode != 0 \
            or sha256_bytes(proc.stdout) != committed["sha256"]:
        raise V2BError(f"HEAD snapshot differs from committed binding: {rel}")
    snapshot = os.path.join(snapshot_dir, f"{label}.json")
    _write_snapshot(snapshot, proc.stdout)
    return dict(path=os.path.abspath(path), sha256=committed["sha256"]), \
        snapshot


def _snapshot_committed_chain(entries, commitment_path, commit,
                              snapshot_dir,
                              require_committed_fn=require_committed,
                              snapshot_head_file_fn=_snapshot_head_file):
    """Stage an immutable, path-normalized chain without reading the salt.

    Masked/governance/commitment/sample/manifest bytes come from fixed HEAD.
    Candidates, completion, and every target artifact are copied once and
    checked against the hashes already sealed in the committed masked chain.
    The staged complete file changes only target paths; staged masked and
    governance objects change only their corresponding artifact bindings.
    """
    if not isinstance(entries, list) or len(entries) != len(EXPECTED):
        raise V2BError("exploratory NLL reveal requires exactly five entries")
    if any(not isinstance(entry, dict) or set(entry) != ENTRY_KEYS
           for entry in entries):
        raise V2BError("exploratory NLL reveal entry schema is malformed")
    blinded_paths = [entry[key] for entry in entries
                     for key in ("masked_path", "governance_path")]
    if len(set(blinded_paths)) != 2 * len(EXPECTED):
        raise V2BError("masked/governance paths must be distinct")

    commitment_binding, commitment_snapshot = snapshot_head_file_fn(
        commitment_path, commit, snapshot_dir, "commitment",
        require_committed_fn=require_committed_fn)
    staged_commitment_binding, commitment_value = artifact_binding(
        commitment_snapshot, SALT_COMMITMENT_SCHEMA)
    staged_commitment_binding["salt_sha256"] = \
        commitment_value.get("salt_sha256")
    frozen = []
    for index, entry in enumerate(entries):
        entry_dir = os.path.join(snapshot_dir, f"repo-{index:02d}")
        os.makedirs(entry_dir, mode=0o700)
        masked_binding, masked_head_snapshot = snapshot_head_file_fn(
            entry["masked_path"], commit, snapshot_dir,
            f"{index:02d}-masked-head",
            require_committed_fn=require_committed_fn)
        governance_binding, governance_head_snapshot = snapshot_head_file_fn(
            entry["governance_path"], commit, snapshot_dir,
            f"{index:02d}-governance-head",
            require_committed_fn=require_committed_fn)
        original_masked, _ = load_json(masked_head_snapshot,
                                       MASKED_DELTAS_SCHEMA)
        original_governance, _ = load_json(governance_head_snapshot,
                                           N_GOVERNANCE_SCHEMA)
        declared = original_masked.get("bindings")
        if not isinstance(declared, dict):
            raise V2BError("committed masked artifact lacks bindings")

        sample_head, sample_snapshot = snapshot_head_file_fn(
            entry["sample_path"], commit, entry_dir, "sample",
            require_committed_fn=require_committed_fn)
        manifest_head, manifest_snapshot = snapshot_head_file_fn(
            entry["manifest_path"], commit, entry_dir, "manifest",
            require_committed_fn=require_committed_fn)
        for name, head, key in (
                ("sample", sample_head, "sample"),
                ("manifest", manifest_head, "assembly")):
            expected = declared.get(key)
            if not isinstance(expected, dict) \
                    or head["sha256"] != expected.get("sha256"):
                raise V2BError(f"committed {name} differs from masked "
                               "binding")

        candidates_declared = declared.get("candidates")
        completion_declared = declared.get("completion")
        if not isinstance(candidates_declared, dict) \
                or not isinstance(completion_declared, dict):
            raise V2BError("masked artifact lacks candidate/completion "
                           "bindings")
        candidates_snapshot = _snapshot_bound_file(
            entry["candidates_path"], candidates_declared.get("sha256"),
            os.path.join(entry_dir, "candidates.json"))
        complete_head_snapshot = _snapshot_bound_file(
            entry["complete_path"], completion_declared.get("sha256"),
            os.path.join(entry_dir, "complete-head.json"))
        complete, _ = load_json(complete_head_snapshot)
        target_rows = complete.get("target_artifacts")
        if not isinstance(target_rows, list) or not target_rows:
            raise V2BError("completion has no target artifacts to snapshot")
        staged_complete = copy.deepcopy(complete)
        for target_index, target_row in enumerate(target_rows):
            if not isinstance(target_row, dict) \
                    or not isinstance(target_row.get("path"), str) \
                    or not isinstance(target_row.get("sha256"), str):
                raise V2BError("completion target binding is malformed")
            target_snapshot = _snapshot_bound_file(
                _target_source_path(target_row["path"],
                                    entry["complete_path"]),
                target_row["sha256"],
                os.path.join(entry_dir, f"target-{target_index:04d}.json"))
            staged_complete["target_artifacts"][target_index]["path"] = \
                target_snapshot
        complete_snapshot = os.path.join(entry_dir, "complete.json")
        write_new_json(complete_snapshot, staged_complete)

        sample_staged, _ = artifact_binding(sample_snapshot)
        manifest_staged, _ = artifact_binding(manifest_snapshot)
        candidates_staged, _ = artifact_binding(candidates_snapshot)
        completion_staged, _ = artifact_binding(complete_snapshot)
        staged_bindings = dict(
            sample=sample_staged, candidates=candidates_staged,
            assembly=manifest_staged, completion=completion_staged,
            run_identity_sha256=declared.get("run_identity_sha256"),
            salt_commitment=staged_commitment_binding)
        staged_masked = copy.deepcopy(original_masked)
        staged_masked["bindings"] = staged_bindings
        masked_snapshot = os.path.join(entry_dir, "masked.json")
        write_new_json(masked_snapshot, staged_masked)
        masked_staged, _ = artifact_binding(masked_snapshot)

        staged_governance = copy.deepcopy(original_governance)
        staged_governance_bindings = copy.deepcopy(
            original_governance.get("bindings"))
        if not isinstance(staged_governance_bindings, dict):
            raise V2BError("committed governance lacks bindings")
        staged_governance_bindings.update(
            masked_deltas=masked_staged, candidates=candidates_staged,
            sample=sample_staged, completion=completion_staged)
        staged_governance["bindings"] = staged_governance_bindings
        governance_snapshot = os.path.join(entry_dir, "governance.json")
        write_new_json(governance_snapshot, staged_governance)

        frozen.append(dict(
            entry, masked_head_snapshot=masked_head_snapshot,
            governance_head_snapshot=governance_head_snapshot,
            masked_snapshot=masked_snapshot,
            governance_snapshot=governance_snapshot,
            sample_snapshot=sample_snapshot,
            manifest_snapshot=manifest_snapshot,
            candidates_snapshot=candidates_snapshot,
            complete_snapshot=complete_snapshot,
            masked_head_binding=masked_binding,
            governance_head_binding=governance_binding,
            staged_commitment_binding=staged_commitment_binding))
    return dict(entries=frozen, commitment_path=commitment_path,
                commitment_snapshot=commitment_snapshot,
                commitment_head_binding=commitment_binding,
                staged_commitment_binding=staged_commitment_binding)


def _normalized_binding(head_binding, schema):
    return dict(path=head_binding["path"], sha256=head_binding["sha256"],
                schema=schema)


def _prevalidate_blind_chain(frozen, analyze_fn=governance_analyze):
    """Prove exact-five HEAD governance and its immutable transformation."""
    commitment_snapshot = frozen["commitment_snapshot"]
    snapshot_binding, commitment = artifact_binding(
        commitment_snapshot, SALT_COMMITMENT_SCHEMA)
    if commitment.get("state") != "committed-pre-score" \
            or commitment.get("algorithm") != SALT_ALGORITHM \
            or not isinstance(commitment.get("salt_sha256"), str) \
            or len(commitment["salt_sha256"]) != 64:
        raise V2BError("public salt commitment is malformed")
    head = frozen["commitment_head_binding"]
    if snapshot_binding["sha256"] != head["sha256"]:
        raise V2BError("public salt commitment snapshot drift")
    commitment_binding = dict(
        _normalized_binding(head, SALT_COMMITMENT_SCHEMA),
        salt_sha256=commitment["salt_sha256"])
    staged_commitment_binding = frozen["staged_commitment_binding"]
    if staged_commitment_binding.get("sha256") != \
            commitment_binding["sha256"] \
            or staged_commitment_binding.get("salt_sha256") != \
            commitment_binding["salt_sha256"]:
        raise V2BError("staged public commitment differs from HEAD")

    rows = {}
    for frozen_entry in frozen["entries"]:
        _, original_masked = artifact_binding(
            frozen_entry["masked_head_snapshot"], MASKED_DELTAS_SCHEMA)
        _, original_governance = artifact_binding(
            frozen_entry["governance_head_snapshot"], N_GOVERNANCE_SCHEMA)
        masked_snapshot = frozen_entry["masked_snapshot"]
        governance_snapshot = frozen_entry["governance_snapshot"]
        masked_snapshot_binding, staged_masked = artifact_binding(
            masked_snapshot, MASKED_DELTAS_SCHEMA)
        _, staged_governance = artifact_binding(
            governance_snapshot, N_GOVERNANCE_SCHEMA)
        masked_binding = _normalized_binding(
            frozen_entry["masked_head_binding"], MASKED_DELTAS_SCHEMA)
        governance_binding = _normalized_binding(
            frozen_entry["governance_head_binding"], N_GOVERNANCE_SCHEMA)
        repo = original_masked.get("repo")
        if not isinstance(repo, str) or repo not in EXPECTED \
                or repo in rows or original_governance.get("repo") != repo \
                or staged_masked.get("repo") != repo \
                or staged_governance.get("repo") != repo:
            raise V2BError(f"duplicate/malformed exploratory corpus {repo!r}")
        masked_generator = original_masked.get("generator")
        governance_generator = original_governance.get("generator")
        if not isinstance(masked_generator, dict) \
                or masked_generator.get("program") != \
                "prepare_v2b_masked_deltas.py" \
                or masked_generator.get("source_tree_hash") != \
                REPLAY_SOURCE_TREE_SHA256:
            raise V2BError(f"masked producer tree drift: {repo}")
        if not isinstance(governance_generator, dict) \
                or governance_generator.get("program") != \
                "v2b_n_governance.py" \
                or governance_generator.get("source_tree_hash") != \
                REPLAY_SOURCE_TREE_SHA256:
            raise V2BError(f"governance producer tree drift: {repo}")
        if original_masked.get("bindings", {}).get("salt_commitment") != \
                commitment_binding:
            raise V2BError(f"masked artifact binds another commitment: {repo}")
        gov_bindings = original_governance.get("bindings")
        if not isinstance(gov_bindings, dict) \
                or gov_bindings.get("masked_deltas") != masked_binding:
            raise V2BError(f"governance does not bind the committed masked "
                           f"HEAD blob: {repo}")

        sample_staged, _ = artifact_binding(frozen_entry["sample_snapshot"])
        manifest_staged, _ = artifact_binding(
            frozen_entry["manifest_snapshot"])
        candidates_staged, _ = artifact_binding(
            frozen_entry["candidates_snapshot"])
        completion_staged, _ = artifact_binding(
            frozen_entry["complete_snapshot"])
        expected_staged_masked = copy.deepcopy(original_masked)
        expected_staged_masked["bindings"] = dict(
            sample=sample_staged, candidates=candidates_staged,
            assembly=manifest_staged, completion=completion_staged,
            run_identity_sha256=original_masked["bindings"].get(
                "run_identity_sha256"),
            salt_commitment=staged_commitment_binding)
        if staged_masked != expected_staged_masked:
            raise V2BError(f"staged masked transformation drift: {repo}")
        expected_staged_governance = copy.deepcopy(original_governance)
        transformed_gov_bindings = copy.deepcopy(gov_bindings)
        transformed_gov_bindings.update(
            masked_deltas=masked_snapshot_binding,
            candidates=candidates_staged, sample=sample_staged,
            completion=completion_staged)
        expected_staged_governance["bindings"] = transformed_gov_bindings
        if staged_governance != expected_staged_governance:
            raise V2BError(f"staged governance transformation drift: {repo}")

        expected_governance = analyze_fn(
            masked_snapshot, frozen_entry["candidates_snapshot"],
            frozen_entry["sample_snapshot"],
            frozen_entry["complete_snapshot"])
        stripped = {key: value for key, value in staged_governance.items()
                    if key != "generator"}
        if expected_governance != stripped:
            raise V2BError(f"blind governance does not recompute before "
                           f"salt reveal: {repo}")
        rows[repo] = dict(frozen_entry, repo=repo,
                          masked_binding=masked_binding,
                          governance_binding=governance_binding,
                          completion_binding=original_masked["bindings"][
                              "completion"])
    if set(rows) != set(EXPECTED):
        raise V2BError("exploratory NLL reveal requires the exact five-corpus "
                       "committed governance set")
    return dict(entries=[rows[repo] for repo in sorted(rows)],
                commitment_binding=commitment_binding,
                commitment_snapshot=commitment_snapshot,
                verification_commitment_binding=staged_commitment_binding)


def _reveal_prevalidated_chain(prevalidated, salt_path,
                               analyze_fn=governance_analyze,
                               verify_fn=verify_repo_unblinding,
                               read_salt_fn=_read_salt):
    """Read salt only after _prevalidate_blind_chain has fully succeeded."""
    salt, snapshot_commitment = read_salt_fn(
        salt_path, prevalidated["commitment_snapshot"])
    commitment_binding = prevalidated["commitment_binding"]
    verification_commitment = \
        prevalidated["verification_commitment_binding"]
    if snapshot_commitment.get("sha256") != \
            verification_commitment["sha256"] \
            or snapshot_commitment.get("salt_sha256") != \
            verification_commitment["salt_sha256"]:
        raise V2BError("revealed salt does not match the prevalidated HEAD "
                       "commitment")
    rows = {}
    for entry in prevalidated["entries"]:
        def staged_analyze(masked_path, candidates_path, sample_path,
                           complete_path):
            return analyze_fn(masked_path, candidates_path, sample_path,
                              complete_path)

        row = verify_fn(
            masked_path=entry["masked_snapshot"],
            governance_path=entry["governance_snapshot"],
            complete_path=entry["complete_snapshot"],
            manifest_path=entry["manifest_snapshot"],
            sample_path=entry["sample_snapshot"],
            candidates_path=entry["candidates_snapshot"],
            salt=salt, commitment_binding=verification_commitment,
            analyze_fn=staged_analyze)
        repo = row.get("repo") if isinstance(row, dict) else None
        if repo != entry["repo"] or repo in rows:
            raise V2BError(f"post-salt corpus reconstruction drift: {repo!r}")
        bindings = row.get("bindings")
        if not isinstance(bindings, dict):
            raise V2BError(f"post-salt binding reconstruction drift: {repo}")
        bindings["masked"] = entry["masked_binding"]
        bindings["governance"] = entry["governance_binding"]
        bindings["completion"] = entry["completion_binding"]
        rows[repo] = row
    if set(rows) != set(EXPECTED):
        raise V2BError("post-salt reconstruction lost a committed corpus")
    return dict(
        schema=NLL_EXPLORATORY_REVEAL_SCHEMA,
        state="revealed-post-nll-governance-exploratory",
        claim_status="exploratory-nll-only-one-checkpoint-pilot",
        formal_v2b_status=(
            "formal-unblinding-artifact-not-produced-joint-pilot-not-completed"),
        nll_blind_status="destroyed-by-this-exploratory-reveal",
        behavioral_status=(
            "not-governed-not-a-co-primary-fresh-confirmatory-sample-required"),
        algorithm=SALT_ALGORITHM,
        salt_commitment=commitment_binding,
        revealed_salt_hex=salt.hex(),
        repos={repo: rows[repo] for repo in sorted(rows)})


def prepare(entries, salt_path, commitment_path,
            analyze_fn=governance_analyze,
            verify_fn=verify_repo_unblinding,
            require_committed_fn=require_committed,
            source_clean_fn=source_clean,
            implementation_freeze_fn=_implementation_freeze_binding,
            snapshot_chain_fn=_snapshot_committed_chain,
            prevalidate_fn=_prevalidate_blind_chain,
            reveal_fn=_reveal_prevalidated_chain):
    """Committed ordering gate plus immutable exact replay."""
    if not source_clean_fn():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    replay_tree = _require_replay_source_tree()
    amendment = _amendment_binding(
        require_committed_fn=require_committed_fn)
    implementation_freeze = implementation_freeze_fn(
        require_committed_fn=require_committed_fn)
    with tempfile.TemporaryDirectory(prefix="v2b-nll-reveal-") as td:
        os.chmod(td, 0o700)
        frozen = snapshot_chain_fn(
            entries, commitment_path, commit_start, td,
            require_committed_fn=require_committed_fn)
        prevalidated = prevalidate_fn(frozen, analyze_fn=analyze_fn)
        artifact = reveal_fn(prevalidated, salt_path,
                             analyze_fn=analyze_fn, verify_fn=verify_fn)
    if not source_clean_fn() or head_commit() != commit_start \
            or source_tree_hash() != tree_start \
            or _require_replay_source_tree() != replay_tree:
        raise V2BError("measurement source drifted during exploratory reveal")
    artifact["prospective_amendment"] = amendment
    artifact["implementation_freeze"] = implementation_freeze
    artifact["replay_source_tree_sha256"] = replay_tree
    artifact["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="finalize_v2b_nll_exploratory_reveal.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    for flag in ("--masked", "--governance", "--complete", "--manifest",
                 "--candidates"):
        ap.add_argument(flag, action="append", required=True,
                        help=f"{flag} artifact; repeat five times in the "
                             "same corpus order")
    ap.add_argument("--sample", required=True)
    ap.add_argument("--salt", required=True)
    ap.add_argument("--salt-commitment", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    groups = (args.masked, args.governance, args.complete, args.manifest,
              args.candidates)
    if any(len(group) != 5 for group in groups):
        raise SystemExit("FATAL: --masked/--governance/--complete/"
                         "--manifest/--candidates must each appear exactly "
                         "five times")
    entries = [dict(masked_path=masked, governance_path=governance,
                    complete_path=complete, manifest_path=manifest,
                    sample_path=args.sample, candidates_path=candidates)
               for masked, governance, complete, manifest, candidates
               in zip(*groups)]
    artifact = prepare(entries, args.salt, args.salt_commitment)
    digest = write_new_json(args.out, artifact)
    # Never print the salt, mappings, means, signs, or target deltas.
    verdicts = "/".join(
        f"{repo}:{row['governance_verdict']}"
        for repo, row in sorted(artifact["repos"].items()))
    print(f"[v2b-nll-exploratory-reveal] {verdicts} -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
