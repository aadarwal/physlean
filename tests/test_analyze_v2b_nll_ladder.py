#!/usr/bin/env python3
"""Ladder analyzer: reconstruction, tier binding, anti-shopping gates."""
import contextlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analyze_v2b_nll_ladder as lad  # noqa: E402
import prepare_v2b_masked_deltas as b3  # noqa: E402
import validity_battery as vb  # noqa: E402
from eval_paired import COMPLETE_SCHEMA  # noqa: E402
from v2b_common import V2BError, identity_key, sha256_file  # noqa: E402

SALT = lad.LADDER_PUBLIC_SALT
TREE = "t" * 64


@contextlib.contextmanager
def _expect(exc_type, needle=None):
    try:
        yield
    except exc_type as err:
        if needle is not None and needle not in str(err):
            raise AssertionError(
                f"expected {needle!r} in {exc_type.__name__}: {err}")
    else:
        raise AssertionError(f"expected {exc_type.__name__}, none raised")


def _key(module, decl):
    return identity_key("lean", [module, decl])


K1 = _key("ModA", "declX")
K2 = _key("ModB", "declY")

BASIC_DELTAS = {
    "E1a": [(K1, 0.1), (K2, 0.3)],
    "E1b": [(K1, 0.01)],
    "E2": [(K1, 0.05), (K2, 0.07)],
}


def test_public_salt_is_literally_32_zero_bytes():
    assert lad.LADDER_PUBLIC_SALT == b"\x00" * 32
    assert lad.FULL_TIER_SET == frozenset(
        ("q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b", "q25c-14b"))
    assert set(lad.PINNED_MANIFEST_SHA256) == {
        "mathlib4", "batteries", "physlib", "sympy", "astropy"}
    assert len(lad.PINNED_REVEAL_SHA256) == 64


def test_pinned_manifests_match_committed_files():
    order = {"mathlib4": 0, "batteries": 1, "physlib": 2, "sympy": 3,
             "astropy": 4}
    for repo, index in order.items():
        path = os.path.join(
            ROOT, "results_v2", "v2b", "assembly",
            f"job19991210_{index}_{repo}.json")
        assert sha256_file(path) == lad.PINNED_MANIFEST_SHA256[repo]
    reveal = os.path.join(
        ROOT, "results_v2", "v2b", "nll_exploratory_reveal",
        "job20007464_nll_exploratory_reveal.json")
    assert sha256_file(reveal) == lad.PINNED_REVEAL_SHA256


def _masked_private(repo, tag, deltas, battery_sha, completion_sha,
                    assembly_sha="a" * 64, language="lean",
                    run_overrides=None):
    tier = vb.PILOT_TIERS[tag]
    families = {}
    private = {}
    for name in ("E1a", "E1b", "E2"):
        rows = deltas.get(name, [])
        fid = b3.family_id(SALT, repo, name)
        sign = b3.family_sign(SALT, repo, name)
        published, centering = b3.blind_rows(rows, sign)
        families[fid] = published
        private[name] = dict(
            fid=fid, sign=sign, n_rows=len(rows),
            removed_mean=centering["removed_mean"] if centering else None,
            fsum_correction=centering["fsum_correction"]
            if centering else None,
            total_centering=centering["total_centering"]
            if centering else None)
    run_identity = dict(
        model=tier["model"], revision=tier["revision"], dtype="bfloat16",
        chunk_tokens=2048, pilot_battery_sha256=battery_sha)
    run_identity.update(run_overrides or {})
    masked = dict(
        repo=repo, language=language, metric="bpb", budget_bytes=16384,
        run_identity=run_identity,
        bindings=dict(assembly=dict(sha256=assembly_sha),
                      completion=dict(sha256=completion_sha),
                      run_identity_sha256="r" * 64),
        families=families)
    return masked, private


