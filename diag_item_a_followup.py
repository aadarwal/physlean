#!/usr/bin/env python3
"""Item-A follow-up falsifier (after diagnostic 19903226 hard-stopped:
oracle/cache_position/repeat PASSED, all 12 bf16 chunk-vs-prod pairs
FAILED). Converged design (PREREG §13): the same-shape bf16
eager-vs-SDPA gate was REJECTED as non-identifying (a backend swap
perturbs every layer's arithmetic order, so its failure cannot separate
a mask defect from legitimate bf16 divergence). Two gates remain, each
identifying:

  F2  q25c, 8192 tokens, FLOAT32 with TF32 OFF (matmul + cudnn;
      float32_matmul_precision='highest'; all asserted AND recorded),
      model attention implementation GATED == 'sdpa', torch SDP
      backend EXPLICITLY FORCED to MATH around every F2 forward
      (audit fix: "fp32 defaults to math" is not guaranteed from
      config — force it and say so), production eval_window chunk 512
      vs 2048: mean|Δ| < 1e-4 AND p99 < 1e-3 (pre-incident oracle
      bounds); repeat-2048 max <= 1e-6. SCOPE, stated plainly: F2 is
      evidence about the SHARED cache/model/mask-construction
      semantics — it does NOT validate bf16 flash-kernel arithmetic.
  CAUSALITY  q25c, bf16, DEFAULT production path, chunk 2048: perturb
      the input token at p=4095 (last position of chunk 2); NLL rows
      0..4093 (targets and logit positions all before p) must be
      unchanged (max <= 1e-6 — the verified determinism bound; correct
      causal masking makes past logits EXACTLY independent of future
      tokens, any dtype, any kernel); row 4094 is EXCLUDED (its target
      token changed); at least one downstream row (>= 4095) must change
      (> 1e-6) or the probe is vacuous. This is the threshold-free mask
      test on the exact bf16 production kernel that F2 cannot reach.

Branches (frozen): F2 fail -> shared chunked-prefill semantic defect
(fix code, hard stop); causality fail -> production-kernel mask defect
(fix code, hard stop); both pass -> the targeted semantic-bug probes
pass and the observed divergence is CONSISTENT with accumulated-KV
bf16 numerics -> re-specification branch (chunked-vs-chunked item A at
unified CHUNK_TOKENS=2048; chunk joins cell_done identity; --big gains
131k/32B chunk-2048 probes). Per-position signed/abs profiles are
persisted; completeness/finiteness gated; identity refusal + completion
re-check; quarantined JSON. No F1 backend gate; characterization
omitted for speed."""
import json, math, os, sys, time

import torch

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

F2_FAM = "q25c"
F2_CTX = 8192
F2_CHUNK_A = 512
F2_CHUNK_B = 2048            # production chunk
F2_MEAN = 1e-4               # pre-incident oracle bounds (not derived
F2_P99 = 1e-3                # from any observed CUDA value)
F2_REPEAT_MAX = 1e-6
CAUSAL_P = 4095              # last input position of chunk 2 (chunks
CAUSAL_CHUNK = 2048          # [0,2048),[2048,4096): most exposed rows
CAUSAL_MAX = 1e-6            # = the verified determinism bound
OUT_DIR = os.path.join(BASE, "results_v2", "diag")
OUT_JSON = os.path.join(OUT_DIR, "item_a_followup.json")


def perturb_ids(ids, p, vocab):
    """PURE (testable): deterministic single-token perturbation at
    position p — (id+1) mod vocab is always a different valid id for
    vocab >= 2; every other position is untouched."""
    assert vocab >= 2 and 0 <= p < len(ids)
    new = list(ids)
    new[p] = (ids[p] + 1) % vocab
    assert new[p] != ids[p]
    return new


