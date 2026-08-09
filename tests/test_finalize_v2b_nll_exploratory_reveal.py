#!/usr/bin/env python3
"""Adversarial synthetic tests for the separate NLL-only reveal."""
import copy
import inspect
import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finalize_v2b_a6 import EXPECTED
from finalize_v2b_nll_exploratory_reveal import (
    AMENDMENT_ADOPTION_COMMIT,
    AMENDMENT_PATH,
    AMENDMENT_SHA256,
    NLL_EXPLORATORY_REVEAL_SCHEMA,
    REPLAY_SOURCE_TREE_SHA256,
    _amendment_binding,
    _prevalidate_blind_chain,
    _replay_source_tree_hash,
    _reveal_prevalidated_chain,
    _snapshot_committed_chain,
    _snapshot_head_file,
    _target_source_path,
    prepare,
)
from finalize_v2b_unblinding import PRODUCTION_UNBLINDING_ENABLED
from finalize_v2b_unblinding import verify_repo_unblinding
from prepare_v2b_masked_deltas import (_read_salt, SALT_ALGORITHM,
                                       _write_salt_pair)
from test_finalize_v2b_unblinding import _unblind_fixture
from v2b_common import (MASKED_DELTAS_SCHEMA, N_GOVERNANCE_SCHEMA,
                        SALT_COMMITMENT_SCHEMA, V2BError,
                        artifact_binding, sha256_file, write_new_json)


def _salt_pair(td):
    salt = os.path.join(td, "salt")
    commitment = os.path.join(td, "commitment.json")
    _write_salt_pair(salt, commitment)
    return salt, commitment


