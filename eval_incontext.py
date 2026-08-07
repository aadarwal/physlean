#!/usr/bin/env python3
"""Teacher-forced per-token NLL vs in-context bytes for one (model, stream).

Measurement semantics (HANDOFF §4, pitfalls §5.3/§5.4):
  - The stream is tokenized once (no special tokens). Each token's UTF-8 byte
    length is recovered from offsets; we assert sum(token bytes) == stream
    bytes, so bits-per-byte has no tokenizer confound.
  - The token sequence is cut into consecutive windows of --ctx-tokens.
    A window boundary is a hard context reset: the first target in a window
    is predicted from ~1 byte of context (that is the c->1 end of the curve).
  - Within a window the model is fed in chunks with a growing KV cache, so
    NLL at position p is exact full-attention teacher forcing over all p-1
    preceding tokens. Logits are log-softmaxed in fp32, in row slices, so
    peak memory stays bounded at 32k context.
  - --reset-per-doc tokenizes and windows each document separately
    (single-file ablation: no cross-file context at all).

Output: gzipped CSV  win,doc,ctxb,blen,tok,nll   (nll in nats)
  ctxb = in-context bytes preceding the target within its window.
Plus <out>.meta.json with model/config/throughput/sanity info.
"""
import argparse, gzip, json, math, os, sys, time

import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

LOG = lambda *a: print(*a, file=sys.stderr, flush=True)


def load_model(name, dtype, device, random_init=False):
    kw = {}
    try:
        cfg = AutoConfig.from_pretrained(name)
        if random_init:
            model = AutoModelForCausalLM.from_config(cfg)
        else:
            try:
                model = AutoModelForCausalLM.from_pretrained(name, dtype=dtype, **kw)
            except TypeError:
                model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=dtype, **kw)
    except Exception:
        raise
    model = model.to(device=device, dtype=dtype)
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    return model, cfg


def token_byte_lens(text, offsets):
    """UTF-8 byte length per token from char-offset mapping."""
    lens = []
    prev_end = 0
    for s, e in offsets:
        # fast tokenizers give contiguous char spans for byte-level BPE;
        # guard against gaps by charging any skipped chars to this token
        s = min(s, prev_end)
        lens.append(len(text[prev_end:e].encode("utf-8")) if e > prev_end
                    else 0)
        prev_end = max(prev_end, e)
    return lens


def doc_lookup(manifest_path):
    spans = []
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path) as f:
            for line in f:
                d = json.loads(line)
                spans.append((d["start"], d["end"], d["doc_id"]))
    return spans


def doc_of(byte_pos, spans, hint=[0]):
    i = hint[0]
    while i < len(spans) and byte_pos >= spans[i][1]:
        i += 1
    hint[0] = min(i, len(spans) - 1) if spans else 0
    if i < len(spans) and spans[i][0] <= byte_pos < spans[i][1]:
        return spans[i][2]
    return -1


@torch.no_grad()
def eval_window(model, ids, device, chunk, slice_rows=256):
    """ids: LongTensor [T]. Returns nll[T-1] (fp32 cpu) for targets ids[1:]."""
    T = ids.shape[0]
    nlls = torch.empty(T - 1, dtype=torch.float32)
    past = None
    done = 0  # number of tokens already fed
    while done < T - 1:
        take = min(chunk, T - done)  # feed up to chunk tokens
        inp = ids[done:done + take].unsqueeze(0).to(device)
        out = model(input_ids=inp, past_key_values=past, use_cache=True)
        past = out.past_key_values
        logits = out.logits[0]  # [take, V]
        # rows predict targets ids[done+1 .. done+take]; last row of the
        # final chunk has no target inside the window
        n_t = min(take, (T - 1) - done)
        tgt = ids[done + 1: done + 1 + n_t].to(device)
        for a in range(0, n_t, slice_rows):
            b = min(a + slice_rows, n_t)
            lp = logits[a:b].float().log_softmax(-1)
            nlls[done + a: done + b] = (
                -lp.gather(1, tgt[a:b, None])[:, 0]).cpu()
        del logits, out
        done += take
    del past
    if device == "mps":
        torch.mps.empty_cache()
    return nlls


