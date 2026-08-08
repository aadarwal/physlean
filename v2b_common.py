#!/usr/bin/env python3
"""Shared, pure provenance primitives for the V2-b paired pipeline.

The measurement artifacts deliberately use a small common vocabulary so the
sampler, assembler, evaluator, and final gate cannot disagree about identity
encoding or silently accept an older schema.  This module has no corpus or ML
dependencies and is safe to import from CPU-only validation jobs.
"""
import hashlib
import json
import os
import tempfile


SEED_FAMILY = "v2b:20260808"
CANDIDATES_SCHEMA = "v2b_candidates_v1"
NEARDUP_SCHEMA = "v2b_neardup_v1"
SAMPLE_SCHEMA = "v2b_sample_v1"
BOUND_SAMPLE_SCHEMA = "v2b_bound_sample_v1"
K7_ORDER_SCHEMA = "v2b_k7_order_v1"
K4X_GRAPH_SCHEMA = "v2b_k4x_external_graph_v1"
MASKED_DELTAS_SCHEMA = "v2b_masked_deltas_v1"
SALT_COMMITMENT_SCHEMA = "v2b_salt_commitment_v1"
N_GOVERNANCE_SCHEMA = "v2b_n_governance_v1"
UNBLINDING_SCHEMA = "v2b_unblinding_v1"
BEHAVIOR_SALT_COMMITMENT_SCHEMA = "v2b_behavior_salt_commitment_v1"
LEAN_KEYWORD_FREEZE_SCHEMA = "v2b_lean_keyword_freeze_v2"
A6_AUDIT_PACKET_SCHEMA = "v2b_a6_audit_packet_v1"
A6_BLIND_SCHEMA = "v2b_a6_blind_v1"
A6_LABELS_SCHEMA = "v2b_a6_blind_labels_v1"
A6_OUTCOME_SCHEMA = "v2b_a6_outcome_v1"
ASSEMBLY_SCHEMA = "v2b_assembly_manifest_v1"


class V2BError(RuntimeError):
    """An input is ambiguous, drifted, or outside the frozen V2-b contract."""


def canonical_json_bytes(value):
    """Frozen UTF-8 compact-JSON encoding used by every V2-b hash key."""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as err:
        raise V2BError(f"value is not canonical-JSON encodable: {err}") \
            from err
    return text.encode("utf-8")


def sha256_bytes(blob):
    if not isinstance(blob, bytes):
        raise V2BError("sha256_bytes requires bytes")
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
    except OSError as err:
        raise V2BError(f"cannot hash {path}: {err}") from err
    return h.hexdigest()


def sha256_json(value):
    return sha256_bytes(canonical_json_bytes(value))


def validate_identity(language, identity):
    """Return the frozen Lean pair or Python source-qualified triple."""
    if not isinstance(identity, (list, tuple)):
        raise V2BError(f"{language} identity is not a list/tuple")
    values = tuple(identity)
    if language == "lean":
        valid = (len(values) == 2
                 and all(isinstance(x, str) and x for x in values))
    elif language == "python":
        valid = (len(values) == 3
                 and isinstance(values[0], str) and bool(values[0])
                 and isinstance(values[1], str) and bool(values[1])
                 and isinstance(values[2], int)
                 and not isinstance(values[2], bool)
                 and values[2] >= 0)
    else:
        raise V2BError(f"unsupported V2-b language {language!r}")
    if not valid:
        raise V2BError(f"invalid {language} identity {identity!r}")
    return values


def identity_key(language, identity):
    """Unambiguous dictionary key; never delimiter-concatenate identities."""
    return canonical_json_bytes(
        list(validate_identity(language, identity))).decode("utf-8")


def seeded_hash(label, repo, *flat_fields):
    """SHA256(json([label, repo, ...])) with fields already spliced flat."""
    if not isinstance(label, str) or not label:
        raise V2BError("seed label must be a non-empty string")
    if not isinstance(repo, str) or not repo:
        raise V2BError("repo must be a non-empty string")
    return sha256_json([label, repo, *flat_fields])


def load_json(path, schema=None):
    """Read one object and return (value, exact file SHA), rejecting drift."""
    def object_no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise V2BError(f"duplicate JSON object key {key!r}")
            out[key] = value
        return out

    def reject_nonfinite(value):
        raise V2BError(f"non-finite JSON number {value}")

    try:
        blob = open(path, "rb").read()
        value = json.loads(blob, object_pairs_hook=object_no_duplicates,
                           parse_constant=reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError, V2BError) as err:
        raise V2BError(f"cannot read JSON artifact {path}: {err}") from err
    if not isinstance(value, dict):
        raise V2BError(f"JSON artifact root is not an object: {path}")
    if schema is not None and value.get("schema") != schema:
        raise V2BError(
            f"{path}: schema {value.get('schema')!r} != {schema!r}")
    return value, sha256_bytes(blob)


def write_new_json(path, value):
    """Atomically publish pretty evidence without replacing any prior file."""
    if not isinstance(value, dict):
        raise V2BError("evidence artifact root must be an object")
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise V2BError(f"refusing to overwrite evidence artifact: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".v2b-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise V2BError(
                f"refusing to overwrite evidence artifact: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return sha256_file(path)


def relative_source_path(corpus_root, source_path):
    """Return a normalized repo-relative path and reject path escape."""
    root = os.path.realpath(corpus_root)
    source = os.path.realpath(source_path)
    try:
        common = os.path.commonpath((root, source))
    except ValueError as err:
        raise V2BError(f"source/root path mismatch: {err}") from err
    if common != root or source == root:
        raise V2BError(f"source is outside corpus root: {source_path}")
    return os.path.relpath(source, root).replace(os.sep, "/")


def artifact_binding(path, schema=None):
    """Small manifest-ready binding after validating the artifact."""
    value, digest = load_json(path, schema=schema)
    return dict(path=os.path.abspath(path), sha256=digest,
                schema=value.get("schema")), value
