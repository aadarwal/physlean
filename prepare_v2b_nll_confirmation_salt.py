#!/usr/bin/env python3
"""Create the confirmation-only blind salt and public pre-score commitment.

This entry point is deliberately separate from masking.  It may run only
after the protocol, implementation freeze, source-gated sample, and six-cell
assembly are committed.  The private 32-byte salt is written outside Git; the
public artifact contains only its SHA256 commitment and exact predecessor
bindings.  If a process dies after creating the private salt but before
publishing the commitment, a retry reuses that salt rather than rerolling it.
"""
import argparse
import hashlib
import json
import os
import stat
import sys

from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, canonical_json_bytes,
                        load_json, sha256_file, sha256_sorted_json,
                        write_new_json)
from v2b_nll_confirmation import (PROTOCOL_PATH, PROTOCOL_RAW_SHA256,
                                  PROTOCOL_SCHEMA,
                                  PROTOCOL_SEMANTIC_SHA256, load_protocol)
from v2b_nll_confirmation_crypto import (STUDY_ID, create_salt_file,
                                          load_salt_file, salt_commitment)


SALT_COMMITMENT_SCHEMA = "v2b_nll_e2_confirmation_salt_commitment_v1"
IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_e2_confirmation_implementation_freeze_v1"
SAMPLE_SCHEMA = "v2b_nll_e2_confirmation_sample_v1"
ASSEMBLY_SCHEMA = "v2b_nll_e2_confirmation_assembly_v1"
STATE = "committed-before-any-confirmation-score"
PRIVATE_RECEIPT_SCHEMA = \
    "v2b_nll_e2_confirmation_private_salt_receipt_v1"
PRIVATE_RECEIPT_STATE = "study-bound-private-salt-created"
SAMPLE_STATE = "drawn-source-gated-module-disjoint-pre-score"
ASSEMBLY_STATE = "complete-model-independent-pre-score-six-cell-assembly"
FORBIDDEN_SALT_SHA256 = frozenset((
    # Exact exploratory/pilot NLL salt commitment. Reuse is forbidden.
    "144ce6956a81a24a30e047367b1e6c1c2cdc8dcbd94afb8da94bbfe83091544f",
    # Public deterministic crypto regression vector: bytes(range(32)).
    "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd",
    # Explicitly reject the canonical non-random all-zero fixture.
    hashlib.sha256(b"\x00" * 32).hexdigest(),
))
ALGORITHM = (
    "commitment=SHA256(exact-32-byte-private-salt); opaque-family and "
    "fixed-width payload derivation are frozen by "
    "v2b_nll_confirmation_crypto.py and the implementation freeze")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _repo_rel(path):
    root, real = os.path.realpath(BASE), os.path.realpath(path)
    try:
        if os.path.commonpath((root, real)) != root:
            raise V2BError(f"confirmation path is outside checkout: {path}")
    except ValueError as err:
        raise V2BError(f"confirmation path mismatch: {err}") from err
    return os.path.relpath(real, root).replace(os.sep, "/")


def protocol_record():
    return dict(path=_repo_rel(PROTOCOL_PATH), schema=PROTOCOL_SCHEMA,
                raw_sha256=PROTOCOL_RAW_SHA256,
                semantic_sha256=PROTOCOL_SEMANTIC_SHA256)


def _artifact_row(path, schema):
    binding, value = artifact_binding(path, schema)
    return dict(path=binding["path"], schema=schema,
                sha256=binding["sha256"]), value


def _validate_freeze(value):
    if not isinstance(value, dict) \
            or value.get("schema") != IMPLEMENTATION_FREEZE_SCHEMA \
            or value.get("study_id") != STUDY_ID \
            or value.get("protocol") != protocol_record():
        raise V2BError("confirmation implementation-freeze binding drift")
    return value


def _validate_predecessor(value, schema, freeze_binding, label):
    if not isinstance(value, dict) or value.get("schema") != schema \
            or value.get("study_id") != STUDY_ID \
            or value.get("protocol") != protocol_record():
        raise V2BError(f"confirmation {label} identity drift")
    bindings = value.get("bindings")
    if not isinstance(bindings, dict) \
            or bindings.get("implementation_freeze") != freeze_binding:
        raise V2BError(f"confirmation {label} does not bind the freeze")
    if schema == SAMPLE_SCHEMA and (
            value.get("state") != SAMPLE_STATE
            or value.get("requested_n") != 200
            or value.get("realized_n") != 200):
        raise V2BError("confirmation bound-sample state/count drift")
    if schema == ASSEMBLY_SCHEMA and (
            value.get("state") != ASSEMBLY_STATE
            or value.get("n_targets") != 200
            or value.get("cell_order") != [
                "k1", "k3:16384", "k4:16384", "k5:0:16384",
                "k5:1:16384", "k5:2:16384"]):
        raise V2BError("confirmation assembly state/grid drift")
    return value


