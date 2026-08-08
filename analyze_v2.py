#!/usr/bin/env python3
"""Analyzer v3 (PREREG §6). Per cell:
  - source-span group collapse with conservation raises (never silent),
  - log-spaced context-byte bins carrying n_windows / n_docs / n_groups /
    bytes (sample size is windows+docs, never tokens),
  - cell classification: quantitative (>=15 windows AND >=30 docs) vs
    descriptive (no CIs, no fits); bins enter fits only with >=8 windows,
  - descriptive stats first: context gain over the first common decade
    [16,256) minus the top common bin; flattening point c_hat(eps),
    eps=0.05 (0.10 sensitivity) — descriptive, NOT minimal-sufficient-ctx,
  - frozen fit gate: power law fit on bins <=8KiB must predict the
    contiguous held-out range (8KiB, top] with mean relative error < 5%
    AND not be beaten there by saturating-exponential or log-linear;
    equal-weight primary, sqrt-byte secondary, byte-weight sensitivity;
    x1.5 bin-edge stability,
  - window-level bootstrap CIs (doc-level as robustness) via per-unit bin
    aggregates (fast) for gain and accepted-fit params,
  - clean-target masking: full_topo cells of code corpora re-analyzed on
    post-cutoff target docs only (context stays natural) per cutoff,
  - XL cells labeled unmatched (fit-stability only),
  - production-identity gate per dump (reuses run_phase1.cell_done; dev
    dumps analyzed only under --allow-dev and never gate anything),
  - dump/meta reconciliation (rows + bytes vs n_scored/bytes_scored),
  - sentinel phase-pair analysis (oriented, byte-weighted primary),
  - errors collected per cell AND from phase pairing; nonzero exit on any.
Outputs results_v2/fits.json + fits.csv + comparisons.json +
phase_pairs.json.
"""
import glob, json, math, os, sys
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

BASE = os.path.dirname(os.path.abspath(__file__))
DUMPS = os.path.join(BASE, "nll_dumps")
STREAMS = os.path.join(BASE, "data", "streams")
OUT = os.path.join(BASE, "results_v2")
LN2 = math.log(2)
EDGES = np.array([1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096,
                  8192, 16384, 32768, 65536, 131072, 262144, 524288], float)
EDGES_ALT = EDGES * 1.5
MIN_BIN_WINDOWS = 8          # a bin below this never enters a fit
MIN_CELL_WINDOWS = 15        # cell classification floors (PREREG §6)
MIN_CELL_DOCS = 30
HOLDOUT_SPLIT = 8192.0       # fit on <=8KiB, predict (8KiB, top]
HOLDOUT_MAX_RELERR = 0.05    # frozen before data
EPS_FLAT = (0.05, 0.10)      # c_hat(eps) primary + sensitivity
N_BOOT = 300

try:
    from prep_streams import CUTOFFS
except Exception:
    CUTOFFS = {"c2024_11": "2024-11-12", "c2025_04": "2025-04-29",
               "c2026_02": "2026-03-01"}
MASKABLE = {"physlib", "mathlib", "qutip", "sympy", "geant4"}  # no LaTeX


# ---------- group collapse (conservation-raising) ----------

def collapse_groups(df):
    """One row per source-span group (PREREG §4): NLL and bytes sum within
    (win, grp); ctxb/doc taken from the group opener. Dumps without a grp
    column (byte-level trainers: 1 token = 1 byte) are already grouped."""
    if "grp" not in df.columns:
        return df
    agg = df.groupby(["win", "grp"], sort=False).agg(
        doc=("doc", "first"), ndoc=("doc", "nunique"),
        ctxb=("ctxb", "first"),
        blen=("blen", "sum"), nll=("nll", "sum")).reset_index()
    if abs(agg.nll.sum() - df.nll.sum()) > 1e-6 * max(df.nll.sum(), 1):
        raise AssertionError("NLL not conserved under group collapse")
    if int(agg.blen.sum()) != int(df.blen.sum()):
        raise AssertionError("bytes not conserved under group collapse")
    if (agg.blen <= 0).any():
        raise AssertionError("zero-byte source-span group after collapse")
    # the EVALUATOR attributes docs per group over the charged byte
    # interval (schema v3): rows of one group must agree — disagreement
    # here is a harness bug, not data to be papered over
    if (agg.ndoc > 1).any():
        raise AssertionError(
            f"{int((agg.ndoc > 1).sum())} groups with mixed doc ids — "
            "evaluator group-doc attribution violated (schema v3)")
    agg = agg.drop(columns="ndoc")
    return agg


