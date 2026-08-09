#!/usr/bin/env python3
"""Fail-closed preflight (PREREG §12). Gates:
  --gate g1   after acquisition repair: env, corpora, streams, locks, shas
  --gate g3a  sentinel science gate (battery-cached 0.5B; 44 frozen cells)
  --gate g3b  expansion gate (small/mid pinned; 152 frozen cells; requires
              sentinel AND paired-v2 pilot signoffs)  [alias: g3]
arXiv is an OPTIONAL preserved artifact: absent -> non-blocking report;
present -> integrity must validate and failure blocks G1 (tri-state).
  --gate big  big shards: big-rung models cached at pinned revisions
Writes results_v2/preflight_<gate>.json (gate-specific evidence is
preserved, never overwritten by later gates). Exit 0 only on pass."""
import argparse, json, os, subprocess, sys, tempfile

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

# ---- FROZEN arm-feasibility manifest (PREREG §5 amendment, adopted
# before any battery/grid outcome at G1, from deterministic
# streams_stats) ----
# masking_viable: >= MASK_MIN_DOCS post-cutoff docs AND >= MASK_MIN_BYTES
# post-cutoff bytes INSIDE the sampled full_topo stream (the arm operates
# in-stream; big corpora dilute recent files — mathlib holds 5,000,507B
# corpus-wide all-new at c2026_02 but only 167,496B landed in the 2.4MB
# seeded sample). allnew_matched: >= MIN_MATCHED post-cutoff bytes
# corpus-wide. FLOORS NEVER MOVE POST-HOC: geant4 c2026_02 all-new
# (130,834B) and mathlib c2026_02 masking (167,496B) are recorded
# near-misses, not grounds for adjustment. Every science gate verifies
# the realized sets still equal these frozen sets EXACTLY (either
# direction of drift fails); narrowness is SCOPED in PREREG §5/§6
# (Qwen3.5 cross-language contamination claims barred), never gated away.
CORE_STREAM_CORPORA = ("physlib", "mathlib", "qutip", "sympy", "geant4")
MASK_MIN_DOCS = 20
MASK_MIN_BYTES = 300_000
ARM_FEASIBILITY = {
    "c2024_11": dict(masking={"physlib", "mathlib"},
                     allnew={"physlib", "mathlib", "sympy", "geant4"}),
    "c2025_04": dict(masking={"physlib", "mathlib"},
                     allnew={"physlib", "mathlib", "sympy", "geant4"}),
    "c2026_02": dict(masking={"physlib"},
                     allnew={"physlib", "mathlib"}),
}


def feasible_sets(clean_avail, masking):
    """PURE frozen-floor classifier (testable): clean_avail[tag][corpus]
    = post-cutoff corpus-wide bytes; masking[tag][corpus] = (docs,
    in-stream post-cutoff bytes). Returns per-tag realized arm sets
    under the FROZEN floors."""
    from prep_streams import MIN_MATCHED
    out = {}
    for tag in ARM_FEASIBILITY:
        out[tag] = dict(
            allnew={c for c, v in (clean_avail.get(tag) or {}).items()
                    if v >= MIN_MATCHED},
            masking={c for c, db in (masking.get(tag) or {}).items()
                     if db[0] >= MASK_MIN_DOCS and db[1] >= MASK_MIN_BYTES})
    return out


def masking_mass():
    """(docs, bytes) of post-cutoff material INSIDE full_topo per
    (tag, corpus) — the masking arm's realized in-stream mass, computed
    from the stream manifests exactly as viability_check reports it."""
    from prep_streams import CUTOFFS
    out = {t: {} for t in CUTOFFS}
    for corpus in CORE_STREAM_CORPORA:
        mp = os.path.join(BASE, "data", "streams", corpus,
                          "full_topo.manifest.jsonl")
        if not os.path.exists(mp):
            continue
        docs = [json.loads(l) for l in open(mp)]
        for tag, cut in CUTOFFS.items():
            post = [d for d in docs if (d.get("date") or "") > cut]
            out[tag][corpus] = (len(post),
                                sum(d["end"] - d["start"] for d in post))
    return out


