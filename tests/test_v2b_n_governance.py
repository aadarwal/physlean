#!/usr/bin/env python3
"""Synthetic tests for §15.A14 blind N governance: exact MoM variance
components on unequal clusters, the all-singleton fallback, the frozen
t table, deterministic projection through the frozen plan machinery
with pilot exclusion, the underfilled-N refusal, the hardened masked
contract (metric/budget/bindings/family ids/pilot arity), and output
blindness (no means, signs, or deltas ever leave). No outcome, score,
or cluster artifact is read.
Run: python3 tests/test_v2b_n_governance.py"""
import hashlib
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import v2b_n_governance as governance
from finalize_v2a import EVIDENCE_SOURCE_COMMIT
from finalize_v2b_a6 import EXPECTED
from layout import PAIRED_SCHEMA_VERSION
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA,
                        MASKED_DELTAS_SCHEMA, V2BError, identity_key,
                        sha256_json, sha256_sorted_json)
from v2b_lean_boundaries import BOUNDARIES_SCHEMA
from v2b_metadata import (COHORT_CUTOFF, SAMPLING_SEED, build_sample_plan,
                          cohort_of, seeded_hash, tercile,
                          tercile_cutpoints)
from v2b_n_governance import (HALFWIDTH_TARGET, N_MAX, N_MIN,
                              PAIRED_COMPLETE_SCHEMA,
                              SALT_COMMITMENT_SCHEMA, T_0975_BY_DF,
                              _require_provenance, analyze,
                              family_governance, projected_halfwidth,
                              variance_components)

PRE = "2023-05-01T00:00:00+00:00"
FIDS = tuple("fam-" + hashlib.sha256(str(i).encode()).hexdigest()[:16]
             for i in range(3))


def test_variance_components_mom_exact():
    out = variance_components({"A": [1.0, 2.0, 3.0], "B": [2.0, 4.0]})
    assert out["mode"] == "mom"
    assert out["n_pilot"] == 5 and out["n_modules"] == 2
    assert abs(out["msw"] - 4.0 / 3.0) < 1e-12
    assert abs(out["msb"] - 1.2) < 1e-12
    assert abs(out["n0"] - 2.4) < 1e-12
    assert out["sigma_b2"] == 0.0                # MSB < MSW truncates
    out = variance_components({"A": [0.0, 0.0], "B": [10.0, 10.0]})
    assert out["sigma_w2"] == 0.0
    assert abs(out["sigma_b2"] - 50.0) < 1e-12   # (100-0)/2


def test_variance_components_fallbacks():
    out = variance_components({"A": [1.0], "B": [3.0], "C": [5.0]})
    assert out["mode"] == "all-singleton-conservative"
    assert out["sigma_w2"] == 0.0
    assert abs(out["sigma_b2"] - 4.0) < 1e-12    # ddof=1 variance
    assert variance_components({"A": [1.0, 2.0]})["mode"] == \
        "insufficient-clusters"
    assert variance_components({"A": [1.0]})["mode"] == \
        "insufficient-clusters"
    try:
        variance_components({"A": [float("nan")], "B": [1.0]})
        assert False, "non-finite delta accepted"
    except V2BError:
        pass


def test_projected_halfwidth_frozen_t():
    assert abs(T_0975_BY_DF[1] - 12.706205) < 1e-6
    assert abs(T_0975_BY_DF[19] - 2.093024) < 1e-6
    # sigma_b2=50, sigma_w2=0, sizes [2,2]: var = 50*8/16 = 25
    hw = projected_halfwidth(50.0, 0.0, [2, 2], 1)
    assert abs(hw - 12.706205 * 5.0) < 1e-6
    # SUPPLEMENT_DF_EXTENSION_AMENDMENT: df>=20 breakpoints are IN the
    # table now (exact hits); a df between breakpoints has no entry in
    # THIS module's exact-lookup helper and still refuses, while df<1
    # refuses always.
    assert abs(T_0975_BY_DF[20] - 2.085963) < 1e-6
    assert abs(T_0975_BY_DF[120] - 1.979930) < 1e-6
    hw20 = projected_halfwidth(50.0, 0.0, [2, 2], 20)
    assert abs(hw20 - 2.085963 * 5.0) < 1e-6
    try:
        projected_halfwidth(1.0, 1.0, [2, 2], 21)
        assert False, "df without an exact entry accepted"
    except V2BError:
        pass
    try:
        projected_halfwidth(1.0, 1.0, [], 3)
        assert False, "empty module sizes accepted"
    except V2BError:
        pass


