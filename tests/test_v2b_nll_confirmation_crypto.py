#!/usr/bin/env python3
import math
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError
from v2b_nll_confirmation_crypto import (
    CONTRAST_IDS, MODEL_IDS, create_salt_file, decrypt_delta, encrypt_delta,
    family_id, load_salt_file, salt_commitment, verify_ciphertext)


SALT = bytes(range(32))
TARGET = "sympy.core.foo\u001fbar\u001f123"


def _reject(fn):
    try:
        fn()
        assert False, "invalid crypto operation succeeded"
    except V2BError:
        pass


def test_frozen_hmac_family_and_payload_vectors():
    assert salt_commitment(SALT) == \
        "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd"
    assert family_id(SALT, "q25c-1.5b", "E2_seed0") == \
        "fam-a556ed2bb4476a49"
    assert encrypt_delta(SALT, "q25c-1.5b", "E2_seed0", TARGET,
                         0.02175) == "cdb43fff183305a3"
    assert encrypt_delta(SALT, "q25c-1.5b", "E1b", TARGET, None) == \
        "7ebdf1a2b3c58f37"


def test_round_trip_padding_and_exact_replay():
    for value in (-3.5, -0.0, 0.0, 0.02175, 17.0):
        cipher = encrypt_delta(SALT, "q25c-7b", "E2_seed2", TARGET,
                               value)
        assert len(cipher) == 16 and cipher == cipher.lower()
        got = decrypt_delta(SALT, "q25c-7b", "E2_seed2", TARGET, cipher)
        assert got == value
        assert math.copysign(1.0, got) == math.copysign(1.0, float(value))
        assert verify_ciphertext(SALT, "q25c-7b", "E2_seed2", TARGET,
                                 cipher, value)
    padding = encrypt_delta(SALT, "q25c-0.5b", "E1b", TARGET, None)
    assert decrypt_delta(SALT, "q25c-0.5b", "E1b", TARGET, padding) == 0.0
    assert verify_ciphertext(SALT, "q25c-0.5b", "E1b", TARGET, padding,
                             None)


def test_all_frozen_family_ids_are_unique_and_tuple_bound():
    ids = [family_id(SALT, model, contrast)
           for model in MODEL_IDS for contrast in CONTRAST_IDS]
    assert len(ids) == 20 == len(set(ids))
    base = encrypt_delta(SALT, MODEL_IDS[0], CONTRAST_IDS[0], TARGET, 1.0)
    assert base != encrypt_delta(SALT, MODEL_IDS[1], CONTRAST_IDS[0],
                                 TARGET, 1.0)
    assert base != encrypt_delta(SALT, MODEL_IDS[0], CONTRAST_IDS[1],
                                 TARGET, 1.0)
    assert base != encrypt_delta(SALT, MODEL_IDS[0], CONTRAST_IDS[0],
                                 TARGET + "x", 1.0)


def test_malformed_nonfinite_and_tampered_payloads_fail_closed():
    for value in (float("nan"), float("inf"), -float("inf"), True, None):
        if value is None:
            continue  # None is the registered padding operation.
        _reject(lambda value=value: encrypt_delta(
            SALT, MODEL_IDS[0], CONTRAST_IDS[0], TARGET, value))
    for cipher in ("", "0" * 15, "0" * 17, "G" * 16, None):
        _reject(lambda cipher=cipher: decrypt_delta(
            SALT, MODEL_IDS[0], CONTRAST_IDS[0], TARGET, cipher))
    for coordinates in (("unknown", CONTRAST_IDS[0]),
                        (MODEL_IDS[0], "unknown")):
        _reject(lambda coordinates=coordinates: encrypt_delta(
            SALT, coordinates[0], coordinates[1], TARGET, 0.0))
    valid = encrypt_delta(SALT, MODEL_IDS[0], CONTRAST_IDS[0], TARGET, 1.0)
    tampered = ("0" if valid[0] != "0" else "1") + valid[1:]
    _reject(lambda: verify_ciphertext(
        SALT, MODEL_IDS[0], CONTRAST_IDS[0], TARGET, tampered, 1.0))


def test_private_salt_file_is_write_once_owned_regular_mode_0600():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "salt.bin")
        assert create_salt_file(path, SALT) == salt_commitment(SALT)
        assert load_salt_file(path) == SALT
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
        _reject(lambda: create_salt_file(path, SALT))
        os.chmod(path, 0o640)
        _reject(lambda: load_salt_file(path))


def test_private_salt_loader_rejects_symlinks_and_wrong_sizes():
    with tempfile.TemporaryDirectory() as directory:
        real = os.path.join(directory, "real.bin")
        link = os.path.join(directory, "link.bin")
        with open(real, "wb") as handle:
            handle.write(b"x" * 31)
        os.chmod(real, 0o600)
        _reject(lambda: load_salt_file(real))
        os.unlink(real)
        with open(real, "wb") as handle:
            handle.write(SALT)
        os.chmod(real, 0o600)
        os.symlink(real, link)
        _reject(lambda: load_salt_file(link))
