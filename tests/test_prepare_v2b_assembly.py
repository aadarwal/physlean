#!/usr/bin/env python3
"""Adversarial synthetic tests for the V2-b assembly driver (B1).

Builds a complete synthetic evidence chain (sources on disk, extraction,
near-dup table with REAL A6-lexed hashes, A6 outcome, candidates, bound
sample, Lean freeze) and exercises binding rehash, U(t)/reverse-closure/
A6-exclusion logic, the k1-k6 + k3s/k4s renderings, exclusion masses,
the BM25 re-lex term-source binding, and the evaluator materialization
API. No real artifact, sample, or cluster path is touched.
Run: python3 tests/test_prepare_v2b_assembly.py"""
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finalize_v2b_a6 import EXPECTED
from prepare_v2b_assembly import (B_STAR, BM25_B, BM25_K1, K6_TIE_LABEL,
                                  build_assembly, materialize)
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, LEAN_KEYWORD_FREEZE_SCHEMA,
                        NEARDUP_SCHEMA, V2BError, canonical_json_bytes,
                        identity_key, seeded_hash, sha256_json)
from v2b_neardup import (lean_keyword_provenance_hash, lex_unit,
                         lexical_records, load_lean_keyword_freeze,
                         verbatim_hash)


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


def _lexed(language, text):
    """Real A6 verbatim hash for one unit payload (B5 re-lex binding)."""
    return verbatim_hash(lex_unit(language, text))


