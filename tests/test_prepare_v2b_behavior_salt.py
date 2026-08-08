#!/usr/bin/env python3
"""Behavioral pre-generation salt/commitment and opaque arm mapping."""
import hashlib
import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepare_v2b_behavior_salt import (
    ARM_DOMAIN, NAMED_ARMS, SALT_ALGORITHM, _read_salt, _write_salt_pair,
    arm_id, arm_mapping)
from v2b_common import BEHAVIOR_SALT_COMMITMENT_SCHEMA, V2BError


def test_write_once_pair_mode_commitment_and_mapping():
    with tempfile.TemporaryDirectory() as td:
        salt_path = os.path.join(td, "private", "salt.hex")
        commitment_path = os.path.join(td, "commitment.json")
        digest = _write_salt_pair(salt_path, commitment_path)
        assert stat.S_IMODE(os.stat(salt_path).st_mode) == 0o600
        commitment = json.load(open(commitment_path))
        assert commitment["schema"] == BEHAVIOR_SALT_COMMITMENT_SCHEMA
        assert commitment["state"] == "committed-pre-generation"
        assert commitment["algorithm"] == SALT_ALGORITHM
        assert commitment["salt_sha256"] == digest
        salt, binding = _read_salt(salt_path, commitment_path)
        assert hashlib.sha256(salt).hexdigest() == digest
        assert binding["salt_sha256"] == digest
        mapping = arm_mapping(salt)
        assert tuple(mapping) == NAMED_ARMS
        assert len(set(mapping.values())) == 5
        assert all(value.startswith("arm-") and len(value) == 20
                   for value in mapping.values())
        assert mapping == arm_mapping(salt)
        assert all(mapping[name] == arm_id(salt, name)
                   for name in NAMED_ARMS)
        assert ARM_DOMAIN == "v2bbehavior-arm:v1"
        try:
            _write_salt_pair(salt_path, os.path.join(td, "other.json"))
            assert False, "private behavioral salt was overwritten"
        except V2BError as err:
            assert "overwrite" in str(err)


def test_salt_and_commitment_drift_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        salt_path = os.path.join(td, "salt.hex")
        commitment_path = os.path.join(td, "commitment.json")
        _write_salt_pair(salt_path, commitment_path)
        os.chmod(salt_path, 0o644)
        try:
            _read_salt(salt_path, commitment_path)
            assert False, "world-readable behavioral salt accepted"
        except V2BError as err:
            assert "0600" in str(err)
        os.chmod(salt_path, 0o600)
        commitment = json.load(open(commitment_path))
        commitment["salt_sha256"] = "0" * 64
        json.dump(commitment, open(commitment_path, "w"))
        try:
            _read_salt(salt_path, commitment_path)
            assert False, "tampered behavioral commitment accepted"
        except V2BError as err:
            assert "does not match" in str(err)

    # A pre-existing public commitment must refuse before creating an
    # orphan private salt.
    with tempfile.TemporaryDirectory() as td:
        salt_path = os.path.join(td, "salt.hex")
        commitment_path = os.path.join(td, "commitment.json")
        open(commitment_path, "w").write("occupied\n")
        try:
            _write_salt_pair(salt_path, commitment_path)
            assert False, "pre-existing commitment was overwritten"
        except V2BError as err:
            assert "overwrite" in str(err)
        assert not os.path.exists(salt_path)


def test_arm_derivation_rejects_foreign_inputs():
    salt = bytes(range(32))
    for bad_salt, arm in ((b"short", "k1"), (salt, "k2"),
                          (salt, "k4-pass-rate=.9")):
        try:
            arm_id(bad_salt, arm)
            assert False, "foreign behavioral arm derivation accepted"
        except V2BError:
            pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B BEHAVIOR SALT TESTS PASS")
