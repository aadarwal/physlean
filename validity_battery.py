#!/usr/bin/env python3
"""Validity battery (PREREG §7, items A–E). Small by design: one small
model, minutes of GPU. Writes results_v2/battery/battery.json; review gates
G3 on these numbers."""
import argparse, json, math, os, random, re, subprocess, sys, time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from layout import (PRODUCTION_CHUNK_TOKENS, token_spans,
                    windows_of)  # production layout, not a copy

OUT = os.path.join(BASE, "results_v2", "battery")
LN2 = math.log(2)
LOG = lambda *a: print(*a, flush=True)
MODELS_JSON = (json.load(open(os.path.join(BASE, "models.json")))
               if os.path.exists(os.path.join(BASE, "models.json")) else {})


def rev_of(mid):
    sha = (MODELS_JSON.get(mid) or {}).get("sha")
    if not sha:  # fail-closed: never fall through to HF HEAD
        raise RuntimeError(f"no pinned revision for {mid} in models.json")
    return sha


PARAM_RANGES = {  # loader sanity: expected text(+wrapper) parameter counts
    "q25c": (0.3e9, 0.7e9), "q3": (0.4e9, 0.9e9),
    "q35": (0.5e9, 1.6e9), "sc2": (2.5e9, 3.5e9),
}


@torch.no_grad()
def chunked_nll(model, ids, device, chunk=PRODUCTION_CHUNK_TOKENS):
    """Compatibility wrapper over the production scorer implementation.

    Battery items must not maintain a second KV-cache loop, and their
    default must be the same frozen chunk used by every grid cell.
    """
    from eval_incontext import eval_window
    ids_t = (ids if isinstance(ids, torch.Tensor)
             else torch.tensor(ids, dtype=torch.long))
    return eval_window(model, ids_t, device, chunk)


def bpb(nll_nats, ids, tok, text_bytes=None):
    return float(nll_nats.sum()) / LN2 / max(
        text_bytes or len(tok.decode(ids[1:]).encode()), 1)


FAM_SMALL = {"q25c": "Qwen/Qwen2.5-Coder-0.5B", "q3": "Qwen/Qwen3-0.6B-Base",
             "q35": "Qwen/Qwen3.5-0.8B-Base", "sc2": "bigcode/starcoder2-3b"}
# Big rungs require a SEPARATE battery --big mode (not yet implemented;
# the `big` preflight gate FAILS CLOSED until battery_big.json exists):
# DeepSeek-V2-Lite architecture probe + Qwen3.5 >=131k cache probe.


LOCAL_ONLY = [True]  # set False only via dev escapes in main()


def tok_of(mid):
    return AutoTokenizer.from_pretrained(mid, revision=rev_of(mid),
                                         local_files_only=LOCAL_ONLY[0])


def load_text_model(mid, device):
    rev = rev_of(mid)
    lo = LOCAL_ONLY[0]
    try:
        m = AutoModelForCausalLM.from_pretrained(mid, dtype=torch.bfloat16,
                                                 revision=rev,
                                                 local_files_only=lo)
    except (TypeError,):
        m = AutoModelForCausalLM.from_pretrained(mid, revision=rev,
                                                 torch_dtype=torch.bfloat16,
                                                 local_files_only=lo)
    except Exception:
        from transformers import AutoModelForImageTextToText
        m = AutoModelForImageTextToText.from_pretrained(
            mid, dtype=torch.bfloat16, revision=rev, local_files_only=lo)
    m = m.to(device).eval()
    cfg = m.config.get_text_config() if hasattr(m.config, "get_text_config") \
        else m.config
    attn = dict(model_type=getattr(cfg, "model_type", None),
                sliding_window=getattr(cfg, "sliding_window", None),
                layer_types=str(getattr(cfg, "layer_types", None))[:200])
    return m, type(m).__name__, sum(p.numel() for p in m.parameters()), attn