def test_family_governance_smallest_feasible_n_and_underfill():
    rows = []
    for g in range(4):
        for i in range(5):
            key = identity_key("lean", [f"Mod{g}", f"Mod{g}.d{i}"])
            rows.append([key, float(g) + 0.1 * i])
    # growing singleton pools: var = (sb2+sw2)/N exactly; first ten N
    # are underfilled and must be skipped, never silently projected
    sizes_by_n = {n: (None if n < N_MIN + 10 else [1] * n)
                  for n in range(N_MIN, N_MAX + 1)}
    out = family_governance(rows, sizes_by_n)
    assert out["mode"] == "mom" and out["df"] == 3
    assert out["halfwidths_by_n"][str(N_MIN)] is None
    components = variance_components(
        {f"Mod{g}": [float(g) + 0.1 * i for i in range(5)]
         for g in range(4)})
    expected = None
    for n in range(N_MIN + 10, N_MAX + 1):
        hw = T_0975_BY_DF[3] * math.sqrt(
            (components["sigma_b2"] + components["sigma_w2"]) / n)
        if hw <= HALFWIDTH_TARGET:
            expected = n
            break
    assert out["chosen_n"] == expected
    assert out["verdict"] == ("feasible" if expected else "infeasible")
    # insufficient clusters propagates as a verdict, never a default
    single = [[identity_key("lean", ["OneMod", f"OneMod.d{i}"]),
               float(i)] for i in range(5)]
    out = family_governance(single, sizes_by_n)
    assert out["verdict"] == "insufficient-clusters"
    assert out["chosen_n"] is None


def _gov_candidates(td, repo="mathlib4", n_targets=430):
    language, corpus_sha = EXPECTED[repo]
    boundary = dict(path="/x/mathlib4-boundaries.json",
                    sha256="8" * 64, schema=BOUNDARIES_SCHEMA)
    targets = []
    for index in range(n_targets):
        identity = [f"Mod{index % 4}", f"Mod{index % 4}.t{index}"]
        targets.append(dict(
            identity=identity, body_bytes=40 + 17 * index,
            span_id=f"{index + 1:064x}",
            module_in_degree=index % 7,
            source_rel=f"src/u{index}.lean",
            first_add=dict(timestamp_utc=PRE,
                           provenance_mode="exact-add",
                           exact_add_unresolved=False, n_add_records=1)))
    cuts_len = tercile_cutpoints([t["body_bytes"] for t in targets])
    cuts_deg = tercile_cutpoints([t["module_in_degree"] for t in targets])
    for t in targets:
        cohort = cohort_of(t["first_add"])
        lt = tercile(t["body_bytes"], *cuts_len)
        ct = tercile(t["module_in_degree"], *cuts_deg)
        t["cohort"] = cohort
        t["strata"] = dict(length_tercile=lt, centrality_tercile=ct,
                           cohort=cohort)
        t["cell"] = f"L{lt}-D{ct}-C{cohort}"
        t["priority"] = seeded_hash(SAMPLING_SEED, repo, *t["identity"])
    value = dict(
        schema=CANDIDATES_SCHEMA, repo=repo, language=language,
        corpus_git_sha=corpus_sha, git_version="git version 2.44.0",
        cohort_cutoff=COHORT_CUTOFF.isoformat(),
        tercile_cutpoints=dict(body_bytes=list(cuts_len),
                               module_in_degree=list(cuts_deg)),
        first_add_provenance_file_counts={
            "exact-add": len(targets), "no-add-pre-witness": 0},
        no_add_pre_witness_files=[],
        lean_boundaries=boundary,
        n_candidates=len(targets), targets=targets,
        structural_evidence=dict(
            evidence_source_commit=EVIDENCE_SOURCE_COMMIT,
            cohort=dict(path="/x/cohort.json", sha256="c" * 64,
                        schema="v2a_structural_cohort_v1"),
            lean_boundaries=boundary),
        generator=dict(source_commit="a" * 40, source_tree_hash="b" * 64,
                       program="prepare_v2b_candidates.py"))
    path = os.path.join(td, "candidates.json")
    json.dump(value, open(path, "w"), sort_keys=True)
    return path, value, targets


