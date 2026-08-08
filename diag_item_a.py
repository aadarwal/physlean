#!/usr/bin/env python3
"""Item-A incident diagnostic (battery 19902567; PREREG §13 frozen
decision rule). One question, decided before any grid outcome exists:
is the chunked-vs-one-shot NLL divergence a CACHE/POSITION BUG (dtype-
independent, boundary-localized — measurement invalid, hard stop) or
BF16 KERNEL-SHAPE NUMERICS (collapses in fp32, diffuse in position —
production chunked path internally stable; item A may be re-specified)?

Frozen design (all bounds set BEFORE running):
  1. PRODUCTION STABILITY, all 4 battery families at 8192 tokens using
     the PRODUCTION eval_window: bf16 chunks {512,1024,4096} each vs
     prod chunk 2048 must meet the ORIGINAL item-A bounds
     (mean|Δ| < 5e-3, p99 < 5e-2); a repeat of 2048 must be
     deterministic (max|Δ| <= 1e-6).
  2. q25c fp32 EAGER ORACLE at 2048 tokens: one-shot vs chunk-512 must
     meet mean < 1e-4, p99 < 1e-3; implicit vs EXPLICIT cache_position
     max|Δ| <= 1e-6.
  3. bf16 one-shot (8192) vs prod-2048 is CHARACTERIZATION ONLY —
     reported, never gated (production never executes that kernel
     shape).
Decision: ANY production-stability or oracle failure hard-stops
(exit 1); only if ALL gates pass may item A be re-specified before the
battery rerun. Reports per-pair signed/abs delta quantiles, strata
(first-chunk / boundary / interior relative to the prod chunking), and
argmax agreement (only argmax IDs are retained; logits are not written).
Identity: refuses without the current lock/freeze/clean-source state;
writes results_v2/diag/item_a_diag.json (quarantine-on-rerun)."""
import json, math, os, sys, time

import torch

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

CTX = 8192
CHUNK_PROD = 2048
CHUNKS_ALT = (512, 1024, 4096)
GATE_MEAN = 5e-3          # original item-A bounds, applied per alternate
GATE_P99 = 5e-2
GATE_REPEAT_MAX = 1e-6
ORACLE_MODEL_FAM = "q25c"
ORACLE_TOKENS = 2048
ORACLE_CHUNK = 512
ORACLE_MEAN = 1e-4
ORACLE_P99 = 1e-3
ORACLE_CACHEPOS_MAX = 1e-6
BOUNDARY_W = 32           # rows flagged 'boundary' after each later
                          # prod-chunk edge (cache-bug fingerprint zone)
EXPECTED_FAMS = ("q25c", "q3", "q35", "sc2")   # must mirror FAM_SMALL
EXPECTED_ALT_KEYS = frozenset(f"{f}/chunk{c}" for f in EXPECTED_FAMS
                              for c in CHUNKS_ALT)
OUT_DIR = os.path.join(BASE, "results_v2", "diag")
OUT_JSON = os.path.join(OUT_DIR, "item_a_diag.json")


def strata_of(n_targets, chunk=CHUNK_PROD, bw=BOUNDARY_W):
    """PURE (testable) partition of output rows by the INPUT/LOGIT
    position that computes them: row j is produced by the logits at
    input position j (predicting token j+1), so the chunk containing
    POSITION j governs (audit fix: keying on the target token j+1 was
    off by one — row 2047 is still computed inside the first chunk with
    no cache; the cached regime starts at row 2048). Exactly one label
    each: 'first' (j < chunk — no cache involved), 'boundary' (first bw
    rows at/after each later chunk edge — where a cache/position bug
    spikes), 'interior' (everything else)."""
    out = []
    for j in range(n_targets):
        if j < chunk:
            out.append("first")
        elif j % chunk < bw:
            out.append("boundary")
        else:
            out.append("interior")
    return out


def delta_stats(a, b):
    """Signed + absolute delta quantiles between two NLL vectors."""
    d = (a.double() - b.double())
    ad = d.abs()
    return dict(n=int(d.numel()),
                mean_signed=float(d.mean()),
                mean_abs=float(ad.mean()),
                p50=float(ad.quantile(0.50)),
                p90=float(ad.quantile(0.90)),
                p99=float(ad.quantile(0.99)),
                max=float(ad.max()))


