#!/usr/bin/env python3
"""Deliverable figures + summary tables from results_v2/fits.json and
results_v2/scratch/*.json.  Outputs results_v2/plots/*.png + summary.md.

Design system (validated 2026-08-07, dataviz six-checks, light surface):
fixed corpus->hue map, language family as linestyle (secondary encoding),
one axis per panel, recessive grid, direct labels + legend.
"""
import glob, json, math, os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "results_v2")
OUT = os.path.join(RES, "plots")

COLOR = {"physlib": "#2a78d6", "mathlib": "#eb6834", "qutip": "#1baf7a",
         "sympy": "#eda100", "geant4": "#e87ba4",
         "arxiv_old": "#008300", "arxiv_new": "#008300"}
LSTYLE = {"physlib": "-", "mathlib": "-",            # lean: solid
          "qutip": (0, (4, 2)), "sympy": (0, (4, 2)),  # python: dashed
          "geant4": (0, (5, 2, 1, 2)),                 # cpp: dash-dot
          "arxiv_old": (0, (1, 2)), "arxiv_new": (0, (1, 2))}  # latex: dotted
LABEL = {"physlib": "Physlib (Lean, physics)", "mathlib": "mathlib4 (Lean, math)",
         "qutip": "QuTiP (Py, physics)", "sympy": "SymPy (Py, math)",
         "geant4": "Geant4 (C++, physics)", "arxiv_old": "arXiv (LaTeX prose)",
         "arxiv_new": "arXiv (LaTeX prose)"}
PARAMS = {"q25c-0.5b": 0.494e9, "q25c-1.5b": 1.54e9, "q25c-3b": 3.09e9,
          "q25c-7b": 7.62e9, "q25c-14b": 14.7e9, "q25c-32b": 32.5e9,
          "q3-0.6b": 0.60e9, "q3-1.7b": 1.72e9, "q3-4b": 4.02e9,
          "q3-8b": 8.19e9, "q3-14b": 14.8e9, "q3-32b": 32.8e9,
          "q35-0.8b": 0.8e9, "q35-2b": 2.0e9, "q35-4b": 4.0e9,
          "q35-9b": 9.0e9, "sc2-3b": 3.03e9, "dsc2-lite": 15.7e9}
FAMILY = [("q25c", "Qwen2.5-Coder"), ("q35", "Qwen3.5"), ("q3", "Qwen3"),
          ("sc2", "StarCoder2"), ("dsc2", "DeepSeek-V2")]
INK, INK2 = "#0b0b0b", "#52514e"

plt.rcParams.update({
    "figure.dpi": 200, "savefig.dpi": 200, "font.size": 9,
    "axes.titlesize": 10, "axes.labelsize": 9, "axes.edgecolor": INK2,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "grid.color": "#d9d8d3", "grid.linewidth": 0.6, "legend.frameon": False,
    "savefig.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
})


def family_of(short):
    for pre, name in FAMILY:
        if short.startswith(pre + "-"):
            return name
    return short


