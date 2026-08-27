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
import sys

import numpy as np

from lang_stats import collect_labeled

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "cs2")
SEED_RUNGS = 29
CAP = 120_000_000
VAL_CAP = 3_000_000
FRACS = [1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0]
MATCHED = ("lean", "python", "cpp")


def split(docs):
    val = [d for i, d in enumerate(docs) if i % 10 == 7]
    train = [d for i, d in enumerate(docs) if i % 10 != 7]
    return train, val


def main():
    langs = sys.argv[1].split(",") if len(sys.argv) > 1 else \
        ["lean", "python", "cpp", "latex"]
    os.makedirs(OUT, exist_ok=True)
    pools = {}
    for lang in langs:
        docs = collect_labeled(lang)
        train, val = split(docs)
        pools[lang] = (train, val)
        print(f"[{lang}] {len(docs)} docs, train "
              f"{sum(len(b) for _, b in train)/1e6:.1f}MB, val "
              f"{sum(len(b) for _, b in val)/1e6:.1f}MB", file=sys.stderr)

    matched = min(min(sum(len(b) for _, b in pools[l][0])
                      for l in MATCHED if l in pools), CAP) \
        if any(l in pools for l in MATCHED) else CAP

    for lang, (train, val) in pools.items():
        rng = np.random.default_rng(SEED_RUNGS)
        order = rng.permutation(len(train))
        cap = matched if lang in MATCHED else CAP
        kept, s = [], 0
        for i in order:
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
        for r, b in val:
            if vs >= VAL_CAP:
                break
            vkeep.append((r, b))
            vs += len(b)
        with open(os.path.join(OUT, f"{lang}_val.bin"), "wb") as fo:
            for _, b in vkeep:
                fo.write(b)
        repo_hist = {}
        for i in kept:
            r = train[i][0]
            repo_hist[r] = repo_hist.get(r, 0) + len(train[i][1])
        man = dict(lang=lang, seed_rungs=SEED_RUNGS, matched_cap=int(cap),
                   train_bytes=int(total), n_train_docs=len(kept),
                   val_bytes=int(vs), n_val_docs=len(vkeep),
                   rung_boundaries=rungs, repos=repo_hist,
                   doc_offsets=[int(o) for o in offs])
        with open(os.path.join(OUT, f"{lang}_cs2.json"), "w") as fo:
            json.dump(man, fo, indent=1)
        print(f"[{lang}] train {total/1e6:.1f}MB val {vs/1e6:.1f}MB rungs "
              + " ".join(f"{v/1e6:.2f}" for v in rungs.values()),
              file=sys.stderr)


if __name__ == "__main__":
    main()
