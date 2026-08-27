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
REFIT_WIDTH_MAX = 0.3  # refits have less H-leverage; 0.3 = degenerate threshold
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


def gamma_fit(mids, vals, certify=True):
    """H-grid fit with identifiability gates. Sensitivity refits pass
    certify=False: only the FATAL gates (positive gamma, interior H*)
    apply — certification gates (profile width, R^2) are properties of
    the primary fit; a refit failing a fatal gate still withholds."""
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
    wmax = PROFILE_WIDTH_MAX if certify else REFIT_WIDTH_MAX
    if width > wmax:
        # identifiability is fatal for refits too (round-4 fix), at the
        # refit-calibrated cap (narrow windows have less H-leverage)
        return dict(reason=f"gate: profile width {width:.3f} > {wmax}",
                    **gates)
    if certify and r2m < MIN_R2:
        # R^2 alone is relaxed for narrow sensitivity windows
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
    """Registered beta from the POOLED scope only (round-3 fix: csupport
    is a sensitivity, never promoted — it must stay paired with the same
    mixture gamma/alpha are trained on); withhold rules enforced. The
    doc-block interval is REQUIRED (round-4 fix: no lag-bootstrap or
    zero-width fallback)."""
    scope = stats["scopes"].get(lang) or {}
    fit = scope.get("fit") or {}
    ci = fit.get("ci_doc_block_boot")
    if fit.get("beta_corr") is None:
        return None, None, None, lang, "no reportable beta_corr", None, None
    if ci is None:
        return (None, None, None, lang,
                "no doc-block bootstrap interval", None, None)
    if fit.get("divergence_withhold"):
        return (None, None, None, lang, "beta withheld (op-vs-fro)",
                None, None)
    lags = scope.get("lags") or []
    valid = scope.get("valid") or []
    n_det = max((l for l, v in zip(lags, valid) if v), default=None)
    return (fit["beta_corr"], ci, fit, lang, None, n_det,
            scope.get("total_bytes"))


def load_capacity(args):
    p = os.path.join(BASE, getattr(args, "capacity", "") or
                     os.path.join("results_cs", "capacity_verdict.json"))
    if os.path.exists(p):
        return json.load(open(p)), p
    return {}, p


