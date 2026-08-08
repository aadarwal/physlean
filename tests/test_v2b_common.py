#!/usr/bin/env python3
"""Tests for shared V2-b identity and evidence primitives."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_common import (V2BError, canonical_json_bytes, identity_key,
                        load_json, relative_source_path, seeded_hash,
                        sha256_file, sha256_json, sha256_sorted_json,
                        validate_identity, write_new_json)


def test_canonical_unicode_and_flat_identity_hashes_are_unambiguous():
    assert canonical_json_bytes(["α", ":", "β"]) == \
        '["α",":","β"]'.encode()
    # These collide under naive colon joining and must not collide here.
    assert seeded_hash("k5:0", "r", "a:b", "c") != \
        seeded_hash("k5:0", "r", "a", "b:c")
    assert identity_key("lean", ["M", "«a:b»"]) == '["M","«a:b»"]'


def test_identity_shapes_fail_closed():
    assert validate_identity("lean", ["M", "d"]) == ("M", "d")
    assert validate_identity("python", ["m", "f", 0]) == ("m", "f", 0)
    for language, identity in (("lean", ["only-one"]),
                               ("python", ["m", "f", True]),
                               ("python", ["m", "f", -1]),
                               ("cpp", ["m", "f"])):
        try:
            validate_identity(language, identity)
        except V2BError:
            pass
        else:
            raise AssertionError((language, identity))


def test_new_only_json_roundtrip_and_schema_gate():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "artifact.json")
        value = {"schema": "synthetic_v1", "text": "λ", "n": 1}
        digest = write_new_json(path, value)
        got, got_digest = load_json(path, schema="synthetic_v1")
        assert got == value
        assert got_digest == digest == sha256_file(path)
        try:
            write_new_json(path, value)
        except V2BError:
            pass
        else:
            raise AssertionError("overwrote evidence")
        try:
            load_json(path, schema="synthetic_v2")
        except V2BError:
            pass
        else:
            raise AssertionError("accepted wrong schema")
        assert json.load(open(path, encoding="utf-8")) == value


def test_sorted_json_self_hash_survives_evidence_publication():
    left = {"b": 1, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 1}
    assert left == right
    assert sha256_json(left) != sha256_json(right)  # legacy/order-sensitive
    assert sha256_sorted_json(left) == sha256_sorted_json(right)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sorted.json")
        value = {"schema": "sorted_v1", "payload": left}
        expected = sha256_sorted_json(value["payload"])
        write_new_json(path, value)
        reloaded, _ = load_json(path, "sorted_v1")
        assert sha256_sorted_json(reloaded["payload"]) == expected


def test_duplicate_keys_and_nonfinite_json_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        for name, blob in (("duplicate", b'{"schema":"x","schema":"x"}'),
                           ("nan", b'{"schema":"x","value":NaN}')):
            path = os.path.join(td, name + ".json")
            with open(path, "wb") as fh:
                fh.write(blob)
            try:
                load_json(path, schema="x")
            except V2BError:
                pass
            else:
                raise AssertionError(f"accepted {name} JSON")


def test_relative_source_path_rejects_escape():
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "repo")
        os.mkdir(root)
        source = os.path.join(root, "pkg", "x.py")
        os.makedirs(os.path.dirname(source))
        open(source, "wb").close()
        assert relative_source_path(root, source) == "pkg/x.py"
        try:
            relative_source_path(root, os.path.join(td, "outside.py"))
        except V2BError:
            pass
        else:
            raise AssertionError("accepted source outside corpus")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B COMMON TESTS PASS")
