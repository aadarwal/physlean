#!/usr/bin/env python3
"""Synthetic adversarial tests for the registered confirmation reveal."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from finalize_v2b_nll_confirmation_reveal import (
    ANALYZER_PATH, REVEAL_SCHEMA, REVEAL_STATE, PROGRAM,
    _expected_ledger, analysis_registration, build_reveal_value,
    reconstruct_models, validate_reveal,
)
from prepare_v2b_nll_confirmation_masked import (
    ledger_record, publication_sha256,
)
from v2b_common import V2BError, sha256_file
from v2b_nll_confirmation_crypto import (
    encrypt_delta, family_id,
)

from test_finalize_v2b_nll_confirmation_fixed_n import fixed_fixture
from test_prepare_v2b_nll_confirmation_masked import SALT


_CACHE = None
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYZER_SHA = sha256_file(os.path.join(_ROOT, ANALYZER_PATH))
REVEAL_SHA = sha256_file(os.path.join(_ROOT, PROGRAM))


def _reject(fn, text=None):
    try:
        fn()
        assert False, "accepted invalid confirmation reveal"
    except V2BError as error:
        if text is not None:
            assert text in str(error), str(error)


def _ledger(expected):
    rows = [dict(label=label, bytes=1, sha256=expected[label])
            for label in sorted(expected)]
    return ledger_record(rows, copy.deepcopy(rows))


def _callbacks():
    return dict(
        ancestor_fn=lambda _older, _newer: True,
        current_sha_fn=lambda _path: ANALYZER_SHA,
        commit_sha_fn=lambda _commit, path: (
            ANALYZER_SHA if path == ANALYZER_PATH else REVEAL_SHA))


def reveal_fixture():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    base = fixed_fixture()
    protocol = base["protocol"]
    freeze_record = copy.deepcopy(
        base["provenance_inputs"]["implementation_freeze"])
    freeze = freeze_record["value"]
    freeze_binding = copy.deepcopy(
        base["masked"]["bindings"]["implementation_freeze"])

    masked = copy.deepcopy(base["masked"])
    masked_record = copy.deepcopy(base["masked_record"])
    masked_binding = dict(
        path=masked_record["path"], schema=masked["schema"],
        sha256=masked_record["sha256"])

    fixed = copy.deepcopy(base["fixed"])
    fixed_record = dict(
        path="/synthetic/fixed-n.json", sha256=publication_sha256(fixed),
        value=fixed)
    fixed_binding = dict(
        path=fixed_record["path"], schema=fixed["schema"],
        sha256=fixed_record["sha256"])

    registration = analysis_registration(
        freeze, freeze_binding,
        masked["ancestry"]["scoring_source_commit"], **_callbacks())
    expected = _expected_ledger(
        fixed, fixed_binding, freeze_binding, registration)
    frozen_files = {row["path"]: row for row in freeze["files"]}
    generator = dict(
        program=PROGRAM, program_sha256=frozen_files[PROGRAM]["sha256"],
        source_commit="f" * 40,
        source_tree_hash=freeze["source_tree_hash"])
    called = []

    def replay(*_args, **_kwargs):
        called.append(True)
        return True

    value = build_reveal_value(
        protocol, base["assembly"], base["assembly_binding"],
        base["salt_record"], base["study_record"], base["model_inputs"],
        base["provenance_inputs"], masked_record, fixed_record, freeze_record,
        SALT, _ledger(expected), generator, replay_fn=replay, **_callbacks())
    _CACHE = dict(base)
    _CACHE.update(
        masked=masked, masked_record=masked_record,
        masked_binding=masked_binding, fixed=fixed,
        fixed_record=fixed_record, fixed_binding=fixed_binding,
        freeze=freeze, freeze_record=freeze_record,
        freeze_binding=freeze_binding, registration=registration,
        reveal=value, reveal_ledger=_ledger(expected),
        reveal_generator=generator, replay_called=called)
    return _CACHE


def _validate(data, value):
    return validate_reveal(
        value, data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"], data["fixed"], data["fixed_binding"],
        data["freeze"], data["freeze_binding"], data["salt_record"],
        **_callbacks())


def test_exact_one_shot_shape_mapping_and_padding_filter():
    data = reveal_fixture()
    value = data["reveal"]
    assert value["schema"] == REVEAL_SCHEMA
    assert value["state"] == REVEAL_STATE
    assert value["grid"]["n_ciphertexts"] == 4000
    assert len(value["models"]) == 4
    assert all(len(model["families"]) == 5 for model in value["models"])
    assert data["replay_called"] == [True]
    e1b = next(row for row in value["models"][0]["families"]
               if row["contrast_id"] == "E1b")
    assert e1b["rows"][0]["padding_filtered"] is True
    assert e1b["rows"][0]["delta_bpb"] is None
    assert e1b["rows"][1]["padding_filtered"] is False
    assert _validate(data, value) == value


def test_eligible_numeric_zero_is_observed_not_padding():
    data = reveal_fixture()
    masked = copy.deepcopy(data["masked"])
    model_id, contrast_id = "q25c-1.5b", "E1b"
    model = next(row for row in masked["models"]
                 if row["model_id"] == model_id)
    family = next(row for row in model["families"]
                  if row["family_id"] == family_id(
                      SALT, model_id, contrast_id))
    key = family["rows"][1]["target_key"]
    family["rows"][1]["ciphertext"] = encrypt_delta(
        SALT, model_id, contrast_id, key, 0.0)
    models, _n_observed, _n_padding = reconstruct_models(
        masked, data["assembly"], SALT)
    revealed = next(row for row in models[1]["families"]
                    if row["contrast_id"] == contrast_id)["rows"][1]
    assert revealed["delta_bpb"] == 0.0
    assert revealed["padding_filtered"] is False


def test_wrong_padding_ciphertext_and_ciphertext_tamper_fail_closed():
    data = reveal_fixture()
    masked = copy.deepcopy(data["masked"])
    model_id, contrast_id = "q25c-0.5b", "E1b"
    model = masked["models"][0]
    family = next(row for row in model["families"]
                  if row["family_id"] == family_id(
                      SALT, model_id, contrast_id))
    key = family["rows"][0]["target_key"]
    family["rows"][0]["ciphertext"] = encrypt_delta(
        SALT, model_id, contrast_id, key, 1.0)
    _reject(lambda: reconstruct_models(masked, data["assembly"], SALT),
            "does not replay")

    bad = copy.deepcopy(data["reveal"])
    bad["revealed_salt_hex"] = ("00" * 32)
    _reject(lambda: _validate(data, bad), "commitment")
    wrong_commitment = copy.deepcopy(data["salt_record"])
    wrong_commitment["value"]["salt_sha256"] = "0" * 64
    wrong_commitment["sha256"] = publication_sha256(
        wrong_commitment["value"])
    _reject(lambda: validate_reveal(
        data["reveal"], data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"], data["fixed"], data["fixed_binding"],
        data["freeze"], data["freeze_binding"], wrong_commitment,
        **_callbacks()), "commitment")


def test_fixed_n_registration_and_ancestry_are_mandatory():
    data = reveal_fixture()
    bad = copy.deepcopy(data["reveal"])
    bad["bindings"].pop("fixed_n")
    _reject(lambda: _validate(data, bad), "binding")
    bad = copy.deepcopy(data["reveal"])
    bad["analysis_registration"][
        "analyzer_commit_is_ancestor_of_scoring"] = False
    _reject(lambda: _validate(data, bad), "registration")
    _reject(lambda: validate_reveal(
        data["reveal"], data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"], data["fixed"], data["fixed_binding"],
        data["freeze"], data["freeze_binding"], data["salt_record"],
        ancestor_fn=lambda _a, _b: False,
        current_sha_fn=lambda _p: ANALYZER_SHA,
        commit_sha_fn=lambda _c, path: (
            ANALYZER_SHA if path == ANALYZER_PATH else REVEAL_SHA)),
        "ancestry")
    _reject(lambda: validate_reveal(
        data["reveal"], data["protocol"], data["assembly"], data["masked"],
        data["masked_binding"], data["fixed"], data["fixed_binding"],
        data["freeze"], data["freeze_binding"], data["salt_record"],
        ancestor_fn=lambda _a, _b: True,
        current_sha_fn=lambda _p: ANALYZER_SHA,
        commit_sha_fn=lambda _c, path: (
            ANALYZER_SHA if path == ANALYZER_PATH else "0" * 64)),
        "execution-commit source")


def test_reveal_ledger_toctou_and_determinism():
    data = reveal_fixture()
    pre = [dict(label="x", bytes=1, sha256="1" * 64)]
    post = [dict(label="x", bytes=2, sha256="1" * 64)]
    _reject(lambda: ledger_record(pre, post), "changed during")
    again = build_reveal_value(
        data["protocol"], data["assembly"], data["assembly_binding"],
        data["salt_record"], data["study_record"], data["model_inputs"],
        data["provenance_inputs"], data["masked_record"],
        data["fixed_record"], data["freeze_record"], SALT,
        data["reveal_ledger"], data["reveal_generator"],
        replay_fn=lambda *_args, **_kwargs: True, **_callbacks())
    assert again == data["reveal"]


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"[ok] {name}")
    print("confirmation reveal synthetic tests: PASS")
