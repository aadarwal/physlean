#!/usr/bin/env python3
"""Synthetic tests for the §15.A14 B3 masked-delta producer: full
hash-chain reconstruction from a fabricated paired completion over a
REAL assembly-fixture manifest, exact completion-vs-manifest key
equality, full-grid cell metadata equality via eval_paired's shared
enumeration, manifest-verified complete-case eligibility, salt +
committed-artifact masking, fsum-corrected centered-residual blinding,
MoM invariance, and fail-closed drift refusals for every adversarial
finding. No model, score, or cluster artifact.
Run: python3 tests/test_prepare_v2b_masked_deltas.py"""
import hashlib
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_paired import COMPLETE_SCHEMA, TARGET_SCHEMA, target_cell_specs
from prepare_v2b_assembly import materialize
from prepare_v2b_masked_deltas import (_production_salt, _read_salt,
                                       _write_salt_pair, blind_rows,
                                       build_masked_deltas, family_id,
                                       family_sign)
from test_prepare_v2b_assembly import _build, _lean_chain
from v2b_common import (ASSEMBLY_SCHEMA, MASKED_DELTAS_SCHEMA,
                        SALT_COMMITMENT_SCHEMA, V2BError, identity_key,
                        sha256_json)
from v2b_n_governance import variance_components

SALT = bytes(range(32))


def test_blind_rows_center_flip_and_invariance():
    raw = [("k-b", 3.0), ("k-a", 1.0), ("k-c", 5.0)]
    rows, centering = blind_rows(raw, -1)
    assert [row[0] for row in rows] == ["k-a", "k-b", "k-c"]  # sorted
    # fsum-corrected centering: zero to within one ulp-scale residue
    assert abs(math.fsum(row[1] for row in rows)) < 1e-12
    assert rows[0][1] == 2.0 and rows[2][1] == -2.0           # flipped
    assert centering["removed_mean"] == 3.0
    assert centering["total_centering"] == \
        centering["removed_mean"] + centering["fsum_correction"]
    assert blind_rows([], 1) == ([], None)
    # awkward floats: the correction pass still centers to ~ulp scale
    awkward = [(f"k{i}", 0.1 + 1e-9 * i) for i in range(7)]
    centered, awk_centering = blind_rows(awkward, 1)
    assert abs(math.fsum(row[1] for row in centered)) < 1e-18
    # MULTI-ROW RECONSTRUCTION: raw = sign*published + total_centering
    # to FP roundoff; exact by forward replay of blind_rows itself
    raw_map = dict(awkward)
    for key, published in centered:
        rebuilt = 1 * published + awk_centering["total_centering"]
        assert abs(rebuilt - raw_map[key]) < 1e-15
    assert blind_rows(awkward, 1) == (centered, awk_centering)  # replay
    # MoM components are invariant to the translation + flip
    raw_by_module = {"A": [1.0, 2.0, 3.0], "B": [2.0, 4.0],
                     "C": [7.0, 7.5]}
    flat = [(f"{m}{i}", v) for m, vs in raw_by_module.items()
            for i, v in enumerate(vs)]
    blinded = dict((row[0], row[1]) for row in blind_rows(flat, -1)[0])
    blind_by_module = {m: [blinded[f"{m}{i}"] for i in range(len(vs))]
                       for m, vs in raw_by_module.items()}
    a = variance_components(raw_by_module)
    b = variance_components(blind_by_module)
    assert abs(a["sigma_b2"] - b["sigma_b2"]) < 1e-12
    assert abs(a["sigma_w2"] - b["sigma_w2"]) < 1e-12


