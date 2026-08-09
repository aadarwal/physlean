#!/usr/bin/env python3
"""Frozen constants and strict validation for the direct-scaling P0 packet.

This module deliberately contains no model-scoring code.  It turns the
reviewed prose design plus exact corpus/model/config ledgers into one small,
machine-checkable artifact which every later structural or GPU stage must
bind by raw file SHA256.  A missing model config index is a P0 failure; the
runtime battery is not allowed to discover or choose model semantics later.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from v2b_common import (V2BError, load_json, sha256_file,
                        sha256_sorted_json)


PROTOCOL_SCHEMA = "v2c_direct_scaling_protocol_v1"
MODEL_CONFIG_INDEX_SCHEMA = "v2c_model_config_index_v1"
PROTOCOL_SEED_LABEL = "v2c-direct-scaling-p0-20260809"

LANGUAGE_REPOS = {
    "lean": ("batteries", "lean4", "mathlib4", "physlib"),
    "python": ("ase", "astropy", "plasmapy", "pymatgen", "qutip",
               "scipy", "sunpy", "sympy", "yt"),
    "cpp": ("geant4",),
}

PRIMARY_MODELS = (
    "Qwen/Qwen2.5-Coder-0.5B",
    "Qwen/Qwen2.5-Coder-1.5B",
    "Qwen/Qwen2.5-Coder-7B",
    "Qwen/Qwen3-0.6B-Base",
    "Qwen/Qwen3-1.7B-Base",
    "Qwen/Qwen3-8B-Base",
    "Qwen/Qwen3.5-0.8B-Base",
    "Qwen/Qwen3.5-2B-Base",
    "Qwen/Qwen3.5-9B-Base",
)


def _family(model_id: str) -> str:
    if model_id.startswith("Qwen/Qwen2.5-Coder-"):
        return "qwen2.5-coder"
    if model_id.startswith("Qwen/Qwen3.5-"):
        return "qwen3.5"
    if model_id.startswith("Qwen/Qwen3-"):
        return "qwen3"
    if model_id == "bigcode/starcoder2-3b":
        return "starcoder2"
    if model_id == "deepseek-ai/DeepSeek-Coder-V2-Lite-Base":
        return "deepseek-coder-v2"
    raise V2BError(f"unclassified frozen model {model_id!r}")


def _size_label(model_id: str) -> str:
    tail = model_id.rsplit("/", 1)[-1]
    for part in tail.replace("-Base", "").split("-"):
        if part.upper().endswith("B") \
                and part[:-1].replace(".", "").isdigit():
            return part.upper()
    if model_id == "deepseek-ai/DeepSeek-Coder-V2-Lite-Base":
        return "lite"
    raise V2BError(f"cannot derive frozen size label for {model_id!r}")


def _load_object(path: str | Path) -> tuple[dict, str]:
    value, digest = load_json(str(path))
    return value, digest


def _exact_keys(value: dict, expected: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        got = set(value) if isinstance(value, dict) else type(value).__name__
        raise V2BError(f"{where}: exact keys {expected!r} required, got {got!r}")


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def validate_model_config_index(index: dict, models_lock: dict,
                                models_lock_sha256: str) -> dict[str, dict]:
    _exact_keys(index, {"schema", "models_lock_sha256", "models",
                        "index_binding", "generator"},
                "model config index")
    if index["schema"] != MODEL_CONFIG_INDEX_SCHEMA:
        raise V2BError("wrong direct-scaling model config index schema")
    if index["models_lock_sha256"] != models_lock_sha256:
        raise V2BError("model config index does not bind models.json")
    rows = index["models"]
    if not isinstance(rows, list) or len(rows) != len(models_lock):
        raise V2BError("model config index must cover models.json exactly")
    by_id: dict[str, dict] = {}
    expected_row_keys = {"model_id", "revision", "config_sha256",
                         "tokenizer_files", "selected_config"}
    for n, row in enumerate(rows):
        _exact_keys(row, expected_row_keys, f"model config row {n}")
        model_id = row["model_id"]
        if model_id in by_id or model_id not in models_lock:
            raise V2BError(f"duplicate/extra model config row {model_id!r}")
        if row["revision"] != models_lock[model_id].get("sha"):
            raise V2BError(f"revision mismatch for {model_id}")
        for key in ("config_sha256",):
            if not _hex(row[key], 64):
                raise V2BError(f"{model_id}: invalid {key}")
        files = row["tokenizer_files"]
        if not isinstance(files, list) or not files:
            raise V2BError(f"{model_id}: tokenizer file ledger is empty")
        names = []
        for frow in files:
            _exact_keys(frow, {"name", "sha256"},
                        f"{model_id} tokenizer file")
            if not isinstance(frow["name"], str) or not frow["name"]:
                raise V2BError(f"{model_id}: invalid tokenizer filename")
            if not _hex(frow["sha256"], 64):
                raise V2BError(f"{model_id}: invalid tokenizer hash")
            names.append(frow["name"])
        if names != sorted(names) or len(names) != len(set(names)):
            raise V2BError(f"{model_id}: tokenizer ledger not unique/sorted")
        selected = row["selected_config"]
        required = {"architectures", "attention_class", "causal_config_path",
                    "causal_model_type", "full_attention_interval",
                    "layer_types", "linear_conv_kernel_dim",
                    "max_position_embeddings", "max_window_layers",
                    "model_type", "num_hidden_layers", "rope_parameters",
                    "rope_scaling", "sliding_window", "use_sliding_window"}
        _exact_keys(selected, required, f"{model_id} selected config")
        if not isinstance(selected["architectures"], list) \
                or not all(isinstance(x, str) and x
                           for x in selected["architectures"]):
            raise V2BError(f"{model_id}: invalid architectures")
        if selected["causal_config_path"] not in ([], ["text_config"]):
            raise V2BError(f"{model_id}: invalid causal config path")
        if selected["attention_class"] not in {
                "native-full", "sliding-window", "hybrid",
                "rope-extended"}:
            raise V2BError(f"{model_id}: invalid attention class")
        mpe = selected["max_position_embeddings"]
        if not isinstance(mpe, int) or isinstance(mpe, bool) or mpe <= 0:
            raise V2BError(f"{model_id}: invalid max_position_embeddings")
        by_id[model_id] = row
    if set(by_id) != set(models_lock):
        raise V2BError("model config index has missing/extra model identities")
    preimage = {k: index[k] for k in ("schema", "models_lock_sha256",
                                      "models", "generator")}
    if index["index_binding"] != sha256_sorted_json(preimage):
        raise V2BError("model config index binding mismatch")
    return by_id


def _seed(label: str) -> str:
    return hashlib.sha256(f"{PROTOCOL_SEED_LABEL}:{label}".encode()).hexdigest()


def _seed_u64(label: str) -> int:
    return int(_seed(label)[:16], 16)


def build_protocol(*, design_path: str | Path, corpora_lock_path: str | Path,
                   models_lock_path: str | Path,
                   model_config_index_path: str | Path,
                   generator: dict) -> dict:
    design_path = Path(design_path)
    if not design_path.is_file():
        raise V2BError(f"missing reviewed design: {design_path}")
    corpora_lock, corpora_sha = _load_object(corpora_lock_path)
    models_lock, models_sha = _load_object(models_lock_path)
    config_index, config_index_sha = _load_object(model_config_index_path)
    if not isinstance(corpora_lock.get("repos"), dict):
        raise V2BError("corpora lock has no repos object")
    expected_repos = {r for rows in LANGUAGE_REPOS.values() for r in rows}
    if set(corpora_lock["repos"]) != expected_repos:
        raise V2BError("direct-scaling panel must equal the 14 locked repos")
    by_id = validate_model_config_index(config_index, models_lock, models_sha)
    if not set(PRIMARY_MODELS) <= set(models_lock):
        raise V2BError("primary 3x3 ladder is not covered by models.json")

    repo_rows = []
    for language, repos in LANGUAGE_REPOS.items():
        for repo in repos:
            lock = corpora_lock["repos"][repo]
            _exact_keys(lock, {"url", "sha"}, f"corpus lock {repo}")
            repo_rows.append({"language": language, "repo": repo,
                              "url": lock["url"], "revision": lock["sha"]})

    model_rows = []
    for model_id in sorted(models_lock):
        locked = models_lock[model_id]
        _exact_keys(locked, {"created", "sha"}, f"models lock {model_id}")
        role = "primary-ladder" if model_id in PRIMARY_MODELS \
            else "secondary-panel"
        model_rows.append({
            "model_id": model_id,
            "revision": locked["sha"],
            "release_timestamp": locked["created"],
            "family": _family(model_id),
            "size_label": _size_label(model_id),
            "role": role,
            "config_sha256": by_id[model_id]["config_sha256"],
            "tokenizer_files": by_id[model_id]["tokenizer_files"],
            "selected_config": by_id[model_id]["selected_config"],
        })

    grid = [512 * (2 ** i) for i in range(12)]
    protocol = {
        "schema": PROTOCOL_SCHEMA,
        "protocol_state": "frozen-before-loss",
        "study_status": "prospective-exploratory-follow-up",
        "frozen_at_utc_date": "2026-08-09",
        "design": {"path": design_path.name,
                   "sha256": sha256_file(str(design_path))},
        "input_ledgers": {
            "corpora_lock": {"path": Path(corpora_lock_path).name,
                             "sha256": corpora_sha},
            "models_lock": {"path": Path(models_lock_path).name,
                            "sha256": models_sha},
            "model_config_index": {
                "path": Path(model_config_index_path).name,
                "sha256": config_index_sha,
                "binding": config_index["index_binding"],
            },
        },
        "panel": {"repositories": repo_rows, "models": model_rows,
                  "primary_models": list(PRIMARY_MODELS),
                  "locked_ladder_scope": "descriptive-checkpoint-specific",
                  "pooled_headline_status": "not-licensed-at-p0",
                  "k3_policy": (
                      "unevaluable-unless-three-distinct-families-pass-k1")},
        "sampling": {
            "seed_family": PROTOCOL_SEED_LABEL,
            "a0_seed_sha256": _seed("a0-origins"),
            "a1_seed_sha256": _seed("a1-targets"),
            "planned_per_repo": 200,
            "target_block_bytes": 4096,
            "minimum_realized_target_bytes": 2048,
            "primary_score_horizon_source_bytes": 512,
            "delta_formula": (
                "max(4096,floor((eligible_axis_bytes-4096)/"
                "max(1,planned_per_repo-1)))"),
            "systematic_offset_formula": (
                "u64be(sha256(seed_sha256,repo,arm)[:8]) mod delta"),
            "alignment": "next-utf8-line-boundary-at-or-after-coordinate",
            "overlap_policy": "reject-pairwise-overlap-never-resample",
            "identity_reuse": "same-origins-targets-all-orderings-models-rungs",
        },
        "stream": {
            "tracked_files_only": True,
            "source_suffixes": {
                "lean": [".lean"], "python": [".py"],
                "cpp": [".c", ".cc", ".cpp", ".cxx", ".h", ".hh",
                        ".hpp", ".hxx", ".icc"],
            },
            "path_exclusions": [],
            "metadata_header": (
                "\\n<V2C_FILE "
                "{compact_sorted_json(repo,path,source_sha256,source_bytes)}>\\n"),
            "metadata_visible_not_scored": True,
            "utf8_policy": "strict-no-replacement",
            "orderings": ["seeded-shuffled", "build-resolved-topological",
                          "reverse-topological"],
            "graph_gate": {"minimum_resolved_reference_fraction": 0.90,
                           "minimum_participating_file_fraction": 0.80,
                           "minimum_resolved_edges": 20},
        },
        "eligibility": {
            "a0": "all-source-bytes-no-comment-or-blank-filter",
            "a1_min_nonwhitespace_bytes_in_primary_horizon": 256,
            "a1_min_noncomment_bytes_in_primary_horizon": 128,
            "a1_context_regimes": ["with-file", "cross-file-only"],
            "cross_file_policy": "skip-target-file-and-backfill-to-exact-c",
            "near_duplicate": {
                "records": "language-lexical-records-layout-excluded",
                "gram_n": 5, "minimum_lexical_records": 20,
                "jaccard_threshold_rational": [7, 10],
                "scope": "union-of-all-headline-contexts-all-orderings-rungs",
            },
            "independence_graph": {
                "edge_if_shared_unique_fivegram_fraction_at_least": 0.05,
                "edge_if_git_history_overlaps": True,
                "minimum_components_for_language_general_claim": 3,
            },
        },
        "context": {
            "grid_bytes": grid,
            "axes": ["q_stream", "q_source", "c"],
            "tokenizer": {"add_special_tokens": False,
                          "prepend_bos": False, "append_eos": False,
                          "normalization": "pinned-tokenizer-defaults-only"},
            "primary_model_context_policy": "pinned-config-no-runtime-extension",
            "extended_context_policy": (
                "separate-descriptive-adapter-only-never-substituted-for-native"),
            "far_context_probe": {
                "required_per_model_rung": True,
                "minimum_loss_change_fraction": 0.01,
                "probe_seed_u64": _seed_u64("far-context-probe"),
            },
            "minimum_contiguous_decades": 2.0,
            "floor_rung_bytes": 512,
            "headline_requires_decades_without_floor": 2.0,
            "headline_max_rung_over_median_exhaustion": 10.0,
            "headline_max_validated_rung_bytes": 256 * 1024,
            "diagnostic_rungs_excluded_from_gates": [512 * 1024,
                                                       1024 * 1024],
            "bin_floor_units": 20,
            "bin_floor_files": 10,
            "cell_floor_units": 100,
            "cell_floor_files": 30,
        },
        "analysis": {
            "denominator_primary": "nll_nats/(ln(2)*covered_source_bytes)",
            "denominator_sensitivity": "nll_nats/(ln(2)*covered_codepoints)",
            "primary_score_horizon_bytes": 512,
            "full_block_secondary": "descriptive-never-fitted-c-at-least-16384",
            "functional_forms": ["A*x^(-beta)+Linf",
                                 "A*exp(-x/tau)+Linf", "a-b*ln(x)"],
            "fit_holdout": {
                "fit_rungs_bytes": [512, 1024, 2048, 4096, 8192, 16384,
                                    32768],
                "holdout_rungs_bytes": [65536, 131072, 262144],
                "diagnostic_only_rungs_bytes": [524288, 1048576],
                "range_applies_to": "validated-fit-plus-holdout-support",
                "minimum_fit_rungs": 5, "minimum_holdout_rungs": 2,
                "powerlaw_max_mean_relative_holdout_error": 0.05,
                "powerlaw_must_not_lose_to_either_alternative": True,
            },
            "crossover": {
                "primary_curve": "nonparametric-common-rung-language-difference",
                "interpolation_axis": "linear-in-log2-context-between-adjacent-rungs",
                "minimum_draw_fraction_with_exactly_one_crossing": 0.95,
                "unstable_output": "no-stable-unique-crossover-within-measured-support",
                "parametric_crossing": "sensitivity-only-no-extrapolation",
            },
            "likelihood": "StudentT(nu=4,mu=nonlinear_cell_mean,sigma_cell)",
            "priors": {
                "intercept": "Normal(1.5,1.5)",
                "log_A": "Normal(0,1.5)",
                "log_beta": "Normal(log(0.1),1)",
                "Linf": "HalfNormal(2)",
                "fixed_slope_contrasts": "Normal(0,0.2)",
                "repo_intercept_sd": "HalfNormal(0.5)",
                "family_slope_sd": "HalfNormal(0.05)",
                "residual_sd": "HalfNormal(1)",
            },
            "sampler": {"algorithm": "NUTS", "chains": 4,
                        "warmup_per_chain": 1000,
                        "draws_per_chain": 2000,
                        "target_accept": 0.90, "max_treedepth": 12,
                        "seed_u64": _seed_u64("bayes-sampler")},
            "convergence": {"rhat_max": 1.01, "bulk_ess_min": 400,
                            "tail_ess_min": 400, "max_divergences": 0},
            "bootstrap": {"replicates": 2000,
                          "unit_seed_u64": _seed_u64("bootstrap-unit"),
                          "repo_seed_u64": _seed_u64("bootstrap-repo")},
            "rope_beta": 0.02,
            "compatibility_decisions": ["compatible", "outside-rope",
                                        "indeterminate"],
            "multiplicity": (
                "primary-checkpoint-family-language contrasts report joint-"
                "posterior 95pct intervals; secondary p-values use Holm"),
        },
        "power": {
            "artifact_schema": "v2c_direct_scaling_power_v1",
            "simulation_seed_u64": _seed_u64("power-simulation"),
            "replicates": 5000,
            "minimum_correct_classification_probability": 0.80,
            "primary_assumptions": {
                "effective_repositories": 4,
                "paired_units_per_repository": 200,
                "score_horizon_bytes": 512,
                "unit_slope_sd": 0.08,
                "repository_slope_sd": 0.005,
                "variance_status": (
                    "assumption-only-at-512-byte-score-horizon;must-be-"
                    "checked-by-frozen-disjoint-calibration-before-language-"
                    "general-scoring"),
                "interval": "two-sided-t-by-repository-df=3",
            },
            "scope": {
                "minimum_effective_repositories": 3,
                "below_minimum_action": (
                    "no-clustered-language-general-power-decision;"
                    "repository-specific-description-only"),
            },
            "scenarios": [
                {"name": "compatible", "true_delta_beta": 0.0,
                 "role": "adequacy-gate",
                 "correct": "ci-entirely-inside-rope"},
                {"name": "outside-positive", "true_delta_beta": 0.04,
                 "role": "adequacy-gate",
                 "correct": "ci-entirely-above-rope"},
                {"name": "boundary", "true_delta_beta": 0.02,
                 "role": "coverage-diagnostic-only",
                 "correct": "ci-covers-true-positive-rope-boundary"},
            ],
            "sensitivity_grid": {
                "unit_slope_sd": [0.08, 0.12],
                "repo_slope_sd": [0.005, 0.01, 0.02, 0.03],
                "effective_repositories": [3, 4, 9],
            },
            "adequacy": {
                "gating_scenarios": ["compatible", "outside-positive"],
                "boundary_scenario_role": (
                    "nominal-interval-coverage-diagnostic-only"),
                "sensitivity_role": (
                    "report-largest-contiguous-repository-slope-sd-"
                    "supported-at-each-repository-count-and-unit-sd;not-an-"
                    "all-grid-conjunction"),
                "calibration_artifact_schema": (
                    "v2c_direct_scaling_variance_calibration_v1"),
                "calibration_population": (
                    "frozen-disjoint-calibration-units-never-used-in-primary-"
                    "or-holdout-fits"),
                "calibration_disclosure": (
                    "repository-slope-variance-and-unit-slope-variance-only;"
                    "no-means-no-condition-contrasts-no-crossover"),
                "calibration_timing": (
                    "sealed-before-any-primary-or-holdout-loss-scoring"),
                "authorization_rule": (
                    "language-general-scoring-only-if-calibrated-unit-and-"
                    "repository-slope-sd-fall-within-the-simulated-adequacy-"
                    "boundary-at-that-language-effective-repository-count;"
                    "otherwise-repository-specific-description-only"),
            },
            "failure_action": (
                "increase-independent-repositories-units-or-score-horizon;"
                "otherwise-repository-specific-only;never-widen-rope"),
        },
        "generator": generator,
    }
    protocol["protocol_binding"] = sha256_sorted_json(protocol)
    validate_protocol(protocol)
    return protocol


def validate_protocol(protocol: dict) -> None:
    top = {"schema", "protocol_state", "study_status", "frozen_at_utc_date",
           "design", "input_ledgers", "panel", "sampling", "stream",
           "eligibility", "context", "analysis", "power", "generator",
           "protocol_binding"}
    _exact_keys(protocol, top, "direct-scaling protocol")
    if protocol["schema"] != PROTOCOL_SCHEMA \
            or protocol["protocol_state"] != "frozen-before-loss":
        raise V2BError("direct-scaling protocol is not a frozen v1 packet")
    preimage = {k: v for k, v in protocol.items() if k != "protocol_binding"}
    if protocol["protocol_binding"] != sha256_sorted_json(preimage):
        raise V2BError("direct-scaling protocol binding mismatch")
    grid = protocol["context"].get("grid_bytes")
    if grid != [512 * (2 ** i) for i in range(12)]:
        raise V2BError("direct-scaling context grid drift")
    if protocol["panel"].get("primary_models") != list(PRIMARY_MODELS):
        raise V2BError("direct-scaling primary model ladder drift")
    repos = protocol["panel"].get("repositories")
    if not isinstance(repos, list) or len(repos) != 14:
        raise V2BError("direct-scaling protocol must bind 14 repositories")
    models = protocol["panel"].get("models")
    if not isinstance(models, list) or len(models) != 17:
        raise V2BError("direct-scaling protocol must bind 17 model checkpoints")
    if protocol["analysis"].get("rope_beta") != 0.02:
        raise V2BError("direct-scaling compatibility ROPE drift")
    minimum = protocol["power"].get(
        "minimum_correct_classification_probability")
    if not isinstance(minimum, (int, float)) or not math.isclose(minimum, .8):
        raise V2BError("direct-scaling power threshold drift")
    power = protocol["power"]
    primary = power.get("primary_assumptions")
    if not isinstance(primary, dict) \
            or primary.get("score_horizon_bytes") != 512 \
            or primary.get("variance_status") != (
                "assumption-only-at-512-byte-score-horizon;must-be-"
                "checked-by-frozen-disjoint-calibration-before-language-"
                "general-scoring"):
        raise V2BError("direct-scaling variance assumption drift")
    scenarios = power.get("scenarios")
    if not isinstance(scenarios, list) \
            or [row.get("name") for row in scenarios] != [
                "compatible", "outside-positive", "boundary"] \
            or [row.get("role") for row in scenarios] != [
                "adequacy-gate", "adequacy-gate",
                "coverage-diagnostic-only"]:
        raise V2BError("direct-scaling power scenario-role drift")
    adequacy = power.get("adequacy")
    if not isinstance(adequacy, dict) \
            or adequacy.get("gating_scenarios") != [
                "compatible", "outside-positive"] \
            or adequacy.get("calibration_timing") != (
                "sealed-before-any-primary-or-holdout-loss-scoring"):
        raise V2BError("direct-scaling adequacy/calibration drift")
    sensitivity = power.get("sensitivity_grid")
    if not isinstance(sensitivity, dict) or set(sensitivity) != {
            "unit_slope_sd", "repo_slope_sd", "effective_repositories"}:
        raise V2BError("direct-scaling sensitivity grid shape drift")
    for key in ("unit_slope_sd", "repo_slope_sd",
                "effective_repositories"):
        values = sensitivity[key]
        if not isinstance(values, list) or not values \
                or values != sorted(values) \
                or len(values) != len(set(values)) \
                or any(not isinstance(value, (int, float)) or value <= 0
                       for value in values):
            raise V2BError(
                f"direct-scaling {key} grid must be strictly ascending")
    for primary_key, grid_key in (
            ("unit_slope_sd", "unit_slope_sd"),
            ("repository_slope_sd", "repo_slope_sd"),
            ("effective_repositories", "effective_repositories")):
        if primary[primary_key] not in sensitivity[grid_key]:
            raise V2BError(
                f"direct-scaling primary {primary_key} absent from grid")


def load_protocol(path: str | Path) -> tuple[dict, str]:
    protocol, digest = load_json(str(path), PROTOCOL_SCHEMA)
    validate_protocol(protocol)
    return protocol, digest
