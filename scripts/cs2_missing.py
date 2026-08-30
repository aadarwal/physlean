#!/usr/bin/env python3
"""Report (and optionally re-emit) CS-2 ladder tasks with no result file.

Reads a task file in slurm/cs2_rungs.sbatch's format
    lang max_bytes ctx seed lr epochs tag
and derives the result path train_scratch.py would write
    results_cs/runs/scratch-<size>-<lang>-s<seed><tag>.json
so "missing" means "no artifact on disk", independent of job history.

Writes two task files, because the partitions differ in time limit:
  big   (frac >= 0.5)  -> mit_preemptable only (48h; these exceed 6h)
  small (frac <  0.5)  -> either partition
"""
import argparse
import os
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--tasks", default="data/cs2/tasks_ladder.txt")
ap.add_argument("--runs-dir", default="results_cs/runs")
ap.add_argument("--size", default="10m")
ap.add_argument("--out-prefix", default="data/cs2/tasks_missing")
ap.add_argument("--big-frac", type=float, default=0.5)
ap.add_argument("--write", action="store_true", help="write the task files")
args = ap.parse_args()

lines = [l.rstrip("\n") for l in open(args.tasks) if l.strip()]
missing_big, missing_small = [], []
for i, line in enumerate(lines):
    parts = line.split()
    if len(parts) < 7:
        print(f"malformed line {i}: {line!r}", file=sys.stderr)
        sys.exit(2)
    lang, _mb, _ctx, seed, _lr, _ep, tag = parts[:7]
    run = f"scratch-{args.size}-{lang}-s{seed}{tag}"
    if os.path.exists(os.path.join(args.runs_dir, run + ".json")):
        continue
    m = re.search(r"-r([0-9.]+)-", tag)
    frac = float(m.group(1)) if m else 1.0
    (missing_big if frac >= args.big_frac else missing_small).append(line)

print(f"tasks={len(lines)} missing={len(missing_big) + len(missing_small)} "
      f"big={len(missing_big)} small={len(missing_small)}")
if args.write:
    for name, rows in (("big", missing_big), ("small", missing_small)):
        path = f"{args.out_prefix}_{name}.txt"
        with open(path, "w") as f:
            f.write("".join(r + "\n" for r in rows))
        print(f"wrote {len(rows)} -> {path}")
