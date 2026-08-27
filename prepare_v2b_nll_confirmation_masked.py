#!/usr/bin/env python3
"""Build the fixed-width blinded artifact for the SymPy confirmation.

This module is deliberately confirmation-specific.  It consumes the exact
four-model study reducer and all 800 target-atomic score artifacts, revalidates
the complete six-cell grid, computes the five frozen BPB contrasts, and emits
exactly 4 * 5 * 200 opaque 8-byte ciphertexts.  A structurally ineligible
diagnostic contrast is encrypted as the registered +0.0 padding value.  The
public rows contain neither contrast names nor eligibility indicators.

The production entry point uses a complete pre/post ledger and rereads every
artifact after discovery, so paths or bytes changed during masking fail
closed.  No model is loaded here.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys

from eval_v2b_nll_confirmation import (
    ASSEMBLY_SCHEMA, MODEL_COMPLETE_SCHEMA, MODEL_IDS, STUDY_COMPLETE_SCHEMA,
    TARGET_SCHEMA, git_is_ancestor, normalize_battery,
    salt_adoption_commit, validate_model_complete, validate_study_complete,
    validate_target_score)
from freeze_v2b_nll_confirmation import validate_freeze, validate_live_freeze
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (V2BError, artifact_binding, load_json, sha256_file,
                        sha256_json, sha256_sorted_json, write_new_json)
from v2b_nll_confirmation import (MODEL_ROWS, PROTOCOL_PATH,
                                  PROTOCOL_RAW_SHA256, SCORED_CELLS,
                                  load_protocol)
from v2b_nll_confirmation_crypto import (
    CONTRAST_IDS, STUDY_ID, encrypt_delta, family_id, load_salt_file,
    salt_commitment)
from prepare_v2b_nll_confirmation_salt import (
    FORBIDDEN_SALT_SHA256, IMPLEMENTATION_FREEZE_SCHEMA,
    SALT_COMMITMENT_SCHEMA, SAMPLE_SCHEMA, protocol_record)
from v2b_nll_confirmation_battery import BATTERY_SCHEMA


MASKED_SCHEMA = "v2b_nll_e2_confirmation_masked_v1"
MASKED_STATE = "exact-fixed-width-mask-complete"
RAW_SCORE_MANIFEST_SCHEMA = \
    "v2b_nll_e2_confirmation_raw_score_manifest_v1"
SOURCE_GATE_SCHEMA = "v2b_nll_e2_confirmation_source_gate_v1"
PROGRAM = os.path.basename(__file__)
N_TARGETS = 200
N_MODELS = 4
N_CONTRASTS = 5
N_MASKED_ROWS = N_TARGETS * N_MODELS * N_CONTRASTS
CELL_ORDER = tuple(SCORED_CELLS)
MODEL_ORDER = tuple(row[0] for row in MODEL_ROWS)

# (contrast id, minuend cell, subtrahend cell, required for every target)
CONTRASTS = (
    ("E1a", "k1", "k4:16384", True),
    ("E1b", "k3:16384", "k4:16384", False),
    ("E2_seed0", "k5:0:16384", "k4:16384", True),
    ("E2_seed1", "k5:1:16384", "k4:16384", False),
    ("E2_seed2", "k5:2:16384", "k4:16384", False),
)
if tuple(row[0] for row in CONTRASTS) != tuple(CONTRAST_IDS):
    raise RuntimeError("confirmation crypto/protocol contrast order drift")

MASKED_TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "ancestry", "cohort",
    "grid", "score_manifest", "models", "models_sha256",
    "input_ledger", "generator",
}
MASKED_BINDING_SCHEMAS = {
    "implementation_freeze": IMPLEMENTATION_FREEZE_SCHEMA,
    "source_gate": SOURCE_GATE_SCHEMA,
    "bound_sample": SAMPLE_SCHEMA,
    "assembly": ASSEMBLY_SCHEMA,
    "salt_commitment": SALT_COMMITMENT_SCHEMA,
    "study_complete": STUDY_COMPLETE_SCHEMA,
}
GENERATOR_KEYS = {
    "program", "program_sha256", "source_commit", "source_tree_hash"}
PUBLIC_LEDGER_KEYS = {
    "algorithm", "n_entries", "entries", "entries_sha256",
    "private_pre_post_equal"}
PROVENANCE_INPUT_KEYS = {
    "implementation_freeze", "model_batteries", "salt_adoption_commit"}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _binding(value, schema, label):
    _exact_keys(value, {"path", "schema", "sha256"}, label)
    if value["schema"] != schema or not _hex(value["sha256"]) \
            or not isinstance(value["path"], str) or not value["path"]:
        raise V2BError(f"malformed confirmation {label} binding")
    return value


def _model_binding(value, label):
    _exact_keys(value, {"model_id", "path", "schema", "sha256"}, label)
    if value["model_id"] not in MODEL_ORDER \
            or value["schema"] != MODEL_COMPLETE_SCHEMA \
            or not isinstance(value["path"], str) or not value["path"] \
            or not _hex(value["sha256"]):
        raise V2BError(f"malformed confirmation {label}")
    return value


def _artifact_record_binding(record, schema, label):
    value = _record(record, schema, label)
    binding = dict(path=record["path"], schema=schema,
                   sha256=record["sha256"])
    return value, binding


def _frozen_file_sha(freeze, relative):
    matches = [row for row in freeze["files"] if row["path"] == relative]
    if len(matches) != 1:
        raise V2BError(f"implementation freeze lacks exact {relative}")
    return matches[0]["sha256"]


def _publication_bytes(value):
    """Exact bytes produced by v2b_common.write_new_json."""
    try:
        text = json.dumps(value, indent=1, sort_keys=True,
                          ensure_ascii=False, allow_nan=False) + "\n"
    except (TypeError, ValueError) as err:
        raise V2BError(f"artifact is not publishable canonical JSON: {err}") \
            from err
    return text.encode("utf-8")


def publication_sha256(value):
    """Public helper used by synthetic fixtures to bind exact raw bytes."""
    return hashlib.sha256(_publication_bytes(value)).hexdigest()


def _record(record, schema, label):
    _exact_keys(record, {"path", "sha256", "value"}, label)
    if not isinstance(record["path"], str) or not record["path"] \
            or not _hex(record["sha256"]) \
            or not isinstance(record["value"], dict) \
            or record["value"].get("schema") != schema \
            or publication_sha256(record["value"]) != record["sha256"]:
        raise V2BError(f"{label} is not bound to exact canonical raw bytes")
    return record["value"]


def _generator(value, program=PROGRAM):
    _exact_keys(value, GENERATOR_KEYS, f"{program} generator")
    if value["program"] != program or not _hex(value["program_sha256"]) \
            or value["program_sha256"] != sha256_file(__file__) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError(f"malformed {program} generator")
    return value


def _target_binding(row, index, target_key):
    _exact_keys(row, {"target_index", "target_key", "path", "schema",
                      "sha256", "shard_index"},
                f"target artifact[{index}]")
    if row["target_index"] != index or row["target_key"] != target_key \
            or row["schema"] != TARGET_SCHEMA \
            or not isinstance(row["path"], str) or not row["path"] \
            or not _hex(row["sha256"]) \
            or not isinstance(row["shard_index"], int) \
            or isinstance(row["shard_index"], bool) \
            or row["shard_index"] < 0:
        raise V2BError("raw target-score binding/order drift")
    return row


def _expected_bindings(assembly_binding, salt_record, study_record,
                       study):
    _binding(assembly_binding, ASSEMBLY_SCHEMA, "assembly")
    salt_binding = dict(path=salt_record["path"],
                        schema=SALT_COMMITMENT_SCHEMA,
                        sha256=salt_record["sha256"])
    study_binding = dict(path=study_record["path"],
                         schema=STUDY_COMPLETE_SCHEMA,
                         sha256=study_record["sha256"])
    common = study.get("bindings")
    if not isinstance(common, dict):
        raise V2BError("study completion lacks predecessor bindings")
    expected = {
        "implementation_freeze": copy.deepcopy(
            common.get("implementation_freeze")),
        "source_gate": copy.deepcopy(common.get("source_gate")),
        "bound_sample": copy.deepcopy(common.get("bound_sample")),
        "assembly": copy.deepcopy(common.get("assembly")),
        "salt_commitment": copy.deepcopy(common.get("salt_commitment")),
        "study_complete": study_binding,
    }
    for label, schema in MASKED_BINDING_SCHEMAS.items():
        _binding(expected[label], schema, label)
    if expected["assembly"] != assembly_binding \
            or expected["salt_commitment"] != salt_binding:
        raise V2BError("masking assembly/salt binding does not match study")
    return expected


def _cell_bpb(cell, target_key, cell_id):
    if cell.get("structural_status") != "eligible" \
            or cell.get("status") != "scored":
        raise V2BError(f"eligible contrast cell is not scored: "
                       f"{target_key} {cell_id}")
    nll = cell.get("nll_nats")
    denominator = cell.get("scored_body_bytes")
    if not isinstance(nll, (int, float)) or isinstance(nll, bool) \
            or not math.isfinite(float(nll)) or float(nll) < 0.0 \
            or not isinstance(denominator, int) \
            or isinstance(denominator, bool) or denominator <= 0:
        raise V2BError(f"malformed score scalar: {target_key} {cell_id}")
    result = float(nll) / math.log(2.0) / denominator
    if not math.isfinite(result):
        raise V2BError(f"nonfinite BPB: {target_key} {cell_id}")
    return result


def _target_deltas(target):
    cells = target.get("cells")
    if not isinstance(cells, list) \
            or [row.get("cell_id") for row in cells] != list(CELL_ORDER):
        raise V2BError("target score lacks exact six-cell order")
    by_id = dict(zip(CELL_ORDER, cells))
    out = {}
    for contrast_id, minuend_id, subtrahend_id, required in CONTRASTS:
        minuend, subtrahend = by_id[minuend_id], by_id[subtrahend_id]
        eligible = minuend.get("structural_status") == "eligible" \
            and subtrahend.get("structural_status") == "eligible"
        if required and not eligible:
            raise V2BError("required confirmation contrast is ineligible")
        if not eligible:
            # Target-score validation already proved these are registered
            # structural ineligibilities with no numeric score fields.
            out[contrast_id] = None
            continue
        out[contrast_id] = (
            _cell_bpb(minuend, target["target_key"], minuend_id)
            - _cell_bpb(subtrahend, target["target_key"], subtrahend_id))
    return out


def _score_manifest(models, cohort_keys, freeze_binding, battery_bindings):
    rows = []
    all_targets = []
    for model_index, (model_id, model_complete, _targets,
                      model_binding) in enumerate(models):
        artifacts = copy.deepcopy(model_complete["target_artifacts"])
        for index, (artifact, target_key) in enumerate(
                zip(artifacts, cohort_keys)):
            _target_binding(artifact, index, target_key)
        row = dict(
            model_id=model_id,
            model_complete=copy.deepcopy(model_binding),
            model_battery=copy.deepcopy(battery_bindings[model_index]),
            n_target_artifacts=N_TARGETS,
            target_artifacts=artifacts,
            target_artifacts_sha256=sha256_sorted_json(artifacts))
        rows.append(row)
        all_targets.extend(artifacts)
    return dict(
        schema=RAW_SCORE_MANIFEST_SCHEMA, n_models=N_MODELS,
        n_target_artifacts=N_TARGETS * N_MODELS, models=rows,
        implementation_freeze=copy.deepcopy(freeze_binding),
        model_batteries=copy.deepcopy(battery_bindings),
        model_batteries_sha256=sha256_sorted_json(battery_bindings),
        models_sha256=sha256_sorted_json(rows),
        target_artifacts_sha256=sha256_sorted_json(all_targets))


def validate_score_manifest(value, cohort_keys):
    _exact_keys(value, {"schema", "n_models", "n_target_artifacts",
                        "implementation_freeze", "model_batteries",
                        "model_batteries_sha256", "models", "models_sha256",
                        "target_artifacts_sha256"},
                "raw score manifest")
    models = value["models"]
    freeze_binding = _binding(
        value["implementation_freeze"], IMPLEMENTATION_FREEZE_SCHEMA,
        "raw-score implementation freeze")
    batteries = value["model_batteries"]
    if value["schema"] != RAW_SCORE_MANIFEST_SCHEMA \
            or value["n_models"] != N_MODELS \
            or value["n_target_artifacts"] != N_TARGETS * N_MODELS \
            or not isinstance(models, list) or len(models) != N_MODELS \
            or not isinstance(batteries, list) \
            or len(batteries) != N_MODELS \
            or value["model_batteries_sha256"] != \
            sha256_sorted_json(batteries) \
            or value["models_sha256"] != sha256_sorted_json(models):
        raise V2BError("raw score manifest header/hash drift")
    del freeze_binding
    for index, battery in enumerate(batteries):
        _binding(battery, BATTERY_SCHEMA,
                 f"raw-score model battery[{index}]")
    all_targets = []
    for model_index, (model_id, row) in enumerate(zip(MODEL_ORDER, models)):
        _exact_keys(row, {"model_id", "model_complete",
                          "model_battery",
                          "n_target_artifacts", "target_artifacts",
                          "target_artifacts_sha256"},
                    f"raw score model {model_id}")
        binding = _model_binding(row["model_complete"],
                                 f"model complete {model_id}")
        artifacts = row["target_artifacts"]
        if row["model_id"] != model_id \
                or binding["model_id"] != model_id \
                or row["model_battery"] != batteries[model_index] \
                or row["n_target_artifacts"] != N_TARGETS \
                or not isinstance(artifacts, list) \
                or len(artifacts) != N_TARGETS \
                or row["target_artifacts_sha256"] != \
                sha256_sorted_json(artifacts):
            raise V2BError("raw score model manifest drift")
        for index, (artifact, key) in enumerate(zip(artifacts, cohort_keys)):
            _target_binding(artifact, index, key)
        all_targets.extend(artifacts)
    if value["target_artifacts_sha256"] != sha256_sorted_json(all_targets):
        raise V2BError("raw score global target-artifact hash drift")
    return value


def _score_bindings(study, model_index):
    common = study["bindings"]
    batteries = common["model_batteries"]
    return dict(
        implementation_freeze=copy.deepcopy(
            common["implementation_freeze"]),
        source_gate=copy.deepcopy(common["source_gate"]),
        bound_sample=copy.deepcopy(common["bound_sample"]),
        assembly=copy.deepcopy(common["assembly"]),
        model_battery=copy.deepcopy(batteries[model_index]),
        all_model_batteries=copy.deepcopy(
            common["all_model_batteries"]),
        salt_commitment=copy.deepcopy(common["salt_commitment"]))


def _validate_provenance_inputs(protocol, assembly, study, salt_value,
                                provenance_inputs, ancestor_fn):
    """Replay exact freeze/batteries and derive trusted model contracts."""
    _exact_keys(provenance_inputs, PROVENANCE_INPUT_KEYS,
                "masking provenance inputs")
    freeze, freeze_binding = _artifact_record_binding(
        provenance_inputs["implementation_freeze"],
        IMPLEMENTATION_FREEZE_SCHEMA, "implementation freeze input")
    validate_freeze(freeze, protocol)
    if freeze_binding != study["bindings"]["implementation_freeze"]:
        raise V2BError("study/implementation-freeze exact binding drift")
    if _frozen_file_sha(freeze, PROGRAM) != sha256_file(__file__):
        raise V2BError("masker source differs from implementation freeze")

    adoption = provenance_inputs["salt_adoption_commit"]
    sequence = study["salt_sequence"]
    if not _hex(adoption, 40) \
            or adoption != sequence["salt_commitment_adoption_commit"]:
        raise V2BError("actual salt adoption differs from study sequence")
    freeze_commit = freeze["implementation_commit"]
    freeze_tree = freeze["source_tree_hash"]
    salt_generator = salt_value["generator"]
    study_commit = sequence["scoring_source_commit"]
    if salt_generator["program"] != \
            "prepare_v2b_nll_confirmation_salt.py" \
            or salt_generator["program_sha256"] != _frozen_file_sha(
                freeze, "prepare_v2b_nll_confirmation_salt.py") \
            or study["generator"]["program"] != \
            "eval_v2b_nll_confirmation.py" \
            or study["generator"]["program_sha256"] != _frozen_file_sha(
                freeze, "eval_v2b_nll_confirmation.py") \
            or salt_generator["source_tree_hash"] != freeze_tree \
            or study["generator"]["source_tree_hash"] != freeze_tree \
            or not ancestor_fn(freeze_commit,
                               salt_generator["source_commit"]) \
            or not ancestor_fn(salt_generator["source_commit"], adoption) \
            or not ancestor_fn(freeze_commit, study_commit) \
            or not ancestor_fn(adoption, study_commit):
        raise V2BError("freeze/salt/study ancestry or source-tree drift")

    records = provenance_inputs["model_batteries"]
    expected_batteries = study["bindings"]["model_batteries"]
    if not isinstance(records, list) or len(records) != N_MODELS \
            or study["bindings"]["all_model_batteries"] != \
            expected_batteries:
        raise V2BError("masking requires exact four battery predecessors")
    core_bindings = {
        name: copy.deepcopy(study["bindings"][name])
        for name in ("implementation_freeze", "source_gate",
                     "bound_sample", "assembly")}
    normalized, battery_bindings = [], []
    for index, (model_id, record, expected_binding, study_model) in enumerate(
            zip(MODEL_ORDER, records, expected_batteries, study["models"])):
        battery, battery_binding = _artifact_record_binding(
            record, BATTERY_SCHEMA, f"model battery {model_id}")
        if battery_binding != expected_binding:
            raise V2BError("study/model-battery exact binding/order drift")
        contract = normalize_battery(
            battery, protocol, assembly, core_bindings)
        if contract["model"] != study_model["model"] \
                or contract["execution"] != study_model["execution"] \
                or contract["model"]["id"] != model_id:
            raise V2BError("battery/study model slot or execution drift")
        battery_commit = battery["generator"]["source_commit"]
        if battery["generator"]["program"] != \
                "v2b_nll_confirmation_battery.py" \
                or battery["generator"]["program_sha256"] != \
                _frozen_file_sha(
                    freeze, "v2b_nll_confirmation_battery.py") \
                or battery_commit != contract["execution"][
                "battery_source_commit"] \
                or battery["generator"]["source_tree_hash"] != freeze_tree \
                or contract["execution"]["source_tree_hash"] != freeze_tree \
                or not ancestor_fn(freeze_commit, battery_commit) \
                or not ancestor_fn(battery_commit, study_commit):
            raise V2BError("battery/freeze/study provenance drift")
        normalized.append(contract)
        battery_bindings.append(battery_binding)
    return dict(freeze=freeze, freeze_binding=freeze_binding,
                freeze_commit=freeze_commit, freeze_tree=freeze_tree,
                adoption=adoption, study_commit=study_commit,
                normalized=normalized,
                battery_bindings=battery_bindings)


def _validate_score_bundle(protocol, assembly, assembly_binding,
                           salt_record, study_record, model_inputs,
                           provenance_inputs, ancestor_fn):
    """Return exact bindings, manifest, and private synthetic deltas."""
    salt_value = _record(salt_record, SALT_COMMITMENT_SCHEMA,
                         "salt commitment input")
    from prepare_v2b_nll_confirmation_salt import validate_commitment
    validate_commitment(salt_value)
    if salt_value["salt_sha256"] in FORBIDDEN_SALT_SHA256:
        raise V2BError("masking refuses a pilot/public/known salt")
    study = _record(study_record, STUDY_COMPLETE_SCHEMA,
                    "study completion input")
    validate_study_complete(study, protocol, assembly, ancestor_fn)
    bindings = _expected_bindings(
        assembly_binding, salt_record, study_record, study)
    if salt_value["bindings"]["implementation_freeze"] != \
            bindings["implementation_freeze"] \
            or salt_value["bindings"]["bound_sample"] != \
            bindings["bound_sample"] \
            or salt_value["bindings"]["assembly"] != bindings["assembly"]:
        raise V2BError("salt commitment predecessor binding drift")
    provenance = _validate_provenance_inputs(
        protocol, assembly, study, salt_value, provenance_inputs,
        ancestor_fn)

    if not isinstance(model_inputs, list) or len(model_inputs) != N_MODELS:
        raise V2BError("masking requires exactly four model input bundles")
    cohort_keys = assembly.get("ordered_target_keys", {}).get("keys")
    if not isinstance(cohort_keys, list) or len(cohort_keys) != N_TARGETS \
            or study.get("ordered_target_keys", {}).get("keys") != cohort_keys:
        raise V2BError("masking cohort is not the exact sealed 200 targets")
    study_artifacts = study["model_artifacts"]
    validated_models, deltas = [], {}
    for model_index, (model_id, bundle, study_artifact) in enumerate(
            zip(MODEL_ORDER, model_inputs, study_artifacts)):
        _exact_keys(bundle, {"path", "sha256", "value", "targets"},
                    f"model bundle {model_id}")
        model_record = {name: bundle[name]
                        for name in ("path", "sha256", "value")}
        model_complete = _record(
            model_record, MODEL_COMPLETE_SCHEMA,
            f"model completion {model_id}")
        expected_model_binding = dict(
            model_id=model_id, path=bundle["path"],
            schema=MODEL_COMPLETE_SCHEMA, sha256=bundle["sha256"])
        if study_artifact != expected_model_binding:
            raise V2BError("study/model-completion exact binding drift")
        study_model = study["models"][model_index]
        normalized = provenance["normalized"][model_index]
        expected_score_bindings = _score_bindings(study, model_index)
        if study_model["model_complete"] != expected_model_binding:
            raise V2BError("study model slot/completion binding drift")
        validate_model_complete(
            model_complete, protocol, assembly, expected_score_bindings,
            expected_model=study_model["model"],
            expected_execution=study_model["execution"],
            ancestor_fn=ancestor_fn)
        model_commit = model_complete["generator"]["source_commit"]
        model_sequence = model_complete["salt_sequence"]
        scorer_sha = _frozen_file_sha(
            provenance["freeze"], "eval_v2b_nll_confirmation.py")
        if model_complete["generator"]["program"] != \
                "eval_v2b_nll_confirmation.py" \
                or model_complete["generator"]["program_sha256"] != \
                scorer_sha \
                or model_complete["model"] != normalized["model"] \
                or model_complete["execution"] != normalized["execution"] \
                or model_complete["bindings"] != expected_score_bindings \
                or model_sequence[
                    "salt_commitment_adoption_commit"] != \
                provenance["adoption"] \
                or model_commit != model_sequence["scoring_source_commit"] \
                or model_complete["generator"]["source_tree_hash"] != \
                provenance["freeze_tree"] \
                or not ancestor_fn(model_commit,
                                   provenance["study_commit"]):
            raise V2BError("model completion does not join frozen study slot")
        targets = bundle["targets"]
        if not isinstance(targets, list) or len(targets) != N_TARGETS:
            raise V2BError("model bundle lacks exact 200 targets")
        for target_index, (record, expected_artifact) in enumerate(
                zip(targets, model_complete["target_artifacts"])):
            target = _record(record, TARGET_SCHEMA,
                             f"target score {model_id}/{target_index}")
            if record["path"] != expected_artifact["path"] \
                    or record["sha256"] != expected_artifact["sha256"]:
                raise V2BError("target score does not match model manifest")
            validate_target_score(
                target, protocol, assembly, target_index,
                expected_score_bindings, study_model["model"],
                study_model["execution"], model_complete["shard_count"],
                ancestor_fn)
            target_commit = target["generator"]["source_commit"]
            target_sequence = target["salt_sequence"]
            battery_commit = normalized["execution"][
                "battery_source_commit"]
            if target["generator"]["program"] != \
                    "eval_v2b_nll_confirmation.py" \
                    or target["generator"]["program_sha256"] != scorer_sha \
                    or target["bindings"] != expected_score_bindings \
                    or target["model"] != study_model["model"] \
                    or target["execution"] != study_model["execution"] \
                    or target_sequence[
                        "salt_commitment_adoption_commit"] != \
                    provenance["adoption"] \
                    or target_commit != target_sequence[
                        "scoring_source_commit"] \
                    or target["generator"]["source_tree_hash"] != \
                    provenance["freeze_tree"] \
                    or not ancestor_fn(battery_commit, target_commit) \
                    or not ancestor_fn(target_commit, model_commit):
                raise V2BError("target score does not join frozen study slot")
            if target["target_key"] != cohort_keys[target_index]:
                raise V2BError("target score cohort order drift")
            for contrast_id, value in _target_deltas(target).items():
                deltas[(model_id, contrast_id,
                        target["target_key"])] = value
        validated_models.append((
            model_id, model_complete, targets, expected_model_binding))
    manifest = _score_manifest(
        validated_models, cohort_keys, provenance["freeze_binding"],
        provenance["battery_bindings"])
    validate_score_manifest(manifest, cohort_keys)
    return dict(bindings=bindings, manifest=manifest, deltas=deltas,
                cohort_keys=cohort_keys, study=study,
                salt_commitment=salt_value, provenance=provenance)


def _ledger_expected(bindings, manifest):
    rows = {
        "protocol": PROTOCOL_RAW_SHA256,
        "implementation_freeze": manifest[
            "implementation_freeze"]["sha256"],
        "assembly": bindings["assembly"]["sha256"],
        "salt_commitment": bindings["salt_commitment"]["sha256"],
        "study_complete": bindings["study_complete"]["sha256"],
    }
    for model_index, model in enumerate(manifest["models"]):
        model_id = model["model_id"]
        rows[f"model_battery:{model_id}"] = manifest[
            "model_batteries"][model_index]["sha256"]
        rows[f"model_complete:{model_id}"] = \
            model["model_complete"]["sha256"]
        for artifact in model["target_artifacts"]:
            rows[f"target_score:{model_id}:{artifact['target_index']:04d}"] \
                = artifact["sha256"]
    return rows


def validate_input_ledger(value, expected_sha_by_label):
    _exact_keys(value, PUBLIC_LEDGER_KEYS, "masking input ledger")
    entries = value["entries"]
    if value["algorithm"] != "sha256-only-public-input-ledger-v1" \
            or value["private_pre_post_equal"] is not True \
            or not isinstance(entries, list) \
            or value["n_entries"] != len(entries):
        raise V2BError("masking input-ledger header drift")
    labels, observed = [], {}
    for index, row in enumerate(entries):
        _exact_keys(row, {"label", "sha256"},
                    f"masking input ledger[{index}]")
        if not isinstance(row["label"], str) or not row["label"] \
                or not _hex(row["sha256"]):
            raise V2BError("malformed masking input-ledger row")
        if row["label"] in observed:
            raise V2BError("duplicate masking input-ledger label")
        labels.append(row["label"])
        observed[row["label"]] = row["sha256"]
    digest = sha256_sorted_json(entries)
    if labels != sorted(expected_sha_by_label) \
            or observed != expected_sha_by_label \
            or value["entries_sha256"] != digest:
        raise V2BError("masking input ledger/hash/binding drift")
    return value


def ledger_record(pre, post):
    if pre != post:
        raise V2BError("confirmation masking inputs changed during execution")
    projected = []
    for index, row in enumerate(pre):
        _exact_keys(row, {"label", "bytes", "sha256"},
                    f"private input ledger[{index}]")
        if not isinstance(row["label"], str) or not row["label"] \
                or not isinstance(row["bytes"], int) \
                or isinstance(row["bytes"], bool) or row["bytes"] < 0 \
                or not _hex(row["sha256"]):
            raise V2BError("malformed private masking input-ledger row")
        projected.append(dict(label=row["label"], sha256=row["sha256"]))
    projected.sort(key=lambda row: row["label"])
    if len({row["label"] for row in projected}) != len(projected):
        raise V2BError("duplicate private masking input-ledger label")
    return dict(algorithm="sha256-only-public-input-ledger-v1",
                n_entries=len(projected), entries=projected,
                entries_sha256=sha256_sorted_json(projected),
                private_pre_post_equal=True)


def build_masked_value(protocol, assembly, assembly_binding, salt_record,
                       study_record, model_inputs, provenance_inputs, salt,
                       input_ledger, generator, ancestor_fn=None):
    """Pure deterministic constructor over exact in-memory artifact records."""
    if protocol.get("study_id") != STUDY_ID:
        raise V2BError("masking protocol/study drift")
    generator = _generator(copy.deepcopy(generator))
    ancestor_fn = ancestor_fn or git_is_ancestor
    bundle = _validate_score_bundle(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, ancestor_fn)
    if salt_commitment(salt) != bundle["salt_commitment"]["salt_sha256"]:
        raise V2BError("private salt does not match public commitment")
    sequence = bundle["study"]["salt_sequence"]
    adoption = sequence["salt_commitment_adoption_commit"]
    scoring = sequence["scoring_source_commit"]
    masking = generator["source_commit"]
    provenance = bundle["provenance"]
    if adoption != provenance["adoption"] \
            or generator["source_tree_hash"] != provenance["freeze_tree"] \
            or not ancestor_fn(provenance["freeze_commit"], masking) \
            or not ancestor_fn(adoption, scoring) \
            or not ancestor_fn(scoring, masking):
        raise V2BError("freeze/salt/scoring/masking ancestry is not monotone")

    keys = bundle["cohort_keys"]
    public_models = []
    all_family_ids = set()
    for model_id in MODEL_ORDER:
        families = []
        for contrast_id in CONTRAST_IDS:
            opaque = family_id(salt, model_id, contrast_id)
            if opaque in all_family_ids:
                raise V2BError("opaque confirmation family collision")
            all_family_ids.add(opaque)
            rows = [dict(
                target_key=key,
                ciphertext=encrypt_delta(
                    salt, model_id, contrast_id, key,
                    bundle["deltas"][(model_id, contrast_id, key)]))
                    for key in keys]
            families.append(dict(
                family_id=opaque, n_rows=N_TARGETS, rows=rows,
                rows_sha256=sha256_sorted_json(rows)))
        # Family position must not encode the frozen contrast order.
        families.sort(key=lambda row: row["family_id"])
        public_models.append(dict(
            model_id=model_id, n_families=N_CONTRASTS,
            families=families,
            families_sha256=sha256_sorted_json(families)))
    bindings = bundle["bindings"]
    validate_input_ledger(
        input_ledger, _ledger_expected(bindings, bundle["manifest"]))
    value = dict(
        schema=MASKED_SCHEMA, state=MASKED_STATE, study_id=STUDY_ID,
        repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=protocol_record(), bindings=copy.deepcopy(bindings),
        ancestry=dict(
            salt_commitment_adoption_commit=adoption,
            scoring_source_commit=scoring,
            masking_source_commit=masking, verified=True),
        cohort=dict(n_targets=N_TARGETS,
                    ordered_target_keys=copy.deepcopy(keys),
                    ordered_target_keys_sha256=sha256_json(keys)),
        grid=dict(model_ids=list(MODEL_ORDER), n_models=N_MODELS,
                  n_contrasts_per_model=N_CONTRASTS,
                  n_masked_rows=N_MASKED_ROWS, ciphertext_bytes=8,
                  ciphertext_hex_chars=16),
        score_manifest=copy.deepcopy(bundle["manifest"]),
        models=public_models,
        models_sha256=sha256_sorted_json(public_models),
        input_ledger=copy.deepcopy(input_ledger),
        generator=copy.deepcopy(generator))
    return validate_masked(value, protocol, assembly, bindings)


def validate_masked(value, protocol, assembly, expected_bindings=None):
    """Strict public validator; it never needs the salt or raw outcomes."""
    _exact_keys(value, MASKED_TOP_KEYS, "confirmation masked artifact")
    if value["schema"] != MASKED_SCHEMA or value["state"] != MASKED_STATE \
            or value["study_id"] != STUDY_ID or value["repo"] != "sympy" \
            or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != protocol_record():
        raise V2BError("confirmation masked identity drift")
    bindings = value["bindings"]
    _exact_keys(bindings, set(MASKED_BINDING_SCHEMAS), "masked bindings")
    for label, schema in MASKED_BINDING_SCHEMAS.items():
        _binding(bindings[label], schema, f"masked {label}")
    if expected_bindings is not None and bindings != expected_bindings:
        raise V2BError("confirmation masked predecessor binding drift")
    assembly_binding = bindings["assembly"]
    if assembly.get("schema") != ASSEMBLY_SCHEMA:
        raise V2BError("masked validator received wrong assembly schema")

    ancestry = value["ancestry"]
    _exact_keys(ancestry, {
        "salt_commitment_adoption_commit", "scoring_source_commit",
        "masking_source_commit", "verified"}, "masking ancestry")
    if any(not _hex(ancestry[name], 40) for name in (
            "salt_commitment_adoption_commit", "scoring_source_commit",
            "masking_source_commit")) or ancestry["verified"] is not True:
        raise V2BError("malformed masking ancestry")
    _generator(value["generator"])
    if value["generator"]["source_commit"] != \
            ancestry["masking_source_commit"]:
        raise V2BError("masking generator/ancestry drift")

    keys = assembly.get("ordered_target_keys", {}).get("keys")
    cohort = value["cohort"]
    _exact_keys(cohort, {"n_targets", "ordered_target_keys",
                         "ordered_target_keys_sha256"}, "masked cohort")
    if not isinstance(keys, list) or len(keys) != N_TARGETS \
            or cohort["n_targets"] != N_TARGETS \
            or cohort["ordered_target_keys"] != keys \
            or cohort["ordered_target_keys_sha256"] != sha256_json(keys):
        raise V2BError("masked cohort/order drift")
    grid = value["grid"]
    _exact_keys(grid, {"model_ids", "n_models", "n_contrasts_per_model",
                       "n_masked_rows", "ciphertext_bytes",
                       "ciphertext_hex_chars"}, "masked grid")
    if grid != dict(model_ids=list(MODEL_ORDER), n_models=N_MODELS,
                    n_contrasts_per_model=N_CONTRASTS,
                    n_masked_rows=N_MASKED_ROWS, ciphertext_bytes=8,
                    ciphertext_hex_chars=16):
        raise V2BError("masked fixed-width grid drift")
    manifest = validate_score_manifest(value["score_manifest"], keys)
    if manifest["implementation_freeze"] != \
            bindings["implementation_freeze"]:
        raise V2BError("score manifest/masked freeze binding drift")
    validate_input_ledger(value["input_ledger"],
                          _ledger_expected(bindings, manifest))

    models = value["models"]
    if not isinstance(models, list) or len(models) != N_MODELS \
            or value["models_sha256"] != sha256_sorted_json(models):
        raise V2BError("masked model table/hash drift")
    seen_families = set()
    for model_id, model in zip(MODEL_ORDER, models):
        _exact_keys(model, {"model_id", "n_families", "families",
                            "families_sha256"}, f"masked model {model_id}")
        families = model["families"]
        if model["model_id"] != model_id \
                or model["n_families"] != N_CONTRASTS \
                or not isinstance(families, list) \
                or len(families) != N_CONTRASTS \
                or families != sorted(families,
                                      key=lambda row: row.get("family_id", "")) \
                or model["families_sha256"] != sha256_sorted_json(families):
            raise V2BError("masked model/family table drift")
        for family in families:
            _exact_keys(family, {"family_id", "n_rows", "rows",
                                 "rows_sha256"}, "masked opaque family")
            opaque, rows = family["family_id"], family["rows"]
            if not isinstance(opaque, str) \
                    or re.fullmatch(r"fam-[0-9a-f]{16}", opaque) is None \
                    or opaque in seen_families \
                    or family["n_rows"] != N_TARGETS \
                    or not isinstance(rows, list) or len(rows) != N_TARGETS \
                    or family["rows_sha256"] != sha256_sorted_json(rows):
                raise V2BError("malformed/duplicate masked opaque family")
            seen_families.add(opaque)
            for key, row in zip(keys, rows):
                _exact_keys(row, {"target_key", "ciphertext"},
                            "masked ciphertext row")
                cipher = row["ciphertext"]
                if row["target_key"] != key \
                        or not isinstance(cipher, str) or len(cipher) != 16 \
                        or any(ch not in "0123456789abcdef" for ch in cipher):
                    raise V2BError("masked ciphertext/cohort row drift")
    if len(seen_families) != N_MODELS * N_CONTRASTS:
        raise V2BError("masked opaque family coverage is incomplete")
    return value


def replay_masked(value, protocol, assembly, assembly_binding, salt_record,
                  study_record, model_inputs, provenance_inputs, salt,
                  ancestor_fn=None):
    """Recompute every ciphertext and require byte-for-byte public equality."""
    validate_masked(value, protocol, assembly)
    rebuilt = build_masked_value(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, salt, value["input_ledger"],
        value["generator"],
        ancestor_fn=ancestor_fn)
    if rebuilt != value:
        raise V2BError("confirmation masked deterministic replay failed")
    return True


def load_masked(path, protocol, assembly, expected_bindings=None):
    value, digest = load_json(path, MASKED_SCHEMA)
    return validate_masked(
        value, protocol, assembly, expected_bindings), digest


def _load_record(path, schema):
    value, digest = load_json(path, schema)
    return dict(path=os.path.abspath(path), sha256=digest, value=value)


def _exact_target_directory(artifacts):
    paths = [row.get("path") for row in artifacts]
    if any(not isinstance(path, str) or not os.path.isabs(path)
           for path in paths):
        raise V2BError("target-score paths must be absolute")
    parents = {os.path.dirname(path) for path in paths}
    if len(parents) != 1:
        raise V2BError("one model's target scores span multiple directories")
    expected = [f"target-{index:04d}.json" for index in range(N_TARGETS)]
    if [os.path.basename(path) for path in paths] != expected:
        raise V2BError("target-score path order/names drift")
    try:
        observed = sorted(os.listdir(next(iter(parents))))
    except OSError as err:
        raise V2BError(f"cannot list target-score directory: {err}") from err
    if observed != expected:
        raise V2BError("target-score directory has missing/extra files")


def _discover_paths(protocol_path, assembly_path, salt_path, study_path):
    study, _ = load_json(study_path, STUDY_COMPLETE_SCHEMA)
    study_bindings = study.get("bindings")
    if not isinstance(study_bindings, dict):
        raise V2BError("study completion lacks predecessor discovery")
    freeze_binding = study_bindings.get("implementation_freeze")
    battery_bindings = study_bindings.get("model_batteries")
    if not isinstance(freeze_binding, dict) \
            or not isinstance(battery_bindings, list) \
            or len(battery_bindings) != N_MODELS:
        raise V2BError("study completion lacks freeze/four batteries")
    freeze_path = freeze_binding.get("path")
    battery_paths = [row.get("path") if isinstance(row, dict) else None
                     for row in battery_bindings]
    if not isinstance(freeze_path, str) or not freeze_path \
            or any(not isinstance(path, str) or not path
                   for path in battery_paths):
        raise V2BError("study predecessor path discovery drift")
    model_paths = [row.get("path") for row in study.get("model_artifacts", [])
                   if isinstance(row, dict)]
    if len(model_paths) != N_MODELS:
        raise V2BError("study completion does not discover four models")
    target_paths = []
    for model_path in model_paths:
        model, _ = load_json(model_path, MODEL_COMPLETE_SCHEMA)
        artifacts = model.get("target_artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != N_TARGETS:
            raise V2BError("model completion does not discover 200 targets")
        _exact_target_directory(artifacts)
        target_paths.append([row["path"] for row in artifacts])
    paths = [("protocol", protocol_path),
             ("implementation_freeze", freeze_path),
             ("assembly", assembly_path),
             ("salt_commitment", salt_path),
             ("study_complete", study_path)]
    for model_id, battery_path, model_path, targets in zip(
            MODEL_ORDER, battery_paths, model_paths, target_paths):
        paths.append((f"model_battery:{model_id}", battery_path))
        paths.append((f"model_complete:{model_id}", model_path))
        paths.extend((f"target_score:{model_id}:{index:04d}", path)
                     for index, path in enumerate(targets))
    return model_paths, target_paths, paths


def _file_ledger(paths):
    rows = []
    for label, path in paths:
        try:
            size = os.path.getsize(path)
        except OSError as err:
            raise V2BError(f"cannot stat masking input {label}: {err}") \
                from err
        rows.append(dict(label=label, bytes=size, sha256=sha256_file(path)))
    rows.sort(key=lambda row: row["label"])
    return rows


def _load_discovered(model_paths, target_paths, study_path,
                     salt_commitment_path):
    study_record = _load_record(study_path, STUDY_COMPLETE_SCHEMA)
    salt_record = _load_record(salt_commitment_path, SALT_COMMITMENT_SCHEMA)
    models = []
    for model_path, targets in zip(model_paths, target_paths):
        model_record = _load_record(model_path, MODEL_COMPLETE_SCHEMA)
        target_records = [_load_record(path, TARGET_SCHEMA)
                          for path in targets]
        models.append(dict(**model_record, targets=target_records))
    return salt_record, study_record, models


def _load_provenance_inputs(study_record, salt_commitment_path,
                            live_freeze=False):
    study = study_record["value"]
    freeze_binding = study["bindings"]["implementation_freeze"]
    battery_bindings = study["bindings"]["model_batteries"]
    freeze_record = _load_record(
        freeze_binding["path"], IMPLEMENTATION_FREEZE_SCHEMA)
    if live_freeze:
        validate_live_freeze(freeze_record["value"], freeze_record["path"])
    battery_records = [
        _load_record(binding["path"], BATTERY_SCHEMA)
        for binding in battery_bindings]
    return dict(
        implementation_freeze=freeze_record,
        model_batteries=battery_records,
        salt_adoption_commit=salt_adoption_commit(salt_commitment_path))


def prepare(private_salt_path, salt_commitment_path, assembly_path,
            study_complete_path, protocol_path=PROTOCOL_PATH):
    """Production, model-free masker over committed score evidence."""
    if not source_clean():
        raise V2BError("source tree dirty before confirmation masking")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation masking requires canonical protocol")
    commit, tree = head_commit(), source_tree_hash()
    model_paths, target_paths, paths = _discover_paths(
        protocol_path, assembly_path, salt_commitment_path,
        study_complete_path)
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
    salt_before = load_salt_file(private_salt_path)
    generator = dict(program=PROGRAM, program_sha256=sha256_file(__file__),
                     source_commit=commit, source_tree_hash=tree)
    value = build_masked_value(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, salt_before,
        ledger_record(pre, pre), generator,
        ancestor_fn=git_is_ancestor)
    salt_after = load_salt_file(private_salt_path)
    post = _file_ledger(paths)
    if salt_before != salt_after or pre != post \
            or not source_clean() or head_commit() != commit \
            or source_tree_hash() != tree:
        raise V2BError("masking inputs/source/private salt changed")
    # Replacing the provisional equal ledger is a no-op on valid production
    # inputs, but makes the pre/post contract explicit.
    value["input_ledger"] = ledger_record(pre, post)
    return validate_masked(value, protocol, assembly, value["bindings"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-salt", required=True)
    parser.add_argument("--salt-commitment", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--study-complete", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite masked artifact")
    value = prepare(
        args.private_salt, args.salt_commitment, args.assembly,
        args.study_complete, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-mask] exact rows={N_MASKED_ROWS} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(2)