def _lean_chain(td, jaccard="0.80", big_dep=False):
    repo = "mathlib4"
    corpus_sha = EXPECTED[repo][1]
    root = os.path.join(td, "corpus")
    # pre is a BYTE-IDENTICAL earlier copy of the target: a genuine
    # verbatim twin under the real lexer, wholly earlier for k2 excision.
    a_lines = ["theorem t : True :=\n", "  trivial\n",
               "theorem t : True :=\n", "  trivial\n",
               "def local1 : Nat :=\n", "  2\n"]
    a_text = "".join(a_lines)
    b_lines = ["def dep2 : Nat :=\n", "  9\n",
               "def dep : Nat :=\n", "  3\n",
               "def rev : Nat :=\n", "  4\n",
               "def ndup : Nat :=\n", "  5\n",
               "def free1 : Nat :=\n", "  6\n",
               "def free2 : Nat :=\n", "  7\n"]
    if big_dep:
        b_lines += ["def big : Nat :=\n"] + \
            ["  -- pad pad pad pad\n"] * 1200 + ["  8\n"]
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
               "M.B.rev": span(b_lines, 4, 2), "M.B.ndup": span(b_lines, 6, 2),
               "M.B.free1": span(b_lines, 8, 2),
               "M.B.free2": span(b_lines, 10, 2)}
    if big_dep:
        b_spans["M.B.big"] = span(b_lines, 12, 1202)

    def decl(spans, name, header, split=":=", shell=()):
        start, end = spans[name]
        return dict(start_byte=start, end_byte=end, header_bytes=header,
                    split_kind=split, shell=list(shell))

    b_decls = {
        "M.B.dep2": decl(b_spans, "M.B.dep2", len("def dep2 : Nat "),
                         split=None),
        "M.B.dep": decl(b_spans, "M.B.dep", len("def dep : Nat ")),
        "M.B.rev": decl(b_spans, "M.B.rev", len("def rev : Nat ")),
        "M.B.ndup": decl(b_spans, "M.B.ndup", len("def ndup : Nat ")),
        "M.B.free1": decl(b_spans, "M.B.free1", len("def free1 : Nat ")),
        "M.B.free2": decl(b_spans, "M.B.free2", len("def free2 : Nat "))}
    edges = [["M.A", "M.A.t", "M.B", "M.B.dep"],
             ["M.B", "M.B.dep", "M.B", "M.B.dep2"],
             ["M.B", "M.B.rev", "M.A", "M.A.t"],
             ["M.A", "M.A.t", "M.B", "M.B.ndup"]]
    if big_dep:
        b_decls["M.B.big"] = decl(b_spans, "M.B.big", len("def big : Nat "))
        edges.append(["M.A", "M.A.t", "M.B", "M.B.big"])
    extraction = dict(
        schema="v2a_lean_extract_v3", repo=repo,
        files=[
            dict(module="M.A", source=a_path, rel="A.lean",
                 source_sha256=a_sha,
                 decls={
                     "M.A.pre": decl(a_spans, "M.A.pre",
                                     len("theorem t : True ")),
                     "M.A.t": decl(a_spans, "M.A.t",
                                   len("theorem t : True "),
                                   shell=["open Nat"]),
                     "M.A.local1": decl(a_spans, "M.A.local1",
                                        len("def local1 : Nat "))}),
            dict(module="M.B", source=b_path, rel="B.lean",
                 source_sha256=b_sha, decls=b_decls)],
        graph=dict(edges=edges))
    extraction_path = os.path.join(td, "extraction.json")
    json.dump(extraction, open(extraction_path, "w"))
    extraction_sha = _sha(open(extraction_path, "rb").read())

    freeze_path = _freeze(td)
    _, freeze_binding = load_lean_keyword_freeze(freeze_path)
    units = []
    texts = {}
    for module, text, lines_spans in (("M.A", a_text, a_spans),
                                      ("M.B", b_text, b_spans)):
        for name, (start, end) in lines_spans.items():
            identity = [module, name]
            texts[name] = text[start:end]
            units.append(dict(identity=identity,
                              key=identity_key("lean", identity),
                              verbatim_sha256=_lexed("lean", texts[name]),
                              normalized_sha256="b" * 64))
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
                outcome=outcome_path, freeze=freeze_path,
                a_spans=a_spans, b_spans=b_spans, texts=texts)


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
        assert manifest["arms_included"] == \
            ["k1", "k2", "k3", "k4", "k3s", "k4s", "k5", "k6"]
        assert manifest["arms_deferred"]                    # never silent
        assert manifest["n_targets"] == 1
        row = manifest["targets"][0]
        # closure = dep, dep2, ndup; ndup excluded as labeled near-dup
        assert row["n_closure_units"] == 3
        assert row["n_k4_units"] == 2
        assert row["n_near_dup_excluded"] == 1
        assert row["n_same_file_excluded"] == 0
        assert row["n_reverse_closure"] == 1                # rev
        assert row["n_universe"] == 4         # dep, dep2, free1, free2
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
        # exclusion masses: counts + byte totals + set hashes
        masses = row["exclusion_masses"]

        def span_bytes(spans, name):
            start, end = spans[name]
            return end - start

        assert masses["near_dup"]["n"] == 2                 # pre twin + ndup
        assert masses["near_dup"]["bytes"] == \
            span_bytes(chain["a_spans"], "M.A.pre") + \
            span_bytes(chain["b_spans"], "M.B.ndup")
        assert masses["reverse_closure"]["n"] == 1
        assert masses["reverse_closure"]["bytes"] == \
            span_bytes(chain["b_spans"], "M.B.rev")
        assert masses["universe"]["n"] == 4
        assert masses["universe"]["bytes"] == sum(
            span_bytes(chain["b_spans"], name)
            for name in ("M.B.dep2", "M.B.dep", "M.B.free1", "M.B.free2"))
        assert masses["k4_near_dup_excluded"]["n"] == 1     # ndup only
        for mass in masses.values():
            assert set(mass) == {"n", "bytes", "sha256"}
        assert row["n_k3_unsplit_bytes"] == \
            span_bytes(chain["b_spans"], "M.B.dep2")
        cell = row["arms"]["k3"][str(65536)]
        assert cell["n_unsplit_units"] == 1
        unsplit_row = [r for r in cell["selected_units"]
                       if tuple(r["identity"]) == ("M.B", "M.B.dep2")][0]
        assert cell["n_unsplit_bytes"] == unsplit_row["included_bytes"]
        # external mass: counts unavailable in this extraction -> null,
        # bytes null with explicit unbound-source reason, never fabricated
        assert row["external"]["n_external"] is None
        assert row["external"]["bytes"] is None
        assert "unbound" in row["external"]["reason"]
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
        assert row["n_universe"] == 5
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


