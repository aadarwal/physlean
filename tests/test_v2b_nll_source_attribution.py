#!/usr/bin/env python3
"""Atomic model-group attribution, conservation, and adversarial drift."""
import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v2b_common import V2BError, sha256_json
from v2b_nll_source_attribution import (MODEL_GROUP_CLASSES,
                                        attribute_cell,
                                        source_class_contrasts,
                                        validate_cross_cell_layout)
from v2b_source_tokens import python_source_spans


def _raw(index, token_id, start, end, nll, inclusion="primary"):
    return dict(token_index=index, token_id=token_id,
                start_char_relative_to_body=start,
                end_char_relative_to_body=end,
                nll_nats=float(nll), inclusion=inclusion)


def _cell(body, rows, q=0, cell_id="k4:16384"):
    prefix = [0]
    for ch in body:
        prefix.append(prefix[-1] + len(ch.encode("utf-8")))
    primary = [row for row in rows if row["inclusion"] == "primary"]
    boundary = [row for row in rows
                if row["inclusion"] == "boundary-sensitivity-only"]
    nll = math.fsum(row["nll_nats"] for row in primary)
    scored = prefix[-1] - prefix[q]
    projection = [[row["token_id"],
                   row["start_char_relative_to_body"],
                   row["end_char_relative_to_body"], row["inclusion"]]
                  for row in rows]
    return dict(
        cell_id=cell_id, arm=cell_id.split(":", 1)[0],
        budget_bytes=16384, seed=None, estimand_role="test",
        eligible=True, cell_manifest_sha256="a" * 64,
        body_layout_signature=sha256_json(projection),
        boundary_ledger=dict(
            exact_body_codepoints=len(body), exact_body_bytes=prefix[-1],
            scored_body_codepoints=len(body) - q,
            scored_body_bytes=scored,
            straddled_body_codepoints=q,
            straddled_body_bytes=prefix[q],
            n_boundary_straddle_tokens=len(boundary),
            primary_token_indices=[row["token_index"] for row in primary],
            boundary_token_indices=[row["token_index"] for row in boundary],
            inclusive_token_indices=(
                [row["token_index"] for row in boundary] +
                [row["token_index"] for row in primary])),
        primary=dict(nll_nats=nll,
                     bpb=nll / math.log(2) / scored),
        raw_body_token_rows=rows)


def _assert_additive(result):
    reconstructed_nll = math.fsum(result["class_nll_nats"].values()) + \
        result["roundoff_residual_nats"]
    assert reconstructed_nll == result["primary_nll_nats"]
    reconstructed_bpb = math.fsum(
        result["class_contribution_bpb"].values()) + \
        result["roundoff_residual_bpb"]
    assert math.isclose(reconstructed_bpb, result["primary_bpb"],
                        rel_tol=0, abs_tol=2e-15)
    sensitivity = result["byte_overlap_apportionment_sensitivity"]
    assert math.isclose(
        math.fsum(sensitivity["class_nll_nats"].values()) +
        sensitivity["roundoff_residual_nats"],
        result["primary_nll_nats"], rel_tol=0, abs_tol=2e-15)


def test_carrier_layout_does_not_turn_unique_core_groups_mixed():
    body = "  foo + 1 # c\n"
    rows = [_raw(1, 10, 2, 5, .1), _raw(2, 11, 6, 7, .2),
            _raw(3, 12, 8, 9, .3), _raw(4, 13, 10, 13, .4),
            _raw(5, 14, 13, 14, .5)]
    out = attribute_cell(body, _cell(body, rows),
                         python_source_spans(body))
    assert [row["model_group_class"] for row in out["model_groups"]] == \
        ["word", "symbol", "literal", "comment_layout", "layout"]
    assert out["model_groups"][0]["source_overlap_bytes"]["layout"] == 2
    assert out["model_groups"][0]["source_overlap_bytes"]["word"] == 3
    assert set(out["class_nll_nats"]) == set(MODEL_GROUP_CLASSES)
    _assert_additive(out)


def test_overlapping_core_tokens_stay_one_mixed_group():
    body = "foo+"
    rows = [_raw(1, 10, 0, 3, .2), _raw(2, 11, 2, 4, .4)]
    out = attribute_cell(body, _cell(body, rows),
                         python_source_spans(body))
    assert out["n_model_groups"] == 1
    group = out["model_groups"][0]
    assert group["model_group_class"] == "mixed_core"
    assert group["token_indices"] == [1, 2]
    assert group["source_overlap_bytes"]["word"] == 3
    assert group["source_overlap_bytes"]["symbol"] == 1
    _assert_additive(out)


def test_boundary_group_is_excluded_exactly_and_unicode_bytes_conserve():
    body = "λx"
    rows = [_raw(1, 20, -2, 1, .8, "boundary-sensitivity-only"),
            _raw(2, 21, 1, 2, .3)]
    out = attribute_cell(body, _cell(body, rows, q=1),
                         python_source_spans(body))
    assert out["boundary_excluded_bytes"] == 2
    assert out["scored_body_bytes"] == 1
    assert out["primary_nll_nats"] == .3
    assert out["model_groups"][0]["start_byte"] == 2
    _assert_additive(out)


