#!/usr/bin/env python3
"""Descriptive plots ONLY, from the analyzer-v3 schema (PREREG §6).

Deliberately narrow at this gate:
  - binned BPB curves per (model, kind) for QUANTITATIVE, matched cells
    (XL/descriptive cells drawn thin+dashed and labeled, never compared),
  - context-gain dot plot with window-bootstrap CIs,
  - the preregistered contamination view: clean-target-MASKED vs full BPB
    per corpus (all-new streams are a robustness arm, not plotted here).
NO beta/Linf headline plots: fits appear only in fits.csv and only where
the frozen holdout gate accepted them. No Phase 2 plots (G6 blocked).
Refuses to run on a pre-v3 fits.json.
"""
import json, math, os, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "results_v2")
OUT = os.path.join(RES, "plots")

COLOR = {"physlib": "#2a78d6", "mathlib": "#eb6834", "qutip": "#1baf7a",
         "sympy": "#eda100", "geant4": "#e87ba4",
         "arxiv_old": "#008300", "arxiv_new": "#008300"}
LSTYLE = {"physlib": "-", "mathlib": "-",
          "qutip": (0, (4, 2)), "sympy": (0, (4, 2)),
          "geant4": (0, (5, 2, 1, 2)),
          "arxiv_old": (0, (1, 2)), "arxiv_new": (0, (1, 2))}
INK, INK2 = "#0b0b0b", "#52514e"
plt.rcParams.update({
    "figure.dpi": 200, "font.size": 9, "axes.edgecolor": INK2,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "grid.color": "#d9d8d3", "grid.linewidth": 0.6, "legend.frameon": False,
    "savefig.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb"})


def load():
    p = os.path.join(RES, "fits.json")
    if not os.path.exists(p):
        sys.exit("no results_v2/fits.json — run analyze_v2.py first")
    d = json.load(open(p))
    if "cells" not in d:
        sys.exit("fits.json is pre-v3 schema; refusing to plot from it")
    if d.get("errors"):
        sys.exit(f"refusing to plot a PARTIAL analysis: fits.json carries "
                 f"{len(d['errors'])} analyzer errors — fix those first "
                 f"(first: {d['errors'][0]})")
    return d["cells"]


def fig_curves(cells):
    by = defaultdict(dict)
    for name, c in cells.items():
        short, corpus, kind = name.split("__", 2)
        by[(short, kind)][corpus] = c["main"]
    for (short, kind), per in sorted(by.items()):
        fig, ax = plt.subplots(figsize=(5.4, 3.8))
        drew = 0
        for corpus, r in sorted(per.items()):
            b = r["bins"]
            if not b["mid"]:
                continue
            quant = r["quantitative"] and not r["unmatched"]
            label = (f"{corpus} (w={r['n_windows']},d={r['n_docs']}"
                     f"{'' if quant else '; descriptive'})")
            ax.plot(b["mid"], b["bpb"], color=COLOR.get(corpus, INK2),
                    ls=LSTYLE.get(corpus, "-") if quant else (0, (1, 1)),
                    lw=2 if quant else 1, alpha=1 if quant else .5,
                    marker="o", ms=3 if quant else 2, label=label)
            drew += 1
        if not drew:
            plt.close(fig)
            continue
        ax.set_xscale("log")
        ax.set_xlabel("in-context bytes c (window-relative)")
        ax.set_ylabel("bits per byte")
        # Keep the condition identity on its own line.  Long sentinel tags
        # such as ``full_topo__perdoc`` and ``full_shuffled`` otherwise push
        # a left-aligned title outside the tight bounding box.
        ax.set_title(f"{short} — {kind}\n"
                     "descriptive curves; sample size = windows/docs",
                     loc="left", fontsize=9)
        ax.grid(True, alpha=.5)
        ax.legend(fontsize=6.5)
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, f"curves_{short}_{kind}.png"),
                    bbox_inches="tight")
        plt.close(fig)


def fig_gains(cells):
    rows = []
    for name, c in cells.items():
        short, corpus, kind = name.split("__", 2)
        r = c["main"]
        if kind != "full_topo" or not r["quantitative"] or r["unmatched"]:
            continue
        g = r["descriptive"].get("context_gain_bpb")
        ci = (r.get("boot_windows") or {}).get("gain_ci95")
        if g is not None:
            rows.append((short, corpus, g, ci))
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(6.2, .5 + .32 * len(rows)))
    labels = []
    for i, (short, corpus, g, ci) in enumerate(sorted(rows)):
        ax.plot([g], [i], "o", ms=5, color=COLOR.get(corpus, INK2))
        if ci:
            ax.plot(ci, [i, i], "-", lw=1.5, color=COLOR.get(corpus, INK2))
        labels.append(f"{short} · {corpus}")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.axvline(0, color=INK2, lw=.8)
    ax.set_xlabel("context gain, bits/byte (decade [16,256) − PER-CELL top "
                  "valid bin; cross-corpus common-support version lives in "
                  "comparisons.json)")
    ax.set_title("Context gain by corpus (full_topo, quantitative cells "
                 "only)", loc="left", fontsize=9)
    ax.grid(True, axis="x", alpha=.5)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "context_gains.png"), bbox_inches="tight")
    plt.close(fig)


def fig_masked(cells):
    """Preregistered contamination view: clean-target masked vs full."""
    rows = []
    for name, c in cells.items():
        short, corpus, kind = name.split("__", 2)
        if kind != "full_topo":
            continue
        full = c["main"]["overall_bpb"]
        for tag in ("c2024_11", "c2025_04", "c2026_02"):
            m = c.get(f"masked_{tag}")
            if m and m.get("n_groups", 0) and m.get("overall_bpb"):
                rows.append((short, corpus, tag, m["overall_bpb"] - full,
                             m["quantitative"]))
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(6.2, .5 + .3 * len(rows)))
    labels = []
    for i, (short, corpus, tag, d, quant) in enumerate(sorted(rows)):
        ax.plot([d], [i], "o" if quant else "x", ms=5,
                color=COLOR.get(corpus, INK2), alpha=1 if quant else .5)
        labels.append(f"{short} · {corpus} · {tag}"
                      + ("" if quant else " (desc)"))
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.axvline(0, color=INK2, lw=.8)
    ax.set_xlabel("BPB(post-cutoff targets, natural context) − BPB(full)")
    ax.set_title("Clean-target masking deltas (positive ⇒ unseen targets "
                 "harder)", loc="left", fontsize=9)
    ax.grid(True, axis="x", alpha=.5)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "masked_deltas.png"), bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    cells = load()
    fig_curves(cells)
    fig_gains(cells)
    fig_masked(cells)
    print(f"[done] descriptive plots -> {OUT} ({len(cells)} cells; "
          "fits live in fits.csv, plotted nowhere)")
