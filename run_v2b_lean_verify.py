#!/usr/bin/env python3
"""Production execution envelope for the V2-b Lean S5 verifier.

Each baseline or candidate is one fresh, resource-bounded process.  Production
uses one frozen bubblewrap namespace and a nonce-authenticated evidence channel.
The wrapper sends the nonce first, durably journals the authenticated start
record and a GO intent, then sends ``GO:<nonce>`` plus EOF.  Lean acknowledges
that authorization before any generated parsing or target elaboration.  The
nonce is published only after the child exits.  Completed outcome-bearing
bundles are immutable and content-addressed by the manifest invocation binding.

This module does not generate completions, choose targets, inspect salts, or
write the final behavioral-outcomes artifact.
"""
import argparse
import copy
import fcntl
import json
import os
import resource
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from prepare_v2b_lean_setups import (
    LAKE_ENV_KEYS, SETUP_INDEX_SCHEMA, query_lake_environment,
    validate_setup_index)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_behavior_verify import (
    LEAN_VERIFY_DRIVER, bind_lean_verify_manifest,
    lean_baseline_certificate, parse_lean_verify_prefix,
    validate_lean_verify_manifest)
from v2b_common import (
    V2BError, artifact_binding, load_json, sha256_bytes, sha256_file,
    sha256_json, sha256_sorted_json)


EXECUTION_SCHEMA = "v2b_lean_verify_execution_v2"
RUNTIME_SCHEMA = "v2b_lean_s5_runtime_v1"
CANONICAL_BWRAP = "/usr/bin/bwrap"
ATTEMPT_OPEN_SCHEMA = "v2b_lean_s5_attempt_open_v1"
GO_INTENT_SCHEMA = "v2b_lean_s5_go_intent_v1"
GO_ACCEPTED_SCHEMA = "v2b_lean_s5_go_accepted_v1"
ATTEMPT_TERMINAL_SCHEMA = "v2b_lean_s5_attempt_terminal_v1"
MAX_PRESTART_ATTEMPTS = 2
CONTROL_HEADROOM_BYTES = 64 * 1024

SANDBOX_CONTRACT = dict(
    schema="v2b_lean_s5_bwrap_contract_v1",
    backend="bubblewrap",
    namespaces=["user", "pid", "network", "ipc", "uts", "cgroup"],
    empty_root=True,
    no_proc=True,
    no_sys=True,
    no_host_home=True,
    no_project_or_pool_parent=True,
    network=False,
    capabilities="drop-all",
    lifecycle=["die-with-parent", "new-session"],
    writable=["private-tmpfs-/tmp"],
    inputs=("read-only exact corpus root, resolved Lean toolchain, direct "
            "manifest/source/setup/reconstruction/driver files, and the "
            "same-cluster system dynamic-loader roots only"),
    environment="clear then exact allowlist",
    channel=("fresh secrets.token_hex(32) as first stdin line; authenticated "
             "start is durably journaled before exact GO:<nonce> plus EOF; "
             "Lean acknowledges GO before target work; inherited fds 0/1/2 "
             "only; nonce-qualified records only"),
)
SANDBOX_CONTRACT_SHA256 = sha256_sorted_json(SANDBOX_CONTRACT)

RESOURCE_LIMITS = dict(
    timeout_seconds=300,
    address_space_bytes=64 * 1024**3,
    cpu_seconds=305,
    n_processes=64,
    n_open_files=256,
    file_size_bytes=16 * 1024**2,
    core_size_bytes=0,
    stdout_bytes=8 * 1024**2,
    stderr_bytes=8 * 1024**2,
)

RUNTIME_KEYS = frozenset((
    "schema", "repo", "corpus_git_sha", "corpus_root", "toolchain",
    "harness_source_tree_sha256",
    "setup_index", "lake_environment", "lake_environment_sha256",
    "search_roots_sha256", "search_directories_sha256",
    "search_symlinks_sha256", "artifacts_sha256", "lean", "driver",
    "wrapper", "bwrap", "system_ro_bindings", "sandbox_contract_sha256",
    "child_environment", "child_environment_sha256", "resource_limits",
    "cwd", "inner_argv_template",
))
FILE_BINDING_KEYS = frozenset(("path", "sha256"))
EXECUTABLE_KEYS = frozenset(("path", "sha256", "version"))
SETUP_BINDING_KEYS = frozenset(("path", "sha256", "schema"))

EVIDENCE_KEYS = frozenset((
    "schema", "mode", "invocation_binding", "semantic_context_binding",
    "sample_id", "baseline_evidence_sha256", "manifest_sha256",
    "runtime_sha256", "channel_nonce", "argv",
    "cwd", "environment_sha256", "sandbox_contract_sha256",
    "resource_limits", "execution_backend", "timeout_seconds", "timed_out",
    "output_limited", "returncode", "wall_time_ns", "stdout_sha256",
    "stdout_bytes", "stderr_sha256", "stderr_bytes",
    "authenticated_prefix_stage", "protocol_valid",
    "protocol_error_sha256", "classification", "outcome_bearing",
    "input_hashes_before", "input_hashes_after", "attempt_id",
    "attempt_open_sha256", "go_intent_sha256", "go_accepted_sha256",
    "attempt_terminal_sha256",
))

CLASSIFICATIONS = frozenset((
    "baseline-verified", "baseline-ineligible", "verified-pass",
    "verification-failure", "candidate-timeout", "candidate-output-limit",
    "candidate-terminated", "harness-invalid", "evidence-invalid",
))

SYSTEM_BIND_CANDIDATES = (
    "/lib", "/lib64", "/usr/lib", "/usr/lib64",
    "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
    "/etc/localtime",
)


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(char in "0123456789abcdef" for char in value)


def _sorted_json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise V2BError(f"cannot encode sorted execution JSON: {err}") from err


def _write_new_bytes(path, blob):
    if not isinstance(blob, bytes):
        raise V2BError("execution byte writer requires bytes")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(blob)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short immutable execution write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(os.path.dirname(os.path.abspath(path)))
    except OSError as err:
        raise V2BError(f"cannot write immutable execution file {path}: {err}") \
            from err


