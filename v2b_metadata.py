#!/usr/bin/env python3
"""V2-b A1-A3: candidate metadata, terciles, quotas, and the sample plan.

Implements DESIGN_V2 §15.A1-.A3 exactly: strict normalization of the two
frozen extraction schemas into one candidate table, module-level centrality
(§2), conservative all-record first-add provenance with vendor diagnostics,
the frozen tercile and proportional-Hamilton quota rules, and deterministic
sample-plan construction. Everything fails closed on schema, revision,
source-path, or git-history problems; nothing here reads model outputs.

The CLI can write v2b_candidates_v2 / v2b_sample_v2 artifacts, but drawing a
REAL study sample remains gated behind the PREREG boundary — synthetic use
only until that boundary is logged.
"""
import argparse
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from prep_streams import VENDOR_RE
from v2b_common import (CANDIDATES_SCHEMA, SAMPLE_SCHEMA, V2BError,
                        artifact_binding, identity_key, load_json,
                        relative_source_path, seeded_hash, sha256_json,
                        validate_identity, sha256_file, write_new_json)
from v2b_lean_boundaries import BOUNDARIES_SCHEMA, load_boundary_overlay

LEAN_EXTRACT_SCHEMA = "v2a_lean_extract_v3"
PYTHON_EXTRACT_SCHEMA = "v2a_python_extract_v3"
# §14.19: the sampling-priority seed stays in the v2a family (frozen there).
SAMPLING_SEED = "v2a:20260808"
# §15.A2: post/clean requires first_add STRICTLY LATER than this instant.
COHORT_CUTOFF = datetime(2024, 11, 12, 23, 59, 59, tzinfo=timezone.utc)
VENDOR_PATH_SEGMENTS = frozenset(("vendor", "third_party", "external"))
BULK_IMPORT_FILES = 100
CELL_LABELS = tuple(f"L{lt}-D{ct}-C{cohort}"
                    for lt in (1, 2, 3) for ct in (1, 2, 3)
                    for cohort in ("pre", "post"))
# V2C_FEASIBILITY_AMENDMENT Problem 3: the V2-c draw doubles the label
# space with a frozen test-module suffix (T = matches the amendment's
# stratum matcher on the dot-form module or slash-form source path,
# N = does not). The pilot/supplement 18-cell space is untouched.
CELL_LABELS_TEST_STRATUM = tuple(f"{label}-{suffix}"
                                 for label in CELL_LABELS
                                 for suffix in ("N", "T"))


# ------------------------------------------------------------------ git

