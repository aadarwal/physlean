#!/usr/bin/env python3
"""Teacher-forced per-token NLL vs in-context bytes for one (model, stream).

Measurement semantics (HANDOFF §4, pitfalls §5.3/§5.4):
  - The stream is tokenized once (no special tokens). Each token's UTF-8 byte
    length is recovered from offsets; we assert sum(token bytes) == stream
    bytes, so bits-per-byte has no tokenizer confound.
  - The token sequence is cut into consecutive windows of --ctx-tokens.
    A window boundary is a hard context reset: the first target in a window
    is predicted from ~1 byte of context (that is the c->1 end of the curve).
  - Within a window the model is fed in one frozen chunk shape with a growing
    KV cache. This implements teacher forcing under the CHECKPOINT'S attention
    semantics over the p-1 preceding tokens (sliding-window and hybrid
    mechanisms may not attend to all of them; PREREG §4). Finite-precision
    bf16 scores are not invariant to chunk shape, so chunk=2048 is part of
    measurement identity. Logits are log-softmaxed in fp32 row slices.
  - --reset-per-doc tokenizes and windows each document separately
    (single-file ablation: no cross-file context at all).

Output: gzipped CSV  win,doc,ctxb,blen,tok,nll,grp  (nll in nats; grp =
segment-global source-span group id; doc attributed per group over its
charged byte interval, -1 when straddling). Windows never split groups;
each window-opening group is excluded from scored BPB and fully
ledgered (scored + opening + phase-skipped == total, asserted). Meta
records the full production identity: schema_version, pinned revision,
stream/manifest/dump SHA256s, source tree hash + cleanliness, ledger
terms, window phase, and max_bytes.
"""
import argparse, gzip, json, math, os, subprocess, sys, time

import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

LOG = lambda *a: print(*a, file=sys.stderr, flush=True)


def load_model(name, dtype, device, random_init=False, revision=None,
               local_only=True):
    """Production loads are LOCAL-ONLY at the pinned revision: preflight
    guarantees the cache, so compute nodes need no network and remote
    metadata drift cannot leak in (dev/random-init may go remote)."""
    cfg = AutoConfig.from_pretrained(name, revision=revision,
                                     local_files_only=local_only)
    loaders = [AutoModelForCausalLM]
    try:  # multimodal "base" families (e.g. Qwen3.5) expose text under a VLM
        from transformers import AutoModelForImageTextToText
        loaders.append(AutoModelForImageTextToText)
    except ImportError:
        pass
    model = err = None
    for loader in loaders:
        try:
            if random_init:
                model = loader.from_config(cfg)
            else:
                try:
                    model = loader.from_pretrained(
                        name, dtype=dtype, revision=revision,
                        local_files_only=local_only)
                except TypeError:
                    model = loader.from_pretrained(
                        name, torch_dtype=dtype, revision=revision,
                        local_files_only=local_only)
            break
        except (ValueError, KeyError, OSError) as e:
            err = e
    if model is None:
        raise RuntimeError(f"no loader accepted {name}: {err}")
    model = model.to(device=device, dtype=dtype)
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    # Use the POST-LOAD config: transformers resolves its concrete attention
    # implementation during model construction (the pre-load AutoConfig can
    # still report None). Record it for every cell.
    mcfg = model.config
    tcfg = (mcfg.get_text_config() if hasattr(mcfg, "get_text_config")
            else mcfg)
    attn_impl = (getattr(mcfg, "_attn_implementation", None)
                 or getattr(tcfg, "_attn_implementation", None))
    ident = dict(model_class=type(model).__name__,
                 n_params=sum(p.numel() for p in model.parameters()),
                 attn_note=dict(
                     implementation=attn_impl,
                     model_type=getattr(tcfg, "model_type", None),
                     sliding_window=getattr(tcfg, "sliding_window", None),
                     layer_types=str(getattr(tcfg, "layer_types", None))[:200]))
    return model, tcfg, ident


from layout import (MEASUREMENT_SCHEMA_VERSION, PRODUCTION_CHUNK_TOKENS,
                    token_spans, windows_of, snap_phase)  # noqa: E402