def _blind_fixture(td):
    """Exact-five synthetic HEAD snapshots; no private salt is consumed."""
    salt, commitment_path = _salt_pair(td)
    commitment_binding, commitment = artifact_binding(
        commitment_path, SALT_COMMITMENT_SCHEMA)
    commitment_binding["salt_sha256"] = commitment["salt_sha256"]
    staged_commitment_path = os.path.join(td, "commitment-snapshot.json")
    write_new_json(staged_commitment_path, commitment)
    staged_commitment_binding, _ = artifact_binding(
        staged_commitment_path, SALT_COMMITMENT_SCHEMA)
    staged_commitment_binding["salt_sha256"] = commitment["salt_sha256"]
    frozen_entries = []
    governance_without_generator = {}
    for index, repo in enumerate(sorted(EXPECTED)):
        direct = {}
        staged_direct = {}
        for name in ("sample", "candidates", "manifest", "complete"):
            value = dict(schema=f"synthetic-{name}", repo=repo)
            original_path = os.path.join(td, f"{index:02d}-{name}.json")
            staged_path = os.path.join(
                td, f"{index:02d}-{name}-snapshot.json")
            write_new_json(original_path, value)
            write_new_json(staged_path, value)
            direct[name], _ = artifact_binding(original_path)
            staged_direct[name], _ = artifact_binding(staged_path)
        masked_path = os.path.join(td, f"{index:02d}-{repo}-masked.json")
        masked = dict(
            schema=MASKED_DELTAS_SCHEMA, repo=repo,
            bindings=dict(
                sample=direct["sample"], candidates=direct["candidates"],
                assembly=direct["manifest"], completion=direct["complete"],
                run_identity_sha256="3" * 64,
                salt_commitment=commitment_binding),
            generator=dict(program="prepare_v2b_masked_deltas.py",
                           source_commit="1" * 40,
                           source_tree_hash=REPLAY_SOURCE_TREE_SHA256))
        write_new_json(masked_path, masked)
        masked_binding, _ = artifact_binding(masked_path,
                                             MASKED_DELTAS_SCHEMA)
        masked_snapshot = os.path.join(
            td, f"{index:02d}-{repo}-masked-snapshot.json")
        staged_masked = copy.deepcopy(masked)
        staged_masked["bindings"] = dict(
            sample=staged_direct["sample"],
            candidates=staged_direct["candidates"],
            assembly=staged_direct["manifest"],
            completion=staged_direct["complete"],
            run_identity_sha256="3" * 64,
            salt_commitment=staged_commitment_binding)
        write_new_json(masked_snapshot, staged_masked)
        staged_masked_binding, _ = artifact_binding(
            masked_snapshot, MASKED_DELTAS_SCHEMA)
        governance_path = os.path.join(
            td, f"{index:02d}-{repo}-governance.json")
        governance = dict(
            schema=N_GOVERNANCE_SCHEMA, repo=repo, verdict="feasible",
            repo_n=240, bindings=dict(
                masked_deltas=masked_binding,
                candidates=direct["candidates"], sample=direct["sample"],
                completion=direct["complete"]),
            generator=dict(program="v2b_n_governance.py",
                           source_commit="2" * 40,
                           source_tree_hash=REPLAY_SOURCE_TREE_SHA256))
        write_new_json(governance_path, governance)
        governance_binding, _ = artifact_binding(
            governance_path, N_GOVERNANCE_SCHEMA)
        governance_snapshot = os.path.join(
            td, f"{index:02d}-{repo}-governance-snapshot.json")
        staged_governance = copy.deepcopy(governance)
        staged_governance["bindings"] = dict(
            masked_deltas=staged_masked_binding,
            candidates=staged_direct["candidates"],
            sample=staged_direct["sample"],
            completion=staged_direct["complete"])
        write_new_json(governance_snapshot, staged_governance)
        frozen_entries.append(dict(
            masked_path=masked_path, governance_path=governance_path,
            complete_path=direct["complete"]["path"],
            manifest_path=direct["manifest"]["path"],
            sample_path=direct["sample"]["path"],
            candidates_path=direct["candidates"]["path"],
            masked_head_snapshot=masked_path,
            governance_head_snapshot=governance_path,
            masked_snapshot=masked_snapshot,
            governance_snapshot=governance_snapshot,
            complete_snapshot=staged_direct["complete"]["path"],
            manifest_snapshot=staged_direct["manifest"]["path"],
            sample_snapshot=staged_direct["sample"]["path"],
            candidates_snapshot=staged_direct["candidates"]["path"],
            masked_head_binding=dict(path=masked_binding["path"],
                                     sha256=masked_binding["sha256"]),
            governance_head_binding=dict(
                path=governance_binding["path"],
                sha256=governance_binding["sha256"]),
            staged_commitment_binding=staged_commitment_binding))
        governance_without_generator[repo] = {
            key: value for key, value in staged_governance.items()
            if key != "generator"}
    frozen = dict(
        entries=frozen_entries,
        commitment_path=commitment_path,
        commitment_snapshot=staged_commitment_path,
        commitment_head_binding=dict(path=commitment_binding["path"],
                                     sha256=commitment_binding["sha256"]),
        staged_commitment_binding=staged_commitment_binding)

    def analyze_stub(masked_path, *_args):
        masked = json.load(open(masked_path, encoding="utf-8"))
        return copy.deepcopy(governance_without_generator[masked["repo"]])

    return salt, frozen, analyze_stub


def test_exact_pre_score_amendment_and_replay_tree_are_frozen():
    binding = _amendment_binding()
    assert binding == dict(
        path=os.path.abspath(AMENDMENT_PATH), sha256=AMENDMENT_SHA256,
        adoption_commit=AMENDMENT_ADOPTION_COMMIT)
    assert sha256_file(AMENDMENT_PATH) == AMENDMENT_SHA256
    assert _replay_source_tree_hash() == REPLAY_SOURCE_TREE_SHA256
    with tempfile.TemporaryDirectory() as td:
        _, snapshot = _snapshot_head_file(
            AMENDMENT_PATH, AMENDMENT_ADOPTION_COMMIT, td, "amendment")
        assert sha256_file(snapshot) == AMENDMENT_SHA256
        changed = os.path.join(td, "changed.md")
        open(changed, "w", encoding="utf-8").write("post-score rewrite\n")
        try:
            _amendment_binding(changed, require_committed_fn=lambda _p: {})
            assert False, "changed amendment bytes were accepted"
        except V2BError as err:
            assert "bytes differ" in str(err)


def test_target_staging_mirrors_formal_sibling_fallback():
    with tempfile.TemporaryDirectory() as td:
        complete = os.path.join(td, "complete.json")
        target = os.path.join(td, "target-0000.json")
        open(complete, "w", encoding="utf-8").write("{}\n")
        open(target, "w", encoding="utf-8").write("{}\n")
        stale = os.path.join(td, "old-location", "target-0000.json")
        assert _target_source_path(stale, complete) == target
        try:
            _target_source_path(os.path.join(td, "missing.json"), complete)
            assert False, "missing target path was accepted"
        except V2BError as err:
            assert "missing" in str(err)


