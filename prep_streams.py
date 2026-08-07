#!/usr/bin/env python3
"""Prepare measurement streams for Phase 1 (pretrained, in-context axis).

Per corpus:
  - collect source files, topologically order by intra-repo imports
    (dependencies first; LaTeX corpora are ordered chronologically),
  - recover each file's first-add date from git history (rename-aware,
    one pass over `git log -M --diff-filter=AR --name-status`); for arXiv
    corpora the submission date plays that role,
  - emit byte-budget-matched streams:
      full_topo       all files, topo order          (headline, contaminated)
      full_shuffled   same file set, shuffled order  (ablation: does
                      dependency-ordered context matter?)
      clean_cYYYY_MM  only files first added AFTER the cutoff (per model
                      family release date) -> contamination-controlled
  - manifest JSONL per stream with byte spans + dates per doc, so the eval
    can bootstrap by document and reset context at doc boundaries.

Budgets are matched across corpora *within a stream kind* (min available,
capped). Corpora under MIN_MATCHED bytes for a kind are still emitted but
flagged unmatched=True and excluded from matched comparisons.
"""
import json, os, random, re, subprocess, sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "corpora")
OUT = os.path.join(BASE, "data", "streams")

CAP = 2_400_000          # bytes per stream (QuTiP-bound, matches pilot scale)
MIN_MATCHED = 150_000    # below this a corpus can't join a matched comparison
SHUFFLE_SEED = 20260807

CUTOFFS = {              # model-family release dates (conservative cutoffs)
    "c2024_11": "2024-11-12",   # Qwen2.5-Coder family (+ StarCoder2, extra-safe)
    "c2025_04": "2025-04-29",   # Qwen3 family
    "c2026_02": "2026-02-27",   # Qwen3.5 family
}

CORPORA = {
    "physlib":   dict(repo="physlib",  dirs=["Physlib", "QuantumInfo"], exts=[".lean"], lang="lean",
                      exclude=["PhyslibAlpha"]),
    "mathlib":   dict(repo="mathlib4", dirs=["Mathlib"], exts=[".lean"], lang="lean"),
    "qutip":     dict(repo="qutip",    dirs=["qutip"],   exts=[".py"],   lang="python"),
    "sympy":     dict(repo="sympy",    dirs=["sympy"],   exts=[".py"],   lang="python"),
    "geant4":    dict(repo="geant4",   dirs=["source"],  exts=[".cc", ".hh", ".icc"], lang="cpp"),
    "arxiv_old": dict(repo="arxiv",    dirs=["old"],     exts=[".tex"],  lang="latex", dated_by="manifest"),
    "arxiv_new": dict(repo="arxiv",    dirs=["new"],     exts=[".tex"],  lang="latex", dated_by="manifest"),
}

