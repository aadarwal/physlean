#!/usr/bin/env python3
"""Adversarial synthetic tests for the post-governance salt reveal:
governance->masked chain binding, salt-vs-commitment agreement, the
determinism proof (committed masked artifact must reconstruct exactly
from the revealed salt over hash-bound paired artifacts), mapping
proof, five-corpus completeness, the committed-ordering gate, and the
write-once auditable output. Nothing here reveals a production salt or
touches real evidence. Run: python3 tests/test_finalize_v2b_unblinding.py"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inspect

from finalize_v2b_unblinding import (BEHAVIORAL_GOVERNANCE_SCHEMA,
                                     PRODUCTION_UNBLINDING_ENABLED,
                                     _require_committed_chain,
                                     _verify_behavioral_chain,
                                     build_unblinding,
                                     verify_repo_unblinding)
from prepare_v2b_masked_deltas import (_read_salt, _write_salt_pair,
                                       build_masked_deltas, family_id)
from test_prepare_v2b_masked_deltas import _paired_fixture
from v2b_common import (N_GOVERNANCE_SCHEMA, UNBLINDING_SCHEMA, V2BError,
                        sha256_json, write_new_json)
from v2b_behavioral_governance import (BEHAVIOR_MASKED_SCHEMA,
                                        MASKED_BINDING_SCHEMAS)
from v2b_n_governance import analyze as governance_analyze


def _unblind_fixture(td, nll_of=None):
    """B3 paired fixture + committed-shape masked and governance files."""
    fixture = _paired_fixture(
        td, nll_of=nll_of or (lambda cell_id: {"k1": 0.9,
                                               "k4:16384": 0.5}
                              .get(cell_id, 0.7)))
    chain = fixture["chain"]
    masked, private = build_masked_deltas(
        fixture["complete"], fixture["manifest_path"], chain["sample"],
        chain["candidates"], fixture["salt"], fixture["commitment"])
    masked = dict(masked, generator=dict(
        source_commit="9" * 40, source_tree_hash="8" * 64,
        program="prepare_v2b_masked_deltas.py"))
    masked_path = os.path.join(td, "masked.json")
    json.dump(masked, open(masked_path, "w"))
    masked_sha = hashlib.sha256(open(masked_path, "rb").read()).hexdigest()
    governance = dict(
        schema=N_GOVERNANCE_SCHEMA, repo=masked["repo"],
        verdict="feasible", repo_n=257,
        bindings=dict(
            masked_deltas=dict(sha256=masked_sha),
            sample=masked["bindings"]["sample"],
            candidates=masked["bindings"]["candidates"],
            completion=masked["bindings"]["completion"]))
    governance_path = os.path.join(td, "governance.json")
    json.dump(governance, open(governance_path, "w"))
    entry = dict(masked_path=masked_path, governance_path=governance_path,
                 complete_path=fixture["complete"],
                 manifest_path=fixture["manifest_path"],
                 sample_path=chain["sample"],
                 candidates_path=chain["candidates"])

    # analyze() cannot run on this toy chain (no frozen 20-pilot plan),
    # so tests inject a stub that returns what a REAL recompute would:
    # the governance file's content minus its generator. The default
    # parameter is asserted to be the real analyzer elsewhere.
    def analyze_stub(*_args):
        value = json.load(open(governance_path))
        return {key: v for key, v in value.items() if key != "generator"}

    return fixture, entry, private, analyze_stub


def _behavioral_bindings(nll_masked_sha):
    rows = {name: dict(schema=schema, sha256="7" * 64)
            for name, schema in MASKED_BINDING_SCHEMAS.items()}
    rows["salt_commitment"]["salt_sha256"] = "8" * 64
    rows["nll_masked_deltas"]["sha256"] = nll_masked_sha
    rows["masked_outcomes"] = dict(
        schema=BEHAVIOR_MASKED_SCHEMA,
        canonical_sha256="9" * 64)
    return rows


def test_repo_unblinding_proves_mapping_and_reconstruction():
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, private, stub = _unblind_fixture(td)
        row = verify_repo_unblinding(
            salt=fixture["salt"], commitment_binding=fixture["commitment"],
            analyze_fn=stub, **entry)
        assert row["repo"] == "mathlib4"
        assert row["reconstructed_equal"] is True
        assert row["governance_verdict"] == "feasible"
        assert row["governance_repo_n"] == 257
        assert set(row["mapping"]) == {"E1a", "E1b", "E2"}
        e1a = row["mapping"]["E1a"]
        assert e1a["fid"] == family_id(fixture["salt"], "mathlib4", "E1a")
        assert e1a["sign"] in (1, -1)
        assert e1a["n_rows"] == 1
        # singleton family: the removed mean IS the raw delta, and the
        # full centering (mean + fsum correction) is published so raw
        # deltas reconstruct: raw = sign*published + total_centering
        expected_delta = (0.9 - 0.5) / __import__("math").log(2) / 8
        assert abs(e1a["removed_mean_bpb"] - expected_delta) < 1e-12
        assert e1a["total_centering_bpb"] == \
            e1a["removed_mean_bpb"] + e1a["fsum_correction"]
        assert row["mapping"]["E1b"]["removed_mean_bpb"] is None
        assert row["mapping"]["E1b"]["total_centering_bpb"] is None
        # production default is the REAL governance analyzer, injected
        # stubs exist only because toy chains cannot satisfy it
        assert inspect.signature(verify_repo_unblinding).parameters[
            "analyze_fn"].default is governance_analyze


def test_unblinding_fails_closed_on_chain_drift():
    # a tampered masked residual no longer reconstructs from the salt
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        value = json.load(open(entry["masked_path"]))
        fid = next(iter(value["families"]))
        for rows in value["families"].values():
            for row in rows:
                row[1] = row[1] + 1.0
        json.dump(value, open(entry["masked_path"], "w"))
        governance = json.load(open(entry["governance_path"]))
        governance["bindings"]["masked_deltas"]["sha256"] = \
            hashlib.sha256(
                open(entry["masked_path"], "rb").read()).hexdigest()
        json.dump(governance, open(entry["governance_path"], "w"))
        try:
            verify_repo_unblinding(salt=fixture["salt"],
                                   commitment_binding=fixture["commitment"],
                                   analyze_fn=stub, **entry)
            assert False, "tampered masked residuals unblinded"
        except V2BError as err:
            assert "reconstruct" in str(err)
    # governance not binding this masked artifact
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        governance = json.load(open(entry["governance_path"]))
        governance["bindings"]["masked_deltas"]["sha256"] = "0" * 64
        json.dump(governance, open(entry["governance_path"], "w"))
        try:
            verify_repo_unblinding(salt=fixture["salt"],
                                   commitment_binding=fixture["commitment"],
                                   analyze_fn=stub, **entry)
            assert False, "unbound governance unblinded"
        except V2BError as err:
            assert "bind" in str(err)
    # a different salt/commitment pair than the masked artifact recorded
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        other_salt = os.path.join(td, "other-salt")
        other_commitment = os.path.join(td, "other-commitment.json")
        _write_salt_pair(other_salt, other_commitment)
        salt, binding = _read_salt(other_salt, other_commitment)
        try:
            verify_repo_unblinding(salt=salt, commitment_binding=binding,
                                   analyze_fn=stub, **entry)
            assert False, "foreign salt unblinded"
        except V2BError as err:
            assert "commitment" in str(err)
    # malformed governance verdict
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        governance = json.load(open(entry["governance_path"]))
        governance["verdict"] = "maybe"
        json.dump(governance, open(entry["governance_path"], "w"))
        try:
            verify_repo_unblinding(salt=fixture["salt"],
                                   commitment_binding=fixture["commitment"],
                                   analyze_fn=stub, **entry)
            assert False, "malformed verdict unblinded"
        except V2BError as err:
            assert "verdict" in str(err)


def test_fabricated_governance_verdict_fails_recompute():
    # a committed-but-fabricated verdict is self-consistent JSON, but it
    # cannot equal the recomputed governance object
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        truth = stub()                        # the honest recompute
        governance = json.load(open(entry["governance_path"]))
        governance["verdict"] = "infeasible"
        governance["repo_n"] = None
        json.dump(governance, open(entry["governance_path"], "w"))
        try:
            verify_repo_unblinding(
                salt=fixture["salt"],
                commitment_binding=fixture["commitment"],
                analyze_fn=lambda *args: truth, **entry)
            assert False, "fabricated governance verdict unblinded"
        except V2BError as err:
            assert "recompute" in str(err)


def test_behavioral_governance_gate():
    # §14.22: NLL governance alone is INSUFFICIENT — the behavioral
    # chain is hard-required and must bind each repo's masked SHA
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, _stub = _unblind_fixture(td)
        masked_sha = hashlib.sha256(
            open(entry["masked_path"], "rb").read()).hexdigest()
        behavioral = dict(schema=BEHAVIORAL_GOVERNANCE_SCHEMA,
                          repo="mathlib4",
                          bindings=_behavioral_bindings(masked_sha))
        behavioral_path = os.path.join(td, "behavioral.json")
        json.dump(behavioral, open(behavioral_path, "w"))
        _verify_behavioral_chain([behavioral_path], [entry])  # passes
        # missing chain refuses with the §14.22 message
        try:
            _verify_behavioral_chain([], [entry])
            assert False, "missing behavioral chain accepted"
        except V2BError as err:
            assert "insufficient" in str(err)
        # a behavioral artifact not binding this masked SHA refuses
        behavioral["bindings"]["nll_masked_deltas"]["sha256"] = "0" * 64
        json.dump(behavioral, open(behavioral_path, "w"))
        try:
            _verify_behavioral_chain([behavioral_path], [entry])
            assert False, "unbound behavioral governance accepted"
        except V2BError as err:
            assert "bind" in str(err)
        # a wrong-schema artifact (e.g. the NLL governance) refuses
        try:
            _verify_behavioral_chain([entry["governance_path"]], [entry])
            assert False, "NLL governance accepted as behavioral"
        except V2BError as err:
            assert "schema" in str(err)
    # prepare() requires the behavioral chain positionally: there is no
    # default that could silently skip it
    from finalize_v2b_unblinding import prepare
    assert PRODUCTION_UNBLINDING_ENABLED is False
    assert inspect.signature(prepare).parameters[
        "behavioral_paths"].default is inspect.Parameter.empty
    try:
        prepare([], "private-salt", "commitment.json", [])
        assert False, "production unblinding enabled before behavior code"
    except V2BError as err:
        assert "disabled" in str(err) and "behavioral-governance" in str(err)


def test_unblinding_requires_exact_five_corpora():
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        # the fixture's own salt/commitment pair lives at td/salt and
        # td/commitment.json: a fully valid single-corpus chain must
        # still refuse on five-corpus completeness
        try:
            build_unblinding([entry], os.path.join(td, "salt"),
                             os.path.join(td, "commitment.json"),
                             analyze_fn=stub)
            assert False, "single-corpus unblinding accepted"
        except V2BError as err:
            assert "five-corpus" in str(err)


def test_committed_ordering_gate_and_output_shape():
    # uncommitted governance/masked artifacts refuse before any reveal
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _, stub = _unblind_fixture(td)
        try:
            _require_committed_chain(
                os.path.join(td, "commitment.json"),
                [entry["masked_path"]], [entry["governance_path"]],
                [os.path.join(td, "behavioral.json")])
            assert False, "uncommitted chain accepted for reveal"
        except V2BError as err:
            assert "commit" in str(err).lower()
    # the artifact shape: revealed salt + auditable mapping, write-once
    with tempfile.TemporaryDirectory() as td:
        fixture, entry, private, stub = _unblind_fixture(td)
        row = verify_repo_unblinding(
            salt=fixture["salt"], commitment_binding=fixture["commitment"],
            analyze_fn=stub, **entry)
        artifact = dict(schema=UNBLINDING_SCHEMA,
                        state="revealed-post-governance",
                        salt_commitment=fixture["commitment"],
                        revealed_salt_hex=fixture["salt"].hex(),
                        repos={row["repo"]: row})
        assert artifact["revealed_salt_hex"] == fixture["salt"].hex()
        assert sha256_json(artifact) == sha256_json(dict(artifact))
        out = os.path.join(td, "unblinding.json")
        write_new_json(out, artifact)
        try:
            write_new_json(out, artifact)
            assert False, "unblinding artifact overwritten"
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B UNBLINDING TESTS PASS")
