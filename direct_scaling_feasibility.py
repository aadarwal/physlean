#!/usr/bin/env python3
"""Produce and validate the model-free V2-c direct-scaling census.

This program deliberately has no defaults for scientific constants.  A run
must be bound to a complete, frozen ``v2c_direct_scaling_protocol_v1`` JSON
artifact.  It reads source bytes from exact commits in ``corpora_lock.json``;
it never imports a tokenizer, model package, or outcome artifact.

The implementation covers P1a Arm-A structural feasibility.  Arm B is emitted
as an explicit fail-closed unsupported phase.  The validator recomputes every
stored decision from the raw occupancy summaries and can optionally reproduce
the complete artifact from the locked source repositories.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import datetime as dt
import hashlib
import heapq
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, load_json, sha256_file,
                        sha256_sorted_json, write_new_json)


SCHEMA = "v2c_direct_scaling_feasibility_v1"
PROTOCOL_SCHEMA = "v2c_direct_scaling_protocol_v1"
PRODUCTION_PROTOCOL_SHA256 = (
    "b32f1ebb7de3e18230cd8f0c28633871e9543408788d07acf7cc2c916d160291")
PROGRAM = "direct_scaling_feasibility.py"
ORDERINGS = ("shuffled", "topological", "reverse-topological")
AXES = ("q_stream", "q_source")
REGIMES = ("with-file", "cross-file-only")
HEX_RE = re.compile(r"[0-9a-f]+")
ARM_B_UNSUPPORTED_REASON = (
    "event-level Arm-B construction is not implemented by this bounded P1a "
    "producer; all K5/K6 decisions are forced false")


class CensusError(V2BError):
    """The structural census input or artifact violates its frozen contract."""


def _fail(message: str) -> None:
    raise CensusError(message)


def _is_int(value: Any, minimum: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and (minimum is None or value >= minimum))


def _is_number(value: Any, minimum: float | None = None,
               maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    value = float(value)
    return (math.isfinite(value)
            and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum))


def _exact_keys(value: Any, keys: Iterable[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != set(keys):
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        _fail(f"{label}: keys {got!r} != {sorted(keys)!r}")


def _required_keys(value: Any, keys: Iterable[str], label: str) -> None:
    required = set(keys)
    if not isinstance(value, dict):
        _fail(f"{label}: expected object, got {type(value).__name__}")
    missing = required - set(value)
    if missing:
        _fail(f"{label}: missing required keys {sorted(missing)!r}")


def _valid_sha(value: Any, lengths: tuple[int, ...] = (40, 64)) -> bool:
    return (isinstance(value, str) and len(value) in lengths
            and HEX_RE.fullmatch(value) is not None)


def _date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail(f"{label}: date must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as err:
        raise CensusError(f"{label}: invalid date {value!r}") from err
    if parsed.isoformat() != value:
        _fail(f"{label}: non-canonical date {value!r}")
    return value


def protocol_projection(protocol: dict[str, Any]) -> dict[str, Any]:
    """Exact P0 protocol-binding preimage (stored decisions are not inputs)."""
    return {key: value for key, value in protocol.items()
            if key != "protocol_binding"}


def validate_protocol(protocol: dict[str, Any], *, protocol_sha256: str,
                      corpora_lock_sha256: str,
                      allow_synthetic: bool = False) -> dict[str, Any]:
    """Validate and project the exact frozen P0 v1 packet for P1a.

    P0's power *constants* are bound as part of ``protocol_binding``.  A power
    result/decision is intentionally neither present nor required here: the
    outcome-free CPU census may run even when the separate power artifact
    blocks loss scoring.
    """
    top = {
        "schema", "protocol_state", "study_status", "frozen_at_utc_date",
        "design", "input_ledgers", "panel", "sampling", "stream",
        "eligibility", "context", "analysis", "power", "generator",
        "protocol_binding",
    }
    _exact_keys(protocol, top, "protocol")
    if protocol["schema"] != PROTOCOL_SCHEMA:
        _fail(f"protocol schema {protocol['schema']!r} != {PROTOCOL_SCHEMA!r}")
    allowed_states = {"frozen-before-loss"}
    if allow_synthetic:
        allowed_states.add("synthetic-fixture")
    if protocol["protocol_state"] not in allowed_states:
        _fail("protocol is not frozen before loss")
    if protocol["protocol_binding"] != sha256_sorted_json(
            protocol_projection(protocol)):
        _fail("protocol_binding does not recompute")
    if not _valid_sha(protocol["protocol_binding"], (64,)):
        _fail("invalid protocol_binding")
    if not _valid_sha(protocol_sha256, (64,)):
        _fail("invalid exact protocol file SHA256")
    if not allow_synthetic and protocol_sha256 != PRODUCTION_PROTOCOL_SHA256:
        _fail("protocol raw SHA256 is not the authoritative P0 reseal")
    ledgers = protocol["input_ledgers"]
    _required_keys(ledgers, {"corpora_lock"}, "protocol.input_ledgers")
    _required_keys(ledgers["corpora_lock"], {"path", "sha256"},
                   "protocol.input_ledgers.corpora_lock")
    if ledgers["corpora_lock"]["sha256"] != corpora_lock_sha256:
        _fail("protocol does not bind the exact corpora lock file")
    generator = protocol["generator"]
    _required_keys(generator, {"source_commit", "source_tree_hash", "program"},
                   "protocol.generator")
    if (not _valid_sha(generator["source_commit"])
            or not _valid_sha(generator["source_tree_hash"], (64,))
            or not isinstance(generator["program"], str)
            or not generator["program"]):
        _fail("protocol generator is invalid")

    sampling = protocol["sampling"]
    stream = protocol["stream"]
    eligibility = protocol["eligibility"]
    context = protocol["context"]
    _exact_keys(sampling, {
        "seed_family",
        "a0_seed_sha256", "a1_seed_sha256", "planned_per_repo",
        "target_block_bytes", "minimum_realized_target_bytes",
        "primary_score_horizon_source_bytes", "seed_u64_rule",
        "systematic_index_formula", "a0_origin_rule",
        "a1_coordinate_rule", "alignment", "overlap_policy", "identity_reuse",
    }, "protocol.sampling")
    _required_keys(stream, {"tracked_files_only", "source_suffixes",
                            "path_exclusions", "metadata_header",
                            "metadata_visible_not_scored", "utf8_policy",
                            "orderings", "graph_gate"}, "protocol.stream")
    _required_keys(eligibility, {"a0", "a1_context_regimes",
                                 "cross_file_policy",
                                 "a1_min_nonwhitespace_bytes_in_primary_horizon",
                                 "a1_min_noncomment_bytes_in_primary_horizon",
                                 "near_duplicate", "independence_graph"},
                   "protocol.eligibility")
    _required_keys(context, {"grid_bytes", "minimum_contiguous_decades",
                             "floor_rung_bytes",
                             "headline_requires_decades_without_floor",
                             "headline_max_rung_over_median_exhaustion",
                             "headline_max_validated_rung_bytes",
                             "diagnostic_rungs_excluded_from_gates",
                             "bin_floor_units", "bin_floor_files",
                             "cell_floor_units", "cell_floor_files"},
                   "protocol.context")
    grid = context["grid_bytes"]
    if (not isinstance(grid, list) or len(grid) < 3
            or any(not _is_int(x, 1) for x in grid)
            or grid != sorted(set(grid))):
        _fail("grid_bytes must be a strictly increasing integer list")
    if context["floor_rung_bytes"] != grid[0]:
        _fail("frozen floor rung is not the first grid rung")
    diagnostics = context["diagnostic_rungs_excluded_from_gates"]
    if (not isinstance(diagnostics, list)
            or diagnostics != sorted(set(diagnostics))
            or any(rung not in grid for rung in diagnostics)
            or context["headline_max_validated_rung_bytes"] not in grid
            or any(rung <= context["headline_max_validated_rung_bytes"]
                   for rung in diagnostics)):
        _fail("invalid diagnostic/headline rung partition")
    if (not _is_int(sampling["target_block_bytes"], 1)
            or not _is_int(sampling["minimum_realized_target_bytes"], 1)
            or sampling["minimum_realized_target_bytes"]
            > sampling["target_block_bytes"]):
        _fail("invalid target block/minimum bytes")
    if (not _is_int(sampling["planned_per_repo"], 1)
            or not _is_int(sampling["primary_score_horizon_source_bytes"], 1)
            or sampling["primary_score_horizon_source_bytes"]
            > sampling["minimum_realized_target_bytes"]):
        _fail("invalid planned_per_repo")
    if any(not _valid_sha(sampling[key], (64,))
           for key in ("a0_seed_sha256", "a1_seed_sha256")):
        _fail("invalid A0/A1 systematic seed")
    if sampling["seed_family"] != "v2c-direct-scaling-p0-20260809":
        _fail("unsupported sampling seed family")
    seed_rule = sampling["seed_u64_rule"]
    _exact_keys(seed_rule, {"preimage", "canonical_json", "digest_decode",
                            "arm_enum"}, "protocol.sampling.seed_u64_rule")
    if seed_rule != {
            "preimage": ["v2c-systematic-offset-v1", "$seed_sha256",
                         "$repo", "$arm"],
            "canonical_json": (
                "json.dumps(value,sort_keys=true,separators=[comma,colon],"
                "ensure_ascii=true) encoded UTF-8"),
            "digest_decode": (
                "unsigned-big-endian-first-8-bytes-of-SHA256"),
            "arm_enum": ["a0", "a1"]}:
        _fail("unsupported systematic seed-u64 rule")
    if sampling["systematic_index_formula"] != (
            "i_j=floor(P*(u+j*2^64)/(n*2^64));"
            "j=0,...,n-1;n=min(planned_per_repo,P)"):
        _fail("unsupported systematic index formula")
    if sampling["a0_origin_rule"] != {
            "population": "lexicographically-sorted-eligible-file-identities",
            "population_size_symbol": "P=N_eligible_files",
            "raw_index": "systematic_index_formula",
            "anchor": "selected-file-exact-metadata-header-start",
            "deduplicate_or_top_up": False}:
        _fail("unsupported A0 origin rule")
    if sampling["a1_coordinate_rule"] != {
            "axis": "concatenated-file-body-bytes-headers-excluded",
            "slot_population": "P=floor(axis_bytes/target_block_bytes)",
            "raw_index": "systematic_index_formula",
            "raw_coordinate": "target_block_bytes*i_j",
            "processing_order": "ascending-raw-coordinate",
            "post_mapping": (
                "map-to-containing-file-then-line-align;reject-cross-file-"
                "header-short-overlap-comment-blank-nearduplicate;never-top-up")
            }:
        _fail("unsupported A1 coordinate rule")
    if (sampling["alignment"]
            != "next-utf8-line-boundary-at-or-after-coordinate"
            or sampling["overlap_policy"]
            != "reject-pairwise-overlap-never-resample"
            or sampling["identity_reuse"]
            != "same-origins-targets-all-orderings-models-rungs"):
        _fail("unsupported sampling alignment/overlap/identity rule")
    if (stream["tracked_files_only"] is not True
            or stream["metadata_visible_not_scored"] is not True
            or stream["utf8_policy"] != "strict-no-replacement"
            or not isinstance(stream["path_exclusions"], list)
            or any(not isinstance(path, str) or not path
                   for path in stream["path_exclusions"])):
        _fail("unsupported source stream/UTF-8 policy")
    if (eligibility["a0"] != "all-source-bytes-no-comment-or-blank-filter"
            or eligibility["a1_context_regimes"]
            != ["with-file", "cross-file-only"]
            or eligibility["cross_file_policy"]
            != "skip-target-file-and-backfill-to-exact-c"):
        _fail("unsupported A0/A1 eligibility or context regime")
    if (not isinstance(stream["metadata_header"], str)
            or "{compact_sorted_json(repo,path,source_sha256,source_bytes)}"
            not in stream["metadata_header"]):
        _fail("unsupported metadata header contract")
    gg = stream["graph_gate"]
    _required_keys(gg, {"minimum_resolved_reference_fraction",
                        "minimum_participating_file_fraction",
                        "minimum_resolved_edges"}, "protocol.stream.graph_gate")
    if (not _is_number(gg["minimum_resolved_reference_fraction"], 0, 1)
            or not _is_int(gg["minimum_resolved_edges"], 0)
            or not _is_number(gg["minimum_participating_file_fraction"], 0, 1)):
        _fail("invalid graph gate")
    floors = {"bin_units": context["bin_floor_units"],
              "bin_files": context["bin_floor_files"],
              "cell_units": context["cell_floor_units"],
              "cell_files": context["cell_floor_files"]}
    if any(not _is_int(value, 1) for value in floors.values()):
        _fail("all unit/file floors must be positive integers")
    if floors["cell_units"] < floors["bin_units"] \
            or floors["cell_files"] < floors["bin_files"]:
        _fail("cell floors cannot be below bin floors")
    rr = {"min_contiguous_decades": context["minimum_contiguous_decades"],
          "floor_rung_bytes": context["floor_rung_bytes"],
          "min_decades_without_floor":
              context["headline_requires_decades_without_floor"],
          "exhaustion_multiplier":
              context["headline_max_rung_over_median_exhaustion"],
          "headline_max_validated_rung_bytes":
              context["headline_max_validated_rung_bytes"]}
    if (not _is_number(rr["min_contiguous_decades"], 0)
            or not _is_number(rr["min_decades_without_floor"], 0)
            or not _is_number(rr["exhaustion_multiplier"], 1)
            or not _is_int(rr["headline_max_validated_rung_bytes"], 1)):
        _fail("invalid range rules")
    if stream["orderings"] != ["seeded-shuffled",
                               "build-resolved-topological",
                               "reverse-topological"]:
        _fail("frozen ordering list drift")

    near = eligibility["near_duplicate"]
    _required_keys(near, {"records", "gram_n", "minimum_lexical_records",
                          "jaccard_threshold_rational", "scope"},
                   "protocol.eligibility.near_duplicate")
    rational = near["jaccard_threshold_rational"]
    if (not isinstance(rational, list) or len(rational) != 2
            or any(not _is_int(x, 1) for x in rational)
            or rational[0] > rational[1]
            or not _is_int(near["gram_n"], 1)
            or not _is_int(near["minimum_lexical_records"], 1)):
        _fail("invalid near-duplicate contract")
    if (near["records"] != "language-lexical-records-layout-excluded"
            or near["scope"]
            != "union-of-all-headline-contexts-all-orderings-rungs"):
        _fail("unsupported near-duplicate record/scope contract")
    dependence = eligibility["independence_graph"]
    _required_keys(dependence, {
        "edge_if_shared_unique_fivegram_fraction_at_least",
        "edge_if_git_history_overlaps",
        "minimum_components_for_language_general_claim"},
        "protocol.eligibility.independence_graph")
    if (not _is_number(dependence[
            "edge_if_shared_unique_fivegram_fraction_at_least"], 0, 1)
            or dependence["edge_if_git_history_overlaps"] is not True
            or not _is_int(dependence[
                "minimum_components_for_language_general_claim"], 1)):
        _fail("invalid repository-independence contract")

    source_suffixes = stream["source_suffixes"]
    if not isinstance(source_suffixes, dict):
        _fail("stream.source_suffixes must be an object")
    repo_rows = protocol["panel"].get("repositories")
    if not isinstance(repo_rows, list) or not repo_rows:
        _fail("protocol repositories must be a non-empty list")
    repositories = []
    for row in repo_rows:
        _exact_keys(row, {"language", "repo", "url", "revision"},
                    "protocol.panel.repositories row")
        language, repo = row["language"], row["repo"]
        suffixes = source_suffixes.get(language)
        if (not isinstance(suffixes, list) or not suffixes
                or suffixes != sorted(set(suffixes))
                or any(not isinstance(x, str) or not x for x in suffixes)
                or language not in {"lean", "python", "cpp"}
                or not isinstance(row["url"], str) or not row["url"]
                or not _valid_sha(row["revision"])):
            _fail(f"{language}: invalid frozen source suffixes")
        repositories.append({
            "repo": repo, "lock_key": repo, "language": language,
            "source_roots": [""], "extensions": suffixes,
            "exclude_paths": list(stream["path_exclusions"]),
            "package_prefixes": [],
            "graph": {"resolver": (
                "python-ast-v1" if language == "python" else
                "lean-import-proposal-fail-closed-v1" if language == "lean"
                else "cpp-unavailable-fail-closed-v1")},
            "protocol_revision": row["revision"],
            "protocol_url": row["url"],
        })
    repo_keys = [row["repo"] for row in repositories]
    if (any(not isinstance(repo, str) or not repo for repo in repo_keys)
            or len(repo_keys) != len(set(repo_keys))):
        _fail("protocol repository names must be non-empty and unique")

    models = protocol["panel"].get("models")
    if not isinstance(models, list) or not models:
        _fail("protocol checkpoints must be non-empty")
    checkpoint_keys = []
    checkpoints = []
    for i, model in enumerate(models):
        _required_keys(model, {"model_id", "revision", "release_timestamp"},
                       f"protocol.panel.models[{i}]")
        if (not isinstance(model["model_id"], str) or not model["model_id"]
                or not _valid_sha(model["revision"])):
            _fail(f"protocol.panel.models[{i}] invalid")
        cutoff = str(model["release_timestamp"])[:10]
        _date(cutoff, f"protocol.panel.models[{i}].release_timestamp")
        checkpoints.append({
            "model_id": model["model_id"], "revision": model["revision"],
            "cutoff_date": cutoff,
            "optimistic_context_bytes":
                context["headline_max_validated_rung_bytes"],
        })
        checkpoint_keys.append(f"{model['model_id']}@{model['revision']}")
    if checkpoint_keys != sorted(set(checkpoint_keys)):
        _fail("protocol checkpoints must be sorted by unique model@revision")

    constants = {
        "grid_bytes": grid,
        "headline_grid_bytes": [rung for rung in grid
                                if rung <= rr["headline_max_validated_rung_bytes"]
                                and rung not in diagnostics],
        "target_block_bytes": sampling["target_block_bytes"],
        "target_min_bytes": sampling["minimum_realized_target_bytes"],
        "primary_horizon_bytes":
            sampling["primary_score_horizon_source_bytes"],
        "planned_a0_origins_per_repo": sampling["planned_per_repo"],
        "planned_a1_targets_per_repo": sampling["planned_per_repo"],
        "a0_seed": sampling["a0_seed_sha256"],
        "a1_seed": sampling["a1_seed_sha256"],
        "systematic_index_formula": sampling["systematic_index_formula"],
        "metadata_header": stream["metadata_header"],
        "a1_min_nonwhitespace_bytes":
            eligibility["a1_min_nonwhitespace_bytes_in_primary_horizon"],
        "a1_min_noncomment_bytes":
            eligibility["a1_min_noncomment_bytes_in_primary_horizon"],
        "near_duplicate": {
            "gram_n": near["gram_n"],
            "minimum_lexical_records": near["minimum_lexical_records"],
            "match_fraction": rational[0] / rational[1],
        },
        "graph_gate": {
            "min_resolution_fraction":
                gg["minimum_resolved_reference_fraction"],
            "min_edges": gg["minimum_resolved_edges"],
            "min_participating_file_fraction":
                gg["minimum_participating_file_fraction"],
            "require_acyclic": True,
        },
        "floors": floors,
        "range": rr,
        "orderings": list(ORDERINGS),
        "repository_dependence": {
            "gram_n": near["gram_n"],
            "min_shared_fraction": dependence[
                "edge_if_shared_unique_fivegram_fraction_at_least"],
            "git_history_overlap": dependence["edge_if_git_history_overlaps"],
            "minimum_components": dependence[
                "minimum_components_for_language_general_claim"],
        },
    }
    if not constants["headline_grid_bytes"]:
        _fail("protocol leaves no headline-eligible structural rungs")
    return {
        "constants": constants, "repositories": repositories,
        "checkpoints": checkpoints,
        "frozen_constants_sha256": protocol["protocol_binding"],
        "power_artifact_required_for_scoring": True,
    }


@dataclass(frozen=True)
class SourceFile:
    repo: str
    language: str
    rel: str
    blob_oid: str
    data: bytes
    text: str
    source_sha256: str
    first_add_date: str | None

    @property
    def nbytes(self) -> int:
        return len(self.data)

    @property
    def codepoints(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class Layout:
    rel: str
    header_start: int
    body_start: int
    body_end: int
    end: int


@dataclass
class RepoInventory:
    spec: dict[str, Any]
    locked_sha: str
    tree_oid: str
    files: list[SourceFile]
    skipped_non_utf8: int
    history_commits: frozenset[str]
    graph: dict[str, Any] | None = None
    orders: dict[str, list[str]] | None = None
    layouts: dict[str, list[Layout]] | None = None
    targets: list[dict[str, Any]] | None = None


def _git(repo: str, args: list[str], *, text: bool = True,
         timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["git", "-C", repo, *args],
                                capture_output=True, text=text,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise CensusError(f"git {' '.join(args)} failed in {repo}: {err}") \
            from err
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        _fail(f"git {' '.join(args)} failed in {repo}: {stderr[:300]}")
    return result


def _path_selected(path: str, spec: dict[str, Any]) -> bool:
    if not any(root == "" or path == root
               or path.startswith(root.rstrip("/") + "/")
               for root in spec["source_roots"]):
        return False
    if not any(path.endswith(ext) for ext in spec["extensions"]):
        return False
    return not any(path == excluded
                   or path.startswith(excluded.rstrip("/") + "/")
                   for excluded in spec["exclude_paths"])


def _first_add_dates(repo_dir: str) -> dict[str, str]:
    """Conservative rename-aware first-public dates from one Git history pass."""
    result = _git(repo_dir, ["log", "-M", "--diff-filter=AR",
                             "--name-status", "--reverse", "--date-order",
                             "--format=\x01%aI\x02%cI"])
    dates: dict[str, str] = {}
    author = committer = None
    for line in result.stdout.splitlines():
        if line.startswith("\x01"):
            author, committer = line[1:].split("\x02", 1)
            continue
        if not line or "\t" not in line or not author or not committer:
            continue
        parts = line.split("\t")
        stamp = min(author, committer)[:10]
        if parts[0] == "A" and len(parts) == 2:
            dates.setdefault(parts[1], stamp)
        elif parts[0].startswith("R") and len(parts) == 3:
            old, new = parts[1], parts[2]
            dates[new] = dates.pop(old, dates.get(new, stamp))
    return dates


def load_inventory(corpora_root: str, spec: dict[str, Any],
                   lock_repos: dict[str, Any]) -> RepoInventory:
    lock_key = spec["lock_key"]
    if lock_key not in lock_repos:
        _fail(f"protocol repo {spec['repo']} lock_key {lock_key!r} is absent")
    locked_sha = lock_repos[lock_key].get("sha")
    locked_url = lock_repos[lock_key].get("url")
    if not _valid_sha(locked_sha):
        _fail(f"corpora lock SHA invalid for {lock_key}")
    if spec["protocol_revision"] != locked_sha:
        _fail(f"{lock_key}: P0 revision does not match the corpus lock")
    if spec["protocol_url"] != locked_url:
        _fail(f"{lock_key}: P0 URL does not match the corpus lock")
    repo_dir = os.path.join(corpora_root, lock_key)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        _fail(f"missing full-history checkout {repo_dir}")
    head = _git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()
    if head != locked_sha:
        _fail(f"{lock_key}: HEAD {head} != locked {locked_sha}")
    dirty = _git(repo_dir, ["status", "--porcelain", "--untracked-files=all"]) \
        .stdout.strip()
    if dirty:
        _fail(f"{lock_key}: checkout has tracked or untracked drift")
    tree_oid = _git(repo_dir, ["rev-parse", f"{locked_sha}^{{tree}}"]) \
        .stdout.strip()
    raw = _git(repo_dir, ["ls-tree", "-r", "-z", locked_sha], text=False).stdout
    dates = _first_add_dates(repo_dir)
    selected: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path_b = record.split(b"\t", 1)
        mode, kind, oid_b = meta.split(b" ", 2)
        path = path_b.decode("utf-8")
        if kind == b"blob" and mode != b"120000" \
                and _path_selected(path, spec):
            selected.append((path, oid_b.decode("ascii")))
    # One cat-file process avoids tens of thousands of `git show` process
    # launches on mathlib-sized repositories while still reading exact blob
    # objects from the locked commit.
    request = "".join(f"{oid}\n" for _, oid in selected).encode("ascii")
    try:
        batch = subprocess.run(["git", "-C", repo_dir, "cat-file", "--batch"],
                               input=request, capture_output=True)
    except OSError as err:
        raise CensusError(f"{spec['repo']}: git cat-file failed: {err}") \
            from err
    if batch.returncode != 0:
        _fail(f"{spec['repo']}: git cat-file failed: "
              f"{batch.stderr.decode(errors='replace')[:300]}")
    cursor = 0
    blobs: list[bytes] = []
    for path, expected_oid in selected:
        newline = batch.stdout.find(b"\n", cursor)
        if newline < 0:
            _fail(f"{spec['repo']}: truncated cat-file header for {path}")
        header = batch.stdout[cursor:newline].split()
        if len(header) != 3 or header[0].decode() != expected_oid \
                or header[1] != b"blob":
            _fail(f"{spec['repo']}: invalid cat-file header for {path}")
        size = int(header[2])
        cursor = newline + 1
        blob = batch.stdout[cursor:cursor + size]
        cursor += size
        if len(blob) != size or batch.stdout[cursor:cursor + 1] != b"\n":
            _fail(f"{spec['repo']}: truncated cat-file blob for {path}")
        cursor += 1
        blobs.append(blob)
    if cursor != len(batch.stdout):
        _fail(f"{spec['repo']}: trailing cat-file output")

    files: list[SourceFile] = []
    skipped = 0
    for (path, oid), blob in zip(selected, blobs):
        try:
            text_value = blob.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        files.append(SourceFile(
            repo=spec["repo"], language=spec["language"], rel=path,
            blob_oid=oid, data=blob, text=text_value,
            source_sha256=hashlib.sha256(blob).hexdigest(),
            first_add_date=dates.get(path)))
    files.sort(key=lambda item: item.rel)
    if not files:
        _fail(f"{spec['repo']}: frozen source selection is empty")
    history = frozenset(_git(repo_dir, ["rev-list", locked_sha])
                        .stdout.splitlines())
    if not history or locked_sha not in history:
        _fail(f"{spec['repo']}: locked history traversal is incomplete")
    return RepoInventory(spec=spec, locked_sha=locked_sha,
                         tree_oid=tree_oid, files=files,
                         skipped_non_utf8=skipped,
                         history_commits=history)


def _strip_comments_for_imports(text: str, language: str) -> str:
    """Remove strings/comments only to propose direct imports.

    Python uses ``ast`` below.  Lean proposals are accepted as native only
    when the exact Lean CLI dependency check succeeds for every file.  C++
    proposals are resolved through the frozen compile-command include paths.
    """
    if language == "lean":
        out, depth, i = [], 0, 0
        while i < len(text):
            if text.startswith("/-", i):
                depth += 1
                out.extend("  ")
                i += 2
            elif depth and text.startswith("-/", i):
                depth -= 1
                out.extend("  ")
                i += 2
            elif depth:
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            elif text.startswith("--", i):
                j = text.find("\n", i)
                if j < 0:
                    out.extend(" " * (len(text) - i))
                    break
                out.extend(" " * (j - i))
                i = j
            else:
                out.append(text[i])
                i += 1
        return "".join(out)
    # Sufficient for preprocessor include proposals; compile-command paths do
    # the scientific resolution work.
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"),
                  text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _module_map(files: list[SourceFile], spec: dict[str, Any]) \
        -> dict[str, str]:
    mapping: dict[str, str] = {}
    if spec["language"] == "python":
        package_dirs = {
            file.rel.rsplit("/", 1)[0] if "/" in file.rel else ""
            for file in files if file.rel.endswith("/__init__.py")
            or file.rel == "__init__.py"}
        for file in files:
            if not file.rel.endswith(".py"):
                continue
            parts = file.rel[:-3].split("/")
            is_init = parts[-1] == "__init__"
            module_parts = parts[:-1] if is_init else parts
            aliases = {".".join(module_parts)} if module_parts else set()
            # Derive import roots from actual package markers in the locked
            # tree (e.g. src/astropy/__init__.py -> astropy), rather than an
            # unfrozen hand-written path table.
            for start in range(len(module_parts)):
                package_chain_ok = True
                last_dir_index = len(module_parts) if is_init \
                    else len(module_parts) - 1
                for end in range(start + 1, last_dir_index + 1):
                    directory = "/".join(module_parts[:end])
                    if directory not in package_dirs:
                        package_chain_ok = False
                        break
                if package_chain_ok:
                    aliases.add(".".join(module_parts[start:]))
            for alias in sorted(aliases):
                if alias in mapping and mapping[alias] != file.rel:
                    # Ambiguous aliases are unusable, never first-wins.
                    mapping[alias] = ""
                else:
                    mapping[alias] = file.rel
        return {module: rel for module, rel in mapping.items() if rel}
    for file in files:
        stem = file.rel
        ext = next(ext for ext in spec["extensions"] if stem.endswith(ext))
        stem = stem[:-len(ext)]
        parts = stem.split("/")
        if spec["language"] == "python" and parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(parts)
        mapping[module] = file.rel
        for root in spec["source_roots"]:
            prefix = root.rstrip("/") + "/"
            if file.rel.startswith(prefix):
                relative = file.rel[len(prefix):]
                relative = relative[:-len(ext)]
                rparts = relative.split("/")
                if spec["language"] == "python" and rparts[-1] == "__init__":
                    rparts = rparts[:-1]
                if rparts:
                    mapping.setdefault(".".join(rparts), file.rel)
    return mapping


def _resolve_longest(module: str, mapping: dict[str, str]) -> str | None:
    # Importing ``pkg.missing`` must not be "resolved" merely because
    # ``pkg/__init__.py`` exists.  ``ImportFrom`` emits its base as a separate
    # reference, so exact lookup also handles attribute imports without this
    # scientifically dangerous prefix fallback.
    return mapping.get(module)


def _python_graph(inventory: RepoInventory) -> dict[str, Any]:
    actual_python = platform.python_version()
    mapping = _module_map(inventory.files, inventory.spec)
    package_roots = {module.split(".", 1)[0] for module in mapping}
    references, parse_failures = [], []
    for file in inventory.files:
        try:
            tree = ast.parse(file.text, filename=file.rel)
        except (SyntaxError, ValueError) as err:
            parse_failures.append([file.rel, type(err).__name__])
            continue
        current = next((module for module, rel in mapping.items()
                        if rel == file.rel and module.count(".") >= 0), "")
        package = current if file.rel.endswith("/__init__.py") \
            else current.rpartition(".")[0]
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base_parts = package.split(".") if package else []
                    keep = max(0, len(base_parts) - node.level + 1)
                    base = ".".join(base_parts[:keep])
                    if node.module:
                        base = ".".join(x for x in (base, node.module) if x)
                else:
                    base = node.module or ""
                if base:
                    names.append(base)
                # Imported names are often attributes, not modules.  Add an
                # alias as another dependency only when it resolves exactly;
                # otherwise the resolved base module is the complete import
                # reference and must not be diluted by false "unresolved"
                # attribute rows.
                for alias in node.names:
                    candidate = ".".join(x for x in (base, alias.name) if x)
                    if candidate in mapping:
                        names.append(candidate)
            for raw_name in sorted(set(names)):
                target = _resolve_longest(raw_name, mapping)
                root = raw_name.split(".", 1)[0] if raw_name else ""
                internal = bool(target) or root in package_roots \
                    or raw_name.startswith(".")
                classification = ("internal-resolved" if target else
                                  "internal-unresolved" if internal else
                                  "external")
                references.append(dict(source=file.rel, reference=raw_name,
                                       classification=classification,
                                       target=target))
    return _finalize_graph(inventory, "python-ast-v1", references,
                           complete=not parse_failures,
                           failures=parse_failures,
                           resolver_evidence={"python_version": actual_python})


LEAN_IMPORT_RE = re.compile(
    r"(?m)^\s*import\s+(?:all\s+)?([A-Za-z0-9_.\u0080-\uffff]+)")


def _finalize_graph(inventory: RepoInventory, resolver: str,
                    references: list[dict[str, Any]], *, complete: bool,
                    failures: list[list[str]],
                    resolver_evidence: dict[str, Any]) -> dict[str, Any]:
    references.sort(key=lambda row: (row["source"], row["reference"],
                                     row["classification"], row["target"] or ""))
    edges = sorted({(row["target"], row["source"])
                    for row in references
                    if row["classification"] == "internal-resolved"
                    and row["target"] != row["source"]})
    internal = [row for row in references
                if row["classification"].startswith("internal-")]
    resolved = [row for row in internal
                if row["classification"] == "internal-resolved"]
    participants = sorted({x for edge in edges for x in edge})
    return dict(
        resolver=resolver, resolver_evidence=resolver_evidence,
        complete=complete, failures=failures,
        references=references,
        references_sha256=sha256_sorted_json(references),
        dependency_references=len(internal),
        dependency_references_resolved=len(resolved),
        dependency_edges=[list(edge) for edge in edges],
        dependency_edges_sha256=sha256_sorted_json(
            [list(edge) for edge in edges]),
        dependency_participating_files=participants)


def build_graph(inventory: RepoInventory, corpora_root: str) -> dict[str, Any]:
    resolver = inventory.spec["graph"]["resolver"]
    if resolver == "python-ast-v1":
        graph = _python_graph(inventory)
    elif resolver == "lean-import-proposal-fail-closed-v1":
        mapping = _module_map(inventory.files, inventory.spec)
        references = []
        for file in inventory.files:
            clean = _strip_comments_for_imports(file.text, "lean")
            for name in sorted(set(LEAN_IMPORT_RE.findall(clean))):
                target = _resolve_longest(name, mapping)
                references.append(dict(
                    source=file.rel, reference=name,
                    classification=("internal-resolved" if target else
                                    "external"), target=target))
        graph = _finalize_graph(
            inventory, resolver, references, complete=False,
            failures=[["native-lean-environment",
                       "not-implemented-fail-closed"]],
            resolver_evidence={"status": "proposal-only-fail-closed"})
    elif resolver == "cpp-unavailable-fail-closed-v1":
        graph = _finalize_graph(
            inventory, resolver, [], complete=False,
            failures=[["per-tu-dependency-scan",
                       "not-available-fail-closed"]],
            resolver_evidence={"status": "unavailable-fail-closed"})
    else:  # already rejected by protocol validation
        _fail(f"unsupported graph resolver {resolver}")
    inventory.graph = graph
    return graph


def _graph_order(files: list[SourceFile], graph: dict[str, Any]) \
        -> tuple[list[str], int]:
    paths = [file.rel for file in files]
    adj: dict[str, set[str]] = defaultdict(set)
    indegree = {path: 0 for path in paths}
    for dependency, dependent in graph["dependency_edges"]:
        if dependent not in adj[dependency]:
            adj[dependency].add(dependent)
            indegree[dependent] += 1
    heap = [path for path in paths if indegree[path] == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        node = heapq.heappop(heap)
        order.append(node)
        for dependent in sorted(adj[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(heap, dependent)
    cyclic = sorted(path for path in paths if indegree[path] > 0)
    order.extend(cyclic)
    return order, len(cyclic)


def _seeded_order(paths: list[str], seed: str, repo: str) -> list[str]:
    return sorted(paths, key=lambda path: hashlib.sha256(
        f"{seed}:shuffle:{repo}:{path}".encode()).digest())


def metadata_header(file: SourceFile, constants: dict[str, Any]) -> bytes:
    payload = json.dumps({
        "path": file.rel, "repo": file.repo,
        "source_bytes": file.nbytes, "source_sha256": file.source_sha256,
    }, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"))
    marker = "{compact_sorted_json(repo,path,source_sha256,source_bytes)}"
    template = constants["metadata_header"]
    if template.count(marker) != 1:
        _fail("frozen metadata header has invalid payload marker count")
    return template.replace(marker, payload).encode("utf-8")


def build_layout(inventory: RepoInventory, order: list[str],
                 constants: dict[str, Any]) -> tuple[list[Layout], str, int]:
    by_path = {file.rel: file for file in inventory.files}
    layouts, position = [], 0
    digest = hashlib.sha256()
    for path in order:
        file = by_path[path]
        header = metadata_header(file, constants)
        digest.update(header)
        header_start = position
        position += len(header)
        body_start = position
        digest.update(file.data)
        position += file.nbytes
        layouts.append(Layout(path, header_start, body_start, position,
                              position))
    return layouts, digest.hexdigest(), position


def build_orderings(inventory: RepoInventory,
                    constants: dict[str, Any]) -> tuple[dict[str, Any], int]:
    topo, n_cycle = _graph_order(inventory.files, inventory.graph or {})
    native_ordering_ok = graph_gate(inventory.graph or {},
                                    len(inventory.files), constants, n_cycle)
    paths = [file.rel for file in inventory.files]
    orders = {
        "shuffled": _seeded_order(paths, constants["a0_seed"],
                                  inventory.spec["repo"]),
        "topological": topo,
        "reverse-topological": list(reversed(topo)),
    }
    expected = sorted(paths)
    file_set_hash = sha256_sorted_json(sorted(
        [file.rel, file.source_sha256, file.nbytes]
        for file in inventory.files))
    rows, layouts = {}, {}
    for ordering in ORDERINGS:
        order = orders[ordering]
        if sorted(order) != expected or len(order) != len(set(order)):
            _fail(f"{inventory.spec['repo']}/{ordering}: composition drift")
        layout, stream_hash, stream_bytes = build_layout(
            inventory, order, constants)
        layouts[ordering] = layout
        rows[ordering] = dict(
            n_files=len(order), stream_bytes=stream_bytes,
            file_set_sha256=file_set_hash,
            ordering_sha256=sha256_sorted_json(order),
            stream_sha256=stream_hash,
            ordering_claim_status=(
                "shuffled-primary" if ordering == "shuffled" else
                "native-resolved" if native_ordering_ok else
                "unavailable-fail-closed"))
    inventory.orders = orders
    inventory.layouts = layouts
    return rows, n_cycle


def _systematic_file_origins(paths: list[str], n: int, seed: str,
                             repo: str) -> list[str]:
    count = min(len(paths), n)
    if not count:
        return []
    u64 = _systematic_u64(seed, repo, "a0")
    indices = _systematic_indices(len(paths), count, u64)
    return [paths[index] for index in indices]


def _systematic_u64(seed_sha256: str, repo: str, arm: str) -> int:
    """Frozen P0 domain-separated unsigned 64-bit systematic phase."""
    if (not _valid_sha(seed_sha256, (64,)) or not repo
            or arm not in ("a0", "a1")):
        _fail("invalid systematic phase input")
    preimage = json.dumps(
        ["v2c-systematic-offset-v1", seed_sha256, repo, arm],
        ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return int.from_bytes(hashlib.sha256(preimage).digest()[:8], "big")


def _systematic_indices(population: int, count: int, u64: int) -> list[int]:
    """Exact frozen phase-shifted systematic indices without replacement."""
    if (not _is_int(population, 1) or not _is_int(count, 1)
            or count > population or not _is_int(u64, 0)
            or u64 >= 1 << 64):
        _fail("invalid systematic-index input")
    scale = 1 << 64
    indices = [(population * (u64 + j * scale)) // (count * scale)
               for j in range(count)]
    if (indices != sorted(set(indices))
            or indices[0] < 0 or indices[-1] >= population):
        _fail("systematic-index formula did not produce unique sorted indices")
    return indices


def _interval_source_stats(layouts: list[Layout], start: int, end: int,
                           body_ends: list[int] | None = None) \
        -> tuple[int, set[str]]:
    source, paths = 0, set()
    if body_ends is None:
        body_ends = [layout.body_end for layout in layouts]
    first = bisect.bisect_right(body_ends, start)
    for layout in layouts[first:]:
        if layout.body_start >= end:
            break
        overlap = max(0, min(end, layout.body_end)
                      - max(start, layout.body_start))
        if overlap:
            source += overlap
            paths.add(layout.rel)
    return source, paths


def a0_occupancy(inventory: RepoInventory,
                 constants: dict[str, Any]) -> tuple[dict[str, Any],
                                                     dict[str, Any]]:
    paths = [file.rel for file in inventory.files]
    origins = _systematic_file_origins(
        paths, constants["planned_a0_origins_per_repo"],
        constants["a0_seed"], inventory.spec["repo"])
    grid = constants["grid_bytes"]
    occupancy: dict[str, Any] = {}
    metadata_fraction: dict[str, Any] = {}
    for ordering in ORDERINGS:
        layouts = inventory.layouts[ordering]
        body_ends = [layout.body_end for layout in layouts]
        by_path = {layout.rel: (i, layout)
                   for i, layout in enumerate(layouts)}
        total = layouts[-1].end
        occupancy[ordering] = {axis: {} for axis in AXES}
        metadata_fraction[ordering] = {}
        previous = 0
        for rung in grid:
            stream_complete = []
            source_complete = []
            stream_files_by_origin: list[list[Any]] = []
            source_files_by_origin: list[list[Any]] = []
            stream_source = stream_metadata = 0
            stream_paths: set[str] = set()
            source_paths: set[str] = set()
            for path in origins:
                index, origin_layout = by_path[path]
                origin = origin_layout.header_start
                if origin + rung <= total:
                    source_bytes, seen = _interval_source_stats(
                        layouts, origin + previous, origin + rung, body_ends)
                    stream_complete.append(path)
                    stream_source += source_bytes
                    stream_metadata += (rung - previous) - source_bytes
                    stream_paths.update(seen)
                    stream_files_by_origin.append([path, sorted(seen)])
                remaining = rung
                lo = previous
                seen_source: set[str] = set()
                delivered = 0
                for layout in layouts[index:]:
                    width = layout.body_end - layout.body_start
                    seg_lo = delivered
                    seg_hi = delivered + width
                    if seg_hi > lo and seg_lo < rung:
                        seen_source.add(layout.rel)
                    delivered = seg_hi
                    if delivered >= rung:
                        break
                if delivered >= rung:
                    source_complete.append(path)
                    source_paths.update(seen_source)
                    source_files_by_origin.append(
                        [path, sorted(seen_source)])
            key = str(rung)
            occupancy[ordering]["q_stream"][key] = dict(
                n_complete_units=len(stream_complete),
                n_distinct_files=len(stream_paths),
                source_bytes=stream_source,
                metadata_bytes=stream_metadata,
                complete_identities=stream_complete,
                complete_identities_sha256=sha256_sorted_json(
                    stream_complete),
                source_files_by_identity=stream_files_by_origin)
            occupancy[ordering]["q_source"][key] = dict(
                n_complete_units=len(source_complete),
                n_distinct_files=len(source_paths),
                source_bytes=(rung - previous) * len(source_complete),
                metadata_bytes=0,
                complete_identities=source_complete,
                complete_identities_sha256=sha256_sorted_json(
                    source_complete),
                source_files_by_identity=source_files_by_origin)
            denominator = stream_source + stream_metadata
            metadata_fraction[ordering][key] = (
                stream_metadata / denominator if denominator else None)
            previous = rung
    return dict(identities=origins,
                identities_sha256=sha256_sorted_json(origins)), \
        dict(occupancy=occupancy, metadata_fraction=metadata_fraction)


def _line_start_at_or_after(data: bytes, offset: int) -> int:
    if offset == 0 or data[offset - 1:offset] == b"\n":
        return offset
    newline = data.find(b"\n", offset)
    return len(data) if newline < 0 else newline + 1


def _utf8_end_at_or_before(data: bytes, end: int) -> int:
    end = min(end, len(data))
    while end > 0:
        try:
            data[:end].decode("utf-8")
            return end
        except UnicodeDecodeError as err:
            end = err.start
    return 0


def _comment_mask(data: bytes, language: str) -> bytearray:
    """Mark comment bytes while respecting quoted strings and Lean nesting."""
    mask = bytearray(len(data))
    i, block_depth = 0, 0
    quote: int | None = None
    escaped = False
    while i < len(data):
        if block_depth:
            mask[i] = 1
            if language == "lean" and data[i:i + 2] == b"/-":
                mask[i:i + 2] = b"\x01\x01"
                block_depth += 1
                i += 2
            elif data[i:i + 2] == b"*/" and language == "cpp":
                mask[i:i + 2] = b"\x01\x01"
                block_depth -= 1
                i += 2
            elif data[i:i + 2] == b"-/" and language == "lean":
                mask[i:i + 2] = b"\x01\x01"
                block_depth -= 1
                i += 2
            else:
                i += 1
            continue
        byte = data[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == quote:
                quote = None
            i += 1
            continue
        if byte in (0x22, 0x27):  # double/single quote
            quote = byte
            i += 1
            continue
        line_marker = (b"#" if language == "python" else
                       b"--" if language == "lean" else b"//")
        if data[i:i + len(line_marker)] == line_marker:
            end = data.find(b"\n", i)
            end = len(data) if end < 0 else end
            mask[i:end] = b"\x01" * (end - i)
            i = end
            continue
        block_marker = b"/-" if language == "lean" else b"/*" \
            if language == "cpp" else None
        if block_marker and data[i:i + 2] == block_marker:
            mask[i:i + 2] = b"\x01\x01"
            block_depth = 1
            i += 2
            continue
        i += 1
    return mask


def _a1_horizon_eligible(data: bytes, language: str,
                         constants: dict[str, Any]) -> bool:
    end = _utf8_end_at_or_before(
        data, min(len(data), constants["primary_horizon_bytes"]))
    horizon = data[:end]
    mask = _comment_mask(horizon, language)
    nonwhitespace = sum(byte not in b" \t\r\n\v\f" for byte in horizon)
    noncomment = sum(not flag for flag in mask)
    return (nonwhitespace >= constants["a1_min_nonwhitespace_bytes"]
            and noncomment >= constants["a1_min_noncomment_bytes"])


def sample_targets(inventory: RepoInventory,
                   constants: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                       dict[str, int]]:
    files = inventory.files
    cumulative, total = [], 0
    for file in files:
        total += file.nbytes
        cumulative.append(total)
    planned = constants["planned_a1_targets_per_repo"]
    block = constants["target_block_bytes"]
    slot_count = total // block
    target_n = min(planned, slot_count)
    phase = _systematic_u64(constants["a1_seed"],
                            inventory.spec["repo"], "a1")
    offsets = ([block * slot for slot in
                _systematic_indices(slot_count, target_n, phase)]
               if target_n else [])
    candidates, accepted_by_file = [], defaultdict(list)
    counts = dict(sampled=0, too_short=0, realized_overlap=0,
                  cross_boundary=0, comment=0, neardup=0)
    for offset in offsets:
        file_index = bisect.bisect_right(cumulative, offset)
        prior = cumulative[file_index - 1] if file_index else 0
        file = files[file_index]
        local = offset - prior
        counts["sampled"] += 1
        if local + block > file.nbytes:
            counts["cross_boundary"] += 1
            continue
        start = _line_start_at_or_after(file.data, local)
        end = _utf8_end_at_or_before(file.data, start + block)
        if end - start < constants["target_min_bytes"]:
            counts["too_short"] += 1
            continue
        if any(start < old_end and old_start < end
               for old_start, old_end in accepted_by_file[file.rel]):
            counts["realized_overlap"] += 1
            continue
        target_bytes = file.data[start:end]
        if not _a1_horizon_eligible(
                target_bytes, inventory.spec["language"], constants):
            counts["comment"] += 1
            continue
        accepted_by_file[file.rel].append((start, end))
        candidates.append(dict(
            repo=inventory.spec["repo"], file_path=file.rel,
            file_byte_start=start, file_byte_end=end,
            target_sha256=hashlib.sha256(target_bytes).hexdigest(),
            first_add_date=file.first_add_date,
            near_duplicate=False))
    candidates.sort(key=lambda row: (row["file_path"], row["file_byte_start"],
                                     row["file_byte_end"]))
    return candidates, counts


LEXICAL_RECORD_RE = re.compile(r"[\w\u0080-\uffff]+|[^\s\w]", re.UNICODE)


def _utf8_window_interior(data: bytes, start: int, end: int) \
        -> tuple[int, int]:
    """Return the complete-scalar interior of byte window ``[start, end)``.

    Source files have already passed a strict whole-blob UTF-8 decode in
    ``load_inventory``.  A byte-axis window can nevertheless begin or end in
    the middle of one of those valid scalars.  Only those incomplete edge
    scalars are outside the lexical view; the frozen byte window itself is not
    moved, padded, or decoded with replacement.
    """
    if (not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end < start or end > len(data)):
        _fail("invalid lexical byte window")
    interior_start = start
    while (interior_start < end
           and data[interior_start] & 0xC0 == 0x80):
        interior_start += 1
    interior_end = end
    while (interior_end > interior_start and interior_end < len(data)
           and data[interior_end] & 0xC0 == 0x80):
        interior_end -= 1
    return interior_start, interior_end


def _lexical_records(data: bytes, language: str, *, start: int = 0,
                     end: int | None = None) \
        -> list[tuple[str, int, int]]:
    requested_end = len(data) if end is None else end
    interior_start, interior_end = _utf8_window_interior(
        data, start, requested_end)
    interior = data[interior_start:interior_end]
    mask = _comment_mask(interior, language)
    visible = bytes(byte if not mask[i] else 0x20
                    for i, byte in enumerate(interior))
    # Strict decoding is part of P0.  In particular, do not replace or ignore
    # malformed bytes: an invalid byte away from a valid scalar-split edge is
    # a fail-closed corpus error.
    text = visible.decode("utf-8")
    char_to_byte = [interior_start]
    total = interior_start
    for char in text:
        total += len(char.encode("utf-8"))
        char_to_byte.append(total)
    return [(match.group(0), char_to_byte[match.start()],
             char_to_byte[match.end()])
            for match in LEXICAL_RECORD_RE.finditer(text)]


def _lexical_grams(data: bytes, base: int, language: str, n: int, *,
                   start: int = 0, end: int | None = None) \
        -> list[tuple[str, int, int, int]]:
    records = _lexical_records(data, language, start=start, end=end)
    grams = []
    for i in range(0, max(0, len(records) - n + 1)):
        group = records[i:i + n]
        digest = sha256_sorted_json([record[0] for record in group])
        grams.append((digest, base + group[0][1], base + group[-1][2], 1))
    return grams


def screen_near_duplicates(inventories: list[RepoInventory],
                           constants: dict[str, Any]) -> None:
    nd = constants["near_duplicate"]
    occurrence: dict[str, list[tuple[str, str, int, int]]] = defaultdict(list)
    files_by_repo: dict[str, dict[str, SourceFile]] = {}
    target_features: dict[tuple[str, str, int, int],
                          tuple[list[tuple[str, int, int, int]],
                                set[str]]] = {}
    watched_grams: set[str] = set()
    for inventory in inventories:
        files_by_repo[inventory.spec["repo"]] = {f.rel: f for f in inventory.files}
        by_file = files_by_repo[inventory.spec["repo"]]
        for target in inventory.targets or []:
            file = by_file[target["file_path"]]
            target_start = target["file_byte_start"]
            target_end = target["file_byte_end"]
            if len(_lexical_records(
                    file.data, inventory.spec["language"],
                    start=target_start, end=target_end)) \
                    < nd["minimum_lexical_records"]:
                continue
            units = _lexical_grams(
                file.data, 0, inventory.spec["language"], nd["gram_n"],
                start=target_start, end=target_end)
            target_set = {unit[0] for unit in units}
            if not target_set:
                continue
            key = (inventory.spec["repo"], target["file_path"],
                   target["file_byte_start"], target["file_byte_end"])
            target_features[key] = (units, target_set)
            watched_grams.update(target_set)
    # Only target grams can seed a target/context Jaccard candidate.  Keeping
    # the entire panel's unrelated 5-gram occurrence index would make memory
    # scale with every corpus token rather than the bounded target cohort.
    for inventory in inventories:
        for file in inventory.files:
            for digest, start, end, _ in _lexical_grams(
                    file.data, 0, inventory.spec["language"], nd["gram_n"]):
                if digest in watched_grams:
                    occurrence[digest].append((inventory.spec["repo"],
                                               file.rel, start, end))
    cmax = constants["headline_grid_bytes"][-1]
    slice_cache: dict[tuple[str, str, int, int], set[str]] = {}
    for inventory in inventories:
        by_file = files_by_repo[inventory.spec["repo"]]
        layout_maps = {
            ordering: {layout.rel: layout
                       for layout in inventory.layouts[ordering]}
            for ordering in ORDERINGS}
        for target in inventory.targets or []:
            feature_key = (inventory.spec["repo"], target["file_path"],
                           target["file_byte_start"],
                           target["file_byte_end"])
            feature = target_features.get(feature_key)
            if feature is None:
                continue
            units, target_set = feature
            seen_windows: set[tuple[str, str, int, int]] = set()
            near = False
            target_length = target["file_byte_end"] - target["file_byte_start"]
            # A Jaccard-near duplicate must share many grams.  Anchoring on
            # the rarest distinct target grams preserves that test while
            # avoiding a quadratic scan over ubiquitous punctuation grams.
            distinct_units: dict[str, tuple[str, int, int, int]] = {}
            for unit in units:
                distinct_units.setdefault(unit[0], unit)
            def has_external_occurrence(unit: tuple[str, int, int, int]) \
                    -> bool:
                return any(
                    other_repo != inventory.spec["repo"]
                    or other_path != target["file_path"]
                    or other_end <= target["file_byte_start"]
                    or other_start >= target["file_byte_end"]
                    for other_repo, other_path, other_start, other_end
                    in occurrence.get(unit[0], []))
            anchors = sorted(
                (unit for unit in distinct_units.values()
                 if has_external_occurrence(unit)),
                key=lambda row: (len(occurrence.get(row[0], [])), row[0]))[:32]
            for digest, target_gram_start, _, _ in anchors:
                for other_repo, other_path, other_start, other_end in \
                        occurrence.get(digest, []):
                    relative = target_gram_start - target["file_byte_start"]
                    candidate_start = other_start - relative
                    candidate_end = candidate_start + target_length
                    other_file = files_by_repo[other_repo][other_path]
                    if candidate_start < 0 or candidate_end > other_file.nbytes:
                        continue
                    window = (other_repo, other_path,
                              candidate_start, candidate_end)
                    if window in seen_windows:
                        continue
                    seen_windows.add(window)
                    if (other_repo == inventory.spec["repo"]
                            and other_path == target["file_path"]
                            and candidate_start < target["file_byte_end"]
                            and target["file_byte_start"] < candidate_end):
                        continue
                    allowed = other_repo != inventory.spec["repo"]
                    if not allowed:
                        for ordering in ORDERINGS:
                            target_layout = layout_maps[ordering][
                                target["file_path"]]
                            other_layout = layout_maps[ordering][other_path]
                            p = (target_layout.body_start
                                 + target["file_byte_start"])
                            span_start = other_layout.body_start + candidate_start
                            span_end = other_layout.body_start + candidate_end
                            with_lo = max(0, p - cmax)
                            prior_end = target_layout.header_start
                            cross_lo = max(0, prior_end - cmax)
                            if ((with_lo <= span_start and span_end <= p)
                                    or (cross_lo <= span_start
                                        and span_end <= prior_end)):
                                allowed = True
                                break
                    if not allowed:
                        continue
                    if window not in slice_cache:
                        slice_cache[window] = {row[0] for row in _lexical_grams(
                            other_file.data, 0,
                            next(item.spec["language"] for item in inventories
                                 if item.spec["repo"] == other_repo),
                            nd["gram_n"], start=candidate_start,
                            end=candidate_end)}
                    other_set = slice_cache[window]
                    union = target_set | other_set
                    similarity = (len(target_set & other_set) / len(union)
                                  if union else 0.0)
                    if similarity >= nd["match_fraction"]:
                        near = True
                        break
                if near:
                    break
            if near:
                target["near_duplicate"] = True


def _quantiles(values: list[int]) -> dict[str, Any]:
    if not values:
        return dict(n=0, min=None, median=None, p90=None, max=None,
                    values_sha256=sha256_sorted_json([]))
    ordered = sorted(values)
    p90_index = min(len(ordered) - 1, math.ceil(.9 * len(ordered)) - 1)
    return dict(n=len(ordered), min=ordered[0],
                median=statistics.median(ordered),
                p90=ordered[p90_index], max=ordered[-1],
                values_sha256=sha256_sorted_json(ordered))


def _greedy_disjoint(intervals: list[tuple[int, int]]) -> int:
    count, last_end = 0, -1
    for start, end in sorted(intervals, key=lambda row: (row[1], row[0])):
        if start >= last_end:
            count += 1
            last_end = end
    return count


def a1_occupancy(inventory: RepoInventory,
                 constants: dict[str, Any]) -> dict[str, Any]:
    targets = [target for target in inventory.targets or []
               if not target["near_duplicate"]]
    grid = constants["grid_bytes"]
    counts: dict[str, Any] = {}
    shared_counts: dict[str, Any] = {}
    fractions: dict[str, Any] = {}
    exhaustion: dict[str, Any] = {}
    disjoint: dict[str, Any] = {}
    overlap: dict[str, Any] = {}
    identities_by_ordering: dict[str, Any] = {}
    for ordering in ORDERINGS:
        layout_map = {layout.rel: layout
                      for layout in inventory.layouts[ordering]}
        positions = []
        exhaustion_values = []
        for target in targets:
            layout = layout_map[target["file_path"]]
            p = layout.body_start + target["file_byte_start"]
            target_end = layout.body_start + target["file_byte_end"]
            own_prefix = p - layout.header_start
            positions.append((target, layout, p, target_end, own_prefix))
            exhaustion_values.append(own_prefix)
        exhaustion[ordering] = _quantiles(exhaustion_values)
        counts[ordering] = {regime: {} for regime in REGIMES}
        shared_counts[ordering] = {}
        fractions[ordering] = {}
        disjoint[ordering] = {}
        overlap[ordering] = {}
        identities_by_ordering[ordering] = {regime: {} for regime in REGIMES}
        for c in grid:
            with_rows, cross_rows, with_fracs, intervals = [], [], [], []
            context_contains_target = 0
            eligible_for_overlap = 0
            target_spans = [(row[2], row[3], row[0]) for row in positions]
            for target, layout, p, target_end, own_prefix in positions:
                if p >= c:
                    with_rows.append(target)
                    with_fracs.append(min(c, own_prefix) / c)
                    intervals.append((p - c, target_end))
                    eligible_for_overlap += 1
                    if any(other is not target and other_start < p
                           and p - c < other_end
                           for other_start, other_end, other in target_spans):
                        context_contains_target += 1
                # Skipping the current file's header and body prefix leaves
                # exactly the complete preceding-file stream.
                if layout.header_start >= c:
                    cross_rows.append(target)
            def identity(row: dict[str, Any]) -> list[Any]:
                return [row["file_path"], row["file_byte_start"],
                        row["file_byte_end"], row["target_sha256"]]
            def cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
                identities = [identity(row) for row in rows]
                return dict(
                    n_blocks=len(rows),
                    n_distinct_files=len({row["file_path"] for row in rows}),
                    target_identities=identities,
                    target_identities_sha256=sha256_sorted_json(identities))
            key = str(c)
            counts[ordering]["with-file"][key] = cell(with_rows)
            counts[ordering]["cross-file-only"][key] = cell(cross_rows)
            identities_by_ordering[ordering]["with-file"][key] = {
                tuple(identity(row)) for row in with_rows}
            identities_by_ordering[ordering]["cross-file-only"][key] = {
                tuple(identity(row)) for row in cross_rows}
            with_ids = {tuple(identity(row)) for row in with_rows}
            shared_rows = [row for row in cross_rows
                           if tuple(identity(row)) in with_ids]
            if len(shared_rows) != len(cross_rows):
                _fail(f"{inventory.spec['repo']}/{ordering}/{c}: "
                      "cross-file cohort is not a with-file subset")
            shared_counts[ordering][key] = cell(shared_rows)
            fractions[ordering][key] = dict(
                n=len(with_fracs), mean=(sum(with_fracs) / len(with_fracs)
                                         if with_fracs else None),
                median=(statistics.median(with_fracs)
                        if with_fracs else None))
            disjoint[ordering][key] = _greedy_disjoint(intervals)
            overlap[ordering][key] = (
                context_contains_target / eligible_for_overlap
                if eligible_for_overlap else None)
    all_ordering_counts: dict[str, Any] = {regime: {} for regime in REGIMES}
    for regime in REGIMES:
        for c in grid:
            key = str(c)
            intersection = set.intersection(*(
                identities_by_ordering[ordering][regime][key]
                for ordering in ORDERINGS))
            identities = [list(identity) for identity in sorted(intersection)]
            all_ordering_counts[regime][key] = dict(
                n_blocks=len(identities),
                n_distinct_files=len({identity[0]
                                      for identity in identities}),
                target_identities=identities,
                target_identities_sha256=sha256_sorted_json(identities))
    return dict(counts=counts, shared_counts=shared_counts,
                all_ordering_counts=all_ordering_counts,
                fractions=fractions, exhaustion=exhaustion,
                disjoint=disjoint, overlap=overlap)


def _best_range(occupancy: dict[str, Any], grid: list[int],
                floors: dict[str, int], *, remove_floor: bool = False,
                minimum_end: int | None = None) -> dict[str, Any]:
    def identities(row: dict[str, Any]) -> set[Any] | None:
        if "complete_identities" in row:
            return set(row["complete_identities"])
        if "target_identities" in row:
            return {tuple(identity) for identity in row["target_identities"]}
        return None

    def files_for(row: dict[str, Any], cohort: set[Any]) -> set[str]:
        if "source_files_by_identity" in row:
            return {path for identity, paths in row["source_files_by_identity"]
                    if identity in cohort for path in paths}
        return {identity[0] for identity in cohort
                if isinstance(identity, tuple) and identity}

    considered = grid[1:] if remove_floor else grid
    best = None
    for i, start in enumerate(considered):
        for end in considered[i:]:
            if minimum_end is not None and end < minimum_end:
                continue
            rungs = [rung for rung in considered if start <= rung <= end]
            rows = [occupancy[str(rung)] for rung in rungs]
            endpoint_cohort = identities(rows[-1])
            if endpoint_cohort is None:
                bin_stats = [(
                    row.get("n_complete_units", row.get("n_blocks", 0)),
                    row["n_distinct_files"]) for row in rows]
                units = min(value[0] for value in bin_stats)
                nfiles = min(value[1] for value in bin_stats)
            else:
                bin_stats = []
                cell_files: set[str] = set()
                for row in rows:
                    row_cohort = identities(row)
                    if row_cohort is None or not endpoint_cohort <= row_cohort:
                        _fail("occupancy identities are not nested")
                    bin_files = files_for(row, endpoint_cohort)
                    bin_stats.append((len(endpoint_cohort), len(bin_files)))
                    cell_files.update(bin_files)
                units, nfiles = len(endpoint_cohort), len(cell_files)
            if any(units_at_bin < floors["bin_units"]
                   or files_at_bin < floors["bin_files"]
                   for units_at_bin, files_at_bin in bin_stats):
                continue
            if units < floors["cell_units"] or nfiles < floors["cell_files"]:
                continue
            decades = math.log10(end / start) if end > start else 0.0
            candidate = dict(start_bytes=start, end_bytes=end,
                             decades=decades, n_complete_units=units,
                             n_distinct_files=nfiles)
            if best is None or (candidate["decades"], end, -start) > (
                    best["decades"], best["end_bytes"], -best["start_bytes"]):
                best = candidate
    return best or dict(start_bytes=None, end_bytes=None, decades=0.0,
                        n_complete_units=0, n_distinct_files=0)


def _ceil_grid(grid: list[int], value: float) -> int | None:
    return next((rung for rung in grid if rung >= value), None)


def implied_headline_rung(grid: list[int], median_exhaustion: float | None,
                          range_rule: dict[str, Any]) -> int | None:
    """Structural cascade: floor-robust reach, then exhaustion reach.

    This helper is outcome-free and is kept public for exact boundary tests.
    ``None`` means the frozen top rung cannot satisfy the conjunction.
    """
    if len(grid) < 2 or median_exhaustion is None:
        return None
    floor_robust_end = _ceil_grid(
        grid, grid[1] * 10 ** range_rule["min_decades_without_floor"])
    exhaustion_end = (range_rule["exhaustion_multiplier"]
                      * median_exhaustion)
    if floor_robust_end is None:
        return None
    return _ceil_grid(grid, max(floor_robust_end, exhaustion_end))


def graph_gate(graph: dict[str, Any], n_files: int,
               constants: dict[str, Any], n_cycle_files: int) -> bool:
    gate = constants["graph_gate"]
    denominator = graph["dependency_references"]
    resolution = (graph["dependency_references_resolved"] / denominator
                  if denominator else 0.0)
    participation = (len(graph["dependency_participating_files"]) / n_files
                     if n_files else 0.0)
    return bool(
        graph["complete"]
        and resolution >= gate["min_resolution_fraction"]
        and len(graph["dependency_edges"]) >= gate["min_edges"]
        and participation >= gate["min_participating_file_fraction"]
        and (not gate["require_acyclic"] or n_cycle_files == 0))


def _repo_ranges(repo: dict[str, Any], constants: dict[str, Any]) \
        -> dict[str, Any]:
    grid, headline_grid = (constants["grid_bytes"],
                           constants["headline_grid_bytes"])
    floors = constants["floors"]
    range_rule = constants["range"]
    def classify(rows: dict[str, Any]) -> dict[str, Any]:
        ordinary = _best_range(rows, grid, floors)
        robust = _best_range(rows, headline_grid, floors,
                             remove_floor=True)
        return dict(
            all=ordinary, without_floor=robust,
            ordinary_range_ok=(ordinary["decades"]
                               >= range_rule["min_contiguous_decades"]),
            headline_floor_robust_ok=(robust["decades"]
                                      >= range_rule[
                                          "min_decades_without_floor"]))
    ranges = {"A0": {}, "A1": {}}
    for ordering in ORDERINGS:
        ranges["A0"][ordering] = {}
        for axis in AXES:
            rows = repo["A0_structural_occupancy_by_ordering_axis_rung"] \
                [ordering][axis]
            ranges["A0"][ordering][axis] = classify(rows)
        ranges["A1"][ordering] = {}
        for regime in REGIMES:
            rows = repo["n_blocks_structurally_complete_by_c_ordering_regime"] \
                [ordering][regime]
            ranges["A1"][ordering][regime] = classify(rows)
        shared = repo[
            "n_blocks_structurally_complete_by_c_ordering_shared_regimes"] \
            [ordering]
        ranges["A1"][ordering]["shared-regime-complete-case"] = classify(
            shared)
    return ranges


def _component_members(repos: list[str], edges: list[dict[str, Any]]) \
        -> list[list[str]]:
    parent = {repo: repo for repo in repos}
    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node
    for edge in edges:
        a, b = edge["repos"]
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = defaultdict(list)
    for repo in repos:
        groups[find(repo)].append(repo)
    return sorted((sorted(group) for group in groups.values()),
                  key=lambda group: group[0])


def _dependence_edges(inventories: list[RepoInventory],
                      constants: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                         dict[str, float]]:
    gram_n = constants["repository_dependence"]["gram_n"]
    gram_sets: dict[str, set[str]] = {}
    import_pairs: dict[tuple[str, str], int] = defaultdict(int)
    prefix_owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for inventory in inventories:
        repo = inventory.spec["repo"]
        grams: set[str] = set()
        for file in inventory.files:
            grams.update(row[0] for row in _lexical_grams(
                file.data, 0, inventory.spec["language"], gram_n))
        gram_sets[repo] = grams
        module_roots = {module.split(".", 1)[0]
                        for module in _module_map(
                            inventory.files, inventory.spec)}
        for prefix in module_roots:
            prefix_owners[(inventory.spec["language"], prefix)].add(repo)
    for inventory in inventories:
        repo = inventory.spec["repo"]
        language = inventory.spec["language"]
        for ref in inventory.graph["references"]:
            if ref["classification"] != "external":
                continue
            root = ref["reference"].split(".", 1)[0].split("/", 1)[0]
            for other in sorted(prefix_owners.get((language, root), set())
                                - {repo}):
                import_pairs[tuple(sorted((repo, other)))] += 1
    edges, fractions = [], {repo: 0.0 for repo in gram_sets}
    rd = constants["repository_dependence"]
    inventories_by_language: dict[str, list[RepoInventory]] = defaultdict(list)
    for inventory in inventories:
        inventories_by_language[inventory.spec["language"]].append(inventory)
    for language, group in inventories_by_language.items():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                a, b = left.spec["repo"], right.spec["repo"]
                shared_hashes = gram_sets[a] & gram_sets[b]
                shared = len(shared_hashes)
                fa = shared / len(gram_sets[a]) if gram_sets[a] else 0.0
                fb = shared / len(gram_sets[b]) if gram_sets[b] else 0.0
                fractions[a] = max(fractions[a], fa)
                fractions[b] = max(fractions[b], fb)
                imports = import_pairs.get((a, b), 0)
                shared_gate = max(fa, fb) >= rd["min_shared_fraction"]
                shared_history = len(left.history_commits
                                     & right.history_commits)
                history_gate = bool(shared_history) \
                    and rd["git_history_overlap"]
                if imports or shared_gate or history_gate:
                    edges.append(dict(language=language, repos=[a, b],
                                      direct_import_references=imports,
                                      shared_git_commits=shared_history,
                                      shared_unique_fivegrams=shared,
                                      shared_fraction_left=fa,
                                      shared_fraction_right=fb))
    edges.sort(key=lambda edge: (edge["language"], edge["repos"]))
    return edges, fractions


def recompute_decisions(repos: list[dict[str, Any]],
                        languages: list[dict[str, Any]],
                        protocol: dict[str, Any]) -> dict[str, Any]:
    constants = protocol["constants"]
    floors_map: dict[str, bool] = {}
    headline: dict[str, bool] = {}
    top_rung: dict[str, bool] = {}
    minimum_components = constants["repository_dependence"][
        "minimum_components"]
    independence = {row["language"]:
                    row["n_independent_components"] >= minimum_components
                    for row in languages}
    by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for repo in repos:
        by_language[repo["language"]].append(repo)
    optimistic = max(row["optimistic_context_bytes"]
                     for row in protocol["checkpoints"])
    for language in sorted(by_language):
        group = by_language[language]
        for axis in AXES:
            key = f"A0/{axis}/na/{language}"
            floors_map[key] = any(
                repo["structural_ranges"]["A0"]["shuffled"][axis]
                    ["headline_floor_robust_ok"]
                for repo in group)
        for regime in REGIMES:
            key = f"A1/c/{regime}/{language}"
            floors_map[key] = any(
                repo["structural_ranges"]["A1"]["shuffled"][regime]
                    ["headline_floor_robust_ok"]
                for repo in group)
        structurally_eligible_requirements = []
        candidates = []
        for repo in group:
            implied = repo["implied_min_context_bytes_for_headline"]
            base_ok = (
                repo["graph_gate_ok"] and implied is not None
                and all(repo["structural_ranges"]["A0"]["shuffled"]
                            [axis]["headline_floor_robust_ok"]
                        for axis in AXES)
                and all(repo["structural_ranges"]["A1"]["shuffled"]
                            [regime]["headline_floor_robust_ok"]
                        for regime in REGIMES)
                and repo["structural_ranges"]["A1"]["shuffled"]
                    ["shared-regime-complete-case"]
                    ["headline_floor_robust_ok"])
            if base_ok:
                structurally_eligible_requirements.append(implied)
            if (base_ok and implied <= optimistic
                    and all(repo["structural_ranges"]["A0"]["shuffled"]
                            [axis]["without_floor"]["end_bytes"] >= implied
                            for axis in AXES)
                    and all(repo["structural_ranges"]["A1"]["shuffled"]
                            [regime]["without_floor"]["end_bytes"] >= implied
                            for regime in REGIMES)
                    and repo["structural_ranges"]["A1"]["shuffled"]
                        ["shared-regime-complete-case"]["without_floor"]
                        ["end_bytes"] >= implied):
                candidates.append(implied)
        headline[language] = bool(candidates)
        top_rung[language] = (bool(structurally_eligible_requirements)
                              and min(structurally_eligible_requirements)
                              == constants["headline_grid_bytes"][-1])
    arm_b = {
        f"{language['language']}/{checkpoint['model_id']}@{checkpoint['revision']}":
        False
        for language in languages for checkpoint in protocol["checkpoints"]
    }
    return {
        "K2_independence_ok_by_language": independence,
        "K5_K6_arm_b_by_model": arm_b,
        "headline_conditions_structurally_reachable_by_language": headline,
        "headline_requires_top_rung_by_language": top_rung,
        "unit_file_floors_structurally_reachable_by_arm_axis_regime_language":
            floors_map,
    }


def _repo_row(inventory: RepoInventory, order_rows: dict[str, Any],
              n_cycle_files: int, a0: dict[str, Any],
              a0_detail: dict[str, Any], a1: dict[str, Any],
              target_counts: dict[str, int], shared_fraction: float,
              protocol: dict[str, Any]) -> dict[str, Any]:
    constants = protocol["constants"]
    graph = inventory.graph
    targets_all = inventory.targets or []
    targets = [target for target in targets_all if not target["near_duplicate"]]
    target_counts = dict(target_counts)
    target_counts["neardup"] = len(targets_all) - len(targets)
    n_files_too_short = sum(file.nbytes < constants["target_min_bytes"]
                            for file in inventory.files)
    total_bytes = sum(file.nbytes for file in inventory.files)
    total_codepoints = sum(file.codepoints for file in inventory.files)
    internal = graph["dependency_references"]
    external = sum(row["classification"] == "external"
                   for row in graph["references"])
    total_refs = internal + external
    cutoff_counts = {}
    for checkpoint in protocol["checkpoints"]:
        key = f"{checkpoint['model_id']}@{checkpoint['revision']}"
        cutoff_counts[key] = sum(
            target["first_add_date"] is not None
            and target["first_add_date"] > checkpoint["cutoff_date"]
            for target in targets)
    first_dates = sorted(file.first_add_date for file in inventory.files
                         if file.first_add_date)
    row = dict(
        repo=inventory.spec["repo"], language=inventory.spec["language"],
        locked_sha=inventory.locked_sha, locked_tree_oid=inventory.tree_oid,
        history_commit_count=len(inventory.history_commits),
        history_commits_sha256=sha256_sorted_json(
            sorted(inventory.history_commits)),
        n_files=len(inventory.files),
        n_files_too_short_for_block=n_files_too_short,
        n_files_skipped_non_utf8=inventory.skipped_non_utf8,
        source_bytes=total_bytes, source_codepoints=total_codepoints,
        bytes_per_codepoint=(total_bytes / total_codepoints
                             if total_codepoints else None),
        source_file_set_sha256=order_rows["shuffled"]["file_set_sha256"],
        orderings=order_rows,
        stream_bytes_topo=order_rows["topological"]["stream_bytes"],
        stream_bytes_shuffled=order_rows["shuffled"]["stream_bytes"],
        stream_bytes_reverse=order_rows["reverse-topological"]["stream_bytes"],
        metadata_bytes_fraction_by_q_stream_ordering=
            a0_detail["metadata_fraction"],
        graph_resolver=graph["resolver"],
        graph_resolver_evidence=graph["resolver_evidence"],
        graph_complete=graph["complete"],
        graph_failures=graph["failures"],
        graph_references_sha256=graph["references_sha256"],
        graph_edges_sha256=graph["dependency_edges_sha256"],
        dependency_references=graph["dependency_references"],
        dependency_references_resolved=
            graph["dependency_references_resolved"],
        dependency_edges=len(graph["dependency_edges"]),
        dependency_participating_files=
            len(graph["dependency_participating_files"]),
        dependency_cycle_files=n_cycle_files,
        graph_gate_ok=graph_gate(graph, len(inventory.files), constants,
                                 n_cycle_files),
        n_A0_origins_structural=len(a0["identities"]),
        A0_origin_identities_sha256=a0["identities_sha256"],
        A0_structural_occupancy_by_ordering_axis_rung=
            a0_detail["occupancy"],
        A0_max_contiguous_structural_decades_by_ordering_axis={},
        n_blocks_sampled=target_counts["sampled"],
        n_blocks_eligible=len(targets),
        n_distinct_files_with_blocks=len({target["file_path"]
                                          for target in targets}),
        target_identities_sha256=sha256_sorted_json([
            [target["file_path"], target["file_byte_start"],
             target["file_byte_end"], target["target_sha256"]]
            for target in targets]),
        same_file_prefix_exhaustion_bytes_distribution=a1["exhaustion"],
        same_file_context_fraction_by_c_ordering=a1["fractions"],
        n_blocks_structurally_complete_by_c_ordering_regime=a1["counts"],
        n_blocks_structurally_complete_by_c_ordering_shared_regimes=
            a1["shared_counts"],
        n_blocks_structurally_complete_by_c_all_orderings_regime=
            a1["all_ordering_counts"],
        n_blocks_disjoint_capacity_by_c_ordering=a1["disjoint"],
        overlap_fraction_by_c_ordering=a1["overlap"],
        n_blocks_rejected_cross_file_boundary=
            target_counts["cross_boundary"],
        n_blocks_rejected_too_short=target_counts["too_short"],
        n_blocks_rejected_realized_overlap=target_counts["realized_overlap"],
        n_blocks_dropped_comment=target_counts["comment"],
        n_blocks_dropped_neardup=target_counts["neardup"],
        external_import_fraction=(external / total_refs if total_refs else 0.0),
        shared_content_fraction_with_panel=shared_fraction,
        first_add_date_min=first_dates[0] if first_dates else None,
        first_add_date_max=first_dates[-1] if first_dates else None,
        n_blocks_post_cutoff_by_model=cutoff_counts,
    )
    row["structural_ranges"] = _repo_ranges(row, constants)
    row["A0_max_contiguous_structural_decades_by_ordering_axis"] = {
        ordering: {axis: row["structural_ranges"]["A0"][ordering][axis]
                         ["all"]["decades"]
                   for axis in AXES}
        for ordering in ORDERINGS}
    median_exhaustion = row["same_file_prefix_exhaustion_bytes_distribution"] \
        ["shuffled"]["median"]
    range_rule = constants["range"]
    exhaustion_min = (range_rule["exhaustion_multiplier"] * median_exhaustion
                      if median_exhaustion is not None else math.inf)
    row["min_rung_meeting_10x_exhaustion_bytes"] = _ceil_grid(
        constants["headline_grid_bytes"], exhaustion_min)
    row["implied_min_context_bytes_for_headline"] = implied_headline_rung(
        constants["headline_grid_bytes"], median_exhaustion, range_rule)
    return row


def build_artifact(*, protocol: dict[str, Any], protocol_sha256: str,
                   corpora_lock: dict[str, Any], corpora_lock_sha256: str,
                   corpora_root: str, generator: dict[str, str],
                   allow_synthetic: bool = False) -> dict[str, Any]:
    projected = validate_protocol(
        protocol, protocol_sha256=protocol_sha256,
        corpora_lock_sha256=corpora_lock_sha256,
        allow_synthetic=allow_synthetic)
    _exact_keys(corpora_lock, {"repos", "arxiv"}, "corpora_lock")
    inventories = [load_inventory(corpora_root, spec, corpora_lock["repos"])
                   for spec in projected["repositories"]]
    order_rows, cycle_counts, a0_rows, a0_details = {}, {}, {}, {}
    target_counts: dict[str, dict[str, int]] = {}
    for inventory in inventories:
        build_graph(inventory, corpora_root)
        order_rows[inventory.spec["repo"]], cycle_counts[inventory.spec["repo"]] = \
            build_orderings(inventory, projected["constants"])
        a0_rows[inventory.spec["repo"]], a0_details[inventory.spec["repo"]] = \
            a0_occupancy(inventory, projected["constants"])
        inventory.targets, target_counts[inventory.spec["repo"]] = \
            sample_targets(inventory, projected["constants"])
    screen_near_duplicates(inventories, projected["constants"])
    dependence_edges, shared_fractions = _dependence_edges(
        inventories, projected["constants"])
    repo_rows = []
    for inventory in inventories:
        repo = inventory.spec["repo"]
        a1 = a1_occupancy(inventory, projected["constants"])
        repo_rows.append(_repo_row(
            inventory, order_rows[repo], cycle_counts[repo], a0_rows[repo],
            a0_details[repo], a1, target_counts[repo],
            shared_fractions[repo], projected))
    repo_rows.sort(key=lambda row: row["repo"])
    language_rows = []
    for language in sorted({row["language"] for row in repo_rows}):
        group = [row for row in repo_rows if row["language"] == language]
        names = [row["repo"] for row in group]
        edges = [edge for edge in dependence_edges
                 if edge["language"] == language]
        components = _component_members(names, edges)
        implied = [row["implied_min_context_bytes_for_headline"]
                   for row in group
                   if row["implied_min_context_bytes_for_headline"] is not None]
        panel_bytes = sum(row["source_bytes"] for row in group)
        panel_codepoints = sum(row["source_codepoints"] for row in group)
        language_rows.append(dict(
            language=language, n_repos=len(group),
            n_independent_components=len(components),
            repository_dependence_edges=edges,
            component_membership=components,
            panel_bytes=panel_bytes,
            bytes_per_codepoint_mean=(panel_bytes / panel_codepoints
                                      if panel_codepoints else None),
            min_rung_meeting_10x_exhaustion_bytes=(min(
                row["min_rung_meeting_10x_exhaustion_bytes"] for row in group
                if row["min_rung_meeting_10x_exhaustion_bytes"] is not None)
                if any(row["min_rung_meeting_10x_exhaustion_bytes"] is not None
                       for row in group) else None),
            implied_min_context_bytes_for_headline=(min(implied)
                                                    if implied else None),
            structural_headline_reachable=False))
    decisions = recompute_decisions(repo_rows, language_rows, projected)
    for row in language_rows:
        row["structural_headline_reachable"] = decisions[
            "headline_conditions_structurally_reachable_by_language"][
                row["language"]]
    artifact = dict(
        schema=SCHEMA, generator=generator,
        protocol_sha256=protocol_sha256,
        corpora_lock_sha256=corpora_lock_sha256,
        frozen_constants_sha256=projected["frozen_constants_sha256"],
        repos=repo_rows, languages=language_rows,
        arm_b_events=[], arm_b_panels_by_model=[],
        stage_coverage={
            "arm_a_structural": "implemented",
            "arm_b_structural": "incomplete-fail-closed",
            "complete_p1a_claim": False,
            "power_decision_consumed": False,
            "power_gate_status": "separate-artifact-not-consumed",
            "loss_scoring_licensed_by_this_artifact": False,
        },
        unsupported_phases={"arm_b": {
            "status": "unsupported-fail-closed",
            "reason": ARM_B_UNSUPPORTED_REASON,
            "K5_K6_forced_false": True}},
        decisions=decisions)
    artifact["payload_sha256"] = sha256_sorted_json(artifact)
    return artifact


def _validate_ordering_composition(repo: dict[str, Any]) -> None:
    orderings = repo.get("orderings")
    if not isinstance(orderings, dict) or set(orderings) != set(ORDERINGS):
        _fail(f"{repo.get('repo')}: ordering set invalid")
    file_sets = {orderings[name].get("file_set_sha256") for name in ORDERINGS}
    n_files = {orderings[name].get("n_files") for name in ORDERINGS}
    stream_bytes = {orderings[name].get("stream_bytes") for name in ORDERINGS}
    if len(file_sets) != 1 or len(n_files) != 1 or len(stream_bytes) != 1:
        _fail(f"{repo.get('repo')}: ordering composition is not invariant")
    if next(iter(n_files)) != repo.get("n_files"):
        _fail(f"{repo.get('repo')}: ordering file count drift")
    if repo.get("source_file_set_sha256") != next(iter(file_sets)):
        _fail(f"{repo.get('repo')}: source file-set binding drift")


def _validate_repo_derived(repo: dict[str, Any],
                           protocol: dict[str, Any]) -> None:
    constants = protocol["constants"]
    spec = next((item for item in protocol["repositories"]
                 if item["repo"] == repo.get("repo")), None)
    if spec is None:
        _fail(f"{repo.get('repo')}: repo absent from protocol")
    graph_contract = spec["graph"]
    resolver = graph_contract["resolver"]
    evidence = repo.get("graph_resolver_evidence")
    evidence_ok = (
        isinstance(evidence, dict)
        and ((resolver == "python-ast-v1"
              and set(evidence) == {"python_version"}
              and isinstance(evidence["python_version"], str)
              and re.fullmatch(r"\d+\.\d+\.\d+", evidence["python_version"]))
             or (resolver == "lean-import-proposal-fail-closed-v1"
                 and evidence == {"status": "proposal-only-fail-closed"})
             or (resolver == "cpp-unavailable-fail-closed-v1"
                 and evidence == {"status": "unavailable-fail-closed"})))
    if repo.get("graph_resolver") != resolver or not evidence_ok:
        _fail(f"{repo.get('repo')}: graph resolver evidence drift")
    if resolver != "python-ast-v1" and repo.get("graph_complete") is not False:
        _fail(f"{repo.get('repo')}: proposal/unavailable graph not fail-closed")
    _validate_ordering_composition(repo)
    if (repo.get("stream_bytes_topo")
            != repo["orderings"]["topological"]["stream_bytes"]
            or repo.get("stream_bytes_shuffled")
            != repo["orderings"]["shuffled"]["stream_bytes"]
            or repo.get("stream_bytes_reverse")
            != repo["orderings"]["reverse-topological"]["stream_bytes"]):
        _fail(f"{repo.get('repo')}: stream-byte alias mismatch")
    denominator = repo.get("dependency_references")
    resolved = repo.get("dependency_references_resolved")
    edges = repo.get("dependency_edges")
    participants = repo.get("dependency_participating_files")
    n_files = repo.get("n_files")
    if not all(_is_int(value, 0) for value in
               (denominator, resolved, edges, participants, n_files)) \
            or resolved > denominator or participants > n_files:
        _fail(f"{repo.get('repo')}: invalid graph counts")
    gate = constants["graph_gate"]
    resolution_fraction = resolved / denominator if denominator else 0.0
    participation_fraction = participants / n_files if n_files else 0.0
    expected_graph_gate = bool(
        repo.get("graph_complete")
        and resolution_fraction >= gate["min_resolution_fraction"]
        and edges >= gate["min_edges"]
        and participation_fraction >= gate["min_participating_file_fraction"]
        and (not gate["require_acyclic"]
             or repo.get("dependency_cycle_files") == 0))
    if repo.get("graph_gate_ok") != expected_graph_gate:
        _fail(f"{repo.get('repo')}: graph gate does not recompute")
    expected_ordering_status = {
        "shuffled": "shuffled-primary",
        "topological": ("native-resolved" if expected_graph_gate
                        else "unavailable-fail-closed"),
        "reverse-topological": ("native-resolved" if expected_graph_gate
                                else "unavailable-fail-closed"),
    }
    if any(repo["orderings"][ordering].get("ordering_claim_status")
           != status for ordering, status in expected_ordering_status.items()):
        _fail(f"{repo.get('repo')}: ordering claim status is not fail-closed")
    if (not _is_int(repo.get("n_blocks_sampled"), 0)
            or not _is_int(repo.get("n_blocks_eligible"), 0)
            or any(not _is_int(repo.get(key), 0) for key in (
                "n_blocks_rejected_cross_file_boundary",
                "n_blocks_rejected_too_short",
                "n_blocks_rejected_realized_overlap",
                "n_blocks_dropped_comment", "n_blocks_dropped_neardup"))):
        _fail(f"{repo.get('repo')}: invalid target/drop counts")
    accounted = (repo["n_blocks_eligible"]
                 + repo["n_blocks_rejected_cross_file_boundary"]
                 + repo["n_blocks_rejected_too_short"]
                 + repo["n_blocks_rejected_realized_overlap"]
                 + repo["n_blocks_dropped_comment"]
                 + repo["n_blocks_dropped_neardup"])
    if accounted != repo["n_blocks_sampled"]:
        _fail(f"{repo.get('repo')}: target/drop accounting mismatch")
    grid_keys = [str(rung) for rung in constants["grid_bytes"]]
    a0 = repo.get("A0_structural_occupancy_by_ordering_axis_rung")
    metadata_fractions = repo.get(
        "metadata_bytes_fraction_by_q_stream_ordering")
    if (not isinstance(a0, dict) or set(a0) != set(ORDERINGS)
            or not isinstance(metadata_fractions, dict)
            or set(metadata_fractions) != set(ORDERINGS)):
        _fail(f"{repo.get('repo')}: A0 occupancy tables invalid")
    for ordering in ORDERINGS:
        if (set(a0[ordering]) != set(AXES)
                or set(metadata_fractions[ordering]) != set(grid_keys)):
            _fail(f"{repo.get('repo')}/{ordering}: A0 axis/grid drift")
        for axis in AXES:
            if set(a0[ordering][axis]) != set(grid_keys):
                _fail(f"{repo.get('repo')}/{ordering}/{axis}: A0 grid drift")
            prior: set[str] | None = None
            previous_rung = 0
            for rung, key in zip(constants["grid_bytes"], grid_keys):
                cell = a0[ordering][axis][key]
                _exact_keys(cell, {
                    "n_complete_units", "n_distinct_files", "source_bytes",
                    "metadata_bytes", "complete_identities",
                    "complete_identities_sha256", "source_files_by_identity",
                }, f"{repo.get('repo')}/{ordering}/{axis}/{key}")
                identities = cell["complete_identities"]
                file_rows = cell["source_files_by_identity"]
                if (not isinstance(identities, list)
                        or identities != sorted(set(identities))
                        or cell["complete_identities_sha256"]
                        != sha256_sorted_json(identities)
                        or cell["n_complete_units"] != len(identities)
                        or not isinstance(file_rows, list)
                        or len(file_rows) != len(identities)
                        or [row[0] for row in file_rows] != identities
                        or any(not isinstance(row, list) or len(row) != 2
                               or not isinstance(row[1], list)
                               or row[1] != sorted(set(row[1]))
                               for row in file_rows)
                        or cell["n_distinct_files"] != len({
                            path for _, paths in file_rows for path in paths})):
                    _fail(f"{repo.get('repo')}/{ordering}/{axis}/{key}: "
                          "A0 identity/file binding mismatch")
                current = set(identities)
                if prior is not None and not current <= prior:
                    _fail(f"{repo.get('repo')}/{ordering}/{axis}/{key}: "
                          "A0 origin cohort is not nested")
                width = rung - previous_rung
                if axis == "q_source":
                    if (cell["metadata_bytes"] != 0
                            or cell["source_bytes"] != width * len(identities)):
                        _fail(f"{repo.get('repo')}/{ordering}/{axis}/{key}: "
                              "source-axis byte accounting mismatch")
                elif (cell["source_bytes"] + cell["metadata_bytes"]
                      != width * len(identities)):
                    _fail(f"{repo.get('repo')}/{ordering}/{axis}/{key}: "
                          "stream-axis byte accounting mismatch")
                prior = current
                previous_rung = rung
        for key in grid_keys:
            stream_cell = a0[ordering]["q_stream"][key]
            denominator = (stream_cell["source_bytes"]
                           + stream_cell["metadata_bytes"])
            expected_fraction = (stream_cell["metadata_bytes"] / denominator
                                 if denominator else None)
            if metadata_fractions[ordering][key] != expected_fraction:
                _fail(f"{repo.get('repo')}/{ordering}/{key}: "
                      "metadata fraction mismatch")
    regimes = repo.get(
        "n_blocks_structurally_complete_by_c_ordering_regime")
    shared = repo.get(
        "n_blocks_structurally_complete_by_c_ordering_shared_regimes")
    all_orderings = repo.get(
        "n_blocks_structurally_complete_by_c_all_orderings_regime")
    if (not isinstance(regimes, dict) or set(regimes) != set(ORDERINGS)
            or not isinstance(shared, dict) or set(shared) != set(ORDERINGS)
            or not isinstance(all_orderings, dict)
            or set(all_orderings) != set(REGIMES)):
        _fail(f"{repo.get('repo')}: A1 cohort tables invalid")

    def identity_cell(cell: Any, label: str) -> set[tuple[Any, ...]]:
        _exact_keys(cell, {"n_blocks", "n_distinct_files",
                           "target_identities",
                           "target_identities_sha256"}, label)
        identities = cell["target_identities"]
        if (not isinstance(identities, list)
                or any(not isinstance(identity, list)
                       or len(identity) != 4 for identity in identities)
                or identities != sorted(identities)
                or len({tuple(identity) for identity in identities})
                != len(identities)
                or cell["target_identities_sha256"]
                != sha256_sorted_json(identities)
                or cell["n_blocks"] != len(identities)
                or cell["n_distinct_files"]
                != len({identity[0] for identity in identities})):
            _fail(f"{label}: target identity binding mismatch")
        return {tuple(identity) for identity in identities}

    regime_sets: dict[str, dict[str, dict[str, set[tuple[Any, ...]]]]] = {}
    for ordering in ORDERINGS:
        if (set(regimes[ordering]) != set(REGIMES)
                or set(shared[ordering]) != set(grid_keys)):
            _fail(f"{repo.get('repo')}/{ordering}: A1 grid/regime drift")
        regime_sets[ordering] = {}
        for regime in REGIMES:
            if set(regimes[ordering][regime]) != set(grid_keys):
                _fail(f"{repo.get('repo')}/{ordering}/{regime}: grid drift")
            regime_sets[ordering][regime] = {}
            prior: set[tuple[Any, ...]] | None = None
            for key in grid_keys:
                current = identity_cell(
                    regimes[ordering][regime][key],
                    f"{repo.get('repo')}/{ordering}/{regime}/{key}")
                if prior is not None and not current <= prior:
                    _fail(f"{repo.get('repo')}/{ordering}/{regime}/{key}: "
                          "cohort is not nested across c")
                regime_sets[ordering][regime][key] = current
                prior = current
        prior = None
        for key in grid_keys:
            current = identity_cell(
                shared[ordering][key],
                f"{repo.get('repo')}/{ordering}/shared/{key}")
            expected = (regime_sets[ordering]["with-file"][key]
                        & regime_sets[ordering]["cross-file-only"][key])
            if current != expected:
                _fail(f"{repo.get('repo')}/{ordering}/{key}: "
                      "shared cohort is not the exact regime intersection")
            if prior is not None and not current <= prior:
                _fail(f"{repo.get('repo')}/{ordering}/{key}: "
                      "shared cohort is not nested across c")
            prior = current
    for regime in REGIMES:
        if set(all_orderings[regime]) != set(grid_keys):
            _fail(f"{repo.get('repo')}/{regime}: all-ordering grid drift")
        prior = None
        for key in grid_keys:
            current = identity_cell(
                all_orderings[regime][key],
                f"{repo.get('repo')}/all-orderings/{regime}/{key}")
            expected = set.intersection(*(
                regime_sets[ordering][regime][key]
                for ordering in ORDERINGS))
            if current != expected:
                _fail(f"{repo.get('repo')}/{regime}/{key}: "
                      "all-ordering cohort is not the exact intersection")
            if prior is not None and not current <= prior:
                _fail(f"{repo.get('repo')}/{regime}/{key}: "
                      "all-ordering cohort is not nested across c")
            prior = current
    recomputed_ranges = _repo_ranges(repo, constants)
    if repo.get("structural_ranges") != recomputed_ranges:
        _fail(f"{repo.get('repo')}: structural ranges do not recompute")
    expected_a0 = {
        ordering: {axis: recomputed_ranges["A0"][ordering][axis]
                         ["all"]["decades"] for axis in AXES}
        for ordering in ORDERINGS}
    if repo.get("A0_max_contiguous_structural_decades_by_ordering_axis") \
            != expected_a0:
        _fail(f"{repo.get('repo')}: A0 range summary mismatch")
    exhaustion = repo.get(
        "same_file_prefix_exhaustion_bytes_distribution", {}).get(
            "shuffled", {}).get("median")
    expected_exhaustion_rung = (
        _ceil_grid(constants["headline_grid_bytes"],
                   constants["range"]["exhaustion_multiplier"] * exhaustion)
        if _is_number(exhaustion, 0) else None)
    expected_implied = implied_headline_rung(
        constants["headline_grid_bytes"], exhaustion
        if _is_number(exhaustion, 0) else None, constants["range"])
    if (repo.get("min_rung_meeting_10x_exhaustion_bytes")
            != expected_exhaustion_rung
            or repo.get("implied_min_context_bytes_for_headline")
            != expected_implied):
        _fail(f"{repo.get('repo')}: headline reach cascade mismatch")


def _validate_language_rows(artifact: dict[str, Any]) -> None:
    repos = artifact["repos"]
    languages = artifact["languages"]
    expected_names = sorted({repo["language"] for repo in repos})
    if (not isinstance(languages, list)
            or [row.get("language") for row in languages] != expected_names):
        _fail("artifact language set/order mismatch")
    for row in languages:
        language = row["language"]
        group = [repo for repo in repos if repo["language"] == language]
        names = [repo["repo"] for repo in group]
        edges = row.get("repository_dependence_edges")
        if not isinstance(edges, list):
            _fail(f"{language}: repository dependence edges are invalid")
        for edge in edges:
            if (not isinstance(edge, dict) or edge.get("language") != language
                    or not isinstance(edge.get("repos"), list)
                    or len(edge["repos"]) != 2
                    or edge["repos"] != sorted(edge["repos"])
                    or any(repo not in names for repo in edge["repos"])):
                _fail(f"{language}: malformed repository dependence edge")
        components = _component_members(names, edges)
        panel_bytes = sum(repo["source_bytes"] for repo in group)
        panel_codepoints = sum(repo["source_codepoints"] for repo in group)
        exhaustion_rungs = [
            repo["min_rung_meeting_10x_exhaustion_bytes"] for repo in group
            if repo["min_rung_meeting_10x_exhaustion_bytes"] is not None]
        implied = [repo["implied_min_context_bytes_for_headline"]
                   for repo in group
                   if repo["implied_min_context_bytes_for_headline"] is not None]
        if (row.get("n_repos") != len(group)
                or row.get("component_membership") != components
                or row.get("n_independent_components") != len(components)
                or row.get("panel_bytes") != panel_bytes
                or row.get("bytes_per_codepoint_mean")
                != (panel_bytes / panel_codepoints
                    if panel_codepoints else None)
                or row.get("min_rung_meeting_10x_exhaustion_bytes")
                != (min(exhaustion_rungs) if exhaustion_rungs else None)
                or row.get("implied_min_context_bytes_for_headline")
                != (min(implied) if implied else None)):
            _fail(f"{language}: language structural summary mismatch")


def validate_artifact(artifact: dict[str, Any], *, protocol: dict[str, Any],
                      protocol_sha256: str, corpora_lock: dict[str, Any],
                      corpora_lock_sha256: str,
                      allow_synthetic: bool = False) -> None:
    projected = validate_protocol(
        protocol, protocol_sha256=protocol_sha256,
        corpora_lock_sha256=corpora_lock_sha256,
        allow_synthetic=allow_synthetic)
    expected_root = {
        "schema", "generator", "protocol_sha256", "corpora_lock_sha256",
        "frozen_constants_sha256", "repos", "languages", "arm_b_events",
        "arm_b_panels_by_model", "stage_coverage", "unsupported_phases",
        "decisions", "payload_sha256",
    }
    _exact_keys(artifact, expected_root, "artifact")
    if artifact["schema"] != SCHEMA:
        _fail(f"artifact schema {artifact['schema']!r} != {SCHEMA!r}")
    if artifact["protocol_sha256"] != protocol_sha256 \
            or artifact["corpora_lock_sha256"] != corpora_lock_sha256 \
            or artifact["frozen_constants_sha256"] != \
            projected["frozen_constants_sha256"]:
        _fail("artifact input binding mismatch")
    payload = dict(artifact)
    claimed = payload.pop("payload_sha256")
    if claimed != sha256_sorted_json(payload):
        _fail("artifact payload_sha256 does not recompute")
    _exact_keys(artifact["generator"],
                {"source_commit", "source_tree_hash", "program"},
                "artifact.generator")
    if (not _valid_sha(artifact["generator"]["source_commit"])
            or not _valid_sha(artifact["generator"]["source_tree_hash"], (64,))
            or artifact["generator"]["program"] != PROGRAM):
        _fail("artifact generator is invalid")
    if not isinstance(artifact["repos"], list) \
            or [row.get("repo") for row in artifact["repos"]] != sorted(
                spec["repo"] for spec in projected["repositories"]):
        _fail("artifact repo set/order does not match protocol")
    lock_shas = {spec["repo"]: corpora_lock["repos"]
                  [spec["lock_key"]]["sha"]
                  for spec in projected["repositories"]}
    for repo in artifact["repos"]:
        if repo.get("locked_sha") != lock_shas.get(repo.get("repo")):
            _fail(f"{repo.get('repo')}: locked SHA mismatch")
        _validate_repo_derived(repo, projected)
    _validate_language_rows(artifact)
    if artifact["arm_b_events"] or artifact["arm_b_panels_by_model"]:
        _fail("Arm B rows present despite fail-closed unsupported mode")
    expected_coverage = {
        "arm_a_structural": "implemented",
        "arm_b_structural": "incomplete-fail-closed",
        "complete_p1a_claim": False,
        "power_decision_consumed": False,
        "power_gate_status": "separate-artifact-not-consumed",
        "loss_scoring_licensed_by_this_artifact": False,
    }
    if artifact["stage_coverage"] != expected_coverage:
        _fail("P1a stage-coverage marker mismatch")
    expected_unsupported = {"arm_b": {
        "status": "unsupported-fail-closed",
        "reason": ARM_B_UNSUPPORTED_REASON,
        "K5_K6_forced_false": True}}
    if artifact["unsupported_phases"] != expected_unsupported:
        _fail("Arm B fail-closed marker mismatch")
    decisions = recompute_decisions(artifact["repos"], artifact["languages"],
                                    projected)
    if artifact["decisions"] != decisions:
        _fail("stored structural decisions do not recompute")
    for language in artifact["languages"]:
        if language.get("structural_headline_reachable") != decisions[
                "headline_conditions_structurally_reachable_by_language"].get(
                    language.get("language")):
            _fail(f"{language.get('language')}: language reach mismatch")


def reproduce_and_compare(artifact: dict[str, Any], *, protocol: dict[str, Any],
                          protocol_sha256: str, corpora_lock: dict[str, Any],
                          corpora_lock_sha256: str, corpora_root: str,
                          allow_synthetic: bool = False) -> None:
    reproduced = build_artifact(
        protocol=protocol, protocol_sha256=protocol_sha256,
        corpora_lock=corpora_lock,
        corpora_lock_sha256=corpora_lock_sha256,
        corpora_root=corpora_root, generator=artifact["generator"],
        allow_synthetic=allow_synthetic)
    if reproduced != artifact:
        _fail("deep validation reproduction differs from artifact")


def _load_inputs(protocol_path: str, lock_path: str) \
        -> tuple[dict[str, Any], str, dict[str, Any], str]:
    protocol, protocol_sha = load_json(protocol_path, PROTOCOL_SCHEMA)
    lock, lock_sha = load_json(lock_path)
    return protocol, protocol_sha, lock, lock_sha


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    produce = sub.add_parser("produce", help="produce a new P1a artifact")
    validate = sub.add_parser("validate", help="validate an existing artifact")
    for command in (produce, validate):
        command.add_argument("--protocol", required=True)
        command.add_argument(
            "--protocol-sha256", required=True,
            help="externally frozen raw SHA256 of --protocol")
        command.add_argument("--corpora-lock", default="corpora_lock.json")
        command.add_argument("--corpora-root", required=True)
        command.add_argument("--allow-synthetic-protocol", action="store_true",
                             help=argparse.SUPPRESS)
    produce.add_argument("--out", required=True)
    validate.add_argument("--artifact", required=True)
    validate.add_argument("--deep", action="store_true",
                          help="reproduce from locked sources and compare")
    args = parser.parse_args()
    protocol, protocol_sha, lock, lock_sha = _load_inputs(
        args.protocol, args.corpora_lock)
    if (not _valid_sha(args.protocol_sha256, (64,))
            or protocol_sha != args.protocol_sha256):
        _fail("raw protocol file SHA256 differs from --protocol-sha256")
    if args.command == "produce":
        if not source_clean():
            _fail("source tree is dirty; refusing evidentiary census")
        generator = dict(source_commit=head_commit(),
                         source_tree_hash=source_tree_hash(), program=PROGRAM)
        artifact = build_artifact(
            protocol=protocol, protocol_sha256=protocol_sha,
            corpora_lock=lock, corpora_lock_sha256=lock_sha,
            corpora_root=args.corpora_root, generator=generator,
            allow_synthetic=args.allow_synthetic_protocol)
        validate_artifact(
            artifact, protocol=protocol, protocol_sha256=protocol_sha,
            corpora_lock=lock, corpora_lock_sha256=lock_sha,
            allow_synthetic=args.allow_synthetic_protocol)
        digest = write_new_json(args.out, artifact)
        print(f"V2C-P1A-DONE {os.path.abspath(args.out)} {digest}")
    else:
        artifact, _ = load_json(args.artifact, SCHEMA)
        validate_artifact(
            artifact, protocol=protocol, protocol_sha256=protocol_sha,
            corpora_lock=lock, corpora_lock_sha256=lock_sha,
            allow_synthetic=args.allow_synthetic_protocol)
        if args.deep:
            reproduce_and_compare(
                artifact, protocol=protocol, protocol_sha256=protocol_sha,
                corpora_lock=lock, corpora_lock_sha256=lock_sha,
                corpora_root=args.corpora_root,
                allow_synthetic=args.allow_synthetic_protocol)
        print(f"V2C-P1A-VALID {sha256_file(args.artifact)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_cli())
    except CensusError as err:
        print(f"V2C-P1A-ERROR: {err}", file=sys.stderr)
        raise SystemExit(2)