def test_neardup_universe_and_relex_fail_closed():
    # near-dup table missing one extraction unit: not the exact universe
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        value = json.load(open(chain["neardup"]))
        value["units"] = value["units"][:-1]
        json.dump(value, open(chain["neardup"], "w"))
        try:
            _build(chain)
            assert False, "partial near-dup unit universe accepted"
        except V2BError as err:
            assert "universe" in str(err)
    # Jaccard pair naming a key outside the extraction universe
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        value = json.load(open(chain["neardup"]))
        value["jaccard_pairs"].append(dict(
            a=identity_key("lean", ["M.X", "M.X.ghost"]),
            b=value["units"][0]["key"], intersection=9, union=10))
        json.dump(value, open(chain["neardup"], "w"))
        try:
            _build(chain)
            assert False, "foreign Jaccard pair key accepted"
        except V2BError as err:
            assert "universe" in str(err)
    # BM25 term source must re-lex to the sealed verbatim hash
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        value = json.load(open(chain["neardup"]))
        free1_key = identity_key("lean", ["M.B", "M.B.free1"])
        for unit in value["units"]:
            if unit["key"] == free1_key:
                unit["verbatim_sha256"] = "e" * 64
        json.dump(value, open(chain["neardup"], "w"))
        try:
            _build(chain)
            assert False, "BM25 re-lex hash drift accepted"
        except V2BError as err:
            assert "re-lex" in str(err)


def test_k5_seeded_orders_and_seed_budgets():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        row = _build(chain)["targets"][0]
        k5 = row["arms"]["k5"]
        assert set(k5) == {"0", "1", "2"}
        pool = [identity_key("lean", ["M.B", name])
                for name in ("M.B.free1", "M.B.free2")]
        for seed in (0, 1, 2):
            arm = k5[str(seed)]
            assert arm["n_units"] == 2
            # §14.21 exact key, §15.A4b descending order (lowest nearest)
            expected = sorted(
                pool,
                key=lambda key: seeded_hash(
                    f"k5:{seed}", "mathlib4", "M.A", "M.A.t",
                    *json.loads(key)),
                reverse=True)
            assert arm["order_sha256"] == sha256_json(expected)
            budget = str(B_STAR)
            cell = arm["cells"][budget]
            rendered = [identity_key("lean", r["identity"])
                        for r in cell["selected_units"]]
            assert rendered == expected
        assert set(k5["0"]["cells"]) == {"4096", "16384", "65536"}
        assert set(k5["1"]["cells"]) == {str(B_STAR)}       # NLL-only @ B*
        assert set(k5["2"]["cells"]) == {str(B_STAR)}


def test_k6_bm25_exact_frozen_formula():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        row = _build(chain)["targets"][0]
        k6 = row["arms"]["k6"]
        assert k6["n_docs"] == 4
        # independent recomputation: df/avgdl over the FULL 9-unit corpus
        all_terms = {}
        for name, text in chain["texts"].items():
            terms = Counter(tuple(r) for r in
                            lexical_records(lex_unit("lean", text)))
            all_terms[name] = terms
        n_corpus = len(all_terms)
        assert n_corpus == 9
        avgdl = sum(sum(t.values()) for t in all_terms.values()) / n_corpus
        df = Counter()
        for terms in all_terms.values():
            for term in terms:
                df[term] += 1
        query = Counter(tuple(r) for r in lexical_records(
            lex_unit("lean", "open Nat\n" + "theorem t : True ")))
        universe = ("M.B.dep2", "M.B.dep", "M.B.free1", "M.B.free2")
        expected_scores = {}
        for name in universe:
            terms = all_terms[name]
            doc_len = sum(terms.values())
            score = 0.0
            for term in sorted(query,
                               key=lambda t: canonical_json_bytes(list(t))):
                tf = terms.get(term, 0)
                if tf == 0:
                    continue
                idf = math.log(1 + (n_corpus - df[term] + 0.5)
                               / (df[term] + 0.5))
                score += query[term] * idf * tf * (BM25_K1 + 1) \
                    / (tf + BM25_K1 * (1 - BM25_B
                                       + BM25_B * doc_len / avgdl))
            expected_scores[identity_key("lean", ["M.B", name])] = score
        tie = {key: seeded_hash(K6_TIE_LABEL, "mathlib4", "M.A", "M.A.t",
                                *json.loads(key))
               for key in expected_scores}
        expected_order = sorted(expected_scores, key=tie.__getitem__,
                                reverse=True)
        expected_order.sort(key=expected_scores.__getitem__)
        assert k6["scores_sha256"] == sha256_json(
            [[key, expected_scores[key]] for key in expected_order])
        cell = k6["cells"][str(65536)]
        rendered = [identity_key("lean", r["identity"])
                    for r in cell["selected_units"]]
        assert rendered == expected_order
        for r in cell["selected_units"]:
            key = identity_key("lean", r["identity"])
            assert r["bm25_score"] == expected_scores[key]
        # forward-closure overlap recorded (dep, dep2 retrieved-allowed)
        assert cell["n_in_forward_closure"] == 2


