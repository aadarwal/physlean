#!/usr/bin/env python3
"""Freeze the complete fresh-SymPy E2 confirmation implementation.

This artifact is created once, after every production entry point, analyzer,
test, and Slurm wrapper is committed, but before the model-free source census
or sample draw.  Every later artifact binds its exact bytes.  Results-only
commits may follow; any change to measurement source changes the frozen source
tree and is rejected.
"""
import argparse
import copy
import os
import subprocess
import sys

from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, load_json, sha256_bytes,
                        sha256_file, sha256_sorted_json, write_new_json)
from v2b_nll_confirmation import (MODEL_ROWS, PROTOCOL_PATH,
                                  PROTOCOL_RAW_SHA256, PROTOCOL_SCHEMA,
                                  PROTOCOL_SEMANTIC_SHA256, SCORED_CELLS,
                                  load_protocol, validate_protocol)


FREEZE_SCHEMA = "v2b_nll_e2_confirmation_implementation_freeze_v1"
FREEZE_STATE = \
    "frozen-before-source-gate-sample-and-any-confirmation-score"
PROGRAM = os.path.basename(__file__)
STUDY_ID = "v2b-nll-e2-fresh-sympy-q25c-ladder-20260809"
N_TARGETS = 200
BUDGET_BYTES = 16384
REQUIRED_CELLS = ("k1", "k4:16384", "k5:0:16384")
DIAGNOSTIC_CELLS = ("k3:16384", "k5:1:16384", "k5:2:16384")
CONTRAST_IDS = ("E1a", "E1b", "E2_seed0", "E2_seed1", "E2_seed2")

SCHEMA_KEYS = (
    "implementation_freeze", "salt_commitment", "source_gate_fragment",
    "source_gate_reduced", "bound_sample", "assembly", "model_battery",
    "target_score", "model_complete", "study_complete", "masked",
    "fixed_n_gate", "reveal", "analysis")

# The exact closure is intentionally explicit.  Future source may not be
# admitted by dropping a row into the artifact; this program and its tests
# must be prospectively changed and re-adopted before any production work.
FILE_ROLES = {
    "results_v2/v2b/NLL_E2_CONFIRMATION_PROTOCOL.json": "protocol-data",
    "v2b_nll_confirmation.py": "protocol-validator",
    "freeze_v2b_nll_confirmation.py": "implementation-freeze",
    "v2b_nll_confirmation_context.py": "source-context",
    "prepare_v2b_nll_confirmation_gate.py": "source-gate",
    "finalize_v2b_nll_confirmation_sample.py": "sampler",
    "prepare_v2b_nll_confirmation_assembly.py": "assembly",
    "v2b_nll_confirmation_crypto.py": "cryptography",
    "v2b_nll_confirmation_stats.py": "statistics",
    "prepare_v2b_nll_confirmation_salt.py": "salt-commitment",
    "v2b_nll_confirmation_battery.py": "model-battery",
    "eval_v2b_nll_confirmation.py": "scorer-reducers",
    "prepare_v2b_nll_confirmation_masked.py": "masker",
    "finalize_v2b_nll_confirmation_fixed_n.py": "fixed-n-gate",
    "finalize_v2b_nll_confirmation_reveal.py": "reveal",
    "analyze_v2b_nll_confirmation.py": "analyzer",
    "v2b_common.py": "shared-provenance",
    "v2b_a6_blind.py": "shared-commit-gate",
    "v2b_metadata.py": "shared-sampler",
    "v2b_assemble.py": "shared-renderer",
    "prepare_v2b_assembly.py": "shared-source-loader",
    "eval_paired.py": "shared-numerical-kernel",
    "eval_incontext.py": "shared-model-kernel",
    "v2b_n_governance.py": "shared-variance-kernel",
    "provenance.py": "shared-environment-provenance",
    "tests/test_v2b_nll_confirmation.py": "test",
    "tests/test_freeze_v2b_nll_confirmation.py": "test",
    "tests/test_prepare_v2b_nll_confirmation_gate.py": "test",
    "tests/test_finalize_v2b_nll_confirmation_sample.py": "test",
    "tests/test_prepare_v2b_nll_confirmation_assembly.py": "test",
    "tests/test_v2b_nll_confirmation_crypto.py": "test",
    "tests/test_v2b_nll_confirmation_stats.py": "test",
    "tests/test_prepare_v2b_nll_confirmation_salt.py": "test",
    "tests/test_v2b_nll_confirmation_battery.py": "test",
    "tests/test_eval_v2b_nll_confirmation.py": "test",
    "tests/test_prepare_v2b_nll_confirmation_masked.py": "test",
    "tests/test_finalize_v2b_nll_confirmation_fixed_n.py": "test",
    "tests/test_finalize_v2b_nll_confirmation_reveal.py": "test",
    "tests/test_analyze_v2b_nll_confirmation.py": "test",
    "tests/test_v2b_nll_confirmation_jobs.py": "test",
    "slurm/v2b_nll_confirmation_gate.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_prepare.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_battery.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_score.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_reduce.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_mask.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_fixed_n.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_reveal.sbatch": "slurm",
    "slurm/v2b_nll_confirmation_analysis.sbatch": "slurm",
}

