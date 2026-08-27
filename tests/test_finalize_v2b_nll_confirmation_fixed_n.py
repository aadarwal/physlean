#!/usr/bin/env python3
"""Adversarial synthetic tests for the blind fixed-N completion gate."""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from finalize_v2b_nll_confirmation_fixed_n import (
    FIXED_N_SCHEMA, FIXED_N_STATE, N_CELL_RECORDS,
    N_TARGET_SCORE_ARTIFACTS, PROGRAM, _fixed_ledger_expected,
    build_fixed_n_value, validate_fixed_n)
from prepare_v2b_nll_confirmation_masked import (
    ledger_record, publication_sha256)
from v2b_common import V2BError, sha256_bytes, sha256_sorted_json
from v2b_nll_confirmation_crypto import encrypt_delta, family_id

from test_prepare_v2b_nll_confirmation_masked import SALT, fixture


_CACHE = None


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid fixed-N confirmation evidence"
    except V2BError as error:
        if text is not None:
            assert text in str(error), str(error)


def fixed_fixture():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    data = fixture()
    masked_binding = dict(
        path=data["masked_record"]["path"],
        schema=data["masked"]["schema"],
        sha256=data["masked_record"]["sha256"])
    expected = _fixed_ledger_expected(data["masked"], masked_binding)
    entries = [dict(label=label, bytes=1, sha256=expected[label])
               for label in sorted(expected)]
    ledger = ledger_record(entries, copy.deepcopy(entries))
    program_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "finalize_v2b_nll_confirmation_fixed_n.py")
    generator = dict(
        program=PROGRAM,
        program_sha256=sha256_bytes(open(program_path, "rb").read()),
        source_commit="f" * 40, source_tree_hash="d" * 64)
    value = build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], data["masked_record"], SALT,
        ledger, generator,
        ancestor_fn=lambda _a, _b: True)
    _CACHE = dict(**data, fixed=value, fixed_ledger=ledger,
                  fixed_generator=generator,
                  masked_binding=masked_binding)
    return _CACHE


def test_exact_fixed_n_gate_is_publicly_outcome_and_family_blind():
    data = fixed_fixture()
    value = data["fixed"]
    assert value["schema"] == FIXED_N_SCHEMA
    assert value["state"] == FIXED_N_STATE
    assert value["grid"]["n_target_score_artifacts"] == \
        N_TARGET_SCORE_ARTIFACTS == 800
    assert value["grid"]["n_cell_records"] == N_CELL_RECORDS == 4800
    assert value["grid"]["n_masked_rows"] == 4000
    assert value["input_ledger"]["n_entries"] == 814
    public = json.dumps(value, sort_keys=True)
    for forbidden in ('"ciphertext":', "family_id", "nll_nats",
                      "delta_bpb", "target_equal_mean", "E1a", "E2_seed",
                      '"bytes"', '"size"'):
        assert forbidden not in public
    assert validate_fixed_n(
        value, data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"]) == value


def test_masked_tamper_and_wrong_registered_padding_fail_replay():
    data = fixed_fixture()
    bad_masked = copy.deepcopy(data["masked"])
    model = bad_masked["models"][0]
    family = next(row for row in model["families"]
                  if row["family_id"] == family_id(
                      SALT, model["model_id"], "E1b"))
    key = family["rows"][0]["target_key"]
    family["rows"][0]["ciphertext"] = encrypt_delta(
        SALT, model["model_id"], "E1b", key, 2.0)
    family["rows_sha256"] = sha256_sorted_json(family["rows"])
    model["families_sha256"] = sha256_sorted_json(model["families"])
    bad_masked["models_sha256"] = sha256_sorted_json(bad_masked["models"])
    bad_record = dict(path=data["masked_record"]["path"],
                      sha256=publication_sha256(bad_masked),
                      value=bad_masked)
    _reject(lambda: build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], bad_record, SALT,
        data["fixed_ledger"], data["fixed_generator"],
        ancestor_fn=lambda _a, _b: True), "replay failed")


