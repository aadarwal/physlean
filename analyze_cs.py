#!/usr/bin/env python3
"""ARM_CS CS-4 analyzer v2 (frozen per ARM_CS §5/§6 BEFORE any ladder run).

Two-phase discipline:
  --phase gamma     reads ONLY top-two-rung artifacts (top for the
                    estimate, second for convergence) + the registered
                    CS-1 stats; enforces exact seeds {0,1,2}, size=10m,
                    doc-reset dumps; binds the SHA256 of every input;
                    writes results_cs/registration_gamma.json. COMMIT IT.
  --phase envelope  refuses unless the registration is committed, clean,
                    and identical to HEAD's blob; computes delta_n FIRST,
                    the fast-learning regime gate, then H3 as equivalence
                    testing (TOST, fixed margin M=0.05), the shifted
                    collapse metric (H3b, descriptive), and sensitivities.

Frozen constants (ARM_CS §6 v2): gamma from the LOG-BINNED curve over
n in [4, 512] (24 bins; window sensitivities [4,128] and [16,512]);
identifiability gates gamma>0.05, interior H*, profile width <= 0.15 at
R^2 >= max-0.002, R^2_max >= 0.9; convergence 0.02 b/B top-two rungs;
H grid step 0.005; alpha shift rule L-H >= 0.02 with >= 4 rungs;
H3-eligible = {lean, python, cpp}; fast learning iff >= 9 of 12 delta_n
exceed gamma/(2 beta) at fit R^2 >= 0.9; horizon gate
n*(P_top) = n_det (P_top/P_corpus)^(1/2 beta) <= T/4.
"""
import argparse
import glob
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LN2 = math.log(2)
GAMMA_LO, GAMMA_HI, GAMMA_BINS = 4, 512, 24
GAMMA_WINDOWS_SENS = [(4, 128), (16, 512)]
CONV_TOL = 0.02
H_STEP = 0.005
MIN_R2 = 0.9
PROFILE_R2_DROP = 0.001  # calibrated: good binned fits span ~0.10 in gamma
PROFILE_WIDTH_MAX = 0.15
GAMMA_MIN = 0.05
SHIFT_MIN = 0.02
MIN_RUNGS = 4
H3_MARGIN = 0.05
H3_ELIGIBLE = {"lean", "python", "cpp"}
COLLAPSE_PTS = 20
DELTA_NS = list(range(1, 13))
DELTA_FAST_COUNT = 9
DELTA_R2_MIN = 0.9
SEEDS = {0, 1, 2}
REG_PATH = os.path.join("results_cs", "registration_gamma.json")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def load_runs(runs_dir, size="10m"):
    out = []
    for p in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        r = json.load(open(p))
        if r.get("size") != size or not r.get("doc_reset") \
                or "-r" not in r.get("run", ""):
            continue
        r["_path"] = p
        out.append(r)
    return out


def rung_map(cs2_dir, lang):
    man = json.load(open(os.path.join(cs2_dir, f"{lang}_cs2.json")))
    return sorted(int(v) for v in man["rung_boundaries"].values())


def rung_of(r, bounds):
    for i, b in enumerate(bounds):
        if abs(r.get("train_bytes", -1) - b) <= 3:
            return i
    return None


def curve_sums(dump_path, n_max=4096):
    df = pd.read_csv(dump_path, usecols=["ctxb", "nll"])
    df = df[(df.ctxb >= 1) & (df.ctxb <= n_max)]
    g = df.groupby("ctxb").nll.agg(["sum", "count"])
    sums = np.zeros(n_max + 1)
    cnts = np.zeros(n_max + 1, dtype=np.int64)
    sums[g.index.values] = g["sum"].values
    cnts[g.index.values] = g["count"].values
    return sums, cnts