# ---- item A re-specification (PREREG §7/§13, after the item-A
# incident + follow-up falsifier PASS): the OLD chunked-vs-one-shot
# equality gate compared production against a kernel path production
# never executes, with bounds never CUDA-calibrated. The follow-up
# found neither targeted semantic-bug signature (fp32/MATH semantic
# delta 3.18e-6 mean; causality protected rows exactly 0), so the
# observed bf16 cross-shape divergence is treated as CONSISTENT WITH
# accumulated-KV numerics — characterized, never gated. The new gates
# are PRODUCTION-PATH INVARIANTS only; every production cell runs ONE
# frozen chunk (layout.PRODUCTION_CHUNK_TOKENS) so the measurement
# never crosses kernel shapes.
A_CTX = 8192               # exact — a short window fails closed
A_REPEAT_MAX = 1e-6        # determinism bound (verified on-device)
A_CAUSAL_MAX = 1e-6        # protected rows; correct masking gives 0
A_F2_MEAN = 1e-4           # pre-incident oracle bounds (never derived
A_F2_P99 = 1e-3            # from any observed CUDA value)
A_F2_CHUNK = 512
A_CAUSAL_P = 4095          # last input position of production chunk 2


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def a_fixed_chunk_verdict(a, expected_fams, expected_chunk):
    """PURE frozen decision rule for A_fixed_chunk_semantics (GPU-free
    testable): exact family coverage; per family — class/param sanity,
    production-chunk identity, exact token count, repeat determinism,
    causality mask + non-vacuity; q25c fp32 leg — dispatch == 'sdpa',
    TF32 fully off, semantic bounds, repeat, exact chunk pair.
    NaN/None/missing and partial/extra coverage all FAIL."""
    fails = []
    if a.get("production_chunk") != expected_chunk:
        fails.append(f"production-chunk:{a.get('production_chunk')}")
    fams = a.get("families") or {}
    if sorted(fams) != sorted(expected_fams):
        fails.append(f"family-coverage:{sorted(fams)}")
    for fam in sorted(set(expected_fams) & set(fams)):
        f = fams[fam]
        if not (f.get("class_ok") is True and f.get("param_sane") is True):
            fails.append(f"class-param:{fam}")
        if f.get("chunk") != expected_chunk:
            fails.append(f"chunk:{fam}")
        if f.get("dtype") != "bfloat16":
            fails.append(f"dtype:{fam}")
        if not (isinstance(f.get("attn_resolved"), str)
                and f["attn_resolved"].strip()):
            fails.append(f"attn-record:{fam}")
        if f.get("n_tokens") != A_CTX:
            fails.append(f"tokens:{fam}")
        if not (_finite(f.get("mean_nll")) and f["mean_nll"] >= 0):
            fails.append(f"mean-nll:{fam}")
        if not (_finite(f.get("repeat_max_abs"))
                and 0 <= f["repeat_max_abs"] <= A_REPEAT_MAX):
            fails.append(f"repeat:{fam}")
        c = f.get("causal") or {}
        vocab = c.get("vocab")
        orig = c.get("orig_token")
        pert = c.get("perturbed_token")
        if (c.get("p") != A_CAUSAL_P
                or c.get("n_protected") != A_CAUSAL_P - 1
                or c.get("n_downstream") != A_CTX - 1 - A_CAUSAL_P
                or not (_finite(c.get("excluded_row_delta"))
                        and c["excluded_row_delta"] >= 0)
                or type(vocab) is not int or vocab < 2
                or type(orig) is not int or not 0 <= orig < vocab
                or type(pert) is not int
                or pert != (orig + 1) % vocab):
            fails.append(f"causality-coverage:{fam}")
        if not (_finite(c.get("protected_max_abs"))
                and 0 <= c["protected_max_abs"] <= A_CAUSAL_MAX):
            fails.append(f"causality-mask:{fam}")
        if not (_finite(c.get("downstream_max_abs"))
                and c["downstream_max_abs"] > A_CAUSAL_MAX):
            fails.append(f"causality-vacuous:{fam}")
    f2 = a.get("f2") or {}
    if f2.get("dtype") != "float32":
        fails.append(f"f2-dtype:{f2.get('dtype')}")
    if f2.get("attn_resolved") != "sdpa":
        fails.append(f"f2-attn-impl:{f2.get('attn_resolved')}")
    if f2.get("sdp_backend_forced") != "MATH":
        fails.append(f"f2-backend:{f2.get('sdp_backend_forced')}")
    if f2.get("tokens") != A_CTX:
        fails.append(f"f2-tokens:{f2.get('tokens')}")
    tf = f2.get("tf32") or {}
    if not (tf.get("matmul_allow_tf32") is False
            and tf.get("cudnn_allow_tf32") is False
            and tf.get("float32_matmul_precision") == "highest"):
        fails.append("f2-tf32")
    st = f2.get("stats") or {}
    stat_scalars = ("mean_signed", "mean_abs", "p50", "p90", "p99",
                    "max")
    abs_stats = (st.get("mean_abs"), st.get("p50"), st.get("p90"),
                 st.get("p99"), st.get("max"))
    if (st.get("n") != A_CTX - 1
            or not all(_finite(st.get(k)) for k in stat_scalars)
            or not all(x >= 0 for x in abs_stats if _finite(x))
            or (all(_finite(x) for x in abs_stats)
                and not (st["p50"] <= st["p90"] <= st["p99"]
                         <= st["max"] and st["mean_abs"] <= st["max"]))):
        fails.append("f2-stats-completeness")
    if not (_finite(st.get("mean_abs")) and _finite(st.get("p99"))
            and st["mean_abs"] < A_F2_MEAN and st["p99"] < A_F2_P99):
        fails.append("f2-semantic")
    if not (_finite(f2.get("repeat_max_abs"))
            and 0 <= f2["repeat_max_abs"] <= A_REPEAT_MAX):
        fails.append("f2-repeat")
    if list(f2.get("chunks") or []) != [A_F2_CHUNK, expected_chunk]:
        fails.append(f"f2-chunks:{f2.get('chunks')}")
    return (not fails, fails)


