#!/usr/bin/env python3
"""Synthetic, outcome-free tests for the exploratory NLL analyzer."""
import copy
import hashlib
import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analyze_v2b_nll_exploratory import (
    ANALYSIS_AMENDMENT_PATH,
    ANALYSIS_AMENDMENT_HISTORY,
    ANALYSIS_AMENDMENT_SHA256,
    ANALYSIS_IMPLEMENTATION_FREEZE_PATH,
    ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA,
    ANALYSIS_SOURCE_FILES,
    BASE,
    CLAIM_STATUS,
    CONTRAST_NAMES,
    FORMAL_STATUS,
    IMPLEMENTATION_FREEZE_SCHEMA,
    NLL_BLIND_STATUS,
    NLL_EXPLORATORY_REVEAL_SCHEMA,
    PILOT_MODEL,
    PILOT_REVISION,
    REPLAY_SOURCE_TREE_SHA256,
    REVEAL_AMENDMENT_ADOPTION_COMMIT,
    REVEAL_AMENDMENT_PATH,
    REVEAL_AMENDMENT_SHA256,
    REVEAL_FREEZE_ADOPTION_COMMIT,
    REVEAL_IMPLEMENTATION_COMMIT,
    REVEAL_IMPLEMENTATION_FREEZE_PATH,
    REVEAL_IMPLEMENTATION_FREEZE_SHA256,
    REVEAL_SOURCE_TREE_SHA256,
    REVEAL_STATE,
    SALT_ALGORITHM,
    T_095_BY_DF,
    _analysis_amendment_binding,
    _analysis_implementation_freeze_binding,
    _analyze_repo_rows,
    _capture_input_ledger,
    _inference,
    _ledger_entry,
    _reveal_amendment_binding,
    _reveal_implementation_freeze_binding,
    _replay_repo,
    analyze_value,
    holm_adjust,
    prepare,
    student_t_cdf,
    student_t_sf,
)
from finalize_v2b_a6 import EXPECTED
from provenance import source_tree_hash
from v2b_common import (MASKED_DELTAS_SCHEMA, N_GOVERNANCE_SCHEMA,
                        V2BError, artifact_binding, sha256_file,
                        write_new_json)


def _row(index, base):
    module_index = index // 2
    module_effect = (module_index - 2) * 0.001
    within = -0.0002 if index % 2 == 0 else 0.0002
    return dict(target_key=f"target-{index:02d}",
                module=f"module-{module_index}",
                delta_bpb=base + module_effect + within)


def _replayed(repo):
    return dict(
        language="lean" if repo not in ("sympy", "astropy") else "python",
        model=PILOT_MODEL, revision=PILOT_REVISION,
        run_identity_sha256="4" * 64,
        governance_verdict="feasible", governance_repo_n=240,
        bindings=dict(masked={}, governance={}, completion={}),
        families=dict(
            E1a=[_row(index, 0.05) for index in range(10)],
            E1b=[_row(index, 0.00) for index in range(8)],
            E2=[_row(index, 0.04) for index in range(2, 10)]))


def _mapping(index):
    return dict(fid=f"fam-{index:016x}", sign=1, n_rows=0,
                removed_mean_bpb=None, fsum_correction=None,
                total_centering_bpb=None)


def _reveal_provenance_bindings():
    amendment = dict(
        path=os.path.abspath(os.path.join(BASE, REVEAL_AMENDMENT_PATH)),
        sha256=REVEAL_AMENDMENT_SHA256,
        adoption_commit=REVEAL_AMENDMENT_ADOPTION_COMMIT)
    freeze = dict(
        path=os.path.abspath(os.path.join(
            BASE, REVEAL_IMPLEMENTATION_FREEZE_PATH)),
        sha256=REVEAL_IMPLEMENTATION_FREEZE_SHA256,
        schema=IMPLEMENTATION_FREEZE_SCHEMA,
        adoption_commit=REVEAL_FREEZE_ADOPTION_COMMIT,
        implementation_commit=REVEAL_IMPLEMENTATION_COMMIT,
        source_tree_sha256=REVEAL_SOURCE_TREE_SHA256)
    return amendment, freeze