def load_fits():
    p = os.path.join(RES, "fits.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def cells_by(fits, kind_prefix):
    out = defaultdict(dict)  # short -> corpus -> cell
    for name, cell in fits.items():
        short, corpus, kind = name.split("__", 2)
        if kind.startswith(kind_prefix):
            out[short][corpus] = cell
    return out


def curve_panel(ax, corpus_cells, title):
    for corpus, cell in sorted(corpus_cells.items()):
        b = cell["mean"]["bins"]
        if not b["mid"]:
            continue
        ax.plot(b["mid"], b["bpb"], color=COLOR.get(corpus, INK2),
                ls=LSTYLE.get(corpus, "-"), lw=2, marker="o", ms=3.5,
                label=LABEL.get(corpus, corpus))
        ax.annotate(corpus, (b["mid"][-1], b["bpb"][-1]),
                    xytext=(4, 0), textcoords="offset points",
                    fontsize=7, color=INK2, va="center")
    ax.set_xscale("log")
    ax.set_xlabel("in-context bytes c")
    ax.set_ylabel("bits per byte")
    ax.set_title(title, loc="left")
    ax.grid(True, which="major", axis="both", alpha=0.5)


def fig_curves(fits):
    for split, tagf in [("full", "full_topo"), ("clean", "clean_")]:
        grid = cells_by(fits, tagf)
        shorts = [s for s in grid if s in PARAMS]
        if not shorts:
            continue
        shorts.sort(key=lambda s: PARAMS[s])
        n = len(shorts)
        ncol = min(4, max(1, n))
        nrow = math.ceil(n / ncol)
        fig, axes = plt.subplots(nrow, ncol,
                                 figsize=(3.6 * ncol, 3.0 * nrow),
                                 squeeze=False, sharey=True)
        for k, s in enumerate(shorts):
            ax = axes[k // ncol][k % ncol]
            curve_panel(ax, grid[s], f"{family_of(s)} {s.split('-', 1)[1]}")
        for k in range(n, nrow * ncol):
            axes[k // ncol][k % ncol].axis("off")
        handles, labels = axes[0][0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=3,
                   bbox_to_anchor=(0.5, -0.02))
        fig.suptitle(f"BPB vs in-context bytes — {split} split "
                     f"(teacher-forced, byte-normalized)", x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0.06, 1, 0.97))
        fig.savefig(os.path.join(OUT, f"curves_{split}.png"),
                    bbox_inches="tight")
        plt.close(fig)


def fig_scaling(fits):
    fig, axes = plt.subplots(2, 2, figsize=(8.6, 6.4), sharex=True)
    for col, (split, tagf) in enumerate([("full", "full_topo"),
                                         ("clean", "clean_")]):
        grid = cells_by(fits, tagf)
        series = defaultdict(list)  # corpus -> [(params, cell)]
        for s, per in grid.items():
            if s in PARAMS and s.startswith("q25c-"):  # primary ladder
                for corpus, cell in per.items():
                    series[corpus].append((PARAMS[s], cell))
        for row, key in enumerate(["beta", "Linf"]):
            ax = axes[row][col]
            for corpus, pts in sorted(series.items()):
                pts.sort()
                xs = [p for p, _ in pts]
                ys = [c["mean"][key] for _, c in pts]
                ci = [((c["mean"].get("ci_window") or {}).get(key)
                       or [np.nan, np.nan]) for _, c in pts]
                lo = [c[0] for c in ci]
                hi = [c[1] for c in ci]
                col_ = COLOR.get(corpus, INK2)
                ax.plot(xs, ys, color=col_, ls=LSTYLE.get(corpus, "-"),
                        lw=2, marker="o", ms=4, label=LABEL.get(corpus))
                if not any(np.isnan(lo)):
                    ax.fill_between(xs, lo, hi, color=col_, alpha=0.18, lw=0)
            ax.set_xscale("log")
            ax.grid(True, alpha=0.5)
            if row == 0:
                ax.set_title(f"{split} split", loc="left")
            ax.set_ylabel(r"$\beta$ (context exponent)" if key == "beta"
                          else r"$L_\infty$ (bits/byte)")
            if row == 1:
                ax.set_xlabel("model parameters (Qwen2.5-Coder ladder)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3,
               bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Predictability scaling by language vs model scale",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(os.path.join(OUT, "scaling_beta_linf.png"),
                bbox_inches="tight")
    plt.close(fig)


def fig_contamination(fits):
    full = cells_by(fits, "full_topo")
    clean = cells_by(fits, "clean_")
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    series = defaultdict(list)
    for s in full:
        if s not in PARAMS or not s.startswith("q25c-"):
            continue
        for corpus in full[s]:
            ckey = "arxiv_new" if corpus == "arxiv_old" else corpus
            if ckey in clean.get(s, {}):
                gap = (full[s][corpus]["overall_bpb"]
                       - clean[s][ckey]["overall_bpb"])
                series[corpus].append((PARAMS[s], gap))
    for corpus, pts in sorted(series.items()):
        pts.sort()
        ax.plot([p for p, _ in pts], [g for _, g in pts],
                color=COLOR.get(corpus, INK2), ls=LSTYLE.get(corpus, "-"),
                lw=2, marker="o", ms=4, label=LABEL.get(corpus, corpus))
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xscale("log")
    ax.set_xlabel("model parameters")
    ax.set_ylabel("BPB(full) − BPB(clean)  [bits/byte]")
    ax.set_title("Contamination gap vs scale (negative ⇒ clean split harder)",
                 loc="left")
    ax.grid(True, alpha=0.5)
    ax.legend(ncol=2, fontsize=7.5)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "contamination_gap.png"),
                bbox_inches="tight")
    plt.close(fig)