# ---------- binning with unit accounting ----------

def bin_table(df, edges):
    """Per-bin aggregates + per-(window,bin) and per-(doc,bin) pivots for
    fast unit-level bootstrap."""
    idx = np.digitize(df.ctxb.values, edges) - 1
    ok = (idx >= 0) & (idx < len(edges) - 1)
    d = df[ok].copy()
    d["bin"] = idx[ok]
    g = d.groupby("bin")
    bins = pd.DataFrame(dict(
        mid=np.sqrt(edges[g.nll.count().index] *
                    edges[g.nll.count().index + 1]),
        nll=g.nll.sum(), bytes=g.blen.sum(), n_groups=g.nll.count(),
        n_windows=g.win.nunique(), n_docs=d[d.doc >= 0].groupby("bin")
        .doc.nunique().reindex(g.nll.count().index).fillna(0).astype(int)))
    bins["bpb"] = bins.nll / LN2 / bins.bytes
    med = d.groupby("bin").apply(
        lambda x: float(np.median(x.nll / LN2 / x.blen)),
        include_groups=False)
    bins["bpb_median"] = med
    wpiv_n = d.pivot_table(index="win", columns="bin", values="nll",
                           aggfunc="sum", fill_value=0.0)
    wpiv_b = d.pivot_table(index="win", columns="bin", values="blen",
                           aggfunc="sum", fill_value=0)
    dd = d[d.doc >= 0]
    dpiv_n = dd.pivot_table(index="doc", columns="bin", values="nll",
                            aggfunc="sum", fill_value=0.0)
    dpiv_b = dd.pivot_table(index="doc", columns="bin", values="blen",
                            aggfunc="sum", fill_value=0)
    return bins, (wpiv_n, wpiv_b), (dpiv_n, dpiv_b)


# ---------- functional forms + frozen holdout gate ----------

def f_pow(c, A, b, L):
    return A * np.power(c, -b) + L


def f_exp(c, A, tau, L):
    return A * np.exp(-c / tau) + L


def f_log(c, a, b):
    return a - b * np.log(c)


def _fit(f, x, y, p0, bounds, w=None):
    try:
        p, _ = curve_fit(f, x, y, p0=p0, bounds=bounds, maxfev=50000,
                         sigma=None if w is None else 1.0 / np.sqrt(w))
        return list(p)
    except Exception:
        return None