def item_A(model, tok, device, res):
    """A_fixed_chunk_semantics: production-path invariants per bf16
    family at EXACTLY 8192 tokens (spans StarCoder2's sliding window and
    Qwen3.5 hybrid state well past one chunk) — loader class/param
    sanity, production-chunk repeat determinism, and the structural
    causality probe on the exact production kernel (resolved attention
    impl RECORDED for all; gated == 'sdpa' only on the q25c fp32 leg,
    which is the semantic-equivalence evidence: TF32 off asserted, torch
    SDP backend FORCED MATH, chunk 512 vs PRODUCTION_CHUNK_TOKENS).
    NO bf16 cross-shape or one-shot gate exists (§13: characterized,
    not gated). Pure helpers are REUSED from diag_item_a_followup —
    the falsifier and the gate share one implementation."""
    from diag_item_a_followup import (causality_partition,
                                      delta_stats, load_fp32_default,
                                      perturb_ids, resolved_attn,
                                      tf32_snapshot)
    text = open(os.path.join(BASE, "data/streams/mathlib/full_topo.txt"),
                encoding="utf-8").read()[:120000]
    fams = {}
    for fam, mid in FAM_SMALL.items():
        tk = tok_of(mid)
        ids = tk(text, add_special_tokens=False)["input_ids"][:A_CTX]
        if len(ids) != A_CTX:
            raise RuntimeError(f"A[{fam}]: only {len(ids)} tokens; the "
                               f"frozen design requires exactly {A_CTX}")
        m2, cls, nparams, attn = load_text_model(mid, device)
        r1 = chunked_nll(m2, ids, device, chunk=PRODUCTION_CHUNK_TOKENS)
        r2 = chunked_nll(m2, ids, device, chunk=PRODUCTION_CHUNK_TOKENS)
        rep_max = float((r1 - r2).abs().max())
        cfg2 = m2.config
        tcfg2 = (cfg2.get_text_config()
                 if hasattr(cfg2, "get_text_config") else cfg2)
        vocab = int(tcfg2.vocab_size)
        pert = perturb_ids(ids, A_CAUSAL_P, vocab)
        rp = chunked_nll(m2, pert, device,
                         chunk=PRODUCTION_CHUNK_TOKENS)
        d = (r1.double() - rp.double()).abs()
        prot, excl, down = causality_partition(len(r1), A_CAUSAL_P)
        lo, hi = PARAM_RANGES[fam]
        fams[fam] = dict(
            model=mid, revision=rev_of(mid), cls=cls, n_params=nparams,
            attn=attn, attn_resolved=resolved_attn(m2),
            dtype="bfloat16", n_tokens=len(ids),
            chunk=PRODUCTION_CHUNK_TOKENS,
            repeat_max_abs=rep_max, mean_nll=float(r1.mean()),
            causal=dict(p=A_CAUSAL_P, vocab=vocab,
                        orig_token=ids[A_CAUSAL_P],
                        perturbed_token=pert[A_CAUSAL_P],
                        n_protected=len(prot),
                        n_downstream=len(down),
                        protected_max_abs=float(d[prot].max()),
                        excluded_row_delta=float(d[excl[0]]),
                        downstream_max_abs=float(d[down].max())),
            class_ok=("ForCausalLM" in cls
                      or "ForConditionalGeneration" in cls),
            param_sane=lo <= nparams <= hi)
        del m2
        if device == "cuda":
            torch.cuda.empty_cache()
        LOG(f"A[{fam}]:", cls, f"{nparams/1e9:.2f}B",
            "repeat", rep_max,
            "causal_prot", fams[fam]["causal"]["protected_max_abs"],
            "attn", fams[fam]["attn_resolved"])
    # q25c fp32 semantic leg: TF32 off (asserted + recorded), model impl
    # gated == 'sdpa' in the verdict, torch SDP backend forced MATH
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    tf = tf32_snapshot()
    assert (tf["matmul_allow_tf32"] is False
            and tf["cudnn_allow_tf32"] is False
            and tf["float32_matmul_precision"] == "highest"), tf
    mid = FAM_SMALL["q25c"]
    tk = tok_of(mid)
    ids = tk(text, add_special_tokens=False)["input_ids"][:A_CTX]
    if len(ids) != A_CTX:
        raise RuntimeError(f"A[f2]: only {len(ids)} tokens")
    m32 = load_fp32_default(mid, rev_of(mid)).to(device)
    from torch.nn.attention import SDPBackend, sdpa_kernel
    with sdpa_kernel([SDPBackend.MATH]):
        a32 = chunked_nll(m32, ids, device, chunk=A_F2_CHUNK)
        b32 = chunked_nll(m32, ids, device,
                          chunk=PRODUCTION_CHUNK_TOKENS)
        b32r = chunked_nll(m32, ids, device,
                           chunk=PRODUCTION_CHUNK_TOKENS)
    f2 = dict(model=mid, revision=rev_of(mid), dtype="float32",
              attn_resolved=resolved_attn(m32),
              sdp_backend_forced="MATH", tf32=tf, tokens=len(ids),
              chunks=[A_F2_CHUNK, PRODUCTION_CHUNK_TOKENS],
              stats=delta_stats(a32, b32),
              repeat_max_abs=float((b32 - b32r).abs().max()))
    del m32
    if device == "cuda":
        torch.cuda.empty_cache()
    block = dict(families=fams, f2=f2,
                 production_chunk=PRODUCTION_CHUNK_TOKENS)
    ok, fails = a_fixed_chunk_verdict(block, tuple(FAM_SMALL),
                                      PRODUCTION_CHUNK_TOKENS)
    block["verdict"] = dict(ok=ok, failures=fails)
    res["A_fixed_chunk_semantics"] = block
    LOG("A: verdict", ok, fails or "all production-path invariants hold",
        "| f2 mean", f2["stats"]["mean_abs"])



