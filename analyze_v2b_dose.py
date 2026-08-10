#!/usr/bin/env python3
"""Frozen post-ladder consumers (POST_LADDER_CONSUMERS_AMENDMENT).

Mode `budget`: dose-response of the standard contrasts over the committed
budget grid {4096, 16384, 65536}, all repos, all five tiers. Mode `k4x`:
the PhysLib sensitivity with the lake-manifest-pinned combined-graph arm in
the reference slot, which lifts the forced k4-based status. Both modes read
ONLY the twenty-five ledger-bound completions — no new scoring — inherit
the ladder analyzer's full anti-shopping surface, validate every chain by
running the UNCHANGED B3 producer first, extract deltas with the UNCHANGED
_load_target/_cell_bpb helpers, and infer with the UNCHANGED
_inference/Student-t/Holm machinery and the frozen +0.02 margin. In budget
mode every B* panel must reproduce the B3 producer's own centering exactly
(and, for the sealed tier, the committed reveal's), so this consumer cannot
drift from the committed ladder artifacts.
"""
import argparse
import math
import os
import sys

from analyze_v2b_nll_exploratory import (
    ALPHA, NLL_EXPLORATORY_REVEAL_SCHEMA, NONINFERIORITY_MARGIN_BPB,
    _canonical_target_row, _inference, _p_greater, _p_less, holm_adjust)
from analyze_v2b_nll_ladder import (
    AMENDMENT_PATH as LADDER_AMENDMENT_PATH, COMPLETE_SCHEMA, FULL_TIER_SET,
    LADDER_LEDGER_SCHEMA, LADDER_PUBLIC_SALT, LADDER_PUBLIC_SALT_NOTE,
    PINNED_MANIFEST_SHA256, PINNED_REVEAL_SHA256,
    PINNED_SCORING_TREE_SHA256, SEALED_TIER, _check_ledger,
    _check_sealed_consistency, _require, _tier_entry)
from layout import PRODUCTION_CHUNK_TOKENS
from prepare_v2b_masked_deltas import _cell_bpb, _load_target
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, sha256_file, \
    write_new_json

AMENDMENT_PATH = "results_v2/v2b/POST_LADDER_CONSUMERS_AMENDMENT.md"
BUDGETS = (4096, 16384, 65536)
B_STAR = 16384
MODES = ("budget", "k4x")
SCHEMAS = {"budget": "v2b_budget_response_v1",
           "k4x": "v2b_k4x_sensitivity_v1"}
CLAIMS = {"budget": "exploratory-nll-only-budget-response",
          "k4x": "exploratory-nll-only-physlib-k4x-sensitivity"}
K4X_EXTERNAL = dict(repo="mathlib4",
                    revision="81a5d257c8e410db227a6665ed08f64fea08e997")
PHYSLIB_FORCED = "uninterpretable-pending-k4x-sensitivity"
CONTRAST_NAMES = ("E1a", "E1b", "E2")
NON_BSTAR_READING_RULE = (
    "per-panel Holm is scoped within (repo, tier, budget); non-B* panels' "
    "positive diagnostic flags are descriptive context for the dose curve "
    "only and are never citable as standalone positive results; B* panels "
    "are the only headline-bearing cells")


def contrast_table(mode, budget):
    ref = "k4x" if mode == "k4x" else "k4"
    return (
        ("E1a", "k1", f"{ref}:{budget}", (f"{ref}:{budget}",)),
        ("E1b", f"k3:{budget}", f"{ref}:{budget}",
         (f"k3:{budget}", f"{ref}:{budget}")),
        ("E2", f"k5:0:{budget}", f"{ref}:{budget}",
         (f"k5:0:{budget}", f"{ref}:{budget}")),
    )


def extract_rows(cells_by_id, language, key, contrasts):
    """Complete-case oriented deltas for one target, as canonical rows."""
    out = {}
    for name, minuend, subtrahend, eligibility in contrasts:
        if not all(cells_by_id[cell].get("eligible") is True
                   for cell in eligibility):
            continue
        delta = _cell_bpb(cells_by_id, minuend, key) \
            - _cell_bpb(cells_by_id, subtrahend, key)
        out[name] = _canonical_target_row(language, key, delta, 1, 0.0)
    return out


