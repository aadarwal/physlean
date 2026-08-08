#!/usr/bin/env python3
"""Fail-closed preflight (PREREG §12). Gates:
  --gate g1   after acquisition repair: env, corpora, streams, locks, shas
  --gate g3a  sentinel science gate (battery-cached 0.5B; 53 frozen cells)
  --gate g3b  expansion gate (small/mid pinned; 183 frozen cells; requires
              sentinel AND paired-v2 pilot signoffs)  [alias: g3]
  --gate big  big shards: big-rung models cached at pinned revisions
Writes results_v2/preflight_<gate>.json (gate-specific evidence is
preserved, never overwritten by later gates). Exit 0 only on pass."""
import argparse, json, os, subprocess, sys

BASE = os.path.dirname(os.path.abspath(__file__))
OK = True
report = {"checks": {}}

SMALL_MID = ["Qwen/Qwen2.5-Coder-0.5B", "Qwen/Qwen2.5-Coder-1.5B",
             "Qwen/Qwen2.5-Coder-3B", "Qwen/Qwen2.5-Coder-7B",
             "Qwen/Qwen3-0.6B-Base", "Qwen/Qwen3-1.7B-Base",
             "Qwen/Qwen3-4B-Base", "Qwen/Qwen3.5-0.8B-Base",
             "Qwen/Qwen3.5-2B-Base", "Qwen/Qwen3.5-4B-Base",
             "bigcode/starcoder2-3b"]
# required staged repo set — SUBSET semantics (review + %12 reality: the
# cluster legitimately retains Phase-2 clones from the earlier session;
# extras are preserved, locked, and reported — never deleted)
EXPECTED_REPOS = {"physlib", "mathlib4", "qutip", "sympy", "geant4",
                  "batteries", "astropy"}
# streams are built from exactly these five; corpus_shas must match
STREAM_SOURCE_REPOS = {"physlib", "mathlib4", "qutip", "sympy", "geant4"}
# NOTE: Qwen3-32B-Base does not exist on HF (verified 401); the Qwen3 dense
# base ladder tops out at 14B.
BIG = ["Qwen/Qwen2.5-Coder-14B", "Qwen/Qwen2.5-Coder-32B",
       "Qwen/Qwen3-8B-Base", "Qwen/Qwen3-14B-Base",
       "Qwen/Qwen3.5-9B-Base", "deepseek-ai/DeepSeek-Coder-V2-Lite-Base"]


def check(name, ok, detail):
    global OK
    report["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: "
          f"{json.dumps(detail, default=str)[:400]}")
    OK = OK and ok


def models_cached(ids):
    from huggingface_hub import snapshot_download
    mj_path = os.path.join(BASE, "models.json")
    if not os.path.exists(mj_path):
        return False, "models.json missing"
    mj = json.load(open(mj_path))
    missing, unpinned = [], []
    for m in ids:
        rev = (mj.get(m) or {}).get("sha")
        if not rev:
            unpinned.append(m)
            continue
        try:
            snapshot_download(m, revision=rev, local_files_only=True)
        except Exception:
            missing.append(m)
    ok = not missing and not unpinned
    return ok, {"missing": missing, "unpinned": unpinned,
                "n_ok": len(ids) - len(missing) - len(unpinned)}


