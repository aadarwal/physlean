#!/usr/bin/env python3
"""Frozen supplement consumer (EPOCH2_NIGHT_AMENDMENT, Part C).

Reads the mathlib4 supplement completions (n<=120 fresh deep-draw targets,
full grid {4096,16384,65536}, all six tiers, epoch-2 batteries) and
produces: supplement-only panels per (tier, budget) via the UNCHANGED dose
machinery; and the predeclared POOLED panels — pilot rows (taken verbatim
from the committed budget-response artifact) plus supplement rows,
disjointness verified per budget, pooled inference via the unchanged
module-clustered machinery — labeled
`exploratory-nll-only-supplemented-pilot`. The pilot-vs-supplement
contrast (both means, difference) is always reported. The supplement
manifest pin and the epoch-2 scoring-tree pin are None until their
post-run pin commits; the consumer refuses to run before both exist."""
import argparse
import os
import sys

from analyze_v2b_dose import (
    BUDGETS, CONTRAST_NAMES, build_panel, contrast_table, extract_rows)
from analyze_v2b_interior import (
    PINNED_INTERIOR_SCORING_TREE as EPOCH2_SCORING_TREE,
    _load_completion_cells)
from analyze_v2b_nll_ladder import (
    FULL_TIER_SET, LADDER_LEDGER_SCHEMA, LADDER_PUBLIC_SALT_NOTE,
    _check_ledger, _require, _tier_entry)
from layout import PRODUCTION_CHUNK_TOKENS
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, load_json, sha256_file, \
    write_new_json

SUPPLEMENT_ANALYSIS_SCHEMA = "v2b_supplement_dose_v1"
SUPPLEMENT_CLAIM = "exploratory-nll-only-supplemented-pilot"
REPO = "mathlib4"
# Post-assembly pin: supplement_job20050588_0_mathlib4.json — the one
# supplement assembly submission (job id recorded per the review note).
PINNED_SUPPLEMENT_MANIFEST_SHA256 = \
    "2543b185e8d6d9359a112079df7b98dfd6547015b7b88a5ac29a3ea1ba5c88e5"
AMENDMENT_PATH = "results_v2/v2b/EPOCH2_NIGHT_AMENDMENT.md"


def _pool(pilot_rows, supplement_rows, label):
    pilot_keys = {row["target_key"] for row in pilot_rows}
    supp_keys = {row["target_key"] for row in supplement_rows}
    overlap = pilot_keys & supp_keys
    _require(not overlap,
             f"pilot/supplement identity overlap in {label}: "
             f"{sorted(overlap)[:3]}")
    return sorted(pilot_rows + supplement_rows,
                  key=lambda row: row["target_key"])