def _contrast_record(name, orientation, rows, summary, raw_p, adjusted_p,
                     assay_label=None):
    positive = name in ("E1a", "E2")
    controlled = positive \
        and summary["inference_status"] == "available" \
        and summary["lower_one_sided_95_bpb"] > 0.0 \
        and adjusted_p <= ALPHA
    record = dict(
        orientation=orientation, metric="bpb",
        favorable_direction=("positive" if positive else
                             "smaller/noninferior"),
        target_rows=rows, inference=summary,
        raw_one_sided_pvalue=raw_p,
        holm_adjusted_pvalue=adjusted_p,
        exploratory_positive_model_based_diagnostic=controlled,
        interpretation_status=(
            "positive-model-based-diagnostic" if controlled
            else "positive-not-established") if positive else assay_label)
    if not positive:
        record["exploratory_positive_model_based_diagnostic"] = False
    return record


def build_panel(language, rows_by_name, contrasts):
    """The frozen three-contrast inference panel at one (tier, budget)."""
    orientations = {name: f"{minuend}-{subtrahend}"
                    for name, minuend, subtrahend, _ in contrasts}
    summaries = {}
    keys = {}
    for name in CONTRAST_NAMES:
        rows = sorted(rows_by_name.get(name, ()),
                      key=lambda row: row["target_key"])
        rows_by_name[name] = rows
        summaries[name] = _inference(rows)
        keys[name] = set(summaries[name]["target_keys"])
    _require(keys["E1b"] <= keys["E1a"] and keys["E2"] <= keys["E1a"],
             "contrast eligibility nesting violated")
    intersection_rows = [row for row in rows_by_name["E1a"]
                         if row["target_key"] in keys["E1b"]]
    assay_summary = _inference(intersection_rows)
    raw_p = {"E1a": _p_greater(summaries["E1a"], 0.0),
             "E2": _p_greater(summaries["E2"], 0.0)}
    p_ni = _p_less(summaries["E1b"], NONINFERIORITY_MARGIN_BPB)
    p_active = _p_greater(assay_summary, NONINFERIORITY_MARGIN_BPB)
    raw_p["E1b"] = max(p_ni, p_active)
    multiplicity = holm_adjust(raw_p)
    adjusted = multiplicity["adjusted_pvalues"]

    e1b_upper = summaries["E1b"]["upper_one_sided_95_bpb"]
    active_lower = assay_summary["lower_one_sided_95_bpb"]
    available = e1b_upper is not None and active_lower is not None \
        and summaries["E1b"]["inference_status"] == "available" \
        and assay_summary["inference_status"] == "available"
    ni_ok = available and e1b_upper <= NONINFERIORITY_MARGIN_BPB
    active_ok = available and active_lower >= NONINFERIORITY_MARGIN_BPB
    holm_ok = adjusted["E1b"] <= ALPHA
    if not available:
        assay_label = "inference-unavailable"
    elif not ni_ok:
        assay_label = "noninferiority-not-established"
    elif not active_ok:
        assay_label = "assay-insensitive-inconclusive"
    elif not holm_ok:
        assay_label = "multiplicity-not-established"
    else:
        assay_label = "interface-sufficiency-compatible-exploratory"

    contrasts_out = {
        name: _contrast_record(
            name, orientations[name], rows_by_name[name], summaries[name],
            raw_p[name], adjusted[name],
            assay_label=assay_label if name == "E1b" else None)
        for name in CONTRAST_NAMES}
    assay = dict(
        margin_bpb=NONINFERIORITY_MARGIN_BPB,
        e1a_on_intersection_inference=assay_summary,
        e1b_upper_one_sided_95_bpb=e1b_upper,
        e1a_intersection_lower_one_sided_95_bpb=active_lower,
        noninferiority_pvalue=p_ni, active_assay_pvalue=p_active,
        intersection_union_pvalue=raw_p["E1b"],
        holm_adjusted_pvalue=adjusted["E1b"], label=assay_label)
    return dict(contrasts=contrasts_out, e1b_assay=assay,
                multiplicity=multiplicity, language=language)


def _force_physlib(panel):
    for name in ("E1a", "E1b"):
        panel["contrasts"][name][
            "exploratory_positive_model_based_diagnostic"] = False
        panel["contrasts"][name]["interpretation_status"] = PHYSLIB_FORCED
    panel["e1b_assay"]["label"] = PHYSLIB_FORCED
    panel["forced_status"] = PHYSLIB_FORCED
    return panel