def grouping_conservation(lens, grps, ids, nll_seq):
    """PRODUCTION grouping/window/drop/collapse conservation for ONE
    (tokenizer, text, per-token NLL sequence). Returns (rec, ok).
    A zero-byte standalone retained group FAILS (collapse raises)."""
    import pandas as pd
    from analyze_v2 import collapse_groups
    recs, drop_nll, drop_rows = [], 0.0, 0
    open_bytes = 0
    cum = [0]
    for L in lens:
        cum.append(cum[-1] + L)
    spans = windows_of(len(ids), 4096, grps, min_tail=1)
    for w, (ws, we) in enumerate(spans):
        og = grps[ws]
        open_bytes += sum(lens[p] for p in range(ws, we)
                          if grps[p] == og)
        for j in range(ws + 1, we):
            if grps[j] == og:
                drop_nll += float(nll_seq[j - 1])
                drop_rows += 1
                continue
            recs.append(dict(win=w, doc=-1, ctxb=cum[j] - cum[ws],
                             blen=lens[j], tok=ids[j],
                             nll=float(nll_seq[j - 1]), grp=grps[j]))
    df = pd.DataFrame(recs)
    try:
        agg = collapse_groups(df)  # raises on any conservation breach
    except AssertionError as e:
        return dict(error=str(e)), False
    all_scored = float(sum(nll_seq[j - 1] for ws, we in spans
                           for j in range(ws + 1, we)))
    nll_ok = abs(float(agg.nll.sum()) + drop_nll - all_scored) \
        < 1e-6 * max(all_scored, 1)
    bytes_ok = int(agg.blen.sum()) == cum[len(ids)] - open_bytes
    ok = bool(nll_ok and bytes_ok and (agg.blen > 0).all())
    rec = dict(n_groups=int(len(agg)),
               zero_rows=int((df.blen == 0).sum()),
               zero_nll_share=float(df[df.blen == 0].nll.sum()
                                    / max(df.nll.sum(), 1e-9)),
               boundary_dropped_rows=drop_rows,
               boundary_dropped_nll=float(drop_nll),
               nll_ok=bool(nll_ok), bytes_ok=bool(bytes_ok),
               conservation_ok=ok)
    return rec, ok