def test_build_sample_plan_pilot_exclusion():
    with tempfile.TemporaryDirectory() as td:
        _, value, targets = _gov_candidates(td, n_targets=30)
        pilot = frozenset(
            identity_key("lean", t["identity"]) for t in targets[:6])
        plan = build_sample_plan(value, 10, exclude_keys=pilot)
        assert plan["n_excluded"] == 6
        assert plan["excluded_keys_sha256"] == sha256_json(sorted(pilot))
        chosen = {identity_key("lean", row["identity"])
                  for row in plan["targets"]}
        assert not chosen & pilot                # never selected
        # original cutpoints still validate against the FULL table
        baseline = build_sample_plan(value, 10)
        assert baseline["n_excluded"] == 0
        # an exclusion key absent from the table refuses
        try:
            build_sample_plan(value, 10, exclude_keys=frozenset(
                [identity_key("lean", ["Ghost", "Ghost.x"])]))
            assert False, "foreign exclusion key accepted"
        except V2BError as err:
            assert "absent" in str(err)


def _gov_chain(td, deltas_fn, n_targets=430, fids=FIDS):
    candidates_path, value, targets = _gov_candidates(
        td, n_targets=n_targets)
    candidates_sha = hashlib.sha256(
        open(candidates_path, "rb").read()).hexdigest()
    # the REAL frozen pilot draw, exactly as finalize_v2b_sample seals it
    plan = build_sample_plan(value, 20)
    plan["candidates_sha256"] = candidates_sha
    pilot_rows = plan["targets"]
    sample = dict(schema=BOUND_SAMPLE_SCHEMA, sampling_state="drawn",
                  plans={value["repo"]: plan})
    sample_path = os.path.join(td, "sample.json")
    json.dump(sample, open(sample_path, "w"))
    sample_sha = hashlib.sha256(
        open(sample_path, "rb").read()).hexdigest()
    rows = [[identity_key("lean", t["identity"]),
             deltas_fn(index, t)] for index, t in enumerate(pilot_rows)]
    assembly_sha = "a" * 64
    run_identity = dict(paired_schema_version=PAIRED_SCHEMA_VERSION,
                        manifest_sha256=assembly_sha, model="m",
                        revision="1" * 40, dtype="bfloat16")
    run_sha = sha256_sorted_json(run_identity)
    scored_generator = dict(source_commit="d" * 40,
                            source_tree_hash="e" * 64,
                            program="eval_paired.py")
    complete = dict(schema=PAIRED_COMPLETE_SCHEMA, repo=value["repo"],
                    language=value["language"],
                    corpus_git_sha=value["corpus_git_sha"],
                    assembly_manifest=dict(sha256=assembly_sha),
                    run_identity=run_identity,
                    run_identity_sha256=run_sha,
                    generator=scored_generator)
    complete_path = os.path.join(td, "complete.json")
    json.dump(complete, open(complete_path, "w"))
    complete_sha = hashlib.sha256(
        open(complete_path, "rb").read()).hexdigest()
    commitment_path = os.path.join(td, "commitment.json")
    salt_sha = "3" * 64
    json.dump(dict(schema=SALT_COMMITMENT_SCHEMA,
                   state="committed-pre-score", salt_sha256=salt_sha),
              open(commitment_path, "w"))
    commitment_sha = hashlib.sha256(
        open(commitment_path, "rb").read()).hexdigest()
    families = {fid: rows for fid in fids}
    masked = dict(
        schema=MASKED_DELTAS_SCHEMA, repo=value["repo"],
        language=value["language"],
        corpus_git_sha=value["corpus_git_sha"],
        metric="bpb", budget_bytes=16384,
        run_identity=run_identity,
        bindings=dict(
            sample=dict(sha256=sample_sha),
            candidates=dict(sha256=candidates_sha),
            assembly=dict(sha256=assembly_sha, schema=ASSEMBLY_SCHEMA),
            completion=dict(sha256=complete_sha,
                            schema=PAIRED_COMPLETE_SCHEMA),
            run_identity_sha256=run_sha,
            salt_commitment=dict(path=commitment_path,
                                 sha256=commitment_sha,
                                 schema=SALT_COMMITMENT_SCHEMA,
                                 salt_sha256=salt_sha)),
        n_rows_by_family={fid: len(rows) for fid in fids},
        families=families,
        generator=dict(source_commit=scored_generator["source_commit"],
                       source_tree_hash=scored_generator["source_tree_hash"],
                       program="prepare_v2b_masked_deltas.py"))
    masked_path = os.path.join(td, "masked.json")
    json.dump(masked, open(masked_path, "w"))
    return masked_path, candidates_path, sample_path, complete_path


