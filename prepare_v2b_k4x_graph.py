#!/usr/bin/env python3
"""Seal the §15.A13 k4x external-graph artifact (physlib -> pinned mathlib).

physlib's mathematical spine is external, so §14.20 makes physlib closure
results uninterpretable until this arm exists. The lake-manifest of the
locked physlib revision pins mathlib at a revision that DIFFERS from the
corpus-lock mathlib HEAD, so this generator binds a DEDICATED v3
extraction of `.lake/packages/mathlib` at exactly that pin (produced by
the same pair_ilean -> extract_lean pipeline) and resolves the physlib
extraction's preserved external reference quadruples against the
snapshot's decl tables under the IDENTICAL definition-parents fold rule
(extract_lean.build_corpus_graph): a const with a span resolves
directly; otherwise the definition_parents chain within the defining
module (bounded 8, cycle-guarded); None is recorded-unresolved, never
positionally guessed. References into any other external root (Lean
core, Std, batteries, ...) stay §14.3 counts-only.

The CLI verifies, fail-closed: physlib checkout at the frozen corpus
revision and clean; lake-manifest mathlib pin equal to the frozen
constant; snapshot checkout at exactly that pin and clean; extraction
schema/repo bindings. The sealed artifact hash-binds both extractions,
the exact lake-manifest bytes, and the frozen revision so assembly can
refuse any drifted input.
"""
import argparse
import json
import os
import subprocess
import sys

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (K4X_GRAPH_SCHEMA, V2BError, artifact_binding,
                        sha256_bytes, sha256_file, write_new_json)

PHYSLIB_REPO = "physlib"
PHYSLIB_REVISION = "e882411d1b6bcbdfdd336d4c509c6cc72e96842d"
K4X_EXTERNAL_REPO = "mathlib4"            # banner prefix + manifest label
# §15.A13: the mathlib revision pinned by physlib's lake-manifest at the
# locked physlib revision — read on-cluster 2026-08-08; NOT the corpus-lock
# mathlib HEAD 87adeaeb (version skew is exactly what this arm removes).
K4X_EXTERNAL_REVISION = "81a5d257c8e410db227a6665ed08f64fea08e997"
# The exact pinned extraction ALREADY exists: V2-a job 19916781_2 emitted
# it from `.lake/packages/mathlib` at exactly the pin (8,275 files). Reuse
# is frozen by repo tag AND whole-file hash; the tag never enters k4x
# ordering keys (those carry repo="physlib").
K4X_EXTERNAL_EXTRACTION_REPO = "physlib_pinned_mathlib"
K4X_EXTERNAL_EXTRACTION_SHA256 = \
    "9f4a192059ede347093c4f424940198e45cc93b9140f0ef8e5b8a465e0b6f796"
LEAN_EXTRACT_SCHEMA = "v2a_lean_extract_v3"
FOLD_BOUND = 8


def lake_manifest_mathlib_rev(manifest_value):
    """The mathlib pin from parsed lake-manifest.json, fail-closed."""
    packages = manifest_value.get("packages") \
        if isinstance(manifest_value, dict) else None
    if not isinstance(packages, list):
        raise V2BError("lake-manifest has no packages list")
    revs = [p.get("rev") for p in packages
            if isinstance(p, dict) and p.get("name") == "mathlib"]
    if len(revs) != 1 or not isinstance(revs[0], str) or not revs[0]:
        raise V2BError(f"lake-manifest pins {len(revs)} mathlib packages; "
                       f"exactly one required")
    return revs[0]


