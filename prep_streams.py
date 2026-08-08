#!/usr/bin/env python3
"""Prepare measurement streams for Phase 1 (pretrained, in-context axis).

Per corpus:
  - collect source files, topologically order by intra-repo imports
    (dependencies first; LaTeX corpora are ordered chronologically),
  - recover each file's first-add date from git history (rename-aware,
    one pass over `git log -M --diff-filter=AR --name-status`); for arXiv
    corpora the submission date plays that role,
  - emit byte-budget-matched streams (ONE corpus-independent selection
    policy: seeded per-file priorities + greedy whole-document fill,
    PREREG §2; the corpus-size-dependent stride sampler is gone):
      full_topo       selected files, topo order     (headline, contaminated)
      full_shuffled   SAME selected set, shuffled    (order ablation)
      full_topo_s2    same rule, second seed         (sampling sensitivity)
      full_topo_xl    nested superset                (window-count suppl.)
      clean_cYYYY_MM  only files first added AFTER the cutoff (per model
                      family release date) -> secondary contamination arm
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
XL_CAP = 12_000_000      # supplementary unmatched streams: window count for
                         # curve stability (2.4MB @ 32k-tok windows is only
                         # ~20 independent context episodes)
MIN_MATCHED = 150_000    # below this a corpus can't join a matched comparison
SHUFFLE_SEED = 20260807

CUTOFFS = {              # model-family release dates (conservative cutoffs)
    "c2024_11": "2024-11-12",   # Qwen2.5-Coder family (+ StarCoder2, extra-safe)
    "c2025_04": "2025-04-29",   # Qwen3 family
    "c2026_02": "2026-03-01",   # Qwen3.5 family: uploads span Feb 27-28;
                                # boundary set strictly AFTER the family max
}

CORE_CORPORA = {"physlib", "mathlib", "qutip", "sympy", "geant4"}
OPTIONAL_CORPORA = {"arxiv_old", "arxiv_new"}  # preserved artifact +
# optional format diagnostic: self-budgeted, NEVER in core target math

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
                # normalize HERE (review fix): emission appends a final
                # newline to files lacking one, so the canonical `bytes`
                # must be the EMITTED size or budgets/caps drift
                if not text.endswith("\n"):
                    text += "\n"
                files.append(dict(rel=os.path.relpath(p, repo), text=text,
                                  bytes=len(text.encode("utf-8"))))
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
    if p.returncode != 0:  # empty output must never masquerade as "no dates"
        raise RuntimeError(f"git log failed in {repo_dir}: {p.stderr[:200]}")
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


VENDOR_RE = re.compile(r"\b(vendor|port(ed|ing)?\b|import(ed)? from|copied"
                       r"|migrat(e|ed|ion)|upstream)\b", re.I)


def follow_first_add(repo, rel):
    """Earliest add date AND a provenance flag when the adding commit's
    subject suggests pre-existing content (vendor/port/copy — PREREG §5:
    git dates bound only in-repo publication)."""
    p = subprocess.run(["git", "-C", repo, "log", "--follow",
                        "--diff-filter=A", "--format=%aI%x02%cI%x02%s",
                        "--", rel],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git log --follow failed for {rel}: "
                           f"{p.stderr[:200]}")
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    if not lines:
        return None, False
    a, c, subj = (lines[-1].split("\x02") + ["", ""])[:3]
    return min(a, c), bool(VENDOR_RE.search(subj))


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
    moved = flagged = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f, (fd, vflag) in zip(cand, ex.map(
                lambda f: follow_first_add(repo, f["rel"]), cand)):
            if fd and fd < f["date"]:
                f["date"] = fd
                moved += 1
            if vflag:
                f["provenance_flag"] = True  # vendor/port/copy suspicion
                flagged += 1
    print(f"[refine] {name}: {len(cand)} candidate-clean files re-dated via "
          f"--follow; {moved} moved earlier; {flagged} vendor/port-flagged",
          file=sys.stderr)


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


SELECT_SEED = 20260808


def doc_priority(rel, seed):
    """Deterministic per-file priority from content-independent identity:
    stable across corpora sizes, reruns, and file-set growth."""
    import hashlib
    return hashlib.sha256(f"{seed}:{rel}".encode()).hexdigest()


def select_docs(files, order, cap, seed=SELECT_SEED, base=None):
    """ONE corpus-independent sampling policy (review fix: every-kth
    stride made the policy a function of corpus size — mathlib sampled
    ~1/40 while QuTiP kept nearly everything): seeded whole-document
    priorities, greedy fill to the nominal cap (never padding), then
    topo-order the selected set. `base` (a previously selected set)
    guarantees nesting for XL: base docs first, then greedy-extend."""
    pos = {i: k for k, i in enumerate(order)}
    chosen = list(base or [])
    s = sum(files[i]["bytes"] for i in chosen)
    taken = set(chosen)
    ranked = sorted((i for i in range(len(files)) if i not in taken),
                    key=lambda i: doc_priority(files[i]["rel"], seed))
    for i in ranked:
        if i in pos and s + files[i]["bytes"] <= cap:
            chosen.append(i)
            s += files[i]["bytes"]
    return sorted((i for i in chosen if i in pos), key=lambda i: pos[i])


def doc_hashes(files, idxs):
    import hashlib
    return sorted(hashlib.sha256(files[i]["text"].encode()).hexdigest()
                  for i in idxs)


def emit_stream(name, kind, files, idxs):
    """Emits idxs verbatim — ALL selection happens in select_docs (one
    uniform policy; the corpus-size-dependent stride sampler is gone).
    Returns stats incl. an order-independent document-set hash."""
    import hashlib
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    txt_path = os.path.join(d, f"{kind}.txt")
    man_path = os.path.join(d, f"{kind}.manifest.jsonl")
    pos = 0
    with open(txt_path, "w", encoding="utf-8") as ftxt, \
         open(man_path, "w", encoding="utf-8") as fman:
        for doc_id, i in enumerate(idxs):
            t = files[i]["text"]  # already newline-normalized at collect
            nb = files[i]["bytes"]
            assert nb == len(t.encode("utf-8"))  # one bytes definition
            ftxt.write(t)
            fman.write(json.dumps(dict(
                doc_id=doc_id, rel=files[i]["rel"], start=pos, end=pos + nb,
                date=files[i]["date"],
                provenance_flag=files[i].get("provenance_flag", False)))
                + "\n")
            pos += nb
    dset = hashlib.sha256("".join(doc_hashes(files, idxs)).encode()).hexdigest()
    return dict(files=len(idxs), bytes=pos, doc_set_sha256=dset)


def build(name, cfg, files, targets):
    order, n_cyc = topo_order(files, cfg)
    stats = dict(corpus=name, lang=cfg["lang"], n_files=len(files),
                 total_bytes=sum(f["bytes"] for f in files), cycles=n_cyc,
                 streams={})
    if name in OPTIONAL_CORPORA:
        # optional diagnostic corpus: ONE self-budgeted stream, always
        # unmatched — no clean/shuffled/s2/XL, no target_delta, and by
        # construction no influence on any code budget
        cap = min(CAP, sum(f["bytes"] for f in files))
        canon = select_docs(files, order, cap)
        st = emit_stream(name, "full_topo", files, canon)
        st["matched"] = False
        st["optional"] = True
        st["selection"] = dict(method="seeded-priority-greedy",
                               seed=SELECT_SEED, self_budgeted=cap)
        stats["streams"]["full_topo"] = st
        return stats
    # core full streams (contaminated arm): canonical subset chosen ONCE
    # by the uniform seeded selection rule; the shuffle ablation permutes
    # EXACTLY these files
    canon = select_docs(files, order, targets["full"])
    st_topo = emit_stream(name, "full_topo", files, canon)
    st_topo["target_bytes"] = targets["full"]
    st_topo["target_delta"] = st_topo["bytes"] - targets["full"]
    st_topo["selection"] = dict(method="seeded-priority-greedy",
                                seed=SELECT_SEED)
    sh = list(canon)
    random.Random(SHUFFLE_SEED).shuffle(sh)
    st_shuf = emit_stream(name, "full_shuffled", files, sh)
    assert st_topo["doc_set_sha256"] == st_shuf["doc_set_sha256"] \
        and st_topo["bytes"] == st_shuf["bytes"], \
        f"{name}: shuffle ablation content diverged"
    stats["streams"]["full_topo"] = st_topo
    stats["streams"]["full_shuffled"] = st_shuf
    # sampling-sensitivity stream (sentinel item): SAME rule, second
    # seed — quantifies selection-policy sensitivity before expansion
    s2 = select_docs(files, order, targets["full"],
                     seed=SELECT_SEED + 1)
    st_s2 = emit_stream(name, "full_topo_s2", files, s2)
    st_s2["target_bytes"] = targets["full"]
    st_s2["target_delta"] = st_s2["bytes"] - targets["full"]
    st_s2["selection"] = dict(method="seeded-priority-greedy",
                              seed=SELECT_SEED + 1)
    stats["streams"]["full_topo_s2"] = st_s2
    # NESTED XL: same priority order greedy-extended past the
    # canonical set — stability statements extend the same content
    xl_idxs = select_docs(files, order, XL_CAP, base=canon)
    xl = emit_stream(name, "full_topo_xl", files, xl_idxs)
    xl["matched"] = False  # window-count supplement, never compared
    xl["nested_superset_of_canonical"] = True
    xl["selection"] = dict(method="seeded-priority-greedy",
                           seed=SELECT_SEED, base="full_topo")
    stats["streams"]["full_topo_xl"] = xl
    # clean streams (post-cutoff files only)
    for tag, cut in CUTOFFS.items():
        if targets[tag] <= 0:
            continue
        pool = [i for i in order if files[i]["date"] and files[i]["date"] > cut]
        avail = sum(files[i]["bytes"] for i in pool)
        sub_order = pool  # topo-ordered already (filtered from order)
        idxs = select_docs(files, sub_order, targets[tag])
        st = emit_stream(name, f"clean_{tag}", files, idxs)
        st["available_bytes"] = avail
        st["unmatched"] = avail < MIN_MATCHED
        if not st["unmatched"]:  # PREREG: matched within EVERY kind
            st["target_bytes"] = targets[tag]
            st["target_delta"] = st["bytes"] - targets[tag]
        stats["streams"][f"clean_{tag}"] = st
    return stats


def compute_targets(corpora_files):
    """Matched-budget targets over CORE code corpora ONLY (amendment):
    an optional corpus present in `corpora_files` can never change any
    code stream's budget — module-level so the invariance is testable."""
    targets = {"full": min(CAP, min(
        sum(f["bytes"] for f in fs) for n, fs in corpora_files.items()
        if n in CORE_CORPORA))}
    clean_avail = {}
    for tag, cut in CUTOFFS.items():
        per = {n: sum(f["bytes"] for f in fs
                      if f["date"] and f["date"] > cut)
               for n, fs in corpora_files.items() if n in CORE_CORPORA}
        ok = [v for v in per.values() if v >= MIN_MATCHED]
        targets[tag] = min(CAP, min(ok)) if ok else 0
        clean_avail[tag] = per
    return targets, clean_avail


