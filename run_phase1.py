#!/usr/bin/env python3
"""Drive the Phase 1 grid: (model x stream) teacher-forced NLL cells.

Priorities:
  P0  Qwen2.5-Coder ladder x {full_topo + clean split} on all corpora
  P1  other small/mid families; StarCoder2
  P2  ablations on ABLATION_MODEL: full_shuffled and reset-per-doc
  P3  big rungs (14B/32B, DeepSeek-V2-Lite) — need >=40GB GPU
  P4  long-context arm (Qwen3.5 @ 131k) on physlib/mathlib
Each cell is a subprocess; finished cells are skipped via the .meta.json
marker, so the runner is resumable and shardable: several jobs can run
disjoint --models subsets concurrently.
"""
import argparse, json, os, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, ".venv", "bin", "python")
STREAMS = os.path.join(BASE, "data", "streams")
DUMPS = os.path.join(BASE, "nll_dumps")
CTX_DEFAULT = 32768

# model id -> (short, clean stream tag, priority, ctx)
FAMILIES = {
    "Qwen/Qwen2.5-Coder-0.5B": ("q25c-0.5b", "c2024_11", 0, CTX_DEFAULT),
    "Qwen/Qwen2.5-Coder-1.5B": ("q25c-1.5b", "c2024_11", 0, CTX_DEFAULT),
    "Qwen/Qwen2.5-Coder-3B":   ("q25c-3b",   "c2024_11", 0, CTX_DEFAULT),
    "Qwen/Qwen2.5-Coder-7B":   ("q25c-7b",   "c2024_11", 0, CTX_DEFAULT),
    "Qwen/Qwen3-0.6B-Base":    ("q3-0.6b",   "c2025_04", 1, CTX_DEFAULT),
    "Qwen/Qwen3-1.7B-Base":    ("q3-1.7b",   "c2025_04", 1, CTX_DEFAULT),
    "Qwen/Qwen3-4B-Base":      ("q3-4b",     "c2025_04", 1, CTX_DEFAULT),
    "Qwen/Qwen3.5-0.8B-Base":  ("q35-0.8b",  "c2026_02", 1, CTX_DEFAULT),
    "Qwen/Qwen3.5-2B-Base":    ("q35-2b",    "c2026_02", 1, CTX_DEFAULT),
    "Qwen/Qwen3.5-4B-Base":    ("q35-4b",    "c2026_02", 1, CTX_DEFAULT),
    "bigcode/starcoder2-3b":   ("sc2-3b",    "c2024_11", 1, CTX_DEFAULT),
    # big rungs (>=40GB): extend the scale ladder
    "Qwen/Qwen2.5-Coder-14B":  ("q25c-14b",  "c2024_11", 3, CTX_DEFAULT),
    "Qwen/Qwen2.5-Coder-32B":  ("q25c-32b",  "c2024_11", 3, CTX_DEFAULT),
    "Qwen/Qwen3-8B-Base":      ("q3-8b",     "c2025_04", 3, CTX_DEFAULT),
    "Qwen/Qwen3-14B-Base":     ("q3-14b",    "c2025_04", 3, CTX_DEFAULT),
    "Qwen/Qwen3-32B-Base":     ("q3-32b",    "c2025_04", 3, CTX_DEFAULT),
    "Qwen/Qwen3.5-9B-Base":    ("q35-9b",    "c2026_02", 3, CTX_DEFAULT),
    "deepseek-ai/DeepSeek-Coder-V2-Lite-Base":
                               ("dsc2-lite", "c2024_11", 3, CTX_DEFAULT),
    # long-context arm: Qwen3.5 supports 262k positions
    "Qwen/Qwen3.5-2B-Base@131k": ("q35-2b-131k", "c2026_02", 4, 131072),
}
ABLATION_MODEL = "Qwen/Qwen2.5-Coder-1.5B"
FULL_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4", "arxiv_old"]
CLEAN_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4", "arxiv_new"]
LONGCTX_CORPORA = ["physlib", "mathlib"]


def jobs():
    out = []
    for mid, (short, ctag, prio, ctx) in FAMILIES.items():
        full = LONGCTX_CORPORA if prio == 4 else FULL_CORPORA
        clean = LONGCTX_CORPORA if prio == 4 else CLEAN_CORPORA
        for c in full:
            out.append((prio, mid, short, c, "full_topo", ctx, []))
        for c in clean:
            out.append((prio, mid, short, c, f"clean_{ctag}", ctx, []))
    short = FAMILIES[ABLATION_MODEL][0]
    for c in FULL_CORPORA:
        out.append((2, ABLATION_MODEL, short, c, "full_shuffled",
                    CTX_DEFAULT, []))
        out.append((2, ABLATION_MODEL, short, c, "full_topo", CTX_DEFAULT,
                    ["--reset-per-doc"]))
    out.sort(key=lambda j: (j[0], j[1]))
    return out


def cell_out(short, corpus, kind, flags):
    tag = kind + ("__perdoc" if "--reset-per-doc" in flags else "")
    return os.path.join(DUMPS, f"{short}__{corpus}__{tag}.csv.gz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prio", type=int, default=None,
                    help="run only this priority tier")
    ap.add_argument("--models", default="",
                    help="comma-separated shorts to run (shard filter)")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    shard = {s.strip() for s in args.models.split(",") if s.strip()}

    from huggingface_hub import snapshot_download
    have = {}
    for mid in {m.split("@")[0] for m in FAMILIES}:
        try:
            snapshot_download(mid, local_files_only=True)
            have[mid] = True
        except Exception:
            have[mid] = False
    os.makedirs(DUMPS, exist_ok=True)
    todo = []
    for prio, mid, short, corpus, kind, ctx, flags in jobs():
        if args.prio is not None and prio != args.prio:
            continue
        if shard and short not in shard:
            continue
        if not have.get(mid.split("@")[0]):
            continue
        stream = os.path.join(STREAMS, corpus, f"{kind}.txt")
        out = cell_out(short, corpus, kind, flags)
        if not os.path.exists(stream):
            continue
        if os.path.exists(out + ".meta.json"):
            continue
        todo.append((prio, mid, short, corpus, kind, ctx, flags, stream, out))
    missing = [m for m, ok in have.items() if not ok]
    if missing:
        print(f"[defer] not in HF cache: {', '.join(sorted(missing))}",
              flush=True)
    print(f"[plan] {len(todo)} cells to run", flush=True)
    if args.dry:
        for t in todo:
            print("   ", t[2], t[3], t[4], t[5], t[6])
        return
    for k, (prio, mid, short, corpus, kind, ctx, flags, stream, out) in \
            enumerate(todo):
        big = any(s in mid for s in ("-7B", "-8B", "-9B", "-14B", "-32B",
                                     "V2-Lite"))
        cmd = [PY, os.path.join(BASE, "eval_incontext.py"),
               "--model", mid.split("@")[0], "--stream", stream, "--out", out,
               "--ctx-tokens", str(ctx),
               "--chunk", "1024" if big else "2048", *flags]
        t0 = time.time()
        print(f"[{k+1}/{len(todo)}] P{prio} {short} {corpus} {kind} ctx={ctx}",
              flush=True)
        env = dict(os.environ)
        env["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, env=env)
        tail = "\n".join(r.stderr.splitlines()[-2:])
        print(f"    -> exit={r.returncode} {time.time()-t0:.0f}s | {tail}",
              flush=True)
    print("[all done]", flush=True)


if __name__ == "__main__":
    main()