def gate_g1():
    try:
        import importlib
        mods = {m: importlib.import_module(m).__version__
                for m in ("torch", "transformers", "huggingface_hub",
                          "numpy", "scipy", "pandas")}
        check("env-imports", True, mods)
    except Exception as e:
        check("env-imports", False, repr(e))

    try:
        st = json.load(open(os.path.join(BASE, "data", "streams_stats.json")))
        bad = []
        for name, c in st["corpora"].items():
            if c["n_files"] == 0 or c["total_bytes"] == 0:
                bad.append(name)
            ft = c["streams"].get("full_topo")
            if name != "arxiv_new" and (not ft or ft["bytes"] < 500_000):
                bad.append(f"{name}:full_topo={ft and ft['bytes']}")
        check("streams-nonzero", not bad, bad or "all full_topo >= 500KB")
        check("budget-targets", st["targets"].get("full", 0) >= 1_000_000,
              st["targets"])
        # byte-matching tolerance for EVERY matched stream kind (review:
        # PREREG matches within each kind; clean tags are compared across
        # corpora too): each non-unmatched cell within 10% of its target,
        # cross-corpus spread within 10%, per kind
        tol = {}
        tol_ok = True
        for kind_key, tgt_key in ([("full_topo", "full"),
                                   ("full_topo_s2", "full")]
                                  + [(f"clean_{t}", t)
                                     for t in ("c2024_11", "c2025_04",
                                               "c2026_02")]):
            tgt = st["targets"].get(tgt_key, 0) or 1
            deltas = {}
            for n, c in st["corpora"].items():
                s = c.get("streams", {}).get(kind_key)
                if s and not s.get("unmatched") \
                        and s.get("target_delta") is not None:
                    deltas[n] = s["target_delta"]
            if len(deltas) < 2:
                tol[kind_key] = "fewer than 2 matched cells"
                continue
            worst = max(abs(v) for v in deltas.values()) / tgt
            spread = (max(deltas.values()) - min(deltas.values())) / tgt
            ok = worst <= 0.10 and spread <= 0.10
            tol_ok = tol_ok and ok
            tol[kind_key] = dict(ok=ok, worst_rel=round(worst, 4),
                                 spread_rel=round(spread, 4))
        check("byte-match-tolerance", tol_ok and "full_topo" in tol
              and isinstance(tol["full_topo"], dict), tol)
        clean_cells = {t: sum(1 for n, c in st["corpora"].items()
                              if not c["streams"].get(f"clean_{t}", {})
                              .get("unmatched", True))
                       for t in ("c2024_11", "c2025_04", "c2026_02")}
        check("clean-matched-cells", all(v >= 3 for v in clean_cells.values()),
              clean_cells)
        gitless = [k for k, v in st.get("corpus_shas", {}).items() if not v]
        shas_keys = set(st.get("corpus_shas") or {})
        check("corpus-shas", shas_keys == STREAM_SOURCE_REPOS
              and not gitless and st.get("arxiv_manifest_sha256"),
              dict(keys=sorted(shas_keys),
                   expected=sorted(STREAM_SOURCE_REPOS), gitless=gitless,
                   arxiv_sha=(st.get("arxiv_manifest_sha256") or "")[:12]))
    except Exception as e:
        check("streams-nonzero", False, repr(e))

    # stream/manifest integrity: completeness, uniqueness, validity,
    # consistency, leakage (review: nonzero size is not integrity)
    try:
        from prep_streams import CUTOFFS as _cuts
        import glob as _glob
        problems = []
        topo_sets = {}
        for mp in sorted(_glob.glob(os.path.join(
                BASE, "data", "streams", "*", "*.manifest.jsonl"))):
            corpus = os.path.basename(os.path.dirname(mp))
            kind = os.path.basename(mp).replace(".manifest.jsonl", "")
            txt = mp.replace(".manifest.jsonl", ".txt")
            if not os.path.exists(txt):
                problems.append(f"{corpus}/{kind}: no txt")
                continue
            docs = [json.loads(l) for l in open(mp)]
            nb = os.path.getsize(txt)  # stream is pure UTF-8 bytes on disk
            pos = 0
            ids = set()
            rels = []
            for d in docs:
                if d["start"] != pos:
                    problems.append(f"{corpus}/{kind}: gap at {pos}")
                    break
                pos = d["end"]
                if d["doc_id"] in ids:
                    problems.append(f"{corpus}/{kind}: dup doc_id")
                    break
                ids.add(d["doc_id"])
                rels.append(d["rel"])
                if d.get("date") is not None and not str(
                        d["date"]).startswith("2"):
                    problems.append(f"{corpus}/{kind}: bad date {d['date']}")
                    break
            else:
                if pos != nb:
                    problems.append(f"{corpus}/{kind}: end {pos} != {nb}")
                if kind.startswith("clean_"):
                    cut = _cuts.get(kind.replace("clean_", ""), "9999")
                    leak = [d["rel"] for d in docs
                            if not (d.get("date") or "") > cut]
                    if leak:
                        problems.append(
                            f"{corpus}/{kind}: {len(leak)} docs at/before "
                            f"cutoff, e.g. {leak[:2]}")
                if kind in ("full_topo", "full_shuffled"):
                    topo_sets.setdefault(corpus, {})[kind] = frozenset(rels)
        for corpus, ks in topo_sets.items():
            if len(ks) == 2 and ks["full_topo"] != ks["full_shuffled"]:
                problems.append(f"{corpus}: topo/shuffle doc sets differ")
        check("stream-integrity", not problems, problems[:8] or "all pass")
    except Exception as e:
        check("stream-integrity", False, repr(e))

    # Phase-2 pools: NOT checked at G1/G3 — pool prep is deferred to the
    # G6 gate together with its own redesign (staging consistency review)

    try:
        import shutil
        free = shutil.disk_usage("/orcd/pool/008").free / 1e9
        check("disk-headroom", free > 100, f"{free:.0f}GB free on POOL")
    except Exception as e:
        check("disk-headroom", False, repr(e))

    fz = os.path.join(BASE, "results_v2", "env", "freeze-cluster.txt")
    try:
        import importlib
        CORE = {"torch": "torch", "transformers": "transformers",
                "huggingface_hub": "huggingface-hub", "numpy": "numpy",
                "scipy": "scipy", "pandas": "pandas"}
        frozen = {}
        if os.path.exists(fz):
            for line in open(fz):
                if "==" in line:
                    n, v = line.strip().split("==", 1)
                    frozen[n.lower()] = v
        drift = {}
        for mod, pkg in CORE.items():
            cur = importlib.import_module(mod).__version__
            fro = frozen.get(pkg)
            if fro != cur:  # exact core identity: unpinned installs can
                drift[pkg] = dict(frozen=fro, current=cur)  # drift (review)
        pinned_ok = importlib.import_module(
            "transformers").__version__ == "5.14.1"
        check("env-frozen", os.path.exists(fz) and pinned_ok and not drift,
              dict(freeze_file=os.path.exists(fz), pin_ok=pinned_ok,
                   core_drift=drift or "all six core versions match"))
    except Exception as e:
        check("env-frozen", False, repr(e))

    try:
        cj = json.load(open(os.path.join(BASE, "corpora", "arxiv",
                                         "checksums.json")))
        pin = json.load(open(os.path.join(BASE, "arxiv_manifest.json")))
        expected = {f"{m['era']}/{k}" for k, m in pin.items()
                    if not m.get("skipped")}
        got = set(cj["files"])
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        failed_pin = [k for k, v in cj["files"].items()
                      if not v.get("matches_pin")]
        # exact per-(era,safe)-key hash equality where sha256 is pinned
        hash_bad = [k for k, m in pin.items()
                    if not m.get("skipped") and m.get("sha256")
                    and (cj["files"].get(f"{m['era']}/{k}") or {})
                    .get("sha256") != m["sha256"]]
        weak_pins = [k for k, m in pin.items()
                     if not m.get("skipped") and not m.get("sha256")]
        # independent DISK scan: prep ingests directories, so un-pinned
        # on-disk .tex files are fatal even if checksums.json missed them
        from arxiv_fetch import scan_disk  # one era-qualified scanner
        on_disk = scan_disk(os.path.join(BASE, "corpora", "arxiv"))
        disk_extra = sorted(on_disk - expected)
        rec_extra = cj.get("extra_on_disk") or []
        check("arxiv-validated",
              not missing and not extra and not failed_pin and not hash_bad
              and not disk_extra and not rec_extra
              and len(got) == len(expected),
              dict(expected=len(expected), got=len(got),
                   missing=missing[:5], extra=extra[:5],
                   disk_extra=disk_extra[:5],
                   failed_pin=failed_pin[:5], hash_mismatch=hash_bad[:5],
                   byte_only_pins=len(weak_pins)))
    except Exception as e:
        check("arxiv-validated", False, repr(e))

    # corpus worktrees: clean, full-history, and (if locked) at the locked
    # SHA with the locked remote (HEAD alone is insufficient provenance)
    try:
        lock_p = os.path.join(BASE, "corpora_lock.json")
        lock = json.load(open(lock_p)) if os.path.exists(lock_p) else None
        probs = []
        croot = os.path.join(BASE, "corpora")
        for name in sorted(os.listdir(croot)):
            d = os.path.join(croot, name)
            if not os.path.isdir(os.path.join(d, ".git")):
                continue
            def g(*a):
                p = subprocess.run(["git", "-C", d, *a],
                                   capture_output=True, text=True)
                if p.returncode != 0:  # a failed git command must FAIL
                    raise RuntimeError(   # the check, not pass it silently
                        f"{name}: git {a[0]} rc={p.returncode}")
                return p.stdout.strip()
            try:
                if g("status", "--porcelain"):
                    probs.append(f"{name}: dirty worktree")
                if g("rev-parse", "--is-shallow-repository") == "true":
                    probs.append(f"{name}: shallow clone (dates unusable)")
                if lock and name in lock.get("repos", {}):
                    ent = lock["repos"][name]
                    if g("rev-parse", "HEAD") != ent["sha"]:
                        probs.append(f"{name}: HEAD != locked sha")
                    if g("remote", "get-url", "origin") != ent["url"]:
                        probs.append(f"{name}: remote URL != locked")
            except RuntimeError as ge:
                probs.append(str(ge))
        present = {n for n in os.listdir(croot)
                   if os.path.isdir(os.path.join(croot, n, ".git"))}
        missing = sorted(EXPECTED_REPOS - present)
        if missing:  # SUBSET semantics: required must all be present;
            probs.append(f"required repos missing: {missing}")
        extras = sorted(present - EXPECTED_REPOS)  # reported, preserved
        check("corpus-worktrees", not probs,
              (probs[:8] if probs else
               dict(status="required subset present, clean+full-history"
                    + (", lock-verified" if lock else ", no lock yet"),
                    extras_preserved=extras)))
    except Exception as e:
        check("corpus-worktrees", False, repr(e))

    # streams must be built from the CURRENT locked inputs: stale streams
    # from a prior checkout must not pass (review fix)
    try:
        import hashlib
        st = json.load(open(os.path.join(BASE, "data",
                                         "streams_stats.json")))
        probs = []
        for repo, sha in (st.get("corpus_shas") or {}).items():
            d = os.path.join(BASE, "corpora", repo)
            p = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                               capture_output=True, text=True)
            if p.returncode != 0:
                probs.append(f"{repo}: git rc={p.returncode}")
            elif p.stdout.strip() != sha:
                probs.append(f"{repo}: streams built at {sha[:10]}, "
                             f"repo now at {p.stdout.strip()[:10]}")
        mfp = os.path.join(BASE, "arxiv_manifest.json")
        cur_m = (hashlib.sha256(open(mfp, "rb").read()).hexdigest()
                 if os.path.exists(mfp) else None)
        if st.get("arxiv_manifest_sha256") != cur_m:
            probs.append("arxiv manifest hash != current")
        for corpus, c in st.get("corpora", {}).items():
            for kind, s in (c.get("streams") or {}).items():
                txt = os.path.join(BASE, "data", "streams", corpus,
                                   f"{kind}.txt")
                if not os.path.exists(txt):
                    probs.append(f"{corpus}/{kind}: txt missing")
                elif os.path.getsize(txt) != s.get("bytes"):
                    probs.append(f"{corpus}/{kind}: recorded "
                                 f"{s.get('bytes')}B != actual "
                                 f"{os.path.getsize(txt)}B")
        check("streams-inputs-current", not probs, probs[:8] or "current")
    except Exception as e:
        check("streams-inputs-current", False, repr(e))