def analyze_supplement(supplement_manifest_path, committed_dose_path,
                       supplement_completions, tier_batteries, ledger,
                       expected_manifest_sha=None, expected_tree=None):
    if expected_manifest_sha is None:
        expected_manifest_sha = PINNED_SUPPLEMENT_MANIFEST_SHA256
    if expected_tree is None:
        expected_tree = EPOCH2_SCORING_TREE
    _require(expected_manifest_sha is not None,
             "no pinned supplement manifest yet; the post-assembly pin "
             "commit must land before analysis")
    _require(expected_tree is not None,
             "no pinned epoch-2 scoring tree yet; the post-scoring pin "
             "commit must land before analysis")
    _require(sha256_file(supplement_manifest_path)
             == expected_manifest_sha,
             "supplement manifest does not match its pin")
    _require(set(supplement_completions) == FULL_TIER_SET
             and set(tier_batteries) == FULL_TIER_SET,
             "supplement consumer requires the full tier set")
    _check_ledger(REPO, ledger, supplement_completions)
    dose_binding, dose = artifact_binding(committed_dose_path)
    _require(dose.get("repo") == REPO
             and dose.get("schema") == "v2b_budget_response_v1",
             "committed dose artifact mismatch")
    manifest, _ = load_json(supplement_manifest_path)
    _require(manifest.get("repo") == REPO
             and tuple(manifest.get("budgets") or ()) == BUDGETS,
             "supplement manifest repo/budget drift")

    battery_shas = {tag: sha256_file(path)
                    for tag, path in tier_batteries.items()}
    tiers = {}
    for tag in sorted(FULL_TIER_SET):
        tier = _tier_entry(tag)
        binding, run_identity, cells_by_target, _rows = \
            _load_completion_cells(supplement_completions[tag], manifest,
                                   expected_tree)
        _require(run_identity.get("model") == tier["model"]
                 and run_identity.get("revision") == tier["revision"]
                 and run_identity.get("dtype") == "bfloat16"
                 and run_identity.get("chunk_tokens")
                 == PRODUCTION_CHUNK_TOKENS,
                 f"supplement completion identity drift: {tag}")
        _require(run_identity.get("pilot_battery_sha256")
                 == battery_shas[tag],
                 f"supplement completion does not bind the epoch battery: "
                 f"{tag}")
        language = manifest.get("language")
        dose_tier = (dose.get("tiers") or {}).get(tag)
        _require(isinstance(dose_tier, dict) and dose_tier,
                 f"committed dose artifact lacks tier {tag}; the pooled "
                 f"panel requires the six-tier dose rerun")

        budgets_out = {}
        for budget in BUDGETS:
            supp_rows_by_name = {name: [] for name in CONTRAST_NAMES}
            for key, cells in cells_by_target.items():
                extracted = extract_rows(cells, language, key,
                                         contrast_table("budget", budget))
                for name, row in extracted.items():
                    supp_rows_by_name[name].append(row)
            supp_panel = build_panel(
                language, dict(supp_rows_by_name),
                contrast_table("budget", budget))
            pilot_panel = (dose_tier.get("budgets") or {}) \
                .get(str(budget)) or {}
            pooled_rows_by_name = {}
            pilot_ns = {}
            for name in CONTRAST_NAMES:
                pilot_rows = ((pilot_panel.get("contrasts") or {})
                              .get(name) or {}).get("target_rows") or []
                pilot_ns[name] = len(pilot_rows)
                pooled_rows_by_name[name] = _pool(
                    pilot_rows, supp_rows_by_name[name],
                    f"{tag} {budget} {name}")
            pooled_panel = build_panel(language, pooled_rows_by_name,
                                       contrast_table("budget", budget))
            contrast_notes = {}
            for name in CONTRAST_NAMES:
                p_inf = ((pilot_panel.get("contrasts") or {})
                         .get(name) or {}).get("inference") or {}
                s_inf = supp_panel["contrasts"][name]["inference"]
                p_mean = p_inf.get("target_equal_mean_bpb")
                s_mean = s_inf.get("target_equal_mean_bpb")
                contrast_notes[name] = dict(
                    pilot_n=pilot_ns[name],
                    pilot_mean_bpb=p_mean,
                    supplement_n=s_inf.get("n_targets"),
                    supplement_mean_bpb=s_mean,
                    difference_bpb=((s_mean - p_mean)
                                    if isinstance(p_mean, float)
                                    and isinstance(s_mean, float)
                                    else None),
                    note="descriptive pilot-vs-supplement contrast; "
                         "same draw law, disjoint identities")
            budgets_out[str(budget)] = dict(
                supplement=supp_panel,
                pooled=pooled_panel,
                pilot_vs_supplement=contrast_notes)
        tiers[tag] = dict(
            tier=tag, language=language,
            model=run_identity["model"], revision=run_identity["revision"],
            budgets=budgets_out,
            completion=dict(sha256=binding["sha256"]))

    return dict(
        schema=SUPPLEMENT_ANALYSIS_SCHEMA, claim_status=SUPPLEMENT_CLAIM,
        repo=REPO, metric="bpb", budgets=list(BUDGETS),
        model_pooling=False, language_pooling="prohibited",
        trend_inference="none-descriptive-dose-curve-only",
        non_bstar_reading_rule=(
            "per-panel Holm within (tier, budget); non-B* positive flags "
            "are descriptive dose-curve context only"),
        public_salt=LADDER_PUBLIC_SALT_NOTE,
        tier_order=sorted(tiers), tiers=tiers,
        bindings=dict(
            supplement_manifest_sha256=expected_manifest_sha,
            committed_dose=dict(path=os.path.abspath(committed_dose_path),
                                sha256=dose_binding["sha256"]),
            batteries={tag: dict(path=os.path.abspath(tier_batteries[tag]),
                                 sha256=battery_shas[tag])
                       for tag in sorted(tier_batteries)},
            ledger_sha256=ledger.get("_binding_sha256"),
            amendment=dict(path=AMENDMENT_PATH,
                           sha256=sha256_file(AMENDMENT_PATH))),
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="analyze_v2b_supplement.py"))


def _parse_tier_args(pairs, label):
    out = {}
    for pair in pairs or ():
        tag, sep, path = pair.partition("=")
        if not sep or not tag or not path:
            raise V2BError(f"malformed --{label}: {pair!r}")
        if tag in out:
            raise V2BError(f"duplicate --{label} tier: {tag}")
        out[tag] = path
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supplement-manifest", required=True)
    ap.add_argument("--committed-dose", required=True)
    ap.add_argument("--completion", action="append", metavar="TIER=PATH")
    ap.add_argument("--battery", action="append", metavar="TIER=PATH")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    for path in (args.supplement_manifest, args.committed_dose,
                 args.ledger, AMENDMENT_PATH):
        require_committed(path)
    batteries = _parse_tier_args(args.battery, "battery")
    for path in batteries.values():
        require_committed(path)
    ledger_binding, ledger = artifact_binding(args.ledger,
                                              LADDER_LEDGER_SCHEMA)
    ledger = dict(ledger, _binding_sha256=ledger_binding["sha256"])
    artifact = analyze_supplement(
        args.supplement_manifest, args.committed_dose,
        _parse_tier_args(args.completion, "completion"),
        batteries, ledger)
    digest = write_new_json(args.out, artifact)
    print(f"V2B-SUPPLEMENT-ANALYZED {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
