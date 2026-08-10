#!/usr/bin/env python3
"""Dose/k4x consumers: contrast tables, extraction, panels, full stub chain."""
import contextlib
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analyze_v2b_dose as dose  # noqa: E402
import analyze_v2b_nll_ladder as lad  # noqa: E402
import validity_battery as vb  # noqa: E402
from eval_paired import COMPLETE_SCHEMA  # noqa: E402
from v2b_common import V2BError, sha256_file  # noqa: E402

TREE = "t" * 64
LN2 = math.log(2)


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


def test_contrast_tables():
    t = dose.contrast_table("budget", 4096)
    assert t[0] == ("E1a", "k1", "k4:4096", ("k4:4096",))
    assert t[1][1] == "k3:4096" and t[1][3] == ("k3:4096", "k4:4096")
    x = dose.contrast_table("k4x", 65536)
    assert x[0][2] == "k4x:65536" and x[2][1] == "k5:0:65536"


def _cell(bpb, scored=1000, eligible=True):
    nll = bpb * LN2 * scored
    return {"primary": {"nll_nats": nll, "bpb": nll / LN2 / scored},
            "boundary_ledger": {"scored_body_bytes": scored},
            "eligible": eligible}


REF_BPB = {4096: 0.9, 16384: 0.8, 65536: 0.75}


def _cells(ref, index, elig16=True):
    cells = {"k1": _cell(1.0 + index * 1e-4)}
    for b in dose.BUDGETS:
        elig = elig16 if b == 16384 else True
        cells[f"{ref}:{b}"] = _cell(REF_BPB[b], eligible=elig)
        cells[f"k3:{b}"] = _cell(REF_BPB[b] + 0.05, eligible=elig)
        cells[f"k5:0:{b}"] = _cell(REF_BPB[b] + 0.1, eligible=elig)
    return cells


def test_extract_rows_and_deltas():
    key = json.dumps(["ModA", "declX"], separators=(",", ":"))
    rows = dose.extract_rows(_cells("k4", 0), "lean", key,
                             dose.contrast_table("budget", 16384))
    assert abs(rows["E1a"]["delta_bpb"] - 0.2) < 1e-9
    assert abs(rows["E1b"]["delta_bpb"] - 0.05) < 1e-9
    assert abs(rows["E2"]["delta_bpb"] - 0.1) < 1e-9
    rows = dose.extract_rows(_cells("k4", 0, elig16=False), "lean", key,
                             dose.contrast_table("budget", 16384))
    assert rows == {}


def test_build_panel_nesting_and_labels():
    key1 = json.dumps(["ModA", "d1"], separators=(",", ":"))
    key2 = json.dumps(["ModB", "d2"], separators=(",", ":"))
    mk = lambda k, v: dict(target_key=k, module=json.loads(k)[0],
                           delta_bpb=v)
    panel = dose.build_panel("lean", {
        "E1a": [mk(key1, 0.3), mk(key2, 0.1)],
        "E1b": [mk(key1, 0.05)],
        "E2": [mk(key1, 0.1), mk(key2, 0.06)]},
        dose.contrast_table("budget", 16384))
    inf = panel["contrasts"]["E1a"]["inference"]
    assert inf["n_targets"] == 2
    assert abs(inf["target_equal_mean_bpb"] - 0.2) < 1e-12
    assert panel["contrasts"]["E1a"]["orientation"] == "k1-k4:16384"
    with _expect(V2BError, "nesting"):
        dose.build_panel("lean", {
            "E1a": [mk(key1, 0.3)],
            "E1b": [mk(key2, 0.05)],
            "E2": []}, dose.contrast_table("budget", 16384))


def test_force_physlib():
    key1 = json.dumps(["M", "d"], separators=(",", ":"))
    panel = dose.build_panel("lean", {
        "E1a": [dict(target_key=key1, module="M", delta_bpb=0.2)],
        "E1b": [], "E2": []}, dose.contrast_table("budget", 16384))
    forced = dose._force_physlib(panel)
    assert forced["contrasts"]["E1a"]["interpretation_status"] == \
        dose.PHYSLIB_FORCED
    assert forced["e1b_assay"]["label"] == dose.PHYSLIB_FORCED