def test_tier_block_reconstructs_exact_means():
    masked, private = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64,
                                      completion_sha="c" * 64)
    block = lad._tier_block("sympy", "q25c-3b", masked, private, "b" * 64)
    assert block["tier"] == "q25c-3b"
    e1a = block["contrasts"]["E1a"]["inference"]
    assert e1a["n_targets"] == 2 and e1a["n_modules"] == 2
    assert abs(e1a["target_equal_mean_bpb"] - 0.2) < 1e-12
    assert block["contrasts"]["E1b"]["inference"]["n_targets"] == 1
    e2 = block["contrasts"]["E2"]["inference"]
    assert abs(e2["target_equal_mean_bpb"] - 0.06) < 1e-12
    assert block["governance"]["verdict"] == lad.LADDER_GOVERNANCE_VERDICT
    assert block["governance"]["repo_n"] is None
    cm = block["centering_by_contrast"]["E1a"]
    assert cm["n_rows"] == 2
    assert abs(cm["removed_mean_bpb"] - 0.2) < 1e-12


def test_tier_block_rejects_wrong_model_battery_chunk():
    masked, private = _masked_private(
        "sympy", "q25c-3b", BASIC_DELTAS, battery_sha="b" * 64,
        completion_sha="c" * 64,
        run_overrides={"model": "Qwen/Qwen2.5-Coder-7B"})
    with _expect(V2BError, "does not match tier"):
        lad._tier_block("sympy", "q25c-3b", masked, private, "b" * 64)
    masked, private = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64,
                                      completion_sha="c" * 64)
    with _expect(V2BError, "committed q25c-3b battery"):
        lad._tier_block("sympy", "q25c-3b", masked, private, "d" * 64)
    m2, p2 = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                             battery_sha="b" * 64, completion_sha="c" * 64,
                             run_overrides={"chunk_tokens": 1024})
    with _expect(V2BError, "dtype/chunk"):
        lad._tier_block("sympy", "q25c-3b", m2, p2, "b" * 64)


def test_physlib_forcing_is_inherited():
    masked, private = _masked_private("physlib", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64,
                                      completion_sha="c" * 64)
    block = lad._tier_block("physlib", "q25c-3b", masked, private, "b" * 64)
    status = block["contrasts"]["E1a"]["interpretation_status"]
    assert status == "uninterpretable-pending-k4x-sensitivity"


