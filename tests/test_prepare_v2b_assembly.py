#!/usr/bin/env python3
"""Adversarial synthetic tests for the V2-b assembly driver slice (B1).

Builds a complete synthetic evidence chain (sources on disk, extraction,
near-dup table, A6 outcome, candidates, bound sample, Lean freeze) and
exercises binding rehash, U(t)/reverse-closure/A6-exclusion logic, the
k1/k2/k3/k4 renderings, and the hard property checks. No real artifact,
sample, or cluster path is touched.
Run: python3 tests/test_prepare_v2b_assembly.py"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2b_a6 import EXPECTED
from prepare_v2b_assembly import build_assembly
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, LEAN_KEYWORD_FREEZE_SCHEMA,
                        NEARDUP_SCHEMA, V2BError, identity_key, sha256_json)
from v2b_neardup import (lean_keyword_provenance_hash,
                         load_lean_keyword_freeze)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return _sha(text.encode("utf-8"))


def _freeze(td):
    tokens = sorted(["by", "def", "omega", "rfl", "simp"])
    repos = ("batteries", "mathlib4", "physlib")
    provenance = [dict(token=token,
                       sources=[dict(repo=repo, reserved_token_table=True,
                                     parser_dispatch=False)
                                for repo in repos])
                  for token in tokens]
    value = dict(
        schema=LEAN_KEYWORD_FREEZE_SCHEMA, derivation="test",
        source_tables=[dict(repo=repo, n_excluded_dispatch_keys=7)
                       for repo in repos],
        n_excluded_dispatch_keys_total=21,
        n_tokens=len(tokens), tokens_sha256=sha256_json(tokens),
        tokens=tokens,
        token_provenance_sha256=lean_keyword_provenance_hash(provenance),
        token_provenance=provenance,
        generator=dict(source_commit="f" * 40, source_tree_hash="1" * 64,
                       program="finalize_v2b_lean_keywords.py"))
    path = os.path.join(td, "freeze.json")
    json.dump(value, open(path, "w"), sort_keys=True)
    return path


def _outcome(td, jaccard="0.80", active_bands=(), name="outcome.json"):
    outcomes = dict(
        jaccard=dict(lean=dict(outcome=jaccard),
                     python=dict(outcome=jaccard)),
        collision_activation=dict(
            lean={band: dict(n_labeled=8, n_clones=8, active=True)
                  for band in active_bands},
            python={}))
    value = dict(schema="v2b_a6_outcome_v1",
                 label_state="unblinded-from-committed-labels",
                 sampling_state="not-drawn",
                 outcomes=outcomes,
                 outcomes_sha256=sha256_json(outcomes))
    path = os.path.join(td, name)
    json.dump(value, open(path, "w"))
    return path


def _lean_chain(td, jaccard="0.80"):
    repo = "mathlib4"
    corpus_sha = EXPECTED[repo][1]
    root = os.path.join(td, "corpus")
    a_lines = ["def pre : Nat :=\n", "  1\n",
               "theorem t : True :=\n", "  trivial\n",
               "def local1 : Nat :=\n", "  2\n"]
    a_text = "".join(a_lines)
    b_lines = ["def dep2 : Nat :=\n", "  9\n",
               "def dep : Nat :=\n", "  3\n",
               "def rev : Nat :=\n", "  4\n",
               "def ndup : Nat :=\n", "  5\n"]
    b_text = "".join(b_lines)
    a_path, b_path = (os.path.join(root, "A.lean"),
                      os.path.join(root, "B.lean"))
    a_sha, b_sha = _write(a_path, a_text), _write(b_path, b_text)

    def span(lines, first, count):
        start = sum(len(line) for line in lines[:first])
        end = start + sum(len(line) for line in lines[first:first + count])
        return start, end

    a_spans = {"M.A.pre": span(a_lines, 0, 2), "M.A.t": span(a_lines, 2, 2),
               "M.A.local1": span(a_lines, 4, 2)}
    b_spans = {"M.B.dep2": span(b_lines, 0, 2), "M.B.dep": span(b_lines, 2, 2),
               "M.B.rev": span(b_lines, 4, 2), "M.B.ndup": span(b_lines, 6, 2)}

    def decl(spans, name, header, split=":=", shell=()):
        start, end = spans[name]
        return dict(start_byte=start, end_byte=end, header_bytes=header,
                    split_kind=split, shell=list(shell))

    extraction = dict(
        schema="v2a_lean_extract_v3", repo=repo,
        files=[
            dict(module="M.A", source=a_path, rel="A.lean",
                 source_sha256=a_sha,
                 decls={
                     "M.A.pre": decl(a_spans, "M.A.pre",
                                     len("def pre : Nat ")),
                     "M.A.t": decl(a_spans, "M.A.t",
                                   len("theorem t : True "),
                                   shell=["open Nat"]),
                     "M.A.local1": decl(a_spans, "M.A.local1",
                                        len("def local1 : Nat "))}),
            dict(module="M.B", source=b_path, rel="B.lean",
                 source_sha256=b_sha,
                 decls={
                     "M.B.dep2": decl(b_spans, "M.B.dep2",
                                      len("def dep2 : Nat "), split=None),
                     "M.B.dep": decl(b_spans, "M.B.dep",
                                     len("def dep : Nat ")),
                     "M.B.rev": decl(b_spans, "M.B.rev",
                                     len("def rev : Nat ")),
                     "M.B.ndup": decl(b_spans, "M.B.ndup",
                                      len("def ndup : Nat "))})],
        graph=dict(edges=[
            ["M.A", "M.A.t", "M.B", "M.B.dep"],
            ["M.B", "M.B.dep", "M.B", "M.B.dep2"],
            ["M.B", "M.B.rev", "M.A", "M.A.t"],
            ["M.A", "M.A.t", "M.B", "M.B.ndup"]]))
    extraction_path = os.path.join(td, "extraction.json")
    json.dump(extraction, open(extraction_path, "w"))
    extraction_sha = _sha(open(extraction_path, "rb").read())

    freeze_path = _freeze(td)
    _, freeze_binding = load_lean_keyword_freeze(freeze_path)
    identities = [["M.A", name] for name in
                  ("M.A.pre", "M.A.t", "M.A.local1")] + \
                 [["M.B", name] for name in
                  ("M.B.dep2", "M.B.dep", "M.B.rev", "M.B.ndup")]
    units = []
    for i, identity in enumerate(identities):
        key = identity_key("lean", identity)
        verbatim = f"{i:064x}"
        if identity[1] == "M.A.pre":            # verbatim twin of target
            verbatim = "1".zfill(64)
        if identity[1] == "M.A.t":
            verbatim = "1".zfill(64)
        units.append(dict(identity=identity, key=key,
                          verbatim_sha256=verbatim,
                          normalized_sha256=f"{i + 100:064x}"))
    target_key = identity_key("lean", ["M.A", "M.A.t"])
    ndup_key = identity_key("lean", ["M.B", "M.B.ndup"])
    neardup = dict(schema=NEARDUP_SCHEMA, repo=repo, language="lean",
                   extraction=dict(path=extraction_path,
                                   sha256=extraction_sha),
                   keyword_evidence=freeze_binding,
                   units=units,
                   jaccard_pairs=[dict(a=min(target_key, ndup_key),
                                       b=max(target_key, ndup_key),
                                       intersection=8, union=10)],
                   collision_groups=[])
    neardup_path = os.path.join(td, "neardup.json")
    json.dump(neardup, open(neardup_path, "w"))

    outcome_path = _outcome(td, jaccard=jaccard)
    outcome_sha = _sha(open(outcome_path, "rb").read())

    candidates = dict(schema=CANDIDATES_SCHEMA, repo=repo,
                      corpus_git_sha=corpus_sha,
                      extraction=dict(path=extraction_path,
                                      sha256=extraction_sha))
    candidates_path = os.path.join(td, "candidates.json")
    json.dump(candidates, open(candidates_path, "w"))
    candidates_sha = _sha(open(candidates_path, "rb").read())

    sample = dict(schema=BOUND_SAMPLE_SCHEMA, sampling_state="drawn",
                  n_requested_per_corpus=20,
                  a6_outcome=dict(sha256=outcome_sha),
                  plans={repo: dict(candidates_sha256=candidates_sha,
                                    targets=[dict(
                                        identity=["M.A", "M.A.t"])])})
    sample_path = os.path.join(td, "sample.json")
    json.dump(sample, open(sample_path, "w"))
    return dict(sample=sample_path, repo=repo, candidates=candidates_path,
                extraction=extraction_path, neardup=neardup_path,
                outcome=outcome_path, freeze=freeze_path)


def _build(chain):
    return build_assembly(chain["sample"], chain["repo"],
                          chain["candidates"], chain["extraction"],
                          chain["neardup"], chain["outcome"],
                          chain["freeze"])


def test_lean_manifest_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        manifest = _build(chain)
        assert manifest["schema"] == ASSEMBLY_SCHEMA
        assert manifest["arms_included"] == ["k1", "k2", "k3", "k4"]
        assert manifest["arms_deferred"]                    # never silent
        assert manifest["n_targets"] == 1
        row = manifest["targets"][0]
        # closure = dep, dep2, ndup; ndup excluded as labeled near-dup
        assert row["n_closure_units"] == 3
        assert row["n_k4_units"] == 2
        assert row["n_near_dup_excluded"] == 1
        assert row["n_same_file_excluded"] == 0
        assert row["n_reverse_closure"] == 1                # rev
        assert row["n_universe"] == 2                       # dep, dep2
        assert row["n_k3_unsplit_units"] == 1               # dep2 verbatim
        # dependency-before-dependent: dep2 renders before dep
        big = row["arms"]["k4"][str(65536)]
        order = [tuple(unit["identity"]) for unit in big["selected_units"]]
        assert order == [("M.B", "M.B.dep2"), ("M.B", "M.B.dep")]
        assert big["eligible"] is False                     # tiny corpus
        # k2 excised the wholly-earlier verbatim twin span
        assert len(row["arms"]["k2_splice"]["merged_exclusions"]) == 1
        # prefix = shell + header, byte-accounted
        assert row["prefix_bytes"] == len("open Nat\n") + \
            len("theorem t : True ")
        assert row["body_bytes"] > 0
        # k1 is the canonical empty cell
        assert row["arms"]["k1"]["context_bytes"] == 0
        assert row["arms"]["k1"]["budget_bytes"] is None
        # determinism
        assert manifest["targets_sha256"] == \
            _build(chain)["targets_sha256"]


def test_near_dup_exclusion_follows_label_outcome():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td, jaccard="0.90")
        # J = 8/10 pair no longer meets the calibrated 0.90 threshold:
        # ndup stays in k4 and in the universe
        manifest = _build(chain)
        row = manifest["targets"][0]
        assert row["n_k4_units"] == 3
        assert row["n_near_dup_excluded"] == 0
        assert row["n_universe"] == 3
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td, jaccard="lexical-inconclusive")
        row = _build(chain)["targets"][0]
        assert row["n_k4_units"] == 3                       # hash-only rule
        assert row["n_near_dup_excluded"] == 0


def test_binding_drift_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        value = json.load(open(chain["candidates"]))
        value["corpus_git_sha"] = "0" * 40
        json.dump(value, open(chain["candidates"], "w"))
        try:
            _build(chain)
            assert False, "candidates drift accepted"
        except V2BError:
            pass
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        value = json.load(open(chain["neardup"]))
        value["extraction"]["sha256"] = "0" * 64
        json.dump(value, open(chain["neardup"], "w"))
        try:
            _build(chain)
            assert False, "unbound near-dup table accepted"
        except V2BError:
            pass
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        _outcome(td, jaccard="0.80", name="outcome.json")   # rewrite bytes
        value = json.load(open(chain["outcome"]))
        value["outcomes"]["jaccard"]["lean"]["outcome"] = "0.90"
        value["outcomes_sha256"] = sha256_json(value["outcomes"])
        json.dump(value, open(chain["outcome"], "w"))
        try:
            _build(chain)
            assert False, "outcome not sealed by sample accepted"
        except V2BError:
            pass
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        try:
            build_assembly(chain["sample"], chain["repo"],
                           chain["candidates"], chain["extraction"],
                           chain["neardup"], chain["outcome"], None)
            assert False, "lean assembly without keyword freeze accepted"
        except V2BError:
            pass


def test_python_manifest_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        repo = "sympy"
        corpus_sha = EXPECTED[repo][1]
        root = os.path.join(td, "corpus")
        a_text = "def f(x):\n    return g(x)\n"
        b_text = "def g(x):\n    return x\n"
        a_path = os.path.join(root, "pkg_a.py")
        b_path = os.path.join(root, "pkg_b.py")
        a_sha, b_sha = _write(a_path, a_text), _write(b_path, b_text)
        extraction = dict(
            schema="v2a_python_extract_v3", repo=repo,
            files=[
                dict(module="pkg.a", source=a_path, rel="pkg_a.py",
                     source_sha256=a_sha,
                     targets=[dict(identity=["pkg.a", "f", 0], start_byte=0,
                                   end_byte=len(a_text),
                                   header_bytes=len("def f(x):"))]),
                dict(module="pkg.b", source=b_path, rel="pkg_b.py",
                     source_sha256=b_sha,
                     targets=[dict(identity=["pkg.b", "g", 0], start_byte=0,
                                   end_byte=len(b_text),
                                   header_bytes=len("def g(x):"))])],
            graph=dict(edges=[["pkg.a", "f", 0, "pkg.b", "g", 0]]))
        extraction_path = os.path.join(td, "extraction.json")
        json.dump(extraction, open(extraction_path, "w"))
        extraction_sha = _sha(open(extraction_path, "rb").read())
        neardup = dict(schema=NEARDUP_SCHEMA, repo=repo, language="python",
                       extraction=dict(path=extraction_path,
                                       sha256=extraction_sha),
                       units=[dict(identity=["pkg.a", "f", 0],
                                   key=identity_key("python",
                                                    ["pkg.a", "f", 0]),
                                   verbatim_sha256="a" * 64,
                                   normalized_sha256="b" * 64),
                              dict(identity=["pkg.b", "g", 0],
                                   key=identity_key("python",
                                                    ["pkg.b", "g", 0]),
                                   verbatim_sha256="c" * 64,
                                   normalized_sha256="d" * 64)],
                       jaccard_pairs=[], collision_groups=[])
        neardup_path = os.path.join(td, "neardup.json")
        json.dump(neardup, open(neardup_path, "w"))
        outcome_path = _outcome(td)
        outcome_sha = _sha(open(outcome_path, "rb").read())
        candidates = dict(schema=CANDIDATES_SCHEMA, repo=repo,
                          corpus_git_sha=corpus_sha,
                          extraction=dict(path=extraction_path,
                                          sha256=extraction_sha))
        candidates_path = os.path.join(td, "candidates.json")
        json.dump(candidates, open(candidates_path, "w"))
        candidates_sha = _sha(open(candidates_path, "rb").read())
        sample = dict(schema=BOUND_SAMPLE_SCHEMA, sampling_state="drawn",
                      n_requested_per_corpus=20,
                      a6_outcome=dict(sha256=outcome_sha),
                      plans={repo: dict(
                          candidates_sha256=candidates_sha,
                          targets=[dict(identity=["pkg.a", "f", 0])])})
        sample_path = os.path.join(td, "sample.json")
        json.dump(sample, open(sample_path, "w"))
        manifest = build_assembly(sample_path, repo, candidates_path,
                                  extraction_path, neardup_path,
                                  outcome_path)
        row = manifest["targets"][0]
        assert manifest["language"] == "python"
        assert row["n_k4_units"] == 1
        assert row["n_k3_unsplit_units"] == 0
        assert row["prefix_bytes"] == len("def f(x):")      # no shell
        assert row["body_bytes"] == len(a_text) - len("def f(x):")
        cell = row["arms"]["k4"][str(4096)]
        assert [tuple(u["identity"]) for u in cell["selected_units"]] == \
            [("pkg.b", "g", 0)]


def test_source_drift_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        with open(os.path.join(td, "corpus", "B.lean"), "a") as fh:
            fh.write("-- drift\n")
        try:
            _build(chain)
            assert False, "source drift accepted"
        except V2BError as err:
            assert "hash drift" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B ASSEMBLY DRIVER TESTS PASS")
