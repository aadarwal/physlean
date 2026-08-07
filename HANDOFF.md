# HANDOFF — physlean: full-scale execution brief for a local agent

You are inheriting a research project mid-flight. Read this whole file before
touching anything. It contains the scientific context, what has already been
built and validated (and the exact numbers to check yourself against), the
pitfalls already hit, and a prioritized experiment plan sized for real compute.
The prior work was done in a locked-down cloud sandbox (2 CPU cores, no PyPI,
no Hugging Face, GitHub-only network); you presumably have GPUs and an open
network, which changes the optimal toolchain — guidance below assumes that.

## 1. The mission

Gwern's essay ["Lean Software Scaling Laws"](https://gwern.net/lean-scaling)
(published late June 2026) proposes an experiment nobody has run: empirically
measure the scaling of coding-LLM perplexity as a function of codebase size in
context, to estimate "predictability" scaling laws by programming language —
with Lean 4 (formal, dependently-typed, machine-checked) as the star test case.
The hypothesis: formal languages are asymptotically more predictable, which has
implications for software security/safety and for which languages win in the AI
era.

An exhaustive literature/web sweep (2026-08-07) found **zero public
implementations, zero citations of the essay in any paper, repo, or dataset**.
The reception was tiny (two comment-less HN threads; one Lean FRO developer,
Kim Morrison, cites the essay's framing in
[kim-em/lean-zip PR #2720](https://github.com/kim-em/lean-zip/pull/2720), but
runs no measurement). This is greenfield. First careful results here are
publishable (blog post at minimum, workshop/arXiv note plausibly).

### Closest prior art — read these before designing anything

- **arXiv:2512.13472** "Scaling Laws for Code: Every Programming Language
  Matters" (Dec 2025, ACL Findings 2026). The single closest work: 420+
  from-scratch pretraining runs, per-language Chinchilla exponents and
  irreducible loss L∞ for 7 mainstream languages; "intrinsic predictability"
  ordering C# < Java ≈ Rust < Go < TypeScript < JavaScript < Python (stricter
  → more predictable, directionally supporting the essay). **No Lean, no formal
  language, and the scaling axis is model/data size — not context/codebase
  size.** Your Phase 2 extends this table with Lean; compare orderings.
- **arXiv:2510.08702** "Scaling Laws for Code: A More Data-Hungry Regime" (Oct
  2025): code-vs-NL scaling laws, language-aggregate.
- **arXiv:2512.24969** "LLMs and the entropy of English" (Princeton, Dec 2025):
  the methodological template for entropy-vs-context-length measurement — but
  English only. Your Phase 1 is essentially this, on code, per language.
- **arXiv:2510.13697** (JetBrains, ICLR 2025): completion accuracy vs
  repository-context size (1K→131K tokens), Python only, accuracy not loss.
- **arXiv:2309.16039** (Llama 2 Long): fits L(c) = (α/c)^β + γ loss-vs-context
  power laws — the functional form — on mixed NL.
- Lean-side scaling work (AlphaProof, DeepSeek-Prover, Kimina, Seed-Prover,
  Goedel-Prover, Pythagoras-Prover 2026) is all proof-success-vs-compute;
  none measures next-token predictability of Lean text. **No paper at any date
  measures LLM loss on Lean/mathlib vs mainstream code vs prose.** That empty
  cell is the cheapest novel result available.