def gated_fits(bins):
    """Fit on mids <= HOLDOUT_SPLIT, judge on the contiguous held-out
    range. Fit and holdout supports must each be TRULY CONTIGUOUS runs of
    valid bins (review fix: gaps from failed middle bins silently changed
    the preregistered support). Returns acceptance + alternatives."""
    valid_idx = list(bins[bins.n_windows >= MIN_BIN_WINDOWS].index)
    if valid_idx and valid_idx != list(range(valid_idx[0],
                                             valid_idx[-1] + 1)):
        return dict(accepted=False,
                    reason="non-contiguous valid-bin support",
                    valid_bins=valid_idx)
    fitb = bins[(bins.n_windows >= MIN_BIN_WINDOWS)]
    lo = fitb[fitb.mid <= HOLDOUT_SPLIT]
    hi = fitb[fitb.mid > HOLDOUT_SPLIT]
    rec = dict(n_fit_bins=int(len(lo)), n_holdout_bins=int(len(hi)))
    if len(lo) < 5 or len(hi) < 2:
        rec["accepted"] = False
        rec["reason"] = "insufficient bins for the frozen holdout gate"
        return rec
    x, y = lo.mid.values, lo.bpb.values
    xh, yh = hi.mid.values, hi.bpb.values
    cands = {
        "powerlaw": (_fit(f_pow, x, y, [2, .3, .7],
                          ([0, 0, 0], [50, 3, 10])), f_pow),
        "sat_exp": (_fit(f_exp, x, y, [2, 1000, .7],
                         ([0, 1, 0], [50, 1e6, 10])), f_exp),
        "log_linear": (_fit(f_log, x, y, [3, .3],
                            ([-50, -10], [50, 10])), f_log),
    }
    errs = {}
    for name, (p, f) in cands.items():
        if p is None:
            errs[name] = None
            continue
        pred = f(xh, *p)
        errs[name] = float(np.mean(np.abs(pred - yh) / np.maximum(yh, 1e-9)))
    rec["holdout_relerr"] = errs
    pe = errs.get("powerlaw")
    others = [errs.get("sat_exp"), errs.get("log_linear")]
    # BOTH alternatives must have fit; an unfittable alternative is an
    # unfalsified comparison, not a win (review fix)
    accepted = (pe is not None and pe < HOLDOUT_MAX_RELERR
                and all(e is not None for e in others)
                and all(pe <= e for e in others))
    rec["accepted"] = bool(accepted)
    if cands["powerlaw"][0] is not None:
        A, b, L = cands["powerlaw"][0]
        rec["powerlaw_equal_weight"] = dict(A=A, beta=b, Linf=L)
        # secondary weightings (reported regardless; sensitivity)
        p2 = _fit(f_pow, x, y, [2, .3, .7], ([0, 0, 0], [50, 3, 10]),
                  w=np.sqrt(lo.bytes.values))
        p3 = _fit(f_pow, x, y, [2, .3, .7], ([0, 0, 0], [50, 3, 10]),
                  w=lo.bytes.values)
        rec["powerlaw_sqrt_bytes"] = (dict(zip(("A", "beta", "Linf"), p2))
                                      if p2 else None)
        rec["powerlaw_bytes_sensitivity"] = (
            dict(zip(("A", "beta", "Linf"), p3)) if p3 else None)
    if not accepted:
        rec.setdefault("reason", "failed frozen holdout gate")
    return rec


# ---------- descriptive statistics ----------

def descriptive(bins):
    out = {}
    valid = bins[bins.n_windows >= MIN_BIN_WINDOWS]
    decade = valid[(valid.mid >= 16) & (valid.mid < 256)]
    if len(decade) and len(valid):
        low = float(decade.nll.sum() / LN2 / decade.bytes.sum())
        topv = float(valid.bpb.iloc[-1])
        out["context_gain_bpb"] = low - topv
        out["gain_low_bpb"], out["gain_top_bpb"] = low, topv
        # suffix-stable flattening (review fix: 'first lucky bin' counted
        # even when later bins rebound): earliest valid bin after which
        # ALL later valid bins stay within eps of the top value
        v = valid.reset_index(drop=True)
        for eps in EPS_FLAT:
            # ABSOLUTE distance to the top reference (review fix: one-sided
            # accepted bins far BELOW top as 'flat' on rebounding curves)
            within = ((v.bpb - topv).abs() <= eps).values
            c = None
            for i in range(len(v)):
                if within[i:].all():
                    c = float(v.mid.iloc[i])
                    break
            out[f"c_hat_eps{eps}"] = c
    return out