def _git(corpus_root, *args):
    p = subprocess.run(["git", "-C", corpus_root, *args],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise V2BError(f"git {' '.join(args)} failed in {corpus_root}: "
                       f"{p.stderr.strip()[:300]}")
    return p.stdout


def _parse_iso(text, where):
    try:
        # Python 3.9 does not accept the ISO-8601 UTC designator while newer
        # runtimes do. Git may emit either spelling for %aI/%cI; they denote
        # the same instant and must not make provenance runtime-dependent.
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        stamp = datetime.fromisoformat(normalized)
    except ValueError as err:
        raise V2BError(f"{where}: bad ISO timestamp {text!r}: {err}") from err
    if stamp.tzinfo is None:
        raise V2BError(f"{where}: naive timestamp {text!r}")
    return stamp


def corpus_git_identity(corpus_root, expected_sha=None):
    """HEAD + git version, refusing shallow or drifted checkouts (§15.A2)."""
    if expected_sha is None:
        raise V2BError("expected corpus revision is mandatory")
    shallow = _git(corpus_root, "rev-parse",
                   "--is-shallow-repository").strip()
    if shallow != "false":
        raise V2BError(f"corpus checkout is shallow: {corpus_root}")
    head = _git(corpus_root, "rev-parse", "HEAD").strip()
    if expected_sha is not None and head != expected_sha:
        raise V2BError(f"corpus revision drift: HEAD {head} != "
                       f"expected {expected_sha}")
    dirty = _git(corpus_root, "status", "--porcelain",
                 "--untracked-files=all")
    if dirty.strip():
        raise V2BError(f"corpus checkout is dirty: {corpus_root}")
    version = _git(corpus_root, "--version").strip()
    roots = [_parse_iso(line, "root-commit")
             for line in _git(corpus_root, "log", "--max-parents=0",
                              "--format=%cI").splitlines() if line.strip()]
    if not roots:
        raise V2BError(f"no root commit found in {corpus_root}")
    return dict(corpus_git_sha=head, git_version=version,
                first_commit=min(roots))


def _commit_signals(corpus_root, commit, cache, cache_lock=None):
    def load():
        out = _git(corpus_root, "diff-tree", "--no-commit-id",
                   "--name-only", "--diff-filter=A", "-r", "--root", commit)
        subject = _git(corpus_root, "show", "-s", "--format=%s",
                       commit).rstrip("\n")
        n_added = sum(1 for line in out.splitlines() if line.strip())
        return dict(subject=subject, n_files_added=n_added,
                    subject_vendor=bool(VENDOR_RE.search(subject)),
                    bulk_import=n_added >= BULK_IMPORT_FILES)
    if cache_lock is None:
        if commit not in cache:
            cache[commit] = load()
        return cache[commit]
    # Holding this lock across the two short commit-level git reads avoids a
    # thundering herd when many files share one bulk-add commit. Per-file
    # --follow histories still run concurrently, which is the expensive part.
    with cache_lock:
        if commit not in cache:
            cache[commit] = load()
        return cache[commit]


def first_add_record(corpus_root, rel, first_commit, commit_cache,
                     cache_lock=None):
    """§15.A2: ALL add records; min over every author+committer timestamp;
    ties -> lexicographically smallest commit; anomaly RECORD-ONLY; vendor
    signals OR'd across every add commit."""
    def parse_records(out, mode):
        parsed = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) != 3:
                raise V2BError(f"{rel}: malformed {mode} record {line!r}")
            commit, a_raw, c_raw = parts
            parsed.append(dict(
                commit=commit, author_date=a_raw, committer_date=c_raw,
                author_stamp=_parse_iso(a_raw, f"{rel}:{commit}:author"),
                committer_stamp=_parse_iso(c_raw,
                                           f"{rel}:{commit}:committer")))
        return parsed

    def minimum_record(records):
        candidates = []
        for record in records:
            candidates.append((record["author_stamp"], record["commit"], 0,
                               "author", record["author_date"]))
            candidates.append((record["committer_stamp"], record["commit"],
                               1, "committer", record["committer_date"]))
        min_stamp = min(stamp for stamp, _, _, _, _ in candidates)
        tied = sorted({commit for stamp, commit, _, _, _ in candidates
                       if stamp == min_stamp})
        chosen = min((row for row in candidates if row[0] == min_stamp),
                     key=lambda row: (row[1], row[2], row[4]))
        return min_stamp, tied, chosen

    out = _git(corpus_root, "log", "--follow", "--find-renames=50%",
               "--diff-filter=A", "--format=%H|%aI|%cI", "--", rel)
    records = parse_records(out, "add")
    path_flag = bool(VENDOR_PATH_SEGMENTS
                     & set(p.lower() for p in rel.split("/")))
    if not records:
        history_out = _git(
            corpus_root, "log", "--follow", "--find-renames=50%",
            "--format=%H|%aI|%cI", "--", rel)
        history = parse_records(history_out, "history-witness")
        if not history:
            raise V2BError(f"{rel}: no add record and no history witness")
        min_stamp, tied, chosen = minimum_record(history)
        if min_stamp > COHORT_CUTOFF:
            raise V2BError(
                f"{rel}: no add record and earliest history witness "
                f"{min_stamp.isoformat()} is after cohort cutoff")
        _, chosen_commit, _, chosen_source, chosen_raw = chosen
        witness_row = next(row for row in history
                           if row["commit"] == chosen_commit)
        anomalous = any(row["author_stamp"] < first_commit
                        for row in history)
        return dict(
            timestamp=chosen_raw,
            timestamp_utc=min_stamp.astimezone(timezone.utc).isoformat(),
            timestamp_source=chosen_source, commit=chosen_commit,
            provenance_mode="no-add-pre-witness",
            exact_add_unresolved=True, n_add_records=0,
            n_history_records=len(history), n_tied_commits=len(tied),
            history_witness=dict(
                commit=chosen_commit,
                author_date=witness_row["author_date"],
                committer_date=witness_row["committer_date"],
                selected_timestamp=chosen_raw,
                selected_source=chosen_source),
            author_date_anomalous=anomalous,
            # Unknown add provenance is conservatively excluded by every
            # vendor-clean sensitivity; it can never look falsely clean.
            vendor_flagged=True, vendor_unknown=True,
            path_vendor=path_flag, per_commit_signals=[])
    min_stamp, tied, chosen = minimum_record(records)
    _, chosen_commit, _, chosen_source, chosen_raw = chosen

    anomalous = any(r["author_stamp"] < first_commit for r in records)
    per_commit = []
    for r in records:
        signals = _commit_signals(corpus_root, r["commit"], commit_cache,
                                  cache_lock=cache_lock)
        per_commit.append(dict(commit=r["commit"],
                               author_date=r["author_date"],
                               committer_date=r["committer_date"],
                               path_vendor=path_flag, **signals))
    vendor = path_flag or any(s["subject_vendor"] or s["bulk_import"]
                              for s in per_commit)
    return dict(
        timestamp=chosen_raw,
        timestamp_utc=min_stamp.astimezone(timezone.utc).isoformat(),
        timestamp_source=chosen_source, commit=chosen_commit,
        provenance_mode="exact-add", exact_add_unresolved=False,
        n_add_records=len(records),
        n_history_records=0, history_witness=None,
        n_tied_commits=len(tied),
        author_date_anomalous=anomalous,
        vendor_flagged=vendor, vendor_unknown=False, path_vendor=path_flag,
        per_commit_signals=per_commit)