def causality_partition(n_rows, p):
    """PURE (testable): NLL row j is computed from logits at input
    position j and scores target ids[j+1]. With input token p perturbed:
    PROTECTED rows 0..p-2 (logit position < p AND target index < p —
    nothing they depend on changed; causal masking makes them exactly
    invariant); EXCLUDED row p-1 (logit position p-1 < p is clean, but
    its TARGET ids[p] changed — its NLL moves for scoring reasons, not
    leakage); DOWNSTREAM rows p..n-1 (logit positions >= p see the
    perturbed token; expected to change)."""
    assert 1 <= p < n_rows
    return (list(range(0, p - 1)), [p - 1], list(range(p, n_rows)))


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def followup_verdict(f2_stats, f2_repeat_max, causal_protected_max,
                     causal_downstream_max, f2_attn, causal_attn):
    """PURE frozen decision rule (PREREG §13). NaN/None/missing fails.
    Bounds: F2 strict '<' (equivalence claim); repeat and causality
    protected inclusive '<=' (determinism bound); non-vacuity strict
    '>' (a downstream row must demonstrably change). BOTH models'
    RESOLVED attention implementation must be exactly 'sdpa' (audit
    fix: a silent eager fallback would falsely close the
    production-kernel question)."""
    fails = []
    if f2_attn != "sdpa":
        fails.append(f"f2-attn-impl:{f2_attn}")
    if causal_attn != "sdpa":
        fails.append(f"causality-attn-impl:{causal_attn}")
    if (f2_stats is None
            or not (_finite(f2_stats.get("mean_abs"))
                    and _finite(f2_stats.get("p99"))
                    and f2_stats["mean_abs"] < F2_MEAN
                    and f2_stats["p99"] < F2_P99)):
        fails.append("f2-semantic")
    if not (_finite(f2_repeat_max) and f2_repeat_max <= F2_REPEAT_MAX):
        fails.append("f2-repeat")
    if not (_finite(causal_protected_max)
            and causal_protected_max <= CAUSAL_MAX):
        fails.append("causality-mask")
    if not (_finite(causal_downstream_max)
            and causal_downstream_max > CAUSAL_MAX):
        fails.append("causality-vacuous")
    return (not fails, fails)


def delta_stats(a, b):
    d = (a.double() - b.double())
    ad = d.abs()
    return dict(n=int(d.numel()),
                mean_signed=float(d.mean()),
                mean_abs=float(ad.mean()),
                p50=float(ad.quantile(0.50)),
                p90=float(ad.quantile(0.90)),
                p99=float(ad.quantile(0.99)),
                max=float(ad.max()))


def signed_profile(a, b):
    """Per-position SIGNED delta, persisted in full (the fingerprint the
    first diagnostic aggregated away)."""
    return [round(float(x), 8) for x in (a.double() - b.double())]


def abs_profile(a, b):
    """Per-position absolute delta, persisted alongside signed delta."""
    return [round(float(x), 8)
            for x in (a.double() - b.double()).abs()]


def tf32_snapshot():
    return dict(
        matmul_allow_tf32=bool(torch.backends.cuda.matmul.allow_tf32),
        cudnn_allow_tf32=bool(torch.backends.cudnn.allow_tf32),
        float32_matmul_precision=torch.get_float32_matmul_precision())


def resolved_attn(model):
    """The implementation transformers ACTUALLY resolved for this model
    (recorded and GATED == 'sdpa': a silent eager fallback would test
    the wrong path and falsely close the production-kernel question)."""
    cfg = model.config
    tcfg = (cfg.get_text_config() if hasattr(cfg, "get_text_config")
            else cfg)
    return (getattr(cfg, "_attn_implementation", None)
            or getattr(tcfg, "_attn_implementation", None))


def load_fp32_default(mid, rev):
    """fp32 load under the DEFAULT attention implementation (no override
    — resolved impl is GATED == 'sdpa' post-load); the torch-level SDP
    backend is separately FORCED to MATH around the F2 forwards:
    shared-semantics evidence only."""
    from transformers import AutoModelForCausalLM
    try:
        m = AutoModelForCausalLM.from_pretrained(
            mid, revision=rev, dtype=torch.float32, local_files_only=True)
    except TypeError:
        m = AutoModelForCausalLM.from_pretrained(
            mid, revision=rev, torch_dtype=torch.float32,
            local_files_only=True)
    m.eval()
    for p in m.parameters():
        p.requires_grad_(False)
    return m