def _write(dirname, name, payload):
    path = os.path.join(dirname, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


class _Fixture:
    """Full-tier-set synthetic chain for one repo (sympy)."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.manifest = os.path.join(
            ROOT, "results_v2", "v2b", "assembly",
            "job19991210_3_sympy.json")  # real pinned file; stub build_fn
        self.sample = _write(tmp, "sample.json", {"stub": "sample"})
        self.candidates = _write(tmp, "candidates.json", {"stub": "cand"})
        self.batteries = {}
        self.completions = {}
        self.built = {}
        ledger_rows = {}
        for tag in sorted(lad.FULL_TIER_SET):
            bat = _write(tmp, vb.PILOT_TIERS[tag]["battery_file"],
                         {"stub": f"battery-{tag}"})
            self.batteries[tag] = bat
            tree = "s" * 64 if tag == lad.SEALED_TIER else TREE
            comp = _write(tmp, f"complete-{tag}.json", {
                "schema": COMPLETE_SCHEMA,
                "generator": {"source_tree_hash": tree}})
            self.completions[tag] = comp
            self.built[comp] = _masked_private(
                "sympy", tag, BASIC_DELTAS,
                battery_sha=sha256_file(bat),
                completion_sha=sha256_file(comp))
            ledger_rows[tag] = dict(path=os.path.abspath(comp),
                                    sha256=sha256_file(comp),
                                    slurm_job_id="job-test")
        self.ledger = {"schema": lad.LADDER_LEDGER_SCHEMA,
                       "repos": {"sympy": ledger_rows},
                       "_binding_sha256": "l" * 64}
        sealed_private = self.built[self.completions[lad.SEALED_TIER]][1]
        mapping = {name: dict(
            n_rows=row["n_rows"], removed_mean_bpb=row["removed_mean"],
            fsum_correction=row["fsum_correction"],
            total_centering_bpb=row["total_centering"])
            for name, row in sealed_private.items()}
        self.reveal = {
            "schema": lad.NLL_EXPLORATORY_REVEAL_SCHEMA,
            "repos": {"sympy": {
                "mapping": mapping,
                "bindings": {"completion": {"sha256": sha256_file(
                    self.completions[lad.SEALED_TIER])}}}}}

    def build_fn(self, complete_path, *_args):
        return self.built[complete_path]

    def analyze(self, **overrides):
        kwargs = dict(
            repo="sympy", manifest_path=self.manifest,
            sample_path=self.sample, candidates_path=self.candidates,
            tier_completions=dict(self.completions),
            tier_batteries=dict(self.batteries),
            ledger=self.ledger, reveal=self.reveal,
            build_fn=self.build_fn, current_tree_hash=TREE)
        kwargs.update(overrides)
        return lad.analyze_repo(**kwargs)


def test_analyze_repo_full_chain_and_gates():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _Fixture(tmp)
        artifact = fx.analyze()
        assert artifact["schema"] == lad.LADDER_ANALYSIS_SCHEMA
        assert artifact["claim_status"] == lad.LADDER_CLAIM_STATUS
        assert artifact["tier_order"] == sorted(lad.FULL_TIER_SET)
        for tag in lad.FULL_TIER_SET:
            mean = artifact["tiers"][tag]["contrasts"]["E1a"][
                "inference"]["target_equal_mean_bpb"]
            assert abs(mean - 0.2) < 1e-12
        assert artifact["bindings"]["manifest_sha256"] == \
            lad.PINNED_MANIFEST_SHA256["sympy"]
        assert artifact["bindings"]["reveal_sha256"] == \
            lad.PINNED_REVEAL_SHA256
        assert artifact["bindings"]["ledger_sha256"] == "l" * 64

        # subsets refused
        partial = dict(fx.completions)
        partial.pop("q25c-7b")
        with _expect(V2BError, "full tier set"):
            fx.analyze(tier_completions=partial)

        # ledger row drift refused
        bad_ledger = json.loads(json.dumps(fx.ledger))
        bad_ledger["repos"]["sympy"]["q25c-3b"]["sha256"] = "0" * 64
        with _expect(V2BError, "differs from the committed ledger"):
            fx.analyze(ledger=bad_ledger)

        # wrong manifest refused (sha pin)
        other = _write(tmp, "not_manifest.json", {"stub": 1})
        with _expect(V2BError, "pinned pilot manifest"):
            fx.analyze(manifest_path=other)

        # non-sealed completion scored on another tree refused
        with _expect(V2BError, "not scored at this source tree"):
            fx.analyze(current_tree_hash="u" * 64)

        # battery filename must match the registry
        bad_bat = dict(fx.batteries)
        alias = _write(tmp, "battery_alias.json", {"stub": "battery-x"})
        bad_bat["q25c-3b"] = alias
        with _expect(V2BError, "battery filename"):
            fx.analyze(tier_batteries=bad_bat)

        # sealed tier must be the reveal-bound completion
        bad_reveal = json.loads(json.dumps(fx.reveal))
        bad_reveal["repos"]["sympy"]["bindings"]["completion"][
            "sha256"] = "9" * 64
        with _expect(V2BError, "reveal-bound completion"):
            fx.analyze(reveal=bad_reveal)

        # sealed centering must reproduce the reveal
        bad_reveal2 = json.loads(json.dumps(fx.reveal))
        bad_reveal2["repos"]["sympy"]["mapping"]["E1a"][
            "removed_mean_bpb"] = 0.123456
        with _expect(V2BError, "centering drift"):
            fx.analyze(reveal=bad_reveal2)

        # cross-tier assembly drift refused
        comp3 = fx.completions["q25c-3b"]
        masked3, private3 = fx.built[comp3]
        fx.built[comp3] = (dict(masked3, bindings=dict(
            masked3["bindings"], assembly=dict(sha256="e" * 64))), private3)
        with _expect(V2BError, "different assembly manifests"):
            fx.analyze()


def test_parse_tier_args():
    parsed = lad._parse_tier_args(["q25c-3b=/x.json", "q25c-7b=/y.json"],
                                  "completion")
    assert parsed == {"q25c-3b": "/x.json", "q25c-7b": "/y.json"}
    with _expect(V2BError, "malformed"):
        lad._parse_tier_args(["nope"], "completion")
    with _expect(V2BError, "duplicate"):
        lad._parse_tier_args(["a=/x", "a=/y"], "completion")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("NLL LADDER ANALYZER TESTS PASS")
