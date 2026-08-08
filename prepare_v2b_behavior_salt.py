#!/usr/bin/env python3
"""Pre-generation behavioral commit/reveal salt for V2-b.

This is deliberately separate from the NLL B3 salt.  It creates one private
32-byte write-once salt (POOL, mode 0600, never printed or committed) and one
public write-once commitment artifact.  The exact five named behavioral arms
map to opaque ids by a prospectively frozen HMAC rule; the mapping is not
published here and is replayed only at the formal joint reveal.

No sample, completion, verifier outcome, pass rate, or model score is read.
"""
import argparse
import hashlib
import hmac
import os
import re

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (BEHAVIOR_SALT_COMMITMENT_SCHEMA, V2BError,
                        artifact_binding, canonical_json_bytes,
                        write_new_json)


NAMED_ARMS = ("k1", "k3", "k4", "k5", "k6")
ARM_DOMAIN = "v2bbehavior-arm:v1"
SALT_BYTES = 32
SALT_ALGORITHM = (
    "commitment = SHA256(salt-32-bytes); arm_id = 'arm-' + first-16-hex("
    "HMAC-SHA256(key=salt, message=canonical_json(["
    "'v2bbehavior-arm:v1', named_arm]))) for exact arms k1/k3/k4/k5/k6")
OPAQUE_ARM_RE = re.compile(r"^arm-[0-9a-f]{16}$")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _write_salt_pair(salt_path, commitment_path):
    """Pure filesystem constructor used by init_salt and synthetic tests."""
    commit_start, tree_start = head_commit(), source_tree_hash()
    salt_path = os.path.abspath(salt_path)
    commitment_path = os.path.abspath(commitment_path)
    if os.path.lexists(salt_path) or os.path.lexists(commitment_path):
        raise V2BError("refusing to overwrite behavioral salt/commitment")
    os.makedirs(os.path.dirname(salt_path), exist_ok=True)
    salt = os.urandom(SALT_BYTES)
    try:
        fd = os.open(salt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                     0o600)
    except OSError as err:
        raise V2BError(f"cannot create private behavioral salt: {err}") \
            from err
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(salt.hex() + "\n")
    commitment = dict(
        schema=BEHAVIOR_SALT_COMMITMENT_SCHEMA,
        state="committed-pre-generation",
        algorithm=SALT_ALGORITHM,
        salt_sha256=hashlib.sha256(salt).hexdigest(),
        generator=dict(source_commit=commit_start,
                       source_tree_hash=tree_start,
                       program="prepare_v2b_behavior_salt.py"))
    write_new_json(commitment_path, commitment)
    return commitment["salt_sha256"]


def init_salt(salt_path, commitment_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    return _write_salt_pair(salt_path, commitment_path)


def _read_salt(salt_path, commitment_path):
    binding, commitment = artifact_binding(
        commitment_path, BEHAVIOR_SALT_COMMITMENT_SCHEMA)
    if commitment.get("state") != "committed-pre-generation" \
            or commitment.get("algorithm") != SALT_ALGORITHM \
            or not _hex(commitment.get("salt_sha256")):
        raise V2BError("behavioral salt commitment artifact is malformed")
    try:
        stat_row = os.lstat(salt_path)
        if not os.path.isfile(salt_path) or os.path.islink(salt_path) \
                or stat_row.st_mode & 0o777 != 0o600:
            raise V2BError("private behavioral salt must be a regular, "
                           "non-symlink file with mode 0600")
        text = open(salt_path, "r", encoding="ascii").read().strip()
        salt = bytes.fromhex(text)
    except (OSError, UnicodeError, ValueError) as err:
        raise V2BError(f"cannot read private behavioral salt: {err}") \
            from err
    if len(salt) != SALT_BYTES:
        raise V2BError(f"private behavioral salt must be {SALT_BYTES} bytes")
    if hashlib.sha256(salt).hexdigest() != commitment["salt_sha256"]:
        raise V2BError("private behavioral salt does not match commitment")
    return salt, dict(binding, salt_sha256=commitment["salt_sha256"])


def arm_id(salt, named_arm):
    if not isinstance(salt, bytes) or len(salt) != SALT_BYTES:
        raise V2BError("behavioral arm derivation needs a 32-byte salt")
    if named_arm not in NAMED_ARMS:
        raise V2BError(f"unknown behavioral arm {named_arm!r}")
    digest = hmac.new(
        salt, canonical_json_bytes([ARM_DOMAIN, named_arm]),
        hashlib.sha256).hexdigest()
    return "arm-" + digest[:16]


def arm_mapping(salt):
    mapping = {name: arm_id(salt, name) for name in NAMED_ARMS}
    if len(set(mapping.values())) != len(NAMED_ARMS) \
            or any(not OPAQUE_ARM_RE.fullmatch(value)
                   for value in mapping.values()):
        raise V2BError("behavioral opaque arm id collision/malformed id")
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-salt", required=True,
                    help="write-once private POOL path (never committed)")
    ap.add_argument("--commitment-out", required=True,
                    help="write-once public commitment JSON")
    args = ap.parse_args()
    try:
        digest = init_salt(args.init_salt, args.commitment_out)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    # Never print the salt or opaque mapping.
    print(f"[v2b-behavior-salt] committed {digest[:12]} -> "
          f"{args.commitment_out}")


if __name__ == "__main__":
    main()
