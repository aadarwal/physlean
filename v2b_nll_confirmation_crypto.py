#!/usr/bin/env python3
"""Pure cryptographic primitives for the blinded E2 confirmation.

The ciphertext is deliberately tiny and deterministic: an IEEE-754 binary64
payload XORed with a tuple-unique HMAC-SHA256 stream.  It provides blinded,
fixed-width payloads; it is not an AEAD construction.  Scientific
authentication comes from exact committed artifact hashes plus independent
mask/reveal replay, as required by the frozen confirmation protocol.
"""
import hashlib
import hmac
import math
import os
import stat
import struct

from v2b_common import V2BError, canonical_json_bytes, sha256_bytes


SALT_BYTES = 32
FAMILY_DOMAIN = "v2b-nll-e2-confirmation-family-v1"
PAYLOAD_DOMAIN = "v2b-nll-e2-confirmation-payload-v1"
STUDY_ID = "v2b-nll-e2-fresh-sympy-q25c-ladder-20260809"
MODEL_IDS = ("q25c-0.5b", "q25c-1.5b", "q25c-3b", "q25c-7b")
CONTRAST_IDS = ("E1a", "E1b", "E2_seed0", "E2_seed1", "E2_seed2")


def _salt(value):
    if not isinstance(value, bytes) or len(value) != SALT_BYTES:
        raise V2BError("confirmation salt must be exactly 32 bytes")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise V2BError(f"confirmation {label} must be a nonempty string")
    return value


def _mac(salt, fields):
    return hmac.new(_salt(salt), canonical_json_bytes(fields),
                    hashlib.sha256).digest()


def _coordinates(study_id, model_id, contrast_id):
    if study_id != STUDY_ID or model_id not in MODEL_IDS \
            or contrast_id not in CONTRAST_IDS:
        raise V2BError("confirmation study/model/contrast coordinate drift")


def salt_commitment(salt):
    """Public SHA256 commitment; never returns or logs salt bytes."""
    return sha256_bytes(_salt(salt))


def family_id(salt, model_id, contrast_id, study_id=STUDY_ID):
    """Return the frozen opaque family identifier for one contrast/model."""
    _coordinates(study_id, model_id, contrast_id)
    digest = _mac(salt, [FAMILY_DOMAIN, study_id, model_id, contrast_id])
    return "fam-" + digest.hex()[:16]


def _payload_stream(salt, model_id, contrast_id, target_key,
                    study_id=STUDY_ID):
    _coordinates(study_id, model_id, contrast_id)
    _text(target_key, "target key")
    return _mac(salt, [PAYLOAD_DOMAIN, study_id, model_id, contrast_id,
                       target_key])[:8]


def _float_bytes(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(float(value)):
        raise V2BError(f"confirmation delta is not finite: {value!r}")
    return struct.pack(">d", float(value))


def encrypt_delta(salt, model_id, contrast_id, target_key, value=None,
                  study_id=STUDY_ID):
    """Return exactly 16 lowercase hex characters.

    ``value=None`` is the frozen structurally-ineligible padding payload
    ``+0.0``.  Eligibility is intentionally absent from public masked rows.
    """
    plain = b"\x00" * 8 if value is None else _float_bytes(value)
    stream = _payload_stream(salt, model_id, contrast_id, target_key,
                             study_id)
    return bytes(a ^ b for a, b in zip(plain, stream)).hex()


def decrypt_delta(salt, model_id, contrast_id, target_key, ciphertext,
                  study_id=STUDY_ID):
    """Decrypt one payload.  Callers must filter padding from a bound ledger."""
    if not isinstance(ciphertext, str) or len(ciphertext) != 16 \
            or any(ch not in "0123456789abcdef" for ch in ciphertext):
        raise V2BError("confirmation ciphertext is not 16 lowercase hex")
    raw = bytes.fromhex(ciphertext)
    stream = _payload_stream(salt, model_id, contrast_id, target_key,
                             study_id)
    value = struct.unpack(">d", bytes(a ^ b for a, b in zip(raw, stream)))[0]
    if not math.isfinite(value):
        raise V2BError("decrypted confirmation payload is nonfinite")
    return value


def verify_ciphertext(salt, model_id, contrast_id, target_key, ciphertext,
                      value=None, study_id=STUDY_ID):
    """Exact replay check used by both the fixed-N gate and reveal."""
    expected = encrypt_delta(salt, model_id, contrast_id, target_key, value,
                             study_id)
    if not hmac.compare_digest(expected, ciphertext):
        raise V2BError("confirmation ciphertext does not replay")
    return True


def create_salt_file(path, salt=None):
    """Create one private salt with O_EXCL, O_NOFOLLOW, mode 0600 and fsync."""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise V2BError("private salt path must be absolute")
    payload = os.urandom(SALT_BYTES) if salt is None else _salt(salt)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as err:
        raise V2BError(f"cannot create private confirmation salt: {err}") \
            from err
    try:
        written = 0
        while written < len(payload):
            n = os.write(fd, payload[written:])
            if n <= 0:
                raise V2BError("short write creating confirmation salt")
            written += n
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    loaded = load_salt_file(path)
    if not hmac.compare_digest(loaded, payload):
        raise V2BError("private confirmation salt changed after creation")
    try:
        directory_fd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as err:
        raise V2BError(f"cannot fsync private salt directory: {err}") \
            from err
    return salt_commitment(payload)


def load_salt_file(path):
    """Read one owned, regular, non-symlink, exact-mode private salt."""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise V2BError("private salt path must be absolute")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        before = os.lstat(path)
        fd = os.open(path, flags)
    except OSError as err:
        raise V2BError(f"cannot open private confirmation salt: {err}") \
            from err
    try:
        opened = os.fstat(fd)
        chunks = []
        while True:
            block = os.read(fd, 64)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    mode = stat.S_IMODE(opened.st_mode)
    if before.st_dev != opened.st_dev or before.st_ino != opened.st_ino \
            or not stat.S_ISREG(opened.st_mode) \
            or opened.st_uid != os.getuid() or mode != 0o600:
        raise V2BError("private confirmation salt ownership/type/mode drift")
    return _salt(b"".join(chunks))