def item_B(device_model, tok_main, device, res):
    corpora = ["physlib", "mathlib", "qutip", "sympy", "geant4"]
    # OPTIONAL arXiv rows are opportunistic and NON-GATING, and must be
    # CURRENT: a corpus joins only if the present streams_stats records
    # it (a stale on-disk file alone must not resurrect it — review fix)
    try:
        _st = json.load(open(os.path.join(BASE, "data",
                                          "streams_stats.json")))
        corpora += [c for c in ("arxiv_old", "arxiv_new")
                    if c in _st.get("corpora", {})]
    except (OSError, ValueError):
        pass
    rows = {}
    for fam, mid in FAM_SMALL.items():  # ALL FOUR tokenizer families
        tk = tok_of(mid)
        for c in corpora:
            p = os.path.join(BASE, f"data/streams/{c}/full_topo.txt")
            if not os.path.exists(p):
                continue
            text = open(p, encoding="utf-8").read()[:500_000]
            enc = tk(text, add_special_tokens=False,
                     return_offsets_mapping=True)
            # PRODUCTION layout: byte partition must hold for every
            # family x corpus (zero rows counted from the same source)
            lens, grps = token_spans(text, enc["offset_mapping"])
            assert sum(lens) == len(text.encode("utf-8")), (fam, c)
            zero = sum(1 for L in lens if L == 0)
            rows[f"{fam}/{c}"] = dict(
                tokens=len(enc["input_ids"]), zero_rows=zero,
                zero_share=zero / max(len(enc["input_ids"]), 1),
                n_groups=len(set(grps)))
    # GROUP CONSERVATION through the PRODUCTION path for EVERY tokenizer
    # family (PREREG B): synthetic deterministic per-token NLL suffices to
    # prove the grouping/window/drop/collapse pipeline conserves; the
    # real-model NLL-share diagnostic stays separate on q25c below.
    conserv_ok = True
    for fam, mid in FAM_SMALL.items():
        tk = tok_of(mid)
        for c in ("physlib", "mathlib"):
            text = open(os.path.join(BASE,
                                     f"data/streams/{c}/full_topo.txt"),
                        encoding="utf-8").read()[:100_000]
            enc = tk(text, add_special_tokens=False,
                     return_offsets_mapping=True)
            ids = enc["input_ids"][:16384]
            lens, grps = token_spans(text, enc["offset_mapping"])
            lens, grps = lens[:len(ids)], grps[:len(ids)]
            synth_nll = [0.001 * (j + 1) for j in range(len(ids) - 1)]
            rec, ok = grouping_conservation(lens, grps, ids, synth_nll)
            conserv_ok = conserv_ok and ok
            rows[f"conserv/{fam}/{c}"] = rec
    # real-model diagnostic (q25c): same production path, measured NLL
    for c in ("physlib", "mathlib"):
        text = open(os.path.join(BASE, f"data/streams/{c}/full_topo.txt"),
                    encoding="utf-8").read()[:100_000]
        enc = tok_main(text, add_special_tokens=False,
                       return_offsets_mapping=True)
        ids = enc["input_ids"][:16384]
        lens, grps = token_spans(text, enc["offset_mapping"])
        lens, grps = lens[:len(ids)], grps[:len(ids)]
        nll = chunked_nll(device_model, ids, device).double()
        rec, ok = grouping_conservation(lens, grps, ids,
                                        [float(x) for x in nll])
        conserv_ok = conserv_ok and ok
        rows[f"nllshare/{c}"] = rec
    # synthetic multibyte partition check through production token_spans
    synth = "∀x∈ℝ!∎αβγ𝔸"
    offs, cch = [], 0
    for ch in synth:
        offs.append((cch, cch + 1))
        if len(ch.encode()) > 1:
            offs.append((cch, cch + 1))
        cch += 1
    sl, sg = token_spans(synth, offs)
    synth_ok = (sum(sl) == len(synth.encode())
                and all(sl[i] > 0 for i in range(len(sl))
                        if sg[i] not in sg[:i]))
    # REAL-offset probe on all four pinned tokenizers (reviewer-verified:
    # 31/31 bytes conserved; Qwen 1 overlap row, SC2 5 incl. partial spans)
    # two fixed probes: the unicode string (reviewer-verified) and a
    # synthetic LaTeX snippet so FORMAT coverage never depends on the
    # OPTIONAL arXiv corpus being present (amendment); real-arXiv rows
    # above are opportunistic and non-gating
    probes = {"unicode": "a∀b ⟨x,y⟩ ↦ α → β",
              "latex": ("\\begin{equation} \\alpha_s(M_Z^2) = "
                        "\\frac{12\\pi}{23\\ln(M_Z^2/\\Lambda^2)} "
                        "\\end{equation} % comment ~5\\%")}
    probe_out, probe_ok = {}, True
    for fam, mid in FAM_SMALL.items():
        tk = tok_of(mid)  # one load per family, shared by both probes
        for pname, probe in probes.items():
            enc = tk(probe, add_special_tokens=False,
                     return_offsets_mapping=True)
            pl, pg = token_spans(probe, enc["offset_mapping"])
            ok = sum(pl) == len(probe.encode())
            probe_ok = probe_ok and ok
            probe_out[f"{fam}/{pname}"] = dict(
                tokens=len(pl),
                overlap_rows=sum(1 for L in pl if L == 0),
                bytes_conserved=ok)
    rows["real_offset_probe"] = probe_out
    rows["synthetic_partition_ok"] = bool(synth_ok)
    rows["conservation_ok"] = bool(conserv_ok and synth_ok and probe_ok)
    res["B_zero_rows"] = rows
    LOG("B:", json.dumps(rows, indent=1)[:800])


def item_C(model, tok, device, res):
    out = {}
    for c in ("mathlib", "physlib"):
        text = open(os.path.join(BASE, f"data/streams/{c}/full_topo.txt"),
                    encoding="utf-8").read()
        ids = tok(text, add_special_tokens=False)["input_ids"]
        rows = []
        for t0 in (40960, 49152, 57344):
            if t0 + 512 > len(ids):
                continue
            tgt = ids[t0:t0 + 512]
            per_prefix = {}
            # 32256 + 512 = 32768 == max_position_embeddings (cap per review)
            for pl in (1024, 4096, 16384, 32256):
                ctx = ids[t0 - pl:t0]
                nll = chunked_nll(model, ctx + tgt, device)
                per_prefix[pl] = float(nll[-len(tgt):].mean())
            rows.append(per_prefix)
        mono_viol = sum(
            1 for r in rows for a, b in zip(sorted(r), sorted(r)[1:])
            if r[b] > r[a] + 0.01)
        out[c] = dict(targets=rows, monotonicity_violations=mono_viol)
    res["C_nested_context"] = out
    LOG("C:", json.dumps(out, indent=1)[:600])


