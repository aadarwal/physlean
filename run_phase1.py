#!/usr/bin/env python3
"""Drive the Phase 1 grid: (model x stream) teacher-forced NLL cells.

Priorities:
  P0  Qwen2.5-Coder ladder x {full_topo + clean split} on all corpora
  P1  other small/mid families; StarCoder2; XL streams (small q25c only)
  P2  sentinel ablations on ABLATION_MODEL (= the battery-cached 0.5B):
      full_shuffled, reset-per-doc, window phases {8192,16384,24576},
      and the second-selection-seed full_topo_s2 streams
  P3  big rungs (14B/32B, DeepSeek-V2-Lite) — need >=40GB GPU; gated g3b
  P4  long-context arm (Qwen3.5 @ 131k) on physlib/mathlib
Submission is sentinel-first (PREREG G3a; 44 frozen sentinel cells,
152 small/mid incl. the sentinel — arXiv holds no core cells).
Each cell is a subprocess; finished cells are skipped via the .meta.json
marker, so the runner is resumable and shardable: several jobs can run
disjoint --models subsets concurrently.
"""
import argparse, json, math, os, subprocess, sys, time

from layout import PRODUCTION_CHUNK_TOKENS

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
    # Qwen3-32B-Base does not exist (HF 401 verified); ladder tops at 14B
    "Qwen/Qwen3.5-9B-Base":    ("q35-9b",    "c2026_02", 3, CTX_DEFAULT),
    "deepseek-ai/DeepSeek-Coder-V2-Lite-Base":
                               ("dsc2-lite", "c2024_11", 3, CTX_DEFAULT),
    # long-context arm: Qwen3.5 supports 262k positions
    "Qwen/Qwen3.5-2B-Base@131k": ("q35-2b-131k", "c2026_02", 4, 131072),
}
# the SENTINEL model (battery-cached) carries the ablations: the first
# science run is one cheap instrument-viability pass, not the full grid
ABLATION_MODEL = "Qwen/Qwen2.5-Coder-0.5B"
# arXiv is DEMOTED to an optional, separately-gated format diagnostic
# (PREREG §2/§13, decided before any outcomes): no arXiv cell exists in
# any core grid, and the optional corpus cannot affect code budgets.
FULL_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4"]
CLEAN_CORPORA = ["physlib", "mathlib", "qutip", "sympy", "geant4"]
LONGCTX_CORPORA = ["physlib", "mathlib"]


XL_MODELS = ["Qwen/Qwen2.5-Coder-0.5B", "Qwen/Qwen2.5-Coder-1.5B",
             "Qwen/Qwen2.5-Coder-3B"]  # window-count supplement (PREREG §6)
XL_CORPORA = ["physlib", "mathlib", "sympy", "geant4"]


def jobs():
    out = []
    for mid, (short, ctag, prio, ctx) in FAMILIES.items():
        full = LONGCTX_CORPORA if prio == 4 else FULL_CORPORA
        clean = LONGCTX_CORPORA if prio == 4 else CLEAN_CORPORA
        for c in full:
            out.append((prio, mid, short, c, "full_topo", ctx, []))
        for c in clean:
            out.append((prio, mid, short, c, f"clean_{ctag}", ctx, []))
        if mid in XL_MODELS:
            for c in XL_CORPORA:
                out.append((1, mid, short, c, "full_topo_xl", ctx, []))
    short = FAMILIES[ABLATION_MODEL][0]
    for c in FULL_CORPORA:
        out.append((2, ABLATION_MODEL, short, c, "full_shuffled",
                    CTX_DEFAULT, []))
        out.append((2, ABLATION_MODEL, short, c, "full_topo", CTX_DEFAULT,
                    ["--reset-per-doc"]))
        # window-phase ablation IN THE SENTINEL (review: content-position
        # confounding is the main threat to the phase-0 curve; paired
        # same-grp analysis across phases tests it BEFORE expansion)
        for ph in (8192, 16384, 24576):
            out.append((2, ABLATION_MODEL, short, c, "full_topo",
                        CTX_DEFAULT, ["--window-phase", str(ph)]))
        # sampling-sensitivity stream (second selection seed, same rule)
        out.append((2, ABLATION_MODEL, short, c, "full_topo_s2",
                    CTX_DEFAULT, []))
    out.sort(key=lambda j: (j[0], j[1]))
    return out