SENTINEL = ["Qwen/Qwen2.5-Coder-0.5B"]


def gate_common_science():
    """Shared by g3a/g3b: g1 + battery + git cleanliness + viability."""
    gate_g1()

    bp = os.path.join(BASE, "results_v2", "battery", "battery.json")
    try:
        b = json.load(open(bp))
        errs = [k for k in b if k.endswith("_error")]
        a = b.get("A_chunk_equality", {})
        zr = b.get("B_zero_rows", {})
        from provenance import source_tree_hash
        src_match = b.get("source_tree_hash") == source_tree_hash()
        mj = json.load(open(os.path.join(BASE, "models.json"))) \
            if os.path.exists(os.path.join(BASE, "models.json")) else {}
        BATTERY_SET = {"Qwen/Qwen2.5-Coder-0.5B", "Qwen/Qwen3-0.6B-Base",
                       "Qwen/Qwen3.5-0.8B-Base", "bigcode/starcoder2-3b"}
        recorded = b.get("model_revisions") or {}
        main_ok = (b.get("model") == "Qwen/Qwen2.5-Coder-0.5B"
                   and b.get("revision") == (mj.get(
                       "Qwen/Qwen2.5-Coder-0.5B") or {}).get("sha"))
        rev_match = (main_ok and set(recorded) == BATTERY_SET
                     and all((mj.get(m) or {}).get("sha") == r
                             for m, r in recorded.items()))
        check("battery-plumbing",
              bool(b.get("plumbing_pass")) and not errs
              and a.get("all_class_ok", False)
              and zr.get("conservation_ok", False)
              and b.get("device") == "cuda"
              and b.get("gate_eligible") is True
              and src_match and rev_match,
              dict(errors=errs, plumbing_pass=b.get("plumbing_pass"),
                   chunk_worst_delta=a.get("mean_abs_delta_nats"),
                   class_ok=a.get("all_class_ok"),
                   conservation=zr.get("conservation_ok"),
                   device=b.get("device"),
                   source_tree_match=src_match,
                   revisions_match=rev_match))
    except Exception as e:
        check("battery-plumbing", False, f"battery.json unreadable: {e!r}")

    # source-tree cleanliness EXCLUDING generated evidence (results_v2 is
    # machine-written and reviewed at boundaries; models.json must be
    # committed because it pins revisions)
    scp = subprocess.run(["git", "-C", BASE, "status", "--porcelain",
                          "--", ".", ":(exclude)results_v2"],
                         capture_output=True, text=True)
    check("source-clean", scp.returncode == 0 and not scp.stdout.strip(),
          f"rc={scp.returncode} " + (scp.stdout.strip()[:200] or "clean"))

    # science gates require a committed, COMPLETE corpus lock (a passing
    # corpus-worktrees with "no lock yet" is fine for g1, not for science)
    try:
        import hashlib
        lock = json.load(open(os.path.join(BASE, "corpora_lock.json")))
        croot = os.path.join(BASE, "corpora")
        have_dirs = {n for n in os.listdir(croot)
                     if os.path.isdir(os.path.join(croot, n, ".git"))}
        locked = set(lock.get("repos", {}))
        unlocked = sorted(have_dirs - locked)       # EVERY present repo
        vanished = sorted(locked - have_dirs)       # must be locked;
        req_unlocked = sorted(EXPECTED_REPOS - locked)  # required subset
        if req_unlocked:
            unlocked = unlocked or req_unlocked
        arx = lock.get("arxiv") or {}
        cur = {}
        for path, key in ((os.path.join(croot, "arxiv", "checksums.json"),
                           "checksums_sha256"),
                          (os.path.join(BASE, "arxiv_manifest.json"),
                           "manifest_sha256")):
            cur[key] = (hashlib.sha256(open(path, "rb").read()).hexdigest()
                        if os.path.exists(path) else None)
        arx_ok = bool(arx) and all(arx.get(k) == cur[k] and cur[k]
                                   for k in cur)
        check("corpus-lock-complete",
              not unlocked and not vanished and arx_ok,
              dict(locked=len(locked), unlocked=unlocked,
                   vanished=vanished, arxiv_hashes_current=arx_ok))
    except Exception as e:
        check("corpus-lock-complete", False,
              f"corpora_lock.json missing/unreadable: {e!r}")

    # science gates forbid weak byte-only arXiv pins: the version+sha256
    # migration must be adopted, not skipped
    try:
        pin = json.load(open(os.path.join(BASE, "arxiv_manifest.json")))
        weak = [k for k, m in pin.items()
                if not m.get("skipped") and not m.get("sha256")]
        check("arxiv-pins-strong", not weak,
              dict(byte_only_pins=len(weak), sample=weak[:5]))
    except Exception as e:
        check("arxiv-pins-strong", False, repr(e))

    raw_inventory()
    viability_check()