def item_D(model, tok, device, res):
    import glob
    files = sorted(glob.glob(os.path.join(BASE, "corpora/physlib/Physlib/**/*.lean"),
                             recursive=True), key=os.path.getsize)
    f = next(p for p in files if 6000 < os.path.getsize(p) < 12000)
    text = open(f, encoding="utf-8").read()
    ids1 = tok(text, add_special_tokens=False)["input_ids"]
    ids = ids1 * 8
    nll = chunked_nll(model, ids, device)
    nb = len(text.encode())
    per_copy = []
    for k in range(8):
        s = max(k * len(ids1) - 1, 0)
        e = (k + 1) * len(ids1) - 1
        per_copy.append(float(nll[s:e].sum()) / LN2 / nb)
    res["D_duplicate_control"] = dict(file=os.path.basename(f), bytes=nb,
                                      bpb_per_copy=per_copy)
    LOG("D:", per_copy)


DECL = re.compile(r"^(theorem|lemma|def|instance|structure|noncomputable def)\s",
                  re.M)


def trunc_bytes(s, nbytes):
    b = s.encode("utf-8")[:nbytes]
    return b.decode("utf-8", errors="ignore")


# item E designated corpus (PREREG §7/§13 amendment): the pinned
# physlib snapshot exposes only 8 source import directives across 538
# files (QuantumInfo 0; `import all` support still yields zero eligible)
# — its dependency graph lives at the ELABORATED level and is reserved
# for the V2-a extractor. The identical parser on pinned mathlib finds
# 81 eligible files, so mathlib is the designated LITE-E corpus. E is
# MACHINERY VALIDATION ONLY: not physlib evidence, not the V2-b
# grounding pilot (DESIGN_V2 §9). Floor = E's own sample size — the
# smallest non-vacuous value, fixed structurally, not outcome-driven: a
# run that cannot FILL its sample fails closed, never silently shrinks.
E_CORPUS = "mathlib"
E_REPO_DIR = "mathlib4"
E_SCAN_DIR = "Mathlib"
E_SAMPLE = 8
E_MIN_ELIGIBLE = E_SAMPLE
# `import all Foo` (Lean >= 4.9) resolves the same module dependency
E_IMPORT_RE = re.compile(r"^import\s+(?:all\s+)?([A-Za-z0-9_.]+)", re.M)


