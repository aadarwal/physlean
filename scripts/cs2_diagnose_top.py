#!/usr/bin/env python3
"""Why are the top rungs non-monotone for some languages?

For each language's top three rungs (T=4096), print per seed:
  lr, epochs, total steps, the FINAL val bpb, the BEST val bpb seen in
  the recorded history, and the step where that best occurred.

final >> best  => the run overfit or destabilised late; the tuned HP
(epochs in particular, which the walk can only double, never halve) is
wrong for that rung, and `final_val_bpb` is not a measure of what the
model size can do. That is a tuning failure, not a capacity failure.
final ~= best  => the run genuinely could not do better, and a capacity
reading is meaningful.
"""
import json
import os
import re
from collections import defaultdict

RUNS = "results_cs/runs"
LANGS = ["lean", "python", "cpp", "latex"]
TOP = 3

rows = defaultdict(list)
for fn in os.listdir(RUNS):
    m = re.match(r"scratch-10m-(\w+)-s(\d)-r([0-9.]+)-c4096\.json$", fn)
    if not m:
        continue
    lang, seed, frac = m.group(1), int(m.group(2)), float(m.group(3))
    try:
        r = json.load(open(os.path.join(RUNS, fn)))
    except Exception:  # noqa: BLE001
        continue
    hist = r.get("history") or []
    vals = [(h.get("step"), h.get("val_bpb")) for h in hist
            if isinstance(h, dict) and isinstance(h.get("val_bpb"), (int, float))]
    best_step, best = (None, None)
    if vals:
        best_step, best = min(((s, v) for s, v in vals), key=lambda t: t[1])
    rows[lang].append(dict(frac=frac, seed=seed, lr=r.get("lr"),
                           epochs=r.get("epochs"), steps=r.get("total_steps"),
                           final=r.get("final_val_bpb"),
                           best=best, best_step=best_step))

for lang in LANGS:
    rs = sorted(rows[lang], key=lambda d: (-d["frac"], d["seed"]))
    fracs = sorted({d["frac"] for d in rs}, reverse=True)[:TOP]
    print(f"\n=== {lang} (top {TOP} rungs) ===")
    for d in [d for d in rs if d["frac"] in fracs]:
        gap = (d["final"] - d["best"]) if (d["final"] is not None
                                           and d["best"] is not None) else None
        flag = ""
        if gap is not None and gap > 0.02:
            flag = f"  <-- LATE DEGRADATION (+{gap:.3f} after step {d['best_step']})"
        print(f"  r={d['frac']:.4f} s{d['seed']} lr={d['lr']} ep={d['epochs']} "
              f"steps={d['steps']} final={d['final']:.4f} "
              f"best={d['best']:.4f}{flag}" if d["final"] is not None
              else f"  r={d['frac']:.4f} s{d['seed']} INCOMPLETE")
