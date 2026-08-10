#!/usr/bin/env python3
"""Frozen exploratory NLL ladder analyzer (NLL_LADDER_EXPLORATORY_AMENDMENT).

One repository per invocation. For every tier in the FROZEN FULL tier set,
raw E1a/E1b/E2 deltas are produced by the UNCHANGED B3 producer
(`prepare_v2b_masked_deltas.build_masked_deltas`) under the fixed PUBLIC
32-zero-byte salt — the B3 blind is destroyed since the exploratory reveal
and none is claimed — and target rows reconstruct through the reveal-frozen
`_reconstruct_family` identity (sign * published + total_centering). Per-repo
inference is the UNCHANGED `_analyze_repo_rows` (unequal-cluster MoM, frozen
t-tables, Holm over E1a/E1b-IUT/E2, the E1b active-assay rule, and the
PhysLib k4x forcing).

Anti-shopping hardening (adversarial review, 2026-08-09): the analyzer
refuses tier subsets (the full frozen set or nothing); every completion must
equal the row of ONE committed completion ledger written before any
analysis; the committed exploratory reveal and the five per-repo assembly
manifests are pinned by sha256 constants; the sealed q25c-1.5b completion
must be the exact reveal-bound completion and must reproduce the reveal's
salt-independent centering; non-sealed completions must have been scored at
THIS source tree; tier batteries must carry their registry filenames.
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

# Frozen upstream constant repeated (eval_paired.COMPLETE_SCHEMA) so this
# CPU analyzer never imports the tokenizer/model stack at module import —
# same pattern as analyze_v2b_nll_exploratory's repeated constants.
COMPLETE_SCHEMA = "v2b_paired_nll_complete_v2"
LADDER_ANALYSIS_SCHEMA = "v2b_nll_ladder_analysis_v1"
LADDER_LEDGER_SCHEMA = "v2b_ladder_completion_ledger_v1"
LADDER_CLAIM_STATUS = "exploratory-nll-only-multi-checkpoint-pilot"
LADDER_GOVERNANCE_VERDICT = "not-run-ladder-exploratory"
DELTA_METRIC = "bpb"
DELTA_BUDGET_BYTES = 16384
SEALED_TIER = "q25c-1.5b"
# The tier set is FROZEN AT ADOPTION: exactly the PILOT_TIERS registry of
# the adopted amendment. Later rungs require a NEW amendment adopted before
# that tier is scored; this analyzer refuses subsets and supersets alike.
# q25c-32b was added by the DOSE_CURVE_EXPANSION amendment and then
# DROPPED by its own predeclared rule: the 32b battery fp32 semantic leg
# OOMed on a 141GB H200 (job 20035959, failed artifact retained), which
# the amendment defines as recorded tier infeasibility. The registry
# entry remains as record; the frozen analyzable set stays FIVE tiers.
FULL_TIER_SET = frozenset(
    ("q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b", "q25c-14b"))
# Anchors pinned at adoption (adversarial-review finding 2): the committed
# exploratory reveal and the five job19991210 assembly manifests.
PINNED_REVEAL_SHA256 = \
    "a2f88275381adbed8b52e17f9960e8fb6359055a867300179afd46837a4e2509"
PINNED_MANIFEST_SHA256 = {
    "mathlib4":
        "e82c54b979ea31353defbda17eb8ed1b1d04d1b8f68056bc5e18f7dae517c7e1",
    "batteries":
        "56daa6151b0444888bfa39aaf7700ab135cf3d036a2290e236fd093fdadc4985",
    "physlib":
        "996febf3f7d9967cb0438a69c37757e4a6056d948df6ef254566cdc96daf7b7b",
    "sympy":
        "1f43e3263a11993a7bd55240aff392f8aaf9dae27ce1477295fafe0b45eed485",
    "astropy":
        "dab767e9947e069be7ff411a911cbbca3b5a395396dcd4c9e9020a3fbbc28f78",
}
# The adopted amendment file is bound into every artifact (finding 4).
AMENDMENT_PATH = "results_v2/v2b/NLL_LADDER_EXPLORATORY_AMENDMENT.md"
# PER-TIER scored-tree pins (expansion review BLOCKER 1: a single global
# pin refuses every completion scored after any code commit, e.g. the 32b
# rung scored at the expansion-adoption tree). Each non-sealed tier maps
# to the ONE source tree its scoring launch ran at; a tier whose pin is
# None is REFUSED by the analyzers until a post-scoring, pre-analysis
# evidence commit fills it in (the value is determined by the
# ledger-bound completion itself, so pinning selects nothing; the ledger
# sha pinning remains the primary anti-shopping gate). The five original
# tiers share the af0655f-lineage tree.
_ORIGINAL_SCORING_TREE = \
    "87d54d84a801dfd148b1495c8885ed31b766355f81ba59b94fa71fcbe8e41958"
PINNED_SCORING_TREE_BY_TIER = {
    "q25c-0.5b": _ORIGINAL_SCORING_TREE,
    "q25c-3b": _ORIGINAL_SCORING_TREE,
    "q25c-7b": _ORIGINAL_SCORING_TREE,
    "q25c-14b": _ORIGINAL_SCORING_TREE,
    "q25c-32b": None,  # filled by the post-scoring pin commit
}
# Backward-compatible alias for consumers that imported the old name;
# semantically it is now "the original five-tier scoring tree".
PINNED_SCORING_TREE_SHA256 = _ORIGINAL_SCORING_TREE
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
    """The recomputed sealed tier must be the reveal-bound completion and
    must reproduce the reveal's salt-independent centering exactly."""
    repos = reveal.get("repos")
    _require(isinstance(repos, dict) and isinstance(repos.get(repo), dict),
             f"reveal lacks repository {repo}")
    reveal_row = repos[repo]
    reveal_completion = (reveal_row.get("bindings") or {}).get("completion")
    _require(isinstance(reveal_completion, dict)
             and isinstance(reveal_completion.get("sha256"), str),
             f"reveal completion binding malformed: {repo}")
    block_completion = (block.get("bindings") or {}).get("completion") or {}
    _require(block_completion.get("sha256")
             == reveal_completion.get("sha256"),
             f"sealed tier is not the reveal-bound completion: {repo}")
    reveal_mapping = reveal_row.get("mapping")
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


