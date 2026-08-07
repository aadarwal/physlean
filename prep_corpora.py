#!/usr/bin/env python3
"""Prepare matched corpora for the physlean pilot.

2x2 design: {physics, math} x {Lean 4, Python}
  physlib  : physics in Lean 4  (Physlib/ + QuantumInfo/, excluding PhyslibAlpha)
  mathlib  : math in Lean 4     (Mathlib/)
  qutip    : physics in Python  (qutip/)
  sympy    : math in Python     (sympy/)

For each corpus: collect source files, topologically order them by intra-repo
imports (dependencies first), deterministically hold out ~10% of files
(every 10th in topo order), emit train.txt and heldout.txt capped to matched
byte budgets, plus stats.json.
"""
import json, os, re, sys
from collections import defaultdict

BASE = os.environ.get("PHYSLEAN_BASE", os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(BASE, "corpora")
OUT  = os.path.join(BASE, "data")

CORPORA = {
    "physlib": dict(repo="physlib",  dirs=["Physlib", "QuantumInfo"], ext=".lean", lang="lean"),
    "mathlib": dict(repo="mathlib4", dirs=["Mathlib"],                ext=".lean", lang="lean"),
    "qutip":   dict(repo="qutip",    dirs=["qutip"],                  ext=".py",   lang="python"),
    "sympy":   dict(repo="sympy",    dirs=["sympy"],                  ext=".py",   lang="python"),
}

LEAN_IMPORT = re.compile(r"^import\s+([A-Za-z0-9_.À-￿]+)", re.M)
PY_IMPORT   = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)

def collect_files(cfg):
    repo = os.path.join(ROOT, cfg["repo"])
    files = []
    for d in cfg["dirs"]:
        base = os.path.join(repo, d)
        for dirpath, _, names in os.walk(base):
            for n in sorted(names):
                if n.endswith(cfg["ext"]):
                    p = os.path.join(dirpath, n)
                    try:
                        with open(p, "rb") as f:
                            b = f.read()
                        text = b.decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    if len(b) < 64:
                        continue
                    rel = os.path.relpath(p, repo)
                    files.append((rel, text, len(b)))
    return files

def module_name(rel, cfg):
    stem = rel[: -len(cfg["ext"])]
    parts = stem.split(os.sep)
    if cfg["lang"] == "python" and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def imports_of(text, cfg):
    if cfg["lang"] == "lean":
        return set(LEAN_IMPORT.findall(text))
    out = set()
    for a, b in PY_IMPORT.findall(text):
        out.add(a or b)
    return out

def topo_order(files, cfg):
    import heapq
    mod2idx = {module_name(rel, cfg): i for i, (rel, _, _) in enumerate(files)}
    adj = defaultdict(set); indeg = [0] * len(files)
    for i, (rel, text, _) in enumerate(files):
        for imp in imports_of(text, cfg):
            cand = None
            parts = imp.split(".")
            for k in range(len(parts), 0, -1):
                m = ".".join(parts[:k])
                if m in mod2idx:
                    cand = mod2idx[m]; break
            if cand is not None and cand != i and i not in adj[cand]:
                adj[cand].add(i); indeg[i] += 1
    heap = [i for i in range(len(files)) if indeg[i] == 0]
    heapq.heapify(heap)
    order, seen = [], set()
    while heap:
        u = heapq.heappop(heap)
        order.append(u); seen.add(u)
        for v in sorted(adj[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)
    cyc = [i for i in range(len(files)) if i not in seen]  # python import cycles
    order.extend(cyc)
    return order, len(cyc)

def build(name, cfg, train_cap, eval_cap):
    files = collect_files(cfg)
    order, n_cyc = topo_order(files, cfg)
    train_idx = [i for k, i in enumerate(order) if k % 10 != 7]
    held_idx  = [i for k, i in enumerate(order) if k % 10 == 7]

    def emit(idxs, cap, path):
        total = sum(files[i][2] for i in idxs)
        keep = idxs
        if total > cap:  # deterministic take-every-kth thinning, topo order kept
            keep, s = [], 0
            step = max(1, int(round(total / cap)))
            for k, i in enumerate(idxs):
                if k % step == 0 and s < cap:
                    keep.append(i); s += files[i][2]
        out_bytes = 0
        with open(path, "w", encoding="utf-8") as f:
            for i in keep:
                t = files[i][1]
                if not t.endswith("\n"):
                    t += "\n"
                f.write(t)
                out_bytes += len(t.encode("utf-8"))
        return len(keep), out_bytes

    os.makedirs(os.path.join(OUT, name), exist_ok=True)
    ntr, btr = emit(train_idx, train_cap, os.path.join(OUT, name, "train.txt"))
    nhe, bhe = emit(held_idx, eval_cap, os.path.join(OUT, name, "heldout.txt"))
    stats = dict(corpus=name, lang=cfg["lang"], n_files=len(files),
                 total_bytes=sum(b for _, _, b in files), cycles=n_cyc,
                 train_files=ntr, train_bytes=btr, heldout_files=nhe, heldout_bytes=bhe)
    print(json.dumps(stats))
    return stats

if __name__ == "__main__":
    avail = {}
    for name, cfg in CORPORA.items():
        fs = collect_files(cfg)
        avail[name] = sum(b for _, _, b in fs)
        print(f"{name}: {len(fs)} files, {avail[name]/1e6:.1f} MB", file=sys.stderr)
    train_cap = int(min(min(avail.values()) * 0.85, 6_000_000))
    eval_cap  = 768_000
    print(f"caps: train={train_cap} eval={eval_cap}", file=sys.stderr)
    all_stats = [build(n, c, train_cap, eval_cap) for n, c in CORPORA.items()]
    with open(os.path.join(OUT, "stats.json"), "w") as f:
        json.dump(dict(train_cap=train_cap, eval_cap=eval_cap, corpora=all_stats), f, indent=2)