def test_salt_pair_commitment_and_derivation():
    fid = family_id(SALT, "physlib", "E1a")
    assert fid.startswith("fam-") and len(fid) == 20
    assert all(ch in "0123456789abcdef" for ch in fid[4:])
    assert family_id(SALT, "physlib", "E1b") != fid
    assert family_id(bytes(32), "physlib", "E1a") != fid  # salt-dependent
    assert family_sign(SALT, "physlib", "E1a") in (1, -1)
    with tempfile.TemporaryDirectory() as td:
        salt_path = os.path.join(td, "salt")
        commitment_path = os.path.join(td, "commitment.json")
        digest = _write_salt_pair(salt_path, commitment_path)
        assert len(digest) == 64
        assert (os.stat(salt_path).st_mode & 0o777) == 0o600
        commitment = json.load(open(commitment_path))
        assert commitment["schema"] == SALT_COMMITMENT_SCHEMA
        assert commitment["state"] == "committed-pre-score"
        assert commitment["salt_sha256"] == digest
        assert commitment["generator"]["program"]
        # both halves are write-once
        try:
            _write_salt_pair(salt_path, commitment_path)
            assert False, "salt overwritten"
        except (FileExistsError, V2BError):
            pass
        # the salt only reads against its EXACT committed artifact
        salt, binding = _read_salt(salt_path, commitment_path)
        assert binding["salt_sha256"] == digest
        other_salt = os.path.join(td, "other-salt")
        with open(other_salt, "w") as fh:
            fh.write("00" * 32 + "\n")
        try:
            _read_salt(other_salt, commitment_path)
            assert False, "uncommitted salt accepted"
        except V2BError as err:
            assert "commitment" in str(err)


SCORED_BODY_BYTES = 8


def _paired_fixture(td, nll_of=None):
    """Fabricated completion + target artifact over a REAL manifest; the
    stored bpb is derived from nll/scored-bytes with the exact production
    expression so the recomputation gate holds."""
    chain = _lean_chain(td, big_dep=True)
    manifest = _build(chain)
    manifest_path = os.path.join(td, "manifest.json")
    json.dump(manifest, open(manifest_path, "w"))
    manifest_sha = hashlib.sha256(
        open(manifest_path, "rb").read()).hexdigest()
    manifest_loaded = json.load(open(manifest_path))
    blobs = materialize(manifest_path, chain["sample"], chain["repo"],
                        chain["candidates"], chain["extraction"],
                        chain["neardup"], chain["outcome"],
                        chain["freeze"], chain["k7"])
    target_row = manifest_loaded["targets"][0]
    target_key = target_row["key"]
    specs = target_cell_specs(
        target_row, blobs[identity_key("lean", ["M.A", "M.A.t"])])
    cells = []
    for index, spec in enumerate(specs):
        cell = dict(spec)
        cell.pop("context")
        nll = (nll_of(cell["cell_id"]) if nll_of
               else 0.4 + 0.001 * index)
        bpb = nll / math.log(2) / SCORED_BODY_BYTES
        cell["primary"] = dict(nll_nats=nll, bpb=bpb, bpc=bpb, n_tokens=9)
        cell["boundary_ledger"] = dict(
            scored_body_bytes=SCORED_BODY_BYTES)
        cells.append(cell)
    run_identity = dict(paired_schema_version=1,
                        manifest_sha256=manifest_sha, model="m",
                        revision="a" * 40, dtype="bfloat16",
                        chunk_tokens=2048,
                        paired_harness_hash="b" * 64,
                        env_fingerprint="c" * 64,
                        ast_class_state="not-run-separate-required-gate")
    run_sha = sha256_json(run_identity)
    generator = dict(source_commit="d" * 40, source_tree_hash="e" * 64,
                     program="eval_paired.py")
    target_artifact = dict(
        schema=TARGET_SCHEMA, run_identity=run_identity,
        run_identity_sha256=run_sha, repo=manifest_loaded["repo"],
        language=manifest_loaded["language"],
        corpus_git_sha=manifest_loaded["corpus_git_sha"],
        assembly_manifest=dict(path=manifest_path, sha256=manifest_sha,
                               schema=ASSEMBLY_SCHEMA),
        assembly_target_sha256=sha256_json(target_row),
        target_index=0, target_key=target_key,
        target_identity=target_row["identity"],
        prefix_sha256=target_row["prefix_sha256"],
        prefix_bytes=target_row["prefix_bytes"],
        body_sha256=target_row["body_sha256"],
        body_bytes=target_row["body_bytes"],
        n_cells=len(cells), cells=cells,
        generator=generator)
    target_path = os.path.join(td, "target-0000.json")
    json.dump(target_artifact, open(target_path, "w"))
    complete = dict(
        schema=COMPLETE_SCHEMA, run_identity=run_identity,
        run_identity_sha256=run_sha, repo=manifest_loaded["repo"],
        language=manifest_loaded["language"],
        corpus_git_sha=manifest_loaded["corpus_git_sha"],
        ast_class_state="not-run-separate-required-gate",
        assembly_manifest=dict(path=manifest_path, sha256=manifest_sha,
                               schema=ASSEMBLY_SCHEMA),
        n_targets=1, n_cells=len(cells),
        target_artifacts=[dict(
            path=target_path,
            sha256=hashlib.sha256(
                open(target_path, "rb").read()).hexdigest(),
            target_key=target_key, n_cells=len(cells))],
        generator=generator)
    complete_path = os.path.join(td, "complete.json")
    json.dump(complete, open(complete_path, "w"))
    salt_path = os.path.join(td, "salt")
    commitment_path = os.path.join(td, "commitment.json")
    _write_salt_pair(salt_path, commitment_path)
    salt, commitment_binding = _read_salt(salt_path, commitment_path)
    return dict(chain=chain, manifest=manifest_loaded,
                manifest_path=manifest_path, complete=complete_path,
                target_path=target_path, target_key=target_key,
                salt=salt, commitment=commitment_binding)