def test_analyze_end_to_end_blind_and_deterministic():
    with tempfile.TemporaryDirectory() as td:
        # identical deltas: zero variance, feasible at N_MIN
        secret = 0.123456789
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: secret)
        artifact = analyze(masked, candidates, sample, complete)
        assert artifact["verdict"] == "feasible"
        assert artifact["repo_n"] == N_MIN
        assert artifact["pilot_exclusion"]["n_excluded"] == 20
        assert artifact["n_families"] == 3
        for fid in FIDS:
            assert artifact["families"][fid]["chosen_n"] == N_MIN
        # BLINDNESS: no means/signs/deltas anywhere in the output
        dumped = json.dumps(artifact)
        assert "0.123456789" not in dumped
        for banned in ("\"mean", "\"sign", "\"delta", "\"effect"):
            assert banned not in dumped
        # determinism
        assert sha256_json(artifact) == \
            sha256_json(analyze(masked, candidates, sample, complete))


def test_analyze_infeasible_and_mixed_verdicts():
    with tempfile.TemporaryDirectory() as td:
        # enormous between-module spread: no N in range reaches 0.02
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 1000.0 * (index % 4))
        artifact = analyze(masked, candidates, sample, complete)
        assert artifact["verdict"] == "infeasible"
        assert artifact["repo_n"] is None
        assert artifact["families"][FIDS[0]]["verdict"] == "infeasible"
    with tempfile.TemporaryDirectory() as td:
        # one infeasible family among three: repo stays infeasible
        masked, candidates, sample, complete = _gov_chain(td, lambda index, t: 0.0)
        value = json.load(open(masked))
        value["families"][FIDS[2]] = [
            [row[0], 1000.0 * (index % 4)] for index, row in
            enumerate(value["families"][FIDS[2]])]
        json.dump(value, open(masked, "w"))
        artifact = analyze(masked, candidates, sample, complete)
        assert artifact["families"][FIDS[0]]["verdict"] == "feasible"
        assert artifact["families"][FIDS[2]]["verdict"] == "infeasible"
        assert artifact["verdict"] == "infeasible"
        assert artifact["repo_n"] is None


def test_analyze_underfilled_pool_is_infeasible():
    with tempfile.TemporaryDirectory() as td:
        # 60 candidates - 20 pilot = 40 < N_MIN: every N underfills, the
        # projection never returns requested N over a smaller denominator
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0, n_targets=60)
        artifact = analyze(masked, candidates, sample, complete)
        assert artifact["verdict"] == "infeasible"
        family = artifact["families"][FIDS[0]]
        assert family["chosen_n"] is None
        assert family["halfwidths_by_n"][str(N_MIN)] is None
        assert family["halfwidths_by_n"][str(N_MAX)] is None