LEAN_IMPORT = re.compile(r"^import\s+([A-Za-z0-9_.À-￿]+)", re.M)
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
CPP_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def collect_files(cfg):
    repo = os.path.join(ROOT, cfg["repo"])
    excl = cfg.get("exclude", [])
    files = []
    for d in cfg["dirs"]:
        top = os.path.join(repo, d)
        for dirpath, dirnames, names in os.walk(top):
            dirnames[:] = [x for x in dirnames if x not in excl]
            for n in sorted(names):
                if not any(n.endswith(e) for e in cfg["exts"]):
                    continue
                p = os.path.join(dirpath, n)
                try:
                    with open(p, "rb") as f:
                        b = f.read()
                    text = b.decode("utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if len(b) < 64:
                    continue
                files.append(dict(rel=os.path.relpath(p, repo), text=text,
                                  bytes=len(b)))
    files.sort(key=lambda r: r["rel"])
    return files


def git_add_dates(repo_dir):
    """current-path -> earliest (author, committer) date, rename-aware.

    One chronological pass over adds (A) and renames (R): an A sets the date
    if the path is untracked; an R carries the original date to the new path.
    Re-adds after deletion keep the earliest date (conservative: a path is
    only 'clean' if it could not have been public before the cutoff).
    """
    cmd = ["git", "-C", repo_dir, "log", "-M", "--diff-filter=AR",
           "--name-status", "--reverse", "--date-order",
           "--format=\x01%aI\x02%cI"]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    dates = {}
    a = c = None
    for line in p.stdout.splitlines():
        if line.startswith("\x01"):
            a, c = line[1:].split("\x02")
            continue
        if not line or "\t" not in line:
            continue
        parts = line.split("\t")
        st = parts[0]
        if st == "A" and len(parts) == 2:
            dates.setdefault(parts[1], (a, c))
        elif st.startswith("R") and len(parts) == 3:
            old, new = parts[1], parts[2]
            if old in dates:
                dates[new] = dates.pop(old)
            else:
                dates.setdefault(new, (a, c))
    return dates


def load_corpus(name, cfg):
    """Files + first-public dates, computed once."""
    files = collect_files(cfg)
    if cfg.get("dated_by") == "manifest":
        man = json.load(open(os.path.join(ROOT, "arxiv", "manifest.json")))
        sub = {k + ".tex": v["submitted"] for k, v in man.items()
               if not v.get("skipped")}
        for f in files:
            f["date"] = sub.get(os.path.basename(f["rel"]))
    else:
        repo = os.path.join(ROOT, cfg["repo"])
        dates = git_add_dates(repo)
        miss = 0
        for f in files:
            d = dates.get(f["rel"])
            f["date"] = min(d) if d else None  # min(author, committer): safest
            miss += d is None
        if miss:
            print(f"[warn] {name}: {miss}/{len(files)} files without add-date",
                  file=sys.stderr)
        refine_candidate_clean_dates(name, repo, files)
    return files


EARLIEST_CUTOFF = min(CUTOFFS.values())


def follow_first_add(repo, rel):
    p = subprocess.run(["git", "-C", repo, "log", "--follow",
                        "--diff-filter=A", "--format=%aI%x02%cI", "--", rel],
                       capture_output=True, text=True, errors="replace")
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    a, c = lines[-1].split("\x02")  # last line = earliest add
    return min(a, c)


def refine_candidate_clean_dates(name, repo, files):
    """The one-pass date can be LATER than truth when a rename+edit exceeds
    -M's threshold in the commit-wide diff (observed on physlib: 1/10 sample,
    5 weeks late). A too-late date would leak old content into clean splits,
    so every candidate-clean file (date > earliest cutoff) is re-dated with
    per-file `git log --follow` and takes the earlier answer."""
    cand = [f for f in files if f["date"] and f["date"] > EARLIEST_CUTOFF]
    if not cand:
        return
    from concurrent.futures import ThreadPoolExecutor
    moved = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f, fd in zip(cand, ex.map(
                lambda f: follow_first_add(repo, f["rel"]), cand)):
            if fd and fd < f["date"]:
                f["date"] = fd
                moved += 1
    print(f"[refine] {name}: {len(cand)} candidate-clean files re-dated via "
          f"--follow; {moved} moved earlier", file=sys.stderr)


def module_name(rel, cfg):
    ext = next(e for e in cfg["exts"] if rel.endswith(e))
    stem = rel[: -len(ext)]
    parts = stem.split(os.sep)
    if cfg["lang"] == "python" and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(text, cfg):
    if cfg["lang"] == "lean":
        return set(LEAN_IMPORT.findall(text))
    if cfg["lang"] == "python":
        return {a or b for a, b in PY_IMPORT.findall(text)}
    if cfg["lang"] == "cpp":
        return set(CPP_INCLUDE.findall(text))
    return set()


def topo_order(files, cfg):
    import heapq
    if cfg["lang"] == "latex":  # chronological (submission date)
        return sorted(range(len(files)),
                      key=lambda i: files[i]["date"] or ""), 0
    if cfg["lang"] == "cpp":    # includes resolve by basename
        key2idx = {}
        for i, f in enumerate(files):
            key2idx.setdefault(os.path.basename(f["rel"]), i)
    else:
        key2idx = {module_name(f["rel"], cfg): i for i, f in enumerate(files)}
    adj = defaultdict(set)
    indeg = [0] * len(files)
    for i, f in enumerate(files):
        for imp in imports_of(f["text"], cfg):
            cand = None
            if cfg["lang"] == "cpp":
                cand = key2idx.get(os.path.basename(imp))
            else:
                parts = imp.split(".")
                for k in range(len(parts), 0, -1):
                    m = ".".join(parts[:k])
                    if m in key2idx:
                        cand = key2idx[m]
                        break
            if cand is not None and cand != i and i not in adj[cand]:
                adj[cand].add(i)
                indeg[i] += 1
    heap = [i for i in range(len(files)) if indeg[i] == 0]
    heapq.heapify(heap)
    order, seen = [], set()
    while heap:
        u = heapq.heappop(heap)
        order.append(u)
        seen.add(u)
        for v in sorted(adj[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)
    cyc = [i for i in range(len(files)) if i not in seen]
    order.extend(cyc)
    return order, len(cyc)


def thin_to_cap(idxs, files, cap):
    total = sum(files[i]["bytes"] for i in idxs)
    if total <= cap:
        return idxs
    keep, s = [], 0
    step = max(1, int(round(total / cap)))
    for k, i in enumerate(idxs):
        if k % step == 0 and s < cap:
            keep.append(i)
            s += files[i]["bytes"]
    return keep


def emit_stream(name, kind, files, idxs, cap):
    idxs = thin_to_cap(idxs, files, cap)
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    txt_path = os.path.join(d, f"{kind}.txt")
    man_path = os.path.join(d, f"{kind}.manifest.jsonl")
    pos = 0
    with open(txt_path, "w", encoding="utf-8") as ftxt, \
         open(man_path, "w", encoding="utf-8") as fman:
        for doc_id, i in enumerate(idxs):
            t = files[i]["text"]
            if not t.endswith("\n"):
                t += "\n"
            nb = len(t.encode("utf-8"))
            ftxt.write(t)
            fman.write(json.dumps(dict(
                doc_id=doc_id, rel=files[i]["rel"], start=pos, end=pos + nb,
                date=files[i]["date"])) + "\n")
            pos += nb
    return dict(files=len(idxs), bytes=pos)


def build(name, cfg, files, targets):
    order, n_cyc = topo_order(files, cfg)
    stats = dict(corpus=name, lang=cfg["lang"], n_files=len(files),
                 total_bytes=sum(f["bytes"] for f in files), cycles=n_cyc,
                 streams={})
    if name != "arxiv_new":   # full streams (contaminated arm)
        stats["streams"]["full_topo"] = emit_stream(
            name, "full_topo", files, order, targets["full"])
        sh = list(order)
        random.Random(SHUFFLE_SEED).shuffle(sh)
        stats["streams"]["full_shuffled"] = emit_stream(
            name, "full_shuffled", files, sh, targets["full"])
    if name != "arxiv_old":   # clean streams (post-cutoff files only)
        for tag, cut in CUTOFFS.items():
            if targets[tag] <= 0:
                continue
            idxs = [i for i in order if files[i]["date"] and files[i]["date"] > cut]
            avail = sum(files[i]["bytes"] for i in idxs)
            st = emit_stream(name, f"clean_{tag}", files, idxs, targets[tag])
            st["available_bytes"] = avail
            st["unmatched"] = avail < MIN_MATCHED
            stats["streams"][f"clean_{tag}"] = st
    return stats


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    corpora_files = {}
    for name, cfg in CORPORA.items():
        corpora_files[name] = load_corpus(name, cfg)
        tot = sum(f["bytes"] for f in corpora_files[name])
        print(f"{name}: {len(corpora_files[name])} files {tot/1e6:.1f}MB",
              file=sys.stderr)

    targets = {"full": min(CAP, min(
        sum(f["bytes"] for f in fs) for n, fs in corpora_files.items()
        if n != "arxiv_new"))}
    clean_avail = {}
    for tag, cut in CUTOFFS.items():
        per = {n: sum(f["bytes"] for f in fs if f["date"] and f["date"] > cut)
               for n, fs in corpora_files.items() if n != "arxiv_old"}
        ok = [v for v in per.values() if v >= MIN_MATCHED]
        targets[tag] = min(CAP, min(ok)) if ok else 0
        clean_avail[tag] = per
        print(f"clean {tag}: " + " ".join(f"{k}={v/1e3:.0f}KB"
                                          for k, v in per.items()),
              file=sys.stderr)
    print("targets:", targets, file=sys.stderr)

    all_stats = dict(targets=targets, clean_available=clean_avail, corpora={})
    for name, cfg in CORPORA.items():
        all_stats["corpora"][name] = build(name, cfg, corpora_files[name],
                                           targets)
    with open(os.path.join(BASE, "data", "streams_stats.json"), "w") as f:
        json.dump(all_stats, f, indent=1)
    print(json.dumps({n: s["streams"] for n, s in all_stats["corpora"].items()},
                     indent=1))
