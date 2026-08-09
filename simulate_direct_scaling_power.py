#!/usr/bin/env python3
"""Outcome-free clustered power simulation for the frozen A0/A1 ROPE."""
from __future__ import annotations

import argparse
import itertools
import math
import os
import random
import statistics

from direct_scaling_protocol import load_protocol
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import V2BError, sha256_sorted_json, write_new_json


POWER_SCHEMA = "v2c_direct_scaling_power_v1"
T975 = {2: 4.302652729749462, 3: 3.182446305284263,
        8: 2.3060041350333704}


def _classifies(name: str, low: float, high: float, rope: float) -> bool:
    if name == "compatible":
        return low >= -rope and high <= rope
    if name == "outside-positive":
        return low > rope
    if name == "boundary":
        return low <= rope <= high
    raise V2BError(f"unknown power scenario {name!r}")


def simulate_cell(*, seed: int, replicates: int, scenario: dict,
                  n_repos: int, units_per_repo: int,
                  unit_slope_sd: float, repo_slope_sd: float,
                  rope: float) -> dict:
    if n_repos - 1 not in T975:
        raise V2BError("power simulator has no frozen t critical for n_repos")
    rng = random.Random(seed)
    tcrit = T975[n_repos - 1]
    correct = 0
    widths = []
    delta = float(scenario["true_delta_beta"])
    mean_noise_sd = unit_slope_sd / math.sqrt(units_per_repo)
    for _ in range(replicates):
        repo_means = [delta + rng.gauss(0.0, repo_slope_sd)
                      + rng.gauss(0.0, mean_noise_sd)
                      for _ in range(n_repos)]
        estimate = statistics.fmean(repo_means)
        se = statistics.stdev(repo_means) / math.sqrt(n_repos)
        low, high = estimate - tcrit * se, estimate + tcrit * se
        widths.append(high - low)
        correct += int(_classifies(scenario["name"], low, high, rope))
    return {
        "scenario": scenario["name"], "true_delta_beta": delta,
        "scenario_role": scenario["role"],
        "effective_repositories": n_repos,
        "paired_units_per_repository": units_per_repo,
        "unit_slope_sd": unit_slope_sd,
        "repository_slope_sd": repo_slope_sd,
        "replicates": replicates,
        "correct_classification_probability": correct / replicates,
        "median_ci_width": statistics.median(widths),
    }