def item_E(model, tok, device, res):
    """Dependency vs equal-BYTE random context, scored on the
    POST-FIRST-DECLARATION SUFFIX of each target file (the accurate name:
    everything from the first declaration onward — headers/imports never
    scored; a per-declaration body split is v2's job). Targets are
    seeded-sampled from all eligible files; random context is drawn
    WITHOUT replacement from eligible non-target/non-dependency files
    (with-replacement could duplicate files, which dependency context
    cannot — review fix), and insufficiency is recorded, never padded.
    Runs on the DESIGNATED corpus with a non-vacuous eligibility floor
    AND exact realized-row gating (see E_CORPUS block above): an
    empty/thin/under-filled E raises. Pool sufficiency is judged
    against the bytes ACTUALLY SHOWN — min(dep bytes, 16KB cap) — not
    the full closure (review fix: full-closure sufficiency skipped
    targets over pool bytes the arm never uses, a latent
    closure-size-correlated selection bias). E is MACHINERY VALIDATION
    ONLY — never physlib evidence, never the V2-b grounding pilot."""
    imp = E_IMPORT_RE
    root = os.path.join(BASE, "corpora", E_REPO_DIR)
    mods = {}
    for dp, _, ns in os.walk(os.path.join(root, E_SCAN_DIR)):
        for n in ns:
            if n.endswith(".lean"):
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, root)[:-5].replace(os.sep, ".")
                mods[rel] = p
    rng = random.Random(11)
    allpaths = sorted(mods.values())
    eligible = []
    for rel, p in sorted(mods.items()):
        text = open(p, encoding="utf-8").read()
        deps = [mods[m] for m in imp.findall(text) if m in mods]
        m = DECL.search(text)
        if len(deps) >= 2 and m and 4000 < len(text.encode()) < 20000:
            eligible.append((p, deps, text[m.start():]))
    rng.shuffle(eligible)          # seeded target sampling, not lexicographic
    pairs = eligible[:E_SAMPLE]
    res.setdefault("E_meta", {}).update(
        corpus=E_CORPUS, scan_root=E_SCAN_DIR, parser=imp.pattern,
        n_files_scanned=len(mods), n_eligible_targets=len(eligible),
        eligibility_floor=E_MIN_ELIGIBLE,
        scope=("machinery validation only — not physlib evidence, not "
               "the V2-b grounding pilot (DESIGN_V2 §9)"))
    if len(eligible) < E_MIN_ELIGIBLE:
        raise RuntimeError(
            f"item E: {len(eligible)} eligible targets on {E_CORPUS} < "
            f"floor {E_MIN_ELIGIBLE} — an empty/thin E must fail, never "
            "pass vacuously")
    rows = []
    skipped = []
    MAX_CTX_TOK = 8192
    for p, deps, body in pairs:
        dep_full = "".join(open(d, encoding="utf-8").read() for d in deps)
        pool = [q for q in allpaths if q != p and q not in deps]
        rng.shuffle(pool)          # WITHOUT replacement
        rand_full = ""
        # sufficiency vs bytes ACTUALLY SHOWN (min of closure and the
        # 16KB cap), never the full closure (review fix — see docstring)
        shown = min(len(dep_full.encode()), 16000)
        need = shown + 40000       # margin for the truncation loop
        for q in pool:
            if len(rand_full.encode()) >= need:
                break
            rand_full += open(q, encoding="utf-8").read()
        if len(rand_full.encode()) < shown:
            skipped.append(os.path.basename(p))
            continue
        # adaptive equal-BYTE cap: shrink until BOTH tokenize within the
        # window, so the contexts actually shown stay byte-matched
        # (post-truncating tokens broke the match when densities differ)
        cap = 16000
        while cap > 1000:
            dep_ctx = trunc_bytes(dep_full, cap)
            rand_ctx = trunc_bytes(rand_full, len(dep_ctx.encode()))
            dep_ids = tok(dep_ctx, add_special_tokens=False)["input_ids"]
            rand_ids = tok(rand_ctx, add_special_tokens=False)["input_ids"]
            if len(dep_ids) <= MAX_CTX_TOK and len(rand_ids) <= MAX_CTX_TOK:
                break
            cap = int(cap * 0.8)
        tgt = tok(body, add_special_tokens=False)["input_ids"][:2048]
        row = {"ctx_bytes_dep": len(dep_ctx.encode()),
               "ctx_bytes_rand": len(rand_ctx.encode()),
               "ctx_tokens_dep": len(dep_ids),
               "ctx_tokens_rand": len(rand_ids)}
        for name, ids_ctx in (("dep", dep_ids), ("rand", rand_ids)):
            nll = chunked_nll(model, ids_ctx + tgt, device)
            row[name] = float(nll[-len(tgt):].mean())
        row["file"] = os.path.basename(p)
        rows.append(row)
    adv = sum(1 for r in rows if r["dep"] < r["rand"])
    res["E_dep_vs_random_context"] = dict(
        corpus=E_CORPUS, rows=rows, dep_wins=adv, n=len(rows),
        skipped_insufficient_pool=skipped,
        mean_delta_nats=float(sum(r["rand"] - r["dep"] for r in rows)
                              / max(len(rows), 1)))
    if len(rows) < E_SAMPLE:
        # exact realized-row gating (review fix: skips could shrink a
        # "passing" E below its sample — the 0-row masquerade at n=1)
        raise RuntimeError(
            f"item E: only {len(rows)}/{E_SAMPLE} realized rows "
            f"(skipped: {skipped}) — an under-filled E must fail")
    LOG("E:", res["E_dep_vs_random_context"]["mean_delta_nats"],
        f"dep wins {adv}/{len(rows)}")


