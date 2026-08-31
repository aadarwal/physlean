#!/usr/bin/env python3
"""How fast is the CS-2 convergence gap closing, and is CONV_TOL reachable?

The gamma phase requires the pooled per-position loss curve to stop
moving between the top two rungs: max_n |L_n(top) - L_n(second)| <=
CONV_TOL (0.02 b/B). lean failed at 0.1945. The decision that matters is
whether that is one or two more doublings of data away, or many.

This computes the same statistic for EVERY adjacent rung pair, so the
trend across the ladder is visible, and extrapolates geometrically to
estimate the doublings still needed. Read-only: it imports the frozen
analyzer's own helpers rather than reimplementing them, so the numbers
are the analyzer's, not a lookalike.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
import analyze_cs as A  # noqa: E402

RUNS = "results_cs/runs"
NLL = "results_cs/nll"
CS2 = "data/cs2"
LANGS = sys.argv[1].split(",") if len(sys.argv) > 1 else \
    ["lean", "python", "cpp", "latex"]

runs = A.load_runs(RUNS, cs2_dir=CS2)
for lang in LANGS:
    bounds = A.rung_map(CS2, lang)
    lruns = [r for r in runs if r["lang"] == lang and r["ctx"] == 4096]
    print(f"\n=== {lang} (CONV_TOL={A.CONV_TOL}) ===")
    curves = {}
    for k in range(len(bounds)):
        rk = [r for r in lruns if A.rung_of(r, bounds) == k]
        if not rk:
            continue
        m, v, _used = A.pooled_binned(rk, NLL)
        if m is not None:
            curves[k] = (m, v)
    gaps = []
    for k in sorted(curves):
        if k - 1 not in curves:
            continue
        (mt, vt), (ms, vs) = curves[k], curves[k - 1]
        common = np.intersect1d(mt, ms)
        gap = float(np.max(np.abs(np.interp(common, mt, vt)
                                  - np.interp(common, ms, vs))))
        gaps.append((k, gap))
        print(f"  rung {k-1}->{k}: gap = {gap:.4f}"
              + ("  (the registered top-two gap)" if k == max(curves) else ""))
    if len(gaps) >= 2:
        ratios = [gaps[i][1] / gaps[i - 1][1]
                  for i in range(1, len(gaps)) if gaps[i - 1][1] > 0]
        if ratios:
            r = sum(ratios[-3:]) / len(ratios[-3:])  # recent shrink factor
            last = gaps[-1][1]
            print(f"  recent per-doubling shrink factor ~ {r:.3f}")
            if r >= 1:
                print("  gap is NOT shrinking — more data alone will not "
                      "reach CONV_TOL")
            elif last > A.CONV_TOL:
                n = math.log(A.CONV_TOL / last) / math.log(r)
                print(f"  => ~{n:.1f} more doublings of data to reach "
                      f"CONV_TOL, i.e. ~{2**n:.0f}x the current top rung")
