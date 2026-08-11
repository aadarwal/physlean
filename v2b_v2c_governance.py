#!/usr/bin/env python3
"""V2-c amended governance (V2C_FEASIBILITY_AMENDMENT, ADOPTED).

Consumes ONLY: (a) the committed BLIND governance artifacts (per-family
sigma_b2/sigma_w2/cluster sizes/module counts — computed and committed
before the exploratory reveal), (b) committed candidate tables and the
committed pilot sample (the frozen plan projection and pilot
exclusion), and (c) the model-free k4-mass scan artifacts. Emits the
V2-c plan: per repo, the standardized-power N (power 0.9 at one-sided
alpha 0.025 against 0.5*sigma_target, N in [40, 400], with the
predeclared 0.2/0.8 anchor sensitivity), the primary budget B*_repo
(largest grid budget with k4-fill fraction >= 0.60, with 0.50/0.70
threshold sensitivities and the original-16KiB co-report obligation
for every moved repo), and the frozen test-module stratum shares. No
model output, mean, or sign is read anywhere in this module."""
import argparse
import math
import re
import sys

from prepare_v2b_assembly import EXPECTED
from v2b_a6_blind import require_committed
from v2b_common import (BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA, V2BError,
                        artifact_binding, sha256_json, write_new_json)
from v2b_metadata import build_sample_plan
from v2b_n_governance import N_GOVERNANCE_SCHEMA, T_0975_BY_DF, _pilot_keys
from provenance import head_commit, source_clean, source_tree_hash
from scan_v2c_k4_mass import SCAN_SCHEMA

V2C_PLAN_SCHEMA = "v2c_governance_plan_v1"
V2C_CLAIM_LABEL = "confirmatory-with-post-pilot-amended-governance"
AMENDMENT_PATH = "results_v2/v2b/V2C_FEASIBILITY_AMENDMENT_DRAFT.md"
AMENDMENT_SHA256 = \
    "49ff6d8f9650921eeb02d0e0e404fa7d991f277a020fe783a10d4b1bced7bc37"
N_MIN, N_MAX = 40, 400
POWER_ANCHOR = 0.5
ANCHOR_SENSITIVITY = (0.2, 0.8)
FILL_FLOOR = 0.60
FILL_SENSITIVITY = (0.50, 0.70)
BUDGET_GRID = (4096, 16384, 65536)
ORIGINAL_PRIMARY = 16384
# Frozen one-sided 90% Student-t quantiles (power 0.9), df 1-19 plus
# the classic printed breakpoints — same lookup convention as the
# SUPPLEMENT_DF_EXTENSION rule (largest tabulated entry <= df).
T_090_BY_DF = {
    1: 3.077684, 2: 1.885618, 3: 1.637744, 4: 1.533206, 5: 1.475884,
    6: 1.439756, 7: 1.414924, 8: 1.396815, 9: 1.383029, 10: 1.372184,
    11: 1.363430, 12: 1.356217, 13: 1.350171, 14: 1.345030,
    15: 1.340606, 16: 1.336757, 17: 1.333379, 18: 1.330391,
    19: 1.327728, 20: 1.325341, 25: 1.316345, 30: 1.310415,
    40: 1.303077, 60: 1.295821, 80: 1.292224, 120: 1.288650,
}
# Frozen test-module stratum matcher (amendment Problem 3), applied to
# BOTH the dot-form module path and the slash-form source path.
TEST_STRATUM_RE = re.compile(
    r"(^|/)tests?/|(^|/)test_|(^|\.)tests?(\.|$)|(^|\.)test_"
    r"|(^|/|\.)conftest(\.|$)|(^|/|\.)testing(/|\.|$)")


def _require(condition, message):
    if not condition:
        raise V2BError(message)


def _t_floor(table, df):
    if df in table:
        return table[df]
    if df >= 20:
        return table[max(k for k in table if k <= df)]
    raise V2BError(f"no frozen t quantile for df={df}")


def _projected_se(sigma_b2, sigma_w2, module_sizes):
    total = sum(module_sizes)
    variance = sigma_b2 * math.fsum(m * m for m in module_sizes) \
        / (total * total) + sigma_w2 / total
    _require(variance >= 0 and math.isfinite(variance),
             "projected variance is negative/non-finite")
    return math.sqrt(variance)


