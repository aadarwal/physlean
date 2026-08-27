#!/usr/bin/env python3
"""Adversarial synthetic tests for confirmation masking."""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval_v2b_nll_confirmation import (
    MODEL_BY_ID, MODEL_IDS, build_model_complete, build_study_complete,
    build_target_score, normalize_battery, salt_sequence)
from freeze_v2b_nll_confirmation import (
    FILE_ROLES, PROGRAM as FREEZE_PROGRAM, build_freeze_value)
from prepare_v2b_nll_confirmation_masked import (
    MASKED_SCHEMA, MODEL_ORDER, N_MASKED_ROWS, PROGRAM, _ledger_expected,
    _validate_score_bundle, build_masked_value, ledger_record,
    publication_sha256, replay_masked, validate_masked)
from prepare_v2b_nll_confirmation_salt import (
    ALGORITHM, ASSEMBLY_SCHEMA, IMPLEMENTATION_FREEZE_SCHEMA, SAMPLE_SCHEMA,
    SALT_COMMITMENT_SCHEMA, STATE, build_commitment_value, protocol_record)
from v2b_common import (
    V2BError, sha256_bytes, sha256_file, sha256_json,
    sha256_sorted_json)
from v2b_nll_confirmation_crypto import (
    CONTRAST_IDS, encrypt_delta, family_id, salt_commitment,
    verify_ciphertext)

# Reuse the scorer's public-builder fixture primitives, but rebuild every
# target with this test's exact salt binding and raw-file digest.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_eval_v2b_nll_confirmation as score_fixture  # noqa: E402
import test_v2b_nll_confirmation_battery as battery_fixture  # noqa: E402
from v2b_nll_confirmation_battery import build_battery_value  # noqa: E402


SALT = bytes([42]) * 32
_CACHE = None
TARGET_COMMIT = "1" * 40
MODEL_COMMIT = "2" * 40
STUDY_COMMIT = "3" * 40


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation masking evidence"
    except V2BError as error:
        if text is not None:
            assert text in str(error), str(error)