def boot_stats(piv_n, piv_b, bins, fit_accepted, seed=7):
    """Unit-level bootstrap on precomputed per-unit bin aggregates.
    Bin support is FROZEN at the point estimate's valid bins (review fix:
    letting support vary per replicate mixes support noise into the CI):
    the decade bins, the top bin, and the fit bins are those of the
    observed cell; replicates with zero bytes in a frozen bin are skipped
    and counted."""
    rng = np.random.default_rng(seed)
    U = len(piv_n)
    if U < 4:
        return None
    valid = bins[bins.n_windows >= MIN_BIN_WINDOWS]
    dec_bins = set(valid[(valid.mid >= 16) & (valid.mid < 256)].index)
    top_bin = valid.index[-1] if len(valid) else None
    fit_bins = [i for i in valid.index if valid.mid[i] <= HOLDOUT_SPLIT]
    if not dec_bins or top_bin is None:
        return dict(n_units=int(U), note="no frozen support")
    Np, Bp = piv_n.values, piv_b.values
    cols = list(piv_n.columns.values)
    col_of = {b: cols.index(b) for b in
              set(dec_bins) | {top_bin} | set(fit_bins) if b in cols}
    mids_fit = np.array([np.sqrt(EDGES[b] * EDGES[b + 1])
                         for b in fit_bins if b in col_of])
    gains, betas, skipped = [], [], 0
    for _ in range(N_BOOT):
        sel = rng.integers(0, U, U)
        nll = Np[sel].sum(0)
        byt = Bp[sel].sum(0)
        dc = [col_of[b] for b in dec_bins if b in col_of]
        tc = col_of.get(top_bin)
        if tc is None or byt[tc] <= 0 or sum(byt[c] for c in dc) <= 0:
            skipped += 1
            continue
        low = sum(nll[c] for c in dc) / LN2 / sum(byt[c] for c in dc)
        gains.append(low - nll[tc] / LN2 / byt[tc])
        if fit_accepted and len(mids_fit) >= 5:
            fc = [col_of[b] for b in fit_bins if b in col_of]
            bb = np.array([byt[c] for c in fc], float)
            if (bb > 0).all():
                yy = np.array([nll[c] for c in fc]) / LN2 / bb
                p = _fit(f_pow, mids_fit, yy, [2, .3, .7],
                         ([0, 0, 0], [50, 3, 10]))
                if p:
                    betas.append(p[1])
    out = dict(n_units=int(U), skipped_reps=int(skipped))
    if len(gains) > 20:
        lo, hi = np.percentile(gains, [2.5, 97.5])
        out["gain_ci95"] = [float(lo), float(hi)]
    if fit_accepted and len(betas) > 20:
        lo, hi = np.percentile(betas, [2.5, 97.5])
        out["beta_ci95"] = [float(lo), float(hi)]
    return out


# ---------- per-cell analysis ----------

def analyze_frame(df, unmatched=False, extra_quant_ok=True,
                  ineligible_reason=None):
    """extra_quant_ok carries protocol-specific eligibility (e.g. masking
    floors) INTO classification BEFORE any fitting, so a floor-failing
    variant can never carry an accepted fit or CIs (review fix)."""
    res = dict(n_groups=int(len(df)), n_windows=int(df.win.nunique()),
               n_docs=int(df[df.doc >= 0].doc.nunique()),
               bytes_scored=int(df.blen.sum()),
               overall_bpb=float(df.nll.sum() / LN2 / df.blen.sum()),
               unmatched=bool(unmatched))
    res["quantitative"] = (res["n_windows"] >= MIN_CELL_WINDOWS
                           and res["n_docs"] >= MIN_CELL_DOCS
                           and bool(extra_quant_ok))
    if not extra_quant_ok:
        res["ineligible_reason"] = ineligible_reason or "protocol floor"
    bins, wpiv, dpiv = bin_table(df, EDGES)
    res["n_unattributed_groups"] = int((df.doc < 0).sum())
    res["bins"] = dict(mid=bins.mid.round(1).tolist(),
                       bpb=bins.bpb.round(5).tolist(),
                       bpb_median=bins.bpb_median.round(5).tolist(),
                       nll=bins.nll.round(3).tolist(),
                       n_windows=bins.n_windows.tolist(),
                       n_docs=bins.n_docs.tolist(),
                       n_groups=bins.n_groups.tolist(),
                       bytes=bins.bytes.tolist())
    res["descriptive"] = descriptive(bins)
    if res["quantitative"]:
        res["fit"] = gated_fits(bins)
        binsA, _, _ = bin_table(df, EDGES_ALT)
        res["fit_alt_edges"] = gated_fits(binsA)
        acc = bool(res["fit"].get("accepted"))
        res["boot_windows"] = boot_stats(*wpiv, bins, acc)
        res["boot_docs"] = boot_stats(*dpiv, bins, acc, seed=11)
    else:
        res["fit"] = dict(accepted=False,
                          reason="cell below quantitative floors "
                                 f"(w={res['n_windows']} d={res['n_docs']})")
    return res


_STREAM_STATS = [None]


