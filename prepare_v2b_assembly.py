#!/usr/bin/env python3
"""Production V2-b assembly driver (B1) — manifest-producing slice.

Consumes and REHASHES the full bound evidence chain for one corpus —
bound sample, candidate table, extraction, A6 near-dup table + label
outcome, and (Lean) the keyword freeze — then builds the target-level
context machinery frozen in DESIGN_V2 §15: the leak-free universe U(t)
(§14.14: all spanned units minus the target's file, its A6
near-duplicates under the LABEL-RESOLVED rules, and its transitive
reverse closure), the §15.A4 canonical dependency order, and the
§15.A4b maximal renderings with byte-suffix budget cells.

SLICE SCOPE (recorded in the manifest, never silent): arms k1, k2, k3,
k4, k5 (seeds 0-2 per §14.21, seeds 1-2 at B* only), k6 (§14.8/§15.A11
exact BM25 with the re-lex-and-verify term source: every document is
re-lexed with the frozen A6 lexer and its verbatim hash must equal the
sealed near-dup table's), and the §15.A10 k3s/k4s same-dependency-set
sensitivities. k7 (order-artifact stream), k4x (physlib combined
graph), and the §14.26 k6-realistic variant are DEFERRED. Token-level
assertions (§14.13/T*) belong to the scoring side per the frozen B6
decision. Exclusion sets are bound as counts + byte masses + set
hashes; k5/k6 orders over the full universe are bound by order/score
hashes with every in-budget identity recorded explicitly.

Hard property checks (assembly failures, never warnings): smaller
budgets are literal byte suffixes of larger ones per arm; at most one
partial unit per cell; no context banner names the target's source
path; prefix + body round-trip byte-exactly against the live source.
"""
import argparse
import math
import sys
from collections import Counter

from finalize_v2b_a6 import EXPECTED
from finalize_v2b_sample import N_PER_CORPUS
from provenance import head_commit, source_clean, source_tree_hash
from v2b_assemble import (canonical_dependency_order, interface_payload,
                          render_chunks, splice_local_prefix,
                          utf8_budget_suffix)
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, NEARDUP_SCHEMA, V2BError,
                        artifact_binding, canonical_json_bytes, identity_key,
                        seeded_hash, sha256_bytes, sha256_json,
                        validate_identity, write_new_json)
from v2b_neardup import (LEAN_EXTRACT_SCHEMA, PYTHON_EXTRACT_SCHEMA,
                         lex_unit, lexical_records, load_lean_keyword_freeze,
                         meets, verbatim_hash)

BUDGET_GRID = (4096, 16384, 65536)        # §14.12/§1: {4,16,64} KiB
B_STAR = 16384                            # §1: B* primary budget
K5_SEEDS = (0, 1, 2)                      # §14.21: primary 0; 1-2 NLL @ B*
K6_TIE_LABEL = "k6tie:v2b:20260808"       # §15.A4b frozen tie key
BM25_K1, BM25_B = 1.2, 0.75              # §15.A11 frozen, untuned
SLICE_ARMS = ("k1", "k2", "k3", "k4", "k3s", "k4s", "k5", "k6")
DEFERRED_ARMS = ("k7", "k4x", "k6-realistic")
JACCARD_THRESHOLDS = {"0.70": (7, 10), "0.80": (4, 5), "0.90": (9, 10)}


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


# ------------------------------------------------------------ bindings

