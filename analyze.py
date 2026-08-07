#!/usr/bin/env python3
"""Fit predictability scaling curves from per-position NLL dumps.

Model: BPB(c) = A * c^(-beta) + Linf   (c = in-context bytes of codebase)
  beta = how fast predictability improves as more of the codebase is in context
  Linf = irreducible bits-per-byte at infinite context (at this model scale)
Bootstrap over eval chunks for 95% CIs. 1 token == 1 byte by construction.
"""
import json, os
import numpy as np
from scipy.optimize import curve_fit

BASE = os.environ.get("PHYSLEAN_BASE", os.path.dirname(os.path.abspath(__file__)))
LN2 = np.log(2.0)
CORPORA = ["physlib", "mathlib", "qutip", "sympy"]

def load(path):
    d = np.genfromtxt(path, delimiter=",", skip_header=1)
    return d[:, 0].astype(int), d[:, 1], d[:, 3] / LN2  # chunk, ctx_len, bits/byte

def binned(ctx, bpb, edges):
    mids, means, ns = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (ctx >= lo) & (ctx < hi)
        if m.sum() > 50:
            mids.append(np.sqrt(lo * (hi - 1)))
            means.append(bpb[m].mean())
            ns.append(int(m.sum()))
    return np.array(mids), np.array(means), np.array(ns)

def powerlaw(c, A, beta, Linf):
    return A * np.power(c, -beta) + Linf

def fit(mids, means, ns):
    try:
        p, _ = curve_fit(powerlaw, mids, means, p0=[2.0, 0.3, 1.0],
                         sigma=1.0 / np.sqrt(ns), bounds=([0, 0, 0], [20, 2, 8]),
                         maxfev=20000)
        return p
    except Exception:
        return [np.nan] * 3

def analyze(path, n_boot=200, seed=7):
    chunk, ctx, bpb = load(path)
    edges = np.array([1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048])
    mids, means, ns = binned(ctx, bpb, edges)
    A, beta, Linf = fit(mids, means, ns)
    rng = np.random.default_rng(seed)
    uc = np.unique(chunk)
    idx_by_chunk = {c: np.flatnonzero(chunk == c) for c in uc}
    boots = []
    for _ in range(n_boot):
        sel = rng.choice(uc, size=len(uc), replace=True)
        ii = np.concatenate([idx_by_chunk[c] for c in sel])
        m2, v2, n2 = binned(ctx[ii], bpb[ii], edges)
        boots.append(fit(m2, v2, n2))
    boots = np.array([b for b in boots if not np.isnan(b[0])])
    lo, hi = (np.percentile(boots, [2.5, 97.5], axis=0) if len(boots) > 10
              else (np.full(3, np.nan), np.full(3, np.nan)))
    gain = (means[mids < 3].mean() - means[-1]) if len(means) > 3 else np.nan
    return dict(
        n_tokens=int(len(ctx)), n_chunks=int(len(uc)),
        overall_bpb=float(bpb.mean()),
        bins=dict(mid=mids.tolist(), bpb=means.tolist(), n=ns.tolist()),
        fit=dict(A=float(A), beta=float(beta), Linf=float(Linf)),
        ci95=dict(A=[float(lo[0]), float(hi[0])], beta=[float(lo[1]), float(hi[1])],
                  Linf=[float(lo[2]), float(hi[2])]),
        context_gain_bpb=float(gain),
    )

if __name__ == "__main__":
    out = {}
    for c in CORPORA:
        p = os.path.join(BASE, "results", f"{c}.csv")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            out[c] = analyze(p)
            f = out[c]["fit"]
            print(f"{c:8s}  BPB={out[c]['overall_bpb']:.3f}  beta={f['beta']:.3f} "
                  f"[{out[c]['ci95']['beta'][0]:.3f},{out[c]['ci95']['beta'][1]:.3f}]  "
                  f"Linf={f['Linf']:.3f}  gain={out[c]['context_gain_bpb']:.3f}")
        else:
            print(f"{c}: no results")
    bp = os.path.join(BASE, "results", "baseline_physlib.csv")
    if os.path.exists(bp) and os.path.getsize(bp) > 1000:
        out["baseline_untrained"] = analyze(bp, n_boot=20)
        print(f"baseline BPB={out['baseline_untrained']['overall_bpb']:.3f} (expect ~{np.log2(257):.2f})")
    with open(os.path.join(BASE, "results", "results.json"), "w") as f:
        json.dump(out, f, indent=2)
    print("wrote results/results.json")