def doc_lookup(manifest_path):
    spans = []
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path) as f:
            for line in f:
                d = json.loads(line)
                spans.append((d["start"], d["end"], d["doc_id"]))
    return spans


def doc_of(byte_pos, spans, hint=[0]):
    i = hint[0]
    while i < len(spans) and byte_pos >= spans[i][1]:
        i += 1
    hint[0] = min(i, len(spans) - 1) if spans else 0
    if i < len(spans) and spans[i][0] <= byte_pos < spans[i][1]:
        return spans[i][2]
    return -1


@torch.no_grad()
def eval_window(model, ids, device, chunk, slice_rows=256):
    """ids: LongTensor [T]. Returns nll[T-1] (fp32 cpu) for targets ids[1:]."""
    T = ids.shape[0]
    nlls = torch.empty(T - 1, dtype=torch.float32)
    past = None
    done = 0  # number of tokens already fed
    while done < T - 1:
        take = min(chunk, T - done)  # feed up to chunk tokens
        inp = ids[done:done + take].unsqueeze(0).to(device)
        out = model(input_ids=inp, past_key_values=past, use_cache=True)
        past = out.past_key_values
        logits = out.logits[0]  # [take, V]
        # rows predict targets ids[done+1 .. done+take]; last row of the
        # final chunk has no target inside the window
        n_t = min(take, (T - 1) - done)
        tgt = ids[done + 1: done + 1 + n_t].to(device)
        for a in range(0, n_t, slice_rows):
            b = min(a + slice_rows, n_t)
            lp = logits[a:b].float().log_softmax(-1)
            nlls[done + a: done + b] = (
                -lp.gather(1, tgt[a:b, None])[:, 0]).cpu()
        del logits, out
        done += take
    del past
    if device == "mps":
        torch.mps.empty_cache()
    # measurement invariant: NaN/inf or negative NLL must never enter the
    # ledger (review fix) — fail loudly, before anything is written
    if not (torch.isfinite(nlls).all() and (nlls >= 0).all()):
        bad = int((~torch.isfinite(nlls)).sum() + (nlls < 0).sum())
        raise AssertionError(f"non-finite/negative NLL in window: "
                             f"{bad} of {len(nlls)} values")
    return nlls