def test_missing_score_extra_binding_and_raw_tamper_fail_closed():
    data = fixed_fixture()
    missing = [dict(bundle) for bundle in data["model_inputs"]]
    missing[2] = dict(missing[2], targets=missing[2]["targets"][:-1])
    _reject(lambda: build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], missing,
        data["provenance_inputs"], data["masked_record"], SALT,
        data["fixed_ledger"],
        data["fixed_generator"], ancestor_fn=lambda _a, _b: True),
        "exact 200")

    tampered = [dict(bundle) for bundle in data["model_inputs"]]
    targets = list(tampered[1]["targets"])
    target = copy.deepcopy(targets[3])
    target["value"]["cells"][2]["nll_nats"] += 0.5
    target["value"]["cells_sha256"] = sha256_sorted_json(
        target["value"]["cells"])
    target["sha256"] = publication_sha256(target["value"])
    targets[3] = target
    tampered[1] = dict(tampered[1], targets=targets)
    _reject(lambda: build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], tampered,
        data["provenance_inputs"], data["masked_record"], SALT,
        data["fixed_ledger"],
        data["fixed_generator"], ancestor_fn=lambda _a, _b: True),
        "model manifest")

    extra_binding = copy.deepcopy(data["fixed"])
    extra_binding["bindings"]["alternate_score"] = copy.deepcopy(
        extra_binding["bindings"]["assembly"])
    _reject(lambda: validate_fixed_n(
        extra_binding, data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"]), "key drift")


def test_wrong_private_salt_and_mask_binding_are_rejected():
    data = fixed_fixture()
    _reject(lambda: build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], data["masked_record"], bytes([43]) * 32,
        data["fixed_ledger"], data["fixed_generator"],
        ancestor_fn=lambda _a, _b: True), "private salt")
    bad_binding = copy.deepcopy(data["masked_binding"])
    bad_binding["sha256"] = "0" * 64
    _reject(lambda: validate_fixed_n(
        data["fixed"], data["protocol"], data["assembly"], data["masked"],
        bad_binding), "masked binding")


def test_fixed_public_validator_rejects_status_count_and_ledger_tamper():
    data = fixed_fixture()
    mutations = (
        lambda value: value.update(state="partial-fixed-n"),
        lambda value: value["grid"].update(n_targets=199),
        lambda value: value["verification"].update(no_partial_n=False),
        lambda value: value["input_ledger"]["entries"].pop(),
        lambda value: value.update(ciphertexts=[]),
    )
    for mutate in mutations:
        bad = copy.deepcopy(data["fixed"])
        mutate(bad)
        _reject(lambda bad=bad: validate_fixed_n(
            bad, data["protocol"], data["assembly"], data["masked"],
            data["masked_binding"]))


def test_fixed_toctou_and_ancestry_fail_closed():
    data = fixed_fixture()
    private = [dict(label=row["label"], bytes=4, sha256=row["sha256"])
               for row in data["fixed_ledger"]["entries"]]
    post = copy.deepcopy(private)
    post[0]["sha256"] = "0" * 64
    _reject(lambda: ledger_record(private, post),
            "changed during")
    _reject(lambda: build_fixed_n_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], data["masked_record"], SALT,
        data["fixed_ledger"],
        data["fixed_generator"], ancestor_fn=lambda _a, _b: False),
        "ancestry")


def test_fixed_public_ledger_cannot_encode_4_5_6_byte_sizes():
    data = fixed_fixture()
    outputs = []
    for size in (4, 5, 6):
        private = [dict(label=row["label"], bytes=size,
                        sha256=row["sha256"])
                   for row in data["fixed_ledger"]["entries"]]
        outputs.append(ledger_record(private, copy.deepcopy(private)))
    assert outputs[0] == outputs[1] == outputs[2] == data["fixed_ledger"]
    assert all(set(row) == {"label", "sha256"}
               for row in outputs[0]["entries"])


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("confirmation fixed-N synthetic tests: PASS")
