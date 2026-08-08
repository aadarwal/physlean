#!/usr/bin/env python3
"""Validity battery (PREREG §7, items A–E). Small by design: one small
model, minutes of GPU. Writes results_v2/battery/battery.json; review gates
G3 on these numbers."""
import argparse, json, math, os, random, re, subprocess, sys, time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from layout import token_spans, windows_of  # production layout, not a copy

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
def chunked_nll(model, ids, device, chunk=1024):
    """per-token NLL (nats, fp32) for ids[1:], growing KV cache."""
    T = len(ids)
    ids_t = torch.tensor(ids, dtype=torch.long)
    out = torch.empty(T - 1, dtype=torch.float32)
    past, done = None, 0
    while done < T - 1:
        take = min(chunk, T - done)
        r = model(input_ids=ids_t[done:done + take][None].to(device),
                  past_key_values=past, use_cache=True)
        past = r.past_key_values
        n_t = min(take, (T - 1) - done)
        tgt = ids_t[done + 1: done + 1 + n_t].to(device)
        lp = r.logits[0][:n_t].float().log_softmax(-1)
        out[done:done + n_t] = (-lp.gather(1, tgt[:, None])[:, 0]).cpu()
        done += take
    return out


@torch.no_grad()
def oneshot_nll(model, ids, device):
    ids_t = torch.tensor(ids, dtype=torch.long)[None].to(device)
    logits = model(input_ids=ids_t, use_cache=False).logits[0]
    tgt = ids_t[0][1:]
    nll = torch.empty(len(ids) - 1, dtype=torch.float32)
    for a in range(0, len(ids) - 1, 256):
        b = min(a + 256, len(ids) - 1)
        lp = logits[a:b].float().log_softmax(-1)
        nll[a:b] = (-lp.gather(1, tgt[a:b, None])[:, 0]).cpu()
    return nll


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