def _synthetic_reveal():
    salt = bytes(range(32))
    commitment = dict(
        path="/synthetic/commitment.json", sha256="1" * 64,
        schema="v2b_salt_commitment_v1",
        salt_sha256=hashlib.sha256(salt).hexdigest())
    repos = {}
    for repo in EXPECTED:
        repos[repo] = dict(
            repo=repo,
            bindings=dict(
                masked=dict(path=f"/synthetic/{repo}-masked.json",
                            sha256="2" * 64,
                            schema=MASKED_DELTAS_SCHEMA),
                governance=dict(path=f"/synthetic/{repo}-governance.json",
                                sha256="3" * 64,
                                schema=N_GOVERNANCE_SCHEMA),
                completion=dict(path=f"/synthetic/{repo}-complete.json",
                                sha256="4" * 64,
                                schema="v2b_paired_nll_complete_v2")),
            governance_verdict="feasible", governance_repo_n=240,
            mapping={name: _mapping(index)
                     for index, name in enumerate(CONTRAST_NAMES)},
            reconstructed_equal=True)
    reveal_amendment, reveal_freeze = _reveal_provenance_bindings()
    return dict(
        schema=NLL_EXPLORATORY_REVEAL_SCHEMA, state=REVEAL_STATE,
        claim_status=CLAIM_STATUS, formal_v2b_status=FORMAL_STATUS,
        nll_blind_status=NLL_BLIND_STATUS,
        behavioral_status=(
            "not-governed-not-a-co-primary-fresh-confirmatory-sample-required"),
        algorithm=SALT_ALGORITHM, salt_commitment=commitment,
        revealed_salt_hex=salt.hex(), repos=repos,
        prospective_amendment=reveal_amendment,
        implementation_freeze=reveal_freeze,
        replay_source_tree_sha256=REPLAY_SOURCE_TREE_SHA256,
        generator=dict(source_commit="9" * 40,
                       source_tree_hash=REVEAL_SOURCE_TREE_SHA256,
                       program="finalize_v2b_nll_exploratory_reveal.py"))


def _analysis_bindings():
    amendment = dict(path=os.path.abspath(os.path.join(
                         BASE, ANALYSIS_AMENDMENT_PATH)),
                     sha256=ANALYSIS_AMENDMENT_SHA256,
                     adoption_commits=list(ANALYSIS_AMENDMENT_HISTORY))
    freeze = dict(
        path=os.path.abspath(os.path.join(
            BASE, ANALYSIS_IMPLEMENTATION_FREEZE_PATH)), sha256="b" * 64,
        schema=ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA,
        adoption_commit="c" * 40, implementation_commit="d" * 40,
        source_tree_sha256="e" * 64)
    reveal = dict(path="/synthetic/reveal.json", sha256="f" * 64,
                  schema=NLL_EXPLORATORY_REVEAL_SCHEMA)
    return reveal, amendment, freeze


def test_frozen_quantiles_and_student_t_fixed_vectors():
    expected = (
        6.313751515, 2.919985580, 2.353363435, 2.131846786,
        2.015048373, 1.943180281, 1.894578605, 1.859548038,
        1.833112933, 1.812461123, 1.795884819, 1.782287556,
        1.770933396, 1.761310136, 1.753050356, 1.745883676,
        1.739606726, 1.734063607, 1.729132812)
    assert tuple(T_095_BY_DF[df] for df in range(1, 20)) == expected
    vectors = (
        (1, -3.0, 0.10241638234956672),
        (1, 1.0, 0.75), (2, 2.0, 0.908248290463863),
        (3, -0.5, 0.32572398242407552),
        (5, 1.5, 0.9030481598787634),
        (10, 4.0, 0.9987408336876317),
        (19, -2.25, 0.018246669314484095))
    for df, value, expected_cdf in vectors:
        assert abs(student_t_cdf(value, df) - expected_cdf) < 2e-13
        assert abs(student_t_sf(value, df) - (1.0 - expected_cdf)) < 2e-13


def test_inference_fail_closed_and_scale_aware_degeneracy():
    empty = _inference([])
    assert empty["inference_status"] == "insufficient-clusters"
    assert empty["ci95_two_sided_bpb"] is None
    one_module = _inference([
        dict(target_key="a", module="m", delta_bpb=0.0),
        dict(target_key="b", module="m", delta_bpb=1.0)])
    assert one_module["inference_status"] == "insufficient-clusters"
    tiny = _inference([
        dict(target_key="a", module="m1", delta_bpb=0.04),
        dict(target_key="b", module="m2", delta_bpb=0.04 + 1e-15)])
    assert tiny["inference_status"] == "degenerate-zero-se"
    assert tiny["ci95_two_sided_bpb"] is None
    assert tiny["lower_one_sided_95_bpb"] is None
    available = _inference([_row(index, 0.04) for index in range(8)])
    assert available["inference_status"] == "available"
    assert available["cluster_sizes"] == [2, 2, 2, 2]