def _salt_commitment_fixture(protocol, base_bindings):
    freeze = base_bindings["implementation_freeze"]
    sample = base_bindings["bound_sample"]
    assembly = base_bindings["assembly"]
    entries = [
        dict(label="assembly", bytes=1, sha256=assembly["sha256"]),
        dict(label="bound_sample", bytes=1, sha256=sample["sha256"]),
        dict(label="implementation_freeze", bytes=1,
             sha256=freeze["sha256"]),
        dict(label="protocol", bytes=1,
             sha256=protocol_record()["raw_sha256"]),
    ]
    digest = sha256_sorted_json(entries)
    ledger = dict(
        algorithm="sha256-sorted-json-file-ledger-v1",
        n_entries=4, entries=entries, pre_entries_sha256=digest,
        post_entries_sha256=digest, entries_sha256=digest, unchanged=True)
    generator = dict(
        program="prepare_v2b_nll_confirmation_salt.py",
        program_sha256=sha256_file(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prepare_v2b_nll_confirmation_salt.py")),
        source_commit="9" * 40,
        source_tree_hash="d" * 64)
    return build_commitment_value(
        protocol, freeze, sample, assembly, salt_commitment(SALT), ledger,
        generator)


def _freeze(protocol):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for relative, role in sorted(FILE_ROLES.items()):
        path = os.path.join(root, relative)
        digest = sha256_file(path) if os.path.exists(path) else "7" * 64
        rows.append(dict(path=relative, sha256=digest, role=role))
    return build_freeze_value(protocol, rows, "c" * 40, "d" * 64)


def _score(context, prefix, body, cell_id, _execution_identity):
    prompt = context + prefix + body
    n_body = len(body.decode("utf-8"))
    ledger = dict(
        schema="v2b_body_token_ledger_v1", paired_schema_version="v4",
        exact_body_bytes=len(body), exact_body_codepoints=n_body,
        scored_body_bytes=len(body), scored_body_codepoints=n_body,
        straddled_body_bytes=0, straddled_body_codepoints=0,
        n_boundary_straddle_tokens=0,
        primary_token_indices=[len(prompt)], boundary_token_indices=[],
        inclusive_token_indices=[len(prompt)], boundary_groups=[],
        boundary_signature=sha256_json([]))
    return dict(
        prompt_sha256=sha256_bytes(prompt), prompt_bytes=len(prompt),
        n_prompt_tokens=len(prompt), body_layout_signature="f" * 64,
        body_token_ledger=ledger,
        nll_nats=float(len(prompt) + list(score_fixture.CELL_ORDER).index(
            cell_id)) / 10.0,
        n_scored_body_tokens=1)


def _score_generator(commit):
    value = score_fixture._generator()
    value["source_commit"] = commit
    return value


def fixture():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    protocol, core_bindings, assembly, materialized = \
        battery_fixture._assembly_fixture()
    freeze = _freeze(protocol)
    freeze_record = dict(
        path="/sealed/confirmation-freeze.json",
        sha256=publication_sha256(freeze), value=freeze)
    freeze_binding = dict(
        path=freeze_record["path"], schema=freeze["schema"],
        sha256=freeze_record["sha256"])
    assembly["bindings"]["implementation_freeze"] = \
        copy.deepcopy(freeze_binding)
    core_bindings["implementation_freeze"] = copy.deepcopy(freeze_binding)
    assembly_binding = dict(
        path="/sealed/confirmation-assembly.json", schema=ASSEMBLY_SCHEMA,
        sha256=publication_sha256(assembly))
    core_bindings["assembly"] = copy.deepcopy(assembly_binding)

    battery_records, battery_bindings, contracts = [], [], []
    for model_id in MODEL_ORDER:
        args = battery_fixture._build_args(model_id)
        args.update(
            freeze=freeze, bindings=copy.deepcopy(core_bindings),
            assembly=assembly, materialized=materialized)
        args["execution_provenance"]["source_commit"] = "8" * 40
        args["execution_provenance"]["source_tree_hash"] = "d" * 64
        args["generator"]["source_commit"] = "8" * 40
        args["generator"]["source_tree_hash"] = "d" * 64
        args["generator"]["program_sha256"] = sha256_file(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "v2b_nll_confirmation_battery.py"))
        args["input_ledger"] = battery_fixture._ledger(
            args["protocol_binding"], args["bindings"],
            args["execution_provenance"])
        battery = build_battery_value(**args)
        path = f"/batteries/{model_id}.json"
        record = dict(path=path, sha256=publication_sha256(battery),
                      value=battery)
        binding = dict(path=path, schema=battery["schema"],
                       sha256=record["sha256"])
        battery_records.append(record)
        battery_bindings.append(binding)
        contracts.append(normalize_battery(
            battery, protocol, assembly, core_bindings))

    salt_base_bindings = dict(
        implementation_freeze=freeze_binding,
        bound_sample=core_bindings["bound_sample"],
        assembly=assembly_binding)
    salt_value = _salt_commitment_fixture(protocol, salt_base_bindings)
    salt_path = "/sealed/confirmation-salt-commitment.json"
    salt_record = dict(path=salt_path, sha256=publication_sha256(salt_value),
                       value=salt_value)
    salt_binding = dict(path=salt_path, schema=SALT_COMMITMENT_SCHEMA,
                        sha256=salt_record["sha256"])
    model_inputs = []
    model_reducer_inputs = []
    for model_index, model_id in enumerate(MODEL_ORDER):
        contract = contracts[model_index]
        bindings = dict(
            implementation_freeze=copy.deepcopy(freeze_binding),
            source_gate=copy.deepcopy(core_bindings["source_gate"]),
            bound_sample=copy.deepcopy(core_bindings["bound_sample"]),
            assembly=copy.deepcopy(assembly_binding),
            model_battery=copy.deepcopy(battery_bindings[model_index]),
            all_model_batteries=copy.deepcopy(battery_bindings),
            salt_commitment=copy.deepcopy(salt_binding))
        targets = []
        for index in range(200):
            key = assembly["targets"][index]["key"]
            value = build_target_score(
                protocol, bindings, contract["model"],
                contract["execution"], salt_sequence(
                    "a" * 40, TARGET_COMMIT,
                    ancestor_fn=lambda _a, _b: True),
                assembly, index, materialized[key], contract["fit_by_pair"],
                0, contract["shard_count"], _score,
                _score_generator(TARGET_COMMIT))
            path = f"/scores/{model_id}/target-{index:04d}.json"
            targets.append(dict(path=path, sha256=publication_sha256(value),
                                value=value))
        model_value = build_model_complete(
            protocol, assembly, bindings, contract["model"],
            contract["execution"], salt_sequence(
                "a" * 40, MODEL_COMMIT,
                ancestor_fn=lambda _a, _b: True),
            contract["shard_count"],
            targets, _score_generator(MODEL_COMMIT),
            ancestor_fn=lambda _older, _newer: True)
        model_path = f"/complete/{model_id}.json"
        model_record = dict(path=model_path,
                            sha256=publication_sha256(model_value),
                            value=model_value, targets=targets)
        model_inputs.append(model_record)
        model_reducer_inputs.append(dict(
            path=model_path, sha256=model_record["sha256"],
            value=model_value))
    study_value = build_study_complete(
        protocol, assembly, model_reducer_inputs,
        _score_generator(STUDY_COMMIT), ancestor_fn=lambda _a, _b: True)
    study_record = dict(path="/complete/study.json",
                        sha256=publication_sha256(study_value),
                        value=study_value)
    provenance_inputs = dict(
        implementation_freeze=freeze_record,
        model_batteries=battery_records,
        salt_adoption_commit="a" * 40)
    bundle = _validate_score_bundle(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, lambda _a, _b: True)
    bindings, manifest = bundle["bindings"], bundle["manifest"]
    expected = _ledger_expected(bindings, manifest)
    entries = [dict(label=label, bytes=1, sha256=expected[label])
               for label in sorted(expected)]
    ledger = ledger_record(entries, copy.deepcopy(entries))
    generator = dict(
        program=PROGRAM,
        program_sha256=sha256_bytes(open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "prepare_v2b_nll_confirmation_masked.py"),
            "rb").read()),
        source_commit="e" * 40, source_tree_hash="d" * 64)
    masked = build_masked_value(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, SALT, ledger, generator,
        ancestor_fn=lambda _older, _newer: True)
    _CACHE = dict(
        protocol=protocol, assembly=assembly,
        assembly_binding=assembly_binding, salt_record=salt_record,
        study_record=study_record, model_inputs=model_inputs,
        provenance_inputs=provenance_inputs,
        ledger=ledger, generator=generator, masked=masked,
        masked_record=dict(path="/masked/confirmation.json",
                           sha256=publication_sha256(masked), value=masked))
    return _CACHE