def stream_unmatched(corpus, kind, stats=None):
    """A cell is unmatched if prep flagged its stream (clean streams
    below MIN_MATCHED; XL supplements) — the analyzer must read prep's
    verdict, not infer it from the kind suffix alone (review fix)."""
    if kind.endswith("_xl"):
        return True
    if stats is None:
        if _STREAM_STATS[0] is None:
            p = os.path.join(BASE, "data", "streams_stats.json")
            _STREAM_STATS[0] = (json.load(open(p))
                                if os.path.exists(p) else {})
        stats = _STREAM_STATS[0]
    s = ((stats.get("corpora") or {}).get(corpus, {})
         .get("streams", {}).get(kind))
    if not s:  # FAIL CLOSED (review): a stream prep never described is
        return True  # never eligible for matched comparison
    return bool(s.get("unmatched")) or s.get("matched") is False


def doc_dates(corpus, kind):
    """doc_id -> (date, provenance_flag) — the flag travels with the
    date so masked variants can report/exclude vendor-suspect docs."""
    mp = os.path.join(STREAMS, corpus, f"{kind}.manifest.jsonl")
    if not os.path.exists(mp):
        return {}
    return {d["doc_id"]: (d.get("date"), bool(d.get("provenance_flag")))
            for d in (json.loads(l) for l in open(mp))}


MASK_MIN_DOCS, MASK_MIN_BYTES = 20, 300_000  # PREREG §6 masking floors


def analyze_cell(path, short, corpus, kind):
    raw = pd.read_csv(path)
    mp = path + ".meta.json"
    if os.path.exists(mp):
        m = json.load(open(mp))
        # reconcile loaded rows/bytes with the meta the evaluator wrote:
        # a truncated or repacked body cannot silently analyze
        if m.get("n_scored") is not None and (
                len(raw) != m["n_scored"]
                or int(raw.blen.sum()) != m.get("bytes_scored")):
            raise AssertionError(
                f"dump/meta reconciliation failed: rows {len(raw)} vs "
                f"{m.get('n_scored')}, bytes {int(raw.blen.sum())} vs "
                f"{m.get('bytes_scored')}")
    df = collapse_groups(raw)
    out = {}
    out["main"] = analyze_frame(
        df, unmatched=stream_unmatched(corpus, kind))
    # bits-per-codepoint REMOVED (review): whole-stream bytes/codepoint x
    # scored BPB is not exact for scored positions. Cross-language
    # descriptive comparisons stay QUALITATIVE until exact scored-
    # codepoint accounting lands in the schema; no bpc inference allowed.
    if kind == "full_topo" and corpus in MASKABLE:
        dates = doc_dates(corpus, kind)
        for tag, cut in CUTOFFS.items():
            keep = {d for d, (dt, _) in dates.items() if dt and dt > cut}
            flagged = {d for d in keep if dates[d][1]}
            sub = df[df.doc.isin(keep)]
            if len(sub) == 0:
                out[f"masked_{tag}"] = dict(
                    n_groups=0, quantitative=False,
                    note="no post-cutoff target docs")
                continue
            floors_ok = (len(keep) >= MASK_MIN_DOCS
                         and int(sub.blen.sum()) >= MASK_MIN_BYTES)
            # declared masking floors (PREREG §6) enter BEFORE fitting so
            # a floor-failing mask can never emit beta/CIs; the masked-vs-
            # full delta is a TEMPORAL-GENERALIZATION (cohort) gap
            r = analyze_frame(sub, extra_quant_ok=floors_ok,
                              ineligible_reason=f"masking floors "
                              f"(docs={len(keep)}, "
                              f"bytes={int(sub.blen.sum())})")
            r["masked_docs"] = int(len(keep))
            r["flagged_docs"] = int(len(flagged))
            r["flagged_bytes"] = int(df[df.doc.isin(flagged)].blen.sum())
            r["interpretation"] = ("temporal-generalization (cohort) gap; "
                                   "contamination is one contributor")
            out[f"masked_{tag}"] = r
            if flagged:  # PREREG §5 promise: vendor/port-flag exclusion
                keep2 = keep - flagged  # sensitivity, SAME floors
                sub2 = df[df.doc.isin(keep2)]
                if len(sub2):
                    floors2 = (len(keep2) >= MASK_MIN_DOCS
                               and int(sub2.blen.sum()) >= MASK_MIN_BYTES)
                    r2 = analyze_frame(
                        sub2, extra_quant_ok=floors2,
                        ineligible_reason="masking floors (no-flag "
                        "sensitivity)")
                    r2["masked_docs"] = int(len(keep2))
                    r2["interpretation"] = ("no-provenance-flag "
                                            "sensitivity of masked_" + tag)
                    out[f"masked_{tag}_noflag"] = r2
    return out