def test_k3s_k4s_same_dependency_set():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td, big_dep=True)
        row = _build(chain)["targets"][0]
        b_star_cell = row["arms"]["k4"][str(B_STAR)]
        wholes = [r["identity"] for r in b_star_cell["selected_units"]
                  if r["wholly_contained"]]
        partial = b_star_cell["partial_unit"]
        assert partial is not None
        assert tuple(partial["identity"]) == ("M.B", "M.B.big")
        for name in ("k3s", "k4s"):
            arm = row["arms"][name]
            assert arm["n_units"] == len(wholes)
            assert arm["identities"] == wholes
            assert arm["excluded_partial"]["identity"] == \
                partial["identity"]
            assert arm["excluded_partial"]["included_bytes"] == \
                partial["included_bytes"]
            assert arm["context_bytes"] > 0
        # budget-UNMATCHED by design: §15.A10 records both byte lengths
        # and promises no direction (a tiny body makes the interface side
        # the longer one)
        assert row["arms"]["k4s"]["context_bytes"] != \
            row["arms"]["k3s"]["context_bytes"]
        assert row["arms"]["k3s"]["n_unsplit_units"] == \
            (1 if ["M.B", "M.B.dep2"] in wholes else 0)


def test_materialize_round_trip():
    with tempfile.TemporaryDirectory() as td:
        chain = _lean_chain(td)
        manifest = _build(chain)
        manifest_path = os.path.join(td, "manifest.json")
        json.dump(manifest, open(manifest_path, "w"))
        blobs = materialize(manifest_path, chain["sample"], chain["repo"],
                            chain["candidates"], chain["extraction"],
                            chain["neardup"], chain["outcome"],
                            chain["freeze"])
        target_key = identity_key("lean", ["M.A", "M.A.t"])
        row = manifest["targets"][0]
        blob = blobs[target_key]
        assert _sha(blob["prefix"]) == row["prefix_sha256"]
        assert _sha(blob["body"]) == row["body_sha256"]
        assert blob["k1"] == b""
        assert _sha(blob["k4:65536"]) == \
            row["arms"]["k4"][str(65536)]["context_sha256"]
        assert _sha(blob["k2:4096"]) == \
            row["arms"]["k2"][str(4096)]["context_sha256"]
        assert _sha(blob[f"k5:0:{B_STAR}"]) == \
            row["arms"]["k5"]["0"]["cells"][str(B_STAR)]["context_sha256"]
        assert _sha(blob[f"k6:{B_STAR}"]) == \
            row["arms"]["k6"]["cells"][str(B_STAR)]["context_sha256"]
        assert _sha(blob["k3s"]) == row["arms"]["k3s"]["context_sha256"]
        assert _sha(blob["k4s"]) == row["arms"]["k4s"]["context_sha256"]
        # a manifest this chain did not produce is refused
        tampered = dict(manifest, targets_sha256="0" * 64)
        tampered_path = os.path.join(td, "tampered.json")
        json.dump(tampered, open(tampered_path, "w"))
        try:
            materialize(tampered_path, chain["sample"], chain["repo"],
                        chain["candidates"], chain["extraction"],
                        chain["neardup"], chain["outcome"],
                        chain["freeze"])
            assert False, "tampered manifest materialized"
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
                                   header_bytes=len("def f(x):"),
                                   n_external=3)]),
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
                                   verbatim_sha256=_lexed("python", a_text),
                                   normalized_sha256="b" * 64),
                              dict(identity=["pkg.b", "g", 0],
                                   key=identity_key("python",
                                                    ["pkg.b", "g", 0]),
                                   verbatim_sha256=_lexed("python", b_text),
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
        # python external counts pass through; bytes stay null
        assert row["external"]["n_external"] == 3
        assert row["external"]["bytes"] is None
        # k5 pool is empty (universe == forward closure); k6 has one doc
        assert row["arms"]["k5"]["0"]["n_units"] == 0
        assert row["arms"]["k5"]["0"]["cells"] == {}
        assert row["arms"]["k6"]["n_docs"] == 1


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
