#!/usr/bin/env python3
"""Build the hash-bound full-file k7 order frozen in DESIGN_V2 §15.A8/A12."""
import argparse
import hashlib
import os
import subprocess

from prep_streams import collect_files, resolved_file_edges, topo_order
from provenance import head_commit, source_clean, source_tree_hash
from v2b_assemble import _components, normalize_payload
from v2b_common import K7_ORDER_SCHEMA, V2BError, write_new_json
from v2b_metadata import corpus_git_identity


K7_CONFIGS = {
    "mathlib4": dict(repo="mathlib4", dirs=["Mathlib"], exts=[".lean"],
                     lang="lean"),
    "batteries": dict(repo="batteries", dirs=["Batteries"],
                      exts=[".lean"], lang="lean"),
    "physlib": dict(repo="physlib", dirs=["Physlib", "QuantumInfo"],
                    exts=[".lean"], lang="lean",
                    exclude=["PhyslibAlpha"]),
    "sympy": dict(repo="sympy", dirs=["sympy"], exts=[".py"],
                  lang="python"),
    "astropy": dict(repo="astropy", dirs=["astropy"], exts=[".py"],
                    lang="python"),
}


def _tracked_paths(corpus_root):
    """Exact HEAD path set; k7 may not admit build debris or other untracked
    source-looking files merely because they happen to be in the worktree."""
    proc = subprocess.run(
        ["git", "-C", corpus_root, "ls-tree", "-r", "-z", "--name-only",
         "HEAD"], capture_output=True)
    if proc.returncode != 0:
        raise V2BError("cannot enumerate locked-revision paths for k7: "
                       + proc.stderr.decode("utf-8", "replace")[:300])
    return {part.decode("utf-8", "surrogateescape")
            for part in proc.stdout.split(b"\0") if part}


def _configured_tracked_candidates(tracked, cfg):
    roots = tuple(directory.rstrip("/") + "/" for directory in cfg["dirs"])
    excluded = set(cfg.get("exclude", ()))
    out = set()
    for rel in tracked:
        if not rel.startswith(roots):
            continue
        tail = next(rel[len(root):] for root in roots
                    if rel.startswith(root))
        if excluded.intersection(tail.split("/")[:-1]):
            continue
        if any(rel.endswith(ext) for ext in cfg["exts"]):
            out.add(rel)
    return out


def _collector_audit(corpus_root, cfg, admitted):
    admitted_by_rel = {row["rel"]: row for row in admitted}
    if len(admitted_by_rel) != len(admitted):
        raise V2BError("prep_streams collector emitted duplicate paths")
    candidate_paths = {}
    nonmatching = dict(count=0, stat_bytes=0, stat_errors=0)
    pruned_directories = 0
    for directory in cfg["dirs"]:
        top = os.path.join(corpus_root, directory)
        if not os.path.isdir(top):
            raise V2BError(f"k7 configured source directory missing: {top}")
        for dirpath, dirnames, names in os.walk(top):
            pruned = [name for name in dirnames
                      if name in cfg.get("exclude", ())]
            pruned_directories += len(pruned)
            dirnames[:] = sorted(name for name in dirnames
                                 if name not in cfg.get("exclude", ()))
            for name in sorted(names):
                path = os.path.join(dirpath, name)
                if not any(name.endswith(ext) for ext in cfg["exts"]):
                    nonmatching["count"] += 1
                    try:
                        nonmatching["stat_bytes"] += os.path.getsize(path)
                    except OSError:
                        nonmatching["stat_errors"] += 1
                    continue
                rel = os.path.relpath(path, corpus_root).replace(os.sep, "/")
                if rel in candidate_paths:
                    raise V2BError(f"k7 walk emitted duplicate {rel}")
                candidate_paths[rel] = path
    expected_candidates = _configured_tracked_candidates(
        _tracked_paths(corpus_root), cfg)
    if set(candidate_paths) != expected_candidates:
        extra = sorted(set(candidate_paths) - expected_candidates)[:5]
        missing = sorted(expected_candidates - set(candidate_paths))[:5]
        raise V2BError("k7 worktree candidate set differs from locked HEAD "
                       f"(untracked/extra={extra}, missing={missing})")
    skipped = {
        "read_error": dict(count=0, stat_bytes=0, stat_errors=0),
        "non_utf8": dict(count=0, raw_bytes=0),
        "under_64_bytes": dict(count=0, raw_bytes=0),
    }
    raw_by_rel = {}
    for rel, path in sorted(candidate_paths.items()):
        try:
            raw = open(path, "rb").read()
        except OSError:
            row = skipped["read_error"]
            row["count"] += 1
            try:
                row["stat_bytes"] += os.path.getsize(path)
            except OSError:
                row["stat_errors"] += 1
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped["non_utf8"]["count"] += 1
            skipped["non_utf8"]["raw_bytes"] += len(raw)
            continue
        if len(raw) < 64:
            skipped["under_64_bytes"]["count"] += 1
            skipped["under_64_bytes"]["raw_bytes"] += len(raw)
            continue
        raw_by_rel[rel] = raw
    if len(raw_by_rel) + sum(row["count"] for row in skipped.values()) \
            != len(candidate_paths):
        raise AssertionError("k7 collector audit does not partition candidates")
    if set(raw_by_rel) != set(admitted_by_rel):
        raise V2BError("audited k7 admission set diverges from collect_files")
    records = {}
    n_appended_lf = 0
    n_normalize_changed_files = 0
    n_normalize_removed_lf = 0
    n_normalize_appended_lf = 0
    for rel, row in admitted_by_rel.items():
        raw = raw_by_rel[rel]
        emitted = row["text"].encode("utf-8")
        expected = raw if raw.endswith(b"\n") else raw + b"\n"
        if emitted != expected or row["bytes"] != len(emitted):
            raise V2BError(f"prep_streams emitted-byte drift for {rel}")
        if not raw.endswith(b"\n"):
            n_appended_lf += 1
        normalized, audit = normalize_payload(emitted)
        if not normalized.endswith(b"\n") or normalized.endswith(b"\n\n") \
                or normalized != emitted.rstrip(b"\n") + b"\n":
            raise V2BError(f"k7 payload normalization drift for {rel}")
        n_normalize_changed_files += normalized != emitted
        n_normalize_removed_lf += audit["n_removed_terminal_lf"]
        n_normalize_appended_lf += audit["n_appended_terminal_lf"]
        records[rel] = dict(
            raw_bytes=len(raw), emitted_bytes=len(emitted),
            normalized_bytes=len(normalized),
            source_sha256=hashlib.sha256(raw).hexdigest(),
            emitted_sha256=hashlib.sha256(emitted).hexdigest(),
            normalized_sha256=hashlib.sha256(normalized).hexdigest(),
            collector_appended_terminal_lf=not raw.endswith(b"\n"),
            normalization=audit)
    return records, dict(
        n_extension_candidates=len(candidate_paths),
        n_admitted=len(admitted),
        n_admitted_raw_bytes=sum(row["raw_bytes"]
                                 for row in records.values()),
        n_admitted_emitted_bytes=sum(row["emitted_bytes"]
                                     for row in records.values()),
        n_admitted_normalized_bytes=sum(row["normalized_bytes"]
                                        for row in records.values()),
        n_pruned_directories=pruned_directories,
        n_terminal_lf_appended=n_appended_lf,
        n_normalize_changed_files=n_normalize_changed_files,
        n_normalize_removed_terminal_lf=n_normalize_removed_lf,
        n_normalize_appended_terminal_lf=n_normalize_appended_lf,
        nonmatching_extension=nonmatching,
        skipped=skipped)