def _load_chain(sample_path, repo, candidates_path, extraction_path,
                neardup_path, outcome_path, keyword_freeze_path):
    if repo not in EXPECTED:
        raise V2BError(f"unexpected assembly corpus {repo!r}")
    language, corpus_sha = EXPECTED[repo]
    sample_binding, sample = artifact_binding(sample_path,
                                              BOUND_SAMPLE_SCHEMA)
    if sample.get("sampling_state") != "drawn" \
            or sample.get("n_requested_per_corpus") != N_PER_CORPUS \
            or not isinstance(sample.get("plans"), dict) \
            or repo not in sample["plans"]:
        raise V2BError("bound sample artifact is malformed or lacks corpus")
    plan = sample["plans"][repo]

    cand_binding, candidates = artifact_binding(candidates_path,
                                                CANDIDATES_SCHEMA)
    if plan.get("candidates_sha256") != cand_binding["sha256"] \
            or candidates.get("repo") != repo \
            or candidates.get("corpus_git_sha") != corpus_sha:
        raise V2BError("candidate table is not the sample's sealed input")

    extraction_binding, extraction = artifact_binding(extraction_path)
    expected_schema = LEAN_EXTRACT_SCHEMA if language == "lean" \
        else PYTHON_EXTRACT_SCHEMA
    if extraction.get("schema") != expected_schema \
            or extraction.get("repo") != repo:
        raise V2BError("extraction schema/repo drift at assembly")
    cand_extraction = candidates.get("extraction")
    if not isinstance(cand_extraction, dict) \
            or cand_extraction.get("sha256") != extraction_binding["sha256"]:
        raise V2BError("extraction is not the candidates' sealed input")

    neardup_binding, neardup = artifact_binding(neardup_path,
                                                NEARDUP_SCHEMA)
    if neardup.get("repo") != repo or neardup.get("language") != language \
            or neardup.get("extraction", {}).get("sha256") != \
            extraction_binding["sha256"]:
        raise V2BError("near-dup table is not bound to this extraction")

    outcome_binding, outcome = artifact_binding(outcome_path)
    if sample.get("a6_outcome", {}).get("sha256") != \
            outcome_binding["sha256"]:
        raise V2BError("A6 outcome is not the sample's sealed input")
    outcomes = outcome.get("outcomes")
    if not isinstance(outcomes, dict) \
            or outcome.get("outcomes_sha256") != sha256_json(outcomes):
        raise V2BError("A6 outcome content hash drift at assembly")

    freeze_binding = None
    if language == "lean":
        if not keyword_freeze_path:
            raise V2BError("Lean assembly requires the keyword freeze")
        _, freeze_binding = load_lean_keyword_freeze(keyword_freeze_path)
        if neardup.get("keyword_evidence") != freeze_binding:
            raise V2BError("near-dup table keyword freeze binding drift")
    return dict(language=language, corpus_git_sha=corpus_sha,
                sample=sample_binding, plan=plan,
                candidates=cand_binding,
                extraction=dict(extraction_binding,
                                schema=extraction.get("schema")),
                neardup=neardup_binding, outcome=outcome_binding,
                keyword_freeze=freeze_binding), \
        sample, candidates, extraction, neardup, outcome


# --------------------------------------------------------- corpus index

def _unit_index(extraction, language):
    """identity_key -> unit record with source, span, and split fields."""
    units = {}
    sources = {}
    for f in extraction.get("files", []):
        source = f.get("source")
        source_sha = f.get("source_sha256")
        rel = f.get("rel") or source
        if not isinstance(source, str) or not _hex(source_sha):
            raise V2BError("extraction file lacks source binding")
        sources[source] = source_sha
        if language == "lean":
            rows = [((f["module"], name), d) for name, d in
                    f.get("decls", {}).items()]
        else:
            rows = [(tuple(t["identity"]), t) for t in f.get("targets", [])]
        for identity, d in rows:
            identity = validate_identity(language, identity)
            key = identity_key(language, identity)
            if key in units:
                raise V2BError(f"duplicate extraction identity {key}")
            units[key] = dict(
                identity=list(identity), key=key, source=source,
                source_rel=rel, source_sha256=source_sha,
                start=d.get("start_byte"), end=d.get("end_byte"),
                header_bytes=d.get("header_bytes"),
                split_kind=d.get("split_kind"),
                shell=d.get("shell") if language == "lean" else None,
                n_external=d.get("n_external")
                if language == "python" else None)
    return units, sources


def _span_bytes(unit):
    start, end = unit.get("start"), unit.get("end")
    if not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) \
            or not 0 <= start < end:
        raise V2BError(f"unit span fields invalid: {unit.get('key')}")
    return end - start


def _set_mass(units, keys):
    """Bind one exclusion set: count, byte mass, and sorted-key hash."""
    ordered = sorted(keys)
    return dict(n=len(ordered),
                bytes=sum(_span_bytes(units[key]) for key in ordered),
                sha256=sha256_json(ordered))


