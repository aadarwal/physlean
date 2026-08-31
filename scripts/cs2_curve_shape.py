#!/usr/bin/env python3
"""Print the pooled top-rung L_n curve and its power-law residuals.

lean's descriptive gamma fit was refused by the estimator's own adequacy
gate (r2 0.843 < 0.9): the loss-vs-context curve is not well described by
a single power law over the fit window. The three languages whose runs we
know are damaged fit a power law easily (r2 0.98-0.99). Printing the
curve makes the shape — and where it departs from a straight line in
log-log — inspectable rather than asserted.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
import analyze_cs as A  # noqa: E402

RUNS, NLL, CS2 = "results_cs/runs", "results_cs/nll", "data/cs2"
LANGS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["lean"]

runs = A.load_runs(RUNS, cs2_dir=CS2)
for lang in LANGS:
    bounds = A.rung_map(CS2, lang)
    top = len(bounds) - 1
    lruns = [r for r in runs if r["lang"] == lang and r["ctx"] == 4096
             and A.rung_of(r, bounds) == top]
    m, v, _ = A.pooled_binned(lruns, NLL)
    if m is None:
        print(f"{lang}: no curve")
        continue
    fit = A.gamma_fit(m, v)
    print(f"\n=== {lang} top rung: pooled L_n (window {A.GAMMA_LO}-{A.GAMMA_HI}) ===")
    print(f"gamma_fit: {({k: (round(x, 4) if isinstance(x, float) else x) for k, x in fit.items() if not isinstance(x, (list, dict))})}")
    win = (m >= A.GAMMA_LO) & (m <= A.GAMMA_HI)
    mw, vw = m[win], v[win]
    print(f"{'n':>7} {'L_n':>9}   drop-per-octave")
    prev_n = prev_v = None
    for n, val in zip(mw, vw):
        rate = ""
        if prev_v is not None and n > prev_n:
            oct_ = np.log2(n / prev_n)
            if oct_ > 0:
                rate = f"{(prev_v - val) / oct_:+.4f}"
        print(f"{n:7.0f} {val:9.4f}   {rate}")
        prev_n, prev_v = n, val
    # how much of the curve is flat tail?
    if len(vw) > 6:
        head = (vw[0] - vw[len(vw) // 2])
        tail = (vw[len(vw) // 2] - vw[-1])
        print(f"\n  first-half drop {head:.4f} b/B, second-half drop {tail:.4f} b/B"
              f"  (ratio {tail / head:.2f})" if head else "")
