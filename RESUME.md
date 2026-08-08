> **HISTORICAL DOCUMENT (2026-08-07).** Superseded by `PREREG.md` (estimand,
> gates, analysis plan) and `DESIGN_V2.md`. Kept verbatim for provenance;
> claims herein about beta/"predictability"/ETAs/watcher automation are NOT
> the campaign's current position. Where this conflicts with PREREG, PREREG wins.

# RESUME — paused 2026-08-07 pending bigger compute

Campaign paused deliberately (user is provisioning better compute). Everything
is validated and resumable; nothing scientific has been measured yet beyond
smoke-scale sanity checks. Read HANDOFF.md first for the science; this file is
only the operational state.

## State at pause

- **Code** (all committed, validated end-to-end on M5/MPS):
  - `prep_streams.py` — Phase 1 streams: 2×2 grid + geant4 (C++) + arXiv LaTeX
    (old=2023H1 contaminated / new=2026-05+ clean), topo/shuffled/clean-per-cutoff
    variants, byte-budget-matched; [superseded post-pause: arXiv is now an
    OPTIONAL self-budgeted format diagnostic outside all core budgets —
    PREREG §2/§13]; contamination dates = rename-aware one-pass
    git walk + per-file `--follow` re-check for candidate-clean files (the
    one-pass date alone can be ~5 weeks late — observed on physlib).
  - `eval_incontext.py` — teacher-forced NLL vs in-context bytes; KV-chunked,
    fp32 log-softmax in row slices, byte-exact accounting (asserted); window
    reset = context reset; `--reset-per-doc` = single-file ablation. Handles
    multimodal-wrapped text models (Qwen3.5 → `get_text_config()` +
    ImageTextToText loader fallback). Device auto: cuda > mps > cpu.
  - `run_phase1.py` — resumable grid runner (skips missing models/streams and
    finished cells). Priorities: P0 Qwen2.5-Coder ladder, P1 other families,
    P2 ablations (shuffled + per-doc on the 1.5B).
  - `analyze_v2.py` — fits BPB(c)=A·c^(−β)+L∞; mean + median estimands;
    window- AND doc-level bootstrap CIs; ×1.5-shifted bin-edge stability.
  - `train_scratch.py` (Phase 2) — byte-level GPT (1 token = 1 byte, vocab 256),
    sizes 10m/30m/100m, grad-checkpointing (needed on MPS; optional on CUDA),
    final val writes per-position NLL in the same CSV schema so analyze_v2
    fits the from-scratch context curves too. `prep_pools.py` — matched
    per-language pools (lean/python/cpp matched; latex unmatched reference).
- **Corpora**: 15 full-history clones under `corpora/` (~7GB; full history is
  REQUIRED for contamination dating) + 265 arXiv LaTeX papers pinned in
  `arxiv_manifest.json` (re-fetch exactly: `python arxiv_fetch.py --from-manifest`).
- **Models**: all 11 in the local HF cache (~54GB): Qwen2.5-Coder {0.5,1.5,3,7}B,
  Qwen3 {0.6,1.7,4}B-Base, Qwen3.5 {0.8,2,4}B-Base, starcoder2-3b.
  `models.json` records HF createdAt (= conservative contamination cutoffs).
- **Not done**: `data/streams/` (prep_streams was killed mid-run — rerun it,
  ~10 min), pools need a rebuild after the python-pool widening (rerun
  `prep_pools.py`), zero Phase 1 grid cells run, zero Phase 2 runs.

## Validated anchors (reproduce on the new box before trusting anything)

- Tokenizer byte accounting: token-byte sum == UTF-8 byte count and decode
  round-trip on Lean+C++/LaTeX mix — passes for all 7 tokenizer families.
- Random-init Qwen2.5-Coder-0.5B: 12.10 nats/token vs ln(151936)=11.93, flat
  across context bins (no leakage; c.f. pilot's PPL≈vocab-size anchor).
- Real 0.5B on mathlib: monotone BPB 3.12 → 0.57 over ctx 1B → 16KB.
- Byte-GPT: init exactly 8.0 bpb; val 8.0 → 4.59 on 3MB Lean smoke.

## Resume on the new machine

```
git clone <this repo> && cd physlean
uv venv --python 3.12 .venv && uv pip install -p .venv torch transformers \
  accelerate tokenizers safetensors "huggingface_hub[hf_transfer]" numpy scipy pandas matplotlib
./fetch_corpora.sh                       # ~7GB, full history
.venv/bin/python arxiv_fetch.py --from-manifest   # pinned corpus, ~15 min
bash models_download.sh                  # or rsync ~/.cache/huggingface/hub
.venv/bin/python prep_streams.py         # streams + contamination splits
.venv/bin/python prep_pools.py           # Phase 2 pools
.venv/bin/python run_phase1.py           # the grid (resumable, re-run anytime)
.venv/bin/python analyze_v2.py           # fits, incremental
# Phase 2 (after Phase 1): train_scratch.py --lang {lean,python,cpp,latex} --size {10m,30m,100m} --seed N
```

## Scale-out once on ≥40GB CUDA

- Add to `run_phase1.FAMILIES`: Qwen2.5-Coder-{14B,32B} (cutoff c2024_11),
  Qwen3-{8B,14B,32B}-Base (c2025_04), Qwen3.5-9B-Base (c2026_02),
  DeepSeek-Coder-V2-Lite (own cutoff 2024-06), Leanstral-2603 / Leanstral-1.5
  (119B-A6B MoE, Lean-specialist anchor; needs ~80GB+ or multi-GPU).
- Long-context arm: Qwen3.5 supports 262k positions — add a 131k-ctx pass on
  physlib/mathlib (needs streams > 400KB/window: raise CAP for a long-ctx
  stream variant).
- Phase 2 full plan: add 300m to `SIZES`, D up to full matched pools, ≥3 seeds,
  and a proper L(N,D) grid; micro-batches can go way up on CUDA; grad-ckpt off.
- Local M5 throughputs for reference: eval ~3.3k tok/s (0.5B @4k ctx);
  train ~2.4k tok/s (10m, ckpt on). CUDA should be 20–100× these.

## Watch out for (already hit once)

- llama.cpp is dead weight here — everything is PyTorch now (HANDOFF §5.1).
- Background shells don't inherit the repo cwd — use absolute paths in logs.
- `head`/`grep` in monitoring pipes need line buffering or you fly blind.
- MPS-only: SDPA training backward materializes attention (OOM at mb≥16
  without grad-ckpt). CUDA flash paths don't have this.
- Contamination caveat for the writeup: git add-dates bound *this repo's*
  publication, not content that was public elsewhere first (vendored/ported
  files); flag suspicious clean files (commit messages containing
  import/port/vendor) before claiming cleanliness.