# ---------- comparisons on common support ----------

def comparisons(cells):
    """Cross-corpus tables per (model, comparison class) restricted to the
    COMMON byte support: bins present with >=MIN_BIN_WINDOWS in EVERY
    matched, quantitative cell of the group."""
    groups = {}
    for name, c in cells.items():
        short, corpus, kind = name.split("__", 2)
        if kind.endswith("_xl"):
            continue
        key = (short, "full" if kind == "full_topo" else
               "shuffled" if kind == "full_shuffled" else
               kind if kind.startswith("clean_") else None)
        if key[1] is None:
            continue
        groups.setdefault(key, {})[corpus] = c["main"]
    out = {}
    for (short, cls), per in groups.items():
        quant = {c: r for c, r in per.items()
                 if r["quantitative"] and not r["unmatched"]}
        if len(quant) < 2:
            continue
        # freeze the COMMON valid bin set across cells (review fix:
        # endpoint-minus-endpoint was not the preregistered statistic)
        commons = None
        for r in quant.values():
            v = {round(m, 1) for m, w in zip(r["bins"]["mid"],
                                             r["bins"]["n_windows"])
                 if w >= MIN_BIN_WINDOWS}
            commons = v if commons is None else commons & v
        if not commons:
            continue
        dec_common = sorted(m for m in commons if 16 <= m < 256)
        top_common = max(commons)
        if not dec_common:
            continue
        tab = {}
        for corpus, r in quant.items():
            z = {round(m, 1): (n, b, p) for m, n, b, p in zip(
                r["bins"]["mid"], r["bins"]["nll"], r["bins"]["bytes"],
                r["bins"]["bpb"])}
            dn = sum(z[m][0] for m in dec_common)
            db = sum(z[m][1] for m in dec_common)
            # SAME preregistered statistic as per-cell: decade aggregate
            # BPB minus BPB at the shared top common bin
            tab[corpus] = dict(
                decade_bpb=dn / LN2 / db,
                top_common_bpb=z[top_common][2],
                gain_common=dn / LN2 / db - z[top_common][2],
                n_common_bins=len(commons))
        out[f"{short}__{cls}"] = dict(
            common_decade_bins=dec_common, top_common_bin=top_common,
            corpora=tab)
    return out


def production_valid(path, name):
    """Reject any dump whose meta is not a production-valid identity
    (review fix: previously any gzip+meta was analyzed). Reuses the
    runner's cell_done so there is exactly one identity definition."""
    from run_phase1 import FAMILIES, cell_done
    short, corpus, tag = name.split("__", 2)
    kind = tag
    flags = []
    if kind.endswith("__perdoc"):
        kind = kind[:-len("__perdoc")]
        flags = ["--reset-per-doc"]
    if "__ph" in kind:
        kind, ph = kind.rsplit("__ph", 1)
        flags = ["--window-phase", ph]
    mid_map = {v[0]: k for k, v in FAMILIES.items()}
    mid = mid_map.get(short)
    if mid is None:
        return False, f"unknown model short {short!r}"
    ctx = FAMILIES[mid][3]
    stream = os.path.join(STREAMS, corpus, f"{kind}.txt")
    mj_p = os.path.join(BASE, "models.json")
    mj = json.load(open(mj_p)) if os.path.exists(mj_p) else {}
    if not os.path.exists(stream):
        return False, f"stream missing: {stream}"
    return (cell_done(path, mid, ctx, flags, stream, mj),
            "cell_done identity check")


