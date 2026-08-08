#!/usr/bin/env python3
"""Synthetic exact-five sealing tests for unlabeled A6 packets."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2b_a6 import EXPECTED, build_packet
from v2b_common import (A6_AUDIT_PACKET_SCHEMA, LEAN_KEYWORD_FREEZE_SCHEMA,
                        NEARDUP_SCHEMA, V2BError, identity_key, sha256_json)
from v2b_neardup import (LEXER_CITATION, load_lean_keyword_freeze,
                         python_keyword_evidence)


def _freeze(td):
    tokens = sorted(["by", "def", "omega", "rfl", "simp"])
    value = dict(
        schema=LEAN_KEYWORD_FREEZE_SCHEMA,
        derivation="test",
        source_tables=[dict(repo=repo) for repo in
                       ("batteries", "mathlib4", "physlib")],
        n_tokens=len(tokens), tokens_sha256=sha256_json(tokens),
        tokens=tokens,
        generator=dict(source_commit="f" * 40,
                       source_tree_hash="1" * 64,
                       program="finalize_v2b_lean_keywords.py"))
    path = os.path.join(td, "freeze.json")
    json.dump(value, open(path, "w"), sort_keys=True)
    return path


def _tables(td, freeze_path):
    _, freeze_binding = load_lean_keyword_freeze(freeze_path)
    paths = []
    for repo, (language, corpus_sha) in EXPECTED.items():
        if language == "lean":
            a_identity, b_identity = ["M", "a"], ["M", "b"]
            keyword_evidence = freeze_binding
        else:
            a_identity, b_identity = ["m", "a", 0], ["m", "b", 1]
            keyword_evidence = python_keyword_evidence()
        a, b = (identity_key(language, a_identity),
                identity_key(language, b_identity))
        units = [dict(identity=a_identity, key=a, verbatim_sha256="a" * 64,
                      normalized_sha256="c" * 64, n_records=25,
                      n_lexical_records=25, under_floor=False),
                 dict(identity=b_identity, key=b, verbatim_sha256="b" * 64,
                      normalized_sha256="d" * 64, n_records=25,
                      n_lexical_records=25, under_floor=False)]
        units.sort(key=lambda row: row["key"])
        table = dict(
            schema=NEARDUP_SCHEMA, repo=repo, language=language,
            corpus_git_sha=corpus_sha,
            extraction=dict(path="extraction.json", sha256="e" * 64,
                            schema="extract"),
            keyword_evidence=keyword_evidence,
            lexer_citation=LEXER_CITATION,
            lexical_floor=20, jaccard_threshold="7/10",
            n_units=2, n_under_floor=0, units=units,
            jaccard_pairs=[dict(a=min(a, b), b=max(a, b),
                                 a_identity=a_identity, b_identity=b_identity,
                                 intersection=7, union=10)],
            collision_groups=[],
            generator=dict(source_commit="f" * 40,
                           source_tree_hash="1" * 64,
                           environment_fingerprint="2" * 64,
                           program="prepare_v2b_neardup.py"))
        path = os.path.join(td, repo + ".json")
        json.dump(table, open(path, "w"), sort_keys=True)
        paths.append(path)
    return paths


def test_packet_is_exact_deterministic_and_still_unlabeled():
    with tempfile.TemporaryDirectory() as td:
        freeze = _freeze(td)
        paths = _tables(td, freeze)
        packet = build_packet(list(reversed(paths)), freeze)
        assert packet["schema"] == A6_AUDIT_PACKET_SCHEMA
        assert packet["label_state"] == "unlabeled"
        assert packet["sampling_state"] == "not-drawn"
        assert [row["repo"] for row in packet["source_tables"]] == \
            sorted(EXPECTED)
        assert packet["calibration"]["lean"]["B1"]["n_selected"] == 3
        assert packet["calibration"]["python"]["B1"]["n_selected"] == 2
        assert packet["collision"]["lean"]["geq20"]["n_selected"] == 0
        assert packet == build_packet(paths, freeze)


def test_packet_rejects_duplicate_and_freeze_drift():
    with tempfile.TemporaryDirectory() as td:
        freeze = _freeze(td)
        paths = _tables(td, freeze)
        try:
            build_packet(paths[:-1] + [paths[0]], freeze)
            assert False, "duplicate corpus substituted for missing one"
        except V2BError:
            pass
        table = json.load(open(paths[0]))
        table["keyword_evidence"]["tokens_sha256"] = "0" * 64
        json.dump(table, open(paths[0], "w"), sort_keys=True)
        try:
            build_packet(paths, freeze)
            assert False, "keyword freeze drift accepted"
        except V2BError:
            pass


def test_packet_rejects_mixed_generator_cohort():
    with tempfile.TemporaryDirectory() as td:
        freeze = _freeze(td)
        paths = _tables(td, freeze)
        table = json.load(open(paths[-1]))
        table["generator"]["source_commit"] = "e" * 40
        json.dump(table, open(paths[-1], "w"), sort_keys=True)
        try:
            build_packet(paths, freeze)
            assert False, "mixed generator source commits accepted"
        except V2BError:
            pass


def test_packet_rejects_phantom_pair_and_drifted_collision_group():
    with tempfile.TemporaryDirectory() as td:
        freeze = _freeze(td)
        paths = _tables(td, freeze)
        pair_table = json.load(open(paths[0]))
        pair_table["jaccard_pairs"][0]["a"] = "[\"phantom\",\"unit\"]"
        json.dump(pair_table, open(paths[0], "w"), sort_keys=True)
        try:
            build_packet(paths, freeze)
            assert False, "pair referring to a phantom unit accepted"
        except V2BError:
            pass

    with tempfile.TemporaryDirectory() as td:
        freeze = _freeze(td)
        paths = _tables(td, freeze)
        group_table = json.load(open(paths[0]))
        for unit in group_table["units"]:
            unit["normalized_sha256"] = "c" * 64
        group_table["collision_groups"] = [dict(
            normalized_sha256="c" * 64, repo=group_table["repo"],
            band="under20", n_records=25, n_members=2,
            n_distinct_verbatim=2,
            members=[dict(identity=unit["identity"],
                          verbatim_sha256=unit["verbatim_sha256"])
                     for unit in group_table["units"]])]
        json.dump(group_table, open(paths[0], "w"), sort_keys=True)
        try:
            build_packet(paths, freeze)
            assert False, "collision group with wrong record-count band accepted"
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B A6 FINALIZER TESTS PASS")