# §14.3 external mass: the extraction exposes external reference COUNTS
# only (Lean: graph.external_reference_edges + external_ref_counts_by_target;
# Python: per-target n_external). No external source span is bound, so byte
# mass is NOT definable until a separately pinned external snapshot exists
# (e.g. physlib k4x): bytes stays null with an explicit reason, never
# fabricated. Pre-score §14.3 amendment pending.
_EXTERNAL_REASON = ("external-source-unbound: counts only; bytes definable "
                    "only under a separately pinned external snapshot "
                    "(pre-score §14.3 amendment pending)")


def _external_counts(extraction, language, target, target_key):
    if language == "python":
        n = target.get("n_external")
    else:
        counts = extraction.get("graph", {}).get(
            "external_ref_counts_by_target")
        if isinstance(counts, dict):
            n = counts.get(target_key, counts.get(target["identity"][1]))
        else:
            n = None
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        n = None
    return dict(n_external=n, bytes=None, reason=_EXTERNAL_REASON)


def _edges(extraction, language):
    out = []
    for e in extraction.get("graph", {}).get("edges", []):
        if language == "lean":
            if len(e) != 4:
                raise V2BError(f"lean edge is not a quadruple: {e!r}")
            out.append(([e[0], e[1]], [e[2], e[3]]))
        else:
            if len(e) != 6:
                raise V2BError(f"python edge is not a sextuple: {e!r}")
            out.append((list(e[:3]), list(e[3:])))
    return out


def _reverse_closure(edges, language, target_key):
    dependents_of = {}
    for dependent, dependency in edges:
        dependents_of.setdefault(
            identity_key(language, dependency), set()).add(
            identity_key(language, dependent))
    seen = set()
    stack = [target_key]
    while stack:
        key = stack.pop()
        for dependent in sorted(dependents_of.get(key, ())):
            if dependent not in seen:
                seen.add(dependent)
                stack.append(dependent)
    seen.discard(target_key)
    return seen


def _a6_exclusion_sets(neardup, outcome, language, unit_keys):
    """LABEL-RESOLVED near-duplicate adjacency (§14.6/§15.A6/§15.A11):
    verbatim-hash twins always; normalized-hash groups only in bands the
    8/8 collision audit ACTIVATED; Jaccard pairs at the calibrated
    threshold, or none when lexically inconclusive. Every near-dup key —
    unit rows, group members, and Jaccard pair sides — must belong to
    the EXACT extraction unit universe, fail-closed, before any
    adjacency is used."""
    table_keys = {unit["key"] for unit in neardup.get("units", [])}
    if table_keys != set(unit_keys):
        raise V2BError("near-dup table keys are not the exact extraction "
                       "unit universe")
    jaccard_out = outcome["outcomes"]["jaccard"].get(language, {})
    threshold = JACCARD_THRESHOLDS.get(jaccard_out.get("outcome"))
    activation = outcome["outcomes"]["collision_activation"].get(language,
                                                                 {})
    adjacency = {}

    def link(a, b):
        if a not in table_keys or b not in table_keys:
            raise V2BError(f"near-dup adjacency key outside the extraction "
                           f"universe: {a!r} ~ {b!r}")
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    by_verbatim = {}
    for unit in neardup.get("units", []):
        by_verbatim.setdefault(unit["verbatim_sha256"], []).append(
            unit["key"])
    for keys in by_verbatim.values():
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                link(keys[i], keys[j])
    for group in neardup.get("collision_groups", []):
        band = group.get("band")
        if not isinstance(activation.get(band), dict) \
                or activation[band].get("active") is not True:
            continue
        keys = [identity_key(language, m["identity"])
                for m in group.get("members", [])]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                link(keys[i], keys[j])
    if threshold is not None:
        for pair in neardup.get("jaccard_pairs", []):
            if pair["a"] not in table_keys or pair["b"] not in table_keys:
                raise V2BError("Jaccard pair key outside the extraction "
                               "universe")
            if meets(pair["intersection"], pair["union"], threshold):
                link(pair["a"], pair["b"])
    return adjacency


# ------------------------------------------------------------ rendering

def _read_source(units_row, cache):
    source = units_row["source"]
    if source not in cache:
        try:
            blob = open(source, "rb").read()
        except OSError as err:
            raise V2BError(f"cannot read assembly source {source}: {err}") \
                from err
        if sha256_bytes(blob) != units_row["source_sha256"]:
            raise V2BError(f"assembly source hash drift: {source}")
        cache[source] = blob
    return cache[source]


