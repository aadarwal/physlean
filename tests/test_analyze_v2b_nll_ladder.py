#!/usr/bin/env python3
"""Ladder analyzer: reconstruction, tier binding, cross-tier consistency."""
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
from v2b_common import V2BError, identity_key, sha256_file  # noqa: E402

SALT = lad.LADDER_PUBLIC_SALT


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


def _masked_private(repo, tag, deltas, battery_sha, assembly_sha="a" * 64,
                    language="lean", run_overrides=None):
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
                      completion=dict(sha256="c" * 64),
                      run_identity_sha256="r" * 64),
        families=families)
    return masked, private


BASIC_DELTAS = {
    "E1a": [(K1, 0.1), (K2, 0.3)],
    "E1b": [(K1, 0.01)],
    "E2": [(K1, 0.05), (K2, 0.07)],
}


def test_tier_block_reconstructs_exact_means():
    masked, private = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64)
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


def test_tier_block_rejects_wrong_model_and_battery():
    masked, private = _masked_private(
        "sympy", "q25c-3b", BASIC_DELTAS, battery_sha="b" * 64,
        run_overrides={"model": "Qwen/Qwen2.5-Coder-7B"})
    with _expect(V2BError, "does not match tier"):
        lad._tier_block("sympy", "q25c-3b", masked, private, "b" * 64)
    masked, private = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64)
    with _expect(V2BError, "committed q25c-3b battery"):
        lad._tier_block("sympy", "q25c-3b", masked, private, "d" * 64)
    with _expect(V2BError, "dtype/chunk"):
        m2, p2 = _masked_private("sympy", "q25c-3b", BASIC_DELTAS,
                                 battery_sha="b" * 64,
                                 run_overrides={"chunk_tokens": 1024})
        lad._tier_block("sympy", "q25c-3b", m2, p2, "b" * 64)


def test_physlib_forcing_is_inherited():
    masked, private = _masked_private("physlib", "q25c-3b", BASIC_DELTAS,
                                      battery_sha="b" * 64)
    block = lad._tier_block("physlib", "q25c-3b", masked, private, "b" * 64)
    status = block["contrasts"]["E1a"]["interpretation_status"]
    assert status == "uninterpretable-pending-k4x-sensitivity"


def _write(dirname, name, payload):
    path = os.path.join(dirname, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def test_analyze_repo_cross_tier_and_reveal_consistency():
    with tempfile.TemporaryDirectory() as tmp:
        manifest = _write(tmp, "manifest.json", {"stub": "manifest"})
        sample = _write(tmp, "sample.json", {"stub": "sample"})
        candidates = _write(tmp, "candidates.json", {"stub": "candidates"})
        bat_a = _write(tmp, "battery_a.json", {"stub": "battery-a"})
        bat_b = _write(tmp, "battery_b.json", {"stub": "battery-b"})
        sha_a, sha_b = sha256_file(bat_a), sha256_file(bat_b)

        built = {}
        built["q25c-0.5b"] = _masked_private(
            "sympy", "q25c-0.5b", BASIC_DELTAS, battery_sha=sha_a)
        built["q25c-3b"] = _masked_private(
            "sympy", "q25c-3b", BASIC_DELTAS, battery_sha=sha_b)

        def build_fn(complete_path, *_args):
            tag = os.path.basename(complete_path).rsplit(".", 1)[0]
            return built[tag]

        completions = {tag: os.path.join(tmp, f"{tag}.json")
                       for tag in built}
        batteries = {"q25c-0.5b": bat_a, "q25c-3b": bat_b}
        artifact = lad.analyze_repo(
            "sympy", manifest, sample, candidates, completions, batteries,
            build_fn=build_fn)
        assert artifact["schema"] == lad.LADDER_ANALYSIS_SCHEMA
        assert artifact["claim_status"] == lad.LADDER_CLAIM_STATUS
        assert artifact["tier_order"] == ["q25c-0.5b", "q25c-3b"]
        assert artifact["bindings"]["assembly_sha256"] == "a" * 64
        for tag in ("q25c-0.5b", "q25c-3b"):
            mean = artifact["tiers"][tag]["contrasts"]["E1a"][
                "inference"]["target_equal_mean_bpb"]
            assert abs(mean - 0.2) < 1e-12

        # cross-tier assembly drift fails closed
        built["q25c-3b"] = _masked_private(
            "sympy", "q25c-3b", BASIC_DELTAS, battery_sha=sha_b,
            assembly_sha="e" * 64)
        with _expect(V2BError, "different assembly manifests"):
            lad.analyze_repo("sympy", manifest, sample, candidates,
                             completions, batteries, build_fn=build_fn)

        # battery set must cover exactly the completions
        with _expect(V2BError, "cover exactly"):
            lad.analyze_repo("sympy", manifest, sample, candidates,
                             completions, {"q25c-0.5b": bat_a},
                             build_fn=build_fn)

        # sealed tier requires the reveal, and centering must reproduce it
        built = {"q25c-1.5b": _masked_private(
            "sympy", "q25c-1.5b", BASIC_DELTAS, battery_sha=sha_a)}
        completions = {"q25c-1.5b": os.path.join(tmp, "q25c-1.5b.json")}
        batteries = {"q25c-1.5b": bat_a}
        with _expect(V2BError, "supplied together"):
            lad.analyze_repo("sympy", manifest, sample, candidates,
                             completions, batteries, build_fn=build_fn)
        private = built["q25c-1.5b"][1]
        mapping = {name: dict(
            n_rows=row["n_rows"], removed_mean_bpb=row["removed_mean"],
            fsum_correction=row["fsum_correction"],
            total_centering_bpb=row["total_centering"])
            for name, row in private.items()}
        reveal = _write(tmp, "reveal.json", {
            "schema": lad.NLL_EXPLORATORY_REVEAL_SCHEMA,
            "repos": {"sympy": {"mapping": mapping}}})
        artifact = lad.analyze_repo(
            "sympy", manifest, sample, candidates, completions, batteries,
            reveal_path=reveal, build_fn=build_fn)
        assert artifact["tiers"]["q25c-1.5b"]["tier"] == "q25c-1.5b"
        assert artifact["bindings"]["reveal"]["sha256"] == \
            sha256_file(reveal)

        mapping_bad = {name: dict(row) for name, row in mapping.items()}
        mapping_bad["E1a"]["removed_mean_bpb"] = 0.123456
        reveal_bad = _write(tmp, "reveal_bad.json", {
            "schema": lad.NLL_EXPLORATORY_REVEAL_SCHEMA,
            "repos": {"sympy": {"mapping": mapping_bad}}})
        with _expect(V2BError, "centering drift"):
            lad.analyze_repo("sympy", manifest, sample, candidates,
                             completions, batteries,
                             reveal_path=reveal_bad, build_fn=build_fn)


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