TOP_KEYS = {
    "schema", "state", "study_id", "protocol", "implementation_commit",
    "source_tree_hash", "files", "files_sha256", "models",
    "scored_cells", "artifact_schemas", "execution_policy", "generator",
}


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _repo_rel(path):
    root, real = os.path.realpath(BASE), os.path.realpath(path)
    try:
        if os.path.commonpath((root, real)) != root:
            raise V2BError(f"freeze path outside checkout: {path}")
    except ValueError as error:
        raise V2BError(f"freeze path mismatch: {error}") from error
    return os.path.relpath(real, root).replace(os.sep, "/")


def _repo_path(relative):
    if not isinstance(relative, str) or not relative \
            or os.path.isabs(relative) or "\\" in relative:
        raise V2BError(f"noncanonical freeze path {relative!r}")
    normalized = os.path.normpath(relative).replace(os.sep, "/")
    if normalized != relative or relative in (".", "..") \
            or relative.startswith("../"):
        raise V2BError(f"freeze path escapes or normalizes: {relative}")
    path = os.path.abspath(os.path.join(BASE, *relative.split("/")))
    if _repo_rel(path) != relative:
        raise V2BError(f"freeze path realpath drift: {relative}")
    return path


def protocol_record():
    return dict(
        path=_repo_rel(PROTOCOL_PATH), schema=PROTOCOL_SCHEMA,
        raw_sha256=PROTOCOL_RAW_SHA256,
        semantic_sha256=PROTOCOL_SEMANTIC_SHA256)


def artifact_schemas(protocol):
    contracts = protocol.get("execution_schema_contracts")
    if not isinstance(contracts, dict):
        raise V2BError("confirmation protocol lacks schema contracts")
    value = {name: contracts.get(name) for name in SCHEMA_KEYS}
    if any(not isinstance(schema, str) or not schema
           for schema in value.values()):
        raise V2BError("confirmation artifact-schema projection drift")
    return value


def model_rows(protocol):
    expected = [dict(id=row[0], name=row[1], revision=row[2],
                     nominal_billions=row[3], role=row[4])
                for row in MODEL_ROWS]
    if protocol.get("models") != expected:
        raise V2BError("confirmation freeze model ladder drift")
    return expected


def execution_policy(protocol):
    model_ids = [row["id"] for row in model_rows(protocol)]
    value = dict(
        repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        n_targets=N_TARGETS, budget_bytes=BUDGET_BYTES,
        model_ids=model_ids, cell_order=list(SCORED_CELLS),
        required_cells=list(REQUIRED_CELLS),
        diagnostic_cells=list(DIAGNOSTIC_CELLS),
        contrast_ids=list(CONTRAST_IDS),
        salt_commitment_before_scoring=True,
        all_batteries_before_scoring=True,
        analyzer_before_scoring=True,
        target_atomic_mode="0600-write-once-compatible-resume",
        shard_policy=("per-model exact committed battery decision; reducer "
                      "requires exact union"),
        partition_time_limit="06:00:00")
    if protocol.get("scored_cells") != value["cell_order"]:
        raise V2BError("confirmation freeze cell order drift")
    return value


def _validate_file_rows(rows):
    if not isinstance(rows, list) or len(rows) != len(FILE_ROLES):
        raise V2BError("implementation freeze file-closure count drift")
    expected_paths = sorted(FILE_ROLES)
    observed_paths = []
    for index, row in enumerate(rows):
        _exact_keys(row, {"path", "sha256", "role"},
                    f"freeze file[{index}]")
        if row["path"] not in FILE_ROLES \
                or row["role"] != FILE_ROLES[row["path"]] \
                or not _hex(row["sha256"]):
            raise V2BError("implementation freeze file row drift")
        observed_paths.append(row["path"])
    if observed_paths != expected_paths or len(set(observed_paths)) != \
            len(observed_paths):
        raise V2BError("implementation freeze file order/set drift")
    return rows


def build_freeze_value(protocol, file_rows, implementation_commit,
                       tree_hash):
    validate_protocol(protocol)
    rows = copy.deepcopy(_validate_file_rows(file_rows))
    if not _hex(implementation_commit, 40) or not _hex(tree_hash):
        raise V2BError("malformed implementation commit/tree")
    by_path = {row["path"]: row for row in rows}
    generator = dict(
        program=PROGRAM,
        program_sha256=by_path[PROGRAM]["sha256"],
        source_commit=implementation_commit,
        source_tree_hash=tree_hash)
    value = dict(
        schema=FREEZE_SCHEMA, state=FREEZE_STATE, study_id=STUDY_ID,
        protocol=protocol_record(), implementation_commit=implementation_commit,
        source_tree_hash=tree_hash, files=rows,
        files_sha256=sha256_sorted_json(rows),
        models=model_rows(protocol), scored_cells=list(SCORED_CELLS),
        artifact_schemas=artifact_schemas(protocol),
        execution_policy=execution_policy(protocol), generator=generator)
    return validate_freeze(value, protocol)