def identity_drift(start, now):
    """Schema-v4 completion re-check (pure, testable): names of the
    identity components that moved between battery start and completion.
    ANY non-empty result forbids publishing gate-eligible evidence —
    the record would not describe what executed."""
    return [k for k in ("source_clean", "source_tree_hash",
                        "harness_hash", "env_fingerprint")
            if start.get(k) != now.get(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-Coder-0.5B")
    ap.add_argument("--allow-non-cuda", action="store_true",
                    help="device override for local smokes; output is "
                         "marked gate-ineligible")
    ap.add_argument("--dev-dirty", action="store_true",
                    help="run on a dirty source tree (dev only); output is "
                         "marked gate-ineligible")
    args = ap.parse_args()
    device = ("cuda" if torch.cuda.is_available() else
              "mps" if torch.backends.mps.is_available() else "cpu")
    if device != "cuda" and not args.allow_non_cuda:
        LOG("BATTERY-INCOMPLETE: CUDA required for gate results "
            "(--allow-non-cuda for local smokes)")
        sys.exit(1)
    from provenance import source_clean
    clean = source_clean()
    # device override and dirty-source are SEPARATE escapes: a CUDA box
    # with a dirty tree must still refuse unless --dev-dirty (review fix)
    if not clean and not args.dev_dirty:
        LOG("BATTERY-INCOMPLETE: dirty source outside results_v2 — the "
            "recorded source_tree_hash would not describe executed code "
            "(--dev-dirty for dev only; output becomes gate-ineligible)")
        sys.exit(1)
    gate_eligible = (device == "cuda") and clean
    if args.allow_non_cuda or args.dev_dirty:
        LOCAL_ONLY[0] = False  # dev escape may fetch; gate runs stay local
    # schema-v4 environment refusal (review blocker): every run WITHOUT
    # a dev escape must match the committed lock AND the write-once
    # freeze, checked BEFORE any tokenizer/model load — battery evidence
    # produced in an unlocked environment must never exist. Dev-escape
    # runs skip the refusal; their output is already gate-ineligible.
    if not (args.allow_non_cuda or args.dev_dirty):
        from provenance import env_matches_freeze, env_matches_lock
        lock_ok, lock_probs = env_matches_lock()
        frz_ok, frz_detail = env_matches_freeze()
        if not (lock_ok and frz_ok):
            LOG("BATTERY-INCOMPLETE: environment does not match the "
                f"committed lock/frozen record — lock: "
                f"{lock_probs[:4] or 'ok'}; freeze: {frz_detail} "
                "(fix_cluster syncs the lock; REFREEZE=1 adopts a new "
                "environment explicitly)")
            sys.exit(1)
    # START identities (review blocker): captured BEFORE tokenizer/model
    # load so the record describes what actually ran, recorded verbatim
    # in battery.json, and RE-CHECKED at completion — a battery whose
    # source/harness/environment moved mid-run never publishes
    # gate-eligible evidence.
    from provenance import (env_fingerprint, gpu_info, harness_hash,
                            head_commit, source_tree_hash)
    ident_start = dict(source_clean=clean,
                       source_tree_hash=source_tree_hash(),
                       harness_hash=harness_hash(),
                       env_fingerprint=env_fingerprint())
    tok = tok_of(args.model)  # pinned; rev_of raises when unpinned
    model, _, _, _ = load_text_model(args.model, device)
    os.makedirs(OUT, exist_ok=True)
    res = dict(model=args.model, device=device,
               gate_eligible=gate_eligible,
               config_notes={"starcoder2": "config bos/eos id 50256 exceeds "
                             "vocab_size 49152 (upstream quirk); harmless "
                             "here — no special tokens are ever added"},
               revision=rev_of(args.model),
               model_revisions={m: rev_of(m) for m in FAM_SMALL.values()},
               torch_version=torch.__version__,
               transformers_version=__import__("transformers").__version__,
               harness_commit=head_commit(),
               # evidence commits move HEAD; the SOURCE tree hash proves
               # "no source diff since measurement" (review fix). All
               # three identities are the EXACT pre-load start values
               # (re-checked at completion); preflight gates them
               # against the current state, so battery and cells share
               # one measurement environment; GPU/driver informational
               source_tree_hash=ident_start["source_tree_hash"],
               harness_hash=ident_start["harness_hash"],
               env_fingerprint=ident_start["env_fingerprint"],
               **gpu_info())
    errors = []
    for fn in (item_A, item_B, item_C, item_D, item_E):
        try:
            fn(model, tok, device, res)
        except Exception as e:
            res[fn.__name__ + "_error"] = repr(e)
            errors.append(fn.__name__)
            LOG(f"[ERR] {fn.__name__}: {e!r}")
    # fail-closed (PREREG §7): errors or plumbing-invariant breaches exit 1
    # A_fixed_chunk_semantics (re-spec, §13): the frozen pure verdict IS
    # the gate — coverage/finiteness/dispatch/chunk all fail closed
    A = res.get("A_fixed_chunk_semantics", {})
    a_ok = bool((A.get("verdict") or {}).get("ok"))
    b_ok = res.get("B_zero_rows", {}).get("conservation_ok", False)
    res["plumbing_pass"] = bool(not errors and a_ok and b_ok)
    # completion re-check (review blocker): the recorded start
    # identities must still hold NOW, or battery.json is never written
    # — a battery whose source cleanliness/tree hash, harness, or
    # environment moved mid-run publishes NO gate-eligible evidence
    # (dev-escape runs skip this; they are already gate-ineligible).
    if not (args.allow_non_cuda or args.dev_dirty):
        from provenance import source_clean as _sc
        drift = identity_drift(ident_start, dict(
            source_clean=_sc(),
            source_tree_hash=source_tree_hash(),
            harness_hash=harness_hash(),
            env_fingerprint=env_fingerprint()))
        if drift:
            LOG(f"BATTERY-INCOMPLETE: {', '.join(drift)} changed DURING "
                "the run — battery.json NOT written (the evidence would "
                "not describe what executed)")
            sys.exit(1)
        res["identities_unchanged_during_run"] = True
    else:
        res["identities_unchanged_during_run"] = False  # dev: unchecked
    bj = os.path.join(OUT, "battery.json")
    if os.path.exists(bj):  # evidence is never overwritten: a failed
        ts = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"  # survives rerun, collision-proof
        os.rename(bj, f"{bj}.quarantine-{ts}")
        LOG(f"[battery] preserved prior battery.json -> quarantine-{ts}")
    with open(bj, "w") as f:
        json.dump(res, f, indent=1)
    if not res["plumbing_pass"]:
        LOG(f"BATTERY-INCOMPLETE errors={errors} chunk_ok={a_ok} "
            f"conservation_ok={b_ok}")
        sys.exit(1)
    LOG("BATTERY-DONE")


if __name__ == "__main__":
    main()