def raw_inventory():
    """Reproducibility record of every raw artifact at this gate — dumps,
    metas, quarantines — with hashes. Called at every science gate AND at
    sentinel-post (review fix: the post-run boundary must not retain the
    pre-run empty inventory)."""
    try:
        import glob as _g
        import hashlib as _h
        inv = {}
        for p in sorted(_g.glob(os.path.join(BASE, "nll_dumps", "*"))):
            inv[os.path.basename(p)] = dict(
                bytes=os.path.getsize(p),
                sha256=_h.sha256(open(p, "rb").read()).hexdigest(),
                quarantined=".quarantine-" in p)
        inv_p = os.path.join(BASE, "results_v2", "raw_inventory.json")
        with open(inv_p, "w") as f:
            json.dump(inv, f, indent=1)
        check("raw-inventory", True,
              dict(artifacts=len(inv),
                   quarantined=sum(1 for v in inv.values()
                                   if v["quarantined"])))
    except Exception as e:
        check("raw-inventory", False, repr(e))


# FROZEN expected counts (review: the first preflight must not bless an
# accidental grid shrink): sentinel = 12 full/clean + 5 XL + 6 shuffled +
# 6 perdoc + 18 phase + 6 s2 = 53; small/mid = 48 P0 + 99 P1 + 36 P2 = 183
EXPECTED_N = {"expected_cells_sentinel.json": 53,
              "expected_cells.json": 183}