def cohort_of(record):
    """post/clean iff STRICTLY LATER than the frozen cutoff instant."""
    if not isinstance(record, dict) \
            or not isinstance(record.get("timestamp_utc"), str):
        raise V2BError("first-add record lacks timestamp_utc")
    stamp = _parse_iso(record["timestamp_utc"], "first_add")
    mode = record.get("provenance_mode")
    if mode == "exact-add":
        if record.get("exact_add_unresolved") is not False \
                or not isinstance(record.get("n_add_records"), int) \
                or isinstance(record.get("n_add_records"), bool) \
                or record["n_add_records"] < 1:
            raise V2BError("malformed exact-add provenance record")
        return "post" if stamp > COHORT_CUTOFF else "pre"
    if mode == "no-add-pre-witness":
        if record.get("exact_add_unresolved") is not True \
                or record.get("n_add_records") != 0 \
                or not isinstance(record.get("history_witness"), dict) \
                or stamp > COHORT_CUTOFF:
            raise V2BError("malformed no-add PRE witness record")
        return "pre"
    raise V2BError(f"unknown first-add provenance mode {mode!r}")


# --------------------------------------------------- extraction -> targets

def _lean_targets(extraction, boundaries):
    corpus_modules = {f["module"] for f in extraction["files"]}
    render = extraction.get("graph", {}).get(
        "internal_renderability_by_target")
    if not isinstance(render, dict):
        raise V2BError("lean extraction lacks internal_renderability_by_"
                       "target (schema v3 requires it)")
    module_importers = {}
    for f in extraction["files"]:
        for imp in f.get("direct_imports", []):
            tgt = imp.get("module")
            if tgt in corpus_modules and tgt != f["module"]:
                module_importers.setdefault(tgt, set()).add(f["module"])
    decl_sources = {}
    for e in extraction["graph"]["edges"]:
        if len(e) != 4:
            raise V2BError(f"lean edge is not a quadruple: {e!r}")
        decl_sources.setdefault((e[2], e[3]), set()).add((e[0], e[1]))
    out = []
    for f in extraction["files"]:
        for name, d in f["decls"].items():
            identity = validate_identity("lean", (f["module"], name))
            key = identity_key("lean", identity)
            boundary = boundaries.get(key)
            if not isinstance(boundary, dict):
                raise V2BError(f"Lean boundary overlay lacks {key}")
            if not (d.get("eligible_kind") and d.get("selection_contained")
                    and boundary.get("status") == "resolved"
                    and boundary.get("split_kind") is not None):
                continue
            counts = render.get(f["module"], {}).get(name)
            coverage = None
            if counts and counts.get("n_internal_occurrences"):
                coverage = (counts["n_renderable_occurrences"]
                            / counts["n_internal_occurrences"])
            out.append(dict(
                identity=list(identity), module=f["module"],
                source=f["source"], kind=d["kind"],
                body_bytes=boundary["body_bytes"],
                span_id=boundary["span_id"],
                renderability_coverage=coverage,
                docstring_bytes=None, duplicate_stratum=False,
                module_in_degree=len(module_importers.get(f["module"], ())),
                decl_in_degree=len(decl_sources.get(tuple(identity), ()))))
    return out


