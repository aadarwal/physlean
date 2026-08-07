#!/usr/bin/env python3
"""Phase 2: from-scratch byte-level GPT per language (nanoGPT-style, MPS).

1 token = 1 byte (vocab 256), so val loss / ln2 IS bits-per-byte with zero
tokenizer confound (HANDOFF §5.3). Matched data budgets come from
prep_pools.py. Final eval writes a per-position NLL dump in the same CSV
schema as eval_incontext.py, so analyze_v2.py fits the from-scratch
in-context curves too (ctxb == position, 1 byte per token).
"""
import argparse, gzip, json, math, os, sys, time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

BASE = os.path.dirname(os.path.abspath(__file__))

SIZES = {  # name -> (n_layer, d_model, n_head, lr)
    "10m": (8, 320, 8, 3e-3),
    "30m": (10, 512, 8, 1.5e-3),
    "100m": (12, 768, 12, 6e-4),
    "300m": (24, 1024, 16, 3e-4),
}


class Attn(nn.Module):
    def __init__(self, d, h):
        super().__init__()
        self.qkv = nn.Linear(d, 3 * d)
        self.out_proj = nn.Linear(d, d)
        self.h = h

    def forward(self, x):
        B, T, D = x.shape
        q, k, v = self.qkv(x).chunk(3, dim=-1)
        q, k, v = (t.view(B, T, self.h, D // self.h).transpose(1, 2)
                   for t in (q, k, v))
        a = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        return self.out_proj(a.transpose(1, 2).reshape(B, T, D))


class Block(nn.Module):
    def __init__(self, d, h, ctx):
        super().__init__()
        self.ln1 = nn.LayerNorm(d)
        self.attn = Attn(d, h)
        self.ln2 = nn.LayerNorm(d)
        self.mlp = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(),
                                 nn.Linear(4 * d, d))

    def forward(self, x):
        x = x + self.attn(self.ln1(x))
        return x + self.mlp(self.ln2(x))


class ByteGPT(nn.Module):
    def __init__(self, n_layer, d, h, ctx, vocab=256, grad_ckpt=True):
        super().__init__()
        self.tok = nn.Embedding(vocab, d)
        self.pos = nn.Embedding(ctx, d)
        self.blocks = nn.ModuleList(Block(d, h, ctx) for _ in range(n_layer))
        self.lnf = nn.LayerNorm(d)
        self.head = nn.Linear(d, vocab, bias=False)
        self.head.weight = self.tok.weight
        self.ctx = ctx
        # MPS SDPA training backward materializes S per layer; checkpointing
        # keeps only one layer's S live at a time (observed 28GB -> fits)
        self.grad_ckpt = grad_ckpt
        self.apply(self._init)
        for name, p in self.named_parameters():  # GPT-2 residual scaling
            if name.endswith("mlp.2.weight") or "out_proj.weight" in name:
                nn.init.normal_(p, std=0.02 / math.sqrt(2 * n_layer))

    @staticmethod
    def _init(m):
        if isinstance(m, (nn.Linear, nn.Embedding)):
            nn.init.normal_(m.weight, std=0.02)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.zeros_(m.bias)

    def forward(self, idx):
        T = idx.shape[1]
        x = self.tok(idx) + self.pos(torch.arange(T, device=idx.device))
        for b in self.blocks:
            if self.grad_ckpt and self.training and torch.is_grad_enabled():
                x = torch.utils.checkpoint.checkpoint(b, x, use_reentrant=False)
            else:
                x = b(x)
        return self.head(self.lnf(x))


def batches(data, ctx, mb, seed, device):
    n_win = (len(data) - 1) // ctx
    order = np.random.default_rng(seed).permutation(n_win)
    offs = np.arange(ctx + 1)
    for i in range(0, n_win - mb + 1, mb):
        starts = order[i:i + mb][:, None] * ctx
        xy = torch.from_numpy(data[starts + offs].astype(np.int64)).to(device)
        yield xy[:, :-1], xy[:, 1:]


