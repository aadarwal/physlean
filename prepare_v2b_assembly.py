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
and k4 at the full budget grid. k5 (seeded orders), k6 (BM25 index),
k7 (filtered stream), k4x (physlib combined graph), and the k3s/k4s
sensitivity derivation are DEFERRED; the universe and reverse-closure
masses they need are already computed and recorded here. Token-level
assertions (§14.13/T*) belong to the scoring side per the frozen B6
decision.

Hard property checks (assembly failures, never warnings): smaller
budgets are literal byte suffixes of larger ones per arm; at most one
partial unit per cell; no context banner names the target's source
path; prefix + body round-trip byte-exactly against the live source.
"""
import argparse
import sys

from finalize_v2b_a6 import EXPECTED
from finalize_v2b_sample import N_PER_CORPUS
from provenance import head_commit, source_clean, source_tree_hash
from v2b_assemble import (canonical_dependency_order, interface_payload,
                          render_chunks, splice_local_prefix,
                          utf8_budget_suffix)
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, NEARDUP_SCHEMA, V2BError,
                        artifact_binding, identity_key, sha256_bytes,
                        sha256_json, validate_identity, write_new_json)
from v2b_neardup import (LEAN_EXTRACT_SCHEMA, PYTHON_EXTRACT_SCHEMA,
                         load_lean_keyword_freeze, meets)

BUDGET_GRID = (4096, 16384, 65536)        # §14.12/§1: {4,16,64} KiB
SLICE_ARMS = ("k1", "k2", "k3", "k4")
DEFERRED_ARMS = ("k5", "k6", "k7", "k4x", "k3s", "k4s")
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
                shell=d.get("shell") if language == "lean" else None)
    return units, sources


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


def _a6_exclusion_sets(neardup, outcome, language):
    """LABEL-RESOLVED near-duplicate adjacency (§14.6/§15.A6/§15.A11):
    verbatim-hash twins always; normalized-hash groups only in bands the
    8/8 collision audit ACTIVATED; Jaccard pairs at the calibrated
    threshold, or none when lexically inconclusive."""
    jaccard_out = outcome["outcomes"]["jaccard"].get(language, {})
    threshold = JACCARD_THRESHOLDS.get(jaccard_out.get("outcome"))
    activation = outcome["outcomes"]["collision_activation"].get(language,
                                                                 {})
    adjacency = {}

    def link(a, b):
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


def _cells_for_rendering(rendering, spans, budgets):
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
        previous = context
    return cells


def _render_unit_arm(language, ordered_units, target_source, budgets):
    for unit in ordered_units:
        if unit["relpath"] == target_source:
            raise AssertionError(
                "context banner would name the target source path")
    rendering, spans = render_chunks(language, [
        dict(identity=unit["identity"], relpath=unit["relpath"],
             payload=unit["payload"]) for unit in ordered_units])
    return _cells_for_rendering(rendering, spans, budgets)


def _assemble_target(language, repo, target_identity, units, edges,
                     adjacency, cache, budgets):
    target_key = identity_key(language, target_identity)
    if target_key not in units:
        raise V2BError(f"sampled target lacks an extraction unit: "
                       f"{target_key}")
    target = units[target_key]
    prefix, body = _prefix_and_body(language, target, cache)
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
    excluded_same_file = excluded_near_dup = 0
    k4_units = []
    for identity in order["unit_order"]:
        key = identity_key(language, identity)
        if key in same_file:
            excluded_same_file += 1
            continue
        if key in near_dups:
            excluded_near_dup += 1
            continue
        unit = units[key]
        k4_units.append(dict(identity=unit["identity"],
                             relpath=unit["source_rel"],
                             payload=_unit_payload(unit, cache),
                             unit=unit))
    n_unsplit = 0
    k3_units = []
    for row in k4_units:
        payload, split = _interface_or_verbatim(language, row["unit"],
                                                row["payload"])
        if not split:
            n_unsplit += 1
        k3_units.append(dict(identity=row["identity"],
                             relpath=row["relpath"], payload=payload))

    arms = {}
    arms["k1"] = dict(context_sha256=sha256_bytes(b""), context_bytes=0,
                      budget_bytes=None, selected_units=[],
                      separator_bytes=0)
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
                                      budgets)
    arms["k2_splice"] = dict(
        merged_exclusions=k2["merged_exclusions"],
        retained_intervals=k2["retained_intervals"],
        separator_bytes=k2["separator_bytes"])
    if k4_units:
        arms["k4"] = _render_unit_arm(language, k4_units,
                                      target["source_rel"], budgets)
        arms["k3"] = _render_unit_arm(language, k3_units,
                                      target["source_rel"], budgets)
    else:
        arms["k4"] = {}
        arms["k3"] = {}
    return dict(
        identity=list(target_identity), key=target_key,
        prefix_sha256=sha256_bytes(prefix), prefix_bytes=len(prefix),
        body_sha256=sha256_bytes(body), body_bytes=len(body),
        source_rel=target["source_rel"],
        n_closure_units=len(order["unit_order"]),
        n_k4_units=len(k4_units), n_k3_unsplit_units=n_unsplit,
        n_same_file_excluded=excluded_same_file,
        n_near_dup_excluded=excluded_near_dup,
        n_reverse_closure=len(reverse),
        n_universe=len(universe),
        target_scc=order["target_scc"],
        arms=arms)


# -------------------------------------------------------------- driver

def build_assembly(sample_path, repo, candidates_path, extraction_path,
                   neardup_path, outcome_path, keyword_freeze_path=None,
                   budgets=BUDGET_GRID):
    bindings, sample, candidates, extraction, neardup, outcome = \
        _load_chain(sample_path, repo, candidates_path, extraction_path,
                    neardup_path, outcome_path, keyword_freeze_path)
    language = bindings["language"]
    units, _ = _unit_index(extraction, language)
    edges = _edges(extraction, language)
    adjacency = _a6_exclusion_sets(neardup, outcome, language)
    cache = {}
    targets = []
    for row in bindings["plan"].get("targets", []):
        identity = validate_identity(language, row.get("identity"))
        targets.append(_assemble_target(language, repo, list(identity),
                                        units, edges, adjacency, cache,
                                        budgets))
    if not targets:
        raise V2BError("bound sample plan has no targets for this corpus")
    targets.sort(key=lambda row: row["key"])
    return dict(
        schema=ASSEMBLY_SCHEMA, repo=repo, language=language,
        corpus_git_sha=bindings["corpus_git_sha"],
        budgets=list(budgets),
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