def _restamp(fixture):
    """Re-seal the completion after a target artifact mutation, exactly
    as a consistent adversary would."""
    complete = json.load(open(fixture["complete"]))
    complete["target_artifacts"][0]["sha256"] = hashlib.sha256(
        open(fixture["target_path"], "rb").read()).hexdigest()
    json.dump(complete, open(fixture["complete"], "w"))


def _run(fixture, sample=None, candidates=None):
    chain = fixture["chain"]
    return build_masked_deltas(
        fixture["complete"], fixture["manifest_path"],
        sample or chain["sample"], candidates or chain["candidates"],
        fixture["salt"], fixture["commitment"])


def test_masked_artifact_end_to_end_blind():
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(
            td, nll_of=lambda cell_id: {"k1": 0.9,
                                        "k4:16384": 0.5}.get(cell_id, 0.7))
        masked, private = _run(fixture)
        assert masked["schema"] == MASKED_DELTAS_SCHEMA
        assert masked["metric"] == "bpb"
        assert masked["budget_bytes"] == 16384
        assert masked["language"] == "lean"
        assert masked["corpus_git_sha"] == \
            fixture["manifest"]["corpus_git_sha"]
        assert masked["run_identity"]["model"] == "m"
        assert masked["bindings"]["salt_commitment"]["salt_sha256"] == \
            hashlib.sha256(fixture["salt"]).hexdigest()
        for name in ("sample", "candidates", "assembly", "completion"):
            assert set(masked["bindings"][name]) >= {"path", "sha256"}
        assert len(masked["families"]) == 3
        # big_dep fixture: only k4 B* is eligible -> E1a has the single
        # target; k3/k5 B* are ineligible -> E1b/E2 emit zero rows
        assert private["E1a"]["n_rows"] == 1
        assert private["E1b"]["n_rows"] == 0
        assert private["E2"]["n_rows"] == 0
        e1a_rows = masked["families"][private["E1a"]["fid"]]
        # singleton family: centered residual is exactly zero — the raw
        # delta (0.9 - 0.5) never appears in the public artifact
        assert e1a_rows == [[fixture["target_key"], 0.0]]
        dumped = json.dumps(masked)
        assert "0.9" not in json.dumps(masked["families"])
        for banned in ("E1a", "E1b", "E2", "\"k1\"", "\"k4\"", "\"mean",
                       "\"sign"):
            assert banned not in dumped
        # deterministic given the salt
        again, _ = _run(fixture)
        assert sha256_json(masked) == sha256_json(again)


