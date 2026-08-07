#!/usr/bin/env python3
"""Phase 2 training pools: per-language byte corpora, matched budgets.

Languages: lean / python / cpp are budget-matched (min pool size, capped);
latex rides along unmatched (reference arm) because arXiv sources are small.
Files are content-hash deduped, shuffled with a fixed seed, every 10th file
held out; train capped to the matched target, val capped to VAL_CAP.
Output: data/pools/<lang>_{train,val}.bin (raw uint8) + pools_stats.json.
"""
import hashlib, json, os, random, sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "corpora")
OUT = os.path.join(BASE, "data", "pools")

CAP = 120_000_000
VAL_CAP = 3_000_000
SEED = 13

POOLS = {
    "lean": [("mathlib4", ["Mathlib"], [".lean"]),
             ("physlib", ["Physlib", "QuantumInfo"], [".lean"]),
             ("batteries", ["Batteries"], [".lean"]),
             ("lean4", ["src"], [".lean"])],
    "python": [("sympy", ["sympy"], [".py"]),
               ("qutip", ["qutip"], [".py"]),
               ("astropy", ["astropy"], [".py"]),
               ("plasmapy", ["src"], [".py"]),
               ("yt", ["yt"], [".py"]),
               ("scipy", ["scipy"], [".py"]),
               ("sunpy", ["sunpy"], [".py"]),
               ("pymatgen", ["src"], [".py"]),
               ("ase", ["ase"], [".py"])],
    "cpp": [("geant4", ["source"], [".cc", ".hh", ".icc"])],
    "latex": [("arxiv", ["old", "new"], [".tex"])],
}
EXCLUDE_DIRS = {"PhyslibAlpha", "test", "tests", "testing"}


def collect(lang):
    files, seen = [], set()
    for repo, dirs, exts in POOLS[lang]:
        for d in dirs:
            top = os.path.join(ROOT, repo, d)
            for dirpath, dirnames, names in os.walk(top):
                dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
                for n in sorted(names):
                    if not any(n.endswith(e) for e in exts):
                        continue
                    p = os.path.join(dirpath, n)
                    try:
                        b = open(p, "rb").read()
                        b.decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    if len(b) < 64:
                        continue
                    h = hashlib.sha1(b).digest()
                    if h in seen:
                        continue
                    seen.add(h)
                    files.append(b if b.endswith(b"\n") else b + b"\n")
    random.Random(SEED).shuffle(files)
    return files


def emit(lang, files, train_cap):
    os.makedirs(OUT, exist_ok=True)
    val = [b for i, b in enumerate(files) if i % 10 == 7]
    train = [b for i, b in enumerate(files) if i % 10 != 7]

    def write(blobs, cap, path):
        total = sum(len(b) for b in blobs)
        keep = blobs
        if total > cap:
            keep, s = [], 0
            step = max(1, round(total / cap))
            for k, b in enumerate(blobs):
                if k % step == 0 and s < cap:
                    keep.append(b)
                    s += len(b)
        with open(path, "wb") as f:
            for b in keep:
                f.write(b)
        return len(keep), sum(len(b) for b in keep)

    ntr, btr = write(train, train_cap, os.path.join(OUT, f"{lang}_train.bin"))
    nva, bva = write(val, VAL_CAP, os.path.join(OUT, f"{lang}_val.bin"))
    return dict(lang=lang, n_files=len(files),
                total_bytes=sum(len(b) for b in files),
                train_files=ntr, train_bytes=btr,
                val_files=nva, val_bytes=bva)


if __name__ == "__main__":
    pools = {lang: collect(lang) for lang in POOLS}
    for lang, fs in pools.items():
        print(f"{lang}: {len(fs)} files {sum(len(b) for b in fs)/1e6:.1f}MB",
              file=sys.stderr)
    matched = min(min(sum(len(b) for b in pools[l]) * 0.9 // 1
                      for l in ("lean", "python", "cpp")), CAP)
    matched = int(matched)
    print(f"matched train target: {matched/1e6:.1f}MB", file=sys.stderr)
    stats = dict(matched_train_bytes=matched, pools={})
    for lang, fs in pools.items():
        cap = matched if lang != "latex" else CAP
        stats["pools"][lang] = emit(lang, fs, cap)
        print(json.dumps(stats["pools"][lang]))
    with open(os.path.join(BASE, "data", "pools_stats.json"), "w") as f:
        json.dump(stats, f, indent=1)
