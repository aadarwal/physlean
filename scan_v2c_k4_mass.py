#!/usr/bin/env python3
"""V2-c model-free k4-fill scan (V2C_FEASIBILITY_AMENDMENT, Problem 2).

For EVERY candidate in a repo's committed candidate table, compute the
byte mass of its maximal k4 rendering — exactly the assembly's
arithmetic: sum over closure units (minus same-file and near-duplicate
exclusions) of len(make_chunk(...)) plus one separator byte per unit —
and whether it fills each frozen grid budget. The closure SET is the
frozen canonical_dependency_order semantics computed via one shared
SCC condensation (target-independent) plus a per-candidate BFS; a
built-in cross-check requires the scan's mass to reproduce the
committed pilot assembly's maximal k4 context_bytes for every pilot
target, fail-closed. No model output is read anywhere; the output is
a governance input only (per-repo primary-budget rule)."""
import argparse
import sys

from prepare_v2b_assembly import (
    BUDGET_GRID, EXPECTED, _a6_exclusion_sets, _edges, _unit_index,
    _unit_payload)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_assemble import _components, _graph_nodes, make_chunk
from v2b_common import (A6_OUTCOME_SCHEMA, CANDIDATES_SCHEMA, V2BError,
                        artifact_binding, identity_key, load_json,
                        validate_identity, write_new_json)
from v2b_lean_boundaries import load_boundary_overlay
from v2b_neardup import NEARDUP_SCHEMA

SCAN_SCHEMA = "v2c_k4_mass_scan_v1"
AMENDMENT_PATH = "results_v2/v2b/V2C_FEASIBILITY_AMENDMENT_DRAFT.md"
AMENDMENT_SHA256 = \
    "49ff6d8f9650921eeb02d0e0e404fa7d991f277a020fe783a10d4b1bced7bc37"


def _require(condition, message):
    if not condition:
        raise V2BError(message)