def test_holm_iut_intersections_labels_and_physlib_override():
    holm = holm_adjust({"E2": 0.02, "E1b": 0.01, "E1a": 0.01})
    assert holm["order"] == ["E1a", "E1b", "E2"]
    assert holm["adjusted_pvalues"] == {
        "E1a": 0.03, "E1b": 0.03, "E2": 0.03}
    result = _analyze_repo_rows("mathlib4", _replayed("mathlib4"))
    assert result["eligible_intersections"]["pairwise"]["E1a&E1b"]["n"] == 8
    assert result["eligible_intersections"]["pairwise"]["E1a&E2"]["n"] == 8
    assert result["eligible_intersections"]["pairwise"]["E1b&E2"]["n"] == 6
    assert result["three_way_n"] == 6
    assert result["e1b_assay"]["label"] == \
        "interface-sufficiency-compatible-exploratory"
    assert result["e1b_assay"]["interface_compatible"] is True
    physlib = _analyze_repo_rows("physlib", _replayed("physlib"))
    assert physlib["e1b_assay"]["label"] == \
        "uninterpretable-pending-k4x-sensitivity"
    assert physlib["e1b_assay"]["interface_compatible"] is False
    assert physlib["contrasts"]["E1a"][
        "exploratory_positive_model_based_diagnostic"] is False


def test_exact_five_reveal_schema_provenance_and_determinism():
    reveal = _synthetic_reveal()
    reveal_binding, amendment, freeze = _analysis_bindings()
    reveal_amendment, reveal_freeze = _reveal_provenance_bindings()

    def replay(repo, *_args):
        return _replayed(repo)

    first = analyze_value(
        reveal, reveal_binding, amendment, freeze,
        reveal_amendment, reveal_freeze, replay_repo_fn=replay,
        ancestor_fn=lambda _older, _newer: True)
    second = analyze_value(copy.deepcopy(reveal), reveal_binding,
                           copy.deepcopy(amendment), copy.deepcopy(freeze),
                           copy.deepcopy(reveal_amendment),
                           copy.deepcopy(reveal_freeze),
                           replay_repo_fn=replay,
                           ancestor_fn=lambda _older, _newer: True)
    assert json.dumps(first, sort_keys=True, allow_nan=False) == \
        json.dumps(second, sort_keys=True, allow_nan=False)
    assert list(first["repos"]) == sorted(EXPECTED)
    assert first["bindings"]["prospective_analysis_amendment"] == amendment
    broken = copy.deepcopy(reveal)
    broken["repos"].pop(next(iter(broken["repos"])))
    try:
        analyze_value(
            broken, reveal_binding, amendment, freeze,
            reveal_amendment, reveal_freeze, replay_repo_fn=replay,
            ancestor_fn=lambda _older, _newer: True)
        assert False, "missing repository was accepted"
    except V2BError as err:
        assert "five-corpus" in str(err)


def test_reveal_provenance_and_analysis_before_reveal_ancestry():
    reveal_amendment, reveal_freeze = _reveal_provenance_bindings()
    assert _reveal_amendment_binding() == reveal_amendment
    assert _reveal_implementation_freeze_binding() == reveal_freeze
    reveal = _synthetic_reveal()
    reveal_binding, amendment, freeze = _analysis_bindings()

    def replay(repo, *_args):
        return _replayed(repo)

    for blocked_commit in (freeze["implementation_commit"],
                           freeze["adoption_commit"]):
        try:
            analyze_value(
                reveal, reveal_binding, amendment, freeze,
                reveal_amendment, reveal_freeze,
                replay_repo_fn=replay,
                ancestor_fn=lambda older, _newer, blocked=blocked_commit:
                older != blocked)
            assert False, "post-reveal analysis commit was accepted"
        except V2BError as err:
            assert "ancestry" in str(err)

    bad_tree = copy.deepcopy(reveal)
    bad_tree["generator"]["source_tree_hash"] = "0" * 64
    try:
        analyze_value(
            bad_tree, reveal_binding, amendment, freeze,
            reveal_amendment, reveal_freeze, replay_repo_fn=replay,
            ancestor_fn=lambda _older, _newer: True)
        assert False, "unauthenticated reveal generator tree was accepted"
    except V2BError as err:
        assert "generator" in str(err)

    bad_echo = copy.deepcopy(reveal)
    bad_echo["implementation_freeze"]["sha256"] = "0" * 64
    try:
        analyze_value(
            bad_echo, reveal_binding, amendment, freeze,
            reveal_amendment, reveal_freeze, replay_repo_fn=replay,
            ancestor_fn=lambda _older, _newer: True)
        assert False, "drifted reveal freeze echo was accepted"
    except V2BError as err:
        assert "freeze" in str(err)