def standardized_power_n(family, module_sizes_by_n, anchor):
    """Smallest N in [N_MIN, N_MAX] whose projected one-sided test at
    alpha 0.025 has power >= 0.9 against a true mean of
    anchor*sigma_target — via the frozen approximation
    anchor*sigma_target >= (t_.975(df) + t_.90(df)) * SE(N), with the
    pilot-family df, exactly the section-15.A14 projection convention."""
    sigma_b2 = family["sigma_b2"]
    sigma_w2 = family["sigma_w2"]
    sigma_target = math.sqrt(sigma_b2 + sigma_w2)
    if sigma_target <= 0 or not math.isfinite(sigma_target):
        return dict(chosen_n=None, verdict="degenerate-zero-variance")
    df = family["n_modules"] - 1
    _require(df >= 1, "family has insufficient pilot clusters")
    threshold = (_t_floor(T_0975_BY_DF, df)
                 + _t_floor(T_090_BY_DF, df))
    for n_candidate in range(N_MIN, N_MAX + 1):
        sizes = module_sizes_by_n[n_candidate]
        if sizes is None:
            continue
        se = _projected_se(sigma_b2, sigma_w2, sizes)
        if anchor * sigma_target >= threshold * se:
            return dict(chosen_n=n_candidate, verdict="powered",
                        df=df, threshold_t_sum=threshold,
                        sigma_target=sigma_target)
    return dict(chosen_n=None, verdict="under-powered-at-cap",
                df=df, threshold_t_sum=threshold,
                sigma_target=sigma_target)