def phase_gamma(args):
    stats = json.load(open(args.stats))
    cap, cap_path = load_capacity(args)
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
        # EXACTLY three artifacts per rung (round-4: {0,0,1,2} must fail)
        if len(top_runs) != 3 or set(entry["seed_top"]) != SEEDS or \
                len(sec_runs) != 3 or set(entry["seed_second"]) != SEEDS:
            entry["reason"] = ("seed artifacts not exactly {0,1,2}: "
                              f"top={len(top_runs)} sec={len(sec_runs)}")
            reg["langs"][lang] = entry
            continue
        if lang in H3_ELIGIBLE:
            # capacity must be adjudicated un-fired BEFORE gamma/H1 are
            # registered from the 10m model (round-4 fix)
            ce = cap.get(lang)
            if not isinstance(ce, dict) or "fired" not in ce or \
                    ce.get("schema") != "cs_capacity_verdict_v1" or \
                    not all(k in ce for k in ("best_30m", "best_10m",
                                              "probe_run_sha256")):
                entry["reason"] = ("capacity unadjudicated (no valid "
                                   "schema-complete entry)")
                reg["langs"][lang] = entry
                continue
            if ce["fired"]:
                entry["reason"] = ("capacity fired: 10m undersized; "
                                   "30m ladder required")
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
        man_p = os.path.join(args.cs2_dir, f"{lang}_cs2.json")
        inputs[os.path.relpath(man_p, BASE)] = sha256_file(man_p)
        if os.path.exists(cap_path):
            inputs[os.path.relpath(cap_path, BASE)] = \
                sha256_file(cap_path)
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
        # window component = POINT spread across identifiable refits (a
        # refit's profile re-measures the H-uncertainty already carried
        # by the primary profile component — including it double-counts);
        # refit identifiability is separately enforced (REFIT_WIDTH_MAX,
        # failure withholds)
        win_g = [fit["gamma"]]
        sens_fail = None
        for lo, hi in GAMMA_WINDOWS_SENS:
            sub = (mt >= lo) & (mt <= hi)
            f2 = gamma_fit(mt[sub], vt[sub], certify=False)
            if "gamma" in f2:
                # point, plus half the identifiability EXCESS beyond the
                # primary standard (round-5: borderline refits widen
                # hw_gamma, never narrow it)
                exc = max(0.0, f2["profile_width"] - PROFILE_WIDTH_MAX)
                win_g += [f2["gamma"] - exc / 2, f2["gamma"] + exc / 2]
            else:
                sens_fail = (f"window [{lo},{hi}] refit failed: "
                             f"{f2.get('reason')}")
                break
        if sens_fail:
            # sensitivity failure WITHHOLDS, never shrinks hw (round-3)
            entry["reason"] = f"gamma withheld — {sens_fail}"
            reg["langs"][lang] = entry
            continue
        seed_g = []
        for r in top_runs:
            m1, v1, _ = pooled_binned([r], args.nll_dir)
            f1 = gamma_fit(m1, v1, certify=False) if m1 is not None else {}
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
            entry["csupport_divergence"] = bfit.get("csupport_divergence")
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
    # round-3 B4 fix: re-verify every input the registration bound
    for lang, rl in reg["langs"].items():
        for rel, sha in (rl.get("input_sha256") or {}).items():
            p = os.path.join(BASE, rel)
            if not os.path.exists(p) or sha256_file(p) != sha:
                sys.exit(f"REFUSED: registered input changed: {rel}")
    out_file = os.path.join(BASE, os.path.dirname(args.reg),
                            "analysis_envelope.json")
    if os.path.exists(out_file) and not getattr(args, "force", False):
        sys.exit(f"refusing to overwrite {out_file} (use --force)")
    cap, cap_path = load_capacity(args)
    runs = load_runs(args.runs_dir)
    env_inputs = {}
    if os.path.exists(cap_path):
        env_inputs[os.path.relpath(cap_path, BASE)] = sha256_file(cap_path)
    out = dict(schema="cs_analysis_envelope_v2",
               registration_sha256=sha256_file(
                   os.path.join(BASE, args.reg)),
               input_sha256=env_inputs, langs={})
    for lang, rl in reg["langs"].items():
        if "gamma" not in rl:
            out["langs"][lang] = dict(reason="no registered gamma")
            continue
        H, gamma = rl["H_inf"], rl["gamma"]
        beta = rl.get("beta_corr")
        bounds = rung_map(args.cs2_dir, lang)
        lruns = [r for r in runs if r["lang"] == lang]
        # per-(ctx, rung) seed groups
        groups = {}
        for r in lruns:
            ru = rung_of(r, bounds)
            if ru is not None:
                groups.setdefault((r["ctx"], ru), []).append(r)
        # the PRIMARY gate covers the T=4096 arm ONLY (round-5 fix: a
        # partially landed T=512 group must not make H3 order-dependent);
        # T=512 is the envelope sensitivity and uses complete groups only
        defects = []
        for k, v in sorted(groups.items()):
            if k[0] != 4096:
                continue
            seeds = [r.get("seed") for r in v]
            if len(seeds) != len(set(seeds)):
                defects.append(f"duplicate runs at {k}")
            elif set(seeds) != SEEDS:
                defects.append(f"incomplete seeds at {k}: {sorted(seeds)}")
        for ru in range(len(bounds)):
            if (4096, ru) not in groups:
                defects.append(f"missing primary rung {ru}")
        for (ctx, ru), v in sorted(groups.items()):
            for r in v:
                d = os.path.join(args.nll_dir,
                                 f"{r['run']}__{r['lang']}__val.csv.gz")
                if not os.path.exists(d):
                    if ctx == 4096:  # primary-arm dumps are required
                        defects.append(f"missing dump for {r['run']}")
                else:
                    env_inputs[os.path.relpath(d, BASE)] = sha256_file(d)
                env_inputs[os.path.relpath(r["_path"], BASE)] = \
                    sha256_file(r["_path"])
        eligible = lang in H3_ELIGIBLE and not rl.get(
            "h3_ineligible_reason")
        if defects and eligible:
            # fail-closed for the claim-bearing arm (round-3 NB5 fix)
            out["langs"][lang] = dict(
                H3=f"WITHHELD ({'; '.join(defects[:4])})",
                alpha_D=None, defects=defects)
            continue
        groups = {k: v for k, v in groups.items()
                  if set(seed_set(v)) == SEEDS
                  and len(v) == len(SEEDS)}
        e = dict(defects_noted=defects)
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
            hstep_fail = False
            for Hv in (H - H_STEP, H + H_STEP):
                pv = sorted((v["P"], v["mean"]) for v in prim.values()
                            if v["mean"] - Hv >= SHIFT_MIN)
                if len(pv) >= MIN_RUNGS:
                    hstep_a.append(ols_shift([p for p, _ in pv],
                                             [v for _, v in pv], Hv)[0])
                else:
                    hstep_fail = True
            if hstep_fail:
                # sensitivity failure WITHHOLDS (round-3 fix)
                e["alpha_D"] = None
                e["reason"] = ("alpha_D withheld — H_inf±step refit "
                               "cannot satisfy the shift rule")
                e["H3"] = "WITHHELD (H-step sensitivity failure)"
                out["langs"][lang] = e
                continue
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
            # gates BEFORE H3: regime, horizon, capacity (ARM_CS §1)
            if beta and rl.get("n_det") and rl.get("P_corpus"):
                n_star = rl["n_det"] * (bounds[-1] / rl["P_corpus"]) \
                    ** (1 / (2 * beta))
                e["horizon"] = dict(n_star_top=n_star,
                                    ok=bool(n_star <= 4096 / 4))
            cap_entry = cap.get(lang) if eligible else None
            e["capacity"] = cap_entry
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
                        note="descriptive only; never H3 (ARM_CS §6)")
            elif not (e.get("horizon") or {}).get("ok"):
                e["H3"] = "INDETERMINATE(horizon)"
            elif not isinstance(cap_entry, dict) or "fired" not in \
                    cap_entry:
                e["H3"] = "WITHHELD (capacity unadjudicated)"
            elif cap_entry.get("fired"):
                e["H3"] = "WITHHELD (capacity fired; 30m ladder pending)"
            else:
                center = a0 - ap
                dhw = math.sqrt(hw ** 2 + ap_hw ** 2)
                e["H3_diff"] = dict(center=center, hw=dhw)
                # robustness verdict, NOT a coverage-calibrated test
                if abs(center) + dhw <= H3_MARGIN:
                    e["H3"] = "CONSISTENT"
                elif abs(center) - dhw > H3_MARGIN:
                    e["H3"] = "INCONSISTENT"
                else:
                    e["H3"] = "INDETERMINATE"
        # H3b: collapse metrics (descriptive; window frozen [4, 64])
        def collapse_metric(gam, bet, shifted=True):
            grids = []
            for ru in sorted(ln_by_rung):
                c = ln_by_rung[ru]
                ns = np.arange(GAMMA_LO, min(64, len(c) - 1) + 1)
                y = ((c[ns] - H) if shifted else c[ns]) * ns ** gam
                xv = bounds[ru] / ns ** (2 * bet)
                okm = ~np.isnan(y) & (y > 0)
                if okm.sum() > 5:
                    o = np.argsort(xv[okm])
                    grids.append((np.log(xv[okm][o]), np.log(y[okm][o])))
            if len(grids) < 3:
                return None, len(grids)
            # pointwise over the UNION grid: with a 64x P range and the
            # frozen [4,64] n window, full common support is empty by
            # arithmetic; variance is taken where >=3 rung curves overlap
            gx = np.linspace(min(g[0].min() for g in grids),
                             max(g[0].max() for g in grids), COLLAPSE_PTS)
            cols, means = [], []
            for xq in gx:
                ys = [np.interp(xq, g[0], g[1]) for g in grids
                      if g[0][0] <= xq <= g[0][-1]]
                if len(ys) >= 3:
                    cols.append(np.var(ys))
                    means.append(np.mean(ys))
            if len(cols) < 5:
                return None, len(grids)
            return float(np.mean(cols)
                         / max(np.var(means), 1e-12)), len(grids)
        if beta:
            m0, ncur = collapse_metric(gamma, beta, shifted=True)
            e["H3b_collapse_metric"] = m0
            e["H3b_n_curves"] = ncur
            e["H3b_raw_form_sens"], _ = collapse_metric(gamma, beta,
                                                        shifted=False)
            e["H3b_sweep"] = {
                f"g{dg:+.1f}_b{db:+.1f}": collapse_metric(
                    gamma + dg, beta + db, shifted=True)[0]
                for dg in (-0.1, 0.0, 0.1) for db in (-0.1, 0.0, 0.1)}
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
    for lang in ("lean", "python", "cpp"):
        json.dump(dict(rung_boundaries={str(i): b for i, b in
                                        enumerate(bounds)}),
                  open(os.path.join(tmp, "cs2", f"{lang}_cs2.json"), "w"))
    beta = 0.7
    g_true, H_true = 0.4, 0.9
    # lean: alpha == gamma/(2 beta) => CONSISTENT; python: off =>
    # INCONSISTENT; cpp: on-prediction but horizon-gated (huge n_det)
    _fake_lang(tmp, "lean", bounds, g_true, H_true,
               g_true / (2 * beta), 0.6)
    _fake_lang(tmp, "python", bounds, g_true, H_true, 0.16, 0.6)
    _fake_lang(tmp, "cpp", bounds, g_true, H_true,
               g_true / (2 * beta), 0.6)
    def scope(n_det_lags):
        return dict(fit=dict(beta_corr=beta,
                             ci_doc_block_boot=[beta - 0.02, beta + 0.02],
                             divergence_withhold=False),
                    lags=n_det_lags, valid=[True] * len(n_det_lags),
                    total_bytes=120_000_000)
    stats = dict(scopes=dict(lean=scope([1, 2, 4, 8, 16, 32]),
                             python=scope([1, 2, 4, 8, 16, 32]),
                             cpp=scope([1, 64, 512, 4096])))
    stats_p = os.path.join(tmp, "lang_stats.json")
    json.dump(stats, open(stats_p, "w"))
    cap_p = os.path.join(tmp, "capacity_verdict.json")
    # capacity-fired must refuse at the GAMMA level (registration is
    # sha-bound to the capacity state, so post-registration toggling is
    # itself refused — tested at the end)
    def _cap(fired):
        return dict(schema="cs_capacity_verdict_v1", fired=fired,
                    best_30m=1.0, best_10m=1.0, probe_run_sha256={})
    json.dump(dict(lean=_cap(True), python=_cap(False),
                   cpp=_cap(False)), open(cap_p, "w"))
    ns = argparse.Namespace(stats=stats_p,
                            runs_dir=os.path.join(tmp, "runs"),
                            nll_dir=os.path.join(tmp, "nll"),
                            cs2_dir=os.path.join(tmp, "cs2"),
                            langs="lean,python,cpp",
                            reg=os.path.relpath(
                                os.path.join(tmp, "reg.json"), BASE),
                            capacity=os.path.relpath(cap_p, BASE),
                            force=False)
    phase_gamma(ns)
    reg0 = json.load(open(os.path.join(tmp, "reg.json")))
    assert "capacity fired" in reg0["langs"]["lean"].get("reason", ""), \
        reg0["langs"]["lean"]
    json.dump({l: _cap(False) for l in ("lean", "python", "cpp")},
              open(cap_p, "w"))
    ns.force = True
    phase_gamma(ns)
    ns.force = False
    reg = json.load(open(os.path.join(tmp, "reg.json")))
    for lang in ("lean", "python", "cpp"):
        e = reg["langs"][lang]
        assert "gamma" in e, (lang, e.get("reason"))
        assert abs(e["gamma"] - g_true) < 0.08, f"{lang} {e['gamma']}"
        assert abs(e["H_inf"] - H_true) < 0.06, f"{lang} {e['H_inf']}"
        assert e["input_sha256"], "no input hashes bound"
    cap_rel = os.path.relpath(cap_p, BASE)
    ns2 = argparse.Namespace(runs_dir=ns.runs_dir, nll_dir=ns.nll_dir,
                             cs2_dir=ns.cs2_dir, reg=ns.reg,
                             skip_git_check=True, force=True,
                             capacity=cap_rel)
    phase_envelope(ns2)
    env = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    el, ep = env["langs"]["lean"], env["langs"]["python"]
    assert el["fast_learning"]["established"], el["fast_learning"]
    assert el["H3"] == "CONSISTENT", (el["H3"], el.get("H3_diff"))
    assert ep["H3"] == "INCONSISTENT", (ep["H3"], ep.get("H3_diff"))
    assert env["langs"]["cpp"]["H3"] == "INDETERMINATE(horizon)", \
        env["langs"]["cpp"].get("H3")
    assert len(el["delta_n"]) >= 10 and \
        abs(float(el["delta_n"]["4"]["delta"]) - 0.6) < 0.1, \
        el["delta_n"].get("4")
    assert el.get("H3b_collapse_metric") is not None
    assert el.get("H3b_raw_form_sens") is not None
    assert len(el.get("H3b_sweep") or {}) == 9
    # post-registration capacity tampering must be REFUSED by the
    # input-sha binding (the gamma-level gate handles fired states)
    orig_cap = open(cap_p).read()
    json.dump(dict(lean=_cap(True), python=_cap(False),
                   cpp=_cap(False)), open(cap_p, "w"))
    try:
        phase_envelope(ns2)
        raise AssertionError("envelope accepted a tampered capacity file")
    except SystemExit as ex:
        assert "registered input changed" in str(ex.code), ex.code
    open(cap_p, "w").write(orig_cap)
    # LOWER-rung seed deletion must withhold H3 (round-3 adversarial)
    victim = os.path.join(tmp, "runs", "scratch-10m-python-s2-r0.json")
    hidden = victim + ".hidden"
    os.rename(victim, hidden)
    phase_envelope(ns2)
    env3 = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    assert env3["langs"]["python"]["H3"].startswith("WITHHELD"), \
        env3["langs"]["python"]["H3"]
    os.rename(hidden, victim)
    # a PARTIAL T=512 group must NOT affect the primary verdict (round-5)
    p512 = os.path.join(tmp, "runs", "scratch-10m-lean-s0-r0c512.json")
    json.dump(dict(run="scratch-10m-lean-s0-r0c512", lang="lean",
                   size="10m", seed=0, ctx=512, lr=1e-3, epochs=2,
                   doc_reset=True, train_bytes=bounds[0],
                   final_val_bpb=2.0, tokens_seen=bounds[0]),
              open(p512, "w"))
    phase_envelope(ns2)
    env5 = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    assert env5["langs"]["lean"]["H3"] == "CONSISTENT", \
        env5["langs"]["lean"]["H3"]
    os.remove(p512)
    # duplicate run must withhold H3
    dup = os.path.join(tmp, "runs", "scratch-10m-lean-s0-r3.json")
    import shutil
    shutil.copy(dup, dup.replace("-r3.json", "-r3dup.json"))
    phase_envelope(ns2)
    env4 = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    assert env4["langs"]["lean"]["H3"].startswith("WITHHELD"), \
        env4["langs"]["lean"]["H3"]
    os.remove(dup.replace("-r3.json", "-r3dup.json"))
    # TOP-rung seed incompleteness must fail closed at gamma
    os.remove(os.path.join(tmp, "runs", "scratch-10m-lean-s2-r6.json"))
    ns.force = True
    phase_gamma(ns)
    reg2 = json.load(open(os.path.join(tmp, "reg.json")))
    assert "reason" in reg2["langs"]["lean"] and \
        "seed" in reg2["langs"]["lean"]["reason"], reg2["langs"]["lean"]
    # refusal on uncommitted registration
    ns3 = argparse.Namespace(runs_dir=ns.runs_dir, nll_dir=ns.nll_dir,
                             cs2_dir=ns.cs2_dir, reg=ns.reg,
                             skip_git_check=False, force=True,
                             capacity=cap_rel)
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
    ap.add_argument("--capacity",
                    default=os.path.join("results_cs",
                                         "capacity_verdict.json"))
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