def strata_stats(a, b, labels):
    d = (a.double() - b.double()).abs()
    out = {}
    for s in ("first", "boundary", "interior"):
        idx = [j for j, L in enumerate(labels) if L == s]
        if idx:
            sub = d[idx]
            out[s] = dict(n=len(idx), mean_abs=float(sub.mean()),
                          max=float(sub.max()))
    return out


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def gate_verdict(alt_stats, repeat_maxes, oracle_stats, cachepos_max):
    """PURE frozen decision rule (PREREG §13): the EXACT 12 alternate
    pairs (4 families x 3 chunks) each within the ORIGINAL bounds; a
    finite repeat-determinism value for ALL FOUR families; fp32 eager
    oracle tight; cache_position exact. Empty/partial inputs and
    NaN/non-finite values FAIL (audit fix: NaN comparisons are False,
    which silently passed the > bounds; a missing family or pair must
    never pass by omission). Characterization entries never enter.
    Returns (ok, failures)."""
    fails = []
    missing = sorted(EXPECTED_ALT_KEYS - set(alt_stats))
    extra = sorted(set(alt_stats) - EXPECTED_ALT_KEYS)
    if missing or extra:
        fails.append(f"alt-coverage:missing={missing[:4]},extra={extra[:4]}")
    for name in sorted(EXPECTED_ALT_KEYS & set(alt_stats)):
        st = alt_stats[name]
        if not (_finite(st.get("mean_abs")) and _finite(st.get("p99"))
                and st["mean_abs"] < GATE_MEAN and st["p99"] < GATE_P99):
            fails.append(f"prod-stability:{name}")
    rep = repeat_maxes or {}
    if sorted(rep) != sorted(EXPECTED_FAMS):
        fails.append(f"repeat-coverage:{sorted(rep)}")
    for fam in sorted(set(EXPECTED_FAMS) & set(rep)):
        if not (_finite(rep[fam]) and rep[fam] <= GATE_REPEAT_MAX):
            fails.append(f"repeat-determinism:{fam}")
    if (oracle_stats is None
            or not (_finite(oracle_stats.get("mean_abs"))
                    and _finite(oracle_stats.get("p99"))
                    and oracle_stats["mean_abs"] < ORACLE_MEAN
                    and oracle_stats["p99"] < ORACLE_P99)):
        fails.append("fp32-eager-oracle")
    if not (_finite(cachepos_max) and cachepos_max <= ORACLE_CACHEPOS_MAX):
        fails.append("cache-position")
    return (not fails, fails)


@torch.no_grad()
def oneshot_argmax(model, ids, device):
    """DIAGNOSTIC-ONLY argmax under the TRUE one-shot path
    (use_cache=False — the exact path battery item A compared against;
    audit fix: eval_window(chunk=CTX) still runs use_cache=True and is
    a DIFFERENT kernel shape). The one-shot forward materializes its
    logits once; argmax is sliced out and logits freed immediately."""
    logits = model(input_ids=ids[None].to(device),
                   use_cache=False).logits[0]
    n = ids.shape[0] - 1
    am = torch.empty(n, dtype=torch.long)
    for a in range(0, n, 1024):
        b = min(a + 1024, n)
        am[a:b] = logits[a:b].argmax(-1).cpu()
    del logits
    return am


@torch.no_grad()
def argmax_chunked(model, ids, device, chunk):
    """DIAGNOSTIC-ONLY streaming argmax under the same chunking pattern
    as production (full logits never retained — only 1 int per target).
    NLL always comes from the production eval_window; this exists solely
    for the rank-agreement report."""
    T = ids.shape[0]
    am = torch.empty(T - 1, dtype=torch.long)
    past = None
    done = 0
    while done < T - 1:
        take = min(chunk, T - done)
        inp = ids[done:done + take].unsqueeze(0).to(device)
        out = model(input_ids=inp, past_key_values=past, use_cache=True)
        past = out.past_key_values
        n_t = min(take, (T - 1) - done)
        am[done:done + n_t] = out.logits[0, :n_t].argmax(-1).cpu()
        del out
        done += take
    del past
    return am


