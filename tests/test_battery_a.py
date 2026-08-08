#!/usr/bin/env python3
"""A_fixed_chunk_semantics pure-logic regression tests (GPU-free):
frozen constants, and the fail-closed verdict across every failure
class — coverage, class/param, chunk identity, token count, repeat,
causality, dispatch, TF32, semantic bounds, chunk pair (PREREG §7/§13).
Run: python3 tests/test_battery_a.py"""
import inspect, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from layout import PRODUCTION_CHUNK_TOKENS
from validity_battery import (A_CAUSAL_MAX, A_CAUSAL_P, A_CTX, A_F2_CHUNK,
                              A_F2_MEAN, A_F2_P99, A_REPEAT_MAX, FAM_SMALL,
                              a_fixed_chunk_verdict, chunked_nll)

FAMS = tuple(FAM_SMALL)
CHUNK = PRODUCTION_CHUNK_TOKENS


def test_frozen_constants():
    """Gate constants are the §7/§13 frozen values; the production chunk
    is the layout constant (single source, shared with cell_done); the
    causality bound equals the determinism bound; F2 reuses the
    pre-incident oracle bounds."""
    assert CHUNK == 2048
    assert A_CTX == 8192
    assert A_REPEAT_MAX == A_CAUSAL_MAX == 1e-6
    assert (A_F2_MEAN, A_F2_P99, A_F2_CHUNK) == (1e-4, 1e-3, 512)
    assert A_CAUSAL_P == 4095 == 2 * CHUNK - 1  # last position, chunk 2
    assert inspect.signature(chunked_nll).parameters["chunk"].default == CHUNK
    assert "return eval_window(model, ids_t, device, chunk)" in \
        inspect.getsource(chunked_nll)


def _fam_ok():
    return dict(class_ok=True, param_sane=True, dtype="bfloat16",
                attn_resolved="sdpa", chunk=CHUNK,
                n_tokens=A_CTX, repeat_max_abs=0.0, mean_nll=2.0,
                causal=dict(p=A_CAUSAL_P,
                            vocab=100, orig_token=42, perturbed_token=43,
                            n_protected=A_CAUSAL_P - 1,
                            n_downstream=A_CTX - 1 - A_CAUSAL_P,
                            excluded_row_delta=1.0,
                            protected_max_abs=0.0,
                            downstream_max_abs=5.0))


def _f2_ok():
    return dict(dtype="float32", attn_resolved="sdpa",
                sdp_backend_forced="MATH", tokens=A_CTX,
                tf32=dict(matmul_allow_tf32=False, cudnn_allow_tf32=False,
                          float32_matmul_precision="highest"),
                stats=dict(n=A_CTX - 1, mean_signed=0.0,
                           mean_abs=3.2e-6, p50=1e-6, p90=1e-5,
                           p99=2.4e-5, max=6.5e-5),
                repeat_max_abs=0.0, chunks=[A_F2_CHUNK, CHUNK])


def _block():
    return dict(families={f: _fam_ok() for f in FAMS}, f2=_f2_ok(),
                production_chunk=CHUNK)


def test_all_pass():
    ok, fails = a_fixed_chunk_verdict(_block(), FAMS, CHUNK)
    assert ok and fails == []


def test_family_coverage_fails_closed():
    """Missing family, extra family, and empty families all fail —
    partial evidence can never pass by omission."""
    b = _block()
    del b["families"]["q35"]
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert not ok and any(f.startswith("family-coverage") for f in fails)
    b = _block()
    b["families"]["rogue"] = _fam_ok()
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert not ok and any(f.startswith("family-coverage") for f in fails)
    ok, fails = a_fixed_chunk_verdict(dict(families={}, f2=_f2_ok()),
                                      FAMS, CHUNK)
    assert not ok
    ok, fails = a_fixed_chunk_verdict({}, FAMS, CHUNK)
    assert not ok


def test_per_family_gates_trip():
    """Each per-family gate independently; NaN/None/missing fail; the
    repeat and causality-protected bounds are inclusive, non-vacuity is
    strict."""
    for mut, tag in (
            (lambda f: f.update(class_ok=False), "class-param"),
            (lambda f: f.update(param_sane=None), "class-param"),
            (lambda f: f.update(chunk=1024), "chunk"),
            (lambda f: f.update(chunk=None), "chunk"),
            (lambda f: f.update(dtype="float32"), "dtype"),
            (lambda f: f.update(attn_resolved=None), "attn-record"),
            (lambda f: f.update(attn_resolved=""), "attn-record"),
            (lambda f: f.update(n_tokens=8191), "tokens"),
            (lambda f: f.update(mean_nll=float("nan")), "mean-nll"),
            (lambda f: f.update(mean_nll=-1.0), "mean-nll"),
            (lambda f: f.update(repeat_max_abs=2e-6), "repeat"),
            (lambda f: f.update(repeat_max_abs=-1.0), "repeat"),
            (lambda f: f.update(repeat_max_abs=float("nan")), "repeat"),
            (lambda f: f.update(repeat_max_abs=None), "repeat"),
            (lambda f: f["causal"].update(protected_max_abs=2e-6),
             "causality-mask"),
            (lambda f: f["causal"].update(protected_max_abs=float("nan")),
             "causality-mask"),
            (lambda f: f.update(causal={}), "causality-mask"),
            (lambda f: f["causal"].update(n_protected=0),
             "causality-coverage"),
            (lambda f: f["causal"].update(perturbed_token=42),
             "causality-coverage"),
            (lambda f: f["causal"].update(vocab=1),
             "causality-coverage"),
            (lambda f: f["causal"].update(excluded_row_delta=float("nan")),
             "causality-coverage"),
            (lambda f: f["causal"].update(excluded_row_delta=-1.0),
             "causality-coverage"),
            (lambda f: f["causal"].update(downstream_max_abs=A_CAUSAL_MAX),
             "causality-vacuous"),
            (lambda f: f["causal"].update(
                downstream_max_abs=float("inf")), "causality-vacuous")):
        b = _block()
        mut(b["families"]["q3"])
        ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
        assert not ok and f"{tag}:q3" in fails, (tag, fails)
    # inclusive boundaries pass exactly at the determinism bound
    b = _block()
    b["families"]["q3"]["repeat_max_abs"] = A_REPEAT_MAX
    b["families"]["q3"]["causal"]["protected_max_abs"] = A_CAUSAL_MAX
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert ok and fails == []