def phase_pair_stats(d0, dp, n_boot=N_BOOT, seed=7):
    """Pure helper (unit-tested directly): joins two collapsed frames on
    grp and reports the ORIENTED gain
        oriented_gain_bpb = -sign(ctxb_p - ctxb_0) * (nll_p - nll_0)
                             / (ln2 * blen)
    (positive = the same content was easier with MORE preceding context,
    whichever phase provided it — a raw signed delta cancels across
    orientations; review fix). Zero-context-delta pairs are excluded;
    blen and doc must match exactly (asserted)."""
    j = (d0.set_index("grp")
         .join(dp.set_index("grp"), lsuffix="_0", rsuffix="_p",
               how="inner"))
    if not len(j):
        return dict(n_pairs=0)
    assert (j.blen_0 == j.blen_p).all(), "blen mismatch in phase pair"
    # doc attribution is window-independent (charged byte interval), so
    # equality must hold INCLUDING the -1 sentinel (review fix)
    assert (j.doc_0 == j.doc_p).all(), "doc mismatch in phase pair"
    j = j[j.ctxb_p != j.ctxb_0]
    if not len(j):
        return dict(n_pairs=0, note="no context-delta pairs")
    sign = np.sign(j.ctxb_p - j.ctxb_0)
    og_nats = -sign * (j.nll_p - j.nll_0)      # per-group oriented nats
    og = og_nats / LN2 / j.blen_0              # per-group bits/byte
    # PRIMARY: byte-weighted aggregate (consistent with the BPB estimand
    # everywhere else); equal-group mean kept as sensitivity (review fix)
    bw = float(og_nats.sum() / LN2 / j.blen_0.sum())
    strata = {}
    ratio = np.maximum(j.ctxb_p, j.ctxb_0) / np.maximum(
        np.minimum(j.ctxb_p, j.ctxb_0), 1)
    sbin = np.round(np.log2(ratio)).astype(int)
    for b in sorted(set(sbin)):
        m = sbin == b
        strata[f"log2_ratio_{b}"] = dict(
            n=int(m.sum()),
            byte_weighted=float(og_nats[m].sum() / LN2
                                / j.blen_0[m].sum()),
            equal_group=float(og[m].mean()))
    rec = dict(n_pairs=int(len(j)),
               n_docs=int(j[j.doc_0 >= 0].doc_0.nunique()),
               oriented_gain_bpb_byte_weighted=bw,
               oriented_gain_bpb_equal_group=float(og.mean()),
               frac_positive=float((og > 0).mean()),
               strata=strata)
    docs = j[j.doc_0 >= 0]
    if docs.doc_0.nunique() >= 4:
        rng = np.random.default_rng(seed)
        uu = docs.doc_0.unique()
        dn = og_nats[j.doc_0 >= 0]
        db = j.blen_0[j.doc_0 >= 0]
        n_by = {u: (float(dn[docs.doc_0 == u].sum()),
                    float(db[docs.doc_0 == u].sum())) for u in uu}
        reps = []
        for _ in range(n_boot):
            sel = rng.choice(uu, len(uu), True)
            tn = sum(n_by[u][0] for u in sel)
            tb = sum(n_by[u][1] for u in sel)
            if tb > 0:
                reps.append(tn / LN2 / tb)
        if len(reps) > 20:
            lo, hi = np.percentile(reps, [2.5, 97.5])
            rec["oriented_gain_ci95_doc"] = [float(lo), float(hi)]
    return rec


def analyze_phase_pairs(allow_dev=False):
    """Sentinel phase ablation (PREREG G3a). Every base AND phase dump is
    production-validated before joining (review fix: a dump rejected by
    the main loop must not contaminate phase_pairs.json). Returns the
    list of problems so main() can exit nonzero."""
    problems = []
    out = {}
    for base_p in sorted(glob.glob(os.path.join(
            DUMPS, "*__full_topo.csv.gz"))):
        stem = base_p[:-len(".csv.gz")]
        phases = sorted(glob.glob(stem + "__ph*.csv.gz"))
        if not phases:
            continue
        name = os.path.basename(stem)
        if not allow_dev:
            ok, why = production_valid(base_p, name)
            if not ok:
                out[name] = dict(rejected=f"base: {why}")
                problems.append(f"phase-pairs {name}: base rejected ({why})")
                continue
        try:
            d0 = collapse_groups(pd.read_csv(base_p))
        except Exception as e:
            out[name] = dict(error=repr(e))
            problems.append(f"phase-pairs {name}: {e!r}")
            continue
        entry = {}
        for pp in phases:
            pname = os.path.basename(pp)[:-len(".csv.gz")]
            ph = pname.rsplit("__ph", 1)[1]
            if not allow_dev:
                ok, why = production_valid(pp, pname)
                if not ok:
                    entry[f"ph{ph}"] = dict(rejected=why)
                    problems.append(f"phase-pairs {pname}: rejected ({why})")
                    continue
            try:
                entry[f"ph{ph}"] = phase_pair_stats(
                    d0, collapse_groups(pd.read_csv(pp)))
            except Exception as e:
                entry[f"ph{ph}"] = dict(error=repr(e))
                problems.append(f"phase-pairs {pname}: {e!r}")
        out[name] = entry
    with open(os.path.join(OUT, "phase_pairs.json"), "w") as f:
        json.dump(out, f, indent=1)
    if out:
        print(f"[phase-pairs] {len(out)} base cells, "
              f"{len(problems)} problems -> results_v2/phase_pairs.json")
    return problems