def analyze_repo(mode, repo, manifest_path, sample_path, candidates_path,
                 tier_completions, tier_batteries, ledger, reveal,
                 build_fn=None, load_target_fn=_load_target,
                 expected_scoring_tree=None):
    _require(mode in MODES, f"unknown mode {mode!r}")
    _require(mode != "k4x" or repo == "physlib",
             "k4x mode is PhysLib-only")
    if build_fn is None:
        from prepare_v2b_masked_deltas import build_masked_deltas
        build_fn = build_masked_deltas
    if expected_scoring_tree is None:
        expected_scoring_tree = PINNED_SCORING_TREE_SHA256
    _require(set(tier_completions) == FULL_TIER_SET,
             f"dose consumers require exactly the frozen full tier set; "
             f"got {sorted(tier_completions or ())}")
    _require(set(tier_batteries) == FULL_TIER_SET,
             "tier batteries must cover exactly the frozen full tier set")
    _require(repo in PINNED_MANIFEST_SHA256, f"unknown repository {repo!r}")
    _require(sha256_file(manifest_path) == PINNED_MANIFEST_SHA256[repo],
             f"assembly manifest does not match the pinned pilot manifest: "
             f"{repo}")
    _check_ledger(repo, ledger, tier_completions)

    from v2b_common import load_json
    manifest_value, _ = load_json(manifest_path)
    if mode == "k4x":
        k4x = manifest_value.get("k4x") or {}
        _require(k4x.get("applicable") is True
                 and k4x.get("external_repo") == K4X_EXTERNAL["repo"]
                 and k4x.get("external_revision")
                 == K4X_EXTERNAL["revision"],
                 "manifest k4x binding does not match the pinned external "
                 "snapshot")

    battery_shas = {}
    for tag in sorted(tier_batteries):
        path = tier_batteries[tag]
        _require(os.path.basename(path)
                 == _tier_entry(tag)["battery_file"],
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
            _require(generator.get("source_tree_hash")
                     == expected_scoring_tree,
                     f"{tag} completion was not scored at the pinned "
                     f"scoring tree: {repo}")
        masked, private = build_fn(
            complete_path, manifest_path, sample_path, candidates_path,
            LADDER_PUBLIC_SALT, LADDER_PUBLIC_SALT_NOTE)
        tier = _tier_entry(tag)
        run_identity = masked.get("run_identity") or {}
        _require(run_identity.get("model") == tier["model"]
                 and run_identity.get("revision") == tier["revision"]
                 and run_identity.get("dtype") == "bfloat16"
                 and run_identity.get("chunk_tokens")
                 == PRODUCTION_CHUNK_TOKENS,
                 f"completion identity does not match tier {tag}: {repo}")
        _require(run_identity.get("pilot_battery_sha256")
                 == battery_shas[tag],
                 f"completion does not bind the committed {tag} battery: "
                 f"{repo}")
        _require(masked["bindings"]["completion"].get("sha256")
                 == complete_binding["sha256"],
                 f"producer completion binding drift: {repo} {tag}")
        this_assembly = masked["bindings"]["assembly"].get("sha256")
        if assembly_sha is None:
            assembly_sha = this_assembly
        _require(this_assembly == assembly_sha,
                 f"tiers bind different assembly manifests: {repo} {tag}")

        language = masked.get("language")
        manifest_binding = dict(sha256=this_assembly)
        rows_all = complete.get("target_artifacts")
        manifest_targets = manifest_value.get("targets")
        complete_dir = os.path.dirname(os.path.abspath(complete_path))
        per_budget_rows = {budget: {name: [] for name in CONTRAST_NAMES}
                           for budget in BUDGETS}
        ref = "k4x" if mode == "k4x" else "k4"
        ref_eligible_keys = {budget: set() for budget in BUDGETS}
        for index, (row, manifest_row) in enumerate(
                zip(rows_all, manifest_targets)):
            cells_by_id = load_target_fn(
                row, index, complete_dir, complete, manifest_binding,
                manifest_value, manifest_row)
            key = row["target_key"]
            for budget in BUDGETS:
                if cells_by_id[f"{ref}:{budget}"].get("eligible") is True:
                    ref_eligible_keys[budget].add(key)
                extracted = extract_rows(
                    cells_by_id, language, key,
                    contrast_table(mode, budget))
                for name, target_row in extracted.items():
                    per_budget_rows[budget][name].append(target_row)

        budgets_out = {}
        for budget in BUDGETS:
            panel = build_panel(language, per_budget_rows[budget],
                                contrast_table(mode, budget))
            if mode == "budget" and repo == "physlib":
                panel = _force_physlib(panel)
            if mode == "budget" and budget == B_STAR:
                for name in CONTRAST_NAMES:
                    rows = per_budget_rows[budget][name]
                    recorded = private[name]
                    _require(recorded["n_rows"] == len(rows),
                             f"B* row count differs from the B3 producer: "
                             f"{repo} {tag} {name}")
                    if rows:
                        mean = math.fsum(
                            row["delta_bpb"] for row in rows) / len(rows)
                        _require(mean == recorded["removed_mean"],
                                 f"B* mean differs from the B3 producer: "
                                 f"{repo} {tag} {name}")
            budgets_out[str(budget)] = panel

        common = set.intersection(*(ref_eligible_keys[b] for b in BUDGETS))
        common_curve = {}
        for budget in BUDGETS:
            rows = [row for row in per_budget_rows[budget]["E1a"]
                    if row["target_key"] in common]
            common_curve[str(budget)] = dict(
                n=len(rows),
                mean_bpb=(math.fsum(row["delta_bpb"] for row in rows)
                          / len(rows)) if rows else None)
        if tag == SEALED_TIER and mode == "budget":
            mapping = {name: dict(
                n_rows=private[name]["n_rows"],
                removed_mean_bpb=private[name]["removed_mean"],
                fsum_correction=private[name]["fsum_correction"],
                total_centering_bpb=private[name]["total_centering"])
                for name in CONTRAST_NAMES}
            _check_sealed_consistency(repo, dict(
                bindings=dict(completion=masked["bindings"]["completion"]),
                centering_by_contrast=mapping), reveal)
        completion_bindings[tag] = masked["bindings"]["completion"]
        tiers[tag] = dict(
            tier=tag, language=language,
            model=run_identity["model"], revision=run_identity["revision"],
            budgets=budgets_out,
            common_subset_e1a=dict(
                n_common=len(common), keys=sorted(common),
                curve=common_curve))

    return dict(
        schema=SCHEMAS[mode], claim_status=CLAIMS[mode], mode=mode,
        repo=repo, metric="bpb", budgets=list(BUDGETS), b_star=B_STAR,
        model_pooling=False, language_pooling="prohibited",
        trend_inference="none-descriptive-dose-curve-only",
        non_bstar_reading_rule=NON_BSTAR_READING_RULE,
        k4x_external=(K4X_EXTERNAL if mode == "k4x" else None),
        e2_control_note=(
            "k5:0 draws from the PhysLib-internal non-dependency universe; "
            "in k4x mode it controls a partly-external reference arm"
            if mode == "k4x" else None),
        tier_order=sorted(tiers), tiers=tiers,
        bindings=dict(
            assembly_sha256=assembly_sha,
            manifest_sha256=PINNED_MANIFEST_SHA256[repo],
            sample_sha256=sha256_file(sample_path),
            candidates_sha256=sha256_file(candidates_path),
            batteries={tag: dict(path=os.path.abspath(tier_batteries[tag]),
                                 sha256=battery_shas[tag])
                       for tag in sorted(tier_batteries)},
            completions=completion_bindings,
            reveal_sha256=PINNED_REVEAL_SHA256,
            ladder_amendment=dict(
                path=LADDER_AMENDMENT_PATH,
                sha256=sha256_file(LADDER_AMENDMENT_PATH)),
            amendment=dict(path=AMENDMENT_PATH,
                           sha256=sha256_file(AMENDMENT_PATH)),
            ledger_sha256=ledger.get("_binding_sha256")),
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="analyze_v2b_dose.py"))


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
    ap.add_argument("--mode", required=True, choices=MODES)
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
    for path in (args.manifest, args.sample, args.ledger, args.reveal,
                 AMENDMENT_PATH, LADDER_AMENDMENT_PATH,
                 *batteries.values()):
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
        args.mode, args.repo, args.manifest, args.sample, args.candidates,
        completions, batteries, ledger, reveal)
    digest = write_new_json(args.out, artifact)
    print(f"V2B-DOSE-ANALYZED {args.mode} {args.repo} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