def active_corpora():
    """All CORE corpora plus an OPTIONAL corpus only when its source
    material is present (tri-state, PREREG §2): a truly absent optional
    corpus is never loaded, dated, built, or recorded — presence uses the
    one shared recursive definition (arxiv_fetch.material_present), so a
    nested stray .tex counts as present and must validate downstream."""
    from arxiv_fetch import material_present
    act = {}
    for name, cfg in CORPORA.items():
        if name in OPTIONAL_CORPORA and not material_present(
                os.path.join(ROOT, cfg["repo"]), era=cfg["dirs"][0]):
            print(f"[optional] {name}: no source material on disk — "
                  "skipped (non-blocking)", file=sys.stderr)
            continue
        act[name] = cfg
    return act


if __name__ == "__main__":
    import shutil
    active = active_corpora()
    # the streams tree is a DERIVED artifact: rebuild it whole so no
    # stale stream (e.g. a demoted/absent optional corpus's earlier
    # emission) can outlive its corpus (review fix). Deterministic
    # emission keeps hashes stable, so valid cell artifacts stay valid.
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    corpora_files = {}
    for name, cfg in active.items():
        corpora_files[name] = load_corpus(name, cfg)
        tot = sum(f["bytes"] for f in corpora_files[name])
        print(f"{name}: {len(corpora_files[name])} files {tot/1e6:.1f}MB",
              file=sys.stderr)
    for name in sorted(CORE_CORPORA):  # fail-closed: core is mandatory
        assert corpora_files.get(name), f"core corpus {name} missing/empty"

    targets, clean_avail = compute_targets(corpora_files)
    for tag, per in clean_avail.items():
        print(f"clean {tag}: " + " ".join(f"{k}={v/1e3:.0f}KB"
                                          for k, v in per.items()),
              file=sys.stderr)
    print("targets:", targets, file=sys.stderr)

    # provenance: exact corpus states measured (PREREG §2). arxiv is not a
    # git repo — its universe is pinned by the manifest's SHA256 instead;
    # with the optional corpus ABSENT the hash is None and no arxiv
    # corpus appears anywhere in streams_stats (tri-state).
    shas = {}
    for name, cfg in active.items():
        if cfg.get("dated_by") == "manifest":
            continue
        p = subprocess.run(["git", "-C", os.path.join(ROOT, cfg["repo"]),
                            "rev-parse", "HEAD"], capture_output=True,
                           text=True)
        shas[cfg["repo"]] = p.stdout.strip() or None
    import hashlib
    arxiv_sha = None
    if any(n in OPTIONAL_CORPORA for n in active):
        # material present -> the dating manifest MUST exist (load_corpus
        # already opened it); record its identity
        man_p = os.path.join(ROOT, "arxiv", "manifest.json")
        arxiv_sha = hashlib.sha256(open(man_p, "rb").read()).hexdigest()
    all_stats = dict(targets=targets, clean_available=clean_avail,
                     corpus_shas=shas, arxiv_manifest_sha256=arxiv_sha,
                     corpora={})
    for name, cfg in active.items():
        all_stats["corpora"][name] = build(name, cfg, corpora_files[name],
                                           targets)
    with open(os.path.join(BASE, "data", "streams_stats.json"), "w") as f:
        json.dump(all_stats, f, indent=1)
    print(json.dumps({n: s["streams"] for n, s in all_stats["corpora"].items()},
                     indent=1))