def _opaque_family(masked, model_id, contrast_id):
    opaque = family_id(SALT, model_id, contrast_id)
    model = next(row for row in masked["models"]
                 if row["model_id"] == model_id)
    return next(row for row in model["families"]
                if row["family_id"] == opaque)


def test_exact_fixed_width_shape_and_padding_has_no_public_status():
    data = fixture()
    masked = data["masked"]
    assert masked["schema"] == MASKED_SCHEMA
    assert masked["grid"]["n_masked_rows"] == N_MASKED_ROWS == 4000
    assert len(masked["models"]) == 4
    assert all(len(model["families"]) == 5 for model in masked["models"])
    assert all(len(family["rows"]) == 200
               for model in masked["models"] for family in model["families"])
    assert data["model_inputs"][0]["targets"][0]["value"][
        "generator"]["source_commit"] == TARGET_COMMIT
    assert data["model_inputs"][0]["value"]["generator"][
        "source_commit"] == MODEL_COMMIT
    assert data["study_record"]["value"]["generator"][
        "source_commit"] == STUDY_COMMIT
    assert len({TARGET_COMMIT, MODEL_COMMIT, STUDY_COMMIT,
                masked["generator"]["source_commit"]}) == 4
    public_rows = json.dumps(masked["models"], sort_keys=True)
    assert "eligib" not in public_rows and "padding" not in public_rows
    assert all(contrast not in public_rows for contrast in CONTRAST_IDS)

    key0 = masked["cohort"]["ordered_target_keys"][0]
    e1b = _opaque_family(masked, MODEL_ORDER[0], "E1b")
    assert verify_ciphertext(
        SALT, MODEL_ORDER[0], "E1b", key0,
        e1b["rows"][0]["ciphertext"], None)
    key1 = masked["cohort"]["ordered_target_keys"][1]
    _reject(lambda: verify_ciphertext(
        SALT, MODEL_ORDER[0], "E1b", key1,
        e1b["rows"][1]["ciphertext"], None), "does not replay")


