#!/usr/bin/env python3
"""Frozen interior-budget consumer (EPOCH2_NIGHT_AMENDMENT, Part B phase 3).

Reads the interior completions ({8192,16384,32768} on the pinned interior
manifests, epoch-2 batteries, all six tiers) and produces per-repo
artifacts with: fresh panels at 8192/32768 via the UNCHANGED dose panel
machinery; the 16384 REPLICATION GATE against the committed pilot
completions under the adopted preconditions (environment-fingerprint
equality AND per-target 16384 metadata-grid equality — equal preconditions
make any primary-bpb inequality a measurement-identity incident that
aborts; unequal preconditions discard the duplicates as non-comparable,
recorded); and the merged FIVE-POINT common-subset E1a dose curve
{4,8,16,32,64}KiB joining the committed budget-response artifact's rows
with the interior rows on the joint eligible target set. All existing
claim labels and the non-B* reading rule carry over; nothing here is a
trend statistic."""
import argparse
import math
import os
import sys

from analyze_v2b_dose import (
    BUDGETS as DOSE_BUDGETS, CONTRAST_NAMES, build_panel, contrast_table,
    extract_rows)
from analyze_v2b_nll_ladder import (
    COMPLETE_SCHEMA, FULL_TIER_SET, LADDER_LEDGER_SCHEMA,
    LADDER_PUBLIC_SALT, LADDER_PUBLIC_SALT_NOTE,
    PINNED_MANIFEST_SHA256, PINNED_SCORING_TREE_BY_TIER, SEALED_TIER,
    _check_ledger, _require, _tier_entry)
from eval_paired import _target_cell_rows
from layout import PRODUCTION_CHUNK_TOKENS
from prepare_v2b_masked_deltas import _cell_bpb, _load_target
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, load_json, sha256_file, \
    write_new_json

INTERIOR_SCHEMA = "v2b_interior_dose_v1"
INTERIOR_CLAIM = "exploratory-nll-only-interior-budget-response"
INTERIOR_BUDGETS = (8192, 16384, 32768)
NEW_BUDGETS = (8192, 32768)
B_STAR = 16384
MERGED_BUDGETS = (4096, 8192, 16384, 32768, 65536)
PINNED_INTERIOR_MANIFEST_SHA256 = {
    "mathlib4":
        "1ea57d0c2941a337efdb50d3d3476718398445e796cafe3ea6a9c66ef90831f6",
    "sympy":
        "da99692033a7828807d39071c4e9eee23ea0cbaf1d3b0c0082e8ae733e460131",
}
# Filled by the post-scoring pin commit; every interior completion (all
# six tiers, no sealed exemption) must have been scored at exactly this
# tree. None => the consumer refuses to run.
PINNED_INTERIOR_SCORING_TREE = None
AMENDMENT_PATH = "results_v2/v2b/EPOCH2_NIGHT_AMENDMENT.md"


def _grid_16k(manifest_row):
    rows = [dict(row) for row in _target_cell_rows(manifest_row)
            if str(row.get("cell_id", "")).endswith(":16384")
            or row.get("cell_id") == "k1"]
    for row in rows:
        row.pop("row", None)
    return rows


def replication_gate(interior_manifest, pilot_manifest,
                     interior_env, pilot_env,
                     interior_cells_by_target, pilot_cells_by_target):
    """Returns the gate report; raises on a true replication failure."""
    if interior_env != pilot_env:
        # Environment mismatch is legitimately tier-wide: no target of
        # this tier is comparable.
        return dict(status="discarded-non-comparable",
                    reason="environment-fingerprint-differs",
                    n_compared=0, discarded_targets=[])
    compared = 0
    discarded = []
    for key, icells in interior_cells_by_target.items():
        # Per-target discards (review fix): one benign mismatch must not
        # exempt the REMAINING targets from the incident check.
        pcells = pilot_cells_by_target.get(key)
        if pcells is None:
            discarded.append(dict(target=key,
                                  reason="pilot-lacks-target"))
            continue
        irow = interior_manifest[key]
        prow = pilot_manifest[key]
        if _grid_16k(irow) != _grid_16k(prow):
            discarded.append(dict(target=key,
                                  reason="metadata-grid-differs"))
            continue
        cell_mismatch = None
        for cell_id, icell in icells.items():
            if not (cell_id.endswith(":16384") or cell_id == "k1"):
                continue
            pcell = pcells.get(cell_id)
            if pcell is None or icell.get("eligible") != \
                    pcell.get("eligible"):
                cell_mismatch = f"eligibility-differs:{cell_id}"
                break
        if cell_mismatch is not None:
            discarded.append(dict(target=key, reason=cell_mismatch))
            continue
        for cell_id, icell in icells.items():
            if not (cell_id.endswith(":16384") or cell_id == "k1"):
                continue
            if icell.get("eligible") is not True:
                continue
            ib = _cell_bpb(icells, cell_id, key)
            pb = _cell_bpb(pcells, cell_id, key)
            if ib != pb:
                raise V2BError(
                    f"REPLICATION FAILURE (measurement-identity "
                    f"incident): {key} {cell_id} interior {ib!r} != "
                    f"pilot {pb!r}")
            compared += 1
    status = "replicated-exactly" if not discarded else \
        "replicated-with-per-target-discards"
    return dict(status=status, reason=None, n_compared=compared,
                discarded_targets=discarded)