def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--stream", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctx-tokens", type=int, default=32768)
    ap.add_argument("--chunk", type=int, default=PRODUCTION_CHUNK_TOKENS)
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--device", default=None)
    ap.add_argument("--reset-per-doc", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=0)
    ap.add_argument("--random-init", action="store_true")
    ap.add_argument("--window-phase", type=int, default=0,
                    help="skip this many leading tokens before windowing "
                         "(content-position alignment ablation)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="dev smokes only; gate-quality dumps require a "
                         "clean source tree so source_tree_hash is honest")
    args = ap.parse_args()

    # A gate-quality cell must use the single ladder-wide numerical path.
    # Fail before model load rather than spend GPU time on an artifact that
    # cell_done is required to reject. Dev/random runs may probe alternatives.
    if (not (args.random_init or args.allow_dirty)
            and args.chunk != PRODUCTION_CHUNK_TOKENS):
        raise SystemExit(
            f"FATAL: production chunk must be {PRODUCTION_CHUNK_TOKENS}; "
            f"got {args.chunk} (use --allow-dirty only for dev probes)")

    from provenance import source_clean, source_tree_hash
    src_clean = source_clean()
    src_hash_start = source_tree_hash()  # captured BEFORE the long eval
    if not src_clean and not (args.allow_dirty or args.random_init):
        raise SystemExit("FATAL: source tree dirty outside results_v2 — "
                         "executed code would differ from the recorded "
                         "source_tree_hash (use --allow-dirty for dev only)")

    meta_path = args.out + ".meta.json"
    if os.path.exists(meta_path) and not args.force:
        LOG(f"[skip] {args.out} already complete")
        return
    if os.path.exists(args.out) and not args.force:
        # bare dump without meta: never overwrite raw artifacts silently
        raise SystemExit(f"FATAL: {args.out} exists without meta — refusing "
                         "to overwrite a raw artifact (--force quarantines)")
    if args.force:  # raw artifacts are never destroyed, even by --force
        ts = time.strftime("%Y%m%d-%H%M%S")
        for p in (args.out, meta_path):
            if os.path.exists(p):
                os.rename(p, f"{p}.quarantine-{ts}")
                LOG(f"[force] preserved {p} -> quarantine-{ts}")

    device = args.device or ("cuda" if torch.cuda.is_available() else
                             "mps" if torch.backends.mps.is_available() else
                             "cpu")
    dtype = getattr(torch, args.dtype)

    # environment identity gate (PREREG §4, schema v4): a PRODUCTION
    # eval refuses BEFORE loading the model unless the live environment
    # matches both the committed lock and the write-once freeze — never
    # burn GPU hours producing cells that cell_done must reject.
    # Production = non-dev, non-random (review fix: keying on
    # device=='cuda' would let a clean real CPU/MPS run bypass the
    # gate). Dev/random-init runs skip it but still record identities.
    # Hardware (GPU/driver) is recorded informationally and NEVER gated
    # (frozen decision: mixed L40S/H200 grids are by design; the
    # battery overlap item characterizes them).
    from provenance import (env_fingerprint, env_matches_freeze,
                            env_matches_lock, gpu_info, harness_hash)
    harness = harness_hash()
    env_fp_start = env_fingerprint()
    if not (args.random_init or args.allow_dirty):
        lock_ok, lock_probs = env_matches_lock()
        frz_ok, frz_detail = env_matches_freeze()
        if not (lock_ok and frz_ok):
            raise SystemExit(
                "FATAL: environment does not match the committed lock/"
                f"frozen record — lock: {lock_probs[:4] or 'ok'}; "
                f"freeze: {frz_detail} (fix_cluster syncs the lock; "
                "REFREEZE=1 adopts a new environment explicitly)")

    import hashlib as _hl
    _man = args.stream.replace(".txt", ".manifest.jsonl")
    stream_sha_start = _hl.sha256(
        open(args.stream, "rb").read()).hexdigest()
    man_sha_start = (_hl.sha256(open(_man, "rb").read()).hexdigest()
                     if os.path.exists(_man) else None)
    text = open(args.stream, encoding="utf-8").read()
    if args.max_bytes:
        text = text[:args.max_bytes]  # char-truncate; fine for smoke runs
    total_bytes = len(text.encode("utf-8"))
    spans = doc_lookup(args.stream.replace(".txt", ".manifest.jsonl"))

    mj_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "models.json")
    revision = None
    if os.path.exists(mj_path):
        revision = (json.load(open(mj_path)).get(args.model) or {}).get("sha")
    if revision is None and not args.random_init:
        raise SystemExit(f"FATAL: no pinned revision for {args.model} in "
                         "models.json (PREREG §4; never evaluate HF HEAD)")
    local_only = not (args.random_init or args.allow_dirty)
    tok = AutoTokenizer.from_pretrained(args.model, revision=revision,
                                        local_files_only=local_only)
    model, cfg, ident = load_model(args.model, dtype, device,
                                   args.random_init, revision=revision,
                                   local_only=local_only)
    max_pos = getattr(cfg, "max_position_embeddings", None) or 1 << 20
    ctx = min(args.ctx_tokens, max_pos)
    LOG(f"[cfg] vocab={cfg.vocab_size} max_pos={max_pos} "
        f"sliding_window={getattr(cfg, 'sliding_window', None)} ctx={ctx}")

    t0 = time.time()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n_rows = 0
    sum_nll = 0.0
    sum_bytes = 0
    tok_count = 0

    def tokenize(s):
        enc = tok(s, add_special_tokens=False, return_offsets_mapping=True)
        lens, grps = token_spans(s, enc["offset_mapping"])
        nb = len(s.encode("utf-8"))
        assert sum(lens) == nb, f"byte accounting broke: {sum(lens)} != {nb}"
        return enc["input_ids"], lens, grps

    segments = []  # (ids, byte_lens, grp_ids, seg_start_byte)
    if args.reset_per_doc and spans:
        by = text.encode("utf-8")
        for s, e, did in spans:
            if s >= total_bytes:
                break
            seg = by[s:min(e, total_bytes)].decode("utf-8", errors="ignore")
            segments.append((*tokenize(seg), s))
    else:
        ids0, lens0, grps0 = tokenize(text)
        ph = snap_phase(grps0, args.window_phase)  # never start mid-group
        phase_skipped_bytes = sum(lens0[:ph])
        segments.append((ids0[ph:], lens0[ph:], grps0[ph:],
                         phase_skipped_bytes))

    with gzip.open(args.out, "wt") as fout:
        # schema v2: grp = SEGMENT-GLOBAL source-span group id (from offset
        # intervals; layout.token_spans) — rows sharing grp form one unicode
        # source-span group and analysis aggregates NLL/bytes per (win, grp)
        fout.write("win,doc,ctxb,blen,tok,nll,grp\n")
        win_id = 0
        bdrop_rows = 0        # scored follower rows of opening groups
        bdrop_nll = 0.0       # ... their NLL (dropped from BPB, recorded)
        opening_bytes = 0     # ALL bytes of every window's opening group
                              # (incl. the never-scored opener at ws)
        seg_bytes_total = 0
        for ids_list, blens, grps, seg_start in segments:
            seg_bytes_total += sum(blens)
            ids = torch.tensor(ids_list, dtype=torch.long)
            cum = [0]
            for L in blens:
                cum.append(cum[-1] + L)  # cum[i] = bytes of tokens[:i]
            # min_tail=1: every nonempty window is kept — tails are short
            # context episodes, not discards (PREREG: nothing dropped)
            for ws, we in windows_of(len(ids), ctx, grps, min_tail=1):
                nll = eval_window(model, ids[ws:we], device, args.chunk)
                rows = []
                open_grp = grps[ws]  # its opener is the unscored ctx token:
                opening_bytes += sum(blens[p] for p in range(ws, we)
                                     if grps[p] == open_grp)
                gbytes = {}          # scored followers are dropped, counted
                # per-GROUP doc attribution over the group's charged byte
                # interval: a single token/group whose span straddles a
                # file boundary gets doc=-1, uniformly for all its rows
                # (row-start lookup alone cannot see the straddle)
                gdoc = {}
                p = ws
                while p < we:
                    g = grps[p]
                    q = p
                    while q + 1 < we and grps[q + 1] == g:
                        q += 1
                    d0 = doc_of(seg_start + cum[p], spans)
                    d1 = doc_of(seg_start + max(cum[q + 1] - 1, cum[p]),
                                spans)
                    gdoc[g] = d0 if d0 == d1 else -1
                    p = q + 1
                for j in range(len(nll)):  # target index = ws + 1 + j
                    p = ws + 1 + j
                    if grps[p] == open_grp:
                        bdrop_rows += 1
                        bdrop_nll += float(nll[j])
                        continue
                    ctxb = cum[p] - cum[ws]
                    blen = blens[p]
                    gbytes[grps[p]] = gbytes.get(grps[p], 0) + blen
                    rows.append(f"{win_id},{gdoc[grps[p]]},{ctxb},{blen},"
                                f"{ids_list[p]},{nll[j].item():.5f},"
                                f"{grps[p]}")
                    sum_nll += float(nll[j])
                    sum_bytes += blen
                # conservation invariant: no retained group without bytes
                assert all(v > 0 for v in gbytes.values()), \
                    f"zero-byte source-span group in window {win_id}"
                fout.write("\n".join(rows) + "\n")
                n_rows += len(rows)
                tok_count += we - ws
                dt = time.time() - t0
                LOG(f"[win {win_id}] {we - ws} tok | total {tok_count} tok "
                    f"in {dt:.0f}s = {tok_count / dt:.0f} tok/s | "
                    f"bpb so far {sum_nll / math.log(2) / max(sum_bytes, 1):.4f}")
                win_id += 1

    # full byte ledger (PREREG §4, literal): scored + opening-group +
    # phase-skipped == TOTAL stream bytes; per-doc mode additionally
    # asserts segment coverage equals the total (decode-clip cannot
    # silently drop bytes)
    phase_skip = segments[0][3] if (segments and not args.reset_per_doc) else 0
    if args.reset_per_doc:
        ledger_ok = (sum_bytes + opening_bytes == seg_bytes_total
                     and seg_bytes_total == total_bytes)
    else:
        ledger_ok = sum_bytes + opening_bytes + phase_skip == total_bytes
    assert ledger_ok, (f"byte ledger broke: scored {sum_bytes} + opening "
                       f"{opening_bytes} + phase {phase_skip} != total "
                       f"{total_bytes} (segments {seg_bytes_total})")
    bpb = sum_nll / math.log(2) / max(sum_bytes, 1)
    per_tok_nats = sum_nll / max(n_rows, 1)
    assert math.isfinite(bpb) and bpb > 0 and math.isfinite(per_tok_nats), \
        f"non-finite summary: bpb={bpb} nats/tok={per_tok_nats}"

    import hashlib
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    man_path = args.stream.replace(".txt", ".manifest.jsonl")
    # dump self-integrity (review: a gzip header is not a body): hash the
    # CLOSED dump file so truncation/repacking is detectable forever
    dump_blob = open(args.out, "rb").read()
    meta = dict(schema_version=MEASUREMENT_SCHEMA_VERSION,
                model=args.model, revision=revision, stream=args.stream,
                ctx_tokens=ctx, chunk=args.chunk, dtype=args.dtype,
                device=device, random_init=args.random_init,
                reset_per_doc=args.reset_per_doc,
                window_phase=args.window_phase, max_bytes=args.max_bytes,
                boundary_dropped_rows=bdrop_rows,
                boundary_dropped_nll_nats=bdrop_nll,
                opening_group_bytes=opening_bytes,
                phase_skipped_bytes=phase_skip,
                byte_ledger_ok=ledger_ok,
                total_bytes=total_bytes, n_tokens=tok_count, n_scored=n_rows,
                bytes_scored=sum_bytes,
                bytes_per_token=total_bytes / max(tok_count, 1),
                overall_bpb=bpb, per_token_nats=per_tok_nats,
                vocab_size=cfg.vocab_size,
                sliding_window=getattr(cfg, "sliding_window", None),
                max_position_embeddings=max_pos,
                **ident,
                dump_sha256=hashlib.sha256(dump_blob).hexdigest(),
                dump_file_bytes=len(dump_blob),
                # hashes captured BEFORE reading (race fix): meta always
                # describes what the model actually evaluated
                stream_sha256=stream_sha_start,
                manifest_sha256=man_sha_start,
                torch_version=torch.__version__,
                transformers_version=__import__(
                    "transformers").__version__,
                cuda_build=getattr(torch.version, "cuda", None),
                harness_hash=harness,
                env_fingerprint=env_fp_start,
                **gpu_info(),  # gpu_name + gpu_driver: informational only
                harness_commit=subprocess.run(
                    ["git", "-C", repo_dir, "rev-parse", "HEAD"],
                    capture_output=True, text=True).stdout.strip() or None,
                source_clean=src_clean,
                source_tree_hash=src_hash_start,
                source_unchanged_during_eval=True,  # asserted just below
                wall_s=time.time() - t0)
    stream_now = hashlib.sha256(open(args.stream, "rb").read()).hexdigest()
    man_now = (hashlib.sha256(open(man_path, "rb").read()).hexdigest()
               if os.path.exists(man_path) else None)
    if stream_now != stream_sha_start or man_now != man_sha_start:
        raise SystemExit("FATAL: stream/manifest changed DURING eval — "
                         "meta withheld; runner will quarantine")
    if (source_tree_hash() != src_hash_start
            or source_clean() != src_clean) and not (
            args.allow_dirty or args.random_init):
        raise SystemExit("FATAL: source tree changed DURING eval — meta "
                         "not written; artifact will be quarantined by "
                         "the runner")
    if env_fingerprint() != env_fp_start:
        raise SystemExit("FATAL: environment changed DURING eval — meta "
                         "not written; artifact will be quarantined by "
                         "the runner")
    if harness_hash() != harness:
        raise SystemExit("FATAL: measurement harness changed DURING eval "
                         "— meta not written; artifact will be "
                         "quarantined by the runner")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=1)
    LOG(f"[done] bpb={bpb:.4f} per-token={per_tok_nats:.3f} nats "
        f"({n_rows} scored, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