def _python_targets(extraction):
    corpus_modules = sorted((f["module"] for f in extraction["files"]),
                            key=len, reverse=True)
    module_importers = {}
    for f in extraction["files"]:
        hit_modules = set()
        for dotted in f.get("imports", {}).values():
            for cm in corpus_modules:
                if dotted == cm or dotted.startswith(cm + "."):
                    if cm != f["module"]:
                        hit_modules.add(cm)
                    break
        for cm in hit_modules:
            module_importers.setdefault(cm, set()).add(f["module"])
    decl_sources = {}
    for e in extraction["graph"]["edges"]:
        if len(e) != 6:
            raise V2BError(f"python edge is not a sextuple: {e!r}")
        decl_sources.setdefault(tuple(e[3:]), set()).add(tuple(e[:3]))
    out = []
    for f in extraction["files"]:
        for t in f["targets"]:
            identity = validate_identity("python", t["identity"])
            if (identity[0] != f["module"] or identity[1] != t["name"]
                    or identity[2] != t["start_byte"]):
                raise V2BError(f"inconsistent python identity {identity!r}")
            out.append(dict(
                identity=list(identity), module=f["module"],
                source=f["source"], kind=t["kind"],
                body_bytes=t["body_bytes"],
                renderability_coverage=None,
                docstring_bytes=t.get("docstring_bytes", 0),
                duplicate_stratum=t["binding_count"] > 1,
                module_in_degree=len(module_importers.get(f["module"], ())),
                decl_in_degree=len(decl_sources.get(tuple(identity), ()))))
    return out


# ---------------------------------------------------- terciles + quotas