def build_k7_order(corpus_root, repo, expected_corpus_sha, cfg=None):
    cfg = dict(cfg or K7_CONFIGS.get(repo) or {})
    if not cfg or cfg.get("repo") != os.path.basename(
            os.path.realpath(corpus_root)):
        raise V2BError(f"k7 config/root mismatch for {repo}")
    if cfg.get("lang") not in ("lean", "python"):
        raise V2BError(f"unsupported k7 language {cfg.get('lang')!r}")
    identity = corpus_git_identity(corpus_root, expected_corpus_sha)
    parent = os.path.dirname(os.path.realpath(corpus_root))
    files = collect_files(cfg, root=parent)
    if not files:
        raise V2BError(f"k7 collector admitted no files for {repo}")
    audit_records, collector = _collector_audit(corpus_root, cfg, files)
    order, n_cycle_nodes, n_edges = topo_order(files, cfg)
    edges = resolved_file_edges(files, cfg)
    if n_edges != len(edges) or sorted(order) != list(range(len(files))):
        raise AssertionError("prep_streams topo order/edge accounting diverged")
    components, component_of = _components(set(range(len(files))), edges)
    scc_name = {cid: min(files[index]["rel"] for index in component)
                for cid, component in enumerate(components)}
    n_cycle_sccs = sum(len(component) > 1 for component in components)
    ordered_rows, diagnostics = [], []
    for index in order:
        rel = files[index]["rel"]
        record = audit_records[rel]
        scc_id = scc_name[component_of[index]]
        ordered_rows.append([rel, record["normalized_bytes"],
                             record["source_sha256"], scc_id])
        diagnostics.append(dict(relpath=rel, file_scc_id=scc_id, **record))
    return dict(schema=K7_ORDER_SCHEMA, repo=repo, language=cfg["lang"],
                corpus_git_sha=identity["corpus_git_sha"],
                order_rule="g3_full_topo_kahn_minheap_v1",
                n_edges=n_edges, n_cycle_nodes=n_cycle_nodes,
                n_cycle_sccs=n_cycle_sccs, collector=collector,
                files=ordered_rows, file_diagnostics=diagnostics)


def prepare(corpus_root, repo, expected_corpus_sha):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    artifact = build_k7_order(corpus_root, repo, expected_corpus_sha)
    corpus_git_identity(corpus_root, expected_corpus_sha)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during k7 build")
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="v2b_k7.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--repo", required=True, choices=tuple(K7_CONFIGS))
    ap.add_argument("--expected-corpus-sha", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact = prepare(args.corpus_root, args.repo,
                       args.expected_corpus_sha)
    digest = write_new_json(args.out, artifact)
    print(f"[v2b-k7] {args.repo}: {len(artifact['files'])} files, "
          f"{artifact['n_edges']} edges -> {args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