def arm_feasibility_check(tags=None, name="arm-feasibility-frozen"):
    """Realized arm sets (from current streams_stats + manifests) must
    EQUAL the frozen manifest for the given tags — fail-closed in both
    directions, so feasibility can never drift silently under a
    re-prep. G1/common gates verify all tags; g3a verifies only its own
    family's row (a sentinel gate keyed to another family's feasibility
    would be incoherent)."""
    try:
        sys.path.insert(0, BASE)
        st = json.load(open(os.path.join(BASE, "data",
                                         "streams_stats.json")))
        avail = st.get("clean_available") or {}
        mm = masking_mass()
        derived = feasible_sets(avail, mm)
        want = tags or list(ARM_FEASIBILITY)
        bad = {}
        for t in want:
            for arm in ("masking", "allnew"):
                if derived[t][arm] != ARM_FEASIBILITY[t][arm]:
                    bad[f"{t}/{arm}"] = dict(
                        realized=sorted(derived[t][arm]),
                        frozen=sorted(ARM_FEASIBILITY[t][arm]))
        # the committed preflight report carries the FULL numeric rows
        # (audit fix: PREREG §5 references this report for the
        # c2024_11/c2025_04 rows, so a passing gate must publish them,
        # not just the set verdicts)
        rows = {t: {c: dict(masking_docs=mm.get(t, {}).get(c, (0, 0))[0],
                            masking_bytes=mm.get(t, {}).get(c, (0, 0))[1],
                            allnew_bytes=(avail.get(t) or {}).get(c))
                    for c in CORE_STREAM_CORPORA}
                for t in want}
        check(name, not bad,
              dict(mismatches=bad or None,
                   frozen={t: {a: sorted(ARM_FEASIBILITY[t][a])
                               for a in ("masking", "allnew")}
                           for t in want},
                   realized_rows=rows))
    except Exception as e:
        check(name, False, repr(e))


def arxiv_present():
    """ONE shared recursive presence definition (review fix: a shallow
    listdir treated a nested-only stray .tex as absent, contradicting
    scan_disk and letting rot pass silently): any .tex anywhere under
    corpora/arxiv is PRESENT and must then validate."""
    sys.path.insert(0, BASE)
    from arxiv_fetch import material_present
    return material_present(os.path.join(BASE, "corpora", "arxiv"))


def arxiv_validated_check():
    """Optional-corpus tri-state (amendment, PREREG §2/§13): ABSENT
    passes non-blocking; PRESENT must fully validate against the adopted
    version+sha256 manifest — failure blocks the gate. Validation
    MEASURES THE DISK (review fix: trusting checksums.json's recorded
    hashes let a .tex mutated after the ledger was written pass): every
    current canonical on-disk file is re-hashed against the pin; the
    ledger is cross-checked as fetch-time evidence, never as truth."""
    if not arxiv_present():
        check("arxiv-validated", True,
              "optional corpus absent — non-blocking (tri-state)")
        return
    try:
        sys.path.insert(0, BASE)
        from arxiv_fetch import ledger_vs_pin, verify_disk_against_pin
        pin = json.load(open(os.path.join(BASE, "arxiv_manifest.json")))
        disk = verify_disk_against_pin(
            os.path.join(BASE, "corpora", "arxiv"), pin)
        disk_ok = not (disk["missing"] or disk["extra"]
                       or disk["hash_mismatch"] or disk["bytes_mismatch"])
        # ledger cross-check (weaker, still required): the fetch-time
        # record must cover the exact universe and its recorded
        # sha256/bytes must agree with the pin INDEPENDENTLY of its own
        # matches_pin claim (review fix: a forged/stale record asserting
        # matches_pin=true must not pass)
        cj = json.load(open(os.path.join(BASE, "corpora", "arxiv",
                                         "checksums.json")))
        expected = {f"{m['era']}/{k}" for k, m in pin.items()
                    if not m.get("skipped")}
        got = set(cj["files"])
        ledger_bad = (sorted(expected - got) + sorted(got - expected)
                      + ledger_vs_pin(cj["files"], pin)
                      + [k for k, v in cj["files"].items()
                         if not v.get("matches_pin")]
                      + list(cj.get("extra_on_disk") or []))
        check("arxiv-validated", disk_ok and not ledger_bad,
              dict(expected=len(expected), disk_rehash_ok=disk_ok,
                   missing=disk["missing"][:5], extra=disk["extra"][:5],
                   hash_mismatch=disk["hash_mismatch"][:5],
                   bytes_mismatch=disk["bytes_mismatch"][:5],
                   byte_only_pins=len(disk["byte_only_pins"]),
                   ledger_bad=ledger_bad[:5]))
    except Exception as e:
        check("arxiv-validated", False, repr(e))


def current_era_presence():
    sys.path.insert(0, BASE)
    from arxiv_fetch import material_present
    root = os.path.join(BASE, "corpora", "arxiv")
    return {era: material_present(root, era=era) for era in ("old", "new")}