def pooled_binned(runs, nll_dir, lo=GAMMA_LO, hi=GAMMA_HI,
                  n_bins=GAMMA_BINS):
    """Pool dump sums over runs; return (bin_mids, bpb_vals) log-binned."""
    S = np.zeros(4097)
    C = np.zeros(4097, dtype=np.int64)
    used = []
    for r in runs:
        dump = os.path.join(nll_dir, f"{r['run']}__{r['lang']}__val.csv.gz")
        if not os.path.exists(dump):
            return None, None, used
        s, c = curve_sums(dump)
        S += s
        C += c
        used.append(dump)
    edges = np.unique(np.round(np.logspace(np.log10(lo), np.log10(hi + 1),
                                           n_bins + 1)).astype(int))
    mids, vals = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        cs = C[a:b].sum()
        if cs < 500:
            continue
        mids.append(math.sqrt(a * max(b - 1, a)))
        vals.append(S[a:b].sum() / cs / LN2)
    return np.array(mids), np.array(vals), used


def gamma_fit(mids, vals):
    """H-grid fit with identifiability gates. Returns dict (maybe reason)."""
    if mids is None or len(mids) < 8:
        return dict(reason="too few binned points")
    y = np.asarray(vals, float)
    lx = np.log(np.asarray(mids, float))
    grid = np.arange(0.0, float(y.min()) - 1e-9, H_STEP)
    if len(grid) < 3:
        return dict(reason="degenerate H grid")
    rows = []
    for H in grid:
        ly = np.log(y - H)
        A = np.vstack([lx, np.ones_like(lx)]).T
        coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
        pred = A @ coef
        sst = float(((ly - ly.mean()) ** 2).sum())
        r2 = 1 - float(((ly - pred) ** 2).sum()) / sst if sst > 0 else -1
        rows.append((r2, float(-coef[0]), float(H)))
    r2s = np.array([r[0] for r in rows])
    i = int(np.argmax(r2s))
    r2m, g, H = rows[i]
    prof = [rows[j][1] for j in range(len(rows))
            if rows[j][0] >= r2m - PROFILE_R2_DROP]
    width = max(prof) - min(prof)
    gates = dict(r2=r2m, gamma_positive=bool(g > GAMMA_MIN),
                 interior=bool(0 < i < len(rows) - 1),
                 profile_width=width)
    if not gates["gamma_positive"]:
        return dict(reason=f"gate: gamma {g:.3f} <= {GAMMA_MIN}", **gates)
    if not gates["interior"]:
        return dict(reason="gate: H* at grid boundary", **gates)
    if width > PROFILE_WIDTH_MAX:
        return dict(reason=f"gate: profile width {width:.3f}", **gates)
    if r2m < MIN_R2:
        return dict(reason=f"gate: r2 {r2m:.3f} < {MIN_R2}", **gates)
    return dict(gamma=g, H_inf=H,
                profile_lo=min(prof), profile_hi=max(prof), **gates)


def git_blob_matches(relpath):
    try:
        wt = subprocess.run(["git", "hash-object", relpath], cwd=BASE,
                            capture_output=True, text=True).stdout.strip()
        head = subprocess.run(["git", "rev-parse", f"HEAD:{relpath}"],
                              cwd=BASE, capture_output=True,
                              text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--",
                                relpath], cwd=BASE, capture_output=True,
                               text=True).stdout.strip()
        return bool(wt) and wt == head and not dirty
    except OSError:
        return False


def seed_set(runs):
    return sorted({r.get("seed") for r in runs})