@torch.no_grad()
def eval_window_explicit_pos(model, ids, device, chunk):
    """DIAGNOSTIC-ONLY mirror of the production loop that passes an
    EXPLICIT cache_position — the implicit-vs-explicit contrast is the
    direct probe of position-derivation bugs in the cached path."""
    T = ids.shape[0]
    nlls = torch.empty(T - 1, dtype=torch.float32)
    past = None
    done = 0
    while done < T - 1:
        take = min(chunk, T - done)
        inp = ids[done:done + take].unsqueeze(0).to(device)
        pos = torch.arange(done, done + take, device=device)
        out = model(input_ids=inp, past_key_values=past, use_cache=True,
                    cache_position=pos)
        past = out.past_key_values
        logits = out.logits[0]
        n_t = min(take, (T - 1) - done)
        tgt = ids[done + 1: done + 1 + n_t].to(device)
        for a in range(0, n_t, 256):
            b = min(a + 256, n_t)
            lp = logits[a:b].float().log_softmax(-1)
            nlls[done + a: done + b] = (
                -lp.gather(1, tgt[a:b, None])[:, 0]).cpu()
        del logits, out
        done += take
    del past
    return nlls


def load_oracle(mid, rev):
    """fp32 EAGER-attention load for the oracle: the math path removes
    fused-kernel shape sensitivity, so remaining divergence is semantic."""
    from transformers import AutoModelForCausalLM
    try:
        m = AutoModelForCausalLM.from_pretrained(
            mid, revision=rev, dtype=torch.float32,
            attn_implementation="eager", local_files_only=True)
    except TypeError:
        m = AutoModelForCausalLM.from_pretrained(
            mid, revision=rev, torch_dtype=torch.float32,
            attn_implementation="eager", local_files_only=True)
    m.eval()
    for p in m.parameters():
        p.requires_grad_(False)
    return m


