#!/usr/bin/env python3
"""Frozen exploratory NLL ladder analyzer (NLL_LADDER_EXPLORATORY_AMENDMENT).

One repository per invocation. For every supplied tier completion, raw
E1a/E1b/E2 deltas are produced by the UNCHANGED B3 producer
(`prepare_v2b_masked_deltas.build_masked_deltas`) under the fixed PUBLIC
32-zero-byte salt — the B3 blind is destroyed since the exploratory reveal
and none is claimed — and target rows reconstruct through the reveal-frozen
`_reconstruct_family` identity (sign * published + total_centering). Per-repo
inference is the UNCHANGED `_analyze_repo_rows` (unequal-cluster MoM, frozen
t-tables, Holm over E1a/E1b-IUT/E2, the E1b active-assay rule, and the
PhysLib k4x forcing). Every tier's completion run identity must match the
frozen PILOT_TIERS registry entry exactly and hash-bind that tier's
committed instrument battery; every tier of one repo must bind the same
assembly manifest. The q25c-1.5b tier is recomputed from its sealed
completion and must reproduce the committed exploratory reveal's
salt-independent centering values exactly.

Ladder results may be read only through this analyzer's committed per-repo
artifact. No pooled cross-tier trend statistic exists here by design.
"""
import argparse
import hashlib
import json
import math
import os
import sys

from analyze_v2b_nll_exploratory import (
    CONTRAST_NAMES, NLL_EXPLORATORY_REVEAL_SCHEMA, _analyze_repo_rows,
    _reconstruct_family)
from layout import PRODUCTION_CHUNK_TOKENS
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, sha256_file, \
    write_new_json
from validity_battery import PILOT_TIERS

LADDER_ANALYSIS_SCHEMA = "v2b_nll_ladder_analysis_v1"
LADDER_CLAIM_STATUS = "exploratory-nll-only-multi-checkpoint-pilot"
LADDER_GOVERNANCE_VERDICT = "not-run-ladder-exploratory"
DELTA_METRIC = "bpb"
DELTA_BUDGET_BYTES = 16384
SEALED_TIER = "q25c-1.5b"
# Fixed PUBLIC salt: 32 zero bytes. Deliberately non-secret — the family
# masking machinery is reused solely so the frozen B3 validation and delta
# construction apply byte-identically to every tier; the reconstruction
# below immediately un-masks with the same constant.
LADDER_PUBLIC_SALT = bytes(32)
LADDER_PUBLIC_SALT_NOTE = dict(
    schema="v2b_ladder_public_salt_v1",
    state="public-no-blind",
    salt_sha256=hashlib.sha256(LADDER_PUBLIC_SALT).hexdigest(),
    note=("fixed 32-zero-byte public salt; the B3 blind is destroyed since "
          "the exploratory reveal and none is claimed for ladder tiers"))
# Salt-independent per-contrast centering fields that must reproduce the
# committed exploratory reveal exactly for the sealed tier (fid/sign are
# real-salt-derived and are NOT comparable under the public salt).
SEALED_CONSISTENCY_FIELDS = ("n_rows", "removed_mean_bpb",
                             "fsum_correction", "total_centering_bpb")


def _require(condition, message):
    if not condition:
        raise V2BError(message)


def _tier_entry(tag):
    _require(tag in PILOT_TIERS, f"unknown ladder tier {tag!r}")
    return PILOT_TIERS[tag]