def _write(dirname, name, payload):
    path = os.path.join(dirname, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


class _Fixture:
    """Stub full-tier chain over a REAL pinned manifest."""

    def __init__(self, tmp, repo, manifest_index, ref):
        self.repo, self.ref = repo, ref
        self.manifest = os.path.join(
            ROOT, "results_v2", "v2b", "assembly",
            f"job19991210_{manifest_index}_{repo}.json")
        self.mvalue = json.load(open(self.manifest))
        self.keys = [t["key"] for t in self.mvalue["targets"]]
        self.sample = _write(tmp, "sample.json", {"stub": 1})
        self.candidates = _write(tmp, "cand.json", {"stub": 2})
        self.batteries, self.completions, ledger_rows = {}, {}, {}
        self.privates = {}
        for tag in sorted(lad.FULL_TIER_SET):
            bat = _write(tmp, vb.PILOT_TIERS[tag]["battery_file"],
                         {"stub": tag})
            self.batteries[tag] = bat
            tree = "s" * 64 if tag == lad.SEALED_TIER else TREE
            comp = _write(tmp, f"complete-{repo}-{tag}.json", {
                "schema": COMPLETE_SCHEMA,
                "generator": {"source_tree_hash": tree},
                "target_artifacts": [
                    {"target_key": k} for k in self.keys]})
            self.completions[tag] = comp
            ledger_rows[tag] = dict(path=os.path.abspath(comp),
                                    sha256=sha256_file(comp),
                                    slurm_job_id="stub")
        self.ledger = {"repos": {repo: ledger_rows},
                       "_binding_sha256": "l" * 64}
        # B* private centering computed through the SAME cell math the
        # analyzer uses, so the exact producer-equality gate holds.
        n = len(self.keys)
        table = dose.contrast_table("budget" if ref == "k4" else "k4x",
                                    16384)
        self.language = "python" if repo in ("sympy", "astropy") else "lean"
        deltas = {name: [] for name in dose.CONTRAST_NAMES}
        for i, key in enumerate(self.keys):
            rows = dose.extract_rows(_cells(ref, i), self.language, key,
                                     table)
            for name, row in rows.items():
                deltas[name].append(row["delta_bpb"])
        self.private = {
            name: dict(n_rows=len(vals),
                       removed_mean=math.fsum(vals) / len(vals),
                       fsum_correction=0.0,
                       total_centering=math.fsum(vals) / len(vals))
            for name, vals in deltas.items()}
        sealed_sha = sha256_file(self.completions[lad.SEALED_TIER])
        self.reveal = {"repos": {repo: {
            "mapping": {name: dict(
                n_rows=row["n_rows"],
                removed_mean_bpb=row["removed_mean"],
                fsum_correction=row["fsum_correction"],
                total_centering_bpb=row["total_centering"])
                for name, row in self.private.items()},
            "bindings": {"completion": {"sha256": sealed_sha}}}}}

    def build_fn(self, complete_path, *_args):
        base = os.path.basename(complete_path)
        tag = base[len(f"complete-{self.repo}-"):-5]
        tier = vb.PILOT_TIERS[tag]
        masked = dict(
            repo=self.repo, language=self.language,
            run_identity=dict(model=tier["model"],
                              revision=tier["revision"], dtype="bfloat16",
                              chunk_tokens=2048,
                              pilot_battery_sha256=sha256_file(
                                  self.batteries[tag])),
            bindings=dict(assembly=dict(sha256="a" * 64),
                          completion=dict(
                              sha256=sha256_file(complete_path)),
                          run_identity_sha256="r" * 64))
        return masked, dict(self.private)

    def load_target_fn(self, row, index, *_args):
        return _cells(self.ref, index)

    def analyze(self, mode, **overrides):
        kwargs = dict(
            mode=mode, repo=self.repo, manifest_path=self.manifest,
            sample_path=self.sample, candidates_path=self.candidates,
            tier_completions=dict(self.completions),
            tier_batteries=dict(self.batteries),
            ledger=self.ledger, reveal=self.reveal,
            build_fn=self.build_fn, load_target_fn=self.load_target_fn,
            expected_scoring_tree=TREE)
        kwargs.update(overrides)
        return dose.analyze_repo(**kwargs)


def test_full_stub_chain_budget_and_k4x():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _Fixture(tmp, "physlib", 2, ref="k4x")
        art = fx.analyze("k4x")
        assert art["schema"] == "v2b_k4x_sensitivity_v1"
        assert art["k4x_external"]["revision"].startswith("81a5d257")
        for tag in lad.FULL_TIER_SET:
            b16 = art["tiers"][tag]["budgets"]["16384"]
            m = b16["contrasts"]["E1a"]["inference"][
                "target_equal_mean_bpb"]
            assert abs(m - (0.2 + 1e-4 * 19 / 2)) < 1e-9
            # no forced status in k4x mode
            assert b16["contrasts"]["E1a"]["interpretation_status"] != \
                dose.PHYSLIB_FORCED
            curve = art["tiers"][tag]["common_subset_e1a"]["curve"]
            assert abs(curve["4096"]["mean_bpb"]
                       - (0.1 + 1e-4 * 19 / 2)) < 1e-9
            assert abs(curve["65536"]["mean_bpb"]
                       - (0.25 + 1e-4 * 19 / 2)) < 1e-9

        with _expect(V2BError, "PhysLib-only"):
            fx2 = _Fixture(tmp, "sympy", 3, ref="k4")
            fx2.analyze("k4x")

        fx3 = _Fixture(tmp, "physlib", 2, ref="k4")
        art3 = fx3.analyze("budget")
        b16 = art3["tiers"]["q25c-3b"]["budgets"]["16384"]
        assert b16["contrasts"]["E1a"]["interpretation_status"] == \
            dose.PHYSLIB_FORCED  # forced in budget mode

        # B* producer-consistency check trips on doctored private
        fx4 = _Fixture(tmp, "physlib", 2, ref="k4")
        fx4.private = dict(fx4.private,
                           E1a=dict(fx4.private["E1a"], removed_mean=0.5))
        fx4.reveal["repos"]["physlib"]["mapping"]["E1a"][
            "removed_mean_bpb"] = 0.5
        with _expect(V2BError, "differs from the B3 producer"):
            fx4.analyze("budget")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("DOSE CONSUMER TESTS PASS")