def _unit_payload(unit, cache):
    blob = _read_source(unit, cache)
    start, end = unit["start"], unit["end"]
    if not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) \
            or not 0 <= start < end <= len(blob):
        raise V2BError(f"assembly unit span invalid: {unit['key']}")
    return blob[start:end]


def _interface_or_verbatim(language, unit, payload):
    header = unit.get("header_bytes")
    split = unit.get("split_kind")
    if language == "lean" and split is None:
        return payload, False                 # §15.A11: verbatim, recorded
    if not isinstance(header, int) or isinstance(header, bool) \
            or not 0 < header < len(payload):
        raise V2BError(f"unit lacks a usable header boundary: {unit['key']}")
    return interface_payload(language, payload, header), True


def _prefix_and_body(language, unit, cache):
    payload = _unit_payload(unit, cache)
    header = unit.get("header_bytes")
    if not isinstance(header, int) or isinstance(header, bool) \
            or not 0 < header < len(payload):
        raise V2BError(f"target lacks a header/body split: {unit['key']}")
    shell = unit.get("shell") or []
    if not isinstance(shell, list) \
            or any(not isinstance(cmd, str) for cmd in shell):
        raise V2BError(f"target shell is malformed: {unit['key']}")
    shell_text = "".join(cmd + "\n" for cmd in shell).encode("utf-8")
    prefix = shell_text + payload[:header]
    body = payload[header:]
    if prefix[len(shell_text):] + body != payload:
        raise AssertionError("prefix/body do not round-trip the target span")
    return prefix, body


def _cells_for_rendering(rendering, spans, budgets, collect=None,
                         collect_key=None):
    cells = {}
    previous = None
    for budget in sorted(budgets):
        cell = utf8_budget_suffix(rendering, spans, budget)
        context = cell.pop("context")
        cell["context_sha256"] = sha256_bytes(context)
        if previous is not None and not context.endswith(previous):
            raise AssertionError(
                "byte-suffix nesting violated across budgets")
        partials = [row for row in cell["selected_units"]
                    if not row["wholly_contained"]]
        if len(partials) > 1:
            raise AssertionError("more than one partial unit in a cell")
        cells[str(budget)] = cell
        if collect is not None:
            collect[f"{collect_key}:{budget}"] = context
        previous = context
    return cells


def _render_unit_arm(language, ordered_units, target_source, budgets,
                     collect=None, collect_key=None):
    for unit in ordered_units:
        if unit["relpath"] == target_source:
            raise AssertionError(
                "context banner would name the target source path")
    rendering, spans = render_chunks(language, [
        dict(identity=unit["identity"], relpath=unit["relpath"],
             payload=unit["payload"]) for unit in ordered_units])
    return _cells_for_rendering(rendering, spans, budgets, collect=collect,
                                collect_key=collect_key)


def _annotate_cells(cells, language, field, value_by_key):
    """Attach a per-unit field to every selected row, plus cell totals."""
    for cell in cells.values():
        marked = 0
        for row in cell["selected_units"]:
            value = value_by_key.get(identity_key(language, row["identity"]))
            if value is not None:
                row[field] = value
                marked += 1
        cell[f"n_{field}"] = marked
    return cells


# ---------------------------------------------------------------- BM25

def _bm25_corpus_index(language, units, verbatim_by_key, cache):
    """§14.8/§15.A11 frozen index over the FULL same-corpus unit universe.

    B5 term source, re-lex-and-verify: every declaration unit is re-lexed
    with the exact frozen A6 lexer and the resulting verbatim hash must
    equal the sealed near-dup table's row — the BM25 term stream is
    thereby bound to the A6 evidence, fail-closed. Terms are the lexical
    typed records (layout sentinels excluded); df and avgdl are frozen
    over ALL corpus units, never per target."""
    index = {}
    df = {}
    total_dl = 0
    for key in sorted(units):
        unit = units[key]
        payload = _unit_payload(unit, cache)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as err:
            raise V2BError(f"unit payload is not UTF-8: {key}: {err}") \
                from err
        records = lex_unit(language, text)
        if verbatim_hash(records) != verbatim_by_key.get(key):
            raise V2BError(f"BM25 re-lex verbatim hash drift against the "
                           f"sealed near-dup table: {key}")
        terms = Counter(tuple(record) for record in
                        lexical_records(records))
        index[key] = (terms, sum(terms.values()))
        total_dl += sum(terms.values())
        for term in terms:
            df[term] = df.get(term, 0) + 1
    n_docs = len(index)
    if n_docs == 0 or total_dl == 0:
        raise V2BError("BM25 universe is empty or has no lexical terms")
    return dict(index=index, df=df, n_docs=n_docs,
                avgdl=total_dl / n_docs)


