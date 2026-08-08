#!/usr/bin/env python3
"""Build the source-only V2-b token ledger before model scoring.

The exact assembly bodies are re-materialized from their hash-bound chain,
then completely partitioned by ``v2b_source_tokens``.  This artifact contains
no NLL and exposes no arm contrast.  Its only claim is lexical source-token
classification; AST-node attribution remains false.
"""
import argparse
import hashlib
import os
import sys

from prepare_v2b_assembly import materialize
from provenance import (head_commit, python_binary_hash, source_clean,
                        source_tree_hash)
from v2b_a6_blind import require_committed
from v2b_common import (ASSEMBLY_SCHEMA, V2BError, artifact_binding,
                        canonical_json_bytes, sha256_bytes, sha256_json,
                        write_new_json)
from v2b_source_tokens import (CLASSIFIER_CONTRACT,
                               CLASSIFIER_CONTRACT_SHA256,
                               SOURCE_TOKEN_LEDGER_SCHEMA, runtime_provenance,
                               source_spans)


STATE = "source-only-pre-score-safe"
HARNESS_FILES = ("v2b_source_tokens.py", "v2b_neardup.py")


def source_token_harness_hash(base_dir=None):
    base = os.path.abspath(base_dir or os.path.dirname(__file__))
    rows = []
    for name in HARNESS_FILES:
        path = os.path.join(base, name)
        try:
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        except OSError as err:
            raise V2BError(f"cannot hash source-token harness {name}: {err}") \
                from err
        rows.append([name, digest])
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _utf8(blob, label):
    if not isinstance(blob, bytes):
        raise V2BError(f"{label} is not bytes")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"{label} is not UTF-8: {err}") from err


def build_source_token_ledger(manifest_path, sample_path, repo,
                              candidates_path, extraction_path,
                              neardup_path, outcome_path,
                              keyword_freeze_path=None, k7_order_path=None,
                              k4x_graph_path=None,
                              external_extraction_path=None,
                              lean_boundaries_path=None):
    manifest_binding, manifest = artifact_binding(manifest_path,
                                                  ASSEMBLY_SCHEMA)
    if manifest.get("repo") != repo:
        raise V2BError("source-token ledger manifest repo mismatch")
    language = manifest.get("language")
    if language not in ("lean", "python"):
        raise V2BError(f"unsupported source-token language {language!r}")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets \
            or manifest.get("n_targets") != len(targets):
        raise V2BError("source-token ledger manifest targets malformed")
    concrete = materialize(
        manifest_path, sample_path, repo, candidates_path, extraction_path,
        neardup_path, outcome_path, keyword_freeze_path, k7_order_path,
        k4x_graph_path, external_extraction_path, lean_boundaries_path)
    expected_keys = [row.get("key") for row in targets]
    if expected_keys != sorted(expected_keys) \
            or set(concrete) != set(expected_keys):
        raise V2BError("source-token materialization target set drift")

    rows = []
    for index, target in enumerate(targets):
        key = target["key"]
        body = concrete[key].get("body")
        if not isinstance(body, bytes) \
                or len(body) != target.get("body_bytes") \
                or sha256_bytes(body) != target.get("body_sha256"):
            raise V2BError(f"source-token body binding drift: {key}")
        partition = source_spans(language, _utf8(body, f"body {key}"))
        if partition["body_bytes"] != len(body):
            raise AssertionError("source-token partition changed body bytes")
        rows.append(dict(
            target_index=index, target_identity=target.get("identity"),
            target_key=key, assembly_target_sha256=sha256_json(target),
            body_sha256=target["body_sha256"],
            **partition))

    runtime = runtime_provenance()
    runtime["python_executable_sha256"] = python_binary_hash()
    return dict(
        schema=SOURCE_TOKEN_LEDGER_SCHEMA, state=STATE,
        claim="source-token NLL attribution", ast_node_attribution=False,
        repo=repo, language=language,
        corpus_git_sha=manifest.get("corpus_git_sha"),
        bindings=dict(assembly_manifest=manifest_binding,
                      assembly_inputs=manifest.get("bindings")),
        classifier_contract=CLASSIFIER_CONTRACT,
        classifier_contract_sha256=CLASSIFIER_CONTRACT_SHA256,
        source_token_harness_sha256=source_token_harness_hash(),
        runtime=runtime,
        limitations=[
            "Lexical source-token classes are not AST nodes or semantics.",
            "Lean interpolated strings remain whole literal spans.",
            "No model score or arm contrast is present in this artifact."],
        n_targets=len(rows), targets_sha256=sha256_json(rows), targets=rows)