def test_analyze_hardened_contract_refusals():
    def tamper(td, mutate, expect):
        masked, candidates, sample, complete = _gov_chain(td, lambda index, t: 0.0)
        value = json.load(open(masked))
        mutate(value)
        json.dump(value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, expect
        except V2BError as err:
            assert expect in str(err)

    with tempfile.TemporaryDirectory() as td:
        tamper(td, lambda v: v.update(metric="bpc"), "metric")
    with tempfile.TemporaryDirectory() as td:
        tamper(td, lambda v: v.update(budget_bytes=4096), "metric")
    with tempfile.TemporaryDirectory() as td:
        tamper(td, lambda v: v["families"].pop(FIDS[0]), "families")
    with tempfile.TemporaryDirectory() as td:
        tamper(td, lambda v: v["families"].update(
            {"fam-XYZ": v["families"].pop(FIDS[0])}), "families")
    with tempfile.TemporaryDirectory() as td:
        tamper(td, lambda v: v["bindings"]["candidates"].update(
            sha256="0" * 64), "bound to this exact")
    # non-pilot delta target refuses
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(td, lambda index, t: 0.0)
        value = json.load(open(masked))
        value["families"][FIDS[0]].append(
            [identity_key("lean", ["Ghost", "Ghost.x"]), 0.5])
        json.dump(value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, "non-pilot delta target accepted"
        except V2BError as err:
            assert "non-pilot" in str(err)
    # a tampered sample plan is NOT the frozen deterministic pilot draw,
    # even when the caller re-stamps every hash consistently
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(td, lambda index, t: 0.0)
        value = json.load(open(sample))
        value["plans"]["mathlib4"]["targets"] = \
            value["plans"]["mathlib4"]["targets"][:19]
        value["plans"]["mathlib4"]["n_selected"] = 19
        json.dump(value, open(sample, "w"))
        masked_value = json.load(open(masked))
        masked_value["families"] = {
            fid: rows[:19] for fid, rows in
            masked_value["families"].items()}
        masked_value["bindings"]["sample"]["sha256"] = hashlib.sha256(
            open(sample, "rb").read()).hexdigest()
        json.dump(masked_value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, "self-consistent tampered pilot plan accepted"
        except V2BError as err:
            assert "frozen deterministic pilot draw" in str(err)


def test_analyze_b3_chain_refusals():
    def masked_tamper(td, mutate, expect):
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        value = json.load(open(masked))
        mutate(value)
        json.dump(value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, expect
        except V2BError as err:
            assert expect in str(err)

    # forged completion binding: tampered completion no longer matches
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        value = json.load(open(complete))
        value["n_targets"] = 99
        json.dump(value, open(complete, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, "unbound completion accepted"
        except V2BError as err:
            assert "does not match" in str(err)
    # run identity restamped consistently in masked only: the completion
    # cross-check still refuses
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        value = json.load(open(masked))
        value["run_identity"]["model"] = "forged"
        value["bindings"]["run_identity_sha256"] = sha256_sorted_json(
            value["run_identity"])
        json.dump(value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, "forged run identity accepted"
        except V2BError as err:
            assert "does not match" in str(err)
    # internal chain: run identity must name the assembly binding
    with tempfile.TemporaryDirectory() as td:
        def broken_manifest(value):
            value["run_identity"]["manifest_sha256"] = "9" * 64
            value["bindings"]["run_identity_sha256"] = sha256_sorted_json(
                value["run_identity"])
        masked_tamper(td, broken_manifest, "binding chain")
    with tempfile.TemporaryDirectory() as td:
        masked_tamper(td, lambda v: v["n_rows_by_family"].update(
            {FIDS[0]: 7}), "n_rows_by_family")
    with tempfile.TemporaryDirectory() as td:
        masked_tamper(td, lambda v: v["generator"].update(
            program="other.py"), "masked generator")
    with tempfile.TemporaryDirectory() as td:
        masked_tamper(td, lambda v: v["generator"].update(
            source_commit="9" * 40), "source identities")
    with tempfile.TemporaryDirectory() as td:
        masked_tamper(td, lambda v: v.update(language="python"),
                      "language/corpus")
    # completion generator drift, with the masked binding re-stamped
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        value = json.load(open(complete))
        value["generator"]["program"] = "other.py"
        json.dump(value, open(complete, "w"))
        masked_value = json.load(open(masked))
        masked_value["bindings"]["completion"]["sha256"] = hashlib.sha256(
            open(complete, "rb").read()).hexdigest()
        json.dump(masked_value, open(masked, "w"))
        try:
            analyze(masked, candidates, sample, complete)
            assert False, "completion generator drift accepted"
        except V2BError as err:
            assert "completion generator" in str(err)
    # the committed salt bytes/digest must equal the public masked binding,
    # not merely occupy some committed-looking path
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        masked_value = json.load(open(masked))
        saved_require = governance.require_committed
        saved_tree = governance.source_tree_hash
        governance.require_committed = lambda path: None
        governance.source_tree_hash = lambda: "e" * 64
        try:
            _require_provenance(masked, masked_value)
            salt_path = masked_value["bindings"]["salt_commitment"]["path"]
            salt_value = json.load(open(salt_path))
            salt_value["salt_sha256"] = "8" * 64
            json.dump(salt_value, open(salt_path, "w"))
            try:
                _require_provenance(masked, masked_value)
                assert False, "drifted committed salt bytes accepted"
            except V2BError as err:
                assert "salt artifact" in str(err)
        finally:
            governance.require_committed = saved_require
            governance.source_tree_hash = saved_tree
    # committed-boundary seam: an uncommitted masked artifact refuses in
    # the production provenance gate
    with tempfile.TemporaryDirectory() as td:
        masked, candidates, sample, complete = _gov_chain(
            td, lambda index, t: 0.0)
        masked_value = json.load(open(masked))
        try:
            _require_provenance(masked, masked_value)
            assert False, "uncommitted masked artifact accepted"
        except V2BError as err:
            assert "commit" in str(err).lower()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B N GOVERNANCE TESTS PASS")