def _bm25_query_terms(language, prefix):
    try:
        text = prefix.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"k6 query prefix is not UTF-8: {err}") from err
    return Counter(tuple(record) for record in
                   lexical_records(lex_unit(language, text)))


def _bm25_score(query_terms, doc_terms, doc_len, corpus):
    """Exact §15.A11 sum over DISTINCT query terms, raw linear qtf.

    IEEE-754 double summation in ascending canonical-JSON term order —
    the one summation-order choice A11 leaves open, frozen here."""
    df, n_docs, avgdl = corpus["df"], corpus["n_docs"], corpus["avgdl"]
    score = 0.0
    for term in sorted(query_terms,
                       key=lambda t: canonical_json_bytes(list(t))):
        tf = doc_terms.get(term, 0)
        if tf == 0:
            continue
        idf = math.log(1 + (n_docs - df.get(term, 0) + 0.5)
                       / (df.get(term, 0) + 0.5))
        score += query_terms[term] * idf * tf * (BM25_K1 + 1) \
            / (tf + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / avgdl))
    return score


def _assemble_target(language, repo, target_identity, units, edges,
                     adjacency, cache, budgets, extraction, bm25,
                     collect=None):
    target_key = identity_key(language, target_identity)
    if target_key not in units:
        raise V2BError(f"sampled target lacks an extraction unit: "
                       f"{target_key}")
    target = units[target_key]
    prefix, body = _prefix_and_body(language, target, cache)
    if collect is not None:
        collect["prefix"], collect["body"] = prefix, body
    near_dups = set(adjacency.get(target_key, ()))
    reverse = _reverse_closure(edges, language, target_key)
    same_file = {key for key, unit in units.items()
                 if unit["source"] == target["source"]
                 and key != target_key}
    universe = set(units) - {target_key} - same_file - near_dups - reverse

    order = canonical_dependency_order(
        language, repo, target_identity,
        [unit["identity"] for unit in units.values()],
        [list(edge) for edge in edges])
    closure_keys = {identity_key(language, identity)
                    for identity in order["unit_order"]}
    excluded_same_file = []
    excluded_near_dup = []
    k4_units = []
    for identity in order["unit_order"]:
        key = identity_key(language, identity)
        if key in same_file:
            excluded_same_file.append(key)
            continue
        if key in near_dups:
            excluded_near_dup.append(key)
            continue
        unit = units[key]
        k4_units.append(dict(identity=unit["identity"],
                             relpath=unit["source_rel"],
                             payload=_unit_payload(unit, cache),
                             unit=unit))
    unsplit_bytes_by_key = {}
    k3_units = []
    for row in k4_units:
        payload, split = _interface_or_verbatim(language, row["unit"],
                                                row["payload"])
        if not split:
            unsplit_bytes_by_key[identity_key(language, row["identity"])] \
                = len(payload)
        k3_units.append(dict(identity=row["identity"],
                             relpath=row["relpath"], payload=payload))

    arms = {}
    arms["k1"] = dict(context_sha256=sha256_bytes(b""), context_bytes=0,
                      budget_bytes=None, selected_units=[],
                      separator_bytes=0)
    if collect is not None:
        collect["k1"] = b""
    target_blob = _read_source(target, cache)
    # §15.A11: k2 excises wholly-earlier near-duplicate-of-target spans
    # in the target's own file, once, before any suffix is taken.
    k2 = splice_local_prefix(
        target_blob, target["start"],
        [dict(start_byte=units[key]["start"], end_byte=units[key]["end"],
              identity=units[key]["identity"])
         for key in sorted(near_dups)
         if units[key]["source"] == target["source"]
         and units[key]["end"] <= target["start"]])
    arms["k2"] = _cells_for_rendering(k2["rendering"], k2["spans"],
                                      budgets, collect=collect,
                                      collect_key="k2")
    arms["k2_splice"] = dict(
        merged_exclusions=k2["merged_exclusions"],
        retained_intervals=k2["retained_intervals"],
        separator_bytes=k2["separator_bytes"])
    if k4_units:
        arms["k4"] = _render_unit_arm(language, k4_units,
                                      target["source_rel"], budgets,
                                      collect=collect, collect_key="k4")
        arms["k3"] = _render_unit_arm(language, k3_units,
                                      target["source_rel"], budgets,
                                      collect=collect, collect_key="k3")
        # §15.A11: per-cell verbatim-rendered (unsplit) unit accounting.
        for cell in arms["k3"].values():
            included = [(row, unsplit_bytes_by_key.get(
                identity_key(language, row["identity"])))
                for row in cell["selected_units"]]
            cell["n_unsplit_units"] = sum(
                1 for _, full in included if full is not None)
            cell["n_unsplit_bytes"] = sum(
                row["included_bytes"] for row, full in included
                if full is not None)
    else:
        arms["k4"] = {}
        arms["k3"] = {}
    arms["k3s"], arms["k4s"] = _sensitivity_arms(
        language, arms["k4"], units, cache, unsplit_bytes_by_key, collect)
    arms["k5"] = _k5_arm(language, repo, target_identity, target, units,
                         universe, closure_keys, cache, budgets, collect)
    arms["k6"] = _k6_arm(language, repo, target_identity, target, units,
                         universe, closure_keys, prefix, cache, budgets,
                         bm25, collect)
    return dict(
        identity=list(target_identity), key=target_key,
        prefix_sha256=sha256_bytes(prefix), prefix_bytes=len(prefix),
        body_sha256=sha256_bytes(body), body_bytes=len(body),
        source_rel=target["source_rel"],
        n_closure_units=len(order["unit_order"]),
        n_k4_units=len(k4_units),
        n_k3_unsplit_units=len(unsplit_bytes_by_key),
        n_k3_unsplit_bytes=sum(unsplit_bytes_by_key.values()),
        n_same_file_excluded=len(excluded_same_file),
        n_near_dup_excluded=len(excluded_near_dup),
        n_reverse_closure=len(reverse),
        n_universe=len(universe),
        exclusion_masses=dict(
            same_file=_set_mass(units, same_file),
            near_dup=_set_mass(units, near_dups),
            reverse_closure=_set_mass(units, reverse),
            universe=_set_mass(units, universe),
            k4_same_file_excluded=_set_mass(units, excluded_same_file),
            k4_near_dup_excluded=_set_mass(units, excluded_near_dup)),
        external=_external_counts(extraction, language, target, target_key),
        target_scc=order["target_scc"],
        arms=arms)