@torch.no_grad()
def val_bpb(model, data, ctx, device, max_bytes=1_000_000, dump=None):
    model.eval()
    tot_nll, tot = 0.0, 0
    n_win = min((len(data) - 1) // ctx, max(1, max_bytes // ctx))
    fdump = gzip.open(dump, "wt") if dump is not None else None
    if fdump:
        fdump.write("win,doc,ctxb,blen,tok,nll\n")
    for w in range(n_win):
        x = torch.from_numpy(data[w * ctx:(w + 1) * ctx]
                             .astype(np.int64))[None].to(device)
        y = torch.from_numpy(data[w * ctx + 1:(w + 1) * ctx + 1]
                             .astype(np.int64))[None].to(device)
        logits = model(x)
        nll = F.cross_entropy(logits[0].float(), y[0], reduction="none")
        tot_nll += float(nll.sum())
        tot += nll.numel()
        if fdump:
            nl = nll.cpu().numpy()
            yl = y[0].cpu().numpy()
            fdump.write("\n".join(
                f"{w},-1,{p + 1},1,{yl[p]},{nl[p]:.5f}"
                for p in range(len(nl))) + "\n")
    if fdump:
        fdump.close()
    model.train()
    return tot_nll / math.log(2) / max(tot, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True)
    ap.add_argument("--size", choices=SIZES, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ctx", type=int, default=2048)
    ap.add_argument("--micro-batch", type=int, default=0)
    ap.add_argument("--step-tokens", type=int, default=49152)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-train-bytes", type=int, default=0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--out-tag", default="")
    args = ap.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else
                             "mps" if torch.backends.mps.is_available() else
                             "cpu")
    L, d, h, lr = SIZES[args.size]
    cuda = torch.cuda.is_available() and (args.device or "cuda") == "cuda"
    mb = args.micro_batch or (
        {"10m": 32, "30m": 24, "100m": 16, "300m": 8} if cuda else
        {"10m": 16, "30m": 8, "100m": 4, "300m": 2})[args.size]
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    train = np.fromfile(os.path.join(BASE, "data", "pools",
                                     f"{args.lang}_train.bin"), dtype=np.uint8)
    val = np.fromfile(os.path.join(BASE, "data", "pools",
                                   f"{args.lang}_val.bin"), dtype=np.uint8)
    if args.max_train_bytes:
        train = train[:args.max_train_bytes]

    model = ByteGPT(L, d, h, args.ctx,
                    grad_ckpt=(device == "mps")).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    run = f"scratch-{args.size}-{args.lang}-s{args.seed}{args.out_tag}"
    print(f"[{run}] params={n_params/1e6:.1f}M train={len(train)/1e6:.1f}MB "
          f"val={len(val)/1e6:.1f}MB device={device}", flush=True)

    accum = max(1, args.step_tokens // (mb * args.ctx))
    n_win = (len(train) - 1) // args.ctx
    steps_per_epoch = n_win // (mb * accum)
    total_steps = int(steps_per_epoch * args.epochs)
    warmup = max(10, int(0.02 * total_steps))
    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95),
                            weight_decay=0.1)

    def lr_at(s):
        if s < warmup:
            return lr * (s + 1) / warmup
        t = (s - warmup) / max(1, total_steps - warmup)
        return 0.1 * lr + 0.45 * lr * (1 + math.cos(math.pi * min(t, 1.0)))

    t0 = time.time()
    step = 0
    tok_seen = 0
    gen = batches(train, args.ctx, mb, args.seed, device)
    history = []
    model.train()
    while step < total_steps:
        for g in opt.param_groups:
            g["lr"] = lr_at(step)
        opt.zero_grad(set_to_none=True)
        loss_acc = 0.0
        for _ in range(accum):
            try:
                x, y = next(gen)
            except StopIteration:
                gen = batches(train, args.ctx, mb,
                              args.seed + 1000 + step, device)
                x, y = next(gen)
            with torch.autocast(device_type=device, dtype=torch.bfloat16):
                logits = model(x)
                loss = F.cross_entropy(logits.reshape(-1, 256), y.reshape(-1))
            (loss / accum).backward()
            loss_acc += float(loss) / accum
            tok_seen += x.numel()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        step += 1
        if step % 20 == 0 or step == total_steps:
            dt = time.time() - t0
            print(f"[{run}] step {step}/{total_steps} "
                  f"train_bpb={loss_acc / math.log(2):.3f} "
                  f"{tok_seen / dt:.0f} tok/s", flush=True)
        if step % max(1, total_steps // 6) == 0 or step == total_steps:
            v = val_bpb(model, val, args.ctx, device)
            history.append(dict(step=step, tokens=tok_seen, val_bpb=v))
            print(f"[{run}] VAL step {step} bpb={v:.4f}", flush=True)

    os.makedirs(os.path.join(BASE, "nll_dumps"), exist_ok=True)
    os.makedirs(os.path.join(BASE, "results_v2", "scratch"), exist_ok=True)
    os.makedirs(os.path.join(BASE, "ckpt"), exist_ok=True)
    dump = os.path.join(BASE, "nll_dumps", f"{run}__{args.lang}__val.csv.gz")
    final = val_bpb(model, val, args.ctx, device,
                    max_bytes=len(val), dump=dump)
    meta = dict(model=run, stream=f"pools/{args.lang}_val.bin",
                ctx_tokens=args.ctx, dtype="bf16-autocast", device=device,
                random_init=False, reset_per_doc=False,
                total_bytes=int(len(val)), n_tokens=int(len(val)),
                vocab_size=256, overall_bpb=final)
    json.dump(meta, open(dump + ".meta.json", "w"), indent=1)
    result = dict(run=run, lang=args.lang, size=args.size, seed=args.seed,
                  n_params=n_params, ctx=args.ctx, lr=lr,
                  train_bytes=int(len(train)), tokens_seen=tok_seen,
                  total_steps=total_steps, final_val_bpb=final,
                  history=history, wall_s=time.time() - t0)
    with open(os.path.join(BASE, "results_v2", "scratch", run + ".json"),
              "w") as f:
        json.dump(result, f, indent=1)
    torch.save(dict(model=model.state_dict(), config=dict(
        n_layer=L, d_model=d, n_head=h, ctx=args.ctx)),
        os.path.join(BASE, "ckpt", run + ".pt"))
    print(f"[{run}] FINAL val_bpb={final:.4f} ({time.time() - t0:.0f}s)",
          flush=True)


if __name__ == "__main__":
    main()
