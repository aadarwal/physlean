#!/usr/bin/env python3
"""Seal the confirmation's blind fixed-N execution-completeness gate.

The gate revalidates all four model reducers, all 800 target score artifacts,
all 4,800 registered cell records, and all 4,000 fixed-width ciphertexts.  It
uses the private salt only for deterministic replay; its public output contains
no ciphertext, opaque family id, family mapping, eligibility count, score,
delta, BPB, or model-specific result.
"""
import argparse
import copy
import os
import sys

from eval_v2b_nll_confirmation import ASSEMBLY_SCHEMA, git_is_ancestor
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, load_json, sha256_file,
                        sha256_json, sha256_sorted_json, write_new_json)
from v2b_nll_confirmation import (PROTOCOL_PATH, PROTOCOL_RAW_SHA256,
                                  load_protocol)
from v2b_nll_confirmation_crypto import STUDY_ID, load_salt_file
from prepare_v2b_nll_confirmation_masked import (
    MASKED_BINDING_SCHEMAS, MASKED_SCHEMA, MODEL_ORDER, N_MASKED_ROWS,
    N_MODELS, N_TARGETS, PROGRAM as MASK_PROGRAM, _binding,
    _discover_paths, _file_ledger, _ledger_expected, _load_discovered,
    _load_provenance_inputs, _frozen_file_sha, _publication_bytes, _record,
    ledger_record, load_masked,
    publication_sha256, replay_masked, validate_input_ledger,
    validate_masked)
from prepare_v2b_nll_confirmation_salt import (
    SALT_COMMITMENT_SCHEMA, protocol_record)


FIXED_N_SCHEMA = "v2b_nll_e2_confirmation_fixed_n_gate_v1"
FIXED_N_STATE = "exact-fixed-n-blind-complete"
PROGRAM = os.path.basename(__file__)
N_REGISTERED_CELLS = 6
N_TARGET_SCORE_ARTIFACTS = N_MODELS * N_TARGETS
N_CELL_RECORDS = N_TARGET_SCORE_ARTIFACTS * N_REGISTERED_CELLS
REQUIRED_CELLS = ("k1", "k4:16384", "k5:0:16384")
DIAGNOSTIC_CELLS = ("k3:16384", "k5:1:16384", "k5:2:16384")

TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "ancestry", "cohort",
    "grid", "score_manifest_sha256", "verification",
    "replay_evidence_sha256", "input_ledger", "generator",
}
FIXED_BINDING_SCHEMAS = dict(MASKED_BINDING_SCHEMAS, masked=MASKED_SCHEMA)
GENERATOR_KEYS = {
    "program", "program_sha256", "source_commit", "source_tree_hash"}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _generator(value):
    _exact_keys(value, GENERATOR_KEYS, "fixed-N generator")
    if value["program"] != PROGRAM or not _hex(value["program_sha256"]) \
            or value["program_sha256"] != sha256_file(__file__) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("malformed fixed-N generator")
    return value


def _fixed_bindings(masked, masked_record):
    bindings = copy.deepcopy(masked["bindings"])
    bindings["masked"] = dict(
        path=masked_record["path"], schema=MASKED_SCHEMA,
        sha256=masked_record["sha256"])
    for label, schema in FIXED_BINDING_SCHEMAS.items():
        _binding(bindings[label], schema, f"fixed-N {label}")
    return bindings


def _fixed_ledger_expected(masked, masked_binding):
    expected = _ledger_expected(masked["bindings"],
                                masked["score_manifest"])
    expected["masked"] = masked_binding["sha256"]
    return expected


def _grid():
    return dict(
        n_models=N_MODELS, n_targets=N_TARGETS,
        n_registered_cells=N_REGISTERED_CELLS,
        n_target_score_artifacts=N_TARGET_SCORE_ARTIFACTS,
        n_cell_records=N_CELL_RECORDS, n_masked_rows=N_MASKED_ROWS)


def _verification():
    return dict(
        required_cells=list(REQUIRED_CELLS),
        diagnostic_cells=list(DIAGNOSTIC_CELLS),
        n_required_cell_records=N_TARGET_SCORE_ARTIFACTS *
        len(REQUIRED_CELLS),
        n_diagnostic_cell_records=N_TARGET_SCORE_ARTIFACTS *
        len(DIAGNOSTIC_CELLS),
        all_required_cells_eligible_and_scored=True,
        all_eligible_diagnostics_scored=True,
        all_ineligible_diagnostics_explicitly_unscored=True,
        raw_score_bindings_replayed=True,
        masked_ciphertexts_replayed=True,
        private_salt_commitment_verified=True,
        exact_fixed_cohort=True, no_partial_n=True, no_redraw=True,
        no_replacement=True)