def phase_of(flags):
    return (int(flags[flags.index("--window-phase") + 1])
            if "--window-phase" in flags else 0)


def cell_out(short, corpus, kind, flags):
    tag = kind + ("__perdoc" if "--reset-per-doc" in flags else "")
    ph = phase_of(flags)
    if ph:
        tag += f"__ph{ph}"  # phase encoded in output identity
    return os.path.join(DUMPS, f"{short}__{corpus}__{tag}.csv.gz")


_HASH_CACHE = {}


def _stream_sha(path):
    import hashlib
    if path not in _HASH_CACHE:
        _HASH_CACHE[path] = hashlib.sha256(
            open(path, "rb").read()).hexdigest()
    return _HASH_CACHE[path]


def cell_done(out, mid, ctx, flags, stream, mj):
    """Done = meta exists AND dump is readable gzip with the v2 header AND
    the meta matches the CURRENT measurement schema version, model,
    revision, stream+manifest hashes, ctx and flags, AND the byte ledger
    held with positive scored rows/bytes, AND (schema v4) the recorded
    measurement-harness hash and environment fingerprint equal the
    CURRENT ones — a grid can never silently mix cells produced by
    different evaluator code or different software environments. Schema
    version (layout.py) bumps only on semantic measurement changes, so
    analysis/design-only commits do not invalidate dumps; GPU/driver are
    informational and deliberately NOT part of this identity."""
    from layout import MEASUREMENT_SCHEMA_VERSION
    from provenance import env_fingerprint, harness_hash
    mp = out + ".meta.json"
    if not os.path.exists(mp):
        return False
    try:
        import gzip, hashlib
        with gzip.open(out, "rt") as f:
            if f.readline().strip() != "win,doc,ctxb,blen,tok,nll,grp":
                return False
        m = json.load(open(mp))
        blob = open(out, "rb").read()  # body integrity, not just header
        if (m.get("dump_sha256") != hashlib.sha256(blob).hexdigest()
                or m.get("dump_file_bytes") != len(blob)):
            return False
        want_rev = (mj.get(mid.split("@")[0]) or {}).get("sha")
        man = stream.replace(".txt", ".manifest.jsonl")
        # full production identity: a smoke/dev dump (--max-bytes,
        # --random-init, phase != 0, dirty tree, absent manifest) must
        # never masquerade as a finished grid cell (review fix)
        return (m.get("schema_version") == MEASUREMENT_SCHEMA_VERSION
                and m.get("model") == mid.split("@")[0]
                and m.get("revision") == want_rev
                and m.get("random_init") is False
                and (m.get("max_bytes") or 0) == 0
                and m.get("window_phase") == phase_of(flags)
                and m.get("source_clean") is True
                and m.get("dtype") == "bfloat16"
                and m.get("device") == "cuda"
                # Incident 19902567 disproved chunk-shape invariance in
                # bf16. One frozen chunk is now part of cell identity;
                # otherwise a resumed ladder could silently mix numerical
                # paths across model sizes.
                and m.get("chunk") == PRODUCTION_CHUNK_TOKENS
                and m.get("ctx_tokens") == min(
                    ctx, m.get("max_position_embeddings") or ctx)
                and m.get("reset_per_doc") == ("--reset-per-doc" in flags)
                and m.get("stream_sha256") == _stream_sha(stream)
                and os.path.exists(man)
                and m.get("manifest_sha256") == _stream_sha(man)
                and m.get("byte_ledger_ok") is True
                and m.get("source_unchanged_during_eval") is True
                and m.get("harness_hash") == harness_hash()
                and m.get("env_fingerprint") == env_fingerprint()
                and (m.get("n_scored") or 0) > 0
                and (m.get("bytes_scored") or 0) > 0
                and isinstance(m.get("overall_bpb"), (int, float))
                and math.isfinite(m["overall_bpb"])
                and m["overall_bpb"] > 0
                and isinstance(m.get("per_token_nats"), (int, float))
                and math.isfinite(m["per_token_nats"]))
    except Exception:
        return False