def expected_cells_check(shards, snap_name):
    """Exact unique expected identities + stream existence for a scope
    (sentinel = {q25c-0.5b}; smallmid = all prio<=2), snapshot-compared
    AND checked against the frozen count."""
    try:
        sys.path.insert(0, BASE)
        from run_phase1 import jobs, phase_of
        ids, missing_streams = [], []
        for prio, mid, short, corpus, kind, ctx, flags in jobs():
            if prio > 2:
                continue
            if shards and short not in shards:
                continue
            tag = kind + ("__perdoc" if "--reset-per-doc" in flags else "")
            ph = phase_of(flags)
            if ph:
                tag += f"__ph{ph}"
            ids.append(f"{short}__{corpus}__{tag}")
            sp = os.path.join(BASE, "data", "streams", corpus, f"{kind}.txt")
            if not (os.path.exists(sp) and os.path.exists(
                    sp.replace(".txt", ".manifest.jsonl"))):
                missing_streams.append(f"{corpus}/{kind}")
        dup = len(ids) != len(set(ids))
        structurally_ok = (not dup and not missing_streams
                           and len(ids) == EXPECTED_N.get(snap_name))
        snap_p = os.path.join(BASE, "results_v2", snap_name)
        drift = None
        if os.path.exists(snap_p):
            old = json.load(open(snap_p))
            drift = sorted(set(old) ^ set(ids))[:10] or None
        elif structurally_ok:  # never freeze a broken/shrunk grid
            json.dump(sorted(ids), open(snap_p, "w"), indent=0)
        else:
            drift = ["snapshot not frozen: structural validation failed"]
        n_ok = len(ids) == EXPECTED_N.get(snap_name)
        check(f"expected-cells[{snap_name}]",
              not dup and not missing_streams and not drift and n_ok,
              dict(n=len(ids), frozen_n=EXPECTED_N.get(snap_name),
                   dup=dup, missing_streams=missing_streams[:8],
                   drift=drift))
    except Exception as e:
        check(f"expected-cells[{snap_name}]", False, repr(e))