def fig_phase2():
    runs = [json.load(open(p)) for p in
            glob.glob(os.path.join(RES, "scratch", "*.json"))
            if "-smoke" not in p]
    if not runs:
        return
    P2COLOR = {"lean": "#2a78d6", "python": "#eda100", "cpp": "#e87ba4",
               "latex": "#008300"}
    P2LS = {"lean": "-", "python": (0, (4, 2)), "cpp": (0, (5, 2, 1, 2)),
            "latex": (0, (1, 2))}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.8))
    by = defaultdict(lambda: defaultdict(list))  # lang -> n_params -> [bpb]
    for r in runs:
        by[r["lang"]][r["n_params"]].append(r["final_val_bpb"])
    for lang, d in sorted(by.items()):
        xs = sorted(d)
        med = [float(np.median(d[x])) for x in xs]
        ax1.plot(xs, med, color=P2COLOR.get(lang, INK2),
                 ls=P2LS.get(lang, "-"), lw=2, marker="o", ms=4, label=lang)
        for x in xs:
            ax1.plot([x] * len(d[x]), d[x], "o", ms=2.5, alpha=0.45,
                     color=P2COLOR.get(lang, INK2))
    ax1.set_xscale("log")
    ax1.set_xlabel("parameters N")
    ax1.set_ylabel("held-out bits/byte")
    ax1.set_title("From-scratch L(N), matched data budgets", loc="left")
    ax1.grid(True, alpha=0.5)
    ax1.legend(fontsize=8)
    biggest = defaultdict(list)
    top_size = max((r["n_params"] for r in runs), default=0)
    for r in runs:
        if r["n_params"] == top_size and r["seed"] == 1:
            for h in r["history"]:
                biggest[r["lang"]].append((h["tokens"], h["val_bpb"]))
    for lang, pts in sorted(biggest.items()):
        pts.sort()
        ax2.plot([t for t, _ in pts], [v for _, v in pts],
                 color=P2COLOR.get(lang, INK2), ls=P2LS.get(lang, "-"),
                 lw=2, marker="o", ms=3, label=lang)
    ax2.set_xscale("log")
    ax2.set_xlabel("training bytes seen D")
    ax2.set_ylabel("held-out bits/byte")
    ax2.set_title(f"L(D) at largest size", loc="left")
    ax2.grid(True, alpha=0.5)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "phase2_scaling.png"), bbox_inches="tight")
    plt.close(fig)


def summary_md(fits):
    lines = ["# Phase 1 fits — BPB(c) = A·c^(−β) + L∞", "",
             "| model | corpus | split | BPB | β [95% CI] | L∞ [95% CI] | "
             "β(median) | β(alt-edges) | gain |",
             "|---|---|---|---|---|---|---|---|---|"]
    def _f(x, n=3):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) \
            else f"{x:.{n}f}"
    for name in sorted(fits, key=lambda n: (n.split("__")[1], n)):
        c = fits[name]
        short, corpus, kind = name.split("__", 2)
        m = c["mean"]
        ci = m.get("ci_window") or {}
        beta_ci = ci.get("beta", [float("nan")] * 2)
        linf_ci = ci.get("Linf", [float("nan")] * 2)
        lines.append(
            f"| {short} | {corpus} | {kind} | {c['overall_bpb']:.3f} | "
            f"{_f(m['beta'])} [{_f(beta_ci[0])},{_f(beta_ci[1])}] | "
            f"{_f(m['Linf'])} [{_f(linf_ci[0])},{_f(linf_ci[1])}] | "
            f"{_f(c['median']['beta'])} | {_f(m['alt_edges']['beta'])} | "
            f"{_f(c.get('context_gain_bpb'))} |")
    with open(os.path.join(RES, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    fits = load_fits()
    if fits:
        fig_curves(fits)
        fig_scaling(fits)
        fig_contamination(fits)
        summary_md(fits)
    fig_phase2()
    print(f"[done] plots -> {OUT}, tables -> {RES}/summary.md "
          f"({len(fits)} cells)")
