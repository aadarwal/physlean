#!/usr/bin/env python3
"""ARM_CS CS-4 analyzer (frozen per ARM_CS §5/§6 BEFORE any ladder run).

Two-phase discipline:
  --phase gamma     reads ONLY the top-two-rung artifacts (top rung for the
                    estimate, second rung for the convergence rule) and the
                    registered CS-1 stats; writes
                    results_cs/registration_gamma.json with per-language
                    {gamma, H_inf, beta_corr, alpha_pred, alpha_pred_delta}
                    and input hashes. This file must then be COMMITTED.
  --phase envelope  REFUSES to run unless registration_gamma.json is
                    committed, clean, and byte-identical to HEAD's blob;
                    then computes L(P), the shifted-primary alpha_D fit,
                    m-sweep / leave-one-rung-out / seed / T sensitivities,
                    the raw-slope replication sensitivity, delta_n (paper
                    §5-style, along the P axis), and the shifted collapse
                    metric; writes results_cs/analysis_envelope.json.

Frozen constants (ARM_CS §6): gamma window n in [4, 64] (sensitivities
[4, 32], [8, 128]); convergence 0.02 b/B between top two rungs over the
window; H grid step 0.005 from 0 to min L_n; alpha shift-exclusion rule
L(P) - H_inf >= 0.02 with >= 4 surviving rungs; collapse over the gamma
window on a 20-point common log grid; delta_n for n in 1..12.

No figures here (CS-4 figures are a separate step). No frozen instrument
is read or written; results_cs/ only.
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

BASE = os.path.dirname(os.path.abspath(__file__))
LN2 = math.log(2)
GAMMA_WINDOW = (4, 64)
GAMMA_WINDOWS_SENS = [(4, 32), (8, 128)]
CONV_TOL = 0.02
H_STEP = 0.005
SHIFT_MIN = 0.02
MIN_RUNGS = 4
COLLAPSE_PTS = 20
DELTA_NS = list(range(1, 13))
REG_PATH = os.path.join("results_cs", "registration_gamma.json")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def load_runs(runs_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        r = json.load(open(p))
        if r.get("size") not in ("10m", "30m"):
            continue
        r["_path"] = p
        out.append(r)
    return out


def rung_map(cs2_dir, lang):
    man = json.load(open(os.path.join(cs2_dir, f"{lang}_cs2.json")))
    bounds = sorted(int(v) for v in man["rung_boundaries"].values())
    return bounds


def rung_of(r, bounds):
    for i, b in enumerate(bounds):
        if abs(r.get("train_bytes", -1) - b) <= 3:
            return i
    return None


def ln_curve(dump_path, n_max=4096):
    """Mean NLL (bits) per within-window position from a doc-reset dump."""
    sums = np.zeros(n_max + 1)
    cnts = np.zeros(n_max + 1, dtype=np.int64)
    with gzip.open(dump_path, "rt") as f:
        header = f.readline()
        assert header.startswith("win,"), dump_path
        for line in f:
            parts = line.rstrip("\n").split(",")
            n = int(parts[2])
            if 1 <= n <= n_max:
                sums[n] += float(parts[5])
                cnts[n] += 1
    with np.errstate(invalid="ignore"):
        return np.where(cnts > 0, sums / np.maximum(cnts, 1) / LN2,
                        np.nan), cnts


def seed_mean_curve(runs, nll_dir, n_max=4096):
    curves = []
    for r in runs:
        dump = os.path.join(nll_dir, f"{r['run']}__{r['lang']}__val.csv.gz")
        if not os.path.exists(dump):
            continue
        c, _ = ln_curve(dump, n_max)
        curves.append(c)
    if not curves:
        return None
    return np.nanmean(np.stack(curves), axis=0)


def gamma_fit(L, window):
    lo, hi = window
    ns = np.arange(lo, min(hi, len(L) - 1) + 1)
    y = L[ns]
    ok = ~np.isnan(y)
    ns, y = ns[ok], y[ok]
    if len(ns) < 8:
        return None
    best = None
    for H in np.arange(0.0, float(y.min()) - 1e-9, H_STEP):
        ly = np.log(y - H)
        lx = np.log(ns.astype(float))
        A = np.vstack([lx, np.ones_like(lx)]).T
        coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
        pred = A @ coef
        sst = float(((ly - ly.mean()) ** 2).sum())
        r2 = 1 - float(((ly - pred) ** 2).sum()) / sst if sst > 0 else -1
        if best is None or r2 > best["r2"]:
            best = dict(gamma=float(-coef[0]), H_inf=float(H), r2=r2)
    return best


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


def phase_gamma(args):
    stats = json.load(open(args.stats))
    runs = load_runs(args.runs_dir)
    reg = dict(schema="cs_registration_gamma_v1",
               stats_file=args.stats, stats_sha256=sha256_file(args.stats),
               constants=dict(window=GAMMA_WINDOW, conv_tol=CONV_TOL,
                              h_step=H_STEP), langs={})
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE,
                                capture_output=True,
                                text=True).stdout.strip()
    except OSError:
        commit = None
    reg["commit"] = commit
    for lang in args.langs.split(","):
        bounds = rung_map(args.cs2_dir, lang)
        top, second = len(bounds) - 1, len(bounds) - 2
        lruns = [r for r in runs if r["lang"] == lang and r["ctx"] == 4096
                 and r.get("doc_reset")]
        top_runs = [r for r in lruns if rung_of(r, bounds) == top
                    and "-r" in r["run"]]
        sec_runs = [r for r in lruns if rung_of(r, bounds) == second
                    and "-r" in r["run"]]
        entry = dict(n_top_runs=len(top_runs), n_second_runs=len(sec_runs),
                     inputs=sorted(r["run"] for r in top_runs + sec_runs))
        Lt = seed_mean_curve(top_runs, args.nll_dir)
        Ls = seed_mean_curve(sec_runs, args.nll_dir)
        if Lt is None or Ls is None:
            entry["reason"] = "missing top-two-rung doc-reset artifacts"
            reg["langs"][lang] = entry
            continue
        lo, hi = GAMMA_WINDOW
        ns = np.arange(lo, hi + 1)
        gap = float(np.nanmax(np.abs(Lt[ns] - Ls[ns])))
        entry["convergence_gap"] = gap
        if gap > CONV_TOL:
            entry["reason"] = (f"not converged: top-two-rung gap {gap:.4f}"
                               f" > {CONV_TOL}")
            reg["langs"][lang] = entry
            continue
        fit = gamma_fit(Lt, GAMMA_WINDOW)
        sens = {f"{w[0]}-{w[1]}": gamma_fit(Lt, w)
                for w in GAMMA_WINDOWS_SENS}
        per_seed = []
        for r in top_runs:
            c = seed_mean_curve([r], args.nll_dir)
            f1 = gamma_fit(c, GAMMA_WINDOW) if c is not None else None
            if f1:
                per_seed.append(f1["gamma"])
        entry.update(gamma=fit["gamma"], H_inf=fit["H_inf"], r2=fit["r2"],
                     gamma_window_sens={k: (v or {}).get("gamma")
                                        for k, v in sens.items()},
                     gamma_seed_spread=[min(per_seed), max(per_seed)]
                     if per_seed else None)
        scope = stats["scopes"].get(lang, {})
        sfit = scope.get("fit") or {}
        beta = sfit.get("beta_corr")
        ci = sfit.get("ci_doc_block_boot") or sfit.get("ci_lag_boot")
        entry["beta_corr"] = beta
        entry["beta_corr_ci"] = ci
        if beta:
            entry["alpha_pred"] = fit["gamma"] / (2 * beta)
            g_lo = min([fit["gamma"]] + [v for v in
                       entry["gamma_window_sens"].values() if v]
                       + (per_seed or []))
            g_hi = max([fit["gamma"]] + [v for v in
                       entry["gamma_window_sens"].values() if v]
                       + (per_seed or []))
            dg = (g_hi - g_lo) / 2
            db = ((ci[1] - ci[0]) / 2) if ci else 0.0
            entry["alpha_pred_delta"] = max(math.sqrt(
                (dg / (2 * beta)) ** 2
                + (fit["gamma"] * db / (2 * beta * beta)) ** 2), 0.03)
        reg["langs"][lang] = entry
    os.makedirs(os.path.dirname(os.path.join(BASE, args.reg)),
                exist_ok=True)
    with open(os.path.join(BASE, args.reg), "w") as f:
        json.dump(reg, f, indent=1)
    print(f"[gamma] wrote {args.reg} — COMMIT IT before --phase envelope")
    for lang, e in reg["langs"].items():
        print(f"  {lang}: " + (f"gamma={e['gamma']:.4f} "
                               f"H={e['H_inf']:.3f} "
                               f"alpha_pred={e.get('alpha_pred')}"
                               if "gamma" in e else e.get("reason", "?")))


def ols(x, y):
    A = np.vstack([np.log(np.asarray(x, float)),
                   np.ones(len(x))]).T
    coef, _, _, _ = np.linalg.lstsq(A, np.log(np.asarray(y, float)),
                                    rcond=None)
    return float(-coef[0])


def phase_envelope(args):
    if not args.skip_git_check and not git_blob_matches(args.reg):
        sys.exit(f"REFUSED: {args.reg} is not committed-clean-and-identical "
                 "to HEAD (ARM_CS §5 step 3)")
    reg = json.load(open(os.path.join(BASE, args.reg)))
    runs = load_runs(args.runs_dir)
    out = dict(schema="cs_analysis_envelope_v1",
               registration_sha256=sha256_file(os.path.join(BASE, args.reg)),
               langs={})
    for lang, rlang in reg["langs"].items():
        if "gamma" not in rlang:
            out["langs"][lang] = dict(reason="no registered gamma")
            continue
        H = rlang["H_inf"]
        gamma = rlang["gamma"]
        beta = rlang["beta_corr"]
        bounds = rung_map(args.cs2_dir, lang)
        lruns = [r for r in runs if r["lang"] == lang and r.get("doc_reset")
                 and "-r" in r["run"] and r.get("size") == "10m"]
        # L(P): seed-mean per (ctx, rung)
        LP = {}
        for r in lruns:
            k = (r["ctx"], rung_of(r, bounds))
            if k[1] is None:
                continue
            LP.setdefault(k, []).append(r["final_val_bpb"])
        curve = {}
        for (ctx, rung), vals in LP.items():
            curve.setdefault(ctx, {})[rung] = dict(
                mean=float(np.mean(vals)), n=len(vals),
                spread=[float(min(vals)), float(max(vals))],
                P=bounds[rung])
        e = dict(curves=curve)
        prim = curve.get(4096, {})
        Ps = [v["P"] for k, v in sorted(prim.items())
              if v["mean"] - H >= SHIFT_MIN]
        Ls = [v["mean"] for k, v in sorted(prim.items())
              if v["mean"] - H >= SHIFT_MIN]
        if len(Ps) < MIN_RUNGS:
            e["alpha_D"] = None
            e["reason"] = (f"only {len(Ps)} rungs survive the "
                           f"shift-exclusion rule")
        else:
            e["alpha_D"] = ols(Ps, [v - H for v in Ls])
            e["alpha_D_raw_sens"] = ols(Ps, Ls)
            e["m_sweep"] = {m: ols(Ps[:m], [v - H for v in Ls[:m]])
                            for m in range(MIN_RUNGS, len(Ps) + 1)}
            e["loo"] = [ols([p for j, p in enumerate(Ps) if j != i],
                            [v - H for j, v in enumerate(Ls) if j != i])
                        for i in range(len(Ps))]
            env = {}
            for ctx, d in curve.items():
                for rung, v in d.items():
                    env[rung] = min(env.get(rung, 9e9), v["mean"])
            eP = [bounds[r] for r in sorted(env) if env[r] - H >= SHIFT_MIN]
            eL = [env[r] - H for r in sorted(env) if env[r] - H >= SHIFT_MIN]
            if len(eP) >= MIN_RUNGS:
                e["alpha_D_envelopeT_sens"] = ols(eP, eL)
            ap, ad = rlang.get("alpha_pred"), rlang.get("alpha_pred_delta")
            if ap is not None:
                lo_a = min(e["loo"] + [e["alpha_D"]])
                hi_a = max(e["loo"] + [e["alpha_D"]])
                if lo_a >= ap - ad and hi_a <= ap + ad:
                    e["H3"] = "SUPPORTED"
                elif hi_a < ap - ad or lo_a > ap + ad:
                    e["H3"] = "REFUTED"
                else:
                    e["H3"] = "INDETERMINATE"
                e["H3_band"] = [ap - ad, ap + ad]
        # delta_n along the P axis (paper §5-style), n in 1..12
        deltas = {}
        by_rung = {}
        for r in lruns:
            if r["ctx"] != 4096:
                continue
            ru = rung_of(r, bounds)
            if ru is not None:
                by_rung.setdefault(ru, []).append(r)
        ln_by_rung = {ru: seed_mean_curve(rs, args.nll_dir, 128)
                      for ru, rs in by_rung.items()}
        for n in DELTA_NS:
            Ps_n, Ls_n = [], []
            for ru in sorted(ln_by_rung):
                c = ln_by_rung[ru]
                if c is not None and n < len(c) and not np.isnan(c[n]):
                    Ps_n.append(bounds[ru])
                    Ls_n.append(float(c[n]))
            if len(Ps_n) < MIN_RUNGS:
                continue
            best = None
            for Hn in np.arange(0.0, min(Ls_n) - 1e-9, H_STEP):
                d = ols(Ps_n, [v - Hn for v in Ls_n])
                ly = np.log(np.array(Ls_n) - Hn)
                lx = np.log(np.array(Ps_n, float))
                A = np.vstack([lx, np.ones_like(lx)]).T
                coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
                pred = A @ coef
                sst = float(((ly - ly.mean()) ** 2).sum())
                r2 = 1 - float(((ly - pred) ** 2).sum()) / sst \
                    if sst > 0 else -1
                if best is None or r2 > best[1]:
                    best = (d, r2, float(Hn))
            deltas[n] = dict(delta=best[0], r2=best[1], H_n=best[2])
        e["delta_n"] = deltas
        if deltas and beta:
            e["min_delta"] = min(v["delta"] for v in deltas.values())
            e["fast_learning"] = bool(
                e["min_delta"] >= gamma / (2 * beta))
        # shifted collapse metric over the gamma window
        lo, hi = GAMMA_WINDOW
        grids = []
        for ru in sorted(ln_by_rung):
            c = ln_by_rung[ru]
            if c is None:
                continue
            ns = np.arange(lo, min(hi, len(c) - 1) + 1)
            y = (c[ns] - H) * ns ** gamma
            x = bounds[ru] / ns ** (2 * beta)
            ok = ~np.isnan(y) & (y > 0)
            if ok.sum() > 5:
                grids.append((np.log(x[ok]), np.log(y[ok])))
        if len(grids) >= 3:
            gx = np.linspace(max(g[0].min() for g in grids),
                             min(g[0].max() for g in grids), COLLAPSE_PTS)
            if gx[0] < gx[-1]:
                interp = [np.interp(gx, g[0][np.argsort(g[0])],
                                    g[1][np.argsort(g[0])]) for g in grids]
                M = np.stack(interp)
                e["collapse_metric"] = float(
                    M.var(axis=0).mean() / max(M.mean(axis=0).var(), 1e-12))
                e["collapse_n_curves"] = len(grids)
        out["langs"][lang] = e
    path = os.path.join(BASE, os.path.dirname(args.reg),
                        "analysis_envelope.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"[envelope] wrote {path}")
    for lang, e in out["langs"].items():
        print(f"  {lang}: alpha_D={e.get('alpha_D')} "
              f"H3={e.get('H3')} min_delta={e.get('min_delta')} "
              f"collapse={e.get('collapse_metric')}")


def selftest():
    """Synthetic recovery: fabricate runs/dumps with known gamma/H/alpha."""
    import tempfile
    rng = np.random.default_rng(3)
    tmp = tempfile.mkdtemp(prefix="cs_selftest_")
    runs_d = os.path.join(tmp, "runs")
    nll_d = os.path.join(tmp, "nll")
    cs2_d = os.path.join(tmp, "cs2")
    for d in (runs_d, nll_d, cs2_d):
        os.makedirs(d)
    g_true, H_true, a_true, beta = 0.4, 0.9, 0.16, 0.7
    bounds = [int(50e6 * f) for f in (1/64, 1/32, 1/16, 1/8, 1/4, 1/2, 1)]
    json.dump(dict(rung_boundaries={str(i): b for i, b in
                                    enumerate(bounds)},
                   val_doc_offsets=[]),
              open(os.path.join(cs2_d, "toy_cs2.json"), "w"))
    for ri, P in enumerate(bounds):
        for seed in (0, 1, 2):
            LP = H_true + 3.0 * P ** -a_true
            run = f"scratch-10m-toy-s{seed}-r{ri}"
            json.dump(dict(run=run, lang="toy", size="10m", seed=seed,
                           ctx=4096, lr=1e-3, epochs=2, doc_reset=True,
                           train_bytes=P, final_val_bpb=LP,
                           tokens_seen=P),
                      open(os.path.join(runs_d, run + ".json"), "w"))
            if ri >= len(bounds) - 2:
                with gzip.open(os.path.join(
                        nll_d, f"{run}__toy__val.csv.gz"), "wt") as f:
                    f.write("win,doc,ctxb,blen,tok,nll\n")
                    for w in range(60):
                        for n in range(1, 200):
                            ln_bits = (H_true + 1.6 * n ** -g_true
                                       + rng.normal(0, 0.02))
                            f.write(f"{w},0,{n},1,65,"
                                    f"{max(ln_bits, 0.01) * LN2:.5f}\n")
    stats = dict(scopes=dict(toy=dict(fit=dict(
        beta_corr=beta, ci_doc_block_boot=[beta - 0.02, beta + 0.02]))))
    stats_p = os.path.join(tmp, "lang_stats.json")
    json.dump(stats, open(stats_p, "w"))
    ns = argparse.Namespace(stats=stats_p, runs_dir=runs_d, nll_dir=nll_d,
                            cs2_dir=cs2_d, langs="toy",
                            reg=os.path.relpath(
                                os.path.join(tmp, "reg.json"), BASE))
    phase_gamma(ns)
    reg = json.load(open(os.path.join(tmp, "reg.json")))
    e = reg["langs"]["toy"]
    assert abs(e["gamma"] - g_true) < 0.08, f"gamma {e['gamma']}"
    assert abs(e["H_inf"] - H_true) < 0.05, f"H {e['H_inf']}"
    ns2 = argparse.Namespace(runs_dir=runs_d, nll_dir=nll_d, cs2_dir=cs2_d,
                             reg=ns.reg, skip_git_check=True)
    phase_envelope(ns2)
    env = json.load(open(os.path.join(tmp, "analysis_envelope.json")))
    a = env["langs"]["toy"]["alpha_D"]
    assert a is not None and abs(a - a_true) < 0.03, f"alpha {a}"
    # refusal check: envelope must refuse an uncommitted registration
    ns3 = argparse.Namespace(runs_dir=runs_d, nll_dir=nll_d, cs2_dir=cs2_d,
                             reg=ns.reg, skip_git_check=False)
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