def stats_arxiv_rows_ok(st_rows, sha, era_present):
    """Exact tri-state consistency (review fix: bool(rows) let a fully
    valid two-era corpus pass with a missing or stale era row): the
    arxiv rows in streams_stats must be EXACTLY those implied by current
    per-era presence, and the manifest hash must be recorded iff any era
    is present. Pure so the rule is testable."""
    exp = sorted(n for n, era in (("arxiv_old", "old"),
                                  ("arxiv_new", "new"))
                 if era_present.get(era))
    return sorted(st_rows) == exp and (sha is not None) == bool(exp)


def lock_arxiv_ok(present, arx, cur):
    """Frozen tri-state lock rule (amendment): current ABSENT passes
    regardless of a prior locked identity (reported, never blocking);
    current PRESENT requires the lock to CARRY the identity and every
    hash to match the on-disk state — pure so the rule is testable."""
    return ((not present) or
            (bool(arx) and all(arx.get(k) == cur[k] and cur[k]
                               for k in cur)))


def arxiv_pins_strong_check():
    """Science gates forbid weak byte-only arXiv pins WHEN the optional
    corpus is present: the version+sha256 migration must be adopted, not
    skipped. Absent -> non-blocking (tri-state)."""
    if not arxiv_present():
        check("arxiv-pins-strong", True,
              "optional corpus absent — non-blocking (tri-state)")
        return
    try:
        pin = json.load(open(os.path.join(BASE, "arxiv_manifest.json")))
        weak = [k for k, m in pin.items()
                if not m.get("skipped") and not m.get("sha256")]
        check("arxiv-pins-strong", not weak,
              dict(byte_only_pins=len(weak), sample=weak[:5]))
    except Exception as e:
        check("arxiv-pins-strong", False, repr(e))


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
            if name not in ("arxiv_old", "arxiv_new") and (
                    not ft or ft["bytes"] < 500_000):
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
        # exact-set per tag vs the FROZEN feasibility manifest
        # (amendment: the old >=3 scalar could be silently satisfied by
        # a single-language pair — c2026_02 matched cells are the Lean
        # pair only; narrowness is scoped in PREREG §5/§6, never
        # threshold-shopped)
        clean_sets = {t: sorted(n for n, c in st["corpora"].items()
                                if not c["streams"].get(f"clean_{t}", {})
                                .get("unmatched", True))
                      for t in ARM_FEASIBILITY}
        check("clean-matched-cells",
              all(clean_sets[t] == sorted(ARM_FEASIBILITY[t]["allnew"])
                  for t in ARM_FEASIBILITY),
              dict(realized=clean_sets,
                   frozen={t: sorted(v["allnew"])
                           for t, v in ARM_FEASIBILITY.items()}))
        gitless = [k for k, v in st.get("corpus_shas", {}).items() if not v]
        shas_keys = set(st.get("corpus_shas") or {})
        # tri-state consistency (amendment): streams_stats must carry
        # EXACTLY the arxiv rows implied by current per-era presence
        # (review fix: bool(rows) let a valid two-era corpus pass with a
        # missing/stale era row) and the manifest hash iff any era is
        # present; stale pre-demotion residue fails and forces re-prep
        st_arx = sorted(n for n in st.get("corpora", {})
                        if n in ("arxiv_old", "arxiv_new"))
        sha = st.get("arxiv_manifest_sha256")
        ep = current_era_presence()
        arx_need = stats_arxiv_rows_ok(st_arx, sha, ep)
        check("corpus-shas", shas_keys == STREAM_SOURCE_REPOS
              and not gitless and arx_need,
              dict(keys=sorted(shas_keys),
                   expected=sorted(STREAM_SOURCE_REPOS), gitless=gitless,
                   era_presence=ep, arxiv_rows_in_stats=st_arx,
                   arxiv_sha=(sha or "")[:12] or None))
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
        # stat BASE, the ACTUAL work filesystem (review fix: the autofs
        # parent /orcd/pool/008 0-stats before automount, so the check
        # measured the automounter, not the volume); BASE forces the
        # mount and is where dumps/streams/caches actually land. Inodes
        # reported too — FILE quota, not bytes, was the HOME failure
        # mode on this cluster.
        du = shutil.disk_usage(BASE)
        sv = os.statvfs(BASE)
        free_gb = du.free / 1e9
        check("disk-headroom", free_gb > 100,
              dict(path=BASE, free_gb=round(free_gb),
                   total_gb=round(du.total / 1e9),
                   inodes_free=sv.f_favail))
    except Exception as e:
        check("disk-headroom", False, repr(e))

    # environment identity (schema v4, shared provenance definition):
    # the live environment must equal BOTH the committed lock (every pin
    # + python contract) and the write-once software-only freeze — one
    # implementation shared with eval refusal and cell_done, so the
    # gate, the evaluator, and cell acceptance can never disagree.
    # GPU/driver stay out of this by frozen decision (runtime-notes.txt
    # is the informational record; the battery overlap item gates
    # hardware effects).
    try:
        sys.path.insert(0, BASE)
        from provenance import (env_fingerprint, env_matches_freeze,
                                env_matches_lock)
        lock_ok, lock_probs = env_matches_lock()
        frz_ok, frz_detail = env_matches_freeze()
        check("env-frozen", lock_ok and frz_ok,
              dict(lock_ok=lock_ok, lock_problems=lock_probs[:6],
                   freeze_ok=frz_ok, freeze_detail=frz_detail,
                   env_fingerprint=env_fingerprint()[:16]))
    except Exception as e:
        check("env-frozen", False, repr(e))

    arxiv_validated_check()

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
        st_rows = [n for n in st.get("corpora", {})
                   if n in ("arxiv_old", "arxiv_new")]
        ep = current_era_presence()
        if not stats_arxiv_rows_ok(st_rows, st.get("arxiv_manifest_sha256"),
                                   ep):
            probs.append(f"arxiv rows/hash inconsistent with current era "
                         f"presence (rows={sorted(st_rows)}, eras={ep})")
        if any(ep.values()) and st.get("arxiv_manifest_sha256") != cur_m:
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

    # frozen arm-feasibility manifest: realized sets must equal the
    # PREREG §5 table exactly (all tags; g3a re-checks its own row)
    arm_feasibility_check()