def main():
    from eval_incontext import eval_window          # PRODUCTION path
    from validity_battery import (FAM_SMALL, load_text_model, oneshot_nll,
                                  rev_of, tok_of)
    assert tuple(FAM_SMALL) == EXPECTED_FAMS, \
        "EXPECTED_FAMS drifted from the battery family set"
    from provenance import (env_fingerprint, env_matches_freeze,
                            env_matches_lock, gpu_info, harness_hash,
                            source_clean, source_tree_hash)
    if not torch.cuda.is_available():
        raise SystemExit("FATAL: diagnostic requires CUDA (it probes the "
                         "CUDA kernel paths the incident occurred on)")
    device = "cuda"
    lock_ok, lock_probs = env_matches_lock()
    frz_ok, frz_detail = env_matches_freeze()
    if not (lock_ok and frz_ok):
        raise SystemExit(f"FATAL: environment mismatch — lock: "
                         f"{lock_probs[:4] or 'ok'}; freeze: {frz_detail}")
    if not source_clean():
        raise SystemExit("FATAL: dirty source tree — the recorded "
                         "identity would not describe executed code")

    text = open(os.path.join(BASE, "data/streams/mathlib/full_topo.txt"),
                encoding="utf-8").read()[:200_000]
    # START identities: recorded verbatim, RE-CHECKED at completion —
    # drift prevents the JSON from ever existing (audit fix)
    ident_start = dict(harness=harness_hash(), env=env_fingerprint(),
                       src=source_tree_hash())
    res = dict(schema="item_a_diag_v1",
               harness_hash=ident_start["harness"],
               env_fingerprint=ident_start["env"],
               source_tree_hash=ident_start["src"],
               **gpu_info(),
               torch_version=torch.__version__,
               gates=dict(mean=GATE_MEAN, p99=GATE_P99,
                          repeat_max=GATE_REPEAT_MAX,
                          oracle_mean=ORACLE_MEAN, oracle_p99=ORACLE_P99,
                          cachepos_max=ORACLE_CACHEPOS_MAX),
               families={})
    alt_stats = {}
    repeat_maxes = {}
    t0 = time.time()
    for fam, mid in FAM_SMALL.items():
        tk = tok_of(mid)
        ids_list = tk(text, add_special_tokens=False)["input_ids"][:CTX]
        ids = torch.tensor(ids_list, dtype=torch.long)
        # audit fix: load_text_model returns a 4-tuple; the identity is
        # recorded exactly as the battery records it
        model, cls, nparams, attn = load_text_model(mid, device)
        prod = eval_window(model, ids, device, CHUNK_PROD)
        rep = eval_window(model, ids, device, CHUNK_PROD)
        rep_max = float((prod - rep).abs().max())
        repeat_maxes[fam] = rep_max
        labels = strata_of(len(prod))
        fam_out = dict(model=mid, revision=rev_of(mid), cls=cls,
                       n_params=nparams, attn=attn,
                       n_targets=len(prod), repeat_max_abs=rep_max,
                       pairs={})
        am_prod = argmax_chunked(model, ids, device, CHUNK_PROD)
        for ch in CHUNKS_ALT:
            alt = eval_window(model, ids, device, ch)
            st = delta_stats(alt, prod)
            st["strata"] = strata_stats(alt, prod, labels)
            am_alt = argmax_chunked(model, ids, device, ch)
            st["argmax_agree"] = float((am_alt == am_prod).float().mean())
            fam_out["pairs"][f"chunk{ch}"] = st
            alt_stats[f"{fam}/chunk{ch}"] = st
        # bf16 TRUE one-shot (use_cache=False — the exact path battery
        # item A compared against; audit fix): CHARACTERIZATION ONLY,
        # never enters the verdict
        one = oneshot_nll(model, ids_list, device)
        st1 = delta_stats(one, prod)
        st1["strata"] = strata_stats(one, prod, labels)
        am_one = oneshot_argmax(model, ids, device)
        st1["argmax_agree"] = float((am_one == am_prod).float().mean())
        fam_out["oneshot_bf16_characterization"] = st1
        res["families"][fam] = fam_out
        del model
        torch.cuda.empty_cache()
        print(f"[{fam}] done {time.time()-t0:.0f}s", flush=True)

    # fp32 EAGER oracle on the sentinel family at 2048 tokens: TRUE
    # one-shot (use_cache=False) vs production cached chunk-512
    mid = FAM_SMALL[ORACLE_MODEL_FAM]
    tk = tok_of(mid)
    o_ids_list = tk(text, add_special_tokens=False)["input_ids"][
        :ORACLE_TOKENS]
    o_ids = torch.tensor(o_ids_list, dtype=torch.long)
    om = load_oracle(mid, rev_of(mid)).to(device)
    o_one = oneshot_nll(om, o_ids_list, device)           # true one-shot
    o_chk = eval_window(om, o_ids, device, ORACLE_CHUNK)  # cached (prod)
    oracle = delta_stats(o_chk, o_one)
    o_exp = eval_window_explicit_pos(om, o_ids, device, ORACLE_CHUNK)
    cachepos_max = float((o_chk - o_exp).abs().max())
    res["oracle"] = dict(family=ORACLE_MODEL_FAM, tokens=ORACLE_TOKENS,
                         chunk=ORACLE_CHUNK, dtype="float32",
                         attn="eager", stats=oracle,
                         cachepos_max_abs=cachepos_max)
    del om
    torch.cuda.empty_cache()

    ok, fails = gate_verdict(alt_stats, repeat_maxes, oracle, cachepos_max)
    res["verdict"] = dict(
        ok=ok, failures=fails,
        meaning=("ALL gates pass: divergence is bf16 kernel-shape "
                 "numerics; production chunked path stable; item A "
                 "re-specification may proceed (PREREG §13)" if ok else
                 "HARD STOP: production-stability/oracle failure — "
                 "treat as cache/position bug; fix code, no gate "
                 "discussion"))
    # completion re-check (audit fix): identities must be UNCHANGED or
    # the JSON never exists — drifted evidence must not be publishable
    drift = [k for k, v in (("harness", harness_hash()),
                            ("env", env_fingerprint()),
                            ("src", source_tree_hash()))
             if ident_start[k] != v]
    if drift or not source_clean():
        raise SystemExit(f"FATAL: {drift or ['source_clean']} changed "
                         "DURING the diagnostic — JSON not written")
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT_JSON):   # evidence is never overwritten
        ts = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        os.rename(OUT_JSON, f"{OUT_JSON}.quarantine-{ts}")
        print(f"[diag] prior JSON -> quarantine-{ts}", flush=True)
    with open(OUT_JSON, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res["verdict"], indent=1), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