def validate_freeze(value, protocol=None):
    if protocol is None:
        protocol, _ = load_protocol()
    validate_protocol(protocol)
    _exact_keys(value, TOP_KEYS, "confirmation implementation freeze")
    if value["schema"] != FREEZE_SCHEMA or value["state"] != FREEZE_STATE \
            or value["study_id"] != STUDY_ID \
            or value["protocol"] != protocol_record() \
            or not _hex(value["implementation_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("confirmation implementation-freeze identity drift")
    rows = _validate_file_rows(value["files"])
    if value["files_sha256"] != sha256_sorted_json(rows) \
            or value["models"] != model_rows(protocol) \
            or value["scored_cells"] != list(SCORED_CELLS) \
            or value["artifact_schemas"] != artifact_schemas(protocol) \
            or value["execution_policy"] != execution_policy(protocol):
        raise V2BError("confirmation implementation-freeze content drift")
    generator = value["generator"]
    _exact_keys(generator, {
        "program", "program_sha256", "source_commit", "source_tree_hash"},
        "implementation-freeze generator")
    program_row = {row["path"]: row for row in rows}[PROGRAM]
    if generator != dict(
            program=PROGRAM, program_sha256=program_row["sha256"],
            source_commit=value["implementation_commit"],
            source_tree_hash=value["source_tree_hash"]):
        raise V2BError("implementation-freeze generator drift")
    return value


def _git(*args):
    process = subprocess.run(
        ["git", *args], cwd=BASE, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise V2BError(f"git {' '.join(args)} failed: {detail}")
    return process.stdout


def _is_ancestor(older, newer):
    process = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer], cwd=BASE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if process.returncode not in (0, 1):
        raise V2BError("cannot verify implementation-freeze ancestry")
    return process.returncode == 0


def validate_live_freeze(value, freeze_path=None):
    protocol, _ = load_protocol()
    validate_freeze(value, protocol)
    if not source_clean() or source_tree_hash() != value["source_tree_hash"]:
        raise V2BError("live source tree differs from implementation freeze")
    current = head_commit()
    if not _is_ancestor(value["implementation_commit"], current):
        raise V2BError("implementation commit is not an ancestor of HEAD")
    for row in value["files"]:
        path = _repo_path(row["path"])
        require_committed(path)
        if sha256_file(path) != row["sha256"]:
            raise V2BError(f"live implementation file drift: {row['path']}")
        committed_blob = _git(
            "show", f"{value['implementation_commit']}:{row['path']}")
        if sha256_bytes(committed_blob) != row["sha256"]:
            raise V2BError(
                f"implementation-commit blob drift: {row['path']}")
    if freeze_path is not None:
        require_committed(freeze_path)
        relative = _repo_rel(freeze_path)
        touches = [line for line in _git(
            "log", "--format=%H", "--", relative).decode().splitlines()
                   if line]
        if len(touches) != 1:
            raise V2BError("implementation freeze must be a one-touch artifact")
    return value


def load_implementation_freeze(path, live=True):
    value, digest = load_json(path, FREEZE_SCHEMA)
    validate_freeze(value)
    if live:
        validate_live_freeze(value, path)
    binding_raw, _ = artifact_binding(path, FREEZE_SCHEMA)
    if binding_raw["sha256"] != digest:
        raise V2BError("implementation freeze binding/hash drift")
    return value, dict(
        path=binding_raw["path"], schema=FREEZE_SCHEMA, sha256=digest)


def _live_file_rows():
    rows = []
    for relative, role in sorted(FILE_ROLES.items()):
        path = _repo_path(relative)
        if not os.path.isfile(path) or os.path.islink(path):
            raise V2BError(f"implementation file missing/symlink: {relative}")
        require_committed(path)
        rows.append(dict(path=relative, sha256=sha256_file(path), role=role))
    return rows


def prepare(protocol_path=PROTOCOL_PATH):
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("freeze requires canonical confirmation protocol")
    if not source_clean():
        raise V2BError("source tree dirty before implementation freeze")
    require_committed(protocol_path)
    protocol, digest = load_protocol(protocol_path)
    if digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation protocol raw digest drift")
    commit, tree = head_commit(), source_tree_hash()
    rows = _live_file_rows()
    if not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("source changed while building implementation freeze")
    return build_freeze_value(protocol, rows, commit, tree)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite implementation freeze")
    value = prepare(args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-freeze] files={len(value['files'])} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(2)