SENTINEL = ["Qwen/Qwen2.5-Coder-0.5B"]


def gate_common_science():
    """Shared by g3a/g3b: g1 + battery + git cleanliness + viability."""
    gate_g1()

    bp = os.path.join(BASE, "results_v2", "battery", "battery.json")
    try:
        b = json.load(open(bp))
        errs = [k for k in b if k.endswith("_error")]
        a = b.get("A_fixed_chunk_semantics", {})
        from layout import PRODUCTION_CHUNK_TOKENS
        from validity_battery import (FAM_SMALL, a_fixed_chunk_verdict)
        a_ok, a_fails = a_fixed_chunk_verdict(
            a, tuple(FAM_SMALL), PRODUCTION_CHUNK_TOKENS)
        a_stored_ok = ((a.get("verdict") or {}).get("ok") == a_ok
                       and (a.get("verdict") or {}).get("failures")
                       == a_fails)
        zr = b.get("B_zero_rows", {})
        from provenance import source_tree_hash
        src_match = b.get("source_tree_hash") == source_tree_hash()
        # schema-v4 identities: battery evidence must have been produced
        # by the CURRENT measurement harness in the CURRENT software
        # environment (one shared definition with eval/cell_done)
        from provenance import env_fingerprint, harness_hash
        # identities must match current AND carry the completion
        # guarantee — older battery evidence without the mid-run
        # re-check (identities_unchanged_during_run) can never gate
        ident_match = (b.get("harness_hash") == harness_hash()
                       and b.get("env_fingerprint") == env_fingerprint()
                       and b.get("identities_unchanged_during_run")
                       is True)
        # item E must be NON-VACUOUS on its designated corpus (PREREG
        # §7/§13: physlib source-imports structurally infeasible — an
        # empty E once reached the gate as n_eligible_targets=0; the
        # expected values are PINNED here, never read from the evidence)
        e_meta = b.get("E_meta") or {}
        e_res = b.get("E_dep_vs_random_context") or {}
        e_ok = (e_meta.get("corpus") == "mathlib"
                and e_res.get("corpus") == "mathlib"
                and e_meta.get("eligibility_floor") == 8
                and (e_meta.get("n_eligible_targets") or 0) >= 8
                and e_res.get("n") == 8  # exact realized rows, no skips
                and e_res.get("skipped_insufficient_pool") == [])
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
              and a_ok and a_stored_ok
              and "A_chunk_equality" not in b
              and zr.get("conservation_ok", False)
              and b.get("device") == "cuda"
              and b.get("gate_eligible") is True
              and src_match and rev_match and ident_match and e_ok,
              dict(errors=errs, plumbing_pass=b.get("plumbing_pass"),
                   item_a_recomputed_ok=a_ok,
                   item_a_failures=a_fails,
                   item_a_stored_matches=a_stored_ok,
                   production_chunk=a.get("production_chunk"),
                   f2_stats=(a.get("f2") or {}).get("stats"),
                   conservation=zr.get("conservation_ok"),
                   device=b.get("device"),
                   source_tree_match=src_match,
                   revisions_match=rev_match,
                   harness_env_match=ident_match,
                   e_designated_nonvacuous=e_ok,
                   e_corpus=e_meta.get("corpus"),
                   e_eligible=e_meta.get("n_eligible_targets")))
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
        # frozen tri-state rule: CURRENT state governs. Current ABSENT
        # -> ok regardless of a prior locked identity (reported).
        # Current PRESENT -> the lock must carry the identity and it
        # must match the on-disk hashes (a present-but-unlocked corpus
        # fails: rewrite + commit the lock to adopt it).
        present = arxiv_present()
        arx_ok = lock_arxiv_ok(present, arx, cur)
        check("corpus-lock-complete",
              not unlocked and not vanished and arx_ok,
              dict(locked=len(locked), unlocked=unlocked,
                   vanished=vanished, arxiv_present=present,
                   arxiv_hashes_current=arx_ok,
                   prior_lock_identity_reported=bool(arx) and not present))
    except Exception as e:
        check("corpus-lock-complete", False,
              f"corpora_lock.json missing/unreadable: {e!r}")

    arxiv_pins_strong_check()

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
        if os.path.exists(inv_p):
            prior = json.load(open(inv_p))
            if not isinstance(prior, dict):
                raise RuntimeError("existing raw inventory is not an object")
            lost = []
            for name, row in prior.items():
                if inv.get(name) == row:
                    continue
                quarantines = [
                    candidate for candidate, candidate_row in inv.items()
                    if candidate.startswith(name + ".quarantine-")
                    and candidate_row.get("quarantined") is True
                    and candidate_row.get("bytes") == row.get("bytes")
                    and candidate_row.get("sha256") == row.get("sha256")]
                if not quarantines:
                    lost.append(name)
            if lost:
                raise RuntimeError(
                    f"refusing raw-inventory loss: {len(lost)} prior "
                    f"artifacts disappeared or changed; sample={lost[:5]}")
        os.makedirs(os.path.dirname(inv_p), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".raw-inventory-", dir=os.path.dirname(inv_p))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(inv, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, inv_p)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        check("raw-inventory", True,
              dict(artifacts=len(inv),
                   quarantined=sum(1 for v in inv.values()
                                   if v["quarantined"])))
    except Exception as e:
        check("raw-inventory", False, repr(e))


