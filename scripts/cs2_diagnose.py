#!/usr/bin/env python3
"""Diagnostics for the CS-2 capacity adjudication and the 10m ladder.

Read-only. Prints, per language:
  * the capacity verdict's own record (10m incumbent vs best 30m probe,
    with the HP each used) — the guard fires at > 0.01 b/B in favour of
    30m, which sends that language to a complete separate 30m ladder;
  * the tuned incumbent HP per rung, so a top-rung HP that drifted far
    from its neighbours is visible;
  * the 10m val_bpb curve over rungs (seed-mean, T=4096), which must be
    monotone non-increasing in data if the runs are healthy. A top rung
    that is WORSE than the rung below it means the 10m arm is
    mis-tuned or undertrained there — and would make the capacity guard
    fire for a reason that is not model capacity.
"""
import json
import os
import re
import sys
from collections import defaultdict

RUNS = "results_cs/runs"
LANGS = ["lean", "python", "cpp", "latex"]


def load(path):
    try:
        return json.load(open(path))
    except Exception as e:  # noqa: BLE001
        print(f"  ! cannot read {path}: {e}", file=sys.stderr)
        return None


ver = load("results_cs/capacity_verdict.json") or {}
inc = load("results_cs/hp_incumbents.json") or {}

print("=== capacity verdict (fires if 30m beats 10m by > 0.01 b/B) ===")
langs = ver.get("languages", ver)
for lang in LANGS:
    d = langs.get(lang)
    if not isinstance(d, dict):
        continue
    keep = {k: v for k, v in d.items()
            if not isinstance(v, (dict, list)) and k != "schema"}
    print(f"{lang:8s} {keep}")

print("\n=== tuned incumbent HP per rung (10m) ===")
for lang in LANGS:
    rungs = inc.get(lang, {})
    if not isinstance(rungs, dict):
        continue
    parts = []
    for frac in sorted(rungs, key=float):
        e = rungs[frac]
        hp = e.get("incumbent", e) if isinstance(e, dict) else e
        lr = hp.get("lr") if isinstance(hp, dict) else None
        ep = hp.get("epochs") if isinstance(hp, dict) else None
        parts.append(f"{float(frac):.4f}:lr={lr},ep={ep}")
    print(f"{lang:8s} " + "  ".join(parts))

print("\n=== 10m val_bpb by rung (T=4096, seed-mean) ===")
by = defaultdict(lambda: defaultdict(list))
for fn in os.listdir(RUNS):
    m = re.match(r"scratch-10m-(\w+)-s(\d)-r([0-9.]+)-c(\d+)\.json$", fn)
    if not m:
        continue
    lang, seed, frac, ctx = m.group(1), m.group(2), float(m.group(3)), m.group(4)
    if ctx != "4096":
        continue
    r = load(os.path.join(RUNS, fn))
    if r and isinstance(r.get("final_val_bpb"), (int, float)):
        by[lang][frac].append(r["final_val_bpb"])

for lang in LANGS:
    fracs = sorted(by[lang])
    if not fracs:
        continue
    cells, prev, bad = [], None, []
    for f in fracs:
        vals = by[lang][f]
        mean = sum(vals) / len(vals)
        cells.append(f"{f:.4f}:{mean:.4f}(n={len(vals)})")
        if prev is not None and mean > prev + 1e-4:
            bad.append(f"{f:.4f}")
        prev = mean
    print(f"{lang:8s} " + "  ".join(cells))
    if bad:
        print(f"{'':8s} ^ NON-MONOTONE at rung(s) {', '.join(bad)}: "
              f"more data gave worse loss")