def test_public_validator_rejects_missing_extra_leak_and_bad_order():
    data = fixture()
    for mutate in (
            lambda value: value["models"][0]["families"][0]["rows"].pop(),
            lambda value: value["models"][0]["families"][0]["rows"][0]
            .update(eligible=True),
            lambda value: value["models"][0]["families"].append(
                copy.deepcopy(value["models"][0]["families"][0])),
            lambda value: value["models"][0]["families"].reverse()):
        bad = copy.deepcopy(data["masked"])
        mutate(bad)
        _reject(lambda bad=bad: validate_masked(
            bad, data["protocol"], data["assembly"]))


def test_ciphertext_tamper_is_shape_valid_but_replay_rejected():
    data = fixture()
    bad = copy.deepcopy(data["masked"])
    row = bad["models"][0]["families"][0]["rows"][0]
    row["ciphertext"] = ("0" if row["ciphertext"][0] != "0" else "1") \
        + row["ciphertext"][1:]
    family = bad["models"][0]["families"][0]
    family["rows_sha256"] = sha256_sorted_json(family["rows"])
    bad["models"][0]["families_sha256"] = sha256_sorted_json(
        bad["models"][0]["families"])
    bad["models_sha256"] = sha256_sorted_json(bad["models"])
    validate_masked(bad, data["protocol"], data["assembly"])
    _reject(lambda: replay_masked(
        bad, data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], SALT,
        ancestor_fn=lambda _a, _b: True), "replay failed")


def test_raw_missing_self_consistent_tamper_and_binding_drift_fail_closed():
    data = fixture()
    missing = [dict(bundle) for bundle in data["model_inputs"]]
    missing[0] = dict(missing[0], targets=missing[0]["targets"][:-1])
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], missing,
        data["provenance_inputs"], SALT,
        data["ledger"], data["generator"],
        ancestor_fn=lambda _a, _b: True), "exact 200")

    tampered = [dict(bundle) for bundle in data["model_inputs"]]
    targets = list(tampered[0]["targets"])
    record = copy.deepcopy(targets[0])
    record["value"]["cells"][0]["nll_nats"] += 1.0
    record["value"]["cells_sha256"] = sha256_sorted_json(
        record["value"]["cells"])
    record["sha256"] = publication_sha256(record["value"])
    targets[0] = record
    tampered[0] = dict(tampered[0], targets=targets)
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], tampered,
        data["provenance_inputs"], SALT,
        data["ledger"], data["generator"],
        ancestor_fn=lambda _a, _b: True), "model manifest")

    salt_record = copy.deepcopy(data["salt_record"])
    # Public deterministic crypto fixture bytes(range(32)) is forbidden even
    # if wrapped in a self-consistent commitment object.
    salt_record["value"]["salt_sha256"] = \
        "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd"
    salt_record["sha256"] = publication_sha256(salt_record["value"])
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        salt_record, data["study_record"], data["model_inputs"],
        data["provenance_inputs"], SALT,
        data["ledger"], data["generator"],
        ancestor_fn=lambda _a, _b: True), "known salt")


def test_ineligible_score_and_wrong_padding_are_rejected_by_replay():
    data = fixture()
    # A public row remains fixed-width, but replacing registered padding with
    # a real scalar cannot pass deterministic replay.
    bad = copy.deepcopy(data["masked"])
    model_id = MODEL_ORDER[0]
    family = next(row for row in bad["models"][0]["families"]
                  if row["family_id"] == family_id(SALT, model_id, "E1b"))
    key = family["rows"][0]["target_key"]
    family["rows"][0]["ciphertext"] = encrypt_delta(
        SALT, model_id, "E1b", key, 1.0)
    family["rows_sha256"] = sha256_sorted_json(family["rows"])
    bad["models"][0]["families_sha256"] = sha256_sorted_json(
        bad["models"][0]["families"])
    bad["models_sha256"] = sha256_sorted_json(bad["models"])
    _reject(lambda: replay_masked(
        bad, data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], SALT,
        ancestor_fn=lambda _a, _b: True), "replay failed")


