#!/usr/bin/env python3
"""ARM_CS CS-2 pools: per-language byte corpora with NESTED whole-doc rungs.

Same collection rule as prep_pools/lang_stats (imports the shared collector,
so nothing can drift), but the train bin is written in SEEDED-SHUFFLED doc
order and the rung table records byte boundaries that fall on document
edges. A byte-prefix truncation of the bin (train_scratch --max-train-bytes)
is therefore EXACTLY a nested whole-document data subset: rung k's docs are
a strict subset of rung k+1's (ARM_CS §4).

Val split: every 10th collected doc (i % 10 == 7), identical rule to
prep_pools. Budgets: lean/python/cpp matched to the smallest train pool
(cap 120MB); latex rides along unmatched (reference arm).

Outputs: data/cs2/<lang>_train.bin, <lang>_val.bin, <lang>_cs2.json.
NEW STANDALONE FILE (ARM_CS discipline; data/cs2 namespace).
"""
import json
import os
import subprocess
import sys

import numpy as np

from lang_stats import collect_labeled, doc_manifest_sha

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "cs2")
SEED_RUNGS = 29
CAP = 120_000_000
VAL_CAP = 12_000_000
FRACS = [1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0]
MATCHED = ("lean", "python", "cpp")


def split_shuffled(docs, rng):
    """Round-2 NB4 fix: split AFTER the seeded shuffle so validation is a
    random draw from the pooled mixture, not the collection order."""
    order = rng.permutation(len(docs))
    val = [docs[order[i]] for i in range(len(order)) if i % 10 == 7]
    train = [docs[order[i]] for i in range(len(order)) if i % 10 != 7]
    return train, val


def main():
    force = "--force" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--force"]
    langs = argv[0].split(",") if argv else \
        ["lean", "python", "cpp", "latex"]
    os.makedirs(OUT, exist_ok=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE,
                                capture_output=True,
                                text=True).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", ".",
             ":(exclude)results_cs", ":(exclude)results_v2"],
            cwd=BASE, capture_output=True, text=True).stdout.strip())
    except OSError:
        commit, dirty = None, None
    pools = {}
    shas = {}
    for lang in langs:
        docs = collect_labeled(lang)
        rng = np.random.default_rng(SEED_RUNGS)
        train, val = split_shuffled(docs, rng)
        pools[lang] = (train, val)
        shas[lang] = doc_manifest_sha([b for _, b in docs])
        print(f"[{lang}] {len(docs)} docs, train "
              f"{sum(len(b) for _, b in train)/1e6:.1f}MB, val "
              f"{sum(len(b) for _, b in val)/1e6:.1f}MB", file=sys.stderr)
    current = all(
        os.path.exists(os.path.join(OUT, f"{lang}_cs2.json"))
        and json.load(open(os.path.join(OUT, f"{lang}_cs2.json"))
                      ).get("collection_sha256") == shas[lang]
        and json.load(open(os.path.join(OUT, f"{lang}_cs2.json"))
                      ).get("val_cap") == VAL_CAP
        for lang in langs)
    if current and not force:
        print("[cs2_pools] all manifests current, skipping (idempotent)",
              file=sys.stderr)
        return
    stale = [lang for lang in langs
             if os.path.exists(os.path.join(OUT, f"{lang}_cs2.json"))]
    if stale and not force:
        sys.exit(f"manifests exist but are stale ({stale}); rerun with "
                 "--force to rebuild (rung boundaries may change)")

    matched = min(min(sum(len(b) for _, b in pools[l][0])
                      for l in MATCHED if l in pools), CAP) \
        if any(l in pools for l in MATCHED) else CAP

    for lang, (train, val) in pools.items():
        # train is ALREADY in seeded-shuffled mixture order (split rule):
        # filling in order keeps rungs = byte-prefixes of that order
        cap = matched if lang in MATCHED else CAP
        kept, s = [], 0
        for i in range(len(train)):
            b = train[i][1]
            if s + len(b) > cap and s > 0:
                continue  # skip docs that would overflow; keep filling small
            kept.append(i)
            s += len(b)
            if s >= cap:
                break
        total = s
        # rung boundaries on doc edges (>=1 doc per rung)
        cum, offs = 0, []
        for i in kept:
            cum += len(train[i][1])
            offs.append(cum)
        rungs = {}
        for f in FRACS:
            target = f * total
            k = 0
            while k + 1 < len(offs) and offs[k] < target:
                k += 1
            rungs[f"{f:.6f}"] = int(offs[k])
        with open(os.path.join(OUT, f"{lang}_train.bin"), "wb") as fo:
            for i in kept:
                fo.write(train[i][1])
        vkeep, vs = [], 0
        val_offs = []
        for r, b in val:
            if vs >= VAL_CAP:
                break
            vkeep.append((r, b))
            vs += len(b)
            val_offs.append(vs)  # cumulative end-offset per val doc
        with open(os.path.join(OUT, f"{lang}_val.bin"), "wb") as fo:
            for _, b in vkeep:
                fo.write(b)
        repo_hist = {}
        for i in kept:
            r = train[i][0]
            repo_hist[r] = repo_hist.get(r, 0) + len(train[i][1])
        man = dict(lang=lang, seed_rungs=SEED_RUNGS, matched_cap=int(cap),
                   val_cap=VAL_CAP, commit=commit, dirty=dirty,
                   collection_sha256=shas[lang],
                   train_bytes=int(total), n_train_docs=len(kept),
                   val_bytes=int(vs), n_val_docs=len(vkeep),
                   rung_boundaries=rungs, repos=repo_hist,
                   doc_offsets=[int(o) for o in offs],
                   val_doc_offsets=[int(o) for o in val_offs])
        with open(os.path.join(OUT, f"{lang}_cs2.json"), "w") as fo:
            json.dump(man, fo, indent=1)
        print(f"[{lang}] train {total/1e6:.1f}MB val {vs/1e6:.1f}MB rungs "
              + " ".join(f"{v/1e6:.2f}" for v in rungs.values()),
              file=sys.stderr)


if __name__ == "__main__":
    main()