def test_f2_gates_trip():
    """Dispatch, TF32 state, semantic bounds (strict '<'), repeat
    (inclusive '<='), and the exact ordered chunk pair."""
    for mut, tag in (
            (lambda f: f.update(dtype="bfloat16"), "f2-dtype"),
            (lambda f: f.update(attn_resolved="eager"), "f2-attn-impl"),
            (lambda f: f.update(attn_resolved=None), "f2-attn-impl"),
            (lambda f: f.update(sdp_backend_forced="FLASH_ATTENTION"),
             "f2-backend"),
            (lambda f: f.update(tokens=A_CTX - 1), "f2-tokens"),
            (lambda f: f["tf32"].update(matmul_allow_tf32=True),
             "f2-tf32"),
            (lambda f: f["tf32"].update(cudnn_allow_tf32=True),
             "f2-tf32"),
            (lambda f: f["tf32"].update(
                float32_matmul_precision="high"), "f2-tf32"),
            (lambda f: f.update(tf32={}), "f2-tf32"),
            (lambda f: f["stats"].update(mean_abs=A_F2_MEAN),
             "f2-semantic"),
            (lambda f: f["stats"].update(p99=A_F2_P99), "f2-semantic"),
            (lambda f: f["stats"].update(mean_abs=float("nan")),
             "f2-semantic"),
            (lambda f: f["stats"].update(n=A_CTX - 2),
             "f2-stats-completeness"),
            (lambda f: f["stats"].update(max=float("inf")),
             "f2-stats-completeness"),
            (lambda f: f["stats"].update(p50=-1.0),
             "f2-stats-completeness"),
            (lambda f: f["stats"].update(p90=1.0),
             "f2-stats-completeness"),
            (lambda f: f.update(stats={}), "f2-semantic"),
            (lambda f: f.update(repeat_max_abs=2e-6), "f2-repeat"),
            (lambda f: f.update(repeat_max_abs=-1.0), "f2-repeat"),
            (lambda f: f.update(repeat_max_abs=None), "f2-repeat"),
            (lambda f: f.update(chunks=[CHUNK, A_F2_CHUNK]),
             "f2-chunks"),
            (lambda f: f.update(chunks=[A_F2_CHUNK, 1024]), "f2-chunks"),
            (lambda f: f.update(chunks=None), "f2-chunks")):
        b = _block()
        mut(b["f2"])
        ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
        assert not ok and any(f.startswith(tag) for f in fails), (tag,
                                                                  fails)
    # missing f2 entirely: multiple f2 gates fail, never a pass
    b = _block()
    del b["f2"]
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert not ok and any(f.startswith("f2-") for f in fails)
    # f2 repeat inclusive at the bound
    b = _block()
    b["f2"]["repeat_max_abs"] = A_REPEAT_MAX
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert ok and fails == []


def test_expected_chunk_is_authoritative():
    """The verdict pins against the PASSED expected chunk — a block
    self-reporting a consistent-but-wrong chunk still fails when the
    layout constant differs (single source of truth)."""
    b = _block()
    for fam in FAMS:
        b["families"][fam]["chunk"] = 1024
    b["f2"]["chunks"] = [A_F2_CHUNK, 1024]
    b["production_chunk"] = 1024
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert not ok
    assert sum(1 for f in fails if f.startswith("chunk:")) == len(FAMS)
    assert any(f.startswith("f2-chunks") for f in fails)
    # The top-level declaration is independently gated; self-consistent
    # family/f2 details cannot excuse a false summary field.
    b = _block()
    b["production_chunk"] = 1024
    ok, fails = a_fixed_chunk_verdict(b, FAMS, CHUNK)
    assert not ok and "production-chunk:1024" in fails


def test_preflight_recomputes_current_verdict():
    """Science preflight must recompute the pure gate from raw A evidence;
    trusting only battery.json's stored verdict would be fail-open."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "preflight_check.py"),
               encoding="utf-8").read()
    assert 'b.get("A_fixed_chunk_semantics", {})' in src
    assert "a_fixed_chunk_verdict(" in src
    assert "a_ok and a_stored_ok" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("BATTERY-A TESTS PASS")
