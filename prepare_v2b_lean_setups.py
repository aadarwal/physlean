#!/usr/bin/env python3
"""Materialize exact Lake ModuleSetup JSON for the Lean boundary audit.

Lake's cached OLean artifacts do not necessarily retain ``*.setup.json``
files (the pinned mathlib checkout retains only a handful).  The non-buildable
Lake module ``setup`` facet is nevertheless queryable.  This producer queries
that facet in deterministic batches, validates one exact ModuleSetup object
per extraction module, writes canonical new-only setup files under POOL, and
publishes a source->setup index consumed by ``v2b_lean_boundaries.py``.

No model, salt, sample, or outcome is read.  This is CPU-only prospective
measurement infrastructure.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, artifact_binding, relative_source_path,
                        sha256_bytes, sha256_file,
                        sha256_json, sha256_sorted_json, write_new_json,
                        load_json)
from v2b_neardup import LEAN_EXTRACT_SCHEMA


SETUP_INDEX_SCHEMA = "v2b_lean_setup_index_v1"
REQUIRED_SETUP_KEYS = frozenset((
    "dynlibs", "importArts", "isModule", "name", "options", "plugins"))
OPTIONAL_SETUP_KEYS = frozenset(("package", "imports"))
IMPORT_KEYS = frozenset(("module", "importAll", "isExported", "isMeta"))
SETUP_INDEX_KEYS = frozenset((
    "schema", "repo", "language", "corpus_git_sha", "extraction",
    "corpus_root", "toolchain", "lean_toolchain_sha256", "lake", "lean",
    "n_modules", "n_batches", "batch_size", "setups", "setups_sha256",
    "rows", "rows_sha256", "batches", "batches_sha256", "n_artifacts",
    "artifacts", "artifacts_sha256"))
SETUP_ROW_KEYS = frozenset((
    "module", "source", "source_rel", "source_sha256", "setup",
    "setup_sha256", "setup_semantics_sha256", "batch_index"))
BATCH_ROW_KEYS = frozenset((
    "batch_index", "n_modules", "first_module", "last_module",
    "targets_sha256", "stdout_sha256", "stderr_sha256",
    "setup_rows_sha256"))
EXTRACTION_BINDING_KEYS = frozenset(("path", "sha256", "schema"))
EXECUTABLE_KEYS = frozenset(("path", "sha256", "version"))
GENERATOR_KEYS = frozenset(("source_commit", "source_tree_hash", "program"))
ARTIFACT_ROW_KEYS = frozenset(("path", "sha256", "roles"))
ARTIFACT_ROLES = frozenset(("import-artifact", "dynamic-library", "plugin"))


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(char in "0123456789abcdef" for char in value)


def _json_no_duplicates(text, where):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(f"duplicate ModuleSetup key {key!r} in "
                               f"{where}")
            value[key] = item
        return value

    def no_nonfinite(value):
        raise V2BError(f"non-finite ModuleSetup number {value} in {where}")

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates,
                           parse_constant=no_nonfinite)
    except (json.JSONDecodeError, V2BError) as err:
        raise V2BError(f"invalid Lake setup JSON in {where}: {err}") \
            from err
    if not isinstance(value, dict):
        raise V2BError(f"Lake setup result is not an object in {where}")
    return value


def validate_setup(value, expected_module, where):
    """Validate the exact stable ModuleSetup JSON surface used by 4.32/4.33."""
    def valid_plugin(plugin):
        if isinstance(plugin, str):
            return bool(plugin)
        if not isinstance(plugin, dict) \
                or set(plugin) not in ({"path"}, {"path", "initFn"}) \
                or not isinstance(plugin.get("path"), str) \
                or not plugin["path"]:
            return False
        init_fn = plugin.get("initFn")
        return init_fn is None or isinstance(init_fn, str) and bool(init_fn)

    def valid_import(entry):
        return isinstance(entry, dict) and set(entry) == IMPORT_KEYS \
            and isinstance(entry.get("module"), str) and bool(entry["module"]) \
            and all(type(entry.get(key)) is bool for key in (
                "importAll", "isExported", "isMeta"))

    def valid_import_artifacts(import_arts):
        def valid_groups(groups):
            if not isinstance(groups, list):
                return False
            flat = all(isinstance(path, str) and bool(path)
                       for path in groups)
            nested = all(isinstance(group, list)
                         and all(isinstance(path, str) and bool(path)
                                 for path in group)
                         for group in groups)
            return flat or nested

        return isinstance(import_arts, dict) and all(
            isinstance(module, str) and bool(module)
            and valid_groups(groups)
            for module, groups in import_arts.items())

    keys = set(value) if isinstance(value, dict) else set()
    if not REQUIRED_SETUP_KEYS <= keys \
            or keys - REQUIRED_SETUP_KEYS - OPTIONAL_SETUP_KEYS:
        raise V2BError(f"ModuleSetup key drift in {where}: {sorted(keys)}")
    if value.get("name") != expected_module \
            or type(value.get("isModule")) is not bool \
            or not isinstance(value.get("dynlibs"), list) \
            or any(not isinstance(path, str) or not path
                   for path in value["dynlibs"]) \
            or not isinstance(value.get("plugins"), list) \
            or any(not valid_plugin(plugin) for plugin in value["plugins"]) \
            or not valid_import_artifacts(value.get("importArts")) \
            or not isinstance(value.get("options"), dict) \
            or ("imports" in value
                and (not isinstance(value["imports"], list)
                     or any(not valid_import(entry)
                            for entry in value["imports"]))) \
            or ("package" in value
                and value["package"] is not None
                and (not isinstance(value["package"], str)
                     or not value["package"])):
        raise V2BError(f"malformed ModuleSetup for {expected_module} in "
                       f"{where}")
    return value


def setup_artifact_roles(value, where):
    """Return every external file the decoded ModuleSetup may read."""
    validate_setup(value, value.get("name"), where)
    roles = {}

    def add(path, role):
        if not isinstance(path, str) or not path:
            raise V2BError(f"empty/non-string {role} path in {where}")
        if not os.path.isabs(path):
            raise V2BError(f"relative {role} path in {where}: {path!r}")
        roles.setdefault(path, set()).add(role)

    for module, groups in value["importArts"].items():
        if not isinstance(module, str) or not module \
                or not isinstance(groups, list):
            raise V2BError(f"malformed importArts row in {where}")
        if all(isinstance(path, str) for path in groups):
            for path in groups:
                add(path, "import-artifact")
        else:
            for group in groups:
                if not isinstance(group, list) \
                        or any(not isinstance(path, str) or not path
                               for path in group):
                    raise V2BError(f"malformed importArts row in {where}")
                for path in group:
                    add(path, "import-artifact")
    for path in value["dynlibs"]:
        add(path, "dynamic-library")
    for plugin in value["plugins"]:
        add(plugin if isinstance(plugin, str) else plugin["path"], "plugin")
    return roles


def _artifact_rows(roles, workers=16):
    if type(workers) is not int or workers < 1:
        raise V2BError("artifact hash worker count must be positive")
    paths = sorted(roles)
    if not paths:
        raise V2BError("ModuleSetup closure contains no external artifacts")
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(paths))) as executor:
        digests = list(executor.map(sha256_file, paths))
    return [dict(path=path, sha256=digest,
                 roles=sorted(roles[path]))
            for path, digest in zip(paths, digests)]


def parse_query_stdout(stdout, expected_modules, where="lake query"):
    """One compact JSON object per requested target, in exact target order."""
    if not isinstance(stdout, str):
        raise V2BError("Lake setup stdout must be text")
    lines = stdout.splitlines()
    if len(lines) != len(expected_modules):
        raise V2BError(f"{where}: got {len(lines)} setup rows for "
                       f"{len(expected_modules)} modules")
    values = []
    for index, (line, module) in enumerate(zip(lines, expected_modules)):
        if not line.strip():
            raise V2BError(f"{where}: blank setup row[{index}]")
        values.append(validate_setup(
            _json_no_duplicates(line, f"{where} row[{index}]"), module,
            f"{where} row[{index}]"))
    return values


def _safe_module_relpath(module):
    if not isinstance(module, str) or not module:
        raise V2BError(f"invalid module name {module!r}")
    parts = module.split(".")
    if any(not part or part in (".", "..") or "/" in part
           or "\\" in part or "\x00" in part for part in parts):
        raise V2BError(f"unsafe module name {module!r}")
    return os.path.join(*parts) + ".setup.json"


def extraction_modules(extraction_path, corpus_root):
    binding, extraction = artifact_binding(extraction_path,
                                           LEAN_EXTRACT_SCHEMA)
    repo = extraction.get("repo")
    files = extraction.get("files")
    if not isinstance(repo, str) or not repo or not isinstance(files, list) \
            or not files:
        raise V2BError("malformed Lean extraction for setup planning")
    rows = []
    seen_modules = set()
    seen_sources = set()
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise V2BError(f"extraction file[{index}] is not an object")
        module = row.get("module")
        source = row.get("source")
        source_sha = row.get("source_sha256")
        if not isinstance(module, str) or not module \
                or not isinstance(source, str) or not source \
                or not _hex(source_sha):
            raise V2BError(f"malformed extraction file[{index}]")
        if module in seen_modules or source in seen_sources:
            raise V2BError(f"duplicate module/source mapping at {module}")
        seen_modules.add(module)
        seen_sources.add(source)
        rel = relative_source_path(corpus_root, source)
        if sha256_file(source) != source_sha:
            raise V2BError(f"source hash drift for {module}")
        rows.append(dict(module=module, source=os.path.abspath(source),
                         source_rel=rel, source_sha256=source_sha))
    rows.sort(key=lambda row: row["module"])
    return binding, repo, rows


def _run(args, cwd, env, timeout):
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              text=True, errors="strict", timeout=timeout,
                              check=False)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as err:
        raise V2BError(f"Lake setup query failed to execute: {err}") from err


def _git(corpus_root, *args):
    result = subprocess.run(["git", "-C", corpus_root, *args],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise V2BError(f"git {' '.join(args)} failed: "
                       f"{result.stderr.strip()[:300]}")
    return result.stdout


def _corpus_identity(corpus_root, expected_sha):
    actual = _git(corpus_root, "rev-parse", "HEAD").strip()
    dirty = _git(corpus_root, "status", "--porcelain",
                 "--untracked-files=all")
    if actual != expected_sha:
        raise V2BError(f"corpus revision drift: {actual} != {expected_sha}")
    if dirty.strip():
        raise V2BError("corpus checkout is dirty during setup query")
    return actual


def _publish_setup(path, value):
    """New-only publication, with deterministic identical-resume support."""
    if os.path.exists(path):
        existing, digest = load_json(path)
        if existing != value:
            raise V2BError(f"existing setup file disagrees: {path}")
        return digest
    return write_new_json(path, value)


def validate_setup_index(value, live_files=True, require_generator=False):
    """Validate one published setup index and all source/setup joins."""
    if not isinstance(value, dict):
        raise V2BError("setup index root is not an object")
    allowed_keys = (SETUP_INDEX_KEYS | {"generator"}) \
        if "generator" in value else SETUP_INDEX_KEYS
    if set(value) != allowed_keys \
            or value.get("schema") != SETUP_INDEX_SCHEMA \
            or value.get("language") != "lean" \
            or not isinstance(value.get("repo"), str) or not value["repo"] \
            or not _hex(value.get("corpus_git_sha"), 40) \
            or not isinstance(value.get("corpus_root"), str) \
            or not value["corpus_root"] \
            or not isinstance(value.get("toolchain"), str) \
            or not value["toolchain"] \
            or not _hex(value.get("lean_toolchain_sha256")):
        raise V2BError("setup index schema/key/identity drift")
    if require_generator and "generator" not in value:
        raise V2BError("published setup index lacks generator binding")
    if "generator" in value:
        generator = value["generator"]
        if not isinstance(generator, dict) or set(generator) != GENERATOR_KEYS \
                or not _hex(generator.get("source_commit"), 40) \
                or not _hex(generator.get("source_tree_hash")) \
                or generator.get("program") != \
                "prepare_v2b_lean_setups.py":
            raise V2BError("setup index generator binding drift")
    extraction = value.get("extraction")
    if not isinstance(extraction, dict) \
            or set(extraction) != EXTRACTION_BINDING_KEYS \
            or extraction.get("schema") != LEAN_EXTRACT_SCHEMA \
            or not isinstance(extraction.get("path"), str) \
            or not extraction["path"] or not _hex(extraction.get("sha256")):
        raise V2BError("setup index extraction binding drift")
    if live_files and sha256_file(extraction["path"]) != \
            extraction["sha256"]:
        raise V2BError("setup index extraction byte drift")
    for label in ("lake", "lean"):
        executable = value.get(label)
        if not isinstance(executable, dict) \
                or set(executable) != EXECUTABLE_KEYS \
                or not isinstance(executable.get("path"), str) \
                or not executable["path"] \
                or not _hex(executable.get("sha256")) \
                or not isinstance(executable.get("version"), str) \
                or not executable["version"]:
            raise V2BError(f"setup index {label} runtime drift")
        if live_files and sha256_file(executable["path"]) != \
                executable["sha256"]:
            raise V2BError(f"setup index {label} executable byte drift")
    setups, rows, batches, artifacts = (
        value.get("setups"), value.get("rows"), value.get("batches"),
        value.get("artifacts"))
    if not isinstance(setups, dict) or not setups \
            or value.get("setups_sha256") != sha256_sorted_json(setups) \
            or not isinstance(rows, list) or not rows \
            or value.get("rows_sha256") != sha256_sorted_json(rows) \
            or not isinstance(batches, list) or not batches \
            or value.get("batches_sha256") != sha256_sorted_json(batches) \
            or not isinstance(artifacts, list) or not artifacts \
            or value.get("artifacts_sha256") != \
            sha256_sorted_json(artifacts) \
            or type(value.get("n_artifacts")) is not int \
            or value["n_artifacts"] != len(artifacts) \
            or type(value.get("n_modules")) is not int \
            or value["n_modules"] != len(rows) \
            or type(value.get("n_batches")) is not int \
            or value["n_batches"] != len(batches) \
            or type(value.get("batch_size")) is not int \
            or value["batch_size"] < 1:
        raise V2BError("setup index table/count/hash drift")
    artifact_paths = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) \
                or set(artifact) != ARTIFACT_ROW_KEYS \
                or not isinstance(artifact.get("path"), str) \
                or not artifact["path"] \
                or not _hex(artifact.get("sha256")) \
                or not isinstance(artifact.get("roles"), list) \
                or not artifact["roles"] \
                or artifact["roles"] != sorted(artifact["roles"]) \
                or len(artifact["roles"]) != len(set(artifact["roles"])) \
                or any(role not in ARTIFACT_ROLES
                       for role in artifact["roles"]):
            raise V2BError(f"setup artifact row[{index}] drift")
        artifact_paths.append(artifact["path"])
    if artifact_paths != sorted(artifact_paths) \
            or len(artifact_paths) != len(set(artifact_paths)):
        raise V2BError("setup artifact path order/membership drift")
    if live_files:
        live_digests = _artifact_rows(
            {row["path"]: set(row["roles"]) for row in artifacts})
        if live_digests != artifacts:
            raise V2BError("setup artifact closure byte drift")
    modules = []
    sources = []
    projected_setups = {}
    projected_artifact_roles = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != SETUP_ROW_KEYS \
                or not isinstance(row.get("module"), str) or not row["module"] \
                or not isinstance(row.get("source"), str) or not row["source"] \
                or not isinstance(row.get("source_rel"), str) \
                or not row["source_rel"] \
                or not isinstance(row.get("setup"), str) or not row["setup"] \
                or not _hex(row.get("source_sha256")) \
                or not _hex(row.get("setup_sha256")) \
                or not _hex(row.get("setup_semantics_sha256")) \
                or type(row.get("batch_index")) is not int \
                or not 0 <= row["batch_index"] < len(batches):
            raise V2BError(f"setup index row[{index}] drift")
        modules.append(row["module"])
        sources.append(row["source"])
        projected_setups[row["source"]] = row["setup"]
        if live_files:
            setup_value, setup_sha = load_json(row["setup"])
            if sha256_file(row["source"]) != row["source_sha256"] \
                    or setup_sha != row["setup_sha256"] \
                    or sha256_sorted_json(setup_value) != \
                    row["setup_semantics_sha256"]:
                raise V2BError(f"setup index row[{index}] live-byte drift")
            validate_setup(setup_value, row["module"],
                           f"setup index row[{index}]")
            for path, roles in setup_artifact_roles(
                    setup_value, f"setup index row[{index}]").items():
                projected_artifact_roles.setdefault(path, set()).update(roles)
    if modules != sorted(modules) or len(modules) != len(set(modules)) \
            or len(sources) != len(set(sources)) \
            or setups != projected_setups:
        raise V2BError("setup index module/source mapping drift")
    if live_files:
        indexed = {row["path"]: row for row in artifacts}
        if set(indexed) != set(projected_artifact_roles) or any(
                indexed[path]["roles"] !=
                sorted(projected_artifact_roles[path])
                for path in projected_artifact_roles):
            raise V2BError("setup index artifact closure membership drift")
    batch_module_total = 0
    for index, batch in enumerate(batches):
        if not isinstance(batch, dict) or set(batch) != BATCH_ROW_KEYS \
                or batch.get("batch_index") != index \
                or type(batch.get("batch_index")) is not int \
                or type(batch.get("n_modules")) is not int \
                or batch["n_modules"] < 1 \
                or any(not isinstance(batch.get(key), str) or not batch[key]
                       for key in ("first_module", "last_module")) \
                or any(not _hex(batch.get(key)) for key in (
                    "targets_sha256", "stdout_sha256", "stderr_sha256",
                    "setup_rows_sha256")):
            raise V2BError(f"setup index batch[{index}] drift")
        members = [row for row in rows if row["batch_index"] == index]
        if len(members) != batch["n_modules"] \
                or members[0]["module"] != batch["first_module"] \
                or members[-1]["module"] != batch["last_module"]:
            raise V2BError(f"setup index batch[{index}] membership drift")
        batch_module_total += len(members)
    if batch_module_total != len(rows):
        raise V2BError("setup index batch coverage drift")
    if live_files:
        toolchain_path = os.path.join(value["corpus_root"], "lean-toolchain")
        if sha256_file(toolchain_path) != value["lean_toolchain_sha256"]:
            raise V2BError("setup index lean-toolchain byte drift")
    return rows


def build_setup_index(extraction_path, corpus_root, expected_corpus_sha,
                      elan_home, setup_dir, batch_size=128,
                      timeout=1800, artifact_workers=16):
    if type(batch_size) is not int or batch_size <= 0:
        raise V2BError("setup batch_size must be a positive integer")
    if type(timeout) is not int or timeout <= 0:
        raise V2BError("setup timeout must be a positive integer")
    if not _hex(expected_corpus_sha, 40):
        raise V2BError("expected corpus revision must be lowercase 40-hex")
    if type(artifact_workers) is not int or artifact_workers <= 0:
        raise V2BError("artifact_workers must be a positive integer")
    for name in ("LEAN_PATH", "LEAN_SRC_PATH"):
        if os.environ.get(name):
            raise V2BError(f"ambient {name} is forbidden for setup query")
    corpus_root = os.path.realpath(corpus_root)
    elan_home = os.path.realpath(elan_home)
    setup_dir = os.path.abspath(setup_dir)
    _corpus_identity(corpus_root, expected_corpus_sha)
    extraction_binding, repo, modules = extraction_modules(
        extraction_path, corpus_root)
    toolchain_path = os.path.join(corpus_root, "lean-toolchain")
    try:
        toolchain_raw = open(toolchain_path, "rb").read()
        toolchain = toolchain_raw.decode("utf-8").strip()
    except (OSError, UnicodeError) as err:
        raise V2BError(f"cannot read corpus lean-toolchain: {err}") from err
    if not toolchain or "\n" in toolchain or "\r" in toolchain:
        raise V2BError("corpus lean-toolchain is malformed")
    lake = os.path.join(elan_home, "bin", "lake")
    elan = os.path.join(elan_home, "bin", "elan")
    if not os.path.isfile(lake) or not os.access(lake, os.X_OK) \
            or not os.path.isfile(elan) or not os.access(elan, os.X_OK):
        raise V2BError("POOL Elan/Lake executable is absent")
    env = os.environ.copy()
    env["ELAN_HOME"] = elan_home
    version = _run([lake, "--version"], corpus_root, env, timeout)
    if version.returncode != 0 or not version.stdout.strip():
        raise V2BError(f"Lake version query failed: {version.stderr[:300]}")
    toolchain_env = dict(env, ELAN_TOOLCHAIN=toolchain)
    lean_which = _run([elan, "which", "lean"], corpus_root, toolchain_env,
                      timeout)
    if lean_which.returncode != 0 or not lean_which.stdout.strip():
        raise V2BError(f"Lean executable resolution failed: "
                       f"{lean_which.stderr[:300]}")
    lean = os.path.realpath(lean_which.stdout.strip())
    if not os.path.isfile(lean) or not os.access(lean, os.X_OK):
        raise V2BError(f"resolved Lean executable is absent: {lean}")
    lean_version = _run([lean, "--version"], corpus_root, toolchain_env,
                        timeout)
    if lean_version.returncode != 0 or not lean_version.stdout.strip():
        raise V2BError(f"Lean version query failed: "
                       f"{lean_version.stderr[:300]}")

    setups = {}
    rows = []
    batches = []
    artifact_roles = {}
    os.makedirs(setup_dir, exist_ok=True)
    for batch_index, first in enumerate(range(0, len(modules), batch_size)):
        batch = modules[first:first + batch_size]
        names = [row["module"] for row in batch]
        targets = [f"+{module}:setup" for module in names]
        result = _run([lake, "query", *targets, "--json"], corpus_root,
                      env, timeout)
        if result.returncode != 0:
            raise V2BError(f"Lake setup batch {batch_index} failed: "
                           f"{result.stderr.strip()[:1000]}")
        values = parse_query_stdout(result.stdout, names,
                                    where=f"batch[{batch_index}]")
        batch_rows = []
        for source_row, value in zip(batch, values):
            for path, roles in setup_artifact_roles(
                    value, f"batch[{batch_index}] {source_row['module']}").items():
                artifact_roles.setdefault(path, set()).update(roles)
            setup_path = os.path.join(
                setup_dir, _safe_module_relpath(source_row["module"]))
            digest = _publish_setup(setup_path, value)
            setups[source_row["source"]] = setup_path
            row = dict(**source_row, setup=os.path.abspath(setup_path),
                       setup_sha256=digest,
                       setup_semantics_sha256=sha256_sorted_json(value),
                       batch_index=batch_index)
            rows.append(row)
            batch_rows.append([source_row["module"], digest])
        batches.append(dict(
            batch_index=batch_index, n_modules=len(batch),
            first_module=names[0], last_module=names[-1],
            targets_sha256=sha256_json(targets),
            stdout_sha256=sha256_bytes(result.stdout.encode("utf-8")),
            stderr_sha256=sha256_bytes(result.stderr.encode("utf-8")),
            setup_rows_sha256=sha256_json(batch_rows)))

    if len(setups) != len(modules) or len(rows) != len(modules):
        raise AssertionError("setup index lost module/source membership")
    _corpus_identity(corpus_root, expected_corpus_sha)
    end_binding, _, end_modules = extraction_modules(extraction_path,
                                                      corpus_root)
    if end_binding["sha256"] != extraction_binding["sha256"] \
            or end_modules != modules:
        raise V2BError("extraction/source inputs drifted during setup query")
    rows.sort(key=lambda row: row["module"])
    artifacts = _artifact_rows(artifact_roles, workers=artifact_workers)
    return dict(
        schema=SETUP_INDEX_SCHEMA, repo=repo, language="lean",
        corpus_git_sha=expected_corpus_sha,
        extraction=dict(extraction_binding, schema=LEAN_EXTRACT_SCHEMA),
        corpus_root=corpus_root,
        toolchain=toolchain,
        lean_toolchain_sha256=sha256_bytes(toolchain_raw),
        lake=dict(path=os.path.abspath(lake), sha256=sha256_file(lake),
                  version=version.stdout.strip()),
        lean=dict(path=lean, sha256=sha256_file(lean),
                  version=lean_version.stdout.strip()),
        n_modules=len(rows), n_batches=len(batches), batch_size=batch_size,
        setups=setups, setups_sha256=sha256_sorted_json(setups),
        rows=rows, rows_sha256=sha256_sorted_json(rows),
        batches=batches, batches_sha256=sha256_sorted_json(batches),
        n_artifacts=len(artifacts), artifacts=artifacts,
        artifacts_sha256=sha256_sorted_json(artifacts))


def prepare(extraction_path, corpus_root, expected_corpus_sha, elan_home,
            setup_dir, batch_size=128, timeout=1800, artifact_workers=16):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    artifact = build_setup_index(
        extraction_path, corpus_root, expected_corpus_sha, elan_home,
        setup_dir, batch_size=batch_size, timeout=timeout,
        artifact_workers=artifact_workers)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during setup query")
    artifact["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="prepare_v2b_lean_setups.py")
    validate_setup_index(artifact, live_files=True, require_generator=True)
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--expected-corpus-sha", required=True)
    ap.add_argument("--elan-home", required=True)
    ap.add_argument("--setup-dir", required=True)
    ap.add_argument("--batch-size", type=int, default=128)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--artifact-workers", type=int, default=16)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        artifact = prepare(
            args.extraction, args.corpus_root, args.expected_corpus_sha,
            args.elan_home, args.setup_dir, args.batch_size, args.timeout,
            args.artifact_workers)
        digest = write_new_json(args.out, artifact)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[v2b-lean-setups] {artifact['repo']}: "
          f"{artifact['n_modules']} modules / {artifact['n_batches']} "
          f"query batches -> {args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
