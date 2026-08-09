#!/usr/bin/env python3
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError
from v2b_nll_confirmation import load_protocol
from v2b_nll_confirmation_crypto import STUDY_ID
from prepare_v2b_nll_confirmation_salt import (
    ALGORITHM, ASSEMBLY_SCHEMA, IMPLEMENTATION_FREEZE_SCHEMA, SAMPLE_SCHEMA,
    SALT_COMMITMENT_SCHEMA, STATE, _create_or_recover_salt,
    _private_receipt_value, _validate_predecessor, _write_private_receipt,
    build_commitment_value, protocol_record, validate_commitment)


def _reject(fn):
    try:
        fn()
        assert False, "invalid salt operation succeeded"
    except V2BError:
        pass


def _binding(schema, digit):
    return dict(path=f"/sealed/{schema}.json", schema=schema,
                sha256=digit * 64)


def _fixture():
    protocol, _ = load_protocol()
    freeze = _binding(IMPLEMENTATION_FREEZE_SCHEMA, "1")
    sample = _binding(SAMPLE_SCHEMA, "2")
    assembly = _binding(ASSEMBLY_SCHEMA, "3")
    entries = [
        dict(label="assembly", bytes=1, sha256=assembly["sha256"]),
        dict(label="bound_sample", bytes=1, sha256=sample["sha256"]),
        dict(label="implementation_freeze", bytes=1,
             sha256=freeze["sha256"]),
        dict(label="protocol", bytes=1,
             sha256=protocol_record()["raw_sha256"]),
    ]
    from v2b_common import sha256_sorted_json
    ledger_sha = sha256_sorted_json(entries)
    ledger = dict(
        algorithm="sha256-sorted-json-file-ledger-v1",
        n_entries=len(entries),
        entries=entries, pre_entries_sha256=ledger_sha,
        post_entries_sha256=ledger_sha, entries_sha256=ledger_sha,
        unchanged=True)
    generator = dict(
        program="prepare_v2b_nll_confirmation_salt.py",
        program_sha256="4" * 64, source_commit="5" * 40,
        source_tree_hash="6" * 64)
    return protocol, freeze, sample, assembly, ledger, generator


def test_commitment_has_exact_public_shape_and_no_private_path_or_salt():
    protocol, freeze, sample, assembly, ledger, generator = _fixture()
    value = build_commitment_value(
        protocol, freeze, sample, assembly, "7" * 64, ledger, generator)
    assert value["schema"] == SALT_COMMITMENT_SCHEMA
    assert value["state"] == STATE
    assert value["study_id"] == STUDY_ID
    assert value["algorithm"] == ALGORITHM
    assert value["protocol"] == protocol_record()
    assert value["bindings"] == dict(
        implementation_freeze=freeze, bound_sample=sample,
        assembly=assembly)
    assert "private_salt" not in value and "salt_path" not in value
    assert set(value) == {
        "schema", "state", "study_id", "algorithm", "salt_sha256",
        "protocol", "bindings", "input_ledger", "generator"}


def test_constructor_rejects_wrong_schema_digest_and_generator():
    args = list(_fixture())
    _reject(lambda: build_commitment_value(*args[:4], "x" * 64,
                                           *args[4:]))
    bad = dict(args[1], schema="wrong")
    _reject(lambda: build_commitment_value(
        args[0], bad, args[2], args[3], "7" * 64, args[4], args[5]))
    bad_generator = dict(args[5], source_commit="8" * 39)
    _reject(lambda: build_commitment_value(
        *args[:4], "7" * 64, args[4], bad_generator))