def tercile_cutpoints(values):
    """§15.A1: q1 at floor((n-1)/3), q2 at floor(2*(n-1)/3), sorted asc."""
    if not values:
        raise V2BError("tercile_cutpoints on empty population")
    ordered = sorted(values)
    n = len(ordered)
    return ordered[(n - 1) // 3], ordered[2 * (n - 1) // 3]


def tercile(value, q1, q2):
    return 1 if value <= q1 else 2 if value <= q2 else 3


def allocate_quotas(populations, n, labels=CELL_LABELS):
    """§15.A1 proportional Hamilton over a fixed label space (the frozen
    18 cells by default; the V2-c test-stratum draw passes the doubled
    space). Floors first; remaining seats by descending fractional
    remainder with ties broken by ascending cell label; a cell never
    receives more than its population; shortfall is never rebalanced."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise V2BError(f"invalid sample size {n!r}")
    unknown = sorted(set(populations) - set(labels))
    if unknown:
        raise V2BError(f"unknown stratum cells {unknown}")
    for label, population in populations.items():
        if not isinstance(population, int) or isinstance(population, bool) \
                or population < 0:
            raise V2BError(f"invalid population for {label}: {population!r}")
    total = sum(populations.get(label, 0) for label in labels)
    if total == 0:
        raise V2BError("empty candidate population")
    quot_rem = {label: divmod(n * populations.get(label, 0), total)
                for label in labels}
    quotas = {label: quot_rem[label][0] for label in labels}
    remaining = n - sum(quotas.values())
    order = sorted(labels,
                   key=lambda label: (-quot_rem[label][1], label))
    for label in order[:remaining]:
        quotas[label] += 1
    return quotas


def build_sample_plan(candidates, n, exclude_keys=frozenset(),
                      test_stratum=False):
    """Deterministic §15.A1 plan from a candidates artifact VALUE. Pure:
    the caller decides whether writing it is a real draw.

    exclude_keys (§15.A14): identity keys removed from the SELECTION pool
    only — the V2-c pilot-exclusion path. Cutpoints, strata, priorities,
    and per-target validation still run over the FULL sealed table, so
    the frozen strata never shift; quotas and per-cell fills see the
    excluded pool. Every excluded key must exist in the table."""
    if candidates.get("schema") != CANDIDATES_SCHEMA:
        raise V2BError(f"not a candidates artifact: "
                       f"{candidates.get('schema')!r}")
    repo, language = candidates.get("repo"), candidates.get("language")
    if not isinstance(repo, str) or not repo:
        raise V2BError("candidates artifact lacks repo")
    if language not in ("lean", "python"):
        raise V2BError("candidates artifact lacks supported language")
    boundary_binding = candidates.get("lean_boundaries")
    structural = candidates.get("structural_evidence")
    structural_boundary = structural.get("lean_boundaries") \
        if isinstance(structural, dict) else None
    if language == "lean":
        if not isinstance(boundary_binding, dict) \
                or boundary_binding.get("schema") != BOUNDARIES_SCHEMA \
                or not isinstance(boundary_binding.get("path"), str) \
                or not boundary_binding["path"] \
                or not isinstance(boundary_binding.get("sha256"), str) \
                or len(boundary_binding["sha256"]) != 64 \
                or structural_boundary != boundary_binding:
            raise V2BError("Lean candidates lack their exact boundary "
                           "artifact binding")
    elif boundary_binding is not None or structural_boundary is not None:
        raise V2BError("Python candidates carry a forbidden Lean boundary "
                       "binding")
    targets = candidates.get("targets")
    if not isinstance(targets, list) or candidates.get("n_candidates") != \
            len(targets):
        raise V2BError("candidate count/table mismatch")
    cutpoints = candidates.get("tercile_cutpoints")
    if not isinstance(cutpoints, dict):
        raise V2BError("candidates artifact lacks tercile cutpoints")
    length_cuts = cutpoints.get("body_bytes")
    degree_cuts = cutpoints.get("module_in_degree")
    if not isinstance(length_cuts, list) or len(length_cuts) != 2 \
            or not isinstance(degree_cuts, list) or len(degree_cuts) != 2:
        raise V2BError("malformed candidate tercile cutpoints")
    if length_cuts[0] > length_cuts[1] \
            or degree_cuts[0] > degree_cuts[1]:
        raise V2BError("candidate tercile cutpoints are reversed")
    raw_body_bytes = [t.get("body_bytes") for t in targets
                      if isinstance(t, dict)]
    raw_module_degrees = [t.get("module_in_degree") for t in targets
                          if isinstance(t, dict)]
    if len(raw_body_bytes) != len(targets) \
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value < 0 for value in raw_body_bytes) \
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value < 0 for value in raw_module_degrees):
        raise V2BError("candidate covariate table is malformed")
    if tuple(length_cuts) != tercile_cutpoints(raw_body_bytes) \
            or tuple(degree_cuts) != tercile_cutpoints(raw_module_degrees):
        raise V2BError("candidate tercile cutpoints do not recompute")
    by_cell = {label: [] for label in
               (CELL_LABELS_TEST_STRATUM if test_stratum
                else CELL_LABELS)}
    seen = set()
    provenance_by_file = {}
    for t in targets:
        if not isinstance(t, dict):
            raise V2BError("candidate target is not an object")
        identity = validate_identity(language, t.get("identity"))
        key = identity_key(language, identity)
        if key in seen:
            raise V2BError(f"duplicate candidate identity {identity!r}")
        seen.add(key)
        span_id = t.get("span_id")
        if language == "lean":
            if not isinstance(span_id, str) or len(span_id) != 64 \
                    or any(ch not in "0123456789abcdef" for ch in span_id):
                raise V2BError(f"Lean candidate lacks span_id: {identity!r}")
        elif span_id is not None:
            raise V2BError(f"Python candidate carries Lean span_id: "
                           f"{identity!r}")
        cell = t.get("cell")
        if cell not in by_cell:
            raise V2BError(f"candidate has invalid cell {cell!r}")
        body_bytes, module_degree = t.get("body_bytes"), \
            t.get("module_in_degree")
        if not isinstance(body_bytes, int) or isinstance(body_bytes, bool) \
                or body_bytes < 0 \
                or not isinstance(module_degree, int) \
                or isinstance(module_degree, bool) or module_degree < 0:
            raise V2BError(f"candidate covariate drift for {identity!r}")
        cohort = cohort_of(t.get("first_add", {}))
        source_rel = t.get("source_rel")
        if not isinstance(source_rel, str) or not source_rel:
            raise V2BError(f"candidate lacks source_rel for {identity!r}")
        provenance = t["first_add"]
        previous = provenance_by_file.setdefault(source_rel, provenance)
        if previous != provenance:
            raise V2BError(f"first-add provenance differs within {source_rel}")
        expected_strata = dict(
            length_tercile=tercile(body_bytes, *length_cuts),
            centrality_tercile=tercile(module_degree, *degree_cuts),
            cohort=cohort)
        expected_cell = (f"L{expected_strata['length_tercile']}-"
                         f"D{expected_strata['centrality_tercile']}-"
                         f"C{cohort}")
        if t.get("strata") != expected_strata or cell != expected_cell \
                or t.get("cohort") != cohort:
            raise V2BError(f"candidate stratum/cell drift for {identity!r}")
        expected_priority = seeded_hash(SAMPLING_SEED, repo, *identity)
        if t.get("priority") != expected_priority:
            raise V2BError(f"candidate priority drift for {identity!r}")
        if key in exclude_keys:
            continue                     # §15.A14: validated, never selected
        if test_stratum:
            from v2b_v2c_governance import TEST_STRATUM_RE
            module_path = str(identity[0])
            source_path = str(t.get("source_rel") or "")
            suffix = "T" if (TEST_STRATUM_RE.search(module_path)
                             or TEST_STRATUM_RE.search(source_path)) \
                else "N"
            by_cell[f"{cell}-{suffix}"].append(t)
        else:
            by_cell[cell].append(t)
    expected_mode_counts = {"exact-add": 0, "no-add-pre-witness": 0}
    for provenance in provenance_by_file.values():
        expected_mode_counts[provenance["provenance_mode"]] += 1
    expected_fallback_files = sorted(
        rel for rel, provenance in provenance_by_file.items()
        if provenance["provenance_mode"] == "no-add-pre-witness")
    if candidates.get("first_add_provenance_file_counts") != \
            expected_mode_counts \
            or candidates.get("no_add_pre_witness_files") != \
            expected_fallback_files:
        raise V2BError("candidate first-add provenance summary drift")
    missing_exclusions = set(exclude_keys) - seen
    if missing_exclusions:
        raise V2BError(f"excluded identity keys absent from the candidate "
                       f"table: {sorted(missing_exclusions)[:3]}")
    populations = {label: len(rows) for label, rows in by_cell.items()}
    active_labels = CELL_LABELS_TEST_STRATUM if test_stratum \
        else CELL_LABELS
    quotas = allocate_quotas(populations, n, labels=active_labels)
    chosen, fills, shortfalls = [], {}, {}
    for label in active_labels:
        rows = sorted(by_cell[label], key=lambda t: t["priority"])
        take = min(quotas[label], len(rows))
        fills[label] = take
        if take < quotas[label]:
            shortfalls[label] = quotas[label] - take
        for target in rows[:take]:
            selected = dict(identity=target["identity"], cell=label,
                            priority=target["priority"])
            if language == "lean":
                selected["span_id"] = target["span_id"]
            chosen.append(selected)
    return dict(schema=SAMPLE_SCHEMA, repo=repo,
                language=language, n_requested=n,
                n_excluded=len(exclude_keys),
                excluded_keys_sha256=sha256_json(sorted(exclude_keys)),
                n_selected=len(chosen), quota_table=quotas,
                cell_populations=populations, cell_fills=fills,
                shortfalls=shortfalls,
                unsampled_cells=sorted(
                    label for label in active_labels
                    if quotas[label] == 0 and populations[label] > 0),
                test_stratum=bool(test_stratum),
                targets=chosen)


# ------------------------------------------------------------ assembly

def build_candidate_table(extraction_path, corpus_root, repo,
                          expected_corpus_sha=None, workers=1,
                          lean_boundaries_path=None):
    binding, extraction = artifact_binding(extraction_path)
    schema = extraction.get("schema")
    if schema == LEAN_EXTRACT_SCHEMA:
        if not lean_boundaries_path:
            raise V2BError("Lean candidates require the parser-backed "
                           "body-boundary artifact")
        boundary_binding, _boundary_artifact, boundary_index = \
            load_boundary_overlay(lean_boundaries_path, extraction_path,
                                  expected_repo=repo)
        language, rows = "lean", _lean_targets(extraction, boundary_index)
    elif schema == PYTHON_EXTRACT_SCHEMA:
        if lean_boundaries_path:
            raise V2BError("Python candidates forbid a Lean boundary "
                           "artifact")
        boundary_binding = None
        language, rows = "python", _python_targets(extraction)
    else:
        raise V2BError(f"unsupported extraction schema {schema!r}")
    if extraction.get("repo") != repo:
        raise V2BError(f"extraction repo {extraction.get('repo')!r} != "
                       f"{repo!r}")
    files = extraction.get("files")
    if not isinstance(files, list) or not files:
        raise V2BError("extraction has no source files")
    seen_modules, seen_sources = set(), set()
    for index, source_record in enumerate(files):
        if not isinstance(source_record, dict):
            raise V2BError(f"extraction file[{index}] is not an object")
        module, source = source_record.get("module"), source_record.get("source")
        if not isinstance(module, str) or not module or module in seen_modules:
            raise V2BError(f"missing/duplicate extraction module {module!r}")
        seen_modules.add(module)
        if not isinstance(source, str) or source in seen_sources:
            raise V2BError(f"missing/duplicate extraction source {source!r}")
        seen_sources.add(source)
        relative_source_path(corpus_root, source)
        expected_source_sha = source_record.get("source_sha256")
        if not isinstance(expected_source_sha, str) \
                or len(expected_source_sha) != 64 \
                or any(c not in "0123456789abcdef"
                       for c in expected_source_sha):
            raise V2BError(f"{module}: invalid extraction source hash")
        got_source_sha = sha256_file(source)
        if got_source_sha != expected_source_sha:
            raise V2BError(f"{module}: live source hash drift")
    if not rows:
        raise V2BError("no eligible candidates in extraction")

    if not isinstance(workers, int) or isinstance(workers, bool) \
            or not 1 <= workers <= 64:
        raise V2BError(f"invalid first-add worker count {workers!r}")
    ident = corpus_git_identity(corpus_root, expected_corpus_sha)
    commit_cache, first_add_cache = {}, {}
    rel_by_source = {row["source"]: relative_source_path(
        corpus_root, row["source"]) for row in rows}
    unique_rels = sorted(set(rel_by_source.values()))
    cache_lock = threading.Lock()

    def read_first_add(rel):
        return rel, first_add_record(corpus_root, rel,
                                     ident["first_commit"], commit_cache,
                                     cache_lock=cache_lock)

    if workers == 1:
        first_add_cache.update(read_first_add(rel) for rel in unique_rels)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            first_add_cache.update(executor.map(read_first_add, unique_rels))
    for row in rows:
        rel = rel_by_source[row["source"]]
        row["source_rel"] = rel
        row["first_add"] = first_add_cache[rel]
        row["cohort"] = cohort_of(first_add_cache[rel])
        del row["source"]

    first_add_mode_counts = {"exact-add": 0, "no-add-pre-witness": 0}
    for record in first_add_cache.values():
        first_add_mode_counts[record["provenance_mode"]] += 1
    no_add_pre_witness_files = sorted(
        rel for rel, record in first_add_cache.items()
        if record["provenance_mode"] == "no-add-pre-witness")

    q1_len, q2_len = tercile_cutpoints([r["body_bytes"] for r in rows])
    q1_deg, q2_deg = tercile_cutpoints([r["module_in_degree"] for r in rows])
    for row in rows:
        lt = tercile(row["body_bytes"], q1_len, q2_len)
        ct = tercile(row["module_in_degree"], q1_deg, q2_deg)
        row["strata"] = dict(length_tercile=lt, centrality_tercile=ct,
                             cohort=row["cohort"])
        row["cell"] = f"L{lt}-D{ct}-C{row['cohort']}"
        row["priority"] = seeded_hash(SAMPLING_SEED, repo, *row["identity"])
    rows.sort(key=lambda r: identity_key(language, r["identity"]))
    return dict(schema=CANDIDATES_SCHEMA, repo=repo, language=language,
                extraction=binding,
                lean_boundaries=boundary_binding,
                corpus_git_sha=ident["corpus_git_sha"],
                git_version=ident["git_version"],
                first_add_workers=workers,
                first_add_provenance_file_counts=first_add_mode_counts,
                no_add_pre_witness_files=no_add_pre_witness_files,
                repository_first_commit_utc=(
                    ident["first_commit"].astimezone(timezone.utc).isoformat()),
                cohort_cutoff=COHORT_CUTOFF.isoformat(),
                tercile_cutpoints=dict(body_bytes=[q1_len, q2_len],
                                       module_in_degree=[q1_deg, q2_deg]),
                n_candidates=len(rows), targets=rows)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--extraction", required=True)
    c.add_argument("--corpus-root", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--expected-corpus-sha", required=True)
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--lean-boundaries")
    c.add_argument("--allow-unbound-dev", action="store_true")
    c.add_argument("--out", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--candidates", required=True)
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--out", required=True)
    s.add_argument("--allow-unbound-dev", action="store_true")
    args = ap.parse_args()
    if args.cmd == "candidates":
        if not args.allow_unbound_dev:
            raise SystemExit("FATAL: unbound candidates CLI is synthetic/dev "
                             "only; production uses prepare_v2b_candidates.py")
        table = build_candidate_table(args.extraction, args.corpus_root,
                                      args.repo, args.expected_corpus_sha,
                                      workers=args.workers,
                                      lean_boundaries_path=
                                      args.lean_boundaries)
        digest = write_new_json(args.out, table)
        print(f"[v2b-candidates] {args.repo}: {table['n_candidates']} "
              f"candidates -> {args.out} ({digest[:12]})")
    else:
        if not args.allow_unbound_dev:
            raise SystemExit("FATAL: sample draw requires the future bound "
                             "V2-b sampler; unbound CLI is synthetic/dev only")
        value, digest = load_json(args.candidates,
                                  schema=CANDIDATES_SCHEMA)
        plan = build_sample_plan(value, args.n)
        plan["candidates_sha256"] = digest
        out_digest = write_new_json(args.out, plan)
        print(f"[v2b-sample] {plan['repo']}: {plan['n_selected']}/"
              f"{plan['n_requested']} -> {args.out} ({out_digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
