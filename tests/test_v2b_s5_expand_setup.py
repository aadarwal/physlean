#!/usr/bin/env python3
"""Real cross-pin test for the transitive, no-build S5 ModuleSetup helper."""
import json
import os
import shutil
import subprocess
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "s5_setup_project")
DRIVER_SOURCE = os.path.join(
    ROOT, "lean_drivers", "V2BS5ExpandSetup.lean")
DRIVER_LAKEFILE = os.path.join(ROOT, "lean_drivers", "lakefile.lean")
TOOLCHAINS = (
    "leanprover/lean4:v4.32.0",
    "leanprover/lean4:v4.33.0-rc2",
)


def _elan():
    return shutil.which("elan")


def _installed(elan, toolchain):
    result = subprocess.run(
        [elan, "toolchain", "list"], capture_output=True, text=True,
        check=False)
    return toolchain in result.stdout


def _run(args, *, env=None, expect_success=True):
    result = subprocess.run(args, env=env, capture_output=True, text=True,
                            check=False, timeout=120)
    if expect_success:
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    else:
        assert result.returncode != 0, (args, result.stdout, result.stderr)
    return result


def _toolchain_root(elan, toolchain, env):
    result = _run([elan, "run", toolchain, "lean", "--print-prefix"],
                  env=env)
    root = result.stdout.strip()
    assert root and os.path.isabs(root) and os.path.isdir(root), root
    return root


def _flatten_import_arts(value):
    flattened = {}
    for module, groups in value.items():
        assert isinstance(module, str) and module
        assert isinstance(groups, list)
        if all(isinstance(path, str) for path in groups):
            paths = groups
        else:
            assert all(isinstance(group, list) for group in groups)
            paths = [path for group in groups for path in group]
        assert paths and all(isinstance(path, str) and path for path in paths)
        flattened[module] = paths
    return flattened


def _exercise(toolchain):
    elan = _elan()
    if elan is None or not _installed(elan, toolchain):
        print(f"[skip] {toolchain} is not installed")
        return
    env = dict(os.environ, ELAN_TOOLCHAIN=toolchain)
    with tempfile.TemporaryDirectory() as temp:
        fixture = os.path.join(temp, "fixture")
        driver = os.path.join(temp, "driver")
        shutil.copytree(FIXTURE, fixture)
        os.makedirs(driver)
        shutil.copy2(DRIVER_SOURCE, driver)
        shutil.copy2(DRIVER_LAKEFILE, driver)

        _run([elan, "run", toolchain, "lake", "build", "--dir",
              fixture, "Probe"], env=env)
        _run([elan, "run", toolchain, "lake", "build", "--dir",
              driver, "v2bS5ExpandSetup"], env=env)

        executable = os.path.join(
            driver, ".lake", "build", "bin", "v2bS5ExpandSetup")
        source = os.path.join(fixture, "Probe", "Target.lean")
        command = [executable, fixture, source,
                   _toolchain_root(elan, toolchain, env)]
        result = _run(command, env=env)
        lines = result.stdout.splitlines()
        assert len(lines) == 1, (result.stdout, result.stderr)
        setup = json.loads(lines[0])
        assert setup["name"] == "Probe.Target", setup
        assert setup["package"] == "S5SetupProbe", setup
        import_arts = _flatten_import_arts(setup["importArts"])
        assert set(import_arts) == {"Probe.Direct", "Probe.Transitive"}, setup
        assert all(os.path.isfile(path)
                   for paths in import_arts.values() for path in paths)

        # `noBuild := true` must turn a missing dependency into a failure and
        # must not recreate it.  This is the mutation barrier production uses.
        missing = import_arts["Probe.Direct"][0]
        os.unlink(missing)
        assert not os.path.exists(missing)
        _run(command, env=env, expect_success=False)
        assert not os.path.exists(missing)


def test_transitive_setup_is_cross_pin_and_no_build():
    for toolchain in TOOLCHAINS:
        _exercise(toolchain)


if __name__ == "__main__":
    test_transitive_setup_is_cross_pin_and_no_build()
    print("V2B S5 TRANSITIVE SETUP TESTS PASS")