def _extraction_tables(extraction, label):
    """(decls_by_module, parents_by_module) from one v3 extraction."""
    if extraction.get("schema") != LEAN_EXTRACT_SCHEMA:
        raise V2BError(f"{label} extraction is not {LEAN_EXTRACT_SCHEMA}")
    decls_by_module = {}
    parents_by_module = {}
    for row in extraction.get("files", []):
        module = row.get("module")
        if not isinstance(module, str) or not module \
                or module in decls_by_module:
            raise V2BError(f"{label} extraction has a missing/duplicate "
                           f"module record: {module!r}")
        decls = row.get("decls")
        if not isinstance(decls, dict):
            raise V2BError(f"{label} module {module} lacks decls")
        decls_by_module[module] = set(decls)
        parents = row.get("definition_parents")
        parents_by_module[module] = dict(parents) \
            if isinstance(parents, dict) else {}
    if not decls_by_module:
        raise V2BError(f"{label} extraction has no modules")
    return decls_by_module, parents_by_module


def _fold(module, name, decls_by_module, parents_by_module):
    """EXACT extract_lean.build_corpus_graph fold: chase generating
    parents within the defining module to a decl WITH a span."""
    seen = set()
    cur = name
    parents = parents_by_module.get(module, {})
    spans = decls_by_module.get(module, set())
    for _ in range(FOLD_BOUND):
        if cur in spans:
            return cur
        if cur in seen or cur not in parents:
            return None
        seen.add(cur)
        cur = parents[cur]
    return None


def resolve_external_references(physlib_extraction, external_extraction):
    """§15.A13 resolution of the preserved physlib external quadruples."""
    physlib_decls, _ = _extraction_tables(physlib_extraction, "physlib")
    ext_decls, ext_parents = _extraction_tables(external_extraction,
                                                "snapshot")
    overlap = set(physlib_decls) & set(ext_decls)
    if overlap:
        raise V2BError(f"physlib/snapshot module namespaces overlap: "
                       f"{sorted(overlap)[:3]}")
    raw = physlib_extraction.get("graph", {}).get(
        "external_reference_edges")
    if not isinstance(raw, list) or not raw:
        raise V2BError("physlib extraction preserves no external "
                       "reference quadruples")
    resolved = set()
    n_direct = n_folded = 0
    unresolved_by_target = {}
    out_of_snapshot_by_root = {}
    for index, edge in enumerate(raw):
        if not isinstance(edge, list) or len(edge) != 4 \
                or not all(isinstance(x, str) and x for x in edge):
            raise V2BError(f"malformed external quadruple[{index}]: "
                           f"{edge!r}")
        mod, pd, tmod, tgt = edge
        if mod not in physlib_decls or pd not in physlib_decls[mod]:
            raise V2BError(f"external quadruple source is not a physlib "
                           f"unit: {mod}.{pd}")
        if tmod not in ext_decls:
            root = tmod.split(".", 1)[0]
            out_of_snapshot_by_root[root] = \
                out_of_snapshot_by_root.get(root, 0) + 1
            continue
        if tgt in ext_decls[tmod]:
            node, provenance = tgt, "direct"
            n_direct += 1
        else:
            node = _fold(tmod, tgt, ext_decls, ext_parents)
            if node is None:
                unresolved_by_target.setdefault(mod, {})[pd] = \
                    unresolved_by_target.get(mod, {}).get(pd, 0) + 1
                continue
            provenance = "folded"
            n_folded += 1
        resolved.add((mod, pd, tmod, node, provenance))
    edges = sorted(resolved)
    return dict(
        n_raw_external_reference_edges=len(raw),
        n_resolved_edges=len(edges),
        n_resolved_direct=n_direct,
        n_resolved_folded=n_folded,
        n_unresolved=sum(count for decls in unresolved_by_target.values()
                         for count in decls.values()),
        n_out_of_snapshot=sum(out_of_snapshot_by_root.values()),
        resolved_edges=[list(edge) for edge in edges],
        unresolved_by_target=unresolved_by_target,
        out_of_snapshot_by_root=out_of_snapshot_by_root)


