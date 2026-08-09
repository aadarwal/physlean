#!/usr/bin/env python3
"""Frozen ladder tier registry + launchers (NLL_LADDER_EXPLORATORY_AMENDMENT).

The q25c-1.5b tier must restate the sealed pilot constants exactly; every
tier's revision must equal the append-only models.json pin; activation is
single-tier per process; the paired validator resolves the tier from the
scored model and rejects cross-tier battery files.
"""
import contextlib
import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import validity_battery as vb  # noqa: E402


def _reload():
    return importlib.reload(vb)


@contextlib.contextmanager
def _expect(exc_type, needle=None):
    try:
        yield
    except exc_type as err:
        if needle is not None and needle not in str(err):
            raise AssertionError(
                f"expected {needle!r} in {exc_type.__name__}: {err}")
    else:
        raise AssertionError(f"expected {exc_type.__name__}, none raised")


def test_registry_shape_and_1p5b_identity():
    m = _reload()
    assert set(m.PILOT_TIERS) == {
        "q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b"}
    files = [t["battery_file"] for t in m.PILOT_TIERS.values()]
    models = [t["model"] for t in m.PILOT_TIERS.values()]
    fams = [t["family"] for t in m.PILOT_TIERS.values()]
    assert len(set(files)) == 4 and len(set(models)) == 4
    assert len(set(fams)) == 4
    for t in m.PILOT_TIERS.values():
        assert len(t["revision"]) == 40
        assert all(c in "0123456789abcdef" for c in t["revision"])
        lo, hi = t["param_range"]
        assert 0 < lo < hi
    t15 = m.PILOT_TIERS["q25c-1.5b"]
    assert t15["model"] == m.PILOT_MODEL == "Qwen/Qwen2.5-Coder-1.5B"
    assert t15["revision"] == m.PILOT_REVISION
    assert t15["family"] == m.PILOT_FAMILY == "q25c-1p5b"
    assert t15["battery_file"] == m.PILOT_BATTERY_FILE \
        == "battery_pilot_1p5b.json"
    assert t15["param_range"] == (1.2e9, 1.8e9)


def test_registry_matches_models_json_pins():
    m = _reload()
    pins = json.load(open(os.path.join(ROOT, "models.json")))
    for t in m.PILOT_TIERS.values():
        assert pins[t["model"]]["sha"] == t["revision"]


def test_activation_rebinds_and_guards():
    m = _reload()
    tier = m.activate_pilot_tier("q25c-3b")
    assert tier["tag"] == "q25c-3b"
    assert m.PILOT_MODEL == "Qwen/Qwen2.5-Coder-3B"
    assert m.PILOT_REVISION == "09d9bc5d376b0cfa0100a0694ea7de7232525803"
    assert m.PILOT_FAMILIES == {"q25c-3b": "Qwen/Qwen2.5-Coder-3B"}
    assert m.PILOT_PARAM_RANGES == {"q25c-3b": (2.5e9, 3.5e9)}
    assert m.PILOT_BATTERY_FILE == "battery_pilot_3b.json"
    m.activate_pilot_tier("q25c-3b")  # same tier is idempotent
    with _expect(RuntimeError, "already active"):
        m.activate_pilot_tier("q25c-7b")
    with _expect(RuntimeError, "unknown pilot tier"):
        m.activate_pilot_tier("q25c-99b")
    m2 = _reload()  # defaults restore to the sealed pilot
    assert m2.PILOT_MODEL == "Qwen/Qwen2.5-Coder-1.5B"
    assert m2.PILOT_BATTERY_FILE == "battery_pilot_1p5b.json"
    with _expect(RuntimeError, "unknown pilot tier"):
        m2.activate_pilot_tier("q25c-99b")


