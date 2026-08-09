#!/usr/bin/env python3
"""Adversarial tests for the oracle-safe S5 exact-file projection."""
import copy
import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_v2b_lean_setups import SETUP_INDEX_SCHEMA
from v2b_common import V2BError, sha256_file, sha256_sorted_json
from v2b_s5_visibility import (
    IMPORT_CLOSURE_SCHEMA, normalize_expanded_setup, produce_visibility,
    validate_visibility)


HEX40 = "1" * 40
HEX64 = "2" * 64


def _write(path, value, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(value, bytes) else "w"
    kwargs = {} if isinstance(value, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as handle:
        handle.write(value)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True)
        handle.write("\n")


def _artifact(path, role):
    return dict(path=path, sha256=sha256_file(path), roles=[role])


def _setup(name, direct, transitive, runtime, grouped=True):
    if grouped:
        import_arts = {
            "Probe.Direct": [[direct]],
            "Probe.Transitive": [[transitive]],
        }
    else:
        import_arts = {
            "Probe.Direct": [direct],
            "Probe.Transitive": [transitive],
        }
    return dict(
        dynlibs=[runtime], importArts=import_arts, isModule=True, name=name,
        options={"autoImplicit": False}, package="Fixture",
        plugins=[{"path": runtime, "initFn": "initialize_fixture"}],
        imports=[dict(module="Probe.Direct", importAll=False,
                      isExported=False, isMeta=False)])


def _broad_index(paths, artifacts, setup):
    source = paths["source"]
    setup_row = dict(
        module="Probe.Target", source=source, source_rel="Probe/Target.lean",
        source_sha256=sha256_file(source), setup=setup,
        setup_sha256=sha256_file(setup),
        setup_semantics_sha256=sha256_sorted_json(
            json.load(open(setup, encoding="utf-8"))),
        batch_index=0)
    rows = [setup_row]
    setups = {source: setup}
    batches = [dict(
        batch_index=0, n_modules=1, first_module="Probe.Target",
        last_module="Probe.Target", targets_sha256=HEX64,
        stdout_sha256=HEX64, stderr_sha256=HEX64,
        setup_rows_sha256=HEX64)]
    roots = [dict(path=os.path.dirname(paths["direct"]),
                  roles=["lean-search-root"], state="directory")]
    lake_environment = dict(
        LEAN_PATH=os.path.dirname(paths["direct"]),
        LEAN_SRC_PATH=os.path.join(paths["workspace"], "Probe"),
        LD_LIBRARY_PATH=os.path.dirname(paths["runtime"]),
        DYLD_LIBRARY_PATH=None,
        PATH=os.path.dirname(paths["lean"]))
    artifacts = sorted(artifacts, key=lambda row: row["path"])
    index = dict(
        schema=SETUP_INDEX_SCHEMA, repo="fixture", language="lean",
        corpus_git_sha=HEX40,
        extraction=dict(path=paths["extraction"], sha256=HEX64,
                        schema="v2a_lean_extract_v3"),
        corpus_root=paths["workspace"],
        toolchain="leanprover/lean4:v4.33.0-rc2",
        lean_toolchain_sha256=sha256_file(paths["pin"]),
        lake=dict(path=paths["lake"], sha256=sha256_file(paths["lake"]),
                  version="Lake version fixture"),
        lean=dict(path=paths["lean"], sha256=sha256_file(paths["lean"]),
                  version="Lean version fixture"),
        environment_probe=dict(path=paths["probe"], sha256=HEX64),
        lake_environment=lake_environment,
        lake_environment_sha256=sha256_sorted_json(lake_environment),
        n_search_roots=len(roots), search_roots=roots,
        search_roots_sha256=sha256_sorted_json(roots),
        n_search_directories=0, search_directories=[],
        search_directories_sha256=sha256_sorted_json([]),
        n_search_symlinks=0, search_symlinks=[],
        search_symlinks_sha256=sha256_sorted_json([]),
        n_modules=1, n_batches=1, batch_size=1,
        setups=setups, setups_sha256=sha256_sorted_json(setups),
        rows=rows, rows_sha256=sha256_sorted_json(rows),
        batches=batches, batches_sha256=sha256_sorted_json(batches),
        n_artifacts=len(artifacts), artifacts=artifacts,
        artifacts_sha256=sha256_sorted_json(artifacts),
        generator=dict(source_commit=HEX40, source_tree_hash=HEX64,
                       program="prepare_v2b_lean_setups.py"))
    _write_json(paths["index"], index)


def _fixture(root, grouped=True):
    root = os.path.realpath(root)
    workspace = os.path.join(root, "workspace")
    toolchain = os.path.join(root, "toolchain")
    direct = os.path.join(
        workspace, ".lake", "build", "lib", "lean", "Probe",
        "Direct.olean")
    transitive = os.path.join(
        workspace, ".lake", "build", "lib", "lean", "Probe",
        "Transitive.olean")
    runtime = os.path.join(toolchain, "lib", "libLeanFixture.so")
    lean = os.path.join(toolchain, "bin", "lean")
    lake = os.path.join(toolchain, "bin", "lake")
    paths = dict(
        workspace=workspace, toolchain=toolchain,
        source=os.path.join(workspace, "Probe", "Target.lean"),
        pin=os.path.join(workspace, "lean-toolchain"), direct=direct,
        transitive=transitive, runtime=runtime, lean=lean, lake=lake,
        helper=os.path.join(root, "v2bS5ExpandSetup"),
        setup=os.path.join(root, "expanded-setup.json"),
        closure=os.path.join(root, "closure.json"),
        index=os.path.join(root, "broad-index.json"),
        extraction=os.path.join(root, "extract.json"),
        probe="/usr/bin/env")
    _write(paths["source"], "import Probe.Direct\n\ndef target := 1\n")
    _write(paths["pin"], "leanprover/lean4:v4.33.0-rc2\n")
    _write(direct, b"direct-olean")
    _write(transitive, b"transitive-olean")
    _write(runtime, b"runtime-library")
    _write(lean, b"lean-executable", executable=True)
    _write(lake, b"lake-executable", executable=True)
    _write(paths["helper"], b"setup-helper", executable=True)
    _write(paths["extraction"], "{}\n")
    setup = _setup("Probe.Target", direct, transitive, runtime,
                   grouped=grouped)
    _write_json(paths["setup"], setup)
    modules = ["Probe.Direct", "Probe.Transitive"]
    closure = dict(
        schema=IMPORT_CLOSURE_SCHEMA, module="Probe.Target",
        source_sha256=sha256_file(paths["source"]), modules=modules,
        modules_sha256=sha256_sorted_json(modules))
    _write_json(paths["closure"], closure)
    artifacts = [
        _artifact(direct, "lean-search-artifact"),
        _artifact(transitive, "lean-search-artifact"),
        _artifact(runtime, "dynamic-search-artifact"),
    ]
    _broad_index(paths, artifacts, paths["setup"])
    return paths


def _produce(paths):
    return produce_visibility(
        "Probe.Target", paths["source"], paths["workspace"],
        paths["toolchain"], paths["helper"], paths["setup"],
        paths["closure"], paths["index"],
        [paths["lean"], paths["runtime"]])


def _expect_failure(call, fragment):
    try:
        call()
        assert False, f"accepted invalid input expected to mention {fragment}"
    except V2BError as err:
        assert fragment in str(err), str(err)


def test_projection_is_deterministic_exact_file_only_and_live_revalidates():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        first = _produce(paths)
        second = _produce(paths)
        assert first == second
        assert validate_visibility(first, live_files=True) is first
        assert first["mount_policy"] == {
            "mode": "exact-file-allowlist-v1",
            "source_transport": "framed-stdin",
            "bind_workspace_root": False,
            "bind_toolchain_root": False,
            "bind_search_roots": False,
        }
        allowlisted = {row["path"] for row in first["allowlist"]}
        assert paths["source"] not in allowlisted
        assert paths["setup"] not in allowlisted
        assert paths["helper"] not in allowlisted
        assert paths["index"] not in allowlisted
        assert paths["workspace"] not in allowlisted
        assert all(not os.path.isdir(path) for path in allowlisted)
        assert first["import_modules"] == [
            "Probe.Direct", "Probe.Transitive"]


def test_flat_432_and_grouped_433_setup_shapes_have_same_projection():
    with tempfile.TemporaryDirectory() as first_temp, \
            tempfile.TemporaryDirectory() as second_temp:
        flat = _fixture(first_temp, grouped=False)
        grouped = _fixture(second_temp, grouped=True)
        flat_setup = json.load(open(flat["setup"], encoding="utf-8"))
        grouped_setup = json.load(open(grouped["setup"], encoding="utf-8"))
        modules = ["Probe.Direct", "Probe.Transitive"]
        flat_rows = normalize_expanded_setup(
            flat_setup, "Probe.Target", modules)
        grouped_rows = normalize_expanded_setup(
            grouped_setup, "Probe.Target", modules)
        def semantic(rows):
            return [(os.path.basename(path), role, module)
                    for path, role, module in rows]
        assert semantic(flat_rows) == semantic(grouped_rows)
        assert _produce(flat)["n_allowlist"] == \
            _produce(grouped)["n_allowlist"]


def test_nontransitive_setup_and_missing_artifacts_fail_closed():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        setup = json.load(open(paths["setup"], encoding="utf-8"))
        del setup["importArts"]["Probe.Transitive"]
        _write_json(paths["setup"], setup)
        _expect_failure(lambda: _produce(paths), "nontransitive")

    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        os.unlink(paths["transitive"])
        _expect_failure(lambda: _produce(paths), "missing expanded setup")

    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        setup = json.load(open(paths["setup"], encoding="utf-8"))
        setup["importArts"]["Probe.Direct"] = [[paths["transitive"]]]
        _write_json(paths["setup"], setup)
        _expect_failure(lambda: _produce(paths), "matching .olean")


def test_target_module_source_and_olean_leakage_fail_closed():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        target_olean = os.path.join(
            paths["workspace"], ".lake", "build", "lib", "lean", "Probe",
            "Target.olean")
        _write(target_olean, b"forbidden-target")
        setup = json.load(open(paths["setup"], encoding="utf-8"))
        setup["dynlibs"].append(target_olean)
        _write_json(paths["setup"], setup)
        index = json.load(open(paths["index"], encoding="utf-8"))
        index["artifacts"].append(
            _artifact(target_olean, "lean-search-artifact"))
        index["artifacts"].sort(key=lambda row: row["path"])
        index["n_artifacts"] = len(index["artifacts"])
        index["artifacts_sha256"] = sha256_sorted_json(index["artifacts"])
        _write_json(paths["index"], index)
        _expect_failure(lambda: _produce(paths), "current target module")

    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        setup = json.load(open(paths["setup"], encoding="utf-8"))
        setup["plugins"].append(paths["source"])
        _write_json(paths["setup"], setup)
        _expect_failure(lambda: _produce(paths), "source text leaked")


def test_path_and_symlink_escapes_fail_before_allowlisting():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        outside = os.path.join(os.path.realpath(temp), "outside.olean")
        _write(outside, b"outside")
        link = paths["direct"]
        os.unlink(link)
        os.symlink(outside, link)
        _expect_failure(lambda: _produce(paths), "symlink target escapes")

    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        outside = os.path.join(
            os.path.realpath(temp), "foreign", "Probe", "Direct.olean")
        _write(outside, b"foreign")
        setup = json.load(open(paths["setup"], encoding="utf-8"))
        setup["importArts"]["Probe.Direct"] = [[outside]]
        _write_json(paths["setup"], setup)
        _expect_failure(lambda: _produce(paths), "escapes the exact artifact")


def test_safe_indexed_runtime_symlink_is_content_bound_and_target_included():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        target = os.path.join(paths["toolchain"], "lib", "libRuntime.so.1")
        link = os.path.join(paths["toolchain"], "lib", "libRuntime.so")
        _write(target, b"runtime-v1")
        os.symlink("libRuntime.so.1", link)
        index = json.load(open(paths["index"], encoding="utf-8"))
        index["artifacts"].extend([
            _artifact(link, "dynamic-search-artifact"),
            _artifact(target, "dynamic-search-artifact"),
        ])
        index["artifacts"].sort(key=lambda row: row["path"])
        index["n_artifacts"] = len(index["artifacts"])
        index["artifacts_sha256"] = sha256_sorted_json(index["artifacts"])
        index["search_symlinks"] = [dict(
            path=link, target="libRuntime.so.1",
            roles=["dynamic-search-artifact"])]
        index["n_search_symlinks"] = 1
        index["search_symlinks_sha256"] = sha256_sorted_json(
            index["search_symlinks"])
        _write_json(paths["index"], index)
        value = produce_visibility(
            "Probe.Target", paths["source"], paths["workspace"],
            paths["toolchain"], paths["helper"], paths["setup"],
            paths["closure"], paths["index"],
            [paths["lean"], paths["runtime"], link])
        rows = {row["path"]: row for row in value["allowlist"]}
        assert rows[link]["kind"] == "symlink"
        assert rows[link]["link_target"] == "libRuntime.so.1"
        assert rows[target]["kind"] == "file"
        assert "symlink-target" in rows[target]["roles"]
        assert validate_visibility(value, live_files=True) is value


def test_manifest_input_and_live_artifact_tampering_are_rejected():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        value = _produce(paths)
        tampered = copy.deepcopy(value)
        tampered["allowlist"][0]["sha256"] = "0" * 64
        _expect_failure(
            lambda: validate_visibility(tampered, live_files=False), "digest")
        _write(paths["direct"], b"mutated-after-production")
        _expect_failure(
            lambda: validate_visibility(value, live_files=True), "join")


def test_closure_and_broad_inventory_tampering_are_rejected():
    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        closure = json.load(open(paths["closure"], encoding="utf-8"))
        closure["modules_sha256"] = "0" * 64
        _write_json(paths["closure"], closure)
        _expect_failure(lambda: _produce(paths), "closure identity/hash")

    with tempfile.TemporaryDirectory() as temp:
        paths = _fixture(temp)
        index = json.load(open(paths["index"], encoding="utf-8"))
        for artifact in index["artifacts"]:
            if artifact["path"] == paths["direct"]:
                artifact["sha256"] = "0" * 64
        index["artifacts_sha256"] = sha256_sorted_json(index["artifacts"])
        _write_json(paths["index"], index)
        _expect_failure(lambda: _produce(paths), "does not join")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B S5 VISIBILITY TESTS PASS")