def plan_repo(repo, governance_path, candidates_path, sample_path,
              scan_path):
    gov_binding, gov = artifact_binding(governance_path,
                                        N_GOVERNANCE_SCHEMA)
    _require(gov.get("repo") == repo, "governance artifact repo mismatch")
    cand_binding, candidates = artifact_binding(candidates_path,
                                                CANDIDATES_SCHEMA)
    _require(candidates.get("repo") == repo,
             "candidate table repo mismatch")
    _require((gov.get("bindings") or {}).get("candidates", {})
             .get("sha256") == cand_binding["sha256"],
             "candidate table is not the blind governance's sealed input")
    sample_binding, sample = artifact_binding(sample_path,
                                              BOUND_SAMPLE_SCHEMA)
    scan_binding, scan = artifact_binding(scan_path, SCAN_SCHEMA)
    _require(scan.get("repo") == repo, "scan artifact repo mismatch")
    _require((scan.get("bindings") or {}).get("candidates", {})
             .get("sha256") == cand_binding["sha256"],
             "scan was not computed over this candidate table")
    _require(scan.get("pilot_crosscheck") is not None
             and scan["pilot_crosscheck"].get("status") == "exact",
             "scan lacks the exact pilot cross-check")

    pilot = _pilot_keys(sample, repo)
    module_sizes_by_n = {}
    for n_candidate in range(N_MIN, N_MAX + 1):
        plan = build_sample_plan(candidates, n_candidate,
                                 exclude_keys=pilot)
        if plan["n_selected"] != n_candidate:
            module_sizes_by_n[n_candidate] = None
            continue
        counts = {}
        for row in plan["targets"]:
            module = row["identity"][0]
            counts[module] = counts.get(module, 0) + 1
        module_sizes_by_n[n_candidate] = sorted(counts.values(),
                                                reverse=True)

    families = gov.get("families") or {}
    _require(families, "governance artifact has no families")
    per_family = {}
    per_family_sensitivity = {}
    chosen = []
    for fid in sorted(families):
        family = families[fid]
        if family.get("verdict") in ("no-eligible-targets",
                                     "insufficient-clusters"):
            per_family[fid] = dict(chosen_n=None,
                                   verdict=family["verdict"])
            continue
        result = standardized_power_n(family, module_sizes_by_n,
                                      POWER_ANCHOR)
        per_family[fid] = result
        per_family_sensitivity[fid] = {
            str(anchor): standardized_power_n(family, module_sizes_by_n,
                                              anchor)
            for anchor in ANCHOR_SENSITIVITY}
        if result["chosen_n"] is not None:
            chosen.append(result["chosen_n"])
    repo_n = max(chosen) if chosen and all(
        row.get("chosen_n") is not None or row.get("verdict")
        in ("no-eligible-targets", "insufficient-clusters")
        for row in per_family.values()) else None

    fractions = scan.get("fill_fractions") or {}
    def primary_at(floor):
        # Amendment override clause: "16,384 stays primary wherever it
        # meets the floor" — the rule rescues repos DOWNWARD, never
        # moves a healthy repo up. (Fills are monotone non-increasing
        # in budget, so when 16384 fails the only possible eligible
        # budget is 4096.)
        eligible = [b for b in BUDGET_GRID
                    if isinstance(fractions.get(str(b)), float)
                    and fractions[str(b)] >= floor]
        if not eligible:
            return None
        if ORIGINAL_PRIMARY in eligible:
            return ORIGINAL_PRIMARY
        below = [b for b in eligible if b < ORIGINAL_PRIMARY]
        _require(below,
                 "fill fractions violate budget monotonicity: a budget "
                 "above the original primary is eligible while the "
                 "original is not")
        return max(below)
    primary = primary_at(FILL_FLOOR)
    budget_block = dict(
        fill_fractions=fractions, floor=FILL_FLOOR,
        primary_budget=primary,
        structurally_ineligible=primary is None,
        moved_off_original=(primary is not None
                            and primary != ORIGINAL_PRIMARY),
        original_primary_co_report_required=(
            primary is not None and primary != ORIGINAL_PRIMARY),
        threshold_sensitivity={str(floor): primary_at(floor)
                               for floor in FILL_SENSITIVITY})

    n_test = 0
    n_total = 0
    for target in candidates.get("targets") or ():
        n_total += 1
        module = str((target.get("identity") or ["", ""])[0])
        source = str(target.get("source_rel") or "")
        if TEST_STRATUM_RE.search(module) \
                or TEST_STRATUM_RE.search(source):
            n_test += 1
    stratum = dict(regex=TEST_STRATUM_RE.pattern, n_candidates=n_total,
                   n_test=n_test,
                   share=(n_test / n_total) if n_total else None)

    return dict(
        repo=repo, repo_n=repo_n,
        verdict=("planned" if repo_n is not None
                 and not budget_block["structurally_ineligible"]
                 else "structurally-ineligible"
                 if budget_block["structurally_ineligible"]
                 else "under-powered-or-unplanned"),
        power=dict(anchor=POWER_ANCHOR, alpha_one_sided=0.025,
                   power=0.9, n_range=[N_MIN, N_MAX],
                   families=per_family,
                   anchor_sensitivity=per_family_sensitivity,
                   anchor_provenance=(
                       "0.5 chosen post-reveal per the adopted "
                       "amendment; sensitivity anchors predeclared")),
        primary_budget=budget_block,
        test_stratum=stratum,
        pilot_exclusion=dict(n_excluded=len(pilot),
                             keys_sha256=sha256_json(sorted(pilot))),
        bindings=dict(governance=dict(sha256=gov_binding["sha256"]),
                      candidates=dict(sha256=cand_binding["sha256"]),
                      sample=dict(sha256=sample_binding["sha256"]),
                      scan=dict(sha256=scan_binding["sha256"])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", required=True,
                    metavar="REPO=GOV,CAND,SAMPLE,SCAN")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    require_committed(AMENDMENT_PATH)
    repos = {}
    for spec in args.repo:
        repo, sep, rest = spec.partition("=")
        parts = rest.split(",")
        if not sep or len(parts) != 4 or repo in repos:
            raise V2BError(f"malformed --repo spec: {spec!r}")
        for path in (parts[0], parts[3]):
            require_committed(path)
        require_committed(parts[2])
        repos[repo] = plan_repo(repo, *parts)
    _require(set(repos) == set(EXPECTED),
             "the plan covers exactly the five frozen corpora or nothing")
    planned = sorted(r for r, row in repos.items()
                     if row["verdict"] == "planned")
    common = None
    fractions_all = [set(b for b in BUDGET_GRID
                         if (repos[r]["primary_budget"]["fill_fractions"]
                             .get(str(b)) or 0) >= FILL_FLOOR)
                     for r in planned]
    if fractions_all:
        shared = set.intersection(*fractions_all)
        common = max(shared) if shared else None
    artifact = dict(
        schema=V2C_PLAN_SCHEMA, claim_label=V2C_CLAIM_LABEL,
        amendment=dict(path=AMENDMENT_PATH, sha256=AMENDMENT_SHA256,
                       provenance="governance amended post-reveal"),
        repos=repos, planned_repos=planned,
        largest_common_feasible_budget=common,
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="v2b_v2c_governance.py"))
    digest = write_new_json(args.out, artifact)
    print(f"V2C-GOVERNANCE-PLANNED {sorted(repos)} -> {args.out} "
          f"{digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