def viability_check():
    # sample-size + clean-target-masking viability (PREREG §6): effective
    # windows per corpus and post-cutoff target mass inside full_topo
    try:
        from prep_streams import CUTOFFS as cutoffs  # single cutoff source
        viability = {}
        for corpus in ("physlib", "mathlib", "qutip", "sympy", "geant4",
                       "arxiv_old"):
            mp = os.path.join(BASE, "data", "streams", corpus,
                              "full_topo.manifest.jsonl")
            if not os.path.exists(mp):
                continue
            docs = [json.loads(l) for l in open(mp)]
            total = sum(d["end"] - d["start"] for d in docs)
            ent = dict(est_windows=round(total / 105_000, 1))
            if corpus != "arxiv_old":  # LaTeX excluded from masking (§5)
                for tag, cut in cutoffs.items():
                    post = [d for d in docs if (d.get("date") or "") > cut]
                    ent[tag] = dict(
                        docs=len(post),
                        kbytes=round(sum(d["end"] - d["start"]
                                         for d in post) / 1e3),
                        ok=len(post) >= 20 and sum(
                            d["end"] - d["start"] for d in post) >= 300_000)
            viability[corpus] = ent
        check("windows-and-masking-viability", bool(viability), viability)
    except Exception as e:
        check("windows-and-masking-viability", False, repr(e))


def gate_g3a():
    """Sentinel gate: only the battery-cached 0.5B and ITS expected cells
    — battery-stage caching must suffice (review fix)."""
    gate_common_science()
    ok, det = models_cached(SENTINEL)
    check("models-sentinel-pinned", ok, det)
    expected_cells_check({"q25c-0.5b"}, "expected_cells_sentinel.json")


