#!/usr/bin/env python3
"""Synthetic, outcome-free tests for the V2-c P1a census.

Run: python3 tests/test_direct_scaling_feasibility.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from direct_scaling_feasibility import (CensusError, _best_range,
                                        SourceFile, RepoInventory,
                                        PRODUCTION_PROTOCOL_SHA256,
                                        _lexical_grams, _lexical_records,
                                        _repo_ranges, _systematic_file_origins,
                                        _systematic_indices,
                                        _systematic_u64, build_artifact,
                                        build_graph,
                                        implied_headline_rung,
                                        protocol_projection,
                                        recompute_decisions,
                                        reproduce_and_compare,
                                        validate_artifact,
                                        validate_protocol)
from v2b_common import sha256_sorted_json


def _write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True,
                  ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _run(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True)
    if result.returncode:
        raise AssertionError(f"{args!r} failed: {result.stderr}")
    return result.stdout.strip()


def _source(label, imports=""):
    rows = [imports] if imports else []
    rows += [f"{label}_{i:03d} = '{label}-unique-payload-{i:03d}'"
             for i in range(256)]
    return "\n".join(rows) + "\n"


def _fixture(tmp, *, graph_failure=False):
    corpora = os.path.join(tmp, "corpora")
    repo = os.path.join(corpora, "repo1")
    os.makedirs(os.path.join(repo, "pkg"))
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "fixture@example.invalid"], repo)
    _run(["git", "config", "user.name", "Fixture"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    open(os.path.join(repo, "pkg", "__init__.py"), "w").write(
        _source("init"))
    open(os.path.join(repo, "pkg", "a.py"), "w").write(_source("a"))
    b_import = "import pkg.missing" if graph_failure else "import pkg.a"
    open(os.path.join(repo, "pkg", "b.py"), "w").write(
        _source("b", b_import))
    open(os.path.join(repo, "pkg", "c.py"), "w").write(
        _source("c", "import pkg.b"))
    _run(["git", "add", "."], repo)
    env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T00:00:00+00:00",
               GIT_COMMITTER_DATE="2026-01-02T00:00:00+00:00")
    _run(["git", "commit", "-qm", "fixture"], repo, env=env)
    sha = _run(["git", "rev-parse", "HEAD"], repo)

    lock = {"repos": {"repo1": {"url": "fixture://repo1", "sha": sha}},
            "arxiv": {"checksums_sha256": "0" * 64,
                       "manifest_sha256": "1" * 64}}
    lock_path = os.path.join(tmp, "corpora_lock.json")
    lock_sha = _write_json(lock_path, lock)
    fixture_protocol = os.path.join(
        ROOT, "tests", "fixtures",
        "v2c_direct_scaling_protocol_fixture.json")
    protocol = json.load(open(fixture_protocol, encoding="utf-8"))
    protocol["input_ledgers"]["corpora_lock"]["sha256"] = lock_sha
    protocol["panel"]["repositories"][0]["revision"] = sha
    protocol["protocol_binding"] = sha256_sorted_json(
        protocol_projection(protocol))
    protocol_path = os.path.join(tmp, "protocol.json")
    protocol_sha = _write_json(protocol_path, protocol)
    generator = {"source_commit": "d" * 40,
                 "source_tree_hash": "e" * 64,
                 "program": "direct_scaling_feasibility.py"}
    return dict(corpora=corpora, lock=lock, lock_sha=lock_sha,
                lock_path=lock_path, protocol_path=protocol_path,
                protocol=protocol, protocol_sha=protocol_sha,
                generator=generator)


def _artifact(fixture):
    return build_artifact(
        protocol=fixture["protocol"],
        protocol_sha256=fixture["protocol_sha"],
        corpora_lock=fixture["lock"],
        corpora_lock_sha256=fixture["lock_sha"],
        corpora_root=fixture["corpora"],
        generator=fixture["generator"], allow_synthetic=True)


def _resign(artifact):
    artifact.pop("payload_sha256", None)
    artifact["payload_sha256"] = sha256_sorted_json(artifact)


def _validate(artifact, fixture):
    validate_artifact(
        artifact, protocol=fixture["protocol"],
        protocol_sha256=fixture["protocol_sha"],
        corpora_lock=fixture["lock"],
        corpora_lock_sha256=fixture["lock_sha"], allow_synthetic=True)


def test_deterministic_composition_invariance_and_deep_reproduction():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        first = _artifact(fixture)
        second = _artifact(fixture)
        assert first == second
        _validate(first, fixture)
        reproduce_and_compare(
            first, protocol=fixture["protocol"],
            protocol_sha256=fixture["protocol_sha"],
            corpora_lock=fixture["lock"],
            corpora_lock_sha256=fixture["lock_sha"],
            corpora_root=fixture["corpora"], allow_synthetic=True)
        orderings = first["repos"][0]["orderings"]
        assert len({row["file_set_sha256"]
                    for row in orderings.values()}) == 1
        assert len({row["stream_bytes"] for row in orderings.values()}) == 1
        assert len({row["ordering_sha256"]
                    for row in orderings.values()}) >= 2


def test_composition_tamper_is_rejected_even_when_resigned():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        artifact = _artifact(fixture)
        artifact["repos"][0]["orderings"]["topological"][
            "file_set_sha256"] = "f" * 64
        _resign(artifact)
        try:
            _validate(artifact, fixture)
        except CensusError as err:
            assert "composition" in str(err)
        else:
            raise AssertionError("composition tamper was accepted")


def test_native_graph_failure_is_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp, graph_failure=True)
        artifact = _artifact(fixture)
        repo = artifact["repos"][0]
        assert repo["dependency_references_resolved"] \
            < repo["dependency_references"]
        assert repo["graph_gate_ok"] is False
        assert artifact["decisions"][
            "headline_conditions_structurally_reachable_by_language"][
                "python"] is False


def test_cpp_without_per_tu_scan_is_conspicuously_fail_closed():
    file = SourceFile(
        repo="cpprepo", language="cpp", rel="src/a.cc", blob_oid="a" * 40,
        data=b'#include "b.hh"\nint main() { return 0; }\n',
        text='#include "b.hh"\nint main() { return 0; }\n',
        source_sha256="b" * 64, first_add_date="2026-01-01")
    inventory = RepoInventory(
        spec={"repo": "cpprepo", "lock_key": "cpprepo",
              "language": "cpp", "source_roots": [""],
              "extensions": [".cc", ".hh"], "exclude_paths": [],
              "package_prefixes": [],
              "graph": {"resolver": "cpp-unavailable-fail-closed-v1"}},
        locked_sha="c" * 40, tree_oid="d" * 40, files=[file],
        skipped_non_utf8=0, history_commits=frozenset({"c" * 40}))
    graph = build_graph(inventory, "/unused")
    assert graph["complete"] is False
    assert graph["dependency_edges"] == []
    assert graph["resolver_evidence"] == {
        "status": "unavailable-fail-closed"}


def test_untracked_corpus_state_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        path = os.path.join(fixture["corpora"], "repo1", "pkg",
                            "untracked.py")
        open(path, "w").write("x = 1\n")
        try:
            _artifact(fixture)
        except CensusError as err:
            assert "untracked drift" in str(err)
        else:
            raise AssertionError("untracked resolver-visible state accepted")


def test_a0_endpoint_is_part_of_exhaustion_cascade():
    def curve(end):
        return {"without_floor": {"end_bytes": end, "decades": 2.1},
                "headline_floor_robust_ok": True}
    repo = {
        "language": "python", "graph_gate_ok": True,
        "implied_min_context_bytes_for_headline": 1024,
        "structural_ranges": {
            "A0": {"shuffled": {"q_stream": curve(2048),
                                  "q_source": curve(512)}},
            "A1": {"shuffled": {
                "with-file": curve(2048),
                "cross-file-only": curve(2048),
                "shared-regime-complete-case": curve(2048)}},
        },
    }
    language = {"language": "python", "n_independent_components": 3}
    projected = {
        "constants": {
            "range": {"min_decades_without_floor": 2.0},
            "repository_dependence": {"minimum_components": 3},
            "headline_grid_bytes": [1, 10, 100, 1024]},
        "checkpoints": [{"model_id": "m", "revision": "a" * 40,
                         "optimistic_context_bytes": 2048}],
    }
    first = recompute_decisions([repo], [language], projected)
    assert first[
        "headline_conditions_structurally_reachable_by_language"][
            "python"] is False
    repo["structural_ranges"]["A0"]["shuffled"]["q_source"] = curve(1024)
    second = recompute_decisions([repo], [language], projected)
    assert second[
        "headline_conditions_structurally_reachable_by_language"][
            "python"] is True
    assert second["headline_requires_top_rung_by_language"]["python"] is True
    repo["structural_ranges"]["A0"]["shuffled"]["q_stream"] = curve(512)
    third = recompute_decisions([repo], [language], projected)
    assert third[
        "headline_conditions_structurally_reachable_by_language"][
            "python"] is False


def test_a0_axes_shared_a1_cohort_sample_cap_and_protocol_ranges():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        artifact = _artifact(fixture)
        repo = artifact["repos"][0]
        constants = validate_protocol(
            fixture["protocol"], protocol_sha256=fixture["protocol_sha"],
            corpora_lock_sha256=fixture["lock_sha"],
            allow_synthetic=True)["constants"]
        assert repo["n_blocks_sampled"] <= fixture["protocol"]["sampling"][
            "planned_per_repo"]
        metadata = repo[
            "metadata_bytes_fraction_by_q_stream_ordering"]["shuffled"]
        assert any(value and value > 0 for value in metadata.values())
        source_rows = repo[
            "A0_structural_occupancy_by_ordering_axis_rung"][
                "shuffled"]["q_source"]
        previous = 0
        for rung in constants["grid_bytes"]:
            row = source_rows[str(rung)]
            assert row["metadata_bytes"] == 0
            assert row["source_bytes"] == ((rung - previous)
                                           * row["n_complete_units"])
            previous = rung
        shared = repo[
            "n_blocks_structurally_complete_by_c_ordering_shared_regimes"]
        regimes = repo[
            "n_blocks_structurally_complete_by_c_ordering_regime"]
        for ordering in shared:
            prior = None
            for rung in constants["grid_bytes"]:
                cell = shared[ordering][str(rung)]
                assert cell["target_identities_sha256"] == \
                    sha256_sorted_json(cell["target_identities"])
                assert cell["n_blocks"] == regimes[ordering][
                    "cross-file-only"][str(rung)]["n_blocks"]
                current = {tuple(identity)
                           for identity in cell["target_identities"]}
                assert prior is None or current <= prior
                prior = current
        all_orderings = repo[
            "n_blocks_structurally_complete_by_c_all_orderings_regime"]
        for regime in ("with-file", "cross-file-only"):
            for rung in constants["grid_bytes"]:
                key = str(rung)
                expected = set.intersection(*(
                    {tuple(identity) for identity in regimes[ordering]
                     [regime][key]["target_identities"]}
                    for ordering in ("shuffled", "topological",
                                     "reverse-topological")))
                actual = {tuple(identity) for identity in
                          all_orderings[regime][key]["target_identities"]}
                assert actual == expected
        assert all(
            repo["structural_ranges"]["A0"][ordering][axis]
                ["without_floor"]["end_bytes"]
            in (None, 1024, 2048, 4096)
            for ordering in ("shuffled", "topological", "reverse-topological")
            for axis in ("q_stream", "q_source"))
        stricter = copy.deepcopy(constants)
        stricter["range"]["min_contiguous_decades"] = 99.0
        reranged = _repo_ranges(repo, stricter)
        assert not any(reranged["A0"][ordering][axis]["ordinary_range_ok"]
                       for ordering in reranged["A0"]
                       for axis in reranged["A0"][ordering])


def test_slurm_path_deep_validates_and_arm_b_is_not_claimed_complete():
    slurm = open(os.path.join(
        ROOT, "slurm", "v2c_direct_scaling_feasibility.sbatch")).read()
    source = open(os.path.join(ROOT, "direct_scaling_feasibility.py")).read()
    assert "--deep" in slurm
    assert "V2C_PROTOCOL_SHA256" in slurm
    assert slurm.count("--protocol-sha256") == 2
    assert '"--untracked-files=all"' in source
    with tempfile.TemporaryDirectory() as tmp:
        artifact = _artifact(_fixture(tmp))
    assert artifact["stage_coverage"] == {
        "arm_a_structural": "implemented",
        "arm_b_structural": "incomplete-fail-closed",
        "complete_p1a_claim": False,
        "power_decision_consumed": False,
        "power_gate_status": "separate-artifact-not-consumed",
        "loss_scoring_licensed_by_this_artifact": False,
    }


def test_floor_robustness_removes_the_floor_rung():
    grid = [1, 10, 100, 1000]
    floors = {"bin_units": 20, "bin_files": 10,
              "cell_units": 100, "cell_files": 30}
    occupancy = {str(rung): {"n_complete_units": 100,
                             "n_distinct_files": 30}
                 for rung in grid}
    all_range = _best_range(occupancy, grid, floors)
    robust = _best_range(occupancy, grid, floors, remove_floor=True)
    assert all_range["decades"] == 3.0
    assert robust["decades"] == 2.0
    thinned = {key: value for key, value in occupancy.items()
               if int(key) <= 100}
    # Preserve the frozen grid with zero occupancy at the missing top rung:
    thinned["1000"] = {"n_complete_units": 0, "n_distinct_files": 0}
    assert _best_range(thinned, grid, floors)["decades"] == 2.0
    assert _best_range(thinned, grid, floors,
                       remove_floor=True)["decades"] == 1.0


def test_ten_x_exhaustion_top_rung_cascade_and_upper_bound():
    grid = [1, 10, 100, 1000, 10_000]
    rule = {"min_decades_without_floor": 2.0,
            "exhaustion_multiplier": 10.0}
    assert implied_headline_rung(grid, 9, rule) == 1000  # floor robustness
    assert implied_headline_rung(grid, 900, rule) == 10_000  # top rung
    assert implied_headline_rung(grid, 1001, rule) is None  # beyond support


def test_frozen_systematic_phase_and_a0_file_strata():
    seed = "ab" * 32
    preimage = json.dumps(
        ["v2c-systematic-offset-v1", seed, "repo1", "a0"],
        ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    expected = int.from_bytes(hashlib.sha256(preimage).digest()[:8], "big")
    assert _systematic_u64(seed, "repo1", "a0") == expected
    paths = [f"f{i:03d}.py" for i in range(37)]
    origins = _systematic_file_origins(paths, 11, seed, "repo1")
    assert len(origins) == len(set(origins)) == 11
    assert origins == sorted(origins)
    indices = _systematic_indices(137, 17, expected)
    scale = 1 << 64
    assert indices == [
        (137 * (expected + j * scale)) // (17 * scale)
        for j in range(17)]
    assert all(right > left for left, right in zip(indices, indices[1:]))


def test_lexical_byte_window_excludes_only_split_utf8_edge_scalars():
    # Both requested endpoints split a four-byte scalar.  Lexical coordinates
    # remain coordinates in the original frozen byte axis, not in a shifted
    # or replacement-decoded string.
    text = "🐍alpha beta gamma delta epsilon🚀"
    data = text.encode("utf-8")
    start, end = 2, len(data) - 2
    records = _lexical_records(data, "python", start=start, end=end)
    expected_start = len("🐍".encode("utf-8"))
    expected_end = len("🐍alpha beta gamma delta epsilon".encode("utf-8"))
    assert [row[0] for row in records] == [
        "alpha", "beta", "gamma", "delta", "epsilon"]
    assert records[0][1] == expected_start
    assert records[-1][2] == expected_end
    assert data[records[0][1]:records[-1][2]].decode("utf-8") == \
        "alpha beta gamma delta epsilon"

    grams = _lexical_grams(
        data, 0, "python", 5, start=start, end=end)
    assert len(grams) == 1
    assert grams[0][1:3] == (expected_start, expected_end)
    # The requested byte window remains unchanged and is not silently rounded.
    assert (start, end) == (2, len(data) - 2)


def test_lexical_byte_window_never_replaces_malformed_interior_utf8():
    data = "αalpha ".encode("utf-8") + b"\xff" + \
        " betaβ".encode("utf-8")
    try:
        _lexical_records(data, "python", start=1, end=len(data) - 1)
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("malformed interior UTF-8 was replaced or ignored")


def test_schema_decision_and_raw_tamper_rejection():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        artifact = _artifact(fixture)
        bad_schema = copy.deepcopy(artifact)
        bad_schema["schema"] = "v2c_direct_scaling_feasibility_v0"
        _resign(bad_schema)
        try:
            _validate(bad_schema, fixture)
        except CensusError:
            pass
        else:
            raise AssertionError("wrong schema accepted")

        bad_decision = copy.deepcopy(artifact)
        bad_decision["decisions"][
            "K2_independence_ok_by_language"]["python"] = True
        _resign(bad_decision)
        try:
            _validate(bad_decision, fixture)
        except CensusError as err:
            assert "decisions" in str(err)
        else:
            raise AssertionError("re-signed decision tamper accepted")

        raw_tamper = copy.deepcopy(artifact)
        raw_tamper["repos"][0][
            "metadata_bytes_fraction_by_q_stream_ordering"][
                "shuffled"]["512"] = 0.123456
        _resign(raw_tamper)
        try:
            _validate(raw_tamper, fixture)
        except CensusError as err:
            assert "metadata fraction" in str(err)
        else:
            raise AssertionError("re-signed occupancy tamper was accepted")

        # Deep reproduction additionally binds raw summaries that cannot be
        # reconstructed from other artifact fields alone.
        raw_tamper = copy.deepcopy(artifact)
        raw_tamper["repos"][0]["first_add_date_min"] = "1999-01-01"
        _resign(raw_tamper)
        _validate(raw_tamper, fixture)
        try:
            reproduce_and_compare(
                raw_tamper, protocol=fixture["protocol"],
                protocol_sha256=fixture["protocol_sha"],
                corpora_lock=fixture["lock"],
                corpora_lock_sha256=fixture["lock_sha"],
                corpora_root=fixture["corpora"], allow_synthetic=True)
        except CensusError as err:
            assert "reproduction differs" in str(err)
        else:
            raise AssertionError("deep validation accepted raw tamper")


def test_protocol_missing_constant_is_rejected_not_defaulted():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        protocol = copy.deepcopy(fixture["protocol"])
        del protocol["sampling"]["a1_seed_sha256"]
        protocol["protocol_binding"] = sha256_sorted_json(
            protocol_projection(protocol))
        try:
            validate_protocol(protocol, protocol_sha256="a" * 64,
                              corpora_lock_sha256=fixture["lock_sha"],
                              allow_synthetic=True)
        except CensusError as err:
            assert "keys" in str(err)
        else:
            raise AssertionError("missing P0 constant was defaulted")


def test_protocol_repo_revision_and_url_must_match_lock():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        for field, bad in (("revision", "f" * 40),
                           ("url", "fixture://wrong")):
            altered = copy.deepcopy(fixture)
            altered["protocol"] = copy.deepcopy(fixture["protocol"])
            altered["protocol"]["panel"]["repositories"][0][field] = bad
            altered["protocol"]["protocol_binding"] = sha256_sorted_json(
                protocol_projection(altered["protocol"]))
            altered["protocol_sha"] = "9" * 64
            try:
                _artifact(altered)
            except CensusError as err:
                assert "does not match the corpus lock" in str(err)
            else:
                raise AssertionError(f"P0 repository {field} drift accepted")


def test_cli_requires_external_raw_protocol_sha_anchor():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _fixture(tmp)
        result = subprocess.run([
            sys.executable, os.path.join(ROOT,
                                         "direct_scaling_feasibility.py"),
            "produce", "--protocol", fixture["protocol_path"],
            "--protocol-sha256", "0" * 64,
            "--corpora-lock", fixture["lock_path"],
            "--corpora-root", fixture["corpora"],
            "--out", os.path.join(tmp, "never-written.json"),
            "--allow-synthetic-protocol"], capture_output=True, text=True)
        assert result.returncode == 2
        assert "differs from --protocol-sha256" in result.stderr


def test_authoritative_production_protocol_sha_is_frozen():
    assert PRODUCTION_PROTOCOL_SHA256 == (
        "b32f1ebb7de3e18230cd8f0c28633871e9543408788d07acf7cc2c916d160291")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("DIRECT SCALING FEASIBILITY TESTS PASS")