# FROZEN expected counts (review: the first preflight must not bless an
# accidental grid shrink). Post-arXiv-demotion: sentinel = 10 full/clean +
# 4 XL + 5 shuffled + 5 perdoc + 15 phase + 5 s2 = 44; small/mid =
# 40 P0 + 82 P1 + 30 P2 = 152
EXPECTED_N = {"expected_cells_sentinel.json": 44,
              "expected_cells.json": 152}


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
    # windows per corpus and post-cutoff target mass inside full_topo —
    # shares masking_mass() and the FROZEN floors with the feasibility
    # gate, so the report and the gate can never disagree
    try:
        mm = masking_mass()
        viability = {}
        for corpus in CORE_STREAM_CORPORA:
            mp = os.path.join(BASE, "data", "streams", corpus,
                              "full_topo.manifest.jsonl")
            if not os.path.exists(mp):
                continue
            docs = [json.loads(l) for l in open(mp)]
            total = sum(d["end"] - d["start"] for d in docs)
            ent = dict(est_windows=round(total / 105_000, 1))
            for tag in mm:
                d_n, d_b = mm[tag].get(corpus, (0, 0))
                ent[tag] = dict(docs=d_n, kbytes=round(d_b / 1e3),
                                ok=d_n >= MASK_MIN_DOCS
                                and d_b >= MASK_MIN_BYTES)
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
    # the sentinel's OWN family arm row (c2024_11) must hold — the
    # sentinel stop/go items evaluate c2024_11 arms only, and a
    # sentinel gate keyed to another family's feasibility would be
    # incoherent (amendment, PREREG §5)
    arm_feasibility_check(tags=["c2024_11"], name="g3a-own-family-arms")


def sentinel_completeness():
    """Post-sentinel artifact verification (review: definition counts are
    insufficient): every one of the 44 sentinel cells (amendment: arXiv
    demoted out of the core grid) — including all three phase variants
    per core corpus — must pass the FULL cell_done identity/integrity
    check on the actual artifacts."""
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
              len(done) == 44 and not missing and n_phase == 15,
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
