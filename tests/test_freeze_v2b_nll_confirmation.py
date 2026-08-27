#!/usr/bin/env python3
"""Synthetic tests for the global confirmation implementation freeze."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freeze_v2b_nll_confirmation import (
    FILE_ROLES, FREEZE_SCHEMA, FREEZE_STATE, PROGRAM, SCHEMA_KEYS,
    STUDY_ID, build_freeze_value, protocol_record, validate_freeze)
from v2b_common import V2BError, sha256_sorted_json
from v2b_nll_confirmation import load_protocol


def _reject(function):
    try:
        function()
        assert False, "invalid implementation freeze succeeded"
    except V2BError:
        pass


def _fixture():
    protocol, _ = load_protocol()
    rows = [dict(path=path, sha256=("b" if path == PROGRAM else "a") * 64,
                 role=role)
            for path, role in sorted(FILE_ROLES.items())]
    value = build_freeze_value(protocol, rows, "c" * 40, "d" * 64)
    return protocol, value


def test_exact_global_freeze_contract_and_analyzer_registration_source():
    protocol, value = _fixture()
    assert validate_freeze(copy.deepcopy(value), protocol) == value
    assert value["schema"] == FREEZE_SCHEMA
    assert value["state"] == FREEZE_STATE
    assert value["study_id"] == STUDY_ID
    assert value["protocol"] == protocol_record()
    assert value["files_sha256"] == sha256_sorted_json(value["files"])
    files = {row["path"]: row for row in value["files"]}
    assert files["analyze_v2b_nll_confirmation.py"]["role"] == "analyzer"
    assert files[PROGRAM]["sha256"] == value["generator"][
        "program_sha256"]
    assert set(value["artifact_schemas"]) == set(SCHEMA_KEYS)


def test_freeze_rejects_file_omission_extra_reorder_role_and_hash_drift():
    protocol, value = _fixture()
    for mutate in (
            lambda row: row["files"].pop(),
            lambda row: row["files"].append(copy.deepcopy(row["files"][0])),
            lambda row: row["files"].reverse(),
            lambda row: row["files"][0].__setitem__("role", "wrong")):
        bad = copy.deepcopy(value)
        mutate(bad)
        bad["files_sha256"] = sha256_sorted_json(bad["files"])
        _reject(lambda bad=bad: validate_freeze(bad, protocol))
    bad = copy.deepcopy(value)
    next(row for row in bad["files"] if row["path"] == PROGRAM)[
        "sha256"] = "0" * 64
    bad["files_sha256"] = sha256_sorted_json(bad["files"])
    _reject(lambda: validate_freeze(bad, protocol))


def test_freeze_rejects_scientific_contract_mutations_and_extra_keys():
    protocol, value = _fixture()
    mutations = (
        ("models", lambda item: item.pop()),
        ("scored_cells", lambda item: item.reverse()),
        ("artifact_schemas", lambda item: item.__setitem__(
            "analysis", "wrong")),
        ("execution_policy", lambda item: item.__setitem__(
            "n_targets", 199)),
        ("protocol", lambda item: item.__setitem__(
            "raw_sha256", "0" * 64)),
        ("generator", lambda item: item.__setitem__(
            "source_commit", "0" * 40)),
    )
    for field, mutate in mutations:
        bad = copy.deepcopy(value)
        mutate(bad[field])
        _reject(lambda bad=bad: validate_freeze(bad, protocol))
    bad = copy.deepcopy(value)
    bad["posthoc"] = True
    _reject(lambda: validate_freeze(bad, protocol))


def test_freeze_is_deterministic():
    protocol, value = _fixture()
    rows = copy.deepcopy(value["files"])
    rebuilt = build_freeze_value(protocol, rows, "c" * 40, "d" * 64)
    assert rebuilt == value