def test_resolver_is_exact():
    m = _reload()
    assert m.resolve_pilot_tier_for_model(
        "Qwen/Qwen2.5-Coder-0.5B") == "q25c-0.5b"
    assert m.resolve_pilot_tier_for_model(
        "Qwen/Qwen2.5-Coder-7B") == "q25c-7b"
    with _expect(RuntimeError, "no unique pilot tier"):
        m.resolve_pilot_tier_for_model("Qwen/Qwen2.5-Coder-32B")


def test_paired_validator_resolves_tier_and_checks_filename():
    _reload()
    import eval_paired as ep
    with _expect(ep.V2BError, "battery-filename"):
        ep.validate_pilot_battery(
            {"model": "Qwen/Qwen2.5-Coder-1.5B"}, "s", "h", "e",
            "Qwen/Qwen2.5-Coder-3B",
            "09d9bc5d376b0cfa0100a0694ea7de7232525803",
            battery_path="results_v2/battery/battery_pilot_1p5b.json")
    with _expect(ep.V2BError, "model-identity"):
        _reload()
        ep.validate_pilot_battery(
            {"model": "Qwen/Qwen2.5-Coder-1.5B"}, "s", "h", "e",
            "Qwen/Qwen2.5-Coder-3B",
            "09d9bc5d376b0cfa0100a0694ea7de7232525803",
            battery_path="results_v2/battery/battery_pilot_3b.json")
    _reload()


LADDER_SCRIPT = os.path.join(ROOT, "slurm", "v2b_paired_ladder.sbatch")
LADDER_BATTERY_SCRIPT = os.path.join(
    ROOT, "slurm", "battery_pilot_ladder.sbatch")


def test_ladder_paired_launcher_contract():
    src = open(LADDER_SCRIPT, encoding="utf-8").read()
    assert "V2B_MODEL_TIER" in src and "--array=0-4" in src
    assert "q25c-1.5b)" in src and "sealed pilot" in src
    for tier, model, battery in (
            ("q25c-0.5b", "Qwen/Qwen2.5-Coder-0.5B", "battery_pilot_0p5b"),
            ("q25c-3b", "Qwen/Qwen2.5-Coder-3B", "battery_pilot_3b"),
            ("q25c-7b", "Qwen/Qwen2.5-Coder-7B", "battery_pilot_7b")):
        assert f"{tier})" in src
        assert model in src
        assert f"results_v2/battery/{battery}.json" in src
    assert '--model "$V2B_MODEL"' in src
    assert '--pilot-battery "$V2B_TIER_BATTERY"' in src
    assert "results_v2/v2b/paired/$V2B_MODEL_TIER" in src
    assert "${V2B_MODEL_TIER}/${V2B_SOURCE_COMMIT:0:12}" in src
    assert "git ls-files --error-unmatch" in src
    assert "git diff --quiet HEAD" in src
    assert src.count("v2b_assert_source_identity") >= 3
    assert src.count("v2b_assert_corpus_identity") >= 3
    assert "job${V2B_ASSEMBLY_JOB}_${V2B_TASK}_${V2B_TAG}.json" in src
    for sha in ("87adeaebd370a3b6a41ac4f044fddd4bf81803ad",
                "76e1c118b0700b4ceafe99532e887d6431625e1a",
                "e882411d1b6bcbdfdd336d4c509c6cc72e96842d",
                "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c",
                "440fe546589c4e496235d712bc29783ecf5a5fec"):
        assert sha in src
    assert "V2B-PAIRED-LADDER-DONE" in src


def test_ladder_battery_launcher_contract():
    src = open(LADDER_BATTERY_SCRIPT, encoding="utf-8").read()
    assert "V2B_MODEL_TIER" in src
    assert 'validity_battery.py --pilot-tier "$V2B_MODEL_TIER"' in src
    assert "never rerun" in src  # the sealed 1.5b battery is refused
    assert "git status --porcelain -- . ':(exclude)results_v2'" in src
    assert "nvidia-smi -L" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[ok] {name}")
    print("PILOT TIER TESTS PASS")