def main():
    from eval_incontext import eval_window          # PRODUCTION scorer
    from validity_battery import (FAM_SMALL, load_text_model, rev_of,
                                  tok_of)
    from provenance import (env_fingerprint, env_matches_freeze,
                            env_matches_lock, gpu_info, harness_hash,
                            source_clean, source_tree_hash)
    if not torch.cuda.is_available():
        raise SystemExit("FATAL: follow-up requires CUDA")
    device = "cuda"
    lock_ok, lock_probs = env_matches_lock()
    frz_ok, frz_detail = env_matches_freeze()
    if not (lock_ok and frz_ok):
        raise SystemExit(f"FATAL: environment mismatch — lock: "
                         f"{lock_probs[:4] or 'ok'}; freeze: {frz_detail}")
    if not source_clean():
        raise SystemExit("FATAL: dirty source tree")
    ident_start = dict(harness=harness_hash(), env=env_fingerprint(),
                       src=source_tree_hash())

    # TF32 OFF for the whole run — asserted, not just requested (F2's
    # fp32 equivalence claim is meaningless if TF32 silently re-tiles)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    tf = tf32_snapshot()
    assert (tf["matmul_allow_tf32"] is False
            and tf["cudnn_allow_tf32"] is False
            and tf["float32_matmul_precision"] == "highest"), tf

    mid = FAM_SMALL[F2_FAM]
    tk = tok_of(mid)
    text = open(os.path.join(BASE, "data/streams/mathlib/full_topo.txt"),
                encoding="utf-8").read()[:200_000]
    ids_list = tk(text, add_special_tokens=False)["input_ids"][:F2_CTX]
    if len(ids_list) != F2_CTX:   # fail-closed: a short window would
        raise SystemExit(          # silently weaken every gate
            f"FATAL: only {len(ids_list)} tokens available; "
            f"the frozen design requires exactly {F2_CTX}")
    ids = torch.tensor(ids_list, dtype=torch.long)
    res = dict(schema="item_a_followup_v1",
               harness_hash=ident_start["harness"],
               env_fingerprint=ident_start["env"],
               source_tree_hash=ident_start["src"],
               **gpu_info(),
               torch_version=torch.__version__,
               tf32=tf32_snapshot(),
               gates=dict(f2_mean=F2_MEAN, f2_p99=F2_P99,
                          f2_repeat_max=F2_REPEAT_MAX,
                          causal_p=CAUSAL_P, causal_max=CAUSAL_MAX,
                          attn_required="sdpa"),
               scope_note=("F2 (fp32, default SDPA -> math backend) is "
                           "evidence about SHARED cache/model/mask-"
                           "construction semantics, NOT bf16 flash "
                           "arithmetic; the causality probe covers the "
                           "bf16 production kernel's masking."))
    t0 = time.time()

    # ---- F2: fp32, TF32 off, default dispatch, chunk 512 vs 2048 ----
    m32 = load_fp32_default(mid, rev_of(mid)).to(device)
    f2_attn = resolved_attn(m32)
    # torch-level SDP backend FORCED to MATH for every F2 forward
    # (audit fix: never rely on implicit fp32 dispatch)
    from torch.nn.attention import SDPBackend, sdpa_kernel
    with sdpa_kernel([SDPBackend.MATH]):
        a = eval_window(m32, ids, device, F2_CHUNK_A)
        b = eval_window(m32, ids, device, F2_CHUNK_B)
        b2 = eval_window(m32, ids, device, F2_CHUNK_B)
    f2_repeat_max = float((b - b2).abs().max())
    f2 = delta_stats(a, b)
    res["f2"] = dict(model=mid, revision=rev_of(mid), dtype="float32",
                     attn_resolved=f2_attn,
                     sdp_backend_forced="MATH",
                     attn_note="model impl gated sdpa; torch SDP "
                               "backend forced to MATH: shared "
                               "cache/model semantics evidence only",
                     tokens=F2_CTX,
                     chunks=[F2_CHUNK_A, F2_CHUNK_B], stats=f2,
                     repeat_max_abs=f2_repeat_max,
                     signed_profile=signed_profile(a, b),
                     abs_profile=abs_profile(a, b))
    del m32
    torch.cuda.empty_cache()
    print(f"[f2] done {time.time()-t0:.0f}s", flush=True)

    # ---- CAUSALITY: bf16, production path, chunk 2048, perturb 4095 --
    model, cls, nparams, attn = load_text_model(mid, device)
    causal_attn = resolved_attn(model)
    orig = eval_window(model, ids, device, CAUSAL_CHUNK)
    # vocab from the TEXT CONFIG, not tokenizer length (audit fix:
    # added tokens can exceed the embedding table; (id+1) % config
    # vocab_size is always a valid embedding row)
    cfg = model.config
    tcfg = (cfg.get_text_config() if hasattr(cfg, "get_text_config")
            else cfg)
    vocab = int(tcfg.vocab_size)
    pert_list = perturb_ids(ids_list, CAUSAL_P, vocab)
    pert = eval_window(model, torch.tensor(pert_list, dtype=torch.long),
                       device, CAUSAL_CHUNK)
    d = (orig.double() - pert.double()).abs()
    protected, excluded, downstream = causality_partition(len(orig),
                                                          CAUSAL_P)
    causal_protected_max = float(d[protected].max())
    causal_downstream_max = float(d[downstream].max())
    res["causality"] = dict(
        model=mid, revision=rev_of(mid), cls=cls, n_params=nparams,
        attn=attn, attn_resolved=causal_attn,
        dtype="bfloat16", chunk=CAUSAL_CHUNK, p=CAUSAL_P,
        vocab=vocab, orig_token=ids_list[CAUSAL_P],
        perturbed_token=pert_list[CAUSAL_P],
        n_protected=len(protected), n_downstream=len(downstream),
        protected_max_abs=causal_protected_max,
        excluded_row_delta=float(d[excluded[0]]),
        downstream_max_abs=causal_downstream_max,
        signed_profile=signed_profile(orig, pert),
        abs_profile=abs_profile(orig, pert))
    del model
    torch.cuda.empty_cache()
    print(f"[causality] done {time.time()-t0:.0f}s", flush=True)

    ok, fails = followup_verdict(f2, f2_repeat_max, causal_protected_max,
                                 causal_downstream_max, f2_attn,
                                 causal_attn)
    if ok:
        meaning = ("BOTH targeted semantic-bug probes pass; the observed "
                   "divergence is consistent with accumulated-KV bf16 "
                   "numerics; re-specification branch is permitted "
                   "(unified chunk, chunk into cell identity — PREREG §13)")
    elif any("-attn-impl" in f for f in fails):
        meaning = ("INVALID RUN: attention dispatch was not sdpa — no "
                   "scientific conclusion; fix model loading and rerun "
                   "the follow-up")
    elif any(f in ("f2-semantic", "f2-repeat") for f in fails):
        meaning = ("HARD STOP: shared chunked-prefill semantic defect "
                   "(f2) — fix code, no gate discussion")
    else:
        meaning = ("HARD STOP: production-kernel mask defect / vacuous "
                   "probe (causality) — fix code, no gate discussion")
    res["verdict"] = dict(ok=ok, failures=fails, meaning=meaning)

    drift = [k for k, v in (("harness", harness_hash()),
                            ("env", env_fingerprint()),
                            ("src", source_tree_hash()))
             if ident_start[k] != v]
    if drift or not source_clean():
        raise SystemExit(f"FATAL: {drift or ['source_clean']} changed "
                         "DURING the follow-up — JSON not written")
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT_JSON):   # evidence is never overwritten
        ts = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        os.rename(OUT_JSON, f"{OUT_JSON}.quarantine-{ts}")
        print(f"[followup] prior JSON -> quarantine-{ts}", flush=True)
    with open(OUT_JSON, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res["verdict"], indent=1), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