def prepare(*args, **kwargs):
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    manifest_path = args[0] if args else kwargs.get("manifest_path")
    require_committed(manifest_path)
    artifact = build_source_token_ledger(*args, **kwargs)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during source-token ledger")
    artifact["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="prepare_v2b_source_token_ledger.py")
    return artifact


def _manifest_paths(manifest_path, overrides):
    """Resolve evaluator inputs from the exact assembly bindings, with
    optional location-only overrides.  materialize rechecks every hash."""
    _, manifest = artifact_binding(manifest_path, ASSEMBLY_SCHEMA)
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise V2BError("assembly manifest lacks input bindings")
    binding_names = {
        "sample": "sample", "candidates": "candidates",
        "extraction": "extraction", "neardup": "neardup",
        "a6_outcome": "a6_outcome",
        "lean_boundaries": "lean_boundaries",
        "lean_keyword_freeze": "keyword_freeze",
        "k7_order": "k7_order", "k4x_graph": "k4x_graph",
        "k4x_external_extraction": "k4x_external_extraction"}
    paths = {}
    for name, binding_name in binding_names.items():
        binding = bindings.get(binding_name)
        override = overrides.get(name)
        path = override or (binding.get("path")
                            if isinstance(binding, dict) else None)
        required = (
            name in ("sample", "candidates", "extraction", "neardup",
                     "a6_outcome", "k7_order")
            or (name == "lean_keyword_freeze"
                and manifest.get("language") == "lean")
            or (name == "lean_boundaries"
                and manifest.get("language") == "lean")
            or (name in ("k4x_graph", "k4x_external_extraction")
                and isinstance(manifest.get("k4x"), dict)
                and manifest["k4x"].get("applicable") is True))
        if required and (not isinstance(path, str) or not path):
            raise V2BError(f"assembly manifest lacks required path {name}")
        paths[name] = path
    return manifest, paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sample")
    ap.add_argument("--repo")
    ap.add_argument("--candidates")
    ap.add_argument("--extraction")
    ap.add_argument("--lean-boundaries")
    ap.add_argument("--neardup")
    ap.add_argument("--a6-outcome")
    ap.add_argument("--lean-keyword-freeze")
    ap.add_argument("--k7-order")
    ap.add_argument("--k4x-graph")
    ap.add_argument("--k4x-external-extraction")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        manifest, paths = _manifest_paths(args.manifest, dict(
            sample=args.sample, candidates=args.candidates,
            extraction=args.extraction, neardup=args.neardup,
            lean_boundaries=args.lean_boundaries,
            a6_outcome=args.a6_outcome,
            lean_keyword_freeze=args.lean_keyword_freeze,
            k7_order=args.k7_order, k4x_graph=args.k4x_graph,
            k4x_external_extraction=args.k4x_external_extraction))
        artifact = prepare(
            args.manifest, paths["sample"], args.repo or manifest["repo"],
            paths["candidates"], paths["extraction"], paths["neardup"],
            paths["a6_outcome"], paths["lean_keyword_freeze"],
            paths["k7_order"], paths["k4x_graph"],
            paths["k4x_external_extraction"],
            paths["lean_boundaries"])
        digest = write_new_json(args.out, artifact)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[v2b-source-token-ledger] {artifact['repo']}: "
          f"{artifact['n_targets']} targets -> {args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