def _load_completion_cells(complete_path, manifest_value, expected_tree):
    binding, complete = artifact_binding(complete_path, COMPLETE_SCHEMA)
    generator = complete.get("generator") or {}
    if expected_tree is not None:
        _require(generator.get("source_tree_hash") == expected_tree,
                 f"completion not scored at the pinned interior tree: "
                 f"{complete_path}")
    rows = complete.get("target_artifacts")
    targets = manifest_value.get("targets")
    _require(isinstance(rows, list) and isinstance(targets, list)
             and len(rows) == len(targets),
             f"completion/manifest target mismatch: {complete_path}")
    complete_dir = os.path.dirname(os.path.abspath(complete_path))
    manifest_binding = dict(
        sha256=(complete.get("assembly_manifest") or {}).get("sha256"))
    cells_by_target = {}
    row_by_target = {}
    for index, (row, manifest_row) in enumerate(zip(rows, targets)):
        cells = _load_target(row, index, complete_dir, complete,
                             manifest_binding, manifest_value,
                             manifest_row)
        cells_by_target[row["target_key"]] = cells
        row_by_target[row["target_key"]] = manifest_row
    run_identity = complete.get("run_identity") or {}
    return binding, run_identity, cells_by_target, row_by_target


def analyze_repo(repo, interior_manifest_path, pilot_manifest_path,
                 interior_completions, pilot_completions, tier_batteries,
                 ledger, pilot_ledger, committed_dose_path,
                 expected_tree=PINNED_INTERIOR_SCORING_TREE,
                 pilot_trees=None):
    _require(repo in PINNED_INTERIOR_MANIFEST_SHA256,
             f"interior consumer covers mathlib4/sympy only; got {repo!r}")
    _require(sha256_file(interior_manifest_path)
             == PINNED_INTERIOR_MANIFEST_SHA256[repo],
             f"interior manifest does not match its pin: {repo}")
    _require(set(interior_completions) == FULL_TIER_SET
             and set(pilot_completions) == FULL_TIER_SET
             and set(tier_batteries) == FULL_TIER_SET,
             "interior consumer requires the full tier set everywhere")
    _require(expected_tree is not None,
             "no pinned interior scoring tree yet; the post-scoring pin "
             "commit must land before analysis")
    _check_ledger(repo, ledger, interior_completions)
    # Review blocker: the replication gate's REFERENCE side must be as
    # pinned as its subject — the pilot manifest matches the ladder pin,
    # pilot completions match the committed ladder ledger rows, and every
    # non-sealed pilot completion matches its per-tier scoring-tree pin.
    _require(sha256_file(pilot_manifest_path)
             == PINNED_MANIFEST_SHA256[repo],
             f"pilot manifest does not match the pinned pilot manifest: "
             f"{repo}")
    _check_ledger(repo, pilot_ledger, pilot_completions)
    if pilot_trees is None:
        pilot_trees = PINNED_SCORING_TREE_BY_TIER
    dose_binding, dose = artifact_binding(committed_dose_path)
    _require(dose.get("repo") == repo
             and dose.get("schema") == "v2b_budget_response_v1",
             "committed dose artifact mismatch")

    interior_manifest, _ = load_json(interior_manifest_path)
    pilot_manifest, _ = load_json(pilot_manifest_path)
    _require(tuple(interior_manifest.get("budgets") or ())
             == INTERIOR_BUDGETS,
             "interior manifest budget grid drift")

    battery_shas = {tag: sha256_file(path)
                    for tag, path in tier_batteries.items()}
    tiers = {}
    for tag in sorted(FULL_TIER_SET):
        tier = _tier_entry(tag)
        binding, run_identity, icells, irows = _load_completion_cells(
            interior_completions[tag], interior_manifest, expected_tree)
        _require(run_identity.get("model") == tier["model"]
                 and run_identity.get("revision") == tier["revision"]
                 and run_identity.get("dtype") == "bfloat16"
                 and run_identity.get("chunk_tokens")
                 == PRODUCTION_CHUNK_TOKENS,
                 f"interior completion identity drift: {repo} {tag}")
        _require(run_identity.get("pilot_battery_sha256")
                 == battery_shas[tag],
                 f"interior completion does not bind the epoch battery: "
                 f"{repo} {tag}")
        pilot_pin = None if tag == SEALED_TIER else pilot_trees.get(tag)
        _require(tag == SEALED_TIER
                 or (isinstance(pilot_pin, str) and pilot_pin),
                 f"pilot tier {tag} has no scoring-tree pin")
        _, pilot_identity, pcells, prows = _load_completion_cells(
            pilot_completions[tag], pilot_manifest, pilot_pin)
        language = interior_manifest.get("language")

        gate = replication_gate(
            irows, prows,
            run_identity.get("env_fingerprint"),
            pilot_identity.get("env_fingerprint"),
            icells, pcells)

        budgets_out = {}
        rows_by_budget = {}
        for budget in NEW_BUDGETS:
            rows_by_name = {name: [] for name in CONTRAST_NAMES}
            for key, cells in icells.items():
                extracted = extract_rows(cells, language, key,
                                         contrast_table("budget", budget))
                for name, target_row in extracted.items():
                    rows_by_name[name].append(target_row)
            panel = build_panel(language, rows_by_name,
                                contrast_table("budget", budget))
            budgets_out[str(budget)] = panel
            rows_by_budget[budget] = rows_by_name

        # merged five-point common-subset E1a curve
        dose_tier = (dose.get("tiers") or {}).get(tag) or {}
        committed_rows = {}
        for budget in DOSE_BUDGETS:
            panel = (dose_tier.get("budgets") or {}).get(str(budget)) or {}
            rows = ((panel.get("contrasts") or {}).get("E1a") or {}) \
                .get("target_rows") or []
            committed_rows[budget] = {row["target_key"]: row["delta_bpb"]
                                      for row in rows}
        interior_e1a = {budget: {row["target_key"]: row["delta_bpb"]
                                 for row in rows_by_budget[budget]["E1a"]}
                        for budget in NEW_BUDGETS}
        joint = set.intersection(
            *(set(committed_rows[b]) for b in DOSE_BUDGETS),
            *(set(interior_e1a[b]) for b in NEW_BUDGETS))
        curve = {}
        for budget in MERGED_BUDGETS:
            source = committed_rows.get(budget) or interior_e1a.get(budget)
            vals = [source[k] for k in joint] if joint else []
            curve[str(budget)] = dict(
                n=len(vals),
                mean_bpb=(math.fsum(vals) / len(vals)) if vals else None,
                source=("committed" if budget in DOSE_BUDGETS
                        else "interior"))
        tiers[tag] = dict(
            tier=tag, language=language,
            model=run_identity["model"], revision=run_identity["revision"],
            replication_gate=gate,
            budgets=budgets_out,
            merged_common_subset_e1a=dict(
                n_joint=len(joint), keys=sorted(joint), curve=curve),
            completion=dict(sha256=binding["sha256"]))

    return dict(
        schema=INTERIOR_SCHEMA, claim_status=INTERIOR_CLAIM, repo=repo,
        metric="bpb", budgets=list(INTERIOR_BUDGETS), b_star=B_STAR,
        merged_budgets=list(MERGED_BUDGETS),
        model_pooling=False, language_pooling="prohibited",
        trend_inference="none-descriptive-dose-curve-only",
        non_bstar_reading_rule=(
            "non-B* panels are descriptive dose-curve context only; "
            "B* panels in the committed pilot artifacts remain the only "
            "headline cells"),
        public_salt=LADDER_PUBLIC_SALT_NOTE,
        tier_order=sorted(tiers), tiers=tiers,
        bindings=dict(
            interior_manifest_sha256=PINNED_INTERIOR_MANIFEST_SHA256[repo],
            pilot_manifest_sha256=sha256_file(pilot_manifest_path),
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
                       program="analyze_v2b_interior.py"))


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
    ap.add_argument("--repo", required=True)
    ap.add_argument("--interior-manifest", required=True)
    ap.add_argument("--pilot-manifest", required=True)
    ap.add_argument("--completion", action="append", metavar="TIER=PATH")
    ap.add_argument("--pilot-completion", action="append",
                    metavar="TIER=PATH")
    ap.add_argument("--battery", action="append", metavar="TIER=PATH")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--pilot-ledger", required=True)
    ap.add_argument("--committed-dose", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    for path in (args.interior_manifest, args.pilot_manifest, args.ledger,
                 args.pilot_ledger, args.committed_dose, AMENDMENT_PATH):
        require_committed(path)
    batteries = _parse_tier_args(args.battery, "battery")
    for path in batteries.values():
        require_committed(path)
    ledger_binding, ledger = artifact_binding(args.ledger,
                                              LADDER_LEDGER_SCHEMA)
    ledger = dict(ledger, _binding_sha256=ledger_binding["sha256"])
    pilot_ledger_binding, pilot_ledger = artifact_binding(
        args.pilot_ledger, LADDER_LEDGER_SCHEMA)
    pilot_ledger = dict(pilot_ledger,
                        _binding_sha256=pilot_ledger_binding["sha256"])
    artifact = analyze_repo(
        args.repo, args.interior_manifest, args.pilot_manifest,
        _parse_tier_args(args.completion, "completion"),
        _parse_tier_args(args.pilot_completion, "pilot-completion"),
        batteries, ledger, pilot_ledger, args.committed_dose)
    digest = write_new_json(args.out, artifact)
    print(f"V2B-INTERIOR-ANALYZED {args.repo} {args.out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