def scope_beta(stats, lang):
    """Registered beta with csupport preference + withhold enforcement."""
    scope = stats["scopes"].get(lang) or {}
    fit = scope.get("fit") or {}
    src = lang
    cs = stats["scopes"].get(f"{lang}__csupport") or {}
    csf = cs.get("fit") or {}
    ci = fit.get("ci_doc_block_boot") or fit.get("ci_lag_boot")
    half = (ci[1] - ci[0]) / 2 if ci else 0.0
    if csf.get("beta_corr") is not None and fit.get("beta_corr") is not None \
            and abs(csf["beta_corr"] - fit["beta_corr"]) > half:
        scope, fit, src = cs, csf, f"{lang}__csupport"
        ci = fit.get("ci_doc_block_boot") or fit.get("ci_lag_boot") or ci
    if fit.get("beta_corr") is None:
        return None, None, None, src, "no reportable beta_corr", None, None
    if fit.get("divergence_withhold"):
        return None, None, None, src, "beta withheld (op-vs-fro)", None, None
    lags = scope.get("lags") or []
    valid = scope.get("valid") or []
    n_det = max((l for l, v in zip(lags, valid) if v), default=None)
    return (fit["beta_corr"], ci, fit, src, None, n_det,
            scope.get("total_bytes"))


def phase_gamma(args):
    stats = json.load(open(args.stats))
    runs = load_runs(args.runs_dir)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE,
                                capture_output=True,
                                text=True).stdout.strip()
    except OSError:
        commit = None
    reg = dict(schema="cs_registration_gamma_v2", commit=commit,
               stats_file=args.stats, stats_sha256=sha256_file(args.stats),
               constants=dict(window=[GAMMA_LO, GAMMA_HI], bins=GAMMA_BINS,
                              conv_tol=CONV_TOL, h_step=H_STEP,
                              min_r2=MIN_R2, margin=H3_MARGIN),
               langs={})
    for lang in args.langs.split(","):
        bounds = rung_map(args.cs2_dir, lang)
        top, second = len(bounds) - 1, len(bounds) - 2
        lruns = [r for r in runs if r["lang"] == lang and r["ctx"] == 4096]
        top_runs = [r for r in lruns if rung_of(r, bounds) == top]
        sec_runs = [r for r in lruns if rung_of(r, bounds) == second]
        entry = dict(seed_top=seed_set(top_runs),
                     seed_second=seed_set(sec_runs))
        if set(entry["seed_top"]) != SEEDS or \
                set(entry["seed_second"]) != SEEDS:
            entry["reason"] = "seed sets incomplete (need exactly {0,1,2})"
            reg["langs"][lang] = entry
            continue
        mt, vt, used_t = pooled_binned(top_runs, args.nll_dir)
        ms, vs, used_s = pooled_binned(sec_runs, args.nll_dir)
        if mt is None or ms is None:
            entry["reason"] = "missing doc-reset dumps"
            reg["langs"][lang] = entry
            continue
        common = np.intersect1d(mt, ms)
        gap = float(np.max(np.abs(
            np.interp(common, mt, vt) - np.interp(common, ms, vs))))
        entry["convergence_gap"] = gap
        inputs = {os.path.relpath(p, BASE): sha256_file(p)
                  for p in used_t + used_s}
        inputs.update({os.path.relpath(r["_path"], BASE):
                       sha256_file(r["_path"])
                       for r in top_runs + sec_runs})
        entry["input_sha256"] = inputs
        if gap > CONV_TOL:
            entry["reason"] = f"not converged: gap {gap:.4f} > {CONV_TOL}"
            reg["langs"][lang] = entry
            continue
        fit = gamma_fit(mt, vt)
        if "gamma" not in fit:
            entry["reason"] = fit.get("reason", "gamma fit failed")
            entry["gamma_gates"] = fit
            reg["langs"][lang] = entry
            continue
        # uncertainty components combined in QUADRATURE (uncorrelated
        # systematics; worst-case stacking made M unreachable even for a
        # clean synthetic). Per-seed single-run fits are a DIAGNOSTIC,
        # not an uncertainty component (they are 3x noisier by
        # construction).
        win_g = [fit["gamma"]]
        for lo, hi in GAMMA_WINDOWS_SENS:
            sub = (mt >= lo) & (mt <= hi)
            f2 = gamma_fit(mt[sub], vt[sub])
            if "gamma" in f2:
                win_g.append(f2["gamma"])
        seed_g = []
        for r in top_runs:
            m1, v1, _ = pooled_binned([r], args.nll_dir)
            f1 = gamma_fit(m1, v1) if m1 is not None else {}
            if "gamma" in f1:
                seed_g.append(f1["gamma"])
        profile_hw = fit["profile_width"] / 2
        win_hw = (max(win_g) - min(win_g)) / 2
        gamma_hw = math.sqrt(profile_hw ** 2 + win_hw ** 2)
        entry.update(gamma=fit["gamma"], H_inf=fit["H_inf"],
                     r2=fit["r2"], profile_width=fit["profile_width"],
                     gamma_hw=gamma_hw,
                     gamma_hw_components=dict(profile=profile_hw,
                                              window=win_hw),
                     gamma_seed_diagnostic=seed_g)
        beta, ci, bfit, src, why, n_det, p_corpus = scope_beta(stats, lang)
        entry["beta_source"] = src
        entry["n_det"] = n_det
        entry["P_corpus"] = p_corpus
        if beta is None:
            entry["h3_ineligible_reason"] = why
        else:
            entry["beta_corr"] = beta
            entry["beta_ci"] = ci
            beta_hw = ((ci[1] - ci[0]) / 2) if ci else 0.0
            ap = fit["gamma"] / (2 * beta)
            entry["alpha_pred"] = ap
            entry["alpha_pred_hw"] = ap * math.sqrt(
                (gamma_hw / fit["gamma"]) ** 2 + (beta_hw / beta) ** 2)
        if lang not in H3_ELIGIBLE:
            entry["h3_ineligible_reason"] = \
                "format diagnostic arm (ARM_CS §1)"
        reg["langs"][lang] = entry
    out_path = os.path.join(BASE, args.reg)
    if os.path.exists(out_path) and not args.force:
        sys.exit(f"refusing to overwrite {args.reg} (use --force)")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(reg, f, indent=1)
    print(f"[gamma] wrote {args.reg} — COMMIT IT before --phase envelope")
    for lang, e in reg["langs"].items():
        print(f"  {lang}: " + (f"gamma={e['gamma']:.4f} H={e['H_inf']:.3f} "
                               f"pred={e.get('alpha_pred')}"
                               if "gamma" in e else e.get("reason", "?")))