def _replay_evidence(masked, masked_binding):
    return sha256_sorted_json(dict(
        study_id=STUDY_ID,
        masked_sha256=masked_binding["sha256"],
        salt_commitment_sha256=masked["bindings"][
            "salt_commitment"]["sha256"],
        score_manifest_sha256=sha256_sorted_json(
            masked["score_manifest"]),
        ordered_target_keys_sha256=masked["cohort"][
            "ordered_target_keys_sha256"],
        n_target_score_artifacts=N_TARGET_SCORE_ARTIFACTS,
        n_cell_records=N_CELL_RECORDS, n_masked_rows=N_MASKED_ROWS,
        replay="exact-private-salt-raw-score-to-ciphertext-v1"))


def build_fixed_n_value(protocol, assembly, assembly_binding, salt_record,
                        study_record, model_inputs, provenance_inputs,
                        masked_record, salt, input_ledger, generator,
                        ancestor_fn=None):
    """Pure constructor; all private values are discarded after replay."""
    if protocol.get("study_id") != STUDY_ID:
        raise V2BError("fixed-N protocol/study drift")
    generator = _generator(copy.deepcopy(generator))
    ancestor_fn = ancestor_fn or git_is_ancestor
    masked = _record(masked_record, MASKED_SCHEMA, "masked input")
    validate_masked(masked, protocol, assembly)
    replay_masked(
        masked, protocol, assembly, assembly_binding, salt_record,
        study_record, model_inputs, provenance_inputs, salt,
        ancestor_fn=ancestor_fn)
    freeze = provenance_inputs["implementation_freeze"]["value"]
    if _frozen_file_sha(freeze, PROGRAM) != sha256_file(__file__) \
            or generator["source_tree_hash"] != freeze["source_tree_hash"] \
            or not ancestor_fn(freeze["implementation_commit"],
                               generator["source_commit"]) \
            or not ancestor_fn(masked["ancestry"]["masking_source_commit"],
                               generator["source_commit"]):
        raise V2BError("freeze/masking/fixed-N provenance is not monotone")
    bindings = _fixed_bindings(masked, masked_record)
    expected_ledger = _fixed_ledger_expected(masked, bindings["masked"])
    validate_input_ledger(input_ledger, expected_ledger)
    ancestry = dict(
        salt_commitment_adoption_commit=masked["ancestry"][
            "salt_commitment_adoption_commit"],
        scoring_source_commit=masked["ancestry"]["scoring_source_commit"],
        masking_source_commit=masked["ancestry"]["masking_source_commit"],
        fixed_n_source_commit=generator["source_commit"], verified=True)
    value = dict(
        schema=FIXED_N_SCHEMA, state=FIXED_N_STATE, study_id=STUDY_ID,
        repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=protocol_record(), bindings=bindings, ancestry=ancestry,
        cohort=dict(
            n_targets=N_TARGETS,
            ordered_target_keys_sha256=masked["cohort"][
                "ordered_target_keys_sha256"]),
        grid=_grid(),
        score_manifest_sha256=sha256_sorted_json(masked["score_manifest"]),
        verification=_verification(),
        replay_evidence_sha256=_replay_evidence(masked,
                                                bindings["masked"]),
        input_ledger=copy.deepcopy(input_ledger),
        generator=copy.deepcopy(generator))
    return validate_fixed_n(
        value, protocol, assembly, masked, bindings["masked"])