def _sensitivity_arms(language, k4_cells, units, cache,
                      unsplit_bytes_by_key, collect=None):
    """§15.A10 k3s/k4s: exactly the units WHOLLY contained in the k4 B*
    suffix, partial excluded from both sides (identity + bytes recorded),
    rendered twice with NO truncation; byte lengths deliberately unequal
    and both recorded. Budget-UNMATCHED labeled sensitivities."""
    if not k4_cells:
        return {}, {}
    cell = k4_cells.get(str(B_STAR))
    if cell is None:
        raise V2BError(f"budget grid lacks B*={B_STAR}; k3s/k4s undefined")
    partial = cell.get("partial_unit")
    excluded = dict(identity=partial["identity"],
                    included_bytes=partial["included_bytes"]) \
        if partial else None
    impl_units, iface_units = [], []
    n_unsplit = 0
    for row in cell["selected_units"]:
        if not row["wholly_contained"]:
            continue
        unit = units[identity_key(language, row["identity"])]
        payload = _unit_payload(unit, cache)
        impl_units.append(dict(identity=unit["identity"],
                               relpath=unit["source_rel"], payload=payload))
        ipayload, split = _interface_or_verbatim(language, unit, payload)
        if not split:
            n_unsplit += 1
        iface_units.append(dict(identity=unit["identity"],
                                relpath=unit["source_rel"],
                                payload=ipayload))
    out = []
    for name, rows in (("k3s", iface_units), ("k4s", impl_units)):
        rendering = b""
        if rows:
            rendering, _ = render_chunks(language, rows)
        arm = dict(context_sha256=sha256_bytes(rendering),
                   context_bytes=len(rendering),
                   n_units=len(rows),
                   identities=[row["identity"] for row in rows],
                   excluded_partial=excluded)
        if name == "k3s":
            arm["n_unsplit_units"] = n_unsplit
        out.append(arm)
        if collect is not None:
            collect[name] = rendering
    return out[0], out[1]