- Caveat literature for the metric itself: arXiv:2608.00624 and
  arXiv:2601.22950 (perplexity's failure modes) — worth citing when writing up.

## 2. What already exists in this repo

The harness was built for the sandbox's constraints, but its design decisions
are portable and several are worth keeping in any variant:

- `prep_corpora.py` — 2×2 corpus grid {physics, math} × {Lean 4, Python}:
  Physlib (ex-PhysLean, physics-in-Lean; PhyslibAlpha excluded — it is
  lower-review AI-generated content), mathlib4, QuTiP, SymPy. Files
  topologically ordered by intra-repo imports (dependencies first), every 10th
  file held out, byte budgets matched across corpora (QuTiP, ~2.7MB total, is
  the binding constraint at this corpus choice).
- `gen_model.py` — random-init tiny llama-arch GGUFs with a **byte-level vocab
  (256 byte tokens + eot, no merges): 1 token = 1 byte exactly**, so NLL/ln2 is
  bits-per-byte with zero tokenizer confound across languages. (Uses
  tokenizer_pre "kimi-k2" purely because that path tolerates absent merges.)
- `patches/llamacpp-b6000-lean-scaling.patch` — three llama.cpp changes:
  optional BPE merges; env-configurable CPU training (FT_LR/FT_EPOCHS/FT_VAL/
  FT_STRIDE/FT_OUT); `PPL_FIRST` + `PPL_DUMP` in llama-perplexity to dump
  per-position NLL (`chunk,ctx_len,token_id,nll`) — the core measurement.
  **llama.cpp is pinned to tag b6000: current master's training path asserts in
  ggml_build_backward_expand.** On GPU you should use PyTorch instead and only
  keep the *measurement semantics*.
- `analyze.py` — bins per-position BPB into log-spaced context buckets, fits
  BPB(c) = A·c^(−β) + L∞, chunk-level bootstrap for 95% CIs.
- `results/` — outputs of the CPU pilot (see §3): per-corpus per-position NLL
  CSVs, results.json with fitted (A, β, L∞), plots, report.

Validated anchors you should reproduce before trusting any change: byte-exact
tokenization (token count == byte count on any file); random-init model scores
PPL ≈ 257 (= vocab size; measured 259–261) flat across positions; lr sweep on a
200KB smoke corpus with the 3.5M model gave heldout PPL 113 @ lr 3e-4 (learns),
148 @ 1e-4, 281 @ 3e-5 (≈ random), diverged @ 1e-3. Sandbox throughput was
~1.6k tok/s train, ~7.5k tok/s eval (2 CPU cores, f32).

## 3. The CPU pilot (what the results/ directory is)

From-scratch training was chosen over evaluating a pretrained model because the
sandbox couldn't download weights — but it turned out to have independent
scientific value, so keep it as a permanent arm: it eliminates **training-set
contamination** (pretrained models have memorized these exact repos from
GitHub) and **unequal exposure** (frontier pretraining has ~1000× more Python
than Lean). The pilot trains one ~3.5M-param byte-LM per corpus with matched
byte budgets (ctx 2048, AdamW, lr 3e-4 swept, f32) and measures held-out BPB vs
in-context bytes c ∈ [1, 2048), fit to A·c^(−β) + L∞.

Treat pilot numbers as existence proof + baseline, not as truth about frontier
models: 3.5M params, ≤1MB/corpus, single seed, no shuffling (topo-order
curriculum), 2KB context ceiling. All four limitations are yours to remove.

## 4. Prioritized experiment plan for real compute

### Phase 1 — the essay's literal experiment (pretrained, in-context axis)

This is the highest-value, lowest-cost phase; a single 24–80GB GPU does it in
hours.

Corpora: the 2×2 grid, plus (strongly recommended) same-domain extensions that
isolate variables the pilot couldn't: physics in C++ (e.g. Geant4, LAMMPS),
physics in Fortran if you want reach, and **informal physics prose** (arXiv
physics LaTeX sources) so you get formal-Lean vs informal-LaTeX physics — the
cleanest formality contrast available. Keep the topo/dependency-ordered stream
construction; add ablations: shuffled file order, and single-file-only streams.
Whether cross-file context helps *more* in Lean (imports, theorem reuse) than
in Python is itself a novel finding either way.

Models (base/pretrain checkpoints, not instruct, where possible):
Qwen2.5-Coder-{0.5B,1.5B,7B,14B,32B}, a Qwen3 size ladder, StarCoder2,
DeepSeek-Coder-V2-Lite; add Leanstral-1.5 (Mistral's Apache-2.0 Lean 4 agent
MoE, July 2026) as the Lean-specialist anchor. The *ladder* matters more than
any single model: the headline plot is **β (and L∞) per language as a function
of model scale** — does the formal-language predictability advantage grow,
shrink, or hold with scale? Nobody has that plot.

Measurement: teacher-forced per-token NLL over streams at large context (32k;
131k where supported), bucketed by preceding in-context bytes.
**Normalize to bits-per-byte** — sum NLL over tokens ÷ sum UTF-8 bytes of those
tokens — never compare per-token perplexity across languages/tokenizers.
Fit A·c^(−β) + L∞ per (model, corpus); bootstrap CIs by document; report
median-based fits alongside means (code NLL is heavy-tailed).

Contamination control (this decides credibility): for each model, build a
held-out split from files whose git add-date postdates the model's training
cutoff (`git log --diff-filter=A --format=%aI -- <file>`; beware renames —
`--follow` — Physlib was renamed twice: HepLean → PhysLean → Physlib, so
recent paths can carry old content). Report clean-split and full-split numbers
separately; expect them to differ and say so.

### Phase 2 — per-language training scaling laws, with Lean in the table

Extends arXiv:2512.13472 into the cell it skipped. Train byte-level (or
fixed-vocab) transformer families from scratch — e.g. 10M/30M/100M/300M — per
language with matched budgets, multiple seeds, proper shuffling; fit L(N, D)
per language → α_N, α_D, L∞ including Lean 4. Corpus pools large enough:
Lean = mathlib4 (~97MB) + Physlib + Std/Batteries + Lean core + major Lake
packages (~300–500MB total); Python-physics = QuTiP + astropy + SciPy +
PlasmaPy + yt + …; use The Stack v2 slices for mainstream-language controls so
your orderings are comparable to 2512.13472's. Use PyTorch (nanoGPT-style is
fine); keep 1-token=1-byte or use bits-per-byte normalization throughout.
Tens of GPU-hours for the ≤100M tier.