def test_analysis_freeze_allows_exact_restored_blobs_after_reveal():
    with tempfile.TemporaryDirectory() as td:
        implementation = "d" * 40
        adoption = "c" * 40
        later_restore = "f" * 40
        files = {path: sha256_file(os.path.join(BASE, path))
                 for path in ANALYSIS_SOURCE_FILES}
        freeze_path = os.path.join(td, "analysis-freeze.json")
        write_new_json(freeze_path, dict(
            schema=ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA,
            state="frozen-before-nll-reveal",
            implementation_commit=implementation,
            source_tree_sha256=source_tree_hash(),
            analysis_amendment=dict(
                sha256=ANALYSIS_AMENDMENT_SHA256,
                adoption_commits=list(ANALYSIS_AMENDMENT_HISTORY)),
            files=files))

        def history(path):
            if os.path.abspath(path) == os.path.abspath(freeze_path):
                return [adoption]
            return [later_restore, implementation]

        binding = _analysis_implementation_freeze_binding(
            path=freeze_path,
            require_committed_fn=lambda path: dict(
                path=path, sha256=sha256_file(path)),
            history_fn=history,
            commit_file_sha_fn=lambda _commit, path: files[path],
            ancestor_fn=lambda _older, _newer: True)
        assert binding["implementation_commit"] == implementation
        assert binding["adoption_commit"] == adoption


def test_full_b3_replay_and_mapping_residual_tamper_refusals():
    # Import the existing exact B3 fixture lazily: production tests run in the
    # locked project environment, while importing the analyzer itself remains
    # CPU-only and does not require the model stack.
    from finalize_v2b_unblinding import verify_repo_unblinding
    from test_finalize_v2b_unblinding import _unblind_fixture

    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _private, governance_stub = _unblind_fixture(td)
        reveal_row = verify_repo_unblinding(
            salt=fixture["salt"],
            commitment_binding=fixture["commitment"],
            analyze_fn=governance_stub, **entry)
        replayed = _replay_repo(
            "mathlib4", reveal_row, fixture["salt"], fixture["commitment"],
            governance_fn=governance_stub,
            expected_model="m", expected_revision="a" * 40)
        assert len(replayed["families"]["E1a"]) == 1
        assert replayed["families"]["E1b"] == []
        assert replayed["families"]["E2"] == []

        bad_mapping = copy.deepcopy(reveal_row)
        bad_mapping["mapping"]["E1a"]["sign"] *= -1
        try:
            _replay_repo(
                "mathlib4", bad_mapping, fixture["salt"],
                fixture["commitment"], governance_fn=governance_stub,
                expected_model="m", expected_revision="a" * 40)
            assert False, "tampered reveal mapping was accepted"
        except V2BError as err:
            assert "mapping" in str(err)

        masked = json.load(open(entry["masked_path"], encoding="utf-8"))
        fid = reveal_row["mapping"]["E1a"]["fid"]
        masked["families"][fid][0][1] += 1.0
        with open(entry["masked_path"], "w", encoding="utf-8") as fh:
            json.dump(masked, fh)
        tampered_binding, _ = artifact_binding(
            entry["masked_path"], MASKED_DELTAS_SCHEMA)
        bad_residual = copy.deepcopy(reveal_row)
        bad_residual["bindings"]["masked"] = tampered_binding
        try:
            _replay_repo(
                "mathlib4", bad_residual, fixture["salt"],
                fixture["commitment"], governance_fn=governance_stub,
                expected_model="m", expected_revision="a" * 40)
            assert False, "tampered masked residual was accepted"
        except V2BError as err:
            assert "replay" in str(err)