def _ledger(paths):
    rows = []
    for label, path in paths:
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat salt input {label}: {err}") from err
        rows.append(dict(label=label, bytes=size, sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    return rows


def _ledger_record(pre, post):
    if pre != post:
        raise V2BError("confirmation salt inputs changed during commitment")
    digest = sha256_sorted_json(pre)
    return dict(algorithm="sha256-sorted-json-file-ledger-v1",
                n_entries=len(pre), entries=pre,
                pre_entries_sha256=digest,
                post_entries_sha256=digest,
                entries_sha256=digest, unchanged=True)


def build_commitment_value(protocol, freeze_binding, sample_binding,
                           assembly_binding, public_salt_sha256,
                           input_ledger, generator):
    """Pure constructor used by the production entry point and tests."""
    if protocol.get("study_id") != STUDY_ID \
            or not _hex(public_salt_sha256):
        raise V2BError("confirmation salt constructor input drift")
    for label, row, schema in (
            ("implementation_freeze", freeze_binding,
             IMPLEMENTATION_FREEZE_SCHEMA),
            ("bound_sample", sample_binding, SAMPLE_SCHEMA),
            ("assembly", assembly_binding, ASSEMBLY_SCHEMA)):
        _exact_keys(row, {"path", "schema", "sha256"}, label)
        if row["schema"] != schema or not _hex(row["sha256"]) \
                or not isinstance(row["path"], str) or not row["path"]:
            raise V2BError(f"malformed confirmation {label} binding")
    if not isinstance(input_ledger, dict) \
            or input_ledger.get("unchanged") is not True \
            or not isinstance(generator, dict) \
            or generator.get("program") != os.path.basename(__file__) \
            or not _hex(generator.get("program_sha256")) \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")):
        raise V2BError("malformed confirmation salt provenance")
    return validate_commitment(dict(
        schema=SALT_COMMITMENT_SCHEMA, state=STATE, study_id=STUDY_ID,
        algorithm=ALGORITHM, salt_sha256=public_salt_sha256,
        protocol=protocol_record(),
        bindings=dict(implementation_freeze=freeze_binding,
                      bound_sample=sample_binding,
                      assembly=assembly_binding),
        input_ledger=input_ledger, generator=generator))


def validate_commitment(value):
    """Strict public-artifact validator used by all later consumers."""
    _exact_keys(value, {
        "schema", "state", "study_id", "algorithm", "salt_sha256",
        "protocol", "bindings", "input_ledger", "generator"},
        "confirmation salt commitment")
    if value["schema"] != SALT_COMMITMENT_SCHEMA \
            or value["state"] != STATE or value["study_id"] != STUDY_ID \
            or value["algorithm"] != ALGORITHM \
            or not _hex(value["salt_sha256"]) \
            or value["protocol"] != protocol_record():
        raise V2BError("confirmation salt commitment identity drift")
    _exact_keys(value["bindings"], {
        "implementation_freeze", "bound_sample", "assembly"},
        "confirmation salt bindings")
    for label, schema in (
            ("implementation_freeze", IMPLEMENTATION_FREEZE_SCHEMA),
            ("bound_sample", SAMPLE_SCHEMA), ("assembly", ASSEMBLY_SCHEMA)):
        row = value["bindings"][label]
        _exact_keys(row, {"path", "schema", "sha256"}, label)
        if row["schema"] != schema or not _hex(row["sha256"]) \
                or not isinstance(row["path"], str) or not row["path"]:
            raise V2BError(f"malformed confirmation {label} binding")
    ledger = value["input_ledger"]
    _exact_keys(ledger, {
        "algorithm", "n_entries", "entries", "pre_entries_sha256",
        "post_entries_sha256", "entries_sha256", "unchanged"},
        "confirmation salt input ledger")
    entries = ledger["entries"]
    if ledger["algorithm"] != "sha256-sorted-json-file-ledger-v1" \
            or ledger["unchanged"] is not True \
            or not isinstance(entries, list) \
            or ledger["n_entries"] != len(entries):
        raise V2BError("confirmation salt input-ledger header drift")
    labels = []
    for index, row in enumerate(entries):
        _exact_keys(row, {"label", "bytes", "sha256"},
                    f"confirmation salt input[{index}]")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError("malformed confirmation salt input-ledger row")
        labels.append(row["label"])
    digest = sha256_sorted_json(entries)
    expected_labels = [
        "assembly", "bound_sample", "implementation_freeze", "protocol"]
    if labels != expected_labels \
            or any(ledger[name] != digest for name in (
                "pre_entries_sha256", "post_entries_sha256",
                "entries_sha256")):
        raise V2BError("confirmation salt input-ledger hash/order drift")
    by_label = {row["label"]: row["sha256"] for row in entries}
    expected_digests = {
        "protocol": PROTOCOL_RAW_SHA256,
        "implementation_freeze": value["bindings"][
            "implementation_freeze"]["sha256"],
        "bound_sample": value["bindings"]["bound_sample"]["sha256"],
        "assembly": value["bindings"]["assembly"]["sha256"],
    }
    if by_label != expected_digests:
        raise V2BError("confirmation salt input ledger/predecessor SHA drift")
    generator = value["generator"]
    _exact_keys(generator, {
        "program", "program_sha256", "source_commit", "source_tree_hash"},
        "confirmation salt generator")
    if generator["program"] != os.path.basename(__file__) \
            or not _hex(generator["program_sha256"]) \
            or not _hex(generator["source_commit"], 40) \
            or not _hex(generator["source_tree_hash"]):
        raise V2BError("confirmation salt generator drift")
    return value


def load_commitment(path):
    value, digest = load_json(path, SALT_COMMITMENT_SCHEMA)
    return validate_commitment(value), digest


def _private_path_outside_git(path):
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise V2BError("private confirmation salt path must be absolute")
    root = os.path.realpath(BASE)
    parent = os.path.realpath(os.path.dirname(path))
    try:
        if os.path.commonpath((root, parent)) == root:
            raise V2BError("private confirmation salt must live outside Git")
    except ValueError:
        pass


def _private_receipt_value(public_digest, nonce):
    if not _hex(public_digest) or not _hex(nonce):
        raise V2BError("malformed private confirmation salt receipt input")
    return dict(
        schema=PRIVATE_RECEIPT_SCHEMA, state=PRIVATE_RECEIPT_STATE,
        study_id=STUDY_ID, salt_sha256=public_digest,
        creation_nonce=nonce,
        generator=dict(program=os.path.basename(__file__),
                       program_sha256=sha256_file(__file__)))


def _write_private_receipt(path, value):
    _private_path_outside_git(path)
    payload = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as err:
        raise V2BError(f"cannot create private salt receipt: {err}") \
            from err
    try:
        written = 0
        while written < len(payload):
            n = os.write(fd, payload[written:])
            if n <= 0:
                raise V2BError("short write creating private salt receipt")
            written += n
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_private_receipt(path):
    _private_path_outside_git(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        before = os.lstat(path)
        fd = os.open(path, flags)
    except OSError as err:
        raise V2BError(f"cannot open private salt receipt: {err}") from err
    try:
        opened = os.fstat(fd)
        chunks = []
        while True:
            block = os.read(fd, 4096)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    if before.st_dev != opened.st_dev or before.st_ino != opened.st_ino \
            or not stat.S_ISREG(opened.st_mode) \
            or stat.S_IMODE(opened.st_mode) != 0o600 \
            or opened.st_uid != os.getuid():
        raise V2BError("private salt receipt ownership/type/mode drift")
    blob = b"".join(chunks)
    if len(blob) > 4096:
        raise V2BError("private salt receipt is unexpectedly large")

    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(
                    f"duplicate private receipt JSON key {key!r}")
            value[key] = item
        return value

    def reject_nonfinite(value):
        raise V2BError(f"non-finite private receipt JSON number {value}")

    try:
        value = json.loads(blob, object_pairs_hook=no_duplicates,
                           parse_constant=reject_nonfinite)
    except (UnicodeError, json.JSONDecodeError, V2BError) as err:
        raise V2BError(f"cannot parse private salt receipt: {err}") \
            from err
    if blob != canonical_json_bytes(value) + b"\n":
        raise V2BError("private salt receipt is not exact canonical bytes")
    _exact_keys(value, {
        "schema", "state", "study_id", "salt_sha256", "creation_nonce",
        "generator"}, "private salt receipt")
    _exact_keys(value["generator"], {"program", "program_sha256"},
                "private salt receipt generator")
    if value["schema"] != PRIVATE_RECEIPT_SCHEMA \
            or value["state"] != PRIVATE_RECEIPT_STATE \
            or value["study_id"] != STUDY_ID \
            or not _hex(value["salt_sha256"]) \
            or not _hex(value["creation_nonce"]) \
            or value["generator"]["program"] != os.path.basename(__file__) \
            or value["generator"]["program_sha256"] != \
            sha256_file(__file__):
        raise V2BError("private confirmation salt receipt drift")
    return value


def _create_or_recover_salt(path, receipt_path):
    """Create once or recover only a study-bound tool-created pair.

    A one-file interruption (salt without receipt, or vice versa) fails
    closed and cannot trigger a reroll.
    """
    _private_path_outside_git(path)
    _private_path_outside_git(receipt_path)
    salt_exists, receipt_exists = os.path.lexists(path), \
        os.path.lexists(receipt_path)
    if salt_exists is not receipt_exists:
        raise V2BError("incomplete private salt/receipt pair; reroll forbidden")
    if salt_exists:
        digest = salt_commitment(load_salt_file(path))
        receipt = _load_private_receipt(receipt_path)
        if receipt["salt_sha256"] != digest:
            raise V2BError("private salt does not match recovery receipt")
    else:
        digest = create_salt_file(path)
        if digest in FORBIDDEN_SALT_SHA256:
            raise V2BError("forbidden/non-random confirmation salt created")
        receipt = _private_receipt_value(digest, os.urandom(32).hex())
        _write_private_receipt(receipt_path, receipt)
    if digest in FORBIDDEN_SALT_SHA256:
        raise V2BError("pilot/known confirmation salt reuse forbidden")
    return digest


def prepare(salt_path, receipt_path, freeze_path, sample_path, assembly_path,
            protocol_path=PROTOCOL_PATH):
    if not source_clean():
        raise V2BError("source tree dirty outside results_v2")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation salt requires canonical protocol path")
    for path in (protocol_path, freeze_path, sample_path, assembly_path):
        require_committed(path)
    commit, tree = head_commit(), source_tree_hash()
    protocol, _ = load_protocol(protocol_path)
    freeze_binding, freeze = _artifact_row(
        freeze_path, IMPLEMENTATION_FREEZE_SCHEMA)
    _validate_freeze(freeze)
    sample_binding, sample = _artifact_row(sample_path, SAMPLE_SCHEMA)
    _validate_predecessor(sample, SAMPLE_SCHEMA, freeze_binding,
                          "bound sample")
    assembly_binding, assembly = _artifact_row(
        assembly_path, ASSEMBLY_SCHEMA)
    _validate_predecessor(assembly, ASSEMBLY_SCHEMA, freeze_binding,
                          "assembly")
    from prepare_v2b_nll_confirmation_assembly import (
        _validate_sample, validate_assembly)
    source_gate_binding = assembly.get("bindings", {}).get("source_gate")
    _validate_sample(sample, protocol, sample_binding,
                     source_gate_binding, freeze_binding)
    validate_assembly(assembly, protocol, sample)
    if assembly.get("bindings", {}).get("bound_sample") != sample_binding:
        raise V2BError("confirmation assembly/sample predecessor drift")
    paths = (("protocol", protocol_path),
             ("implementation_freeze", freeze_path),
             ("bound_sample", sample_path), ("assembly", assembly_path))
    pre = _ledger(paths)
    public_digest = _create_or_recover_salt(salt_path, receipt_path)
    post = _ledger(paths)
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed during salt commitment")
    generator = dict(
        program=os.path.basename(__file__),
        program_sha256=sha256_file(__file__), source_commit=commit,
        source_tree_hash=tree)
    return build_commitment_value(
        protocol, freeze_binding, sample_binding, assembly_binding,
        public_digest, _ledger_record(pre, post), generator)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-salt", required=True)
    parser.add_argument("--private-receipt", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite salt commitment artifact")
    value = prepare(args.private_salt, args.private_receipt,
                    args.implementation_freeze, args.sample,
                    args.assembly, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-salt] committed -> {args.out} "
          f"({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as err:
        print(f"FATAL: {err}", file=sys.stderr)
        raise SystemExit(2)