def item_A(model, tok, device, res):
    """Chunk-vs-one-shot equality PER ARCHITECTURE (per review: the risky
    paths are Qwen3.5 hybrid attention/cache and StarCoder2 sliding window,
    not the vanilla path), plus loader-class / text-param-count assert."""
    text = open(os.path.join(BASE, "data/streams/mathlib/full_topo.txt"),
                encoding="utf-8").read()[:120000]
    out = {}
    # 8192 tokens: spans StarCoder2's 4096 sliding-window boundary and
    # exercises hybrid-attention state well past one chunk (per review;
    # 2048 would never enter the risky cache regime)
    for fam, mid in FAM_SMALL.items():
        tk = tok_of(mid)
        ids = tk(text, add_special_tokens=False)["input_ids"][:8192]
        m2, cls, nparams, attn = load_text_model(mid, device)
        a = chunked_nll(m2, ids, device, chunk=512)
        b = oneshot_nll(m2, ids, device)
        d = (a - b).abs()
        lo, hi = PARAM_RANGES[fam]
        out[fam] = dict(model=mid, revision=rev_of(mid), cls=cls,
                        n_params=nparams, attn=attn,
                        n=len(ids), mean_abs_delta_nats=float(d.mean()),
                        p99_abs_delta_nats=float(d.quantile(0.99)),
                        max_abs_delta_nats=float(d.max()),
                        mean_nll=float(b.mean()),
                        class_ok=("ForCausalLM" in cls
                                  or "ForConditionalGeneration" in cls),
                        param_sane=lo <= nparams <= hi)
        del m2
        if device == "cuda":
            torch.cuda.empty_cache()
        LOG(f"A[{fam}]:", out[fam]["cls"], f"{nparams/1e9:.2f}B",
            "meanΔ", out[fam]["mean_abs_delta_nats"],
            "p99Δ", out[fam]["p99_abs_delta_nats"])
    res["A_chunk_equality"] = dict(
        families=out,
        mean_abs_delta_nats=max(v["mean_abs_delta_nats"]
                                for v in out.values()),
        p99_abs_delta_nats=max(v["p99_abs_delta_nats"]
                               for v in out.values()),
        all_class_ok=all(v["class_ok"] and v["param_sane"]
                         for v in out.values()))
    LOG("A: worst meanΔ", res["A_chunk_equality"]["mean_abs_delta_nats"],
        "worst p99Δ", res["A_chunk_equality"]["p99_abs_delta_nats"])



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
    corpora = ["physlib", "mathlib", "qutip", "sympy", "geant4", "arxiv_old"]
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
    probe = "a∀b ⟨x,y⟩ ↦ α → β"
    probe_out, probe_ok = {}, True
    for fam, mid in FAM_SMALL.items():
        enc = tok_of(mid)(probe, add_special_tokens=False,
                          return_offsets_mapping=True)
        pl, pg = token_spans(probe, enc["offset_mapping"])
        ok = sum(pl) == len(probe.encode())
        probe_ok = probe_ok and ok
        probe_out[fam] = dict(tokens=len(pl),
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


def item_E(model, tok, device, res):
    """Dependency vs equal-BYTE random context, scored on the
    POST-FIRST-DECLARATION SUFFIX of each target file (the accurate name:
    everything from the first declaration onward — headers/imports never
    scored; a per-declaration body split is v2's job). Targets are
    seeded-sampled from all eligible files; random context is drawn
    WITHOUT replacement from eligible non-target/non-dependency files
    (with-replacement could duplicate files, which dependency context
    cannot — review fix), and insufficiency is recorded, never padded."""
    imp = re.compile(r"^import\s+([A-Za-z0-9_.]+)", re.M)
    root = os.path.join(BASE, "corpora/physlib")
    mods = {}
    for dp, _, ns in os.walk(os.path.join(root, "Physlib")):
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
    pairs = eligible[:8]
    res.setdefault("E_meta", {})["n_eligible_targets"] = len(eligible)
    rows = []
    skipped = []
    MAX_CTX_TOK = 8192
    for p, deps, body in pairs:
        dep_full = "".join(open(d, encoding="utf-8").read() for d in deps)
        pool = [q for q in allpaths if q != p and q not in deps]
        rng.shuffle(pool)          # WITHOUT replacement
        rand_full = ""
        need = len(dep_full.encode()) + 40000
        for q in pool:
            if len(rand_full.encode()) >= need:
                break
            rand_full += open(q, encoding="utf-8").read()
        if len(rand_full.encode()) < len(dep_full.encode()):
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
        rows=rows, dep_wins=adv, n=len(rows),
        skipped_insufficient_pool=skipped,
        mean_delta_nats=float(sum(r["rand"] - r["dep"] for r in rows)
                              / max(len(rows), 1)))
    LOG("E:", res["E_dep_vs_random_context"]["mean_delta_nats"],
        f"dep wins {adv}/{len(rows)}")


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
    tok = tok_of(args.model)  # pinned; rev_of raises when unpinned
    model, _, _, _ = load_text_model(args.model, device)
    from provenance import source_tree_hash, head_commit
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
               # "no source diff since measurement" (review fix)
               source_tree_hash=source_tree_hash())
    errors = []
    for fn in (item_A, item_B, item_C, item_D, item_E):
        try:
            fn(model, tok, device, res)
        except Exception as e:
            res[fn.__name__ + "_error"] = repr(e)
            errors.append(fn.__name__)
            LOG(f"[ERR] {fn.__name__}: {e!r}")
    # fail-closed (PREREG §7): errors or plumbing-invariant breaches exit 1
    A = res.get("A_chunk_equality", {})
    a_ok = (A.get("mean_abs_delta_nats", 9) < 5e-3
            and A.get("p99_abs_delta_nats", 9) < 5e-2
            and A.get("all_class_ok", False))
    b_ok = res.get("B_zero_rows", {}).get("conservation_ok", False)
    res["plumbing_pass"] = bool(not errors and a_ok and b_ok)
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