def test_predecessors_require_exact_protocol_state_freeze_and_grid():
    _, freeze, sample_binding, assembly_binding, _, _ = _fixture()
    sample = dict(
        schema=SAMPLE_SCHEMA, state="drawn-source-gated-module-disjoint-pre-score",
        study_id=STUDY_ID, protocol=protocol_record(), requested_n=200,
        realized_n=200, bindings=dict(implementation_freeze=freeze))
    assert _validate_predecessor(
        sample, SAMPLE_SCHEMA, freeze, "sample") == sample
    bad = dict(sample, protocol=dict(protocol_record(), raw_sha256="9" * 64))
    _reject(lambda: _validate_predecessor(
        bad, SAMPLE_SCHEMA, freeze, "sample"))
    assembly = dict(
        schema=ASSEMBLY_SCHEMA,
        state="complete-model-independent-pre-score-six-cell-assembly",
        study_id=STUDY_ID, protocol=protocol_record(), n_targets=200,
        cell_order=["k1", "k3:16384", "k4:16384", "k5:0:16384",
                    "k5:1:16384", "k5:2:16384"],
        bindings=dict(implementation_freeze=freeze,
                      bound_sample=sample_binding))
    assert _validate_predecessor(
        assembly, ASSEMBLY_SCHEMA, freeze, "assembly") == assembly
    bad = dict(assembly, state="complete-after-score")
    _reject(lambda: _validate_predecessor(
        bad, ASSEMBLY_SCHEMA, freeze, "assembly"))


def test_public_validator_rejects_ledger_and_binding_tamper():
    import copy
    args = _fixture()
    value = build_commitment_value(*args[:4], "7" * 64, *args[4:])
    assert validate_commitment(copy.deepcopy(value)) == value
    bad = copy.deepcopy(value)
    bad["input_ledger"]["entries"][0]["bytes"] += 1
    _reject(lambda: validate_commitment(bad))
    bad = copy.deepcopy(value)
    bad["input_ledger"]["entries"].append(
        dict(label="extra", bytes=1, sha256="a" * 64))
    from v2b_common import sha256_sorted_json
    digest = sha256_sorted_json(bad["input_ledger"]["entries"])
    bad["input_ledger"].update(
        n_entries=5, entries_sha256=digest,
        pre_entries_sha256=digest, post_entries_sha256=digest)
    _reject(lambda: validate_commitment(bad))
    bad = copy.deepcopy(value)
    bad["bindings"]["assembly"]["sha256"] = "9" * 63
    _reject(lambda: validate_commitment(bad))


def test_private_salt_is_recovered_not_rerolled_after_interruption():
    with tempfile.TemporaryDirectory() as directory:
        # TemporaryDirectory is outside the source checkout in normal tests.
        path = os.path.join(directory, "confirmation-salt.bin")
        receipt = os.path.join(directory, "confirmation-salt-receipt.json")
        first = _create_or_recover_salt(path, receipt)
        before = open(path, "rb").read()
        second = _create_or_recover_salt(path, receipt)
        after = open(path, "rb").read()
        assert first == second
        assert before == after and len(after) == 32
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_private_salt_path_inside_checkout_is_rejected():
    path = os.path.abspath("results_v2/v2b/forbidden-private-salt.bin")
    _reject(lambda: _create_or_recover_salt(path, path + ".receipt"))


def test_ambiguous_preexisting_or_known_salt_cannot_be_recovered():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "preexisting.bin")
        receipt = os.path.join(directory, "receipt.json")
        with open(path, "wb") as handle:
            handle.write(b"\x00" * 32)
        os.chmod(path, 0o600)
        _reject(lambda: _create_or_recover_salt(path, receipt))
        os.unlink(path)
        with open(receipt, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        os.chmod(receipt, 0o600)
        _reject(lambda: _create_or_recover_salt(path, receipt))

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "public-test-vector.bin")
        receipt = os.path.join(directory, "receipt.json")
        payload = bytes(range(32))
        import hashlib
        digest = hashlib.sha256(payload).hexdigest()
        with open(path, "wb") as handle:
            handle.write(payload)
        os.chmod(path, 0o600)
        _write_private_receipt(
            receipt, _private_receipt_value(digest, "a" * 64))
        _reject(lambda: _create_or_recover_salt(path, receipt))


def test_recovery_receipt_must_retain_exact_tool_written_bytes():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "confirmation-salt.bin")
        receipt = os.path.join(directory, "confirmation-salt-receipt.json")
        _create_or_recover_salt(path, receipt)
        with open(receipt, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        with open(receipt, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(receipt, 0o600)
        _reject(lambda: _create_or_recover_salt(path, receipt))
