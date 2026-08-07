#!/usr/bin/env python3
"""Fit BPB(c) = A c^(-beta) + Linf per (model, corpus, stream-kind) cell.

Estimands per log-spaced context-byte bin:
  mean   : sum(nll)/ln2 / sum(bytes)      (byte-weighted, the BPB definition)
  median : median over tokens of nll/ln2/byte_len  (heavy-tail robustness)
Bootstrap CIs: resample WINDOWS (independent context episodes; the pilot's
"chunk") and, as robustness, DOCUMENTS. Bin-edge stability: refit with edges
shifted by x1.5. All fits share one edge set across corpora/models.
Outputs results_v2/fits.json + fits.csv (+ per-cell binned curves).
"""
import glob, gzip, json, math, os, re, sys
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

BASE = os.path.dirname(os.path.abspath(__file__))
DUMPS = os.path.join(BASE, "nll_dumps")
OUT = os.path.join(BASE, "results_v2")
LN2 = math.log(2)
EDGES = np.array([1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096,
                  8192, 16384, 32768, 65536, 131072, 262144], dtype=float)
EDGES_ALT = EDGES * 1.5
MIN_ROWS, MIN_UNITS = 40, 2
N_BOOT = 300


def powerlaw(c, A, beta, Linf):
    return A * np.power(c, -beta) + Linf


def binned(ctx, nll, blen, edges, how):
    mids, vals, ws = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (ctx >= lo) & (ctx < hi)
        n = int(m.sum())
        if n < MIN_ROWS:
            continue
        mids.append(math.sqrt(lo * hi))
        if how == "mean":
            vals.append(nll[m].sum() / LN2 / blen[m].sum())
            ws.append(blen[m].sum())
        else:
            vals.append(float(np.median(nll[m] / LN2 / blen[m])))
            ws.append(n)
    return np.array(mids), np.array(vals), np.array(ws)


def fit(mids, vals, ws):
    if len(mids) < 5:
        return [np.nan] * 3
    try:
        p, _ = curve_fit(powerlaw, mids, vals, p0=[2.0, 0.3, 0.7],
                         sigma=1.0 / np.sqrt(ws), bounds=([0, 0, 0], [50, 3, 10]),
                         maxfev=50000)
        return list(p)
    except Exception:
        return [np.nan] * 3


def boot(df, unit_col, edges, how, n=N_BOOT, seed=7):
    rng = np.random.default_rng(seed)
    units = df[unit_col].unique()
    groups = {u: g for u, g in df.groupby(unit_col)}
    out = []
    for _ in range(n):
        sel = rng.choice(units, size=len(units), replace=True)
        g = pd.concat([groups[u] for u in sel], ignore_index=True)
        m, v, w = binned(g.ctxb.values, g.nll.values, g.blen.values, edges, how)
        out.append(fit(m, v, w))
    arr = np.array([o for o in out if not np.isnan(o[0])])
    if len(arr) < 20:
        return None
    lo, hi = np.percentile(arr, [2.5, 97.5], axis=0)
    return dict(A=[lo[0], hi[0]], beta=[lo[1], hi[1]], Linf=[lo[2], hi[2]],
                n_ok=int(len(arr)))


def analyze_cell(path):
    df = pd.read_csv(path)
    df = df[df.blen > 0]
    res = dict(n_rows=int(len(df)), n_windows=int(df.win.nunique()),
               n_docs=int(df[df.doc >= 0].doc.nunique()),
               overall_bpb=float(df.nll.sum() / LN2 / df.blen.sum()),
               bytes_scored=int(df.blen.sum()))
    for how in ("mean", "median"):
        m, v, w = binned(df.ctxb.values, df.nll.values, df.blen.values,
                         EDGES, how)
        A, beta, Linf = fit(m, v, w)
        entry = dict(A=A, beta=beta, Linf=Linf,
                     bins=dict(mid=m.tolist(), bpb=v.tolist(),
                               w=[float(x) for x in w]))
        if how == "mean":
            ma, va, wa = binned(df.ctxb.values, df.nll.values, df.blen.values,
                                EDGES_ALT, how)
            entry["alt_edges"] = dict(zip(("A", "beta", "Linf"),
                                          fit(ma, va, wa)))
            entry["ci_window"] = boot(df, "win", EDGES, how)
            if res["n_docs"] > 3:
                entry["ci_doc"] = boot(df[df.doc >= 0], "doc", EDGES, how)
        res[how] = entry
    lo = df.ctxb < 4
    hi = df.ctxb >= EDGES[-4]
    if lo.sum() > 20 and hi.sum() > 20:
        res["context_gain_bpb"] = float(
            df.nll[lo].sum() / LN2 / df.blen[lo].sum()
            - df.nll[hi].sum() / LN2 / df.blen[hi].sum())
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    cells = {}
    rows = []
    for path in sorted(glob.glob(os.path.join(DUMPS, "*.csv.gz"))):
        meta_p = path + ".meta.json"
        if not os.path.exists(meta_p):
            continue
        name = os.path.basename(path)[:-7]
        try:
            short, corpus, kind = name.split("__", 2)
        except ValueError:
            continue
        try:
            res = analyze_cell(path)
        except Exception as e:
            print(f"[err] {name}: {e}", file=sys.stderr)
            continue
        res["meta"] = json.load(open(meta_p))
        cells[name] = res
        f = res["mean"]
        ci = f.get("ci_window") or {}
        rows.append(dict(
            model=short, corpus=corpus, kind=kind,
            bpb=round(res["overall_bpb"], 4),
            A=round(f["A"], 3) if not math.isnan(f["A"]) else None,
            beta=round(f["beta"], 4) if not math.isnan(f["beta"]) else None,
            beta_lo=round(ci.get("beta", [np.nan] * 2)[0], 4) if ci else None,
            beta_hi=round(ci.get("beta", [np.nan] * 2)[1], 4) if ci else None,
            Linf=round(f["Linf"], 4) if not math.isnan(f["Linf"]) else None,
            Linf_lo=round(ci.get("Linf", [np.nan] * 2)[0], 4) if ci else None,
            Linf_hi=round(ci.get("Linf", [np.nan] * 2)[1], 4) if ci else None,
            beta_median=round(res["median"]["beta"], 4)
                if not math.isnan(res["median"]["beta"]) else None,
            beta_altedges=round(f["alt_edges"]["beta"], 4)
                if not math.isnan(f["alt_edges"]["beta"]) else None,
            gain=round(res.get("context_gain_bpb", float("nan")), 4),
        ))
        print(f"{name:60s} bpb={res['overall_bpb']:.3f} "
              f"beta={f['beta']:.3f} Linf={f['Linf']:.3f}", flush=True)
    with open(os.path.join(OUT, "fits.json"), "w") as f:
        json.dump(cells, f, indent=1)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "fits.csv"), index=False)
    print(f"[done] {len(cells)} cells -> results_v2/fits.{{json,csv}}")


if __name__ == "__main__":
    main()