### Phase 3 (optional) — the verification gradient

Within-language predictability vs verification status: Lean files with vs
without `sorry`; PhyslibAlpha (AI-generated, lower review) vs reviewed Physlib;
Rust by `unsafe` density; SPARK/Ada; seL4-style verified C vs ordinary C. This
speaks directly to the essay's security motivation.

## 5. Pitfalls already hit (do not rediscover these)

1. llama.cpp master cannot train (assert in ggml_build_backward_expand); b6000
   works. On GPU, skip llama.cpp entirely for training.
2. lr is touchy at tiny scale: 1e-3 silently diverges to worse-than-random
   while accuracy still climbs. Always eval on held-out data; never trust the
   training progress bar — with unshuffled topo-ordered data the running loss
   rises on hard mid-corpus sections and looks like divergence when it isn't.
3. Tokenizer normalization is the #1 cross-language confound; byte-level vocab
   or bits-per-byte normalization is non-negotiable. (SPM-style vocabs mangle
   whitespace via ▁-replacement — 3 tokens per space — which is why the GGUF
   uses a GPT-2-alphabet byte vocab with no merges.)
4. Chunked evaluation resets context at chunk boundaries: position-0 tokens
   have genuinely zero context (that's the c→1 end of the curve, a feature),
   but make sure chunk boundaries don't systematically align with file
   boundaries differently across corpora.
5. Lean files are Unicode-dense (∀, ⟨⟩, ↦ …): any tokenizer/vocab choice must
   round-trip them byte-exactly; verify token-count == byte-count on mathlib
   files specifically.
6. Corpus budget matching matters and QuTiP is small; if you enlarge the
   Python-physics pool, re-match budgets rather than letting Python grow.

## 6. Deliverables to aim for

The essay-answering artifact is a table + two plots: per-language β and L∞
with CIs (Phase 1, per model scale; Phase 2, per training scale), and the
BPB-vs-context curves overlaid by language. Compare your mainstream-language
ordering against 2512.13472's; highlight where Lean lands; state clearly which
axis (in-context vs training) each claim lives on. Write up with the
contamination-split honesty and the caveat literature cited. Cite
gwern.net/lean-scaling as the motivating proposal — and consider emailing
gwern@gwern.net with results; he explicitly solicits follow-ups on proposed
research and (as of Aug 2026) is winding down solo writing, so external
uptake is the essay's only path to being tested.

## 7. Sanity checklist before believing any result

Random-init model flat at log2(V) BPB across positions. Token count == byte
count on every corpus. Held-out eval (never train loss) for all comparisons.
Same absolute byte budgets across languages in any matched comparison.
Bootstrap CIs by document/chunk, not by token. Fits stable under bin-edge
changes and under mean→median swap. Contamination splits reported separately.
Multiple seeds for anything trained from scratch.