def build_power(protocol_path: str, generator: dict) -> dict:
    protocol, protocol_sha = load_protocol(protocol_path)
    spec = protocol["power"]
    primary = spec["primary_assumptions"]
    rows = []
    for n, scenario in enumerate(spec["scenarios"]):
        rows.append(simulate_cell(
            seed=spec["simulation_seed_u64"] + n,
            replicates=spec["replicates"], scenario=scenario,
            n_repos=primary["effective_repositories"],
            units_per_repo=primary["paired_units_per_repository"],
            unit_slope_sd=primary["unit_slope_sd"],
            repo_slope_sd=primary["repository_slope_sd"],
            rope=protocol["analysis"]["rope_beta"],
        ))
    sensitivity = []
    grid = spec["sensitivity_grid"]
    combos = itertools.product(grid["effective_repositories"],
                              grid["unit_slope_sd"],
                              grid["repo_slope_sd"])
    cell = 0
    for n_repos, unit_sd, repo_sd in combos:
        for n, scenario in enumerate(spec["scenarios"]):
            sensitivity.append(simulate_cell(
                seed=spec["simulation_seed_u64"] + 1000 + cell * 10 + n,
                replicates=spec["replicates"], scenario=scenario,
                n_repos=n_repos,
                units_per_repo=primary["paired_units_per_repository"],
                unit_slope_sd=unit_sd, repo_slope_sd=repo_sd,
                rope=protocol["analysis"]["rope_beta"],
            ))
        cell += 1
    threshold = spec["minimum_correct_classification_probability"]
    gating_names = set(spec["adequacy"]["gating_scenarios"])
    primary_ok = all(
        row["correct_classification_probability"] >= threshold
        for row in rows if row["scenario"] in gating_names)
    boundary_rows = [row for row in rows
                     if row["scenario_role"] ==
                     "coverage-diagnostic-only"]
    if len(boundary_rows) != 1:
        raise V2BError("expected exactly one boundary coverage row")
    boundary_coverage = {
        "scenario": "boundary",
        "coverage_probability": boundary_rows[0][
            "correct_classification_probability"],
        "gating": False,
    }
    adequacy_boundaries = []
    for n_repos in grid["effective_repositories"]:
        for unit_sd in grid["unit_slope_sd"]:
            tested = []
            prefix_open = True
            largest = None
            for repo_sd in grid["repo_slope_sd"]:
                cell_rows = [
                    row for row in sensitivity
                    if row["effective_repositories"] == n_repos
                    and math.isclose(row["unit_slope_sd"], unit_sd)
                    and math.isclose(row["repository_slope_sd"], repo_sd)
                    and row["scenario"] in gating_names
                ]
                if len(cell_rows) != len(gating_names):
                    raise V2BError("incomplete simulated adequacy cell")
                minimum_probability = min(
                    row["correct_classification_probability"]
                    for row in cell_rows)
                passes = minimum_probability >= threshold
                tested.append({
                    "repository_slope_sd": repo_sd,
                    "minimum_gating_probability": minimum_probability,
                    "passes": passes,
                })
                if prefix_open and passes:
                    largest = repo_sd
                else:
                    prefix_open = False
            adequacy_boundaries.append({
                "effective_repositories": n_repos,
                "unit_slope_sd": unit_sd,
                "largest_contiguous_passing_repository_slope_sd": largest,
                "tested_repository_slope_sd": tested,
            })
    primary_boundary = next(
        row for row in adequacy_boundaries
        if row["effective_repositories"] ==
        primary["effective_repositories"]
        and math.isclose(row["unit_slope_sd"], primary["unit_slope_sd"]))
    supported_sd = primary_boundary[
        "largest_contiguous_passing_repository_slope_sd"]
    assumed_variance_supported = supported_sd is not None \
        and primary["repository_slope_sd"] <= supported_sd
    simulation_ok = primary_ok and assumed_variance_supported
    artifact = {
        "schema": POWER_SCHEMA,
        "protocol": {"path": os.path.basename(protocol_path),
                     "sha256": protocol_sha,
                     "binding": protocol["protocol_binding"]},
        "primary_rows": rows,
        "sensitivity_rows": sensitivity,
        "adequacy_boundaries": adequacy_boundaries,
        "boundary_coverage_diagnostic": boundary_coverage,
        "decision": {
            "threshold": threshold,
            "central_scenario_power_ok": primary_ok,
            "declared_assumption_within_adequacy_boundary":
                assumed_variance_supported,
            "power_simulation_ok_at_declared_assumption": simulation_ok,
            "variance_assumption_status":
                "assumption-only-pending-disjoint-calibration",
            "variance_calibration_required": True,
            "language_general_scoring_authorized": False,
            "structural_census_authorized": simulation_ok,
            "authorization_reason":
                "variance-calibration-not-yet-sealed",
        },
        "generator": generator,
    }
    artifact["power_binding"] = sha256_sorted_json(artifact)
    return artifact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not source_clean():
        raise V2BError("source tree must be clean before P0 power publication")
    commit, tree = head_commit(), source_tree_hash()
    artifact = build_power(
        args.protocol,
        {"program": os.path.basename(__file__), "source_commit": commit,
         "source_tree_hash": tree},
    )
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed during P0 power simulation")
    digest = write_new_json(args.out, artifact)
    print("[v2c-power] "
          f"simulation_ok={artifact['decision']['power_simulation_ok_at_declared_assumption']} "
          f"scoring_authorized={artifact['decision']['language_general_scoring_authorized']} "
          f"-> {args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