def _k5_arm(language, repo, target_identity, target, units, universe,
            closure_keys, cache, budgets, collect=None):
    """§14.21/§15.A4b k5: U(t) minus the forward closure, ranked by the
    frozen per-(target, seed) hash, rendered top-to-bottom DESCENDING so
    the lowest hash is query-nearest and every suffix reproduces the
    frozen draw. Seed 0 renders the full grid; seeds 1-2 (NLL-only
    sensitivity) render B* only. The seed label is decimal: "k5:0"."""
    pool = sorted(universe - closure_keys)
    seeds = {}
    for seed in K5_SEEDS:
        label = f"k5:{seed}"
        priority = {key: seeded_hash(label, repo, *target_identity,
                                     *units[key]["identity"])
                    for key in pool}
        ordered = sorted(pool, key=priority.__getitem__, reverse=True)
        seed_budgets = budgets if seed == 0 else (B_STAR,)
        cells = {}
        if ordered:
            cells = _render_unit_arm(
                language,
                [dict(identity=units[key]["identity"],
                      relpath=units[key]["source_rel"],
                      payload=_unit_payload(units[key], cache))
                 for key in ordered],
                target["source_rel"], seed_budgets, collect=collect,
                collect_key=f"k5:{seed}")
        seeds[str(seed)] = dict(
            n_units=len(ordered),
            n_bytes=sum(_span_bytes(units[key]) for key in ordered),
            order_sha256=sha256_json(ordered),
            cells=cells)
    return seeds


def _k6_arm(language, repo, target_identity, target, units, universe,
            closure_keys, prefix, cache, budgets, bm25, collect=None):
    """§14.8/§15.A4b/§15.A11 k6: BM25 over U(t) (forward deps allowed),
    query = the exact common unscored prefix under the same A6 lexer,
    ordered score-ascending with the frozen k6tie hash DESCENDING within
    equal scores (lower hash nearer the query). Selected rows carry
    full-precision scores; the full ordered (key, score) vector is bound
    by scores_sha256. Forward-closure overlap is recorded per cell."""
    query_terms = _bm25_query_terms(language, prefix)
    docs = sorted(universe)
    scores = {}
    for key in docs:
        doc_terms, doc_len = bm25["index"][key]
        scores[key] = _bm25_score(query_terms, doc_terms, doc_len, bm25)
    tie = {key: seeded_hash(K6_TIE_LABEL, repo, *target_identity,
                            *units[key]["identity"])
           for key in docs}
    ordered = sorted(docs, key=tie.__getitem__, reverse=True)
    ordered.sort(key=scores.__getitem__)      # stable: score asc, tie desc
    cells = {}
    if ordered:
        cells = _render_unit_arm(
            language,
            [dict(identity=units[key]["identity"],
                  relpath=units[key]["source_rel"],
                  payload=_unit_payload(units[key], cache))
             for key in ordered],
            target["source_rel"], budgets, collect=collect,
            collect_key="k6")
        _annotate_cells(cells, language, "bm25_score", scores)
        _annotate_cells(cells, language, "in_forward_closure",
                        {key: True for key in closure_keys})
    return dict(
        n_docs=len(docs),
        n_query_terms=len(query_terms),
        scores_sha256=sha256_json([[key, scores[key]] for key in ordered]),
        cells=cells)


# -------------------------------------------------------------- driver