def test_blind_exact_five_governance_replays_before_reveal():
    with tempfile.TemporaryDirectory() as td:
        salt_path, frozen, analyze_stub = _blind_fixture(td)
        prevalidated = _prevalidate_blind_chain(
            frozen, analyze_fn=analyze_stub)
        assert [entry["repo"] for entry in prevalidated["entries"]] == \
            sorted(EXPECTED)

        repo_by_snapshot = {
            entry["masked_snapshot"]: entry["repo"]
            for entry in prevalidated["entries"]}

        def verify_stub(*, masked_path, governance_path, candidates_path,
                        sample_path, complete_path, salt,
                        commitment_binding, analyze_fn, **_rest):
            assert len(salt) == 32
            expected = analyze_fn(masked_path, candidates_path, sample_path,
                                  complete_path)
            assert expected["bindings"]["masked_deltas"]["path"] == \
                masked_path
            return dict(repo=repo_by_snapshot[masked_path],
                        governance_verdict="feasible",
                        reconstructed_equal=True,
                        bindings=dict(masked={}, governance={}), mapping={})

        artifact = _reveal_prevalidated_chain(
            prevalidated, salt_path, analyze_fn=analyze_stub,
            verify_fn=verify_stub)
        assert artifact["schema"] == NLL_EXPLORATORY_REVEAL_SCHEMA
        assert artifact["formal_v2b_status"] == \
            "formal-unblinding-artifact-not-produced-joint-pilot-not-completed"
        assert artifact["nll_blind_status"] == \
            "destroyed-by-this-exploratory-reveal"
        assert artifact["behavioral_status"].startswith("not-governed")
        assert set(artifact["repos"]) == set(EXPECTED)
        for repo, row in artifact["repos"].items():
            assert row["bindings"]["completion"]["path"] == next(
                entry["complete_path"] for entry in frozen["entries"]
                if json.load(open(entry["masked_head_snapshot"]))["repo"]
                == repo)
        assert len(bytes.fromhex(artifact["revealed_salt_hex"])) == 32
        assert artifact["schema"] != "v2b_unblinding_v1"


def test_pre_salt_gate_rejects_missing_duplicate_and_fabricated_governance():
    with tempfile.TemporaryDirectory() as td:
        _salt_path, frozen, analyze_stub = _blind_fixture(td)
        missing = dict(frozen, entries=frozen["entries"][:-1])
        try:
            _prevalidate_blind_chain(missing, analyze_fn=analyze_stub)
            assert False, "incomplete blind set reached salt boundary"
        except V2BError as err:
            assert "five-corpus" in str(err)

        duplicate = copy.deepcopy(frozen)
        duplicate["entries"][-1] = copy.deepcopy(duplicate["entries"][0])
        try:
            _prevalidate_blind_chain(duplicate, analyze_fn=analyze_stub)
            assert False, "duplicate blind set reached salt boundary"
        except V2BError as err:
            assert "duplicate" in str(err)

        fabricated = copy.deepcopy(frozen)
        gov_path = fabricated["entries"][0]["governance_snapshot"]
        gov = json.load(open(gov_path, encoding="utf-8"))
        gov["verdict"] = "infeasible"
        fake_path = os.path.join(td, "fabricated-governance.json")
        write_new_json(fake_path, gov)
        fake_binding, _ = artifact_binding(fake_path, N_GOVERNANCE_SCHEMA)
        fabricated["entries"][0]["governance_snapshot"] = fake_path
        fabricated["entries"][0]["governance_head_binding"] = dict(
            path=fake_binding["path"], sha256=fake_binding["sha256"])
        try:
            _prevalidate_blind_chain(fabricated, analyze_fn=analyze_stub)
            assert False, "fabricated verdict reached salt boundary"
        except V2BError as err:
            assert "governance transformation drift" in str(err) \
                or "recompute" in str(err)


