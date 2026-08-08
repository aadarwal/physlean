#!/usr/bin/env python3
"""Synthetic packet -> blind labels -> A6 outcome boundary tests."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finalize_v2b_a6 import EXPECTED, build_packet
from v2b_a6_blind import (BLIND_RUBRIC, build_blind_core, build_outcome,
                          require_committed, require_single_commit)
from v2b_common import (A6_BLIND_SCHEMA, A6_LABELS_SCHEMA,
                        A6_OUTCOME_SCHEMA, LEAN_KEYWORD_FREEZE_SCHEMA,
                        V2BError, sha256_bytes, sha256_file, sha256_json)
from v2b_neardup import (LEAN_EXTRACT_SCHEMA, PYTHON_EXTRACT_SCHEMA,
                         build_neardup_artifact,
                         lean_keyword_provenance_hash,
                         load_lean_keyword_freeze)


def _dump(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True)


def _freeze(td):
    tokens = sorted(["by", "def", "omega", "rfl", "simp"])
    repos = ("batteries", "mathlib4", "physlib")
    provenance = [dict(
        token=token,
        sources=[dict(repo=repo, reserved_token_table=True,
                      parser_dispatch=False) for repo in repos])
        for token in tokens]
    value = dict(
        schema=LEAN_KEYWORD_FREEZE_SCHEMA,
        derivation="synthetic exact parser-token union",
        source_tables=[dict(repo=repo, n_excluded_dispatch_keys=7)
                       for repo in repos],
        n_excluded_dispatch_keys_total=21,
        n_tokens=len(tokens), tokens_sha256=sha256_json(tokens),
        tokens=tokens,
        token_provenance_sha256=lean_keyword_provenance_hash(provenance),
        token_provenance=provenance,
        generator=dict(source_commit="a" * 40,
                       source_tree_hash="b" * 64,
                       program="finalize_v2b_lean_keywords.py"))
    path = os.path.join(td, "freeze.json")
    _dump(path, value)
    return path


def _source(language):
    if language == "lean":
        left = ("def alpha (x : Nat) : Nat :=\n"
                "  1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12\n")
        right = ("def beta (y : Nat) : Nat :=\n"
                 "  1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12\n")
    else:
        left = ("def alpha(x):\n"
                "    return 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12\n")
        right = ("def beta(y):\n"
                 "    return 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12\n")
    return left, right


def _table(td, repo, language, corpus_sha, freeze_path):
    left, right = _source(language)
    separator = "\n"
    text = left + separator + right
    source = os.path.join(td, repo + (".lean" if language == "lean" else ".py"))
    with open(source, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    blob = text.encode("utf-8")
    split = len((left + separator).encode("utf-8"))
    if language == "lean":
        module = "Synthetic" + repo.title().replace("4", "Four")
        files = [dict(
            module=module, source=source, source_sha256=sha256_bytes(blob),
            decls={
                "alpha": dict(start_byte=0,
                              end_byte=len(left.encode("utf-8"))),
                "beta": dict(start_byte=split, end_byte=len(blob))})]
        extraction = dict(schema=LEAN_EXTRACT_SCHEMA, repo=repo, files=files)
    else:
        module = "synthetic_" + repo
        files = [dict(
            module=module, source=source, source_sha256=sha256_bytes(blob),
            targets=[
                dict(identity=[module, "alpha", 0], start_byte=0,
                     end_byte=len(left.encode("utf-8"))),
                dict(identity=[module, "beta", split], start_byte=split,
                     end_byte=len(blob))])]
        extraction = dict(schema=PYTHON_EXTRACT_SCHEMA, repo=repo, files=files)
    extraction_path = os.path.join(td, repo + "-extraction.json")
    _dump(extraction_path, extraction)
    if language == "lean":
        keywords, evidence = load_lean_keyword_freeze(freeze_path)
        table = build_neardup_artifact(
            extraction_path, repo, keywords, evidence)
    else:
        table = build_neardup_artifact(extraction_path, repo)
    table.update(
        corpus_git_sha=corpus_sha, n_source_files=1,
        structural_evidence=dict(synthetic=True),
        generator=dict(source_commit="a" * 40,
                       source_tree_hash="b" * 64,
                       environment_fingerprint="c" * 64,
                       program="prepare_v2b_neardup.py"))
    path = os.path.join(td, repo + "-neardup.json")
    _dump(path, table)
    return path


def _fixture(td):
    freeze = _freeze(td)
    tables = [_table(td, repo, language, corpus_sha, freeze)
              for repo, (language, corpus_sha) in EXPECTED.items()]
    packet = build_packet(tables, freeze)
    packet["generator"] = dict(source_commit="d" * 40,
                               source_tree_hash="e" * 64,
                               program="finalize_v2b_a6.py")
    packet_path = os.path.join(td, "packet.json")
    _dump(packet_path, packet)
    return packet_path


def _presentation_and_labels(td, packet_path, label="duplicate"):
    core, mapping, _, _ = build_blind_core(packet_path)
    presentation = dict(core)
    presentation["generator"] = dict(
        source_commit="f" * 40, source_tree_hash="1" * 64,
        program="prepare_v2b_a6_blind.py")
    presentation_path = os.path.join(td, "presentation.json")
    _dump(presentation_path, presentation)
    labels = dict(
        schema=A6_LABELS_SCHEMA, label_state="blind-complete",
        rubric=BLIND_RUBRIC, labeler="synthetic-reviewer",
        presentation_sha256=sha256_file(presentation_path),
        labels=[dict(pair_id=row["pair_id"], label=label, note="")
                for row in presentation["pairs"]])
    labels_path = os.path.join(td, "labels.json")
    _dump(labels_path, labels)
    return core, mapping, presentation_path, labels_path


def test_blind_pack_is_deterministic_deduplicated_and_has_leak_whitelist():
    with tempfile.TemporaryDirectory() as td:
        packet_path = _fixture(td)
        core, mapping, _, _ = build_blind_core(packet_path)
        assert core == build_blind_core(packet_path)[0]
        assert core["schema"] == A6_BLIND_SCHEMA
        assert set(core) == {"schema", "label_state", "rubric", "n_pairs",
                             "pairs"}
        assert core["n_pairs"] == 5
        assert all(len(roles) == 2 for roles in mapping.values())
        assert all(set(row) == {"pair_id", "language", "left", "right"}
                   for row in core["pairs"])
        forbidden = {"repo", "bin", "band", "intersection", "union",
                     "normalized_sha256", "verbatim_sha256", "roles"}
        assert all(not (set(row) & forbidden) for row in core["pairs"])


def test_blind_labels_project_once_to_both_frozen_gates():
    with tempfile.TemporaryDirectory() as td:
        packet_path = _fixture(td)
        core, mapping, presentation_path, labels_path = \
            _presentation_and_labels(td, packet_path)
        outcome = build_outcome(packet_path, presentation_path, labels_path)
        assert outcome["schema"] == A6_OUTCOME_SCHEMA
        assert outcome["sampling_state"] == "not-drawn"
        assert outcome["n_blind_pairs"] == core["n_pairs"] == 5
        assert outcome["n_projected_roles"] == sum(
            len(roles) for roles in mapping.values()) == 10
        assert outcome["outcomes"]["jaccard"]["lean"]["outcome"] == \
            "lexical-inconclusive"
        assert not outcome["outcomes"]["collision_activation"]["lean"][
            "geq20"]["active"]


def test_presentation_leak_label_omission_and_source_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        packet_path = _fixture(td)
        _, _, presentation_path, labels_path = _presentation_and_labels(
            td, packet_path)
        presentation = json.load(open(presentation_path))
        presentation["pairs"][0]["repo"] = "leak"
        _dump(presentation_path, presentation)
        try:
            build_outcome(packet_path, presentation_path, labels_path)
            assert False, "human presentation accepted hidden audit metadata"
        except V2BError:
            pass

    with tempfile.TemporaryDirectory() as td:
        packet_path = _fixture(td)
        _, _, presentation_path, labels_path = _presentation_and_labels(
            td, packet_path)
        labels = json.load(open(labels_path))
        labels["labels"].pop()
        _dump(labels_path, labels)
        try:
            build_outcome(packet_path, presentation_path, labels_path)
            assert False, "incomplete blind labels accepted"
        except V2BError:
            pass

    with tempfile.TemporaryDirectory() as td:
        packet_path = _fixture(td)
        packet = json.load(open(packet_path))
        table = json.load(open(packet["source_tables"][0]["path"]))
        source = json.load(open(table["extraction"]["path"]))["files"][0][
            "source"]
        with open(source, "a", encoding="utf-8") as fh:
            fh.write("-- drift\n")
        try:
            build_blind_core(packet_path)
            assert False, "live source drift accepted by blind renderer"
        except V2BError:
            pass


def test_uncommitted_label_boundary_refuses():
    fd, path = tempfile.mkstemp(prefix=".a6-uncommitted-", dir=ROOT)
    os.close(fd)
    try:
        try:
            require_committed(path)
            assert False, "uncommitted blind labels accepted"
        except V2BError:
            pass
    finally:
        os.unlink(path)


def test_label_history_must_be_exactly_one_commit():
    import v2b_a6_blind as blind

    original = blind.subprocess.run

    class Result:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    try:
        blind.subprocess.run = lambda *args, **kwargs: Result("a" * 40 + "\n")
        assert require_single_commit(os.path.join(ROOT, "labels.json")) == \
            "a" * 40
        blind.subprocess.run = lambda *args, **kwargs: Result(
            "a" * 40 + "\n" + "b" * 40 + "\n")
        try:
            require_single_commit(os.path.join(ROOT, "labels.json"))
            assert False, "relabelled path with two commits accepted"
        except V2BError:
            pass
    finally:
        blind.subprocess.run = original


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B A6 BLINDING TESTS PASS")