def build_assembly(sample_path, repo, candidates_path, extraction_path,
                   neardup_path, outcome_path, keyword_freeze_path=None,
                   budgets=BUDGET_GRID, collect=None):
    bindings, sample, candidates, extraction, neardup, outcome = \
        _load_chain(sample_path, repo, candidates_path, extraction_path,
                    neardup_path, outcome_path, keyword_freeze_path)
    language = bindings["language"]
    units, _ = _unit_index(extraction, language)
    edges = _edges(extraction, language)
    adjacency = _a6_exclusion_sets(neardup, outcome, language, set(units))
    cache = {}
    bm25 = _bm25_corpus_index(
        language, units,
        {unit["key"]: unit["verbatim_sha256"]
         for unit in neardup.get("units", [])}, cache)
    targets = []
    for row in bindings["plan"].get("targets", []):
        identity = validate_identity(language, row.get("identity"))
        target_collect = None
        if collect is not None:
            target_collect = collect.setdefault(
                identity_key(language, identity), {})
        targets.append(_assemble_target(language, repo, list(identity),
                                        units, edges, adjacency, cache,
                                        budgets, extraction, bm25,
                                        collect=target_collect))
    if not targets:
        raise V2BError("bound sample plan has no targets for this corpus")
    targets.sort(key=lambda row: row["key"])
    return dict(
        schema=ASSEMBLY_SCHEMA, repo=repo, language=language,
        corpus_git_sha=bindings["corpus_git_sha"],
        budgets=list(budgets), b_star=B_STAR,
        k5_seeds=list(K5_SEEDS),
        bm25=dict(k1=BM25_K1, b=BM25_B, n_corpus_units=bm25["n_docs"],
                  avgdl=bm25["avgdl"]),
        arms_included=list(SLICE_ARMS),
        arms_deferred=list(DEFERRED_ARMS),
        bindings=dict(sample=bindings["sample"],
                      candidates=bindings["candidates"],
                      extraction=bindings["extraction"],
                      neardup=bindings["neardup"],
                      a6_outcome=bindings["outcome"],
                      keyword_freeze=bindings["keyword_freeze"]),
        n_targets=len(targets), targets=targets,
        targets_sha256=sha256_json(targets))


def materialize(manifest_path, sample_path, repo, candidates_path,
                extraction_path, neardup_path, outcome_path,
                keyword_freeze_path=None):
    """Deterministic evaluator materialization API (§15.A9 handoff).

    Re-runs the exact assembly construction from the same bound
    artifacts, verifies the rebuilt targets against the sealed
    manifest's targets_sha256 and bindings, and returns the concrete
    bytes per target: {target_key: {"prefix": bytes, "body": bytes,
    "k1"/"k3s"/"k4s": bytes, "<arm>:<budget>": bytes,
    "k5:<seed>:<budget>": bytes}}. eval_paired calls this instead of
    reverse-engineering selected-unit spans from JSON, then REHASHES
    prefix/context/body against the manifest before model load."""
    _, manifest = artifact_binding(manifest_path, ASSEMBLY_SCHEMA)
    if manifest.get("repo") != repo:
        raise V2BError("manifest repo mismatch at materialization")
    budgets = manifest.get("budgets")
    if not isinstance(budgets, list) or not budgets:
        raise V2BError("manifest lacks a budget grid")
    collect = {}
    rebuilt = build_assembly(sample_path, repo, candidates_path,
                             extraction_path, neardup_path, outcome_path,
                             keyword_freeze_path,
                             budgets=tuple(budgets), collect=collect)
    def _paths_stripped(bindings):
        if not isinstance(bindings, dict):
            return None
        return {name: {field: value for field, value in binding.items()
                       if field != "path"}
                if isinstance(binding, dict) else binding
                for name, binding in bindings.items()}

    # Bindings compare path-insensitively: the evaluator may see the same
    # sealed artifacts under a different job layout; sha256 is the binding.
    if rebuilt["targets_sha256"] != manifest.get("targets_sha256") \
            or _paths_stripped(rebuilt["bindings"]) != \
            _paths_stripped(manifest.get("bindings")):
        raise V2BError("materialized assembly does not reproduce the "
                       "sealed manifest")
    return collect


def prepare(sample_path, repo, candidates_path, extraction_path,
            neardup_path, outcome_path, keyword_freeze_path=None):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    manifest = build_assembly(sample_path, repo, candidates_path,
                              extraction_path, neardup_path, outcome_path,
                              keyword_freeze_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during assembly")
    manifest["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="prepare_v2b_assembly.py")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--neardup", required=True)
    ap.add_argument("--a6-outcome", required=True)
    ap.add_argument("--lean-keyword-freeze")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    manifest = prepare(args.sample, args.repo, args.candidates,
                       args.extraction, args.neardup, args.a6_outcome,
                       args.lean_keyword_freeze)
    digest = write_new_json(args.out, manifest)
    print(f"[v2b-assembly] {args.repo}: {manifest['n_targets']} targets, "
          f"arms {'/'.join(manifest['arms_included'])} "
          f"(deferred {'/'.join(manifest['arms_deferred'])}) -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