def test_cross_cell_allows_nll_change_but_not_layout_change():
    body = "foo+"
    rows = [_raw(1, 10, 0, 3, .2), _raw(2, 11, 2, 4, .4)]
    first = attribute_cell(body, _cell(body, rows, cell_id="k1"),
                           python_source_spans(body))
    changed = copy.deepcopy(rows)
    changed[0]["nll_nats"] = 1.7
    second = attribute_cell(body, _cell(body, changed, cell_id="k4:16384"),
                            python_source_spans(body))
    assert validate_cross_cell_layout([first, second]) == \
        first["model_group_layout_sha256"]
    different = copy.deepcopy(second)
    different["model_group_layout_sha256"] = "0" * 64
    try:
        validate_cross_cell_layout([first, different])
        assert False, "arm-specific tokenizer/source layout accepted"
    except V2BError as err:
        assert "arm-specific" in str(err)


def test_tampered_raw_metadata_and_primary_summary_fail_closed():
    body = "foo+"
    rows = [_raw(1, 10, 0, 3, .2), _raw(2, 11, 2, 4, .4)]
    source = python_source_spans(body)
    for mutate, message in (
            (lambda cell: cell.update(body_layout_signature="0" * 64),
             "signature"),
            (lambda cell: cell["raw_body_token_rows"].reverse(), "sorted"),
            (lambda cell: cell["primary"].update(nll_nats=9.0), "raw"),
            (lambda cell: cell["boundary_ledger"].update(
                scored_body_bytes=99), "conserve")):
        cell = _cell(body, copy.deepcopy(rows))
        mutate(cell)
        try:
            attribute_cell(body, cell, source)
            assert False, f"tampering accepted: {message}"
        except V2BError:
            pass


def test_source_ledger_character_byte_map_is_revalidated():
    body = "λ + 1"
    rows = [_raw(1, 1, 0, 1, .1), _raw(2, 2, 2, 3, .2),
            _raw(3, 3, 4, 5, .3)]
    source = python_source_spans(body)
    source["char_to_byte_prefix"][1] = 1
    try:
        attribute_cell(body, _cell(body, rows), source)
        assert False, "tampered UTF-8 boundary map accepted"
    except V2BError as err:
        assert "character/byte" in str(err)


def _attributed_target(key, scale=1.0, e1b_eligible=True):
    body = "foo+"
    source = python_source_spans(body)
    specs = (("k1", .9), ("k3:16384", .7), ("k4:16384", .4),
             ("k5:0:16384", .6))
    cells = []
    for cell_id, base in specs:
        rows = [_raw(1, 10, 0, 3, base * scale * .4),
                _raw(2, 11, 2, 4, base * scale * .6)]
        value = attribute_cell(body, _cell(body, rows, cell_id=cell_id),
                               source)
        if cell_id == "k3:16384" and not e1b_eligible:
            value["eligible"] = False
        cells.append(value)
    return dict(target_key=key, cells=cells)


def test_additive_target_and_repo_source_contrasts():
    result = source_class_contrasts([
        _attributed_target("a", 1.0), _attributed_target("b", 1.7)])
    assert result["ast_node_attribution"] is False
    assert result["effect_shares_computed"] is False
    assert result["denominator"] == "total-scored-body-bytes"
    for name in ("E1a", "E1b", "E2"):
        contrast = result["contrasts"][name]
        assert contrast["n_targets"] == 2
        components = contrast[
            "target_equal_mean_source_class_delta_bpb"]
        rebuilt = math.fsum(components.values()) + contrast[
            "target_equal_mean_cell_roundoff_delta_bpb"] + contrast[
            "target_equal_mean_reconstruction_residual_bpb"]
        assert math.isclose(
            rebuilt, contrast["target_equal_mean_total_delta_bpb"],
            rel_tol=0, abs_tol=2e-15)
        for row in contrast["rows"]:
            row_total = math.fsum(row["source_class_delta_bpb"].values()) + \
                row["cell_roundoff_delta_bpb"] + \
                row["contrast_reconstruction_residual_bpb"]
            assert math.isclose(row_total, row["total_delta_bpb"],
                                rel_tol=0, abs_tol=2e-15)


def test_contrast_complete_case_is_shared_across_all_classes():
    result = source_class_contrasts([
        _attributed_target("a", e1b_eligible=False),
        _attributed_target("b")])
    assert result["contrasts"]["E1a"]["n_targets"] == 2
    assert result["contrasts"]["E1b"]["n_targets"] == 1
    assert result["contrasts"]["E2"]["n_targets"] == 2
    assert set(result["contrasts"]["E1b"]["rows"][0][
        "source_class_delta_bpb"]) == set(MODEL_GROUP_CLASSES)


def test_source_contrast_missing_required_cell_fails_closed():
    target = _attributed_target("a")
    target["cells"] = target["cells"][:-1]
    try:
        source_class_contrasts([target])
        assert False, "missing contrast cell accepted"
    except V2BError as err:
        assert "missing" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B NLL SOURCE ATTRIBUTION TESTS PASS")
