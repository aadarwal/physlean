#!/usr/bin/env python3
"""The preflight inventory must not erase prior raw-evidence listings."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preflight_check as preflight


def _reset(base):
    preflight.BASE = base
    preflight.OK = True
    preflight.report = {"checks": {}}
    os.makedirs(os.path.join(base, "nll_dumps"), exist_ok=True)
    os.makedirs(os.path.join(base, "results_v2"), exist_ok=True)


def test_empty_first_inventory_is_allowed():
    with tempfile.TemporaryDirectory() as td:
        _reset(td)
        preflight.raw_inventory()
        path = os.path.join(td, "results_v2", "raw_inventory.json")
        assert json.load(open(path)) == {}
        assert preflight.report["checks"]["raw-inventory"]["ok"] is True


def test_empty_scan_cannot_truncate_nonempty_inventory():
    with tempfile.TemporaryDirectory() as td:
        _reset(td)
        path = os.path.join(td, "results_v2", "raw_inventory.json")
        prior = {"old.csv.gz": {
            "bytes": 3, "sha256": "a" * 64, "quarantined": False}}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(prior, handle)
        before = open(path, "rb").read()
        preflight.raw_inventory()
        assert open(path, "rb").read() == before
        assert preflight.report["checks"]["raw-inventory"]["ok"] is False
        assert preflight.OK is False


def test_nonempty_scan_refreshes_atomically():
    with tempfile.TemporaryDirectory() as td:
        _reset(td)
        dump = os.path.join(td, "nll_dumps", "new.csv.gz")
        with open(dump, "wb") as handle:
            handle.write(b"new")
        preflight.raw_inventory()
        path = os.path.join(td, "results_v2", "raw_inventory.json")
        value = json.load(open(path))
        assert list(value) == ["new.csv.gz"]
        assert value["new.csv.gz"]["bytes"] == 3
        assert os.stat(path).st_mode & 0o777 == 0o644
        assert not [name for name in os.listdir(os.path.dirname(path))
                    if name.startswith(".raw-inventory-")]


def test_inventory_allows_addition_and_hash_preserving_quarantine_rename():
    with tempfile.TemporaryDirectory() as td:
        _reset(td)
        dumps = os.path.join(td, "nll_dumps")
        path = os.path.join(td, "results_v2", "raw_inventory.json")
        old = os.path.join(dumps, "old.csv.gz")
        with open(old, "wb") as handle:
            handle.write(b"old")
        preflight.raw_inventory()
        prior = json.load(open(path))
        with open(os.path.join(dumps, "new.csv.gz"), "wb") as handle:
            handle.write(b"new")
        preflight.raw_inventory()
        assert set(json.load(open(path))) == {"old.csv.gz", "new.csv.gz"}
        renamed = old + ".quarantine-20260808"
        os.rename(old, renamed)
        preflight.raw_inventory()
        refreshed = json.load(open(path))
        assert "old.csv.gz" not in refreshed
        assert refreshed[os.path.basename(renamed)]["sha256"] == \
            prior["old.csv.gz"]["sha256"]
        assert refreshed[os.path.basename(renamed)]["quarantined"] is True


def test_multiple_identical_quarantine_copies_remain_valid_survivors():
    with tempfile.TemporaryDirectory() as td:
        _reset(td)
        dumps = os.path.join(td, "nll_dumps")
        original = os.path.join(dumps, "repeat.csv.gz")
        with open(original, "wb") as handle:
            handle.write(b"deterministic")
        preflight.raw_inventory()
        first = original + ".quarantine-first"
        second = original + ".quarantine-second"
        os.rename(original, first)
        shutil.copyfile(first, second)
        preflight.raw_inventory()
        assert set(json.load(open(os.path.join(
            td, "results_v2", "raw_inventory.json")))) == {
                os.path.basename(first), os.path.basename(second)}
        shutil.copyfile(first, original)
        preflight.raw_inventory()
        assert preflight.report["checks"]["raw-inventory"]["ok"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PREFLIGHT RAW INVENTORY TESTS PASS")