def test_complete_input_ledger_enumerates_targets_and_rejects_drift():
    from finalize_v2b_unblinding import verify_repo_unblinding
    from test_finalize_v2b_unblinding import _unblind_fixture

    with tempfile.TemporaryDirectory() as td:
        fixture, entry, _private, governance_stub = _unblind_fixture(td)
        row = verify_repo_unblinding(
            salt=fixture["salt"],
            commitment_binding=fixture["commitment"],
            analyze_fn=governance_stub, **entry)
        reveal = _synthetic_reveal()
        reveal["salt_commitment"] = fixture["commitment"]
        reveal["revealed_salt_hex"] = fixture["salt"].hex()
        for repo in EXPECTED:
            reveal["repos"][repo]["bindings"] = copy.deepcopy(
                row["bindings"])
        reveal_path = os.path.join(td, "reveal.json")
        write_new_json(reveal_path, reveal)
        reveal_binding, _ = artifact_binding(
            reveal_path, NLL_EXPLORATORY_REVEAL_SCHEMA)
        reveal_amendment, reveal_freeze = _reveal_provenance_bindings()

        amendment_path = os.path.join(td, "analysis-amendment.md")
        with open(amendment_path, "w", encoding="utf-8") as fh:
            fh.write("synthetic prospective amendment\n")
        analysis_amendment = dict(
            path=amendment_path, sha256=sha256_file(amendment_path),
            adoption_commits=list(ANALYSIS_AMENDMENT_HISTORY))
        freeze_path = os.path.join(td, "analysis-freeze.json")
        files = {path: sha256_file(os.path.join(BASE, path))
                 for path in ANALYSIS_SOURCE_FILES}
        write_new_json(freeze_path, dict(
            schema=ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA, files=files))
        freeze_binding, _ = artifact_binding(
            freeze_path, ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA)
        analysis_freeze = dict(
            path=freeze_path, sha256=freeze_binding["sha256"],
            schema=ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA,
            adoption_commit="c" * 40, implementation_commit="d" * 40,
            source_tree_sha256="e" * 64)

        ledger = _capture_input_ledger(
            reveal_path, reveal, reveal_binding, reveal_amendment,
            reveal_freeze, analysis_amendment, analysis_freeze)
        assert ledger == _capture_input_ledger(
            reveal_path, reveal, reveal_binding, reveal_amendment,
            reveal_freeze, analysis_amendment, analysis_freeze)
        target_labels = [row["label"] for row in ledger["entries"]
                         if ".target[" in row["label"]]
        assert target_labels == [f"{repo}.target[0000]"
                                 for repo in sorted(EXPECTED)]
        with open(fixture["target_path"], "ab") as fh:
            fh.write(b" ")
        try:
            _capture_input_ledger(
                reveal_path, reveal, reveal_binding, reveal_amendment,
                reveal_freeze, analysis_amendment, analysis_freeze)
            assert False, "mid-analysis target byte drift was accepted"
        except V2BError as err:
            assert "hash drift" in str(err)


def test_prepare_rejects_pre_post_ledger_toctou():
    with tempfile.TemporaryDirectory() as td:
        watched = os.path.join(td, "watched.json")
        with open(watched, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        reveal = _synthetic_reveal()
        reveal_binding, amendment, freeze = _analysis_bindings()
        reveal_amendment, reveal_freeze = _reveal_provenance_bindings()

        def binding_stub(_path, _schema):
            return reveal_binding, reveal

        def ledger_stub(*_args):
            row = _ledger_entry("watched", watched)
            return dict(entries=[row], ledger_sha256=row["sha256"])

        def mutating_analysis(*_args):
            with open(watched, "a", encoding="utf-8") as fh:
                fh.write("drift\n")
            return {"bindings": {}}

        try:
            prepare(
                "/synthetic/reveal.json",
                source_clean_fn=lambda: True,
                head_commit_fn=lambda: "1" * 40,
                source_tree_hash_fn=lambda: "2" * 64,
                artifact_binding_fn=binding_stub,
                require_inputs_fn=lambda *_args: None,
                analysis_amendment_fn=lambda: amendment,
                analysis_freeze_fn=lambda: freeze,
                reveal_amendment_fn=lambda: reveal_amendment,
                reveal_freeze_fn=lambda: reveal_freeze,
                capture_ledger_fn=ledger_stub,
                analyze_value_fn=mutating_analysis,
                ancestor_fn=lambda _older, _newer: True)
            assert False, "pre/post ledger TOCTOU was accepted"
        except V2BError as err:
            assert "ledger drifted" in str(err)


def test_committed_analysis_amendment_binding_is_exact():
    binding = _analysis_amendment_binding()
    assert binding["sha256"] == ANALYSIS_AMENDMENT_SHA256
    assert binding["adoption_commits"] == list(ANALYSIS_AMENDMENT_HISTORY)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B NLL EXPLORATORY ANALYSIS TESTS PASS")