def validate_fixed_n(value, protocol, assembly, masked, masked_binding):
    """Strict outcome-free public validator for reveal consumers."""
    _exact_keys(value, TOP_KEYS, "confirmation fixed-N gate")
    validate_masked(masked, protocol, assembly)
    _binding(masked_binding, MASKED_SCHEMA, "fixed-N masked input")
    if publication_sha256(masked) != masked_binding["sha256"]:
        raise V2BError("fixed-N masked binding does not match exact raw bytes")
    if value["schema"] != FIXED_N_SCHEMA \
            or value["state"] != FIXED_N_STATE \
            or value["study_id"] != STUDY_ID or value["repo"] != "sympy" \
            or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != protocol_record():
        raise V2BError("confirmation fixed-N identity drift")
    expected_bindings = copy.deepcopy(masked["bindings"])
    expected_bindings["masked"] = copy.deepcopy(masked_binding)
    bindings = value["bindings"]
    _exact_keys(bindings, set(FIXED_BINDING_SCHEMAS), "fixed-N bindings")
    for label, schema in FIXED_BINDING_SCHEMAS.items():
        _binding(bindings[label], schema, f"fixed-N {label}")
    if bindings != expected_bindings:
        raise V2BError("fixed-N/masked predecessor binding drift")
    ancestry = value["ancestry"]
    _exact_keys(ancestry, {
        "salt_commitment_adoption_commit", "scoring_source_commit",
        "masking_source_commit", "fixed_n_source_commit", "verified"},
        "fixed-N ancestry")
    if any(not _hex(ancestry[name], 40) for name in (
            "salt_commitment_adoption_commit", "scoring_source_commit",
            "masking_source_commit", "fixed_n_source_commit")) \
            or ancestry["verified"] is not True \
            or any(ancestry[name] != masked["ancestry"][name]
                   for name in ("salt_commitment_adoption_commit",
                                "scoring_source_commit",
                                "masking_source_commit")):
        raise V2BError("fixed-N ancestry/masked chain drift")
    _generator(value["generator"])
    if value["generator"]["source_commit"] != \
            ancestry["fixed_n_source_commit"] \
            or value["generator"]["source_tree_hash"] != \
            masked["generator"]["source_tree_hash"]:
        raise V2BError("fixed-N generator/ancestry drift")
    _exact_keys(value["cohort"], {
        "n_targets", "ordered_target_keys_sha256"}, "fixed-N cohort")
    if value["cohort"] != dict(
            n_targets=N_TARGETS,
            ordered_target_keys_sha256=masked["cohort"][
                "ordered_target_keys_sha256"]):
        raise V2BError("fixed-N cohort drift")
    if value["grid"] != _grid() \
            or value["score_manifest_sha256"] != \
            sha256_sorted_json(masked["score_manifest"]) \
            or value["verification"] != _verification() \
            or value["replay_evidence_sha256"] != \
            _replay_evidence(masked, masked_binding):
        raise V2BError("fixed-N grid/replay evidence drift")
    validate_input_ledger(
        value["input_ledger"],
        _fixed_ledger_expected(masked, masked_binding))
    return value


def load_fixed_n(path, protocol, assembly, masked, masked_binding):
    value, digest = load_json(path, FIXED_N_SCHEMA)
    return validate_fixed_n(
        value, protocol, assembly, masked, masked_binding), digest


def prepare(private_salt_path, salt_commitment_path, assembly_path,
            study_complete_path, masked_path,
            protocol_path=PROTOCOL_PATH):
    """Production fixed-N replay over committed, immutable predecessors."""
    if not source_clean():
        raise V2BError("source tree dirty before fixed-N confirmation gate")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("fixed-N gate requires canonical protocol")
    commit, tree = head_commit(), source_tree_hash()
    model_paths, target_paths, score_paths = _discover_paths(
        protocol_path, assembly_path, salt_commitment_path,
        study_complete_path)
    paths = list(score_paths) + [("masked", masked_path)]
    for _label, path in paths:
        require_committed(path)
    pre = _file_ledger(paths)
    protocol, _ = load_protocol(protocol_path)
    assembly_binding_raw, assembly = artifact_binding(
        assembly_path, ASSEMBLY_SCHEMA)
    assembly_binding = dict(
        path=assembly_binding_raw["path"], schema=ASSEMBLY_SCHEMA,
        sha256=assembly_binding_raw["sha256"])
    salt_record, study_record, model_inputs = _load_discovered(
        model_paths, target_paths, study_complete_path, salt_commitment_path)
    provenance_inputs = _load_provenance_inputs(
        study_record, salt_commitment_path, live_freeze=True)
    masked_record_value, masked_digest = load_json(masked_path, MASKED_SCHEMA)
    masked_record = dict(path=os.path.abspath(masked_path),
                         sha256=masked_digest, value=masked_record_value)
    salt_before = load_salt_file(private_salt_path)
    generator = dict(program=PROGRAM, program_sha256=sha256_file(__file__),
                     source_commit=commit, source_tree_hash=tree)
    value = build_fixed_n_value(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, masked_record, salt_before,
        ledger_record(pre, pre), generator, ancestor_fn=git_is_ancestor)
    salt_after = load_salt_file(private_salt_path)
    post = _file_ledger(paths)
    if salt_before != salt_after or pre != post \
            or not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("fixed-N inputs/source/private salt changed")
    value["input_ledger"] = ledger_record(pre, post)
    return validate_fixed_n(
        value, protocol, assembly, masked_record_value,
        value["bindings"]["masked"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-salt", required=True)
    parser.add_argument("--salt-commitment", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--study-complete", required=True)
    parser.add_argument("--masked", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite fixed-N gate")
    value = prepare(
        args.private_salt, args.salt_commitment, args.assembly,
        args.study_complete, args.masked, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-fixed-n] exact target scores="
          f"{N_TARGET_SCORE_ARTIFACTS} cells={N_CELL_RECORDS} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(2)