def test_masked_producer_fails_closed():
    # (target bytes) tampered score changes the artifact hash
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        value["cells"][0]["primary"]["bpb"] = 0.0
        json.dump(value, open(fixture["target_path"], "w"))
        try:
            _run(fixture)
            assert False, "tampered target artifact accepted"
        except V2BError as err:
            assert "hash drift" in str(err)
    # (3) completion keys must equal manifest keys exactly
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        complete = json.load(open(fixture["complete"]))
        complete["target_artifacts"][0]["target_key"] = "[\"X\",\"Y\"]"
        json.dump(complete, open(fixture["complete"], "w"))
        try:
            _run(fixture)
            assert False, "foreign completion key accepted"
        except V2BError as err:
            assert "exactly" in str(err)
    # (3) cell counts must sum
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        complete = json.load(open(fixture["complete"]))
        complete["n_cells"] += 1
        json.dump(complete, open(fixture["complete"], "w"))
        try:
            _run(fixture)
            assert False, "cell count mismatch accepted"
        except V2BError as err:
            assert "sum" in str(err)
    # (3) target_index must match, even re-stamped consistently
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        value["target_index"] = 7
        json.dump(value, open(fixture["target_path"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "wrong target index accepted"
        except V2BError as err:
            assert "binding drift" in str(err)
    # (4) the ENTIRE grid must match: dropping a NON-contrast cell
    # (k2:4096) with consistent counts/hashes still refuses
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        value["cells"] = [cell for cell in value["cells"]
                          if cell["cell_id"] != "k2:4096"]
        value["n_cells"] = len(value["cells"])
        json.dump(value, open(fixture["target_path"], "w"))
        complete = json.load(open(fixture["complete"]))
        complete["target_artifacts"][0]["n_cells"] = value["n_cells"]
        complete["n_cells"] = value["n_cells"]
        json.dump(complete, open(fixture["complete"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "missing non-contrast cell accepted"
        except V2BError as err:
            assert "grid" in str(err)
    # (5) eligibility must EQUAL the manifest boolean, not be trusted
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        for cell in value["cells"]:
            if cell["cell_id"] == "k4:16384":
                cell["eligible"] = False        # manifest says True
        json.dump(value, open(fixture["target_path"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "eligibility flip accepted"
        except V2BError as err:
            assert "grid" in str(err)
    # (2) sample/candidates are schema-validated artifacts
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        try:
            _run(fixture, sample=fixture["chain"]["candidates"])
            assert False, "candidates artifact accepted as bound sample"
        except V2BError as err:
            assert "schema" in str(err)
    # run identity drift
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        complete = json.load(open(fixture["complete"]))
        complete["run_identity"]["model"] = "other"
        json.dump(complete, open(fixture["complete"], "w"))
        try:
            _run(fixture)
            assert False, "run identity drift accepted"
        except V2BError as err:
            assert "run identity" in str(err)
    # committed-boundary: an uncommitted commitment artifact refuses in
    # the production salt path
    with tempfile.TemporaryDirectory() as td:
        salt_path = os.path.join(td, "salt")
        commitment_path = os.path.join(td, "commitment.json")
        _write_salt_pair(salt_path, commitment_path)
        try:
            _production_salt(salt_path, commitment_path)
            assert False, "uncommitted salt commitment accepted"
        except V2BError as err:
            assert "commit" in str(err).lower()
    # generator mismatch between target and completion, restamped
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        value["generator"]["source_commit"] = "f" * 40
        json.dump(value, open(fixture["target_path"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "generator mismatch accepted"
        except V2BError as err:
            assert "generator" in str(err)
    # full identity drift against the manifest row, restamped
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        value["target_identity"] = ["M.A", "M.A.pre"]
        json.dump(value, open(fixture["target_path"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "target identity drift accepted"
        except V2BError as err:
            assert "rebind" in str(err)
    # bpb denominator drift: a self-consistently restamped bpb that no
    # longer recomputes from nll/scored bytes refuses
    with tempfile.TemporaryDirectory() as td:
        fixture = _paired_fixture(td)
        value = json.load(open(fixture["target_path"]))
        for cell in value["cells"]:
            if cell["cell_id"] == "k4:16384":
                cell["primary"]["bpb"] += 0.01
        json.dump(value, open(fixture["target_path"], "w"))
        _restamp(fixture)
        try:
            _run(fixture)
            assert False, "restamped bpb accepted"
        except V2BError as err:
            assert "recompute" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B MASKED DELTA TESTS PASS")