def _fsync_directory(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as err:
        raise V2BError(f"cannot fsync execution directory {path}: {err}") \
            from err


def _write_new_durable_json(path, value):
    if not isinstance(value, dict):
        raise V2BError("durable execution JSON must be an object")
    blob = _sorted_json_bytes(value) + b"\n"
    _write_new_bytes(path, blob)
    return sha256_bytes(blob)


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
        raise V2BError("S5 runtime corpus SHA is malformed")
    if _git(corpus_root, "rev-parse", "HEAD").strip() != expected_sha:
        raise V2BError("S5 runtime corpus revision drift")
    if _git(corpus_root, "status", "--porcelain",
            "--untracked-files=all").strip():
        raise V2BError("S5 runtime corpus checkout is dirty")


def _command(args, cwd, env, timeout):
    try:
        return subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, timeout=timeout,
            check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise V2BError(f"S5 runtime probe failed: {err}") from err


def _child_environment(setup_index):
    lake = setup_index["lake_environment"]
    if not isinstance(lake, dict) or set(lake) != set(LAKE_ENV_KEYS):
        raise V2BError("S5 Lake environment shape drift")
    env = {
        "HOME": "/tmp/home",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/tmp/xdg-cache",
        "XDG_CONFIG_HOME": "/tmp/xdg-config",
        "XDG_DATA_HOME": "/tmp/xdg-data",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "LEAN_NUM_THREADS": "1",
    }
    for name in LAKE_ENV_KEYS:
        value = lake[name]
        if value is not None:
            if not isinstance(value, str) or not value:
                raise V2BError(f"S5 Lake environment {name} drift")
            env[name] = value
    return {name: env[name] for name in sorted(env)}


def build_runtime(setup_index_path, elan_home=None, probe_timeout=600):
    """Validate the exact live setup closure and build the manifest runtime."""
    if type(probe_timeout) is not int or probe_timeout <= 0:
        raise V2BError("S5 runtime probe timeout must be positive")
    setup_binding, setup_index = artifact_binding(
        setup_index_path, SETUP_INDEX_SCHEMA)
    validate_setup_index(setup_index, live_files=True, require_generator=True)
    corpus_root = os.path.realpath(setup_index["corpus_root"])
    corpus_sha = setup_index["corpus_git_sha"]
    _corpus_identity(corpus_root, corpus_sha)

    toolchain = setup_index["toolchain"]
    if not isinstance(toolchain, str) or not toolchain:
        raise V2BError("S5 setup index toolchain is empty")
    elan_home = os.path.realpath(
        elan_home or os.environ.get("ELAN_HOME", ""))
    if not elan_home or not os.path.isdir(elan_home):
        raise V2BError("S5 runtime ELAN_HOME is absent")
    query_env = os.environ.copy()
    query_env["ELAN_HOME"] = elan_home
    query_env["ELAN_TOOLCHAIN"] = toolchain
    for name in ("LEAN_PATH", "LEAN_SRC_PATH"):
        query_env.pop(name, None)
    live_lake = query_lake_environment(
        setup_index["lake"]["path"], corpus_root, query_env, probe_timeout,
        setup_index["environment_probe"]["path"])
    if live_lake != setup_index["lake_environment"]:
        raise V2BError("S5 live Lake environment drift")

    lean = setup_index["lean"]
    lean_path = os.path.realpath(lean["path"])
    if sha256_file(lean_path) != lean["sha256"]:
        raise V2BError("S5 Lean executable byte drift")
    version = _command([lean_path, "--version"], corpus_root, query_env, 60)
    try:
        version_text = version.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError as err:
        raise V2BError("S5 Lean version is not UTF-8") from err
    if version.returncode != 0 or version_text != lean["version"]:
        raise V2BError("S5 Lean version drift")

    driver_path = os.path.realpath(LEAN_VERIFY_DRIVER)
    wrapper_path = os.path.abspath(__file__)
    bwrap_path = os.path.realpath(CANONICAL_BWRAP)
    if not bwrap_path or not os.path.isfile(bwrap_path) \
            or not os.access(bwrap_path, os.X_OK):
        raise V2BError("production S5 requires an executable bubblewrap")
    bwrap_version = _command([bwrap_path, "--version"], corpus_root,
                             query_env, 60)
    try:
        bwrap_version_text = bwrap_version.stdout.decode(
            "utf-8", errors="strict").strip()
    except UnicodeError as err:
        raise V2BError("bubblewrap version is not UTF-8") from err
    if bwrap_version.returncode != 0 or not bwrap_version_text:
        raise V2BError("cannot identify bubblewrap runtime")

    environment = _child_environment(setup_index)
    system_ro_bindings = sorted(
        path for path in SYSTEM_BIND_CANDIDATES if os.path.exists(path))
    runtime = dict(
        schema=RUNTIME_SCHEMA,
        repo=setup_index["repo"], corpus_git_sha=corpus_sha,
        corpus_root=corpus_root, toolchain=toolchain,
        harness_source_tree_sha256=source_tree_hash(),
        setup_index=dict(setup_binding, schema=SETUP_INDEX_SCHEMA),
        lake_environment=copy.deepcopy(setup_index["lake_environment"]),
        lake_environment_sha256=setup_index["lake_environment_sha256"],
        search_roots_sha256=setup_index["search_roots_sha256"],
        search_directories_sha256=setup_index[
            "search_directories_sha256"],
        search_symlinks_sha256=setup_index["search_symlinks_sha256"],
        artifacts_sha256=setup_index["artifacts_sha256"],
        lean=dict(path=lean_path, sha256=lean["sha256"],
                  version=lean["version"]),
        driver=dict(path=driver_path, sha256=sha256_file(driver_path)),
        wrapper=dict(path=wrapper_path, sha256=sha256_file(wrapper_path)),
        bwrap=dict(path=bwrap_path, sha256=sha256_file(bwrap_path),
                   version=bwrap_version_text),
        system_ro_bindings=system_ro_bindings,
        sandbox_contract_sha256=SANDBOX_CONTRACT_SHA256,
        child_environment=environment,
        child_environment_sha256=sha256_sorted_json(environment),
        resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        cwd=corpus_root,
        inner_argv_template=[lean_path, "--run", driver_path,
                             "<manifest.json>"],
    )
    validate_runtime(runtime, setup_index_path=setup_index_path,
                     live_files=True)
    return runtime


def validate_runtime(runtime, setup_index_path=None, live_files=False):
    if not isinstance(runtime, dict) or set(runtime) != RUNTIME_KEYS \
            or runtime.get("schema") != RUNTIME_SCHEMA:
        raise V2BError("S5 runtime schema/key drift")
    if not isinstance(runtime.get("repo"), str) or not runtime["repo"] \
            or not _hex(runtime.get("corpus_git_sha"), 40) \
            or not isinstance(runtime.get("corpus_root"), str) \
            or not runtime["corpus_root"] \
            or not isinstance(runtime.get("toolchain"), str) \
            or not runtime["toolchain"]:
        raise V2BError("S5 runtime corpus/toolchain identity drift")
    setup = runtime.get("setup_index")
    if not isinstance(setup, dict) or set(setup) != SETUP_BINDING_KEYS \
            or setup.get("schema") != SETUP_INDEX_SCHEMA \
            or not _hex(setup.get("sha256")) \
            or not isinstance(setup.get("path"), str) or not setup["path"]:
        raise V2BError("S5 runtime setup-index binding drift")
    if setup_index_path is not None \
            and os.path.abspath(setup_index_path) != os.path.abspath(
                setup["path"]):
        raise V2BError("S5 runtime setup-index path drift")
    for name in ("lake_environment_sha256", "search_roots_sha256",
                 "search_directories_sha256", "search_symlinks_sha256",
                 "artifacts_sha256", "sandbox_contract_sha256",
                 "child_environment_sha256",
                 "harness_source_tree_sha256"):
        if not _hex(runtime.get(name)):
            raise V2BError(f"S5 runtime {name} is malformed")
    if runtime["sandbox_contract_sha256"] != SANDBOX_CONTRACT_SHA256 \
            or runtime["resource_limits"] != RESOURCE_LIMITS:
        raise V2BError("S5 sandbox/resource contract drift")
    if runtime["lake_environment_sha256"] != sha256_sorted_json(
            runtime["lake_environment"]) \
            or runtime["child_environment_sha256"] != sha256_sorted_json(
                runtime["child_environment"]):
        raise V2BError("S5 runtime environment binding drift")
    for label in ("lean", "bwrap"):
        row = runtime.get(label)
        if not isinstance(row, dict) or set(row) != EXECUTABLE_KEYS \
                or not isinstance(row.get("path"), str) or not row["path"] \
                or not _hex(row.get("sha256")) \
                or not isinstance(row.get("version"), str) \
                or not row["version"]:
            raise V2BError(f"S5 runtime {label} binding drift")
    for label in ("driver", "wrapper"):
        row = runtime.get(label)
        if not isinstance(row, dict) or set(row) != FILE_BINDING_KEYS \
                or not isinstance(row.get("path"), str) or not row["path"] \
                or not _hex(row.get("sha256")):
            raise V2BError(f"S5 runtime {label} binding drift")
    canonical_files = {
        "driver": os.path.realpath(LEAN_VERIFY_DRIVER),
        "wrapper": os.path.realpath(__file__),
    }
    for label, canonical in canonical_files.items():
        if runtime[label]["path"] != canonical \
                or runtime[label]["sha256"] != sha256_file(canonical):
            raise V2BError(f"S5 runtime {label} is not the canonical file")
    if runtime.get("cwd") != runtime["corpus_root"] \
            or runtime.get("inner_argv_template") != [
                runtime["lean"]["path"], "--run",
                runtime["driver"]["path"], "<manifest.json>"]:
        raise V2BError("S5 runtime cwd/argv template drift")
    system = runtime.get("system_ro_bindings")
    if not isinstance(system, list) or system != sorted(set(system)) \
            or any(not isinstance(path, str) or not os.path.isabs(path)
                   for path in system):
        raise V2BError("S5 runtime system binding drift")
    if live_files:
        if not source_clean() or runtime["harness_source_tree_sha256"] != \
                source_tree_hash():
            raise V2BError("S5 harness source-tree identity drift")
        setup_index, digest = load_json(setup["path"], SETUP_INDEX_SCHEMA)
        if digest != setup["sha256"]:
            raise V2BError("S5 live setup-index byte drift")
        validate_setup_index(setup_index, live_files=True,
                             require_generator=True)
        projection = {
            "repo": setup_index["repo"],
            "corpus_git_sha": setup_index["corpus_git_sha"],
            "corpus_root": os.path.realpath(setup_index["corpus_root"]),
            "toolchain": setup_index["toolchain"],
            "lake_environment": setup_index["lake_environment"],
            "lake_environment_sha256": setup_index[
                "lake_environment_sha256"],
            "search_roots_sha256": setup_index["search_roots_sha256"],
            "search_directories_sha256": setup_index[
                "search_directories_sha256"],
            "search_symlinks_sha256": setup_index[
                "search_symlinks_sha256"],
            "artifacts_sha256": setup_index["artifacts_sha256"],
        }
        if any(runtime.get(name) != value
               for name, value in projection.items()) \
                or runtime["lean"] != setup_index["lean"] \
                or runtime["child_environment"] != \
                _child_environment(setup_index) \
                or system != sorted(
                    path for path in SYSTEM_BIND_CANDIDATES
                    if os.path.exists(path)):
            raise V2BError("S5 runtime/setup-index projection drift")
        canonical_bwrap = os.path.realpath(CANONICAL_BWRAP)
        if runtime["bwrap"]["path"] != canonical_bwrap \
                or runtime["bwrap"]["sha256"] != \
                sha256_file(canonical_bwrap):
            raise V2BError("S5 runtime bwrap is not the canonical file")
        _corpus_identity(runtime["corpus_root"],
                         runtime["corpus_git_sha"])
        for label in ("lean", "driver", "wrapper", "bwrap"):
            if sha256_file(runtime[label]["path"]) != \
                    runtime[label]["sha256"]:
                raise V2BError(f"S5 live {label} byte drift")
        if any(not os.path.exists(path) for path in system):
            raise V2BError("S5 system loader binding disappeared")
    return runtime


def _validate_manifest_setup_join(manifest, runtime):
    """Bind the source/module/ModuleSetup triple to one exact setup row."""
    setup_index, digest = load_json(
        runtime["setup_index"]["path"], SETUP_INDEX_SCHEMA)
    if digest != runtime["setup_index"]["sha256"]:
        raise V2BError("S5 manifest join setup-index byte drift")
    rows = validate_setup_index(
        setup_index, live_files=False, require_generator=True)
    original = os.path.realpath(manifest["originalFile"])
    matches = [row for row in rows
               if os.path.realpath(row["source"]) == original]
    if len(matches) != 1:
        raise V2BError("S5 original source lacks one exact setup-index row")
    row = matches[0]
    if manifest["originalFile"] != row["source"] \
            or manifest["logicalFileName"] != row["source"] \
            or row["module"] != manifest["moduleName"] \
            or os.path.realpath(row["setup"]) != os.path.realpath(
                manifest["moduleSetupFile"]) \
            or row["source_sha256"] != manifest["originalSha256"] \
            or row["setup_sha256"] != manifest["moduleSetupSha256"]:
        raise V2BError("S5 manifest source/module/setup join drift")
    return row


def _runtime_sha(runtime):
    validate_runtime(runtime)
    return sha256_sorted_json(runtime)


def _toolchain_root(lean_path):
    path = os.path.realpath(lean_path)
    root = os.path.dirname(os.path.dirname(path))
    if not os.path.isdir(root):
        raise V2BError("cannot resolve S5 Lean toolchain root")
    return root


def _require_private_run_root(run_dir, runtime):
    """Keep the nonce journal outside every broad child-visible mount."""
    run_root = os.path.realpath(run_dir)
    broad_roots = [runtime["corpus_root"],
                   _toolchain_root(runtime["lean"]["path"]),
                   *runtime["system_ro_bindings"]]
    for visible in broad_roots:
        visible_root = os.path.realpath(visible)
        try:
            contained = os.path.commonpath(
                (run_root, visible_root)) == visible_root
        except ValueError:
            contained = False
        if contained:
            raise V2BError(
                "S5 private run root is inside a child-visible mount")
    return run_root


def _parent_directories(paths):
    values = set()
    for path in paths:
        parent = os.path.dirname(path)
        while parent and parent != "/":
            values.add(parent)
            parent = os.path.dirname(parent)
    return sorted(values, key=lambda value: (value.count(os.sep), value))


def _sandbox_argv(runtime, manifest, manifest_path):
    files = [manifest_path, manifest["originalFile"],
             manifest["moduleSetupFile"], runtime["driver"]["path"]]
    files.extend(sample["reconstructedFile"] for sample in
                 manifest["samples"])
    roots = [runtime["corpus_root"],
             _toolchain_root(runtime["lean"]["path"]),
             *runtime["system_ro_bindings"]]
    paths = [os.path.abspath(path) for path in (*roots, *files)]
    argv = [runtime["bwrap"]["path"], "--unshare-all",
            "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            "--clearenv"]
    for directory in _parent_directories(paths):
        argv.extend(("--dir", directory))
    argv.extend(("--dev", "/dev", "--tmpfs", "/tmp",
                 "--dir", "/tmp/home", "--dir", "/tmp/xdg-cache",
                 "--dir", "/tmp/xdg-config", "--dir", "/tmp/xdg-data"))
    for path in sorted(set(roots)):
        argv.extend(("--ro-bind", path, path))
    for path in sorted(set(files)):
        argv.extend(("--ro-bind", path, path))
    for name, value in sorted(runtime["child_environment"].items()):
        argv.extend(("--setenv", name, value))
    argv.extend(("--chdir", runtime["cwd"], "--",
                 runtime["lean"]["path"], "--run",
                 runtime["driver"]["path"], manifest_path))
    return argv


def _direct_input_hashes(manifest, runtime, manifest_path):
    rows = [
        dict(role="manifest", path=manifest_path,
             sha256=sha256_file(manifest_path)),
        dict(role="original", path=manifest["originalFile"],
             sha256=sha256_file(manifest["originalFile"])),
        dict(role="module-setup", path=manifest["moduleSetupFile"],
             sha256=sha256_file(manifest["moduleSetupFile"])),
        dict(role="driver", path=runtime["driver"]["path"],
             sha256=sha256_file(runtime["driver"]["path"])),
        dict(role="wrapper", path=runtime["wrapper"]["path"],
             sha256=sha256_file(runtime["wrapper"]["path"])),
        dict(role="lean", path=runtime["lean"]["path"],
             sha256=sha256_file(runtime["lean"]["path"])),
        dict(role="bwrap", path=runtime["bwrap"]["path"],
             sha256=sha256_file(runtime["bwrap"]["path"])),
    ]
    rows.extend(dict(
        role="reconstructed", sample_id=sample["id"],
        path=sample["reconstructedFile"],
        sha256=sha256_file(sample["reconstructedFile"]))
        for sample in manifest["samples"])
    return rows


def _set_resource_limits(limits, enforce_address_space=True):
    # Darwin maps its system shared cache at a virtual address above this
    # experiment's 64 GiB Linux limit, so lowering RLIMIT_AS there fails before
    # exec.  The only caller allowed to omit that limit is the explicitly
    # unisolated local integration-test backend; production bubblewrap always
    # installs it.
    if enforce_address_space:
        resource.setrlimit(resource.RLIMIT_AS, (
            limits["address_space_bytes"], limits["address_space_bytes"]))
    resource.setrlimit(resource.RLIMIT_CPU, (
        limits["cpu_seconds"], limits["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_NPROC, (
        limits["n_processes"], limits["n_processes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (
        limits["n_open_files"], limits["n_open_files"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (
        limits["file_size_bytes"], limits["file_size_bytes"]))
    resource.setrlimit(resource.RLIMIT_CORE, (
        limits["core_size_bytes"], limits["core_size_bytes"]))


class _BoundedReader(threading.Thread):
    def __init__(self, stream, limit, exceeded):
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.exceeded = exceeded
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.error = None

    def snapshot(self):
        with self.lock:
            return bytes(self.buffer)

    def run(self):
        try:
            while True:
                # BufferedReader.read(size) may wait for the full requested
                # size while the child is still alive.  The authorization
                # handshake needs each flushed marker immediately.
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk:
                    break
                with self.lock:
                    remaining = self.limit - len(self.buffer)
                    if remaining > 0:
                        self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded.set()
        except OSError as err:
            self.error = err
        finally:
            self.stream.close()


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _execute(argv, cwd, env, nonce, manifest, limits, attempt,
             enforce_address_space=True):
    exceeded = threading.Event()
    started = time.monotonic_ns()
    deadline = time.monotonic() + limits["timeout_seconds"]
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
            preexec_fn=lambda: _set_resource_limits(
                limits, enforce_address_space=enforce_address_space))
    except (OSError, subprocess.SubprocessError) as err:
        raise V2BError(f"cannot start S5 child: {err}") from err
    stdout_reader = _BoundedReader(
        process.stdout, limits["stdout_bytes"], exceeded)
    stderr_reader = _BoundedReader(
        process.stderr, limits["stderr_bytes"], exceeded)
    stdout_reader.start()
    stderr_reader.start()
    try:
        process.stdin.write((nonce + "\n").encode("ascii"))
        process.stdin.flush()
    except OSError:
        _kill_group(process)
    timed_out = False
    authorized = False
    go_intent_sha = None
    go_accepted_sha = None
    expected_start = ("baseline-awaiting-authorization"
                      if manifest["mode"] == "baseline"
                      else "candidate-awaiting-authorization")
    while process.poll() is None:
        if authorized and go_accepted_sha is None:
            accepted_prefix = _accepted_control_prefix(
                stdout_reader.snapshot(), manifest, nonce)
            if accepted_prefix is not None:
                accepted_stage = ("baseline-started"
                                  if manifest["mode"] == "baseline"
                                  else "candidate-started")
                accepted = dict(
                    schema=GO_ACCEPTED_SCHEMA,
                    attempt_id=attempt["attempt_id"],
                    invocation_binding=manifest["invocationBinding"],
                    mode=manifest["mode"],
                    authenticated_stage=accepted_stage,
                    stdout_prefix_sha256=sha256_bytes(accepted_prefix),
                    stdout_prefix_bytes=len(accepted_prefix))
                go_accepted_sha = _write_new_durable_json(
                    os.path.join(
                        attempt["directory"], "go-accepted.json"),
                    accepted)
        if exceeded.is_set():
            _kill_group(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_group(process)
            break
        if not authorized:
            snapshot = stdout_reader.snapshot()
            last_newline = snapshot.rfind(b"\n")
            complete_prefix = (snapshot[:last_newline + 1]
                               if last_newline >= 0 else b"")
            prefix, protocol_error = _last_valid_prefix(
                complete_prefix, manifest, nonce)
            if protocol_error is not None:
                _kill_group(process)
                break
            if prefix["stage"] == expected_start:
                if len(snapshot) > \
                        limits["stdout_bytes"] - CONTROL_HEADROOM_BYTES:
                    exceeded.set()
                    continue
                prefix_sha = sha256_bytes(complete_prefix)
                _write_new_bytes(os.path.join(
                    attempt["directory"], "start-prefix.bin"),
                    complete_prefix)
                intent = dict(
                    schema=GO_INTENT_SCHEMA,
                    attempt_id=attempt["attempt_id"],
                    invocation_binding=manifest["invocationBinding"],
                    mode=manifest["mode"], authenticated_stage=expected_start,
                    stdout_prefix_sha256=prefix_sha,
                    stdout_prefix_bytes=len(complete_prefix),
                    nonce_sha256=sha256_bytes(nonce.encode("ascii")))
                go_intent_sha = _write_new_durable_json(
                    os.path.join(attempt["directory"], "go-intent.json"),
                    intent)
                if exceeded.is_set() or time.monotonic() >= deadline \
                        or process.poll() is not None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                    _kill_group(process)
                    break
                try:
                    process.stdin.write(
                        ("GO:" + nonce + "\n").encode("ascii"))
                    process.stdin.flush()
                    process.stdin.close()
                    authorized = True
                except OSError:
                    _kill_group(process)
                    break
        time.sleep(.02)
    if not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        returncode = process.wait(timeout=10)
    stdout_reader.join(timeout=10)
    stderr_reader.join(timeout=10)
    if stdout_reader.is_alive() or stderr_reader.is_alive():
        raise V2BError("S5 bounded output reader did not terminate")
    if stdout_reader.error is not None or stderr_reader.error is not None:
        raise V2BError("S5 bounded output reader failed")
    if authorized and go_accepted_sha is None:
        snapshot = stdout_reader.snapshot()
        accepted_prefix = _accepted_control_prefix(
            snapshot, manifest, nonce)
        if accepted_prefix is not None:
            accepted_stage = ("baseline-started"
                              if manifest["mode"] == "baseline"
                              else "candidate-started")
            accepted = dict(
                schema=GO_ACCEPTED_SCHEMA,
                attempt_id=attempt["attempt_id"],
                invocation_binding=manifest["invocationBinding"],
                mode=manifest["mode"],
                authenticated_stage=accepted_stage,
                stdout_prefix_sha256=sha256_bytes(accepted_prefix),
                stdout_prefix_bytes=len(accepted_prefix))
            go_accepted_sha = _write_new_durable_json(
                os.path.join(attempt["directory"], "go-accepted.json"),
                accepted)
    return dict(
        stdout=stdout_reader.snapshot(),
        stderr=stderr_reader.snapshot(), returncode=returncode,
        timed_out=timed_out, output_limited=exceeded.is_set(),
        wall_time_ns=time.monotonic_ns() - started,
        go_intent_sha256=go_intent_sha,
        go_accepted_sha256=go_accepted_sha,
    )


def _last_valid_prefix(stdout, manifest, nonce):
    marker = ("@@V2B_LEAN_VERIFY:" + nonce + "@@").encode("ascii")
    authenticated = []
    last = dict(stage="before-prevalidation")
    error = None
    for line in stdout.splitlines():
        if not line.startswith(marker):
            continue
        authenticated.append(line)
        try:
            last = parse_lean_verify_prefix(
                b"\n".join(authenticated), manifest, nonce)
        except V2BError as err:
            error = err
            break
    return last, error


def _complete_line_prefix(stdout):
    """Retain only newline-complete bytes from an untrusted raw stream."""
    if not isinstance(stdout, bytes):
        raise V2BError("S5 control transcript must be raw bytes")
    last_newline = stdout.rfind(b"\n")
    return stdout[:last_newline + 1] if last_newline >= 0 else b""


def _accepted_control_prefix(stdout, manifest, nonce):
    """Return the exact raw prefix ending at a valid GO-acceptance row.

    Later generated output may be truncated or malformed.  It cannot erase a
    complete, already-authenticated acknowledgment that precedes it.
    """
    complete = _complete_line_prefix(stdout)
    marker = ("@@V2B_LEAN_VERIFY:" + nonce + "@@").encode("ascii")
    authenticated = []
    offset = 0
    accepted_stage = ("baseline-started"
                      if manifest["mode"] == "baseline"
                      else "candidate-started")
    for raw_line in complete.splitlines(keepends=True):
        offset += len(raw_line)
        if not raw_line.startswith(marker):
            continue
        authenticated.append(raw_line.rstrip(b"\r\n"))
        try:
            parsed = parse_lean_verify_prefix(
                b"\n".join(authenticated), manifest, nonce)
        except V2BError:
            return None
        if parsed["stage"] in (accepted_stage, "complete"):
            return complete[:offset]
    return None


def classify_execution(manifest, stdout, nonce, returncode, timed_out=False,
                       output_limited=False,
                       authorization_committed=False):
    """Pure frozen stage/termination truth table used by writer and reader."""
    # A timeout or output-cap kill can interrupt a genuine authenticated line.
    # Only newline-terminated records are evidence in that case; otherwise a
    # truncated terminal record would turn an already-started candidate zero
    # into a hard protocol failure.
    if timed_out or output_limited:
        separator = b"\n" if isinstance(stdout, bytes) else "\n"
        evidence = separator.join(stdout.split(separator)[:-1]) \
            if not stdout.endswith(separator) else stdout
    else:
        evidence = stdout
    prefix, protocol_error = _last_valid_prefix(
        evidence, manifest, nonce)
    stage = prefix["stage"]
    protocol_valid = protocol_error is None
    if protocol_valid:
        try:
            prefix = parse_lean_verify_prefix(evidence, manifest, nonce)
            stage = prefix["stage"]
        except V2BError as err:
            protocol_error = err
            protocol_valid = False
    error_sha = (sha256_bytes(str(protocol_error).encode("utf-8"))
                 if protocol_error is not None else None)
    if manifest["mode"] == "baseline":
        if not protocol_valid:
            classification = "evidence-invalid"
            outcome_bearing = stage == "complete"
        elif timed_out or output_limited or returncode != 0 \
                or stage != "complete":
            classification = "harness-invalid"
            outcome_bearing = False
        elif prefix["baseline"]["status"] == "verified":
            classification = "baseline-verified"
            outcome_bearing = True
        else:
            classification = "baseline-ineligible"
            outcome_bearing = True
    else:
        started = stage in ("candidate-started", "complete") \
            or authorization_committed
        if not protocol_valid:
            classification = "evidence-invalid"
            outcome_bearing = started
        elif not started:
            classification = "harness-invalid"
            outcome_bearing = False
        elif timed_out:
            classification = "candidate-timeout"
            outcome_bearing = True
        elif output_limited:
            classification = "candidate-output-limit"
            outcome_bearing = True
        elif returncode != 0 or stage != "complete":
            classification = "candidate-terminated"
            outcome_bearing = True
        elif prefix["samples"][0]["status"] == "verified":
            classification = "verified-pass"
            outcome_bearing = True
        else:
            classification = "verification-failure"
            outcome_bearing = True
    return dict(
        authenticated_prefix_stage=stage,
        protocol_valid=protocol_valid,
        protocol_error_sha256=error_sha,
        classification=classification,
        outcome_bearing=outcome_bearing,
        parsed=prefix if protocol_valid else None,
    )


def _bundle_directory(run_dir, manifest):
    digest = sha256_json([
        "v2b-lean-s5-bundle-v1", manifest["mode"],
        manifest["invocationBinding"]])
    return os.path.join(run_dir, "bundles", digest)


def _input_manifest_path(run_dir, manifest):
    return os.path.join(run_dir, "inputs", manifest["invocationBinding"],
                        "manifest.json")


def _stage_manifest(run_dir, manifest):
    path = _input_manifest_path(run_dir, manifest)
    blob = _sorted_json_bytes(manifest)
    if os.path.exists(path):
        if open(path, "rb").read() != blob:
            raise V2BError("existing S5 staged manifest disagrees")
        return path, blob
    parent = os.path.dirname(path)
    os.makedirs(os.path.dirname(parent), exist_ok=True)
    temp = tempfile.mkdtemp(prefix="manifest-", dir=os.path.dirname(parent))
    try:
        candidate = os.path.join(temp, "manifest.json")
        _write_new_bytes(candidate, blob)
        try:
            os.rename(temp, parent)
        except OSError as err:
            raise V2BError(f"cannot publish staged S5 manifest: {err}") \
                from err
    finally:
        if os.path.isdir(temp):
            shutil.rmtree(temp)
    return path, blob


def _attempt_root(run_dir, manifest):
    return os.path.join(run_dir, "attempts", manifest["invocationBinding"])


def _attempt_directory(run_dir, manifest, attempt_id):
    if not _hex(attempt_id):
        raise V2BError("S5 attempt id is malformed")
    return os.path.join(_attempt_root(run_dir, manifest), attempt_id)


def _prepare_attempt(run_dir, manifest, manifest_blob, runtime_sha, nonce,
                     direct_inputs):
    root = _attempt_root(run_dir, manifest)
    os.makedirs(root, mode=0o700, exist_ok=True)
    try:
        names = sorted(name for name in os.listdir(root)
                       if os.path.isdir(os.path.join(root, name)))
    except OSError as err:
        raise V2BError(f"cannot inspect S5 attempts: {err}") from err
    if any(not _hex(name) for name in names):
        raise V2BError("S5 attempt directory contains a foreign entry")
    for name in names:
        prior = os.path.join(root, name)
        if os.path.exists(os.path.join(prior, "go-intent.json")):
            raise V2BError(
                "prior S5 GO intent exists; refusing outcome-selective retry")
    if len(names) >= MAX_PRESTART_ATTEMPTS:
        raise V2BError("S5 pre-start retry limit is exhausted")
    ordinal = len(names)
    nonce_sha = sha256_bytes(nonce.encode("ascii"))
    attempt_id = sha256_json([
        "v2b-s5-attempt-v1", manifest["invocationBinding"], ordinal,
        nonce_sha])
    directory = _attempt_directory(run_dir, manifest, attempt_id)
    try:
        os.mkdir(directory, 0o700)
        _fsync_directory(root)
    except OSError as err:
        raise V2BError(f"cannot create immutable S5 attempt: {err}") \
            from err
    sample_id = (manifest["samples"][0]["id"]
                 if manifest["mode"] == "candidate" else None)
    opened = dict(
        schema=ATTEMPT_OPEN_SCHEMA, attempt_id=attempt_id,
        attempt_ordinal=ordinal,
        invocation_binding=manifest["invocationBinding"],
        semantic_context_binding=manifest["semanticContextBinding"],
        mode=manifest["mode"], sample_id=sample_id,
        manifest_sha256=sha256_bytes(manifest_blob),
        runtime_sha256=runtime_sha, nonce_sha256=nonce_sha,
        direct_inputs_before=direct_inputs,
        direct_inputs_before_sha256=sha256_sorted_json(direct_inputs))
    open_sha = _write_new_durable_json(
        os.path.join(directory, "attempt-open.json"), opened)
    _write_new_bytes(os.path.join(directory, "channel-nonce.txt"),
                     (nonce + "\n").encode("ascii"))
    return dict(directory=directory, attempt_id=attempt_id,
                attempt_open_sha256=open_sha, opened=opened)


def _read_bundle_files(directory):
    paths = {name: os.path.join(directory, name) for name in (
        "manifest.json", "runtime.json", "stdout.bin", "stderr.bin",
        "evidence.json")}
    if not os.path.isdir(directory) or any(
            not os.path.isfile(path) for path in paths.values()):
        raise V2BError(f"incomplete S5 execution bundle: {directory}")
    manifest, manifest_sha = load_json(paths["manifest.json"])
    stored_runtime, stored_runtime_sha = load_json(
        paths["runtime.json"], RUNTIME_SCHEMA)
    evidence, evidence_sha = load_json(
        paths["evidence.json"], EXECUTION_SCHEMA)
    stdout = open(paths["stdout.bin"], "rb").read()
    stderr = open(paths["stderr.bin"], "rb").read()
    return (paths, manifest, manifest_sha, stored_runtime,
            stored_runtime_sha, evidence, evidence_sha, stdout, stderr)


def _validate_attempt_journal(run_dir, manifest, evidence, nonce,
                              stdout, stderr):
    attempt_id = evidence.get("attempt_id")
    if not _hex(attempt_id):
        raise V2BError("S5 evidence attempt id is malformed")
    directory = _attempt_directory(run_dir, manifest, attempt_id)
    required = {
        "open": ("attempt-open.json", ATTEMPT_OPEN_SCHEMA),
        "intent": ("go-intent.json", GO_INTENT_SCHEMA),
        "terminal": ("terminal.json", ATTEMPT_TERMINAL_SCHEMA),
    }
    accepted_path = os.path.join(directory, "go-accepted.json")
    if evidence.get("go_accepted_sha256") is not None:
        required["accepted"] = ("go-accepted.json", GO_ACCEPTED_SCHEMA)
    elif os.path.exists(accepted_path):
        raise V2BError("S5 evidence omits an existing GO-acceptance record")
    rows = {}
    hashes = {}
    for label, (name, schema) in required.items():
        row, digest = load_json(os.path.join(directory, name), schema)
        rows[label] = row
        hashes[label] = digest
    if evidence.get("attempt_open_sha256") != hashes["open"] \
            or evidence.get("go_intent_sha256") != hashes["intent"] \
            or evidence.get("go_accepted_sha256") != hashes.get("accepted") \
            or evidence.get("attempt_terminal_sha256") != \
            hashes["terminal"]:
        raise V2BError("S5 attempt-journal hash drift")
    try:
        nonce_blob = open(os.path.join(
            directory, "channel-nonce.txt"), "rb").read()
        start_prefix = open(os.path.join(
            directory, "start-prefix.bin"), "rb").read()
        journal_stdout = open(os.path.join(
            directory, "stdout.bin"), "rb").read()
        journal_stderr = open(os.path.join(
            directory, "stderr.bin"), "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read S5 attempt journal: {err}") from err
    nonce_sha = sha256_bytes(nonce.encode("ascii"))
    opened, intent, accepted, terminal = (
        rows["open"], rows["intent"], rows.get("accepted"), rows["terminal"])
    if nonce_blob != (nonce + "\n").encode("ascii") \
            or journal_stdout != stdout or journal_stderr != stderr \
            or not stdout.startswith(start_prefix) \
            or any(row.get("attempt_id") != attempt_id \
                   or row.get("invocation_binding") != \
                   manifest["invocationBinding"]
                   for row in (opened, intent, terminal)
                   + (() if accepted is None else (accepted,))) \
            or opened.get("nonce_sha256") != nonce_sha \
            or intent.get("nonce_sha256") != nonce_sha \
            or intent.get("stdout_prefix_sha256") != \
            sha256_bytes(start_prefix) \
            or intent.get("stdout_prefix_bytes") != len(start_prefix):
        raise V2BError("S5 attempt-journal identity drift")
    if accepted is not None:
        accepted_bytes = accepted.get("stdout_prefix_bytes")
        if type(accepted_bytes) is not int \
                or accepted_bytes < len(start_prefix) \
                or accepted_bytes > len(stdout) \
                or accepted.get("stdout_prefix_sha256") != \
                sha256_bytes(stdout[:accepted_bytes]):
            raise V2BError("S5 GO-acceptance prefix drift")
    if terminal.get("nonce_sha256") != nonce_sha \
            or terminal.get("go_intent_sha256") != hashes["intent"] \
            or terminal.get("go_accepted_sha256") != hashes.get("accepted") \
            or terminal.get("stdout_sha256") != sha256_bytes(stdout) \
            or terminal.get("stdout_bytes") != len(stdout) \
            or terminal.get("stderr_sha256") != sha256_bytes(stderr) \
            or terminal.get("stderr_bytes") != len(stderr):
        raise V2BError("S5 attempt terminal byte drift")
    return rows


def validate_execution(directory, expected_manifest, runtime,
                       require_production=True):
    validate_lean_verify_manifest(expected_manifest)
    validate_runtime(runtime, live_files=require_production)
    runtime_sha = _runtime_sha(runtime)
    if expected_manifest["runtimeSha256"] != runtime_sha:
        raise V2BError("S5 manifest/runtime binding drift")
    (paths, manifest, manifest_sha, stored_runtime, stored_runtime_sha,
     evidence, evidence_sha, stdout, stderr) = _read_bundle_files(directory)
    if manifest != expected_manifest or stored_runtime != runtime \
            or stored_runtime_sha != runtime_sha \
            or set(evidence) != EVIDENCE_KEYS:
        raise V2BError("S5 execution manifest/evidence key drift")
    nonce = evidence.get("channel_nonce")
    if not _hex(nonce):
        raise V2BError("S5 evidence channel nonce is malformed")
    backend = evidence.get("execution_backend")
    if backend not in ("bubblewrap", "none-test-only") \
            or require_production and backend != "bubblewrap":
        raise V2BError("S5 execution backend is not production bubblewrap")
    if evidence.get("schema") != EXECUTION_SCHEMA \
            or evidence.get("mode") != manifest["mode"] \
            or evidence.get("invocation_binding") != \
            manifest["invocationBinding"] \
            or evidence.get("semantic_context_binding") != \
            manifest["semanticContextBinding"] \
            or evidence.get("manifest_sha256") != manifest_sha \
            or evidence.get("runtime_sha256") != runtime_sha \
            or evidence.get("environment_sha256") != \
            runtime["child_environment_sha256"] \
            or evidence.get("sandbox_contract_sha256") != \
            SANDBOX_CONTRACT_SHA256 \
            or evidence.get("resource_limits") != RESOURCE_LIMITS \
            or evidence.get("timeout_seconds") != \
            RESOURCE_LIMITS["timeout_seconds"] \
            or evidence.get("cwd") != runtime["cwd"] \
            or evidence.get("stdout_sha256") != sha256_bytes(stdout) \
            or evidence.get("stdout_bytes") != len(stdout) \
            or evidence.get("stderr_sha256") != sha256_bytes(stderr) \
            or evidence.get("stderr_bytes") != len(stderr):
        raise V2BError("S5 execution evidence binding drift")
    if any(not _hex(evidence.get(field)) for field in (
                "attempt_id", "attempt_open_sha256", "go_intent_sha256",
                "attempt_terminal_sha256")) \
            or evidence.get("go_accepted_sha256") is not None \
            and not _hex(evidence.get("go_accepted_sha256")) \
            or evidence.get("classification") not in CLASSIFICATIONS \
            or type(evidence.get("timed_out")) is not bool \
            or type(evidence.get("output_limited")) is not bool \
            or type(evidence.get("returncode")) is not int \
            or type(evidence.get("wall_time_ns")) is not int \
            or evidence["wall_time_ns"] < 0:
        raise V2BError("S5 execution scalar evidence drift")
    if evidence.get("go_accepted_sha256") is None:
        if manifest["mode"] != "candidate" \
                or evidence.get("authenticated_prefix_stage") != \
                "candidate-awaiting-authorization" \
                or evidence.get("classification") not in (
                    "candidate-timeout", "candidate-output-limit",
                    "candidate-terminated") \
                or evidence.get("outcome_bearing") is not True:
            raise V2BError("S5 missing GO acknowledgment is not a frozen zero")
    sample_id = (manifest["samples"][0]["id"]
                 if manifest["mode"] == "candidate" else None)
    baseline_sha = (manifest["baselineCertificate"][
        "baselineEvidenceSha256"]
        if manifest["mode"] == "candidate" else None)
    if evidence.get("sample_id") != sample_id \
            or evidence.get("baseline_evidence_sha256") != baseline_sha:
        raise V2BError("S5 execution sample/baseline membership drift")
    run_root = os.path.dirname(os.path.dirname(directory))
    journal = _validate_attempt_journal(
        run_root, manifest, evidence, nonce, stdout, stderr)
    terminal = journal["terminal"]
    for field in (
            "returncode", "timed_out", "output_limited", "wall_time_ns",
            "authenticated_prefix_stage", "protocol_valid",
            "protocol_error_sha256", "classification", "outcome_bearing"):
        if terminal.get(field) != evidence.get(field):
            raise V2BError(f"S5 attempt terminal {field} drift")
    staged_manifest = _input_manifest_path(run_root, manifest)
    expected_inputs = _direct_input_hashes(
        manifest, runtime, staged_manifest)
    if evidence.get("input_hashes_before") != expected_inputs \
            or evidence.get("input_hashes_after") != expected_inputs \
            or journal["open"].get("direct_inputs_before") != \
            evidence["input_hashes_before"] \
            or terminal.get("direct_inputs_after") != \
            evidence["input_hashes_after"]:
        raise V2BError("S5 direct input hashes drift")
    expected_argv = (_sandbox_argv(runtime, manifest, staged_manifest)
                     if backend == "bubblewrap" else
                     [runtime["lean"]["path"], "--run",
                      runtime["driver"]["path"], staged_manifest])
    if evidence.get("argv") != expected_argv:
        raise V2BError("S5 execution argv drift")
    classified = classify_execution(
        manifest, stdout, nonce, evidence["returncode"],
        timed_out=evidence["timed_out"],
        output_limited=evidence["output_limited"],
        authorization_committed=True)
    for field in (
            "authenticated_prefix_stage", "protocol_valid",
            "protocol_error_sha256", "classification", "outcome_bearing"):
        if evidence.get(field) != classified[field]:
            raise V2BError(f"S5 execution classification {field} drift")
    if not classified["protocol_valid"]:
        raise V2BError("S5 authenticated execution transcript is malformed")
    return dict(
        directory=directory, manifest=manifest, evidence=evidence,
        evidence_sha256=evidence_sha, stdout=stdout, stderr=stderr,
        parsed=classified["parsed"], reused=True)


def load_verified_baseline(directory, runtime, require_production=True):
    manifest, _ = load_json(os.path.join(directory, "manifest.json"))
    if manifest.get("mode") != "baseline":
        raise V2BError("S5 baseline bundle carries candidate manifest")
    envelope = validate_execution(
        directory, manifest, runtime,
        require_production=require_production)
    if envelope["evidence"]["classification"] != "baseline-verified":
        raise V2BError("S5 baseline bundle is not certifiable")
    complete = {key: envelope["parsed"][key] for key in
                ("prevalidation", "baseline", "samples")}
    certificate = lean_baseline_certificate(
        complete, manifest, envelope["evidence_sha256"])
    envelope["certificate"] = certificate
    return envelope


def classify_candidate(directory, runtime, require_production=True):
    manifest, _ = load_json(os.path.join(directory, "manifest.json"))
    if manifest.get("mode") != "candidate":
        raise V2BError("S5 candidate classifier received a baseline bundle")
    envelope = validate_execution(
        directory, manifest, runtime,
        require_production=require_production)
    classification = envelope["evidence"]["classification"]
    if classification == "verified-pass":
        envelope["pass"] = 1
    elif classification in (
            "verification-failure", "candidate-timeout",
            "candidate-output-limit", "candidate-terminated"):
        envelope["pass"] = 0
    else:
        raise V2BError("S5 candidate bundle has no valid binary outcome")
    return envelope


def _invocation_lock_path(run_dir, manifest):
    return os.path.join(run_dir, "locks",
                        manifest["invocationBinding"] + ".lock")


def run_fresh(manifest, runtime, run_dir,
              baseline_directory=None, allow_unisolated_test=False,
              _nonce_for_test=None):
    """Serialize one invocation through validation, execution, and publish."""
    validate_lean_verify_manifest(manifest)
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    lock_path = _invocation_lock_path(run_dir, manifest)
    os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as err:
        raise V2BError(f"cannot open S5 invocation lock: {err}") from err
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError as err:
            raise V2BError(f"S5 invocation lock failed: {err}") from err
        return _run_fresh_locked(
            manifest, runtime, run_dir,
            baseline_directory=baseline_directory,
            allow_unisolated_test=allow_unisolated_test,
            _nonce_for_test=_nonce_for_test)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _run_fresh_locked(manifest, runtime, run_dir,
                      baseline_directory=None, allow_unisolated_test=False,
                      _nonce_for_test=None):
    """Run or revalidate one exact manifest-bound fresh process."""
    validate_lean_verify_manifest(manifest)
    validate_runtime(runtime, live_files=not allow_unisolated_test)
    runtime_sha = _runtime_sha(runtime)
    if manifest["runtimeSha256"] != runtime_sha:
        raise V2BError("S5 run manifest/runtime binding drift")
    require_production = not allow_unisolated_test
    if require_production:
        _require_private_run_root(run_dir, runtime)
        _validate_manifest_setup_join(manifest, runtime)
    if manifest["mode"] == "candidate":
        if not isinstance(baseline_directory, str) or not baseline_directory:
            raise V2BError("candidate S5 run requires validated baseline")
        baseline = load_verified_baseline(
            baseline_directory, runtime,
            require_production=require_production)
        if manifest["baselineCertificate"] != baseline["certificate"]:
            raise V2BError("candidate S5 certificate is not baseline-derived")
    elif baseline_directory is not None:
        raise V2BError("baseline S5 run cannot consume another baseline")
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    final_dir = _bundle_directory(run_dir, manifest)
    if os.path.exists(final_dir):
        return validate_execution(
            final_dir, manifest, runtime,
            require_production=require_production)
    manifest_path, manifest_blob = _stage_manifest(run_dir, manifest)
    nonce = (_nonce_for_test if _nonce_for_test is not None
             else secrets.token_hex(32))
    if not _hex(nonce) or _nonce_for_test is not None \
            and not allow_unisolated_test:
        raise V2BError("S5 test nonce may only be used by unisolated tests")
    backend = "none-test-only" if allow_unisolated_test else "bubblewrap"
    argv = ([runtime["lean"]["path"], "--run",
             runtime["driver"]["path"], manifest_path]
            if allow_unisolated_test else
            _sandbox_argv(runtime, manifest, manifest_path))
    env = (os.environ.copy() if allow_unisolated_test else
           dict(runtime["child_environment"]))
    before = _direct_input_hashes(manifest, runtime, manifest_path)
    attempt = _prepare_attempt(
        run_dir, manifest, manifest_blob, runtime_sha, nonce, before)
    result = _execute(
        argv, runtime["cwd"], env, nonce, manifest, RESOURCE_LIMITS,
        attempt,
        enforce_address_space=not allow_unisolated_test)
    after = _direct_input_hashes(manifest, runtime, manifest_path)
    classified = classify_execution(
        manifest, result["stdout"], nonce, result["returncode"],
        timed_out=result["timed_out"],
        output_limited=result["output_limited"],
        authorization_committed=result["go_intent_sha256"] is not None)
    _write_new_bytes(os.path.join(attempt["directory"], "stdout.bin"),
                     result["stdout"])
    _write_new_bytes(os.path.join(attempt["directory"], "stderr.bin"),
                     result["stderr"])
    terminal = dict(
        schema=ATTEMPT_TERMINAL_SCHEMA,
        attempt_id=attempt["attempt_id"],
        invocation_binding=manifest["invocationBinding"],
        mode=manifest["mode"],
        nonce_sha256=sha256_bytes(nonce.encode("ascii")),
        go_intent_sha256=result["go_intent_sha256"],
        go_accepted_sha256=result["go_accepted_sha256"],
        returncode=result["returncode"], timed_out=result["timed_out"],
        output_limited=result["output_limited"],
        wall_time_ns=result["wall_time_ns"],
        stdout_sha256=sha256_bytes(result["stdout"]),
        stdout_bytes=len(result["stdout"]),
        stderr_sha256=sha256_bytes(result["stderr"]),
        stderr_bytes=len(result["stderr"]),
        authenticated_prefix_stage=classified[
            "authenticated_prefix_stage"],
        protocol_valid=classified["protocol_valid"],
        protocol_error_sha256=classified["protocol_error_sha256"],
        classification=classified["classification"],
        outcome_bearing=classified["outcome_bearing"],
        direct_inputs_after=after,
        direct_inputs_after_sha256=sha256_sorted_json(after))
    terminal_sha = _write_new_durable_json(
        os.path.join(attempt["directory"], "terminal.json"), terminal)
    sample_id = (manifest["samples"][0]["id"]
                 if manifest["mode"] == "candidate" else None)
    baseline_sha = (manifest["baselineCertificate"][
        "baselineEvidenceSha256"]
        if manifest["mode"] == "candidate" else None)
    evidence = dict(
        schema=EXECUTION_SCHEMA, mode=manifest["mode"],
        invocation_binding=manifest["invocationBinding"],
        semantic_context_binding=manifest["semanticContextBinding"],
        sample_id=sample_id, baseline_evidence_sha256=baseline_sha,
        manifest_sha256=sha256_bytes(manifest_blob),
        runtime_sha256=runtime_sha,
        channel_nonce=nonce, argv=argv, cwd=runtime["cwd"],
        environment_sha256=runtime["child_environment_sha256"],
        sandbox_contract_sha256=SANDBOX_CONTRACT_SHA256,
        resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        execution_backend=backend,
        timeout_seconds=RESOURCE_LIMITS["timeout_seconds"],
        timed_out=result["timed_out"],
        output_limited=result["output_limited"],
        returncode=result["returncode"],
        wall_time_ns=result["wall_time_ns"],
        stdout_sha256=sha256_bytes(result["stdout"]),
        stdout_bytes=len(result["stdout"]),
        stderr_sha256=sha256_bytes(result["stderr"]),
        stderr_bytes=len(result["stderr"]),
        authenticated_prefix_stage=classified[
            "authenticated_prefix_stage"],
        protocol_valid=classified["protocol_valid"],
        protocol_error_sha256=classified["protocol_error_sha256"],
        classification=classified["classification"],
        outcome_bearing=classified["outcome_bearing"],
        input_hashes_before=before, input_hashes_after=after,
        attempt_id=attempt["attempt_id"],
        attempt_open_sha256=attempt["attempt_open_sha256"],
        go_intent_sha256=result["go_intent_sha256"],
        go_accepted_sha256=result["go_accepted_sha256"],
        attempt_terminal_sha256=terminal_sha)
    if set(evidence) != EVIDENCE_KEYS:
        raise AssertionError("internal S5 evidence schema drift")
    partial_root = os.path.join(run_dir, ".partial")
    os.makedirs(partial_root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="execution-", dir=partial_root)
    try:
        _write_new_bytes(os.path.join(work, "manifest.json"), manifest_blob)
        _write_new_bytes(os.path.join(work, "runtime.json"),
                         _sorted_json_bytes(runtime))
        _write_new_bytes(os.path.join(work, "stdout.bin"), result["stdout"])
        _write_new_bytes(os.path.join(work, "stderr.bin"), result["stderr"])
        evidence_sha = _write_new_durable_json(
            os.path.join(work, "evidence.json"), evidence)
        if before != after:
            raise V2BError("S5 direct inputs changed during execution")
        if classified["outcome_bearing"] and classified["protocol_valid"]:
            destination = final_dir
            os.makedirs(os.path.dirname(destination), exist_ok=True)
        else:
            destination = attempt["directory"]
        if destination == final_dir:
            os.rename(work, destination)
    except OSError as err:
        raise V2BError(f"cannot publish S5 execution bundle: {err}") from err
    finally:
        if os.path.isdir(work):
            shutil.rmtree(work)
    if not classified["outcome_bearing"]:
        raise V2BError(
            f"S5 pre-outcome harness attempt retained at "
            f"{attempt['directory']}")
    if not classified["protocol_valid"]:
        raise V2BError(
            f"S5 authenticated transcript invalid at "
            f"{attempt['directory']}")
    envelope = validate_execution(
        destination, manifest, runtime,
        require_production=require_production)
    envelope["evidence_sha256"] = evidence_sha
    return envelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--setup-index", required=True)
    parser.add_argument("--elan-home", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--baseline-dir")
    args = parser.parse_args()
    try:
        if not source_clean():
            raise V2BError("S5 production source tree is dirty")
        commit_start, tree_start = head_commit(), source_tree_hash()
        runtime = build_runtime(args.setup_index, args.elan_home)
        manifest, _ = load_json(args.manifest)
        envelope = run_fresh(
            manifest, runtime, args.run_dir,
            baseline_directory=args.baseline_dir)
        if not source_clean() or head_commit() != commit_start \
                or source_tree_hash() != tree_start:
            raise V2BError("S5 production source changed during execution")
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    print(f"[v2b-lean-s5] {envelope['evidence']['classification']} "
          f"{envelope['directory']} "
          f"({envelope['evidence_sha256'][:12]})")


if __name__ == "__main__":
    main()