def test_full_staging_survives_every_live_non_salt_input_being_destroyed():
    """The formal replay consumes only immutable staged bytes post-salt."""
    with tempfile.TemporaryDirectory() as td:
        fixture, base_entry, _private, _analyze = _unblind_fixture(td)
        entries = []
        for index in range(5):
            masked = os.path.join(td, f"copy-{index}-masked.json")
            governance = os.path.join(td, f"copy-{index}-governance.json")
            open(masked, "wb").write(open(base_entry["masked_path"],
                                             "rb").read())
            open(governance, "wb").write(
                open(base_entry["governance_path"], "rb").read())
            entries.append(dict(base_entry, masked_path=masked,
                                governance_path=governance))

        def snapshot_stub(path, _commit, snapshot_dir, label,
                          require_committed_fn=None):
            del require_committed_fn
            destination = os.path.join(snapshot_dir, f"{label}.json")
            blob = open(path, "rb").read()
            open(destination, "wb").write(blob)
            return dict(path=os.path.abspath(path),
                        sha256=sha256_file(path)), destination

        snapshot_root = os.path.join(td, "snapshots")
        os.mkdir(snapshot_root, 0o700)
        frozen = _snapshot_committed_chain(
            entries, os.path.join(td, "commitment.json"), "0" * 40,
            snapshot_root, require_committed_fn=lambda _path: {},
            snapshot_head_file_fn=snapshot_stub)
        staged = frozen["entries"][0]
        staged_commitment = frozen["staged_commitment_binding"]

        # Destroy every live replay input BEFORE any salt read. A later reopen
        # would now fail; the immutable staged chain must still reconstruct.
        live_paths = set()
        for key in ("masked_path", "governance_path", "complete_path",
                    "manifest_path", "sample_path", "candidates_path"):
            live_paths.add(entries[0][key])
        live_complete = json.load(open(entries[0]["complete_path"]))
        live_paths.update(row["path"]
                          for row in live_complete["target_artifacts"])
        for path in live_paths:
            open(path, "w", encoding="utf-8").write("{}\n")

        def staged_analyze(*_args):
            value = json.load(open(staged["governance_snapshot"]))
            return {key: row for key, row in value.items()
                    if key != "generator"}

        row = verify_repo_unblinding(
            masked_path=staged["masked_snapshot"],
            governance_path=staged["governance_snapshot"],
            complete_path=staged["complete_snapshot"],
            manifest_path=staged["manifest_snapshot"],
            sample_path=staged["sample_snapshot"],
            candidates_path=staged["candidates_snapshot"],
            salt=fixture["salt"], commitment_binding=staged_commitment,
            analyze_fn=staged_analyze)
        assert row["repo"] == "mathlib4"
        assert row["reconstructed_equal"] is True


def test_prepare_orders_snapshot_and_prevalidation_before_reveal():
    events = []

    def snapshot_stub(*_args, **_kwargs):
        events.append("snapshot-head")
        return "frozen"

    def rejecting_prevalidate(_frozen, **_kwargs):
        events.append("prevalidate-governance")
        raise V2BError("synthetic pre-salt rejection")

    def forbidden_reveal(*_args, **_kwargs):
        events.append("read-salt")
        raise AssertionError("salt phase ran after failed governance")

    try:
        prepare([], "private-salt", "commitment.json",
                require_committed_fn=lambda _path: {},
                source_clean_fn=lambda: True,
                implementation_freeze_fn=lambda **_kwargs: {},
                snapshot_chain_fn=snapshot_stub,
                prevalidate_fn=rejecting_prevalidate,
                reveal_fn=forbidden_reveal)
        assert False, "failed blind prevalidation did not stop prepare"
    except V2BError as err:
        assert "pre-salt" in str(err)
    assert events == ["snapshot-head", "prevalidate-governance"]


def test_formal_boundary_remains_disabled_and_behaviorally_gated():
    assert PRODUCTION_UNBLINDING_ENABLED is False
    params = inspect.signature(prepare).parameters
    assert "behavioral_paths" not in params
    source = open(os.path.join(ROOT, "finalize_v2b_nll_exploratory_reveal.py"),
                  encoding="utf-8").read()
    assert "--behavioral-governance" not in source
    assert "v2b_unblinding_v1" not in source


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B NLL EXPLORATORY REVEAL TESTS PASS")
