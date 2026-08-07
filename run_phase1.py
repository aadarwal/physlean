#!/usr/bin/env python3
"""Drive the Phase 1 grid: (model x stream) teacher-forced NLL cells.

Priorities (run order):
  P0  Qwen2.5-Coder ladder x {full_topo + its clean split} on all corpora
  P1  other families x {full_topo + their clean split}; StarCoder2
  P2  ablations on ABLATION_MODEL: full_shuffled and reset-per-doc
Each cell is a subprocess (survives MPS leaks); done cells are skipped via
the .meta.json marker, so this script is resumable and can be re-run as
models/streams appear.
"""
import json, os, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, ".venv", "bin", "python")
STREAMS = os.path.join(BASE, "data", "streams")
DUMPS = os.path.join(BASE, "nll_dumps")

FAMILIES = {  # model id -> (short, clean stream tag, priority)
    "Qwen/Qwen2.5-Coder-0.5B": ("q25c-0.5b", "c2024_11", 0),
    "Qwen/Qwen2.5-Coder-1.5B": ("q25c-1.5b", "c2024_11", 0),
    "Qwen/Qwen2.5-Coder-3B":   ("q25c-3b",   "c2024_11", 0),
    "Qwen/Qwen2.5-Coder-7B":   ("q25c-7b",   "c2024_11", 0),
    "Qwen/Qwen3-0.6B-Base":    ("q3-0.6b",   "c2025_04", 1),
    "Qwen/Qwen3-1.7B-Base":    ("q3-1.7b",   "c2025_04", 1),
    "Qwen/Qwen3-4B-Base":      ("q3-4b",     "c2025_04", 1),
    "Qwen/Qwen3.5-0.8B-Base":  ("q35-0.8b",  "c2026_02", 1),
    "Qwen/Qwen3.5-2B-Base":    ("q35-2b",    "c2026_02", 1),
    "Qwen/Qwen3.5-4B-Base":    ("q35-4b",    "c2026_02", 1),
    "bigcode/starcoder2-3b":   ("sc2-3b",    "c2024_11", 1),
}
ABLATION_MODEL = "Qwen/Qwen2.5-Coder-1.5B"
FULL_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4", "arxiv_old"]
CLEAN_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4", "arxiv_new"]
CTX = 32768
CHUNK = {0.5: 2048}  # default 2048; big models drop to 1024 via size guess


def jobs():
    out = []
    for mid, (short, ctag, prio) in FAMILIES.items():
        for c in FULL_CORPORA:
            out.append((prio, mid, short, c, "full_topo", []))
        for c in CLEAN_CORPORA:
            out.append((prio, mid, short, c, f"clean_{ctag}", []))
    short = FAMILIES[ABLATION_MODEL][0]
    for c in FULL_CORPORA:
        out.append((2, ABLATION_MODEL, short, c, "full_shuffled", []))
        out.append((2, ABLATION_MODEL, short, c, "full_topo",
                    ["--reset-per-doc"]))
    # stable order: priority, then model (keeps weights hot in page cache)
    out.sort(key=lambda j: (j[0], j[1]))
    return out


def cell_out(short, corpus, kind, flags):
    tag = kind + ("__perdoc" if "--reset-per-doc" in flags else "")
    return os.path.join(DUMPS, f"{short}__{corpus}__{tag}.csv.gz")


def main():
    only_prio = int(sys.argv[1]) if len(sys.argv) > 1 else None
    os.makedirs(DUMPS, exist_ok=True)
    from huggingface_hub import snapshot_download
    have = {}
    for mid in FAMILIES:
        try:
            snapshot_download(mid, local_files_only=True)
            have[mid] = True
        except Exception:
            have[mid] = False
            print(f"[defer] {mid} not in local HF cache yet", flush=True)
    todo = []
    for prio, mid, short, corpus, kind, flags in jobs():
        if only_prio is not None and prio != only_prio:
            continue
        if not have.get(mid):
            continue
        stream = os.path.join(STREAMS, corpus, f"{kind}.txt")
        out = cell_out(short, corpus, kind, flags)
        if not os.path.exists(stream):
            continue
        if os.path.exists(out + ".meta.json"):
            continue
        todo.append((prio, mid, short, corpus, kind, flags, stream, out))
    print(f"[plan] {len(todo)} cells to run", flush=True)
    for k, (prio, mid, short, corpus, kind, flags, stream, out) in enumerate(todo):
        big = any(s in mid for s in ("-7B", "-4B", "-9B"))
        cmd = [PY, os.path.join(BASE, "eval_incontext.py"),
               "--model", mid, "--stream", stream, "--out", out,
               "--ctx-tokens", str(CTX),
               "--chunk", "1024" if big else "2048", *flags]
        t0 = time.time()
        print(f"[{k+1}/{len(todo)}] P{prio} {short} {corpus} {kind} {flags}",
              flush=True)
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)
        tail = "\n".join(r.stderr.splitlines()[-2:])
        print(f"    -> exit={r.returncode} {time.time()-t0:.0f}s | {tail}",
              flush=True)
    print("[all done]", flush=True)


if __name__ == "__main__":
    main()