def windows_of(n_tok, ctx, min_tail=1024):
    spans = [(s, min(s + ctx, n_tok)) for s in range(0, n_tok, ctx)]
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_tail:
        spans = spans[:-1]
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--stream", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctx-tokens", type=int, default=32768)
    ap.add_argument("--chunk", type=int, default=2048)
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--device", default=None)
    ap.add_argument("--reset-per-doc", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=0)
    ap.add_argument("--random-init", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    meta_path = args.out + ".meta.json"
    if os.path.exists(meta_path) and not args.force:
        LOG(f"[skip] {args.out} already complete")
        return

    device = args.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    dtype = getattr(torch, args.dtype)

    text = open(args.stream, encoding="utf-8").read()
    if args.max_bytes:
        text = text[:args.max_bytes]  # char-truncate; fine for smoke runs
    total_bytes = len(text.encode("utf-8"))
    spans = doc_lookup(args.stream.replace(".txt", ".manifest.jsonl"))

    tok = AutoTokenizer.from_pretrained(args.model)
    model, cfg = load_model(args.model, dtype, device, args.random_init)
    max_pos = getattr(cfg, "max_position_embeddings", None) or 1 << 20
    ctx = min(args.ctx_tokens, max_pos)
    LOG(f"[cfg] vocab={cfg.vocab_size} max_pos={max_pos} "
        f"sliding_window={getattr(cfg, 'sliding_window', None)} ctx={ctx}")

    t0 = time.time()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n_rows = 0
    sum_nll = 0.0
    sum_bytes = 0
    tok_count = 0

    def tokenize(s):
        enc = tok(s, add_special_tokens=False, return_offsets_mapping=True)
        lens = token_byte_lens(s, enc["offset_mapping"])
        nb = len(s.encode("utf-8"))
        assert sum(lens) == nb, f"byte accounting broke: {sum(lens)} != {nb}"
        return enc["input_ids"], lens

    segments = []  # (ids, byte_lens, seg_start_byte)
    if args.reset_per_doc and spans:
        by = text.encode("utf-8")
        for s, e, did in spans:
            if s >= total_bytes:
                break
            seg = by[s:min(e, total_bytes)].decode("utf-8", errors="ignore")
            segments.append((*tokenize(seg), s))
    else:
        segments.append((*tokenize(text), 0))

    with gzip.open(args.out, "wt") as fout:
        fout.write("win,doc,ctxb,blen,tok,nll\n")
        win_id = 0
        for ids_list, blens, seg_start in segments:
            ids = torch.tensor(ids_list, dtype=torch.long)
            cum = [0]
            for L in blens:
                cum.append(cum[-1] + L)  # cum[i] = bytes of tokens[:i]
            for ws, we in windows_of(len(ids), ctx):
                nll = eval_window(model, ids[ws:we], device, args.chunk)
                rows = []
                for j in range(len(nll)):  # target index = ws + 1 + j
                    p = ws + 1 + j
                    ctxb = cum[p] - cum[ws]
                    blen = blens[p]
                    byte_pos = seg_start + cum[p]
                    did = doc_of(byte_pos, spans)
                    rows.append(f"{win_id},{did},{ctxb},{blen},{ids_list[p]},"
                                f"{nll[j].item():.5f}")
                    sum_nll += float(nll[j])
                    sum_bytes += blen
                fout.write("\n".join(rows) + "\n")
                n_rows += len(rows)
                tok_count += we - ws
                dt = time.time() - t0
                LOG(f"[win {win_id}] {we - ws} tok | total {tok_count} tok "
                    f"in {dt:.0f}s = {tok_count / dt:.0f} tok/s | "
                    f"bpb so far {sum_nll / math.log(2) / max(sum_bytes, 1):.4f}")
                win_id += 1

    bpb = sum_nll / math.log(2) / max(sum_bytes, 1)
    per_tok_nats = sum_nll / max(n_rows, 1)
    meta = dict(model=args.model, stream=args.stream, ctx_tokens=ctx,
                chunk=args.chunk, dtype=args.dtype, device=device,
                random_init=args.random_init, reset_per_doc=args.reset_per_doc,
                total_bytes=total_bytes, n_tokens=tok_count, n_scored=n_rows,
                bytes_scored=sum_bytes, bytes_per_token=total_bytes / max(tok_count, 1),
                overall_bpb=bpb, per_token_nats=per_tok_nats,
                vocab_size=cfg.vocab_size,
                sliding_window=getattr(cfg, "sliding_window", None),
                max_position_embeddings=max_pos,
                wall_s=time.time() - t0)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=1)
    LOG(f"[done] bpb={bpb:.4f} per-token={per_tok_nats:.3f} nats "
        f"({n_rows} scored, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