def scan_repo(repo, candidates_path, extraction_path, neardup_path,
              outcome_path, boundaries_path=None, corpus_root=None,
              pilot_manifest_path=None):
    _require(repo in EXPECTED, f"unknown corpus {repo!r}")
    language, _corpus_sha = EXPECTED[repo]
    cand_binding, candidates = artifact_binding(candidates_path,
                                                CANDIDATES_SCHEMA)
    _require(candidates.get("repo") == repo,
             "candidate table repo mismatch")
    ext_binding, extraction = artifact_binding(extraction_path)
    nd_binding, neardup = artifact_binding(neardup_path, NEARDUP_SCHEMA)
    out_binding, outcome = artifact_binding(outcome_path,
                                            A6_OUTCOME_SCHEMA)
    if pilot_manifest_path is not None:
        # Review blocker (b): the eligibility inputs are sha-pinned by
        # requiring byte-equality with the COMMITTED pilot assembly's
        # own chain bindings — the same sealed inputs, provably.
        _mbind, mvalue = artifact_binding(pilot_manifest_path)
        mbindings = mvalue.get("bindings") or {}
        for name, binding in (("candidates", cand_binding),
                              ("extraction", ext_binding),
                              ("neardup", nd_binding)):
            recorded = (mbindings.get(name) or {}).get("sha256")
            _require(recorded == binding["sha256"],
                     f"scan input {name} does not match the committed "
                     f"pilot assembly binding for {repo}")
    boundary_index = None
    bnd_binding = None
    if language == "lean":
        _require(boundaries_path is not None,
                 "lean scan requires the boundary overlay")
        bnd_binding, _boundary_artifact, boundary_index = \
            load_boundary_overlay(boundaries_path, extraction_path,
                                  expected_repo=repo)

    units = _unit_index(extraction, language, boundary_index,
                        corpus_root=corpus_root)
    edges = _edges(extraction, language)
    adjacency = _a6_exclusion_sets(neardup, outcome, language,
                                   set(units))

    # One shared SCC condensation — canonical_dependency_order's closure
    # is defined over components and is target-independent up to the
    # per-target BFS below.
    checked, normalized_edges = _graph_nodes(
        language, [unit["identity"] for unit in units.values()],
        [list(edge) for edge in edges])
    components, component_of = _components(checked, normalized_edges)
    condensed = {cid: set() for cid in range(len(components))}
    for src, dst in normalized_edges:
        a, b = component_of[src], component_of[dst]
        if a != b:
            condensed[a].add(b)
    members_by_cid = {}
    for identity, cid in component_of.items():
        members_by_cid.setdefault(cid, []).append(
            identity_key(language, identity))

    chunk_len = {}
    cache = {}
    for key, unit in units.items():
        chunk, _audit = make_chunk(language, unit["source_rel"],
                                   _unit_payload(unit, cache))
        chunk_len[key] = len(chunk) + 1  # join/separator LF per unit

    budgets = tuple(BUDGET_GRID)
    rows = []
    n_missing_unit = 0
    for target in candidates.get("targets") or ():
        identity = validate_identity(language, target.get("identity"))
        key = identity_key(language, identity)
        if key not in units:
            n_missing_unit += 1
            rows.append(dict(key=key, k4_mass=None, unit_missing=True,
                             fills={str(b): False for b in budgets}))
            continue
        target_unit = units[key]
        tcid = component_of[tuple(identity)]
        seen = {tcid}
        stack = [tcid]
        while stack:
            cid = stack.pop()
            for dep in condensed[cid]:
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        seen.discard(tcid)
        near = adjacency.get(key, set())
        mass = 0
        for cid in seen:
            for member in members_by_cid[cid]:
                if member == key or member in near:
                    continue
                if units[member]["source"] == target_unit["source"]:
                    continue
                mass += chunk_len[member]
        rows.append(dict(key=key, k4_mass=mass, unit_missing=False,
                         fills={str(b): mass >= b for b in budgets}))

    n_rows = len(rows)
    fractions = {str(b): (sum(1 for row in rows if row["fills"][str(b)])
                          / n_rows if n_rows else None)
                 for b in budgets}

    crosscheck = None
    if pilot_manifest_path is not None:
        manifest, _ = load_json(pilot_manifest_path)
        by_key = {row["key"]: row for row in rows}
        checked_n = 0
        for mrow in manifest.get("targets") or ():
            mkey = identity_key(
                language, validate_identity(language, mrow["identity"]))
            k4_cells = (mrow.get("arms") or {}).get("k4") or {}
            maximal = None
            for cell in k4_cells.values():
                if isinstance(cell, dict) \
                        and cell.get("rendering_bytes") is not None:
                    maximal = cell["rendering_bytes"]
                    break
            if maximal is None or mkey not in by_key:
                continue
            _require(by_key[mkey]["k4_mass"] == maximal,
                     f"scan mass diverges from the committed pilot "
                     f"assembly: {mkey} scan={by_key[mkey]['k4_mass']} "
                     f"manifest={maximal}")
            checked_n += 1
        _require(checked_n > 0,
                 "pilot cross-check matched no targets; refusing an "
                 "unvalidated scan")
        crosscheck = dict(
            n_checked=checked_n, status="exact",
            note=("exactness is certified on the pilot targets only; "
                  "all other candidates rest on the shared-"
                  "implementation argument (the scan imports "
                  "_components/make_chunk from the assembler itself)"))

    return dict(
        schema=SCAN_SCHEMA, repo=repo, language=language,
        budgets=list(budgets), n_candidates=n_rows,
        n_missing_unit=n_missing_unit,
        fill_fractions=fractions, rows=rows,
        pilot_crosscheck=crosscheck,
        bindings=dict(
            candidates=dict(sha256=cand_binding["sha256"]),
            extraction=dict(sha256=ext_binding["sha256"]),
            neardup=dict(sha256=nd_binding["sha256"]),
            a6_outcome=dict(sha256=out_binding["sha256"]),
            lean_boundaries=(dict(sha256=bnd_binding["sha256"])
                             if bnd_binding else None),
            amendment=dict(path=AMENDMENT_PATH, sha256=AMENDMENT_SHA256)),
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="scan_v2c_k4_mass.py"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--neardup", required=True)
    ap.add_argument("--a6-outcome", required=True)
    ap.add_argument("--lean-boundaries")
    ap.add_argument("--corpus-root")
    ap.add_argument("--pilot-manifest",
                    help="committed pilot assembly manifest for the "
                         "exact-mass cross-check (required when one "
                         "exists for the repo)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(args.a6_outcome)
    require_committed(AMENDMENT_PATH)
    artifact = scan_repo(args.repo, args.candidates, args.extraction,
                         args.neardup, args.a6_outcome,
                         boundaries_path=args.lean_boundaries,
                         corpus_root=args.corpus_root,
                         pilot_manifest_path=args.pilot_manifest)
    digest = write_new_json(args.out, artifact)
    print(f"V2C-K4-SCANNED {args.repo} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