def _tier_block(repo, tag, masked, private, battery_sha256):
    """Pure per-tier analysis from the B3 producer's (masked, private)."""
    tier = _tier_entry(tag)
    _require(isinstance(masked, dict) and isinstance(private, dict),
             f"malformed producer output: {repo} {tag}")
    _require(masked.get("repo") == repo,
             f"producer repo drift: {repo} {tag}")
    _require(masked.get("metric") == DELTA_METRIC
             and masked.get("budget_bytes") == DELTA_BUDGET_BYTES,
             f"producer metric/budget drift: {repo} {tag}")
    run_identity = masked.get("run_identity")
    _require(isinstance(run_identity, dict),
             f"missing run identity: {repo} {tag}")
    _require(run_identity.get("model") == tier["model"]
             and run_identity.get("revision") == tier["revision"],
             f"completion model/revision does not match tier {tag}: "
             f"{run_identity.get('model')!r} @ "
             f"{run_identity.get('revision')!r}")
    _require(run_identity.get("dtype") == "bfloat16"
             and run_identity.get("chunk_tokens") == PRODUCTION_CHUNK_TOKENS,
             f"completion dtype/chunk drift: {repo} {tag}")
    _require(isinstance(battery_sha256, str)
             and run_identity.get("pilot_battery_sha256") == battery_sha256,
             f"completion does not bind the committed {tag} battery: "
             f"{repo}")
    bindings = masked.get("bindings")
    _require(isinstance(bindings, dict)
             and isinstance(bindings.get("assembly"), dict)
             and isinstance(bindings.get("completion"), dict),
             f"producer bindings malformed: {repo} {tag}")

    families = {}
    mappings = {}
    for name in CONTRAST_NAMES:
        row = private.get(name)
        _require(isinstance(row, dict),
                 f"producer private mapping missing: {repo} {tag} {name}")
        mapping = dict(
            fid=row["fid"], sign=row["sign"], n_rows=row["n_rows"],
            removed_mean_bpb=row["removed_mean"],
            fsum_correction=row["fsum_correction"],
            total_centering_bpb=row["total_centering"])
        _require(mapping["fid"] in masked.get("families", {}),
                 f"mapped family absent from producer rows: "
                 f"{repo} {tag} {name}")
        families[name] = _reconstruct_family(
            masked.get("language"), masked["families"][mapping["fid"]],
            mapping, f"{repo} {tag} {name}")
        mappings[name] = mapping
    replayed = dict(
        language=masked.get("language"),
        model=run_identity["model"], revision=run_identity["revision"],
        run_identity_sha256=bindings.get("run_identity_sha256"),
        governance_verdict=LADDER_GOVERNANCE_VERDICT,
        governance_repo_n=None,
        bindings=dict(assembly=bindings["assembly"],
                      completion=bindings["completion"],
                      battery=dict(sha256=battery_sha256)),
        families=families)
    block = _analyze_repo_rows(repo, replayed)
    return dict(block, tier=tag, centering_by_contrast=mappings)


def _check_sealed_consistency(repo, block, reveal):
    """The recomputed sealed tier must reproduce the committed reveal's
    salt-independent centering values exactly."""
    repos = reveal.get("repos")
    _require(isinstance(repos, dict) and isinstance(repos.get(repo), dict),
             f"reveal lacks repository {repo}")
    reveal_mapping = repos[repo].get("mapping")
    _require(isinstance(reveal_mapping, dict),
             f"reveal mapping malformed: {repo}")
    for name in CONTRAST_NAMES:
        ours = block["centering_by_contrast"].get(name) or {}
        theirs = reveal_mapping.get(name) or {}
        for field in SEALED_CONSISTENCY_FIELDS:
            _require(field in theirs,
                     f"reveal mapping field missing: {repo} {name} {field}")
            _require(ours.get(field) == theirs.get(field),
                     f"sealed-tier centering drift vs committed reveal: "
                     f"{repo} {name} {field} "
                     f"{ours.get(field)!r} != {theirs.get(field)!r}")