def test_ledger_toctou_and_deterministic_replay():
    data = fixture()
    assert replay_masked(
        data["masked"], data["protocol"], data["assembly"],
        data["assembly_binding"], data["salt_record"],
        data["study_record"], data["model_inputs"],
        data["provenance_inputs"], SALT,
        ancestor_fn=lambda _a, _b: True)
    private = [dict(label=row["label"], bytes=4, sha256=row["sha256"])
               for row in data["ledger"]["entries"]]
    post = copy.deepcopy(private)
    post[-1]["bytes"] += 1
    _reject(lambda: ledger_record(private, post),
            "changed during")
    bad_ledger = copy.deepcopy(data["ledger"])
    bad_ledger["entries"][0]["sha256"] = "0" * 64
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], SALT, bad_ledger, data["generator"],
        ancestor_fn=lambda _a, _b: True))


def test_model_slot_swap_and_alternate_freeze_fail_exact_joins():
    data = fixture()
    swapped = copy.deepcopy(data["model_inputs"])
    swapped[0], swapped[1] = swapped[1], swapped[0]
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], swapped,
        data["provenance_inputs"], SALT, data["ledger"], data["generator"],
        ancestor_fn=lambda _a, _b: True), "binding drift")

    provenance = copy.deepcopy(data["provenance_inputs"])
    alternate = copy.deepcopy(provenance["implementation_freeze"])
    alternate["value"]["implementation_commit"] = "0" * 40
    alternate["value"]["generator"]["source_commit"] = "0" * 40
    alternate["sha256"] = publication_sha256(alternate["value"])
    provenance["implementation_freeze"] = alternate
    _reject(lambda: build_masked_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        provenance, SALT, data["ledger"], data["generator"],
        ancestor_fn=lambda _a, _b: True), "freeze")


def _retarget_sequence(data, adoption, source):
    models = copy.deepcopy(data["model_inputs"])
    study = copy.deepcopy(data["study_record"])
    target_record = models[0]["targets"][0]
    target_record["value"]["salt_sequence"].update(
        salt_commitment_adoption_commit=adoption,
        scoring_source_commit=source)
    target_record["value"]["generator"]["source_commit"] = source
    target_record["sha256"] = publication_sha256(target_record["value"])
    model = models[0]["value"]
    model["target_artifacts"][0]["sha256"] = target_record["sha256"]
    model["target_artifacts_sha256"] = sha256_sorted_json(
        model["target_artifacts"])
    model["shards"][0]["target_artifacts_sha256"] = sha256_sorted_json(
        model["target_artifacts"])
    models[0]["sha256"] = publication_sha256(model)
    study_model_binding = study["value"]["model_artifacts"][0]
    study_model_binding["sha256"] = models[0]["sha256"]
    study["value"]["models"][0]["model_complete"] = copy.deepcopy(
        study_model_binding)
    study["value"]["model_artifacts_sha256"] = sha256_sorted_json(
        study["value"]["model_artifacts"])
    study["sha256"] = publication_sha256(study["value"])
    return study, models


def test_target_adoption_and_scoring_source_cannot_self_validate():
    data = fixture()
    cases = (
        ("4" * 40, TARGET_COMMIT, lambda _a, _b: True),
        ("a" * 40, "0" * 40,
         lambda older, newer: not (
             older == "0" * 40 and newer == MODEL_COMMIT)),
    )
    for adoption, source, ancestor_fn in cases:
        study, models = _retarget_sequence(data, adoption, source)
        _reject(lambda study=study, models=models,
                ancestor_fn=ancestor_fn: build_masked_value(
            data["protocol"], data["assembly"], data["assembly_binding"],
            data["salt_record"], study, models,
            data["provenance_inputs"], SALT,
            # The changed model/study raw hashes are intentionally absent
            # from this ledger; exact sequence joining happens earlier.
            data["ledger"], data["generator"],
            ancestor_fn=ancestor_fn), "target score")


def test_public_ledger_is_size_independent_sha_only_projection():
    data = fixture()
    ledgers = []
    for size in (4, 5, 6):
        private = [dict(label=row["label"], bytes=size,
                        sha256=row["sha256"])
                   for row in data["ledger"]["entries"]]
        ledgers.append(ledger_record(private, copy.deepcopy(private)))
    assert ledgers[0] == ledgers[1] == ledgers[2]
    encoded = json.dumps(ledgers[0], sort_keys=True)
    assert '"bytes"' not in encoded and '"size"' not in encoded
    assert all(set(row) == {"label", "sha256"}
               for row in ledgers[0]["entries"])


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("confirmation masked synthetic tests: PASS")