def build_k4x_graph(physlib_extraction_path, external_extraction_path,
                    lake_manifest_bytes):
    """Pure §15.A13 artifact construction from validated inputs."""
    physlib_binding, physlib_extraction = artifact_binding(
        physlib_extraction_path)
    external_binding, external_extraction = artifact_binding(
        external_extraction_path)
    if physlib_extraction.get("repo") != PHYSLIB_REPO:
        raise V2BError("physlib extraction repo drift")
    if external_extraction.get("repo") != K4X_EXTERNAL_EXTRACTION_REPO:
        raise V2BError("snapshot extraction repo tag drift")
    manifest_rev = lake_manifest_mathlib_rev(
        json.loads(lake_manifest_bytes.decode("utf-8")))
    if manifest_rev != K4X_EXTERNAL_REVISION:
        raise V2BError(
            f"lake-manifest mathlib pin {manifest_rev} != frozen "
            f"{K4X_EXTERNAL_REVISION}")
    resolution = resolve_external_references(physlib_extraction,
                                             external_extraction)
    return dict(
        schema=K4X_GRAPH_SCHEMA,
        repo=PHYSLIB_REPO,
        external_repo=K4X_EXTERNAL_REPO,
        external_revision=K4X_EXTERNAL_REVISION,
        physlib_revision=PHYSLIB_REVISION,
        lake_manifest_sha256=sha256_bytes(lake_manifest_bytes),
        physlib_extraction=dict(physlib_binding,
                                schema=physlib_extraction.get("schema")),
        external_extraction=dict(external_binding,
                                 schema=external_extraction.get("schema")),
        resolution=resolution)


def _git_state(root):
    def run(*argv):
        proc = subprocess.run(["git", "-C", root, *argv],
                              capture_output=True)
        if proc.returncode != 0:
            raise V2BError(f"git {' '.join(argv)} failed in {root}: "
                           + proc.stderr.decode("utf-8", "replace")[:200])
        return proc.stdout.decode("utf-8").strip()

    return run("rev-parse", "HEAD"), run("status", "--porcelain") == ""


def prepare(physlib_root, physlib_extraction_path,
            external_extraction_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    head, clean = _git_state(physlib_root)
    if head != PHYSLIB_REVISION or not clean:
        raise V2BError(f"physlib checkout is {head} (clean={clean}); "
                       f"frozen revision is {PHYSLIB_REVISION}")
    snapshot_root = os.path.join(physlib_root, ".lake", "packages",
                                 "mathlib")
    snap_head, snap_clean = _git_state(snapshot_root)
    if snap_head != K4X_EXTERNAL_REVISION or not snap_clean:
        raise V2BError(
            f"snapshot checkout is {snap_head} (clean={snap_clean}); "
            f"frozen pin is {K4X_EXTERNAL_REVISION}")
    manifest_path = os.path.join(physlib_root, "lake-manifest.json")
    try:
        manifest_bytes = open(manifest_path, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read {manifest_path}: {err}") from err
    # Production reuse gate: only the exact job-19916781_2 pinned
    # extraction may seal a real k4x graph (the pure builder stays
    # hash-agnostic for synthetic tests).
    if sha256_file(external_extraction_path) != \
            K4X_EXTERNAL_EXTRACTION_SHA256:
        raise V2BError("snapshot extraction is not the frozen "
                       "job-19916781_2 artifact")
    artifact = build_k4x_graph(physlib_extraction_path,
                               external_extraction_path, manifest_bytes)
    artifact["snapshot_root"] = os.path.abspath(snapshot_root)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during k4x graph build")
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="prepare_v2b_k4x_graph.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--physlib-root", required=True)
    ap.add_argument("--physlib-extraction", required=True)
    ap.add_argument("--external-extraction", required=True,
                    help="v3 extraction of .lake/packages/mathlib at the "
                         "frozen pin (pair_ilean -> extract_lean)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact = prepare(args.physlib_root, args.physlib_extraction,
                       args.external_extraction)
    digest = write_new_json(args.out, artifact)
    resolution = artifact["resolution"]
    print(f"[v2b-k4x] {resolution['n_resolved_edges']} resolved "
          f"({resolution['n_resolved_folded']} folded), "
          f"{resolution['n_unresolved']} unresolved, "
          f"{resolution['n_out_of_snapshot']} out-of-snapshot -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