def quarantine(out):
    """Preserve invalid artifacts (provenance rule: raw outputs are never
    destroyed) while unblocking a clean rerun."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    for p in (out, out + ".meta.json"):
        if os.path.exists(p):
            os.rename(p, f"{p}.quarantine-{ts}")


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
    mj = {}
    mj_path = os.path.join(BASE, "models.json")
    if os.path.exists(mj_path):
        mj = json.load(open(mj_path))
    have = {}
    for mid in {m.split("@")[0] for m in FAMILIES}:
        rev = (mj.get(mid) or {}).get("sha")
        try:
            # cached AT THE PINNED REVISION — any other cached revision
            # would pass here and then fail every cell (review fix)
            snapshot_download(mid, revision=rev, local_files_only=True)
            have[mid] = bool(rev)
        except Exception:
            have[mid] = False
    os.makedirs(DUMPS, exist_ok=True)
    # fail-closed shard accounting: EVERY expected cell in this shard's
    # scope is classified; silent omission is impossible (review fix)
    todo, done, miss_model, miss_stream, invalid = [], [], [], [], []
    for prio, mid, short, corpus, kind, ctx, flags in jobs():
        if args.prio is not None and prio != args.prio:
            continue
        if shard and short not in shard:
            continue
        cell = f"{short}__{corpus}__{kind}"
        stream = os.path.join(STREAMS, corpus, f"{kind}.txt")
        out = cell_out(short, corpus, kind, flags)
        if not have.get(mid.split("@")[0]):
            miss_model.append(cell)
        elif not os.path.exists(stream):
            miss_stream.append(cell)
        elif cell_done(out, mid, ctx, flags, stream, mj):
            done.append(cell)
        else:
            if os.path.exists(out + ".meta.json") or os.path.exists(out):
                # ANY leftover artifact (bare meta OR bare/truncated dump)
                # is invalid: eval would either skip-and-exit-0 on the meta
                # or OVERWRITE the dump, violating raw-artifact
                # preservation. Recorded here; quarantined AT EXECUTION so
                # a --dry plan stays read-only (review fixes)
                invalid.append(cell)
            todo.append((prio, mid, short, corpus, kind, ctx, flags,
                         stream, out))
    print(f"[plan] done={len(done)} runnable={len(todo)} "
          f"quarantined-invalid={len(invalid)} "
          f"missing-model={len(miss_model)} missing-stream={len(miss_stream)}",
          flush=True)
    for c in invalid:
        print(f"    QUARANTINED {c}", flush=True)
    for c in miss_model:
        print(f"    MISSING-MODEL {c}", flush=True)
    for c in miss_stream:
        print(f"    MISSING-STREAM {c}", flush=True)
    if args.dry:
        for t in todo:
            print("   ", t[2], t[3], t[4], t[5], t[6])
        return
    failed = []
    invalid_set = set(invalid)
    for k, (prio, mid, short, corpus, kind, ctx, flags, stream, out) in \
            enumerate(todo):
        if f"{short}__{corpus}__{kind}" in invalid_set:
            quarantine(out)
        cmd = [PY, os.path.join(BASE, "eval_incontext.py"),
               "--model", mid.split("@")[0], "--stream", stream, "--out", out,
               "--ctx-tokens", str(ctx),
               "--chunk", str(PRODUCTION_CHUNK_TOKENS), *flags]
        t0 = time.time()
        print(f"[{k+1}/{len(todo)}] P{prio} {short} {corpus} {kind} ctx={ctx}",
              flush=True)
        env = dict(os.environ)
        env["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, env=env)
        tail = "\n".join(r.stderr.splitlines()[-2:])
        # exit 0 is not enough: verify the produced artifact actually
        # satisfies cell_done (post-subprocess verification, review fix)
        ok = (r.returncode == 0
              and cell_done(out, mid, ctx, flags, stream, mj))
        print(f"    -> exit={r.returncode} verified={ok} "
              f"{time.time()-t0:.0f}s | {tail}", flush=True)
        if not ok:
            failed.append(f"{short}__{corpus}__{kind}")
    # fail-closed: a shard that ends with ANY gap must not look successful
    gaps = failed + miss_model + miss_stream
    if gaps:
        print(f"[GAP MANIFEST] {len(gaps)} unfinished cells "
              f"(failed={len(failed)} missing-model={len(miss_model)} "
              f"missing-stream={len(miss_stream)}):", flush=True)
        for f in gaps:
            print(f"    MISSING {f}", flush=True)
        sys.exit(1)
    print("[all done, no gaps]", flush=True)


if __name__ == "__main__":
    main()