def sentinel_completeness():
    """Post-sentinel artifact verification (review: definition counts are
    insufficient): every one of the 53 sentinel cells — including all
    three phase variants per corpus — must pass the FULL cell_done
    identity/integrity check on the actual artifacts."""
    try:
        sys.path.insert(0, BASE)
        from run_phase1 import jobs, cell_out, cell_done
        mj_p = os.path.join(BASE, "models.json")
        mj = json.load(open(mj_p)) if os.path.exists(mj_p) else {}
        done, missing = [], []
        n_phase = 0
        for prio, mid, short, corpus, kind, ctx, flags in jobs():
            if prio > 2 or short != "q25c-0.5b":
                continue
            if "--window-phase" in flags:
                n_phase += 1
            out = cell_out(short, corpus, kind, flags)
            stream = os.path.join(BASE, "data", "streams", corpus,
                                  f"{kind}.txt")
            (done if os.path.exists(stream) and cell_done(
                out, mid, ctx, flags, stream, mj) else missing).append(
                os.path.basename(out))
        check("sentinel-artifacts-complete",
              len(done) == 53 and not missing and n_phase == 18,
              dict(verified=len(done), missing=missing[:8],
                   phase_variants_defined=n_phase))
    except Exception as e:
        check("sentinel-artifacts-complete", False, repr(e))


def gate_sentinel_post():
    """The G3a REVIEW boundary: artifact-level completeness of the
    finished sentinel run PLUS the refreshed post-run raw inventory (no
    model-cache rechecks — those belong to the pre-run gates)."""
    sentinel_completeness()
    raw_inventory()


def gate_g3b():
    """Small/mid expansion gate: requires REVIEWED sentinel evidence AND
    artifact-verified sentinel completeness."""
    gate_common_science()
    sentinel_completeness()
    ok, det = models_cached(SMALL_MID)
    check("models-small-mid-pinned", ok, det)
    expected_cells_check(None, "expected_cells.json")
    for tag, fname, hint in (
            ("sentinel-reviewed", "sentinel_signoff.json",
             "run the G3a sentinel, review its evidence"),
            ("v2-pilot-reviewed", "v2_pilot_signoff.json",
             "complete G3.5 (V2-a extraction validation + V2-b pilot) — "
             "the adopted ordering puts the paired experiment before "
             "grid expansion")):
        sp = os.path.join(BASE, "results_v2", fname)
        try:
            s = json.load(open(sp))
            check(tag, s.get("approved") is True,
                  dict(signoff=s.get("approved"), by=s.get("by"),
                       note=(s.get("note") or "")[:120]))
        except Exception as e:
            check(tag, False,
                  f"no signoff ({e!r}) — {hint}, then commit "
                  f"results_v2/{fname}")


def gate_big():
    ok, det = models_cached(BIG)
    check("models-big-pinned", ok, det)
    # FAIL-CLOSED until implemented (review: the battery's promise that
    # DeepSeek runs the A-probe at this gate must not be a dead comment):
    # required before any big shard — (1) DeepSeek-V2-Lite architecture
    # probe (loader class/params, chunk-vs-one-shot across its MoE +
    # 4-bit-free path, tokenizer offsets), (2) Qwen3.5 long-context cache
    # probe at >= 131072 tokens for the 131k arm, both via a battery
    # --big mode writing results_v2/battery/battery_big.json.
    bb = os.path.join(BASE, "results_v2", "battery", "battery_big.json")
    try:
        b = json.load(open(bb))
        check("big-battery-probes",
              bool(b.get("plumbing_pass")) and b.get("device") == "cuda"
              and b.get("gate_eligible") is True,
              dict(plumbing_pass=b.get("plumbing_pass"),
                   probes=sorted(k for k in b if k.startswith("A_"))))
    except Exception as e:
        check("big-battery-probes", False,
              f"battery --big not yet implemented/run ({e!r}) — the big "
              "gate stays closed until its probes exist")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=("g1", "g3a", "sentinel-post", "g3b",
                                       "g3", "big"), default="g1")
    args = ap.parse_args()
    {"g1": gate_g1, "g3a": gate_g3a, "sentinel-post": gate_sentinel_post,
     "g3b": gate_g3b, "g3": gate_g3b, "big": gate_big}[args.gate]()
    os.makedirs(os.path.join(BASE, "results_v2"), exist_ok=True)
    report["gate"] = args.gate
    with open(os.path.join(BASE, "results_v2",
                           f"preflight_{args.gate}.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(f"PREFLIGHT[{args.gate}]", "PASS" if OK else "FAIL")
    sys.exit(0 if OK else 1)