def _check_ledger(repo, ledger, tier_completions):
    """Every supplied completion must equal the committed ledger row; the
    ledger row set must be exactly the frozen full tier set (finding 1)."""
    repos = ledger.get("repos")
    _require(isinstance(repos, dict) and isinstance(repos.get(repo), dict),
             f"completion ledger lacks repository {repo}")
    rows = repos[repo]
    _require(set(rows) == FULL_TIER_SET,
             f"completion ledger tier set is not the frozen full set: "
             f"{repo} {sorted(rows)}")
    for tag, path in tier_completions.items():
        row = rows.get(tag)
        _require(isinstance(row, dict)
                 and isinstance(row.get("path"), str)
                 and isinstance(row.get("sha256"), str),
                 f"completion ledger row malformed: {repo} {tag}")
        _require(os.path.abspath(path) == row["path"],
                 f"completion path differs from the committed ledger: "
                 f"{repo} {tag}")
        _require(sha256_file(path) == row["sha256"],
                 f"completion hash differs from the committed ledger: "
                 f"{repo} {tag}")


def analyze_repo(repo, manifest_path, sample_path, candidates_path,
                 tier_completions, tier_batteries, ledger, reveal,
                 build_fn=None, expected_scoring_trees=None):
    """Analyze one repository across the frozen full ladder tier set."""
    if build_fn is None:
        from prepare_v2b_masked_deltas import build_masked_deltas
        build_fn = build_masked_deltas
    if expected_scoring_trees is None:
        expected_scoring_trees = PINNED_SCORING_TREE_BY_TIER
    _require(isinstance(tier_completions, dict)
             and set(tier_completions) == FULL_TIER_SET,
             f"ladder requires exactly the frozen full tier set "
             f"{sorted(FULL_TIER_SET)}; got "
             f"{sorted(tier_completions or ())}")
    _require(set(tier_batteries) == FULL_TIER_SET,
             "tier batteries must cover exactly the frozen full tier set")
    _require(repo in PINNED_MANIFEST_SHA256,
             f"unknown repository {repo!r}")
    _require(sha256_file(manifest_path) == PINNED_MANIFEST_SHA256[repo],
             f"assembly manifest does not match the pinned pilot manifest: "
             f"{repo}")
    _require(isinstance(reveal, dict),
             "the committed exploratory reveal is required")
    _check_ledger(repo, ledger, tier_completions)

    battery_shas = {}
    for tag in sorted(tier_batteries):
        path = tier_batteries[tag]
        _require(os.path.basename(path)
                 == PILOT_TIERS[tag]["battery_file"],
                 f"battery filename does not match the {tag} registry "
                 f"entry: {os.path.basename(path)!r}")
        battery_shas[tag] = sha256_file(path)

    tiers = {}
    assembly_sha = None
    completion_bindings = {}
    for tag in sorted(tier_completions):
        complete_path = tier_completions[tag]
        complete_binding, complete = artifact_binding(
            complete_path, COMPLETE_SCHEMA)
        generator = complete.get("generator") or {}
        if tag != SEALED_TIER:
            pinned_tree = expected_scoring_trees.get(tag)
            _require(isinstance(pinned_tree, str) and pinned_tree,
                     f"{tag} has no pinned scoring tree yet; the "
                     f"post-scoring pin commit must land before analysis")
            _require(generator.get("source_tree_hash") == pinned_tree,
                     f"{tag} completion was not scored at its pinned "
                     f"scoring tree: {repo}")
        masked, private = build_fn(
            complete_path, manifest_path, sample_path,
            candidates_path, LADDER_PUBLIC_SALT, LADDER_PUBLIC_SALT_NOTE)
        block = _tier_block(repo, tag, masked, private, battery_shas[tag])
        _require(masked["bindings"]["completion"].get("sha256")
                 == complete_binding["sha256"],
                 f"producer completion binding drift: {repo} {tag}")
        this_assembly = masked["bindings"]["assembly"].get("sha256")
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
            manifest_sha256=PINNED_MANIFEST_SHA256[repo],
            sample_path=os.path.abspath(sample_path),
            sample_sha256=sha256_file(sample_path),
            candidates_path=os.path.abspath(candidates_path),
            candidates_sha256=sha256_file(candidates_path),
            batteries={tag: dict(
                path=os.path.abspath(tier_batteries[tag]),
                sha256=battery_shas[tag]) for tag in sorted(tier_batteries)},
            completions=completion_bindings,
            reveal_sha256=PINNED_REVEAL_SHA256,
            amendment=dict(path=AMENDMENT_PATH,
                           sha256=sha256_file(AMENDMENT_PATH)),
            ledger_sha256=ledger.get("_binding_sha256")),
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
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--reveal", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    completions = _parse_tier_args(args.completion, "completion")
    batteries = _parse_tier_args(args.battery, "battery")
    require_committed(args.manifest)
    require_committed(args.sample)
    require_committed(args.ledger)
    require_committed(args.reveal)
    require_committed(AMENDMENT_PATH)
    for path in batteries.values():
        require_committed(path)
    reveal_binding, reveal = artifact_binding(
        args.reveal, NLL_EXPLORATORY_REVEAL_SCHEMA)
    if reveal_binding["sha256"] != PINNED_REVEAL_SHA256:
        raise V2BError("reveal file does not match the pinned committed "
                       "exploratory reveal")
    ledger_binding, ledger = artifact_binding(
        args.ledger, LADDER_LEDGER_SCHEMA)
    ledger = dict(ledger, _binding_sha256=ledger_binding["sha256"])
    artifact = analyze_repo(
        args.repo, args.manifest, args.sample, args.candidates,
        completions, batteries, ledger, reveal)
    digest = write_new_json(args.out, artifact)
    print(f"V2B-NLL-LADDER-ANALYZED {args.repo} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