def analyze_repo(repo, manifest_path, sample_path, candidates_path,
                 tier_completions, tier_batteries, reveal_path=None,
                 build_fn=None):
    """Analyze one repository across ladder tiers.

    tier_completions/tier_batteries: {tier tag: path}. The sealed tier
    requires reveal_path (and reveal_path requires the sealed tier)."""
    if build_fn is None:
        from prepare_v2b_masked_deltas import build_masked_deltas
        build_fn = build_masked_deltas
    _require(isinstance(tier_completions, dict) and tier_completions,
             "no tier completions supplied")
    _require(set(tier_completions) <= set(PILOT_TIERS),
             f"unknown tier tags: "
             f"{sorted(set(tier_completions) - set(PILOT_TIERS))}")
    _require(set(tier_batteries) == set(tier_completions),
             "tier batteries must cover exactly the supplied completions")
    _require((SEALED_TIER in tier_completions) == (reveal_path is not None),
             "the sealed q25c-1.5b tier and --reveal are supplied together")

    reveal = None
    reveal_binding = None
    if reveal_path is not None:
        reveal_binding, reveal = artifact_binding(
            reveal_path, NLL_EXPLORATORY_REVEAL_SCHEMA)

    battery_shas = {}
    for tag in sorted(tier_batteries):
        battery_shas[tag] = sha256_file(tier_batteries[tag])

    tiers = {}
    assembly_sha = None
    completion_bindings = {}
    for tag in sorted(tier_completions):
        masked, private = build_fn(
            tier_completions[tag], manifest_path, sample_path,
            candidates_path, LADDER_PUBLIC_SALT, LADDER_PUBLIC_SALT_NOTE)
        block = _tier_block(repo, tag, masked, private, battery_shas[tag])
        this_assembly = block["bindings"]["assembly"].get("sha256") \
            if isinstance(block.get("bindings", {}).get("assembly"), dict) \
            else masked["bindings"]["assembly"].get("sha256")
        _require(isinstance(this_assembly, str) and this_assembly,
                 f"missing assembly binding: {repo} {tag}")
        if assembly_sha is None:
            assembly_sha = this_assembly
        _require(this_assembly == assembly_sha,
                 f"tiers bind different assembly manifests: {repo} {tag}")
        if tag == SEALED_TIER:
            _check_sealed_consistency(repo, block, reveal)
        completion_bindings[tag] = masked["bindings"]["completion"]
        tiers[tag] = block

    return dict(
        schema=LADDER_ANALYSIS_SCHEMA,
        claim_status=LADDER_CLAIM_STATUS,
        repo=repo,
        metric=DELTA_METRIC,
        budget_bytes=DELTA_BUDGET_BYTES,
        model_pooling=False,
        language_pooling="prohibited",
        trend_inference="none-descriptive-forest-only",
        public_salt=LADDER_PUBLIC_SALT_NOTE,
        governance_note=(
            "blind N governance was a sealed-pilot artifact; ladder tiers "
            "carry verdict " + LADDER_GOVERNANCE_VERDICT),
        tier_order=sorted(tiers),
        tiers=tiers,
        bindings=dict(
            assembly_sha256=assembly_sha,
            manifest_path=os.path.abspath(manifest_path),
            manifest_sha256=sha256_file(manifest_path),
            sample_path=os.path.abspath(sample_path),
            sample_sha256=sha256_file(sample_path),
            candidates_path=os.path.abspath(candidates_path),
            candidates_sha256=sha256_file(candidates_path),
            batteries={tag: dict(
                path=os.path.abspath(tier_batteries[tag]),
                sha256=battery_shas[tag]) for tag in sorted(tier_batteries)},
            completions=completion_bindings,
            reveal=reveal_binding),
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="analyze_v2b_nll_ladder.py"))


def _parse_tier_args(pairs, label):
    out = {}
    for pair in pairs or ():
        tag, sep, path = pair.partition("=")
        if not sep or not tag or not path:
            raise V2BError(f"malformed --{label} (want tier=path): {pair!r}")
        if tag in out:
            raise V2BError(f"duplicate --{label} tier: {tag}")
        out[tag] = path
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--completion", action="append", metavar="TIER=PATH")
    ap.add_argument("--battery", action="append", metavar="TIER=PATH")
    ap.add_argument("--reveal", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    completions = _parse_tier_args(args.completion, "completion")
    batteries = _parse_tier_args(args.battery, "battery")
    require_committed(args.manifest)
    require_committed(args.sample)
    for path in batteries.values():
        require_committed(path)
    if args.reveal is not None:
        require_committed(args.reveal)
    artifact = analyze_repo(
        args.repo, args.manifest, args.sample, args.candidates,
        completions, batteries, reveal_path=args.reveal)
    digest = write_new_json(args.out, artifact)
    print(f"V2B-NLL-LADDER-ANALYZED {args.repo} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