def main():
    allow_dev = "--allow-dev" in sys.argv  # local smokes only; never gates
    os.makedirs(OUT, exist_ok=True)
    cells, rows, errors = {}, [], []
    for path in sorted(glob.glob(os.path.join(DUMPS, "*.csv.gz"))):
        if ".quarantine-" in path:
            continue
        if not os.path.exists(path + ".meta.json"):
            continue
        name = os.path.basename(path)[:-7]
        try:
            short, corpus, kind = name.split("__", 2)
        except ValueError:
            continue
        if not allow_dev:
            ok, why = production_valid(path, name)
            if not ok:
                errors.append(f"{name}: NOT production-valid ({why})")
                print(f"[IDENTITY-REJECT] {name}: {why}",
                      file=sys.stderr, flush=True)
                continue
        if kind.startswith("full_topo__ph") or kind.endswith("__perdoc"):
            kind = kind  # ablation variants analyzed under their tag
        try:
            cells[name] = analyze_cell(path, short, corpus,
                                       kind.split("__")[0]
                                       if "__" in kind else kind)
        except Exception as e:
            errors.append(f"{name}: {e!r}")
            print(f"[CELL-ERROR] {name}: {e!r}", file=sys.stderr, flush=True)
            continue
        for variant, r in cells[name].items():
            if r.get("n_groups", 1) == 0:
                continue
            fit = r.get("fit") or {}
            acc = bool(fit.get("accepted"))
            pw = fit.get("powerlaw_equal_weight") or {}
            bw = r.get("boot_windows") or {}
            rows.append(dict(
                cell=name, variant=variant,
                model=short, corpus=corpus, kind=kind,
                quantitative=r["quantitative"], unmatched=r.get("unmatched"),
                n_windows=r["n_windows"], n_docs=r["n_docs"],
                bpb=round(r["overall_bpb"], 4),
                gain=r["descriptive"].get("context_gain_bpb"),
                gain_ci=bw.get("gain_ci95"),
                c_hat=r["descriptive"].get("c_hat_eps0.05"),
                fit_accepted=acc,
                # rejected fits emit NO reportable parameters (review fix)
                beta=pw.get("beta") if acc else None,
                Linf=pw.get("Linf") if acc else None,
                beta_ci=bw.get("beta_ci95") if acc else None,
                fit_reason=fit.get("reason")))
            print(f"{name}[{variant}] bpb={r['overall_bpb']:.3f} "
                  f"quant={r['quantitative']} "
                  f"gain={r['descriptive'].get('context_gain_bpb')} "
                  f"fit={'ACC' if fit.get('accepted') else 'rej'}",
                  flush=True)
    # phase pairing runs BEFORE serialization so its failures land in the
    # main error artifact, not only the exit code (review fix)
    errors.extend(analyze_phase_pairs(allow_dev=allow_dev))
    with open(os.path.join(OUT, "fits.json"), "w") as f:
        json.dump(dict(cells=cells, errors=errors), f, indent=1)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "fits.csv"), index=False)
    with open(os.path.join(OUT, "comparisons.json"), "w") as f:
        json.dump(comparisons(cells), f, indent=1)
    print(f"[done] {len(cells)} cells, {len(errors)} errors -> results_v2/")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
