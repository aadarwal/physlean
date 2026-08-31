#!/usr/bin/env python3
"""DESCRIPTIVE gamma from the top rung — explicitly NOT a registration.

The gamma phase refused every language (lean: convergence gap 0.1945 >
0.02). That refusal is about validity, not about missing data: the runs
finished and the curves exist. This prints what the frozen estimator
would say if it were applied to the top-rung curve anyway, so the size
and direction of what we are giving up is visible.

Everything printed here is EXPLORATORY (ARM_CS §5.5 declares the whole
first pass exploratory) and is NOT written to any registration file.

Bias direction: L_n = H_n + KL_n. The model is data-limited, and its
shortfall KL_n is expected to GROW with context position n (long-range
structure is what a data-starved model learns last). That flattens the
measured curve relative to the language's true H_n, so a gamma read off
it is biased DOWNWARD — and so is any alpha_pred = gamma / (2*beta)
derived from it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze_cs as A  # noqa: E402

RUNS, NLL, CS2 = "results_cs/runs", "results_cs/nll", "data/cs2"
LANGS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["lean", "python", "cpp", "latex"]

stats = json.load(open("results_cs/lang_stats.json"))


def beta_of(lang):
    for row in (stats if isinstance(stats, list) else stats.get("scopes", [])):
        if row.get("scope") == lang:
            return row.get("beta_corr")
    return None


runs = A.load_runs(RUNS, cs2_dir=CS2)
print("DESCRIPTIVE ONLY — not registered, gates not satisfied\n")
for lang in LANGS:
    bounds = A.rung_map(CS2, lang)
    top = len(bounds) - 1
    lruns = [r for r in runs if r["lang"] == lang and r["ctx"] == 4096
             and A.rung_of(r, bounds) == top]
    if not lruns:
        print(f"{lang}: no top-rung runs")
        continue
    m, v, _u = A.pooled_binned(lruns, NLL)
    if m is None:
        print(f"{lang}: no pooled curve")
        continue
    fit = A.gamma_fit(m, v)
    g = fit.get("gamma")
    b = beta_of(lang)
    line = f"{lang:8s} n_seeds={len(lruns)}"
    if g is None:
        print(line + f"  gamma_fit refused: {fit.get('reason')}")
        continue
    line += (f"  gamma~{g:.4f}  H_inf~{fit.get('h_inf', float('nan')):.4f}"
             f"  R2={fit.get('r2', float('nan')):.4f}")
    if b:
        line += f"  beta_corr={b:.4f}  =>  alpha_pred = g/(2b) ~ {g / (2 * b):.4f}"
    print(line)
print("\nReminder: these are lower-bound-flavoured, not the registered "
      "quantities. The convergence gate refused them for a reason.")
