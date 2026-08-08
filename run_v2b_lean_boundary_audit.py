#!/usr/bin/env python3
"""Run the parser-backed Lean body-boundary audit, requeue safely.

One isolated Lean process handles one source module.  Completed module runs
are immutable directories named by their invocation binding; a requeue only
reuses a directory after revalidating its exact manifest, stdout, stderr,
runtime binding, and strict marker transcript.  The final corpus result is
then assembled in the global manifest's committed span order.

This is CPU-only prospective measurement infrastructure.  It reads no model
output, sample draw, salt, masked contrast, or outcome.
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile

from prepare_v2b_lean_setups import (SETUP_INDEX_SCHEMA,
                                     validate_setup_index)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (V2BError, artifact_binding, load_json, sha256_bytes,
                        sha256_file, sha256_json, sha256_sorted_json,
                        write_new_json)
from v2b_lean_boundaries import (BOUNDARY_MANIFEST_SCHEMA,
                                 BOUNDARY_RESULT_SCHEMA,
                                 aggregate_driver_runs,
                                 build_driver_manifests,
                                 canonical_driver_manifest_bytes,
                                 parse_driver_stdout)


RUN_EVIDENCE_SCHEMA = "v2b_lean_boundary_module_run_v1"
RUN_EVIDENCE_KEYS = frozenset((
    "schema", "module_name", "invocation_binding", "manifest_sha256",
    "stdout_sha256", "stderr_sha256", "exit_code", "argv", "cwd",
    "environment_sha256", "runtime_sha256", "source_sha256_before",
    "source_sha256_after", "setup_sha256_before", "setup_sha256_after",
    "driver_sha256_before", "driver_sha256_after", "lean_sha256_before",
    "lean_sha256_after"))
RUNTIME_KEYS = frozenset((
    "setup_index", "corpus_root", "corpus_git_sha", "toolchain", "lean",
    "driver", "cwd", "argv_template", "environment",
    "environment_sha256"))
LEAN_KEYS = frozenset(("path", "sha256", "version"))
DRIVER_KEYS = frozenset(("path", "sha256"))
SETUP_BINDING_KEYS = frozenset(("path", "sha256", "schema"))
ENVIRONMENT_KEYS = (
    "ELAN_HOME", "ELAN_TOOLCHAIN", "LANG", "LC_ALL", "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH", "LIBRARY_PATH", "LEAN_CC", "LEAN_NUM_THREADS",
    "PATH", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME")
FORBIDDEN_LEAN_ENV = ("LEAN_PATH", "LEAN_SRC_PATH")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(char in "0123456789abcdef" for char in value)


def _strict_text(blob, where):
    try:
        return blob.decode("utf-8", errors="strict")
    except UnicodeError as err:
        raise V2BError(f"{where} is not strict UTF-8: {err}") from err


def _git(corpus_root, *args):
    process = subprocess.run(
        ["git", "-C", corpus_root, *args], capture_output=True,
        text=True, errors="strict", check=False)
    if process.returncode != 0:
        raise V2BError(f"git {' '.join(args)} failed in {corpus_root}: "
                       f"{process.stderr.strip()[:500]}")
    return process.stdout


def _corpus_identity(corpus_root, expected_sha):
    if not _hex(expected_sha, 40):
        raise V2BError("boundary runner expected corpus SHA is malformed")
    actual = _git(corpus_root, "rev-parse", "HEAD").strip()
    if actual != expected_sha:
        raise V2BError(f"corpus revision drift: {actual} != {expected_sha}")
    dirty = _git(corpus_root, "status", "--porcelain",
                 "--untracked-files=all")
    if dirty.strip():
        raise V2BError("corpus checkout is dirty during boundary audit")


def _environment(elan_home, toolchain):
    if not isinstance(elan_home, str) or not elan_home:
        raise V2BError("boundary runner ELAN_HOME is empty")
    for name in FORBIDDEN_LEAN_ENV:
        if os.environ.get(name):
            raise V2BError(f"ambient {name} is forbidden for the Lean audit")
    env = os.environ.copy()
    env["ELAN_HOME"] = os.path.realpath(elan_home)
    env["ELAN_TOOLCHAIN"] = toolchain
    projection = {name: env.get(name) for name in ENVIRONMENT_KEYS}
    return env, projection


def _command(args, cwd, env, timeout):
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise V2BError(f"Lean boundary process failed to execute: {err}") \
            from err


def validate_runtime(setup_index_path, global_manifest, driver_path,
                     elan_home):
    setup_binding, setup_index = artifact_binding(
        setup_index_path, SETUP_INDEX_SCHEMA)
    validate_setup_index(
        setup_index, live_files=True, require_generator=True)
    if setup_index.get("repo") != global_manifest.get("repo") \
            or setup_index.get("extraction", {}).get("sha256") != \
            global_manifest.get("extraction", {}).get("sha256"):
        raise V2BError("setup index does not bind the boundary plan")
    corpus_root = os.path.realpath(setup_index.get("corpus_root", ""))
    corpus_sha = setup_index.get("corpus_git_sha")
    toolchain = setup_index.get("toolchain")
    if not corpus_root or not os.path.isdir(corpus_root) \
            or not _hex(corpus_sha, 40) \
            or not isinstance(toolchain, str) or not toolchain.strip():
        raise V2BError("setup index lacks exact corpus/toolchain identity")
    _corpus_identity(corpus_root, corpus_sha)
    setups = setup_index.get("setups")
    if not isinstance(setups, dict) or not setups \
            or setup_index.get("setups_sha256") != \
            sha256_sorted_json(setups):
        raise V2BError("setup index source mapping binding drift")
    for span in global_manifest.get("spans", []):
        source = span.get("source")
        setup = span.get("setup")
        if setups.get(source) != setup:
            raise V2BError(f"boundary plan/setup-index join drift: {source}")
        if sha256_file(source) != span.get("source_sha256") \
                or sha256_file(setup) != span.get("setup_sha256"):
            raise V2BError(f"boundary runtime input byte drift: {source}")
        try:
            common = os.path.commonpath(
                (corpus_root, os.path.realpath(source)))
        except ValueError as err:
            raise V2BError(f"boundary source/root mismatch: {err}") from err
        if common != corpus_root:
            raise V2BError(f"boundary source escapes corpus root: {source}")
    lean = setup_index.get("lean")
    if not isinstance(lean, dict) or set(lean) != LEAN_KEYS \
            or not isinstance(lean.get("path"), str) or not lean["path"] \
            or not _hex(lean.get("sha256")) \
            or not isinstance(lean.get("version"), str) \
            or not lean["version"]:
        raise V2BError("setup index Lean runtime record is malformed")
    lean_path = os.path.realpath(lean["path"])
    if not os.path.isfile(lean_path) or not os.access(lean_path, os.X_OK) \
            or sha256_file(lean_path) != lean["sha256"]:
        raise V2BError("resolved Lean executable hash/path drift")
    env, environment = _environment(elan_home, toolchain)
    version = _command([lean_path, "--version"], corpus_root, env, 60)
    if version.returncode != 0 \
            or _strict_text(version.stdout, "Lean --version stdout").strip() \
            != lean["version"]:
        raise V2BError("live Lean version disagrees with setup index")
    driver_path = os.path.abspath(driver_path)
    driver_sha = sha256_file(driver_path)
    runtime = dict(
        setup_index=dict(setup_binding, schema=SETUP_INDEX_SCHEMA),
        corpus_root=corpus_root, corpus_git_sha=corpus_sha,
        toolchain=toolchain,
        lean=dict(path=lean_path, sha256=lean["sha256"],
                  version=lean["version"]),
        driver=dict(path=driver_path, sha256=driver_sha),
        cwd=corpus_root,
        argv_template=[lean_path, "--run", driver_path,
                       "<module-manifest.json>"],
        environment=environment,
        environment_sha256=sha256_sorted_json(environment))
    if set(runtime) != RUNTIME_KEYS:
        raise AssertionError("internal boundary runtime schema drift")
    return runtime, env


def _module_directory(run_dir, module, invocation_binding):
    digest = sha256_json(["v2b-lean-boundary-module-dir-v1", module,
                          invocation_binding])
    return os.path.join(run_dir, "modules", digest)


def _load_completed(final_dir, expected_manifest, driver_sha, toolchain,
                    runtime):
    paths = {name: os.path.join(final_dir, name) for name in (
        "manifest.json", "stdout.txt", "stderr.txt", "evidence.json")}
    if not os.path.isdir(final_dir) or any(not os.path.isfile(path)
                                           for path in paths.values()):
        raise V2BError(f"incomplete existing module evidence: {final_dir}")
    manifest, manifest_sha = load_json(paths["manifest.json"])
    evidence, evidence_sha = load_json(
        paths["evidence.json"], RUN_EVIDENCE_SCHEMA)
    stdout_blob = open(paths["stdout.txt"], "rb").read()
    stderr_blob = open(paths["stderr.txt"], "rb").read()
    stdout = _strict_text(stdout_blob, "boundary stdout")
    stderr = _strict_text(stderr_blob, "boundary stderr")
    actual_argv = evidence.get("argv")
    partial_root = os.path.join(
        os.path.dirname(os.path.dirname(final_dir)), ".partial")
    argv_manifest = actual_argv[3] \
        if isinstance(actual_argv, list) and len(actual_argv) == 4 \
        else None
    argv_work = os.path.dirname(argv_manifest) \
        if isinstance(argv_manifest, str) else None
    argv_valid = isinstance(argv_manifest, str) \
        and os.path.basename(argv_manifest) == "manifest.json" \
        and os.path.dirname(argv_work) == partial_root \
        and os.path.basename(argv_work).startswith("module-") \
        and actual_argv[:3] == [runtime["lean"]["path"], "--run",
                               runtime["driver"]["path"]]
    expected_source_sha = sha256_file(expected_manifest["originalFile"])
    expected_setup_sha = sha256_file(expected_manifest["moduleSetupFile"])
    if manifest != expected_manifest or set(evidence) != RUN_EVIDENCE_KEYS \
            or evidence.get("module_name") != \
            expected_manifest["moduleName"] \
            or evidence.get("invocation_binding") != \
            expected_manifest["invocationBinding"] \
            or evidence.get("manifest_sha256") != manifest_sha \
            or evidence.get("stdout_sha256") != sha256_bytes(stdout_blob) \
            or evidence.get("stderr_sha256") != sha256_bytes(stderr_blob) \
            or type(evidence.get("exit_code")) is not int \
            or evidence["exit_code"] != 0 \
            or evidence.get("runtime_sha256") != \
            sha256_sorted_json(runtime) \
            or evidence.get("environment_sha256") != \
            runtime["environment_sha256"] \
            or not argv_valid \
            or evidence.get("cwd") != runtime["cwd"] \
            or evidence.get("source_sha256_before") != expected_source_sha \
            or evidence.get("source_sha256_after") != expected_source_sha \
            or evidence.get("setup_sha256_before") != expected_setup_sha \
            or evidence.get("setup_sha256_after") != expected_setup_sha \
            or evidence.get("driver_sha256_before") != driver_sha \
            or evidence.get("driver_sha256_after") != driver_sha \
            or evidence.get("lean_sha256_before") != \
            runtime["lean"]["sha256"] \
            or evidence.get("lean_sha256_after") != \
            runtime["lean"]["sha256"]:
        raise V2BError(f"existing module evidence binding drift: {final_dir}")
    parse_driver_stdout(stdout, manifest, driver_sha, toolchain)
    return dict(manifest=manifest, manifest_sha256=manifest_sha,
                stdout=stdout, stderr=stderr,
                exit_code=evidence["exit_code"],
                evidence_sha256=evidence_sha)


def _write_bytes(path, blob):
    with open(path, "xb") as handle:
        handle.write(blob)


def _run_one(module, manifest, runtime, env, run_dir, timeout):
    driver_sha = runtime["driver"]["sha256"]
    lean_sha = runtime["lean"]["sha256"]
    toolchain = runtime["toolchain"]
    final_dir = _module_directory(
        run_dir, module, manifest["invocationBinding"])
    if os.path.exists(final_dir):
        return module, _load_completed(
            final_dir, manifest, driver_sha, toolchain, runtime), True
    modules_dir = os.path.join(run_dir, "modules")
    temp_root = os.path.join(run_dir, ".partial")
    os.makedirs(modules_dir, exist_ok=True)
    os.makedirs(temp_root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="module-", dir=temp_root)
    manifest_path = os.path.join(work, "manifest.json")
    stdout_path = os.path.join(work, "stdout.txt")
    stderr_path = os.path.join(work, "stderr.txt")
    evidence_path = os.path.join(work, "evidence.json")
    manifest_blob = canonical_driver_manifest_bytes(manifest)
    _write_bytes(manifest_path, manifest_blob)
    source = manifest["originalFile"]
    setup = manifest["moduleSetupFile"]
    before = dict(source=sha256_file(source), setup=sha256_file(setup),
                  driver=sha256_file(runtime["driver"]["path"]),
                  lean=sha256_file(runtime["lean"]["path"]))
    argv = [runtime["lean"]["path"], "--run",
            runtime["driver"]["path"], manifest_path]
    process = _command(argv, runtime["cwd"], env, timeout)
    stdout_blob, stderr_blob = process.stdout, process.stderr
    stdout = _strict_text(stdout_blob, f"{module} stdout")
    stderr = _strict_text(stderr_blob, f"{module} stderr")
    after = dict(source=sha256_file(source), setup=sha256_file(setup),
                 driver=sha256_file(runtime["driver"]["path"]),
                 lean=sha256_file(runtime["lean"]["path"]))
    _write_bytes(stdout_path, stdout_blob)
    _write_bytes(stderr_path, stderr_blob)
    evidence = dict(
        schema=RUN_EVIDENCE_SCHEMA, module_name=module,
        invocation_binding=manifest["invocationBinding"],
        manifest_sha256=sha256_bytes(manifest_blob),
        stdout_sha256=sha256_bytes(stdout_blob),
        stderr_sha256=sha256_bytes(stderr_blob),
        exit_code=process.returncode, argv=argv, cwd=runtime["cwd"],
        environment_sha256=runtime["environment_sha256"],
        runtime_sha256=sha256_sorted_json(runtime),
        source_sha256_before=before["source"],
        source_sha256_after=after["source"],
        setup_sha256_before=before["setup"],
        setup_sha256_after=after["setup"],
        driver_sha256_before=before["driver"],
        driver_sha256_after=after["driver"],
        lean_sha256_before=before["lean"],
        lean_sha256_after=after["lean"])
    evidence_sha = write_new_json(evidence_path, evidence)
    if before != after or before["source"] != sha256_file(source) \
            or before["setup"] != sha256_file(setup) \
            or before["driver"] != driver_sha \
            or before["lean"] != lean_sha:
        raise V2BError(f"runtime/input bytes drifted during module {module}")
    if process.returncode != 0:
        raise V2BError(f"Lean boundary module {module} exited "
                       f"{process.returncode}: {stderr.strip()[-1000:]}")
    parse_driver_stdout(stdout, manifest, driver_sha, toolchain)
    try:
        os.rename(work, final_dir)
    except OSError as err:
        raise V2BError(f"cannot atomically publish {module}: {err}") from err
    return module, dict(
        manifest=manifest, manifest_sha256=sha256_bytes(manifest_blob),
        stdout=stdout, stderr=stderr, exit_code=process.returncode,
        evidence_sha256=evidence_sha), False


def run_audit(global_manifest_path, setup_index_path, driver_path,
              elan_home, run_dir, workers=16, timeout=7200):
    if type(workers) is not int or workers <= 0:
        raise V2BError("boundary worker count must be a positive integer")
    if type(timeout) is not int or timeout <= 0:
        raise V2BError("boundary timeout must be a positive integer")
    _, global_manifest = artifact_binding(
        global_manifest_path, BOUNDARY_MANIFEST_SCHEMA)
    runtime, env = validate_runtime(
        setup_index_path, global_manifest, driver_path, elan_home)
    manifests = build_driver_manifests(
        global_manifest, driver_path, runtime["toolchain"])
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    runs = {}
    reused = 0
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(workers, len(manifests)))
    futures = [executor.submit(
            _run_one, module, manifests[module], runtime, env, run_dir,
            timeout) for module in sorted(manifests)]
    try:
        for future in concurrent.futures.as_completed(futures):
            module, envelope, was_reused = future.result()
            runs[module] = envelope
            reused += int(was_reused)
            print(f"[v2b-lean-boundary] {len(runs)}/{len(manifests)} "
                  f"{module} ({'reused' if was_reused else 'new'})",
                  flush=True)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    result = aggregate_driver_runs(
        global_manifest_path, driver_path, runtime["toolchain"], runs)
    runtime_after, _ = validate_runtime(
        setup_index_path, global_manifest, driver_path, elan_home)
    if runtime_after != runtime:
        raise V2BError("boundary runtime changed during corpus audit")
    result["runtime"] = runtime
    result["runtime_sha256"] = sha256_sorted_json(runtime)
    return result, reused


def _publish_resume(path, value):
    if os.path.exists(path):
        existing, digest = load_json(path, BOUNDARY_RESULT_SCHEMA)
        if existing != value:
            raise V2BError(f"existing corpus boundary result disagrees: {path}")
        return digest, True
    return write_new_json(path, value), False


def prepare(global_manifest_path, setup_index_path, driver_path, elan_home,
            run_dir, workers=16, timeout=7200):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    result, reused = run_audit(
        global_manifest_path, setup_index_path, driver_path, elan_home,
        run_dir, workers=workers, timeout=timeout)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during boundary audit")
    result["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="run_v2b_lean_boundary_audit.py")
    return result, reused


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--setup-index", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--elan-home", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result, reused = prepare(
            args.manifest, args.setup_index, args.driver, args.elan_home,
            args.run_dir, workers=args.workers, timeout=args.timeout)
        digest, output_reused = _publish_resume(args.out, result)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[v2b-lean-boundary] complete: {result['n_modules']} modules / "
          f"{result['n_spans']} spans; {reused} module runs reused; "
          f"result {'reused' if output_reused else 'new'} "
          f"({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