def ols_shift(Ps, Ls, H):
    lx = np.log(np.asarray(Ps, float))
    ly = np.log(np.asarray(Ls, float) - H)
    A = np.vstack([lx, np.ones_like(lx)]).T
    coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
    pred = A @ coef
    sst = float(((ly - ly.mean()) ** 2).sum())
    r2 = 1 - float(((ly - pred) ** 2).sum()) / sst if sst > 0 else -1
    return float(-coef[0]), r2


def phase_envelope(args):
    if not args.skip_git_check and not git_blob_matches(args.reg):
        sys.exit("REFUSED: registration not committed-clean-identical to "
                 "HEAD (ARM_CS §5)")
    reg = json.load(open(os.path.join(BASE, args.reg)))
    runs = load_runs(args.runs_dir)
    out = dict(schema="cs_analysis_envelope_v2",
               registration_sha256=sha256_file(
                   os.path.join(BASE, args.reg)), langs={})
    for lang, rl in reg["langs"].items():
        if "gamma" not in rl:
            out["langs"][lang] = dict(reason="no registered gamma")
            continue
        H, gamma = rl["H_inf"], rl["gamma"]
        beta = rl.get("beta_corr")
        bounds = rung_map(args.cs2_dir, lang)
        lruns = [r for r in runs if r["lang"] == lang]
        # per-(ctx, rung) seed groups; drop incomplete-seed rungs, recorded
        groups = {}
        for r in lruns:
            ru = rung_of(r, bounds)
            if ru is not None:
                groups.setdefault((r["ctx"], ru), []).append(r)
        incomplete = sorted(str(k) for k, v in groups.items()
                            if set(seed_set(v)) != SEEDS)
        groups = {k: v for k, v in groups.items()
                  if set(seed_set(v)) == SEEDS}
        e = dict(incomplete_rungs_dropped=incomplete)
        curve = {}
        for (ctx, ru), rs in groups.items():
            vals = [r["final_val_bpb"] for r in rs]
            curve.setdefault(ctx, {})[ru] = dict(
                mean=float(np.mean(vals)),
                per_seed={r["seed"]: r["final_val_bpb"] for r in rs},
                P=bounds[ru])
        e["curves"] = curve
        # delta_n FIRST (regime gate precedes H3)
        ln_by_rung = {}
        for (ctx, ru), rs in groups.items():
            if ctx != 4096:
                continue
            m, v, _ = pooled_binned(rs, args.nll_dir, lo=1, hi=16,
                                    n_bins=16)
            # exact small-n curve for delta: unbinned via curve_sums
            S = np.zeros(129)
            C = np.zeros(129, dtype=np.int64)
            ok = True
            for r in rs:
                d = os.path.join(args.nll_dir,
                                 f"{r['run']}__{r['lang']}__val.csv.gz")
                if not os.path.exists(d):
                    ok = False
                    break
                s, c = curve_sums(d, 128)
                S += s
                C += c
            if ok:
                with np.errstate(invalid="ignore"):
                    ln_by_rung[ru] = np.where(C > 0, S / np.maximum(C, 1)
                                              / LN2, np.nan)
        deltas = {}
        for n in DELTA_NS:
            Ps_n = [bounds[ru] for ru in sorted(ln_by_rung)
                    if not np.isnan(ln_by_rung[ru][n])]
            Ls_n = [float(ln_by_rung[ru][n]) for ru in sorted(ln_by_rung)
                    if not np.isnan(ln_by_rung[ru][n])]
            if len(Ps_n) < MIN_RUNGS:
                continue
            best = None
            for Hn in np.arange(0.0, min(Ls_n) - 1e-9, H_STEP):
                d, r2 = ols_shift(Ps_n, Ls_n, Hn)
                if best is None or r2 > best[1]:
                    best = (d, r2, float(Hn))
            deltas[n] = dict(delta=best[0], r2=best[1], H_n=best[2])
        e["delta_n"] = deltas
        fast = None
        if beta and deltas:
            thr = gamma / (2 * beta)
            good = [d for d in deltas.values()
                    if d["delta"] > thr and d["r2"] >= DELTA_R2_MIN]
            fast = len(good) >= DELTA_FAST_COUNT and \
                len(deltas) >= len(DELTA_NS) - 1
            e["fast_learning"] = dict(established=bool(fast),
                                      n_exceeding=len(good),
                                      threshold=thr,
                                      n_estimated=len(deltas))
        # alpha_D from the shifted primary (T=4096)
        prim = curve.get(4096, {})
        pts = sorted((v["P"], v["mean"], v["per_seed"])
                     for v in prim.values() if v["mean"] - H >= SHIFT_MIN)
        if len(pts) < MIN_RUNGS:
            e["alpha_D"] = None
            e["reason"] = f"only {len(pts)} rungs survive shift rule"
        else:
            Ps = [p for p, _, _ in pts]
            Ls = [v for _, v, _ in pts]
            a0, _ = ols_shift(Ps, Ls, H)
            seed_a = []
            for s in SEEDS:
                try:
                    a_s, _ = ols_shift(Ps, [ps[s] for _, _, ps in pts], H)
                    seed_a.append(a_s)
                except KeyError:
                    pass
            loo_a = []
            for i in range(len(Ps)):
                a_l, _ = ols_shift([p for j, p in enumerate(Ps) if j != i],
                                   [v for j, v in enumerate(Ls) if j != i],
                                   H)
                loo_a.append(a_l)
            # H_inf grid-step systematic (dominant near the shift floor):
            # refit at H +- one grid step, re-applying the shift rule
            hstep_a = [a0]
            for Hv in (H - H_STEP, H + H_STEP):
                pv = sorted((v["P"], v["mean"]) for v in prim.values()
                            if v["mean"] - Hv >= SHIFT_MIN)
                if len(pv) >= MIN_RUNGS:
                    hstep_a.append(ols_shift([p for p, _ in pv],
                                             [v for _, v in pv], Hv)[0])
            # quadrature of uncorrelated components (see gamma phase note)
            hw = math.sqrt(
                ((max(seed_a + [a0]) - min(seed_a + [a0])) / 2) ** 2
                + ((max(loo_a + [a0]) - min(loo_a + [a0])) / 2) ** 2
                + ((max(hstep_a) - min(hstep_a)) / 2) ** 2)
            e["alpha_D"] = a0
            e["alpha_D_hw"] = hw
            e["alpha_D_hw_components"] = dict(
                seed=(max(seed_a + [a0]) - min(seed_a + [a0])) / 2,
                loo=(max(loo_a + [a0]) - min(loo_a + [a0])) / 2,
                hstep=(max(hstep_a) - min(hstep_a)) / 2)
            e["alpha_D_raw_sens"] = ols_shift(Ps, Ls, 0.0)[0]
            e["m_sweep"] = {m: ols_shift(Ps[:m], Ls[:m], H)[0]
                            for m in range(MIN_RUNGS, len(Ps) + 1)}
            env = {}
            for ctx, d in curve.items():
                for ru, v in d.items():
                    env[ru] = min(env.get(ru, 9e9), v["mean"])
            eP = [bounds[r] for r in sorted(env)
                  if env[r] - H >= SHIFT_MIN]
            eL = [env[r] for r in sorted(env) if env[r] - H >= SHIFT_MIN]
            if len(eP) >= MIN_RUNGS:
                e["alpha_D_envelopeT_sens"] = ols_shift(eP, eL, H)[0]
            # H3: regime gate first, then TOST with fixed margin
            ap = rl.get("alpha_pred")
            ap_hw = rl.get("alpha_pred_hw")
            if rl.get("h3_ineligible_reason"):
                e["H3"] = f"NOT-ELIGIBLE ({rl['h3_ineligible_reason']})"
            elif ap is None or ap_hw is None:
                e["H3"] = "NOT-ELIGIBLE (no alpha_pred)"
            elif fast is None or not fast:
                e["H3"] = "INDETERMINATE(regime)"
                if beta and deltas:
                    md = min(d["delta"] for d in deltas.values())
                    e["slow_regime_descriptive"] = dict(
                        min_delta=md,
                        operative=min(md, gamma / (2 * beta)),
                        note="descriptive only; not the zero-parameter "
                             "test (ARM_CS §6)")
            else:
                center = a0 - ap
                dhw = math.sqrt(hw ** 2 + ap_hw ** 2)
                e["H3_diff"] = dict(center=center, hw=dhw)
                if abs(center) + dhw <= H3_MARGIN:
                    e["H3"] = "SUPPORTED"
                elif abs(center) - dhw > H3_MARGIN:
                    e["H3"] = "REFUTED"
                else:
                    e["H3"] = "INDETERMINATE"
        # horizon gate
        if beta and rl.get("n_det") and rl.get("P_corpus"):
            n_star = rl["n_det"] * (bounds[-1] / rl["P_corpus"]) \
                ** (1 / (2 * beta))
            e["horizon"] = dict(n_star_top=n_star,
                                ok=bool(n_star <= 4096 / 4))
        # H3b: shifted collapse metric (descriptive)
        grids = []
        for ru in sorted(ln_by_rung):
            c = ln_by_rung[ru]
            ns = np.arange(GAMMA_LO, min(128, len(c) - 1))
            y = (c[ns] - H) * ns ** gamma
            if beta:
                xv = bounds[ru] / ns ** (2 * beta)
                okm = ~np.isnan(y) & (y > 0)
                if okm.sum() > 5:
                    o = np.argsort(xv[okm])
                    grids.append((np.log(xv[okm][o]), np.log(y[okm][o])))
        if len(grids) >= 3:
            gx = np.linspace(max(g[0].min() for g in grids),
                             min(g[0].max() for g in grids), COLLAPSE_PTS)
            if gx[0] < gx[-1]:
                M = np.stack([np.interp(gx, g[0], g[1]) for g in grids])
                e["H3b_collapse_metric"] = float(
                    M.var(axis=0).mean() / max(M.mean(axis=0).var(), 1e-12))
                e["H3b_n_curves"] = len(grids)
        out["langs"][lang] = e
    path = os.path.join(BASE, os.path.dirname(args.reg),
                        "analysis_envelope.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"[envelope] wrote {path}")
    for lang, e in out["langs"].items():
        print(f"  {lang}: alpha_D={e.get('alpha_D')} H3={e.get('H3')} "
              f"fast={e.get('fast_learning')} "
              f"H3b={e.get('H3b_collapse_metric')}")


def _fake_lang(tmp, lang, bounds, g, H, a, delta, seeds=(0, 1, 2)):
    rng = np.random.default_rng(hash(lang) % 2 ** 31)
    runs_d = os.path.join(tmp, "runs")
    nll_d = os.path.join(tmp, "nll")
    for ri, P in enumerate(bounds):
        for s in seeds:
            LP = H + 39.0 * P ** -a
            run = f"scratch-10m-{lang}-s{s}-r{ri}"
            json.dump(dict(run=run, lang=lang, size="10m", seed=s,
                           ctx=4096, lr=1e-3, epochs=2, doc_reset=True,
                           train_bytes=P, final_val_bpb=LP,
                           tokens_seen=P),
                      open(os.path.join(runs_d, run + ".json"), "w"))
            with gzip.open(os.path.join(nll_d,
                                        f"{run}__{lang}__val.csv.gz"),
                           "wt") as f:
                f.write("win,doc,ctxb,blen,tok,nll\n")
                rows = []
                for w in range(100):
                    for n in range(1, 513):
                        ln_bits = (H + 1.6 * n ** -g + 200.0 * P ** -delta
                                   + rng.normal(0, 0.002))
                        rows.append(f"{w},0,{n},1,65,"
                                    f"{max(ln_bits, 0.01) * LN2:.5f}")
                f.write("\n".join(rows) + "\n")


def selftest():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="cs_selftest_")
    for d in ("runs", "nll", "cs2"):
        os.makedirs(os.path.join(tmp, d))
    bounds = [int(50e6 * f) for f in (1 / 64, 1 / 32, 1 / 16, 1 / 8,
                                      1 / 4, 1 / 2, 1)]
    for lang in ("lean", "python"):
        json.dump(dict(rung_boundaries={str(i): b for i, b in
                                        enumerate(bounds)}),
                  open(os.path.join(tmp, "cs2", f"{lang}_cs2.json"), "w"))
    beta = 0.7
    g_true, H_true = 0.4, 0.9
    # lean: alpha == gamma/(2 beta) => SUPPORTED; python: off => REFUTED
    _fake_lang(tmp, "lean", bounds, g_true, H_true,
               g_true / (2 * beta), 0.6)
    _fake_lang(tmp, "python", bounds, g_true, H_true, 0.16, 0.6)
    stats = dict(scopes={
        lang: dict(fit=dict(beta_corr=beta,
                            ci_doc_block_boot=[beta - 0.02, beta + 0.02],
                            divergence_withhold=False),
                   lags=[1, 2, 4, 8, 16, 32], valid=[True] * 6,
                   total_bytes=120_000_000)
        for lang in ("lean", "python")})
    stats_p = os.path.join(tmp, "lang_stats.json")
    json.dump(stats, open(stats_p, "w"))
    ns = argparse.Namespace(stats=stats_p,
                            runs_dir=os.path.join(tmp, "runs"),
                            nll_dir=os.path.join(tmp, "nll"),
                            cs2_dir=os.path.join(tmp, "cs2"),
                            langs="lean,python",
                            reg=os.path.relpath(
                                os.path.join(tmp, "reg.json"), BASE),
                            force=False)
    phase_gamma(ns)
    reg = json.load(open(os.path.join(tmp, "reg.json")))
    for lang in ("lean", "python"):
        e = reg["langs"][lang]
        assert "gamma" in e, e.get("reason")
        assert abs(e["gamma"] - g_true) < 0.08, f"{lang} gamma {e['gamma']}"
        assert abs(e["H_inf"] - H_true) < 0.06, f"{lang} H {e['H_inf']}"
        assert e["input_sha256"], "no input hashes bound"
    ns2 = argparse.Namespace(runs_dir=ns.runs_dir, nll_dir=ns.nll_dir,
                             cs2_dir=ns.cs2_dir, reg=ns.reg,
                             skip_git_check=True)
    phase_envelope(ns2)
    env = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    el, ep = env["langs"]["lean"], env["langs"]["python"]
    assert el["fast_learning"]["established"], el["fast_learning"]
    assert el["H3"] == "SUPPORTED", (el["H3"], el.get("H3_diff"))
    assert ep["H3"] == "REFUTED", (ep["H3"], ep.get("H3_diff"))
    assert len(el["delta_n"]) >= 10 and \
        abs(float(el["delta_n"]["4"]["delta"]) - 0.6) < 0.1, el["delta_n"].get("4")
    assert el.get("H3b_collapse_metric") is not None
    assert el["horizon"]["n_star_top"] > 0
    # seed-incompleteness must fail closed
    victim = glob.glob(os.path.join(tmp, "runs",
                                    "scratch-10m-lean-s2-r6.json"))[0]
    os.remove(victim)
    ns.force = True
    phase_gamma(ns)
    reg2 = json.load(open(os.path.join(tmp, "reg.json")))
    assert "reason" in reg2["langs"]["lean"] and \
        "seed" in reg2["langs"]["lean"]["reason"], reg2["langs"]["lean"]
    # refusal on uncommitted registration
    ns3 = argparse.Namespace(runs_dir=ns.runs_dir, nll_dir=ns.nll_dir,
                             cs2_dir=ns.cs2_dir, reg=ns.reg,
                             skip_git_check=False)
    try:
        phase_envelope(ns3)
        raise AssertionError("envelope did not refuse uncommitted reg")
    except SystemExit as ex:
        assert "REFUSED" in str(ex.code), ex.code
    print("SELFTEST PASS", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["gamma", "envelope"])
    ap.add_argument("--langs", default="lean,python,cpp,latex")
    ap.add_argument("--stats",
                    default=os.path.join(BASE, "results_cs",
                                         "lang_stats.json"))
    ap.add_argument("--runs-dir",
                    default=os.path.join(BASE, "results_cs", "runs"))
    ap.add_argument("--nll-dir",
                    default=os.path.join(BASE, "results_cs", "nll"))
    ap.add_argument("--cs2-dir", default=os.path.join(BASE, "data", "cs2"))
    ap.add_argument("--reg", default=REG_PATH)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-git-check", action="store_true",
                    help="selftest only; never for real analyses")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    elif args.phase == "gamma":
        phase_gamma(args)
    elif args.phase == "envelope":
        phase_envelope(args)
    else:
        ap.error("--phase or --selftest required")


if __name__ == "__main__":
    main()
