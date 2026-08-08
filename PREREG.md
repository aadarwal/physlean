# PREREG — shared design document (source of truth)

Status: **v1 DRAFT — pending adversarial review sign-off** (both agents +
human). Supersedes any single pane's summary. Changes to this document are
themselves review boundaries. Gates below; nothing past G3 runs without
explicit human approval. Open disagreements are logged in §13 and must be
resolved or explicitly accepted before the affected gate.

## 1. Estimand and non-claims

We estimate, per (model m, corpus s): **C_{m,s}(c)** = byte-normalized
teacher-forced code length (bits/byte) of stream tokens as a function of the
number of contiguous preceding in-context bytes c, on dependency-ordered
concatenated streams, with hard context resets at fixed token-window
boundaries.

This is a **model-relative** quantity: C = H(true conditional) + KL(model
mismatch). It is NOT: intrinsic language entropy, design quality, security,
or a per-language scaling law. Cross-family comparisons are qualitative only.
Findings are **exploratory**; confirmatory claims require the paired-context
v2 design (§9). "L∞" in fits is a finite-model extrapolated asymptote and is
reported as `asymptotic model BPB`, never as irreducible entropy.

PRIOR DATA EXPOSURE (integrity disclosure — this preregistration is
pre-full-grid but NOT data-naive): before the gates were frozen, the
authors observed engineering smokes recorded in RESUME.md — a random-init
Qwen2.5-Coder-0.5B anchor (~12.10 nats/token ≈ ln V, flat in context), a
single real 0.5B pass over ~120KB of mathlib text (BPB falling ~3.1→0.6
through ~16KB of context), a byte-GPT Lean training smoke (8.0→4.59 BPB),
and this session's synthetic/random-init plumbing smokes. NO cross-corpus
comparison, no model-ladder result, no clean/full split, and no
order/phase ablation was ever computed or seen. The frozen numeric gates
(§6, §7) were set AFTER these smokes; the mathlib smoke in particular
means "context reduces 0.5B loss on mathlib" was known — every claim at
that or finer granularity is therefore exploratory regardless of gate
outcomes.

NAMING (conceptual fork, recorded as a decision): everything in this
program manipulates AVAILABLE CONTEXT — window-relative context in G3,
repository context on fixed targets in v2. Neither manipulates codebase
SCALE or GROWTH, and no result here is presented as a "software/codebase
scaling law". The confirmatory question is named **repository-context
sufficiency**; the growth question belongs to the separate longitudinal
snapshot arm (DESIGN_V2 §11), which has its own gate.

Novelty positioning (updated after adjacent-work review): Lean4Physics
(ICLR 2026, arXiv:2510.26094) already shows a BEHAVIORAL Lean context
effect — ~+11.9pp average pass@16 on 200 LeanPhysBench theorems with vs
without PhysLib context. Our contribution is therefore the
**context-scaling curve of byte-normalized code length with controlled
context selection and contamination control**, cross-language — not "the
first Lean context study". Lean4Physics is cited as external behavioral
validation. LICENSING CONSTRAINT: LeanPhysBench is CC BY-NC with an
explicit prohibition on using it to train, fine-tune, or evaluate AI
models — it is NOT ingested or evaluated here absent written permission
(PhysLib code itself is Apache-2.0 and fine).

## 2. Data

Corpora (Phase 1 measurement): physlib (Lean/physics), mathlib4 (Lean/math),
qutip (Py/physics), sympy (Py/math), geant4 (C++/physics), and the
**LaTeX-source reference** corpus (arXiv e-print bundles; old=2023H1 /
new=2026-05+, ids pinned in arxiv_manifest.json). Construction confound,
logged: these are RAW CONCATENATED LaTeX SOURCE BUNDLES — macros,
auxiliary files, and possible included-file duplication included — not
clean informal prose; NO Lean-vs-prose formality claim is drawn from
this arm.
arXiv pinning contract: every non-skipped source is pinned to an EXPLICIT
version — v1 for ALL entries migrated from the legacy byte-only manifest
(no Atom versions were stored for either era); future fresh listings
record the Atom-listed version — and
fetched as /e-print/{id}{vN}; the committed manifest records per-file
version, byte count, and SHA256, and refetch/preflight validate exact
per-key hash equality plus the exact expected key set (no missing, no
extra on-disk files — prep ingests the directory). The one-time migration
from the byte-only snapshot is a reviewed two-commit adoption; weak
byte-only pins fail the science gates. Clean-arm cleanliness is unaffected
by versioning (revisions cannot predate submission); the v1 pin makes the
historical arm's "extant at submission" reading exact.
batteries and astropy are staged now and enter as v2 corpora at G3.5;
a second C++ repo (e.g. LAMMPS) is DEFERRED and currently unstaged. Until
multi-repo cells exist, all corpus-level claims are labeled single-repo. Known artifact confound: Lean cells are theorem/proof corpora,
Python cells executable libraries; domain-matched, not artifact-matched.

Budgets: byte-matched across corpora within each stream kind (min
available, cap 2.4MB) via ONE corpus-independent selection policy —
seeded per-file priorities (SHA256 of seed:relpath), greedy
whole-document fill to the nominal budget (never padding), then
topo-ordering of the selected set; selection method/seed and the
order-independent doc-set hash are recorded per stream. (The earlier
every-kth stride made sampling POLICY a function of corpus size —
mathlib at ~1/40 vs QuTiP near-complete — a corpus-dependent bias
invisible to byte tolerance; review fix.) Stated limitation: sampled
corpora have INCOMPLETE dependency closures by construction; the topo
order orders the selected set only. Corpus-sampling sensitivity is a
sentinel item: `full_topo_s2` re-runs the same rule under a second seed.
Streams: `full_topo` (headline), `full_shuffled` (order ablation, same
selected set), `full_topo_s2` (sampling sensitivity), `full_topo_xl`
(nested extension), `clean_*` (secondary only, §5).

Provenance: every prep run records each corpus's git HEAD SHA; every eval
dump's meta records the harness commit; models.json records HF repo SHAs.
Raw per-token NLL dumps (`nll_dumps/*.csv.gz` + meta) are the preserved
artifact and are never overwritten by analysis changes.

## 3. Models

Families (base checkpoints): Qwen2.5-Coder {0.5,1.5,3,7,(14,32)}B — primary
ladder; Qwen3 {0.6,1.7,4,(8,14)}B (no 32B base exists — HF 401 verified);
Qwen3.5 {0.8,2,4,(9)}B (multimodal
wrapper, hybrid attention — own family, never pooled); StarCoder2-3b (16k
max, 4k sliding window — reported separately, never on 32k-model plots);
DeepSeek-Coder-V2-Lite (MoE). Scale trends are **within-family only**.
Contamination cutoffs = HF repo creation dates (conservative): c2024_11 /
c2025_04 / c2026_02 per family.

## 4. Measurement semantics

Tokenize stream with the model's tokenizer (no special tokens); byte length
per token from offsets; **assert** sum(token bytes) == stream bytes (byte
conservation — necessary, not sufficient for attribution). Tokens whose char
spans overlap a predecessor (multi-token unicode chars) form a **source-span
group**: dumps carry a `grp` column (segment-global source-group id), and
analysis aggregates NLL and bytes **within groups**, asserting byte-union
coverage and NLL conservation per cell, across all four tokenizer families.
Exclusion is explicit, never silent: each window-opening group (whose
opener is the window's unscored first token) is EXCLUDED from scored BPB
— its bytes are reported as opening_group_bytes and its scored followers'
NLL as boundary-dropped — and the evaluator asserts the full ledger
scored + opening + phase-skipped == evaluated bytes per cell. Per-corpus
group mass is a battery diagnostic (§7).

Windows of ctx tokens (32k default) are nonoverlapping context-reset
episodes — the resampling cluster for bootstraps, NOT statistically
independent samples (adjacent windows share a repo and its conventions);
the
chunked KV-cache forward with fp32 log-softmax is exact w.r.t. **the
checkpoint's attention semantics** (equality with one-shot forward is a
battery assert) — NOT "full attention over all c bytes": StarCoder2's 4k
sliding window and Qwen3.5's hybrid attention do not expose all in-window
bytes, and each cell's meta records the attention mechanism note. ctx_bytes
for a target = bytes of preceding tokens within its window (bytes present,
not necessarily attended). The window-phase ablation (phases {8192, 16384,
24576} on the sentinel 0.5B, paired same-group analysis) runs IN G3a and
gates expansion; until it reports, all position curves are descriptive.

Reproducibility pinning: evaluator loads model weights at the revision
recorded in models.json (resolved SHA in cell meta); meta also records the
SHA256 of the stream and its manifest, the harness commit, and whether the
working tree was clean.

## 5. Contamination protocol

Primary: **clean-target masking** — score only tokens of documents whose
rename-aware first-add date (min of author/committer; --follow-verified)
postdates the model family cutoff, inside the FULL topo stream, so targets
are post-cutoff while context keeps the natural (old) dependency
distribution. Computed from existing full_topo dumps (doc_id -> date join).
**Code corpora only**: the LaTeX corpus's two eras are disjoint stream
universes (full_topo = 2023 era contains no post-cutoff targets), so LaTeX
is excluded from this protocol; its contamination design is the era-vs-era
comparison of matched streams (arxiv_old vs arxiv_new), which exists by
construction.
Secondary: all-new streams (`clean_*`, both target and context post-cutoff)
as a robustness arm. Both reported; full-split numbers always shown beside
them. Caveat recorded: git dates bound publication in THIS repo only;
vendored/ported content may be older — the adding-commit subjects of
candidate-clean files ARE screened (implemented in prep: vendor/port/copy/
migrate regex; flags land in stream manifests), reported, and excludable
as a sensitivity. The masked-vs-full delta is named a
**temporal-generalization (cohort) gap** everywhere: newer code differs by
topic/style/era, so contamination is one contributor, never the whole
story. arXiv sources are pinned to EXPLICIT versions (v1 for the
historical arm) with per-file SHA256 after the one-time repin migration
(two-commit workflow: code commit, then the cluster-generated candidate
manifest reviewed and adopted); weak byte-only pins fail the science
gates. Cross-language BPB is UTF-8-sensitive (Lean glyph density): exact
scored-codepoint accounting does not exist yet, so cross-language
descriptive comparisons stay QUALITATIVE (no bits-per-codepoint field is
emitted or inferred) until a schema revision adds per-group codepoint
counts.

## 6. Analysis plan

1. Nonparametric first: binned curves (log-spaced c bins, common byte
   support across compared cells, per-bin means AND medians) with
   window-level bootstrap; doc-level bootstrap as robustness. **Sample size
   is windows and documents, never tokens**: every bin reports effective
   windows and distinct docs; a bin enters fits only with >= 8 windows; a
   cell gets CIs/fits only if classified "quantitative" (>= 15 windows and
   >= 30 docs overall), else it is published as descriptive/insufficient
   with no exponent. Matched 2.4MB streams (~20 windows @ 32k) carry the
   cross-corpus comparisons; supplementary unmatched `full_topo_xl` streams
   (<= 12MB, primary ladder small/mid sizes only) carry curve/fit stability.
   Clean-target masking viability (post-cutoff doc count and byte mass per
   corpus per cutoff) is quantified in preflight before G3; corpora under
   floor (>= 20 docs, >= 300KB) are labeled descriptive for that protocol.
2. Primary summary statistics (descriptive, unpaired-curve): context gain =
   BPB over the first common decade (c in [16,256)) minus BPB at the top
   common bin (both gated by the >=8-window bin floor; the c<4 bin is too sparse and
   content-confounded to headline), and the curve-flattening point ĉ(eps)
   relative to the cell's own top-common-bin BPB. Causal "minimal sufficient
   context" is NOT identified by these curves and is reserved for the paired
   fixed-target design (§9).
3. Power-law fit A·c^(−β)+L∞ is reported ONLY if it predicts a contiguous
   held-out high-context range (fit on c ≤ 8KiB, predict [8KiB, top common
   bin]; relative-error gate), and is always shown against alternative
   functional forms (saturating exponential, log-linear) under the same
   holdout; equal-weight and sqrt-byte-weight fits shown, byte-weight as
   sensitivity; alt bin edges as stability check. β never headlines alone.
4. G3 NUMERIC cross-language inference is BARRED until exact
   scored-position codepoint accounting exists (bytes-only normalization
   is UTF-8-biased against Lean); cross-language reading stays
   qualitative. Family-scoped ladders; unmatched streams (XL) excluded
   from matched cross-corpus comparisons — they appear only in per-corpus fit-stability
   plots, labeled unmatched; single-repo cells labeled as such.
5. Frozen numeric gates (set before any grid data exists): the power-law
   fit is accepted ONLY if mean relative error on the contiguous held-out
   range (bins in (8KiB, top-common]) is < 5% AND it is not beaten there
   by the saturating-exponential or log-linear alternatives; ĉ(eps) uses
   eps = 0.05 bits/byte above the cell's top-common-bin BPB (eps = 0.10
   reported as sensitivity). These constants do not move after data.

## 7. Validity battery (G2 gate; smallest set)

On Qwen2.5-Coder-0.5B unless noted; all outputs to results_v2/battery/.
A. Chunked-vs-one-shot NLL equality on one 8k window (report max/mean |Δ|;
   tolerance note for bf16).
B. Zero-byte-row mass per corpus x tokenizer family (rows share; NLL share
   on two corpora) — gates the merge fix.
C. Nested-context monotonicity: same 512-token targets scored under true
   prefixes {1k,4k,16k,32k}; report violations.
D. Duplicate/boilerplate control: file repeated 8x -> later copies must
   collapse toward ~0 BPB (in-context copying sensitivity).
E. (lite) Dependency-vs-irrelevant context: physlib targets under direct
   import context vs equal-byte random same-corpus context.

Gating semantics: **A and B are plumbing invariants** — failures block
G3. FROZEN numeric gates (set before any battery run; code and PREREG
must agree): A passes iff, for EVERY family probed, the chunked-vs-
one-shot mean |ΔNLL| < 5e-3 nats AND p99 |ΔNLL| < 5e-2 nats AND the
loaded class is a text-generation class within the predeclared
per-family parameter range (loader sanity). B passes iff group
aggregation conserves NLL exactly (fp64 tolerance 1e-6 relative) and
byte union equals raw bytes exactly, on real corpora and the synthetic
and real-offset probes, for all four tokenizer families. **C, D, E are characterization controls**,
scientific outcomes reported against predeclared relative expectations
(C: mean NLL non-increasing in prefix length within noise; D: copies ≥ 2
collapse by ≥ 5x vs copy 1; E: direction reported, no threshold) — surprises
there inform interpretation and the G3.5 design, and only a plumbing-level
anomaly (e.g. NLL non-conservation) blocks.

Big-gate extension (planned; the `big` preflight FAILS CLOSED until it
exists and passes): a battery `--big` mode running (1) the DeepSeek-
V2-Lite architecture probe (loader/params/offsets, chunk-vs-one-shot on
the MoE path) and (2) a Qwen3.5 cache probe at >= 131072 tokens before
the 131k arm. Additionally, ONE overlap model (Qwen2.5-Coder-3B) is
re-run on H200 after its L40S cells to bound hardware/kernel drift in
the size ladders; the overlap delta is reported with the big-rung
results and a per-cell |ΔBPB| > 0.005 b/B flags investigation.

## 8. Exploratory grid (G3)

The prepared (model x corpus x stream) grid runs ONLY after G1+G2 review.
It supports: descriptive curves, within-family scale trends, order
ablations, contamination-gap descriptions — all corpus-level. It cannot
identify language-causal effects (single repo per cell, artifact confound,
model-mismatch term); the writeup states this in the abstract.

Scope clarification (review): **prefix context length and codebase scale
are different interventions.** G3 varies the former only; it never
manipulates codebase size. Position curves can reward entanglement or
repetition (large late gains) while well-modularized code saturates early,
so β carries NO monotone design-quality interpretation and is never
presented as one.

## 9. v2 main experiment (design doc at G2.5 — BEFORE any grid spend)

Paired-context conditions on fixed targets (signature-only / local-file /
dependency-closure reference / random matched-byte / retrieved / full).
**Headline v2 estimands**: repository-context gain (query-only vs
reference), interface sufficiency (k3-vs-k4 non-inferiority),
relevant-vs-equal-byte-irrelevant context gain, excess-to-reference
(integrated excess loss vs the dependency-closure reference), and the
tested-grid threshold c_epsilon on fixed
targets, verifier/test pass@k, and mutation detection/repair; same-target
ΔNLL and AST-node deltas as secondary. Plus: semantically paired
implementations (lean-zip vs zlib; FormalScience pairs); within-Lean
formality ablations; blame-hunk clean targets; hierarchical multi-repo
models. This is the confirmatory experiment; the G3
sweep is its motivation section. Per review: the full design doc is written
and reviewed at **G2.5, before G3 submission**, and the battery includes a
minimal paired-target pilot probe (item E) so the design is grounded in
measured behavior rather than speculation.

Falsifiers / decision rules (preregistered):
- NLL-as-proxy is REJECTED if paired context gains fail to predict
  within-target compile/test/proof pass@k or mutation detection/repair.
- Language-level interpretation is REJECTED if effects vanish across
  repos or model families, or under semantic pairing (tokenizers are
  inseparable from pretrained models and are never varied independently).
- Security inference is asserted ONLY with specification-coverage and
  TCB/runtime stratification (lean-zip strata above).
- Reporting: bytes AND tokens; total target bits per matched semantic
  unit; no extrapolation outside observed context support.

Lean dependency reference (per review): the G2.5 closure condition is
defined from **elaborated declaration references** (.ilean declaration/
reference/directImport tables; LeanDojo-v2-style extraction for source
spans and premises, caches on POOL), enabling exact declaration targets and
interface-only vs implementation-bearing premise contexts. Regex module
imports (as in battery item E) are the coarse baseline only.

lean-zip correction (source review, kirancodes.me
"who-watches-the-watchers", Apr 2026 fuzzing audit): lean-zip is NOT a
monolithic verified-vs-zlib pair — its strata differ (verified application
core: no memory defects in 105M fuzz executions; unverified application
modules, e.g. the Archive.lean parser: DoS found; trusted Lean C++ runtime
base: heap overflow found). The design therefore stratifies targets by
{proved implementation modules, unproved application modules, trusted
runtime boundary}, records specification coverage per target, and treats
lean-zip as a within-repo verification-gradient case study; whole-repo
comparisons to zlib are made only with stratum labels attached.

## 10. Phase 2 framing — G6 BLOCKED pending redesign

At ~120MB/language, one-epoch 30M/100M models see only ~4 and ~1.2 bytes
per parameter — severely undertrained; this CANNOT estimate L(N,D) and is
not run as-is. Redesign required before G6: smaller N and/or much more D,
>= 3 seeds, and an explicit statement of what the fixed-budget comparison
identifies. 300m dropped. Runs only after redesign review, after G3.

## 11. Gates and review boundaries

G0 this document (review: both agents + human) ->
G1 acquisition repair + fail-closed integrity; boundary report = commit
   hashes, preflight output, corpus SHAs, model inventory ->
G2 battery results ->
G2.5 v2 design doc written and reviewed ->
G3a SENTINEL run (Qwen2.5-Coder-0.5B only; 53 frozen cells: full+clean,
   XL, shuffled, per-doc, window phases {8192,16384,24576}, and the
   second-selection-seed streams): stop/go on INSTRUMENT VIABILITY ONLY —
   realized windows/docs vs preflight estimates, byte-ledger and
   quarantine cleanliness, per-cell runtime (grounds all later ETAs),
   order/reset sensitivity magnitudes, the PAIRED SAME-GROUP phase
   analysis (same content under shifted window positions — the direct
   probe of content-position confounding in the phase-0 curve), and
   sampling-seed sensitivity. Never on whether any language looks
   favorable ->
G3.5 v2 EXTRACTION VALIDATION + PILOT (V2-a/V2-b) — adopted strategic
   ordering: the paired fixed-target experiment that directly identifies
   repository-context sufficiency precedes any grid expansion, because
   the stream grid is position/content-confounded by construction ->
G3b small/mid grid expansion (human approves, explicit --smallmid;
   183 frozen cells; OPTIONAL descriptive breadth after G3.5) ->
G4 OPTIONAL grid analysis (analyzer-v3 over whatever grid ran; purely
   descriptive) ->
G5 CONFIRMATORY v2 full run (V2-c) + analysis (V2-d) per DESIGN_V2 —
   the extraction/pilot half (V2-a/b) already happened at G3.5 ->
G6 Phase 2 (blocked pending redesign).
Each boundary message includes: commit hash, changed files, exact commands
and results, and open disagreements. Reviewer runs adversarial checks in
parallel; disagreements recorded here before proceeding.

## 12. Fail-closed execution rules

Setup/fix scripts verify each step and refuse completion markers on any
failure. Submission requires a passing preflight (streams nonzero and
matched, models cached at pinned revisions, env imports + frozen
environment, battery plumbing for G3). Runners classify EVERY expected
cell (done / runnable / missing-model / missing-stream) and exit nonzero
on any gap; Slurm job success without cell success is treated as failure.
Model revision pins are append-only (re-pinning requires explicit REPIN=1).
**Source-clean definition**: `git status --porcelain -- . ':(exclude)results_v2'`
must be empty — results_v2/ is machine-written evidence, committed and
reviewed at boundaries; everything else (code, PREREG, models.json,
arxiv_manifest) must be committed before measurement.

## 13. Disagreement log

- ADOPTED (2026-08-07 late): reviewer's strategic ordering — v2
  extraction/pilot (G3.5) before grid expansion (G3b); the sentinel grid
  runs first only as instrument validation. Supersedes my earlier
  run-grid-concurrently position for the expansion decision; rationale:
  the paired design identifies the target quantity, the stream grid does
  not.
- SUPERSEDED (same date): the frozen sentinel count 29 → 53 after the
  reviewer moved the window-phase ablation and sampling-seed sensitivity
  into G3a (their explicit supersession of the earlier hard-code-29
  request).

- [SUPERSEDED by the adopted G3.5 ordering below] Reviewer proposed
  piloting the paired fixed-target experiment before ANY grid spend.
  Originally partially adopted: design doc moves to G2.5 (before
  submission) and battery item E is a minimal paired probe; the ~1-GPU-day
  exploratory grid itself still runs at G3 after human approval, because its
  raw dumps are condition-independent, feed target selection for v2, and the
  marginal cost of delay exceeds the marginal risk given the G2 plumbing
  gates. Recorded 2026-08-07; open for human override.

## 14. Known limitations (standing list)

Model-relative estimand; single repo per cell (until G4+); proofs-vs-
software artifact confound; contiguous-stream packing is not dependency
closure; window position/content confound (DIAGNOSED, not eliminated, by the same-group phase pairs; shuffles probe order, not position);
git dates bound only in-repo publication; exact-reference NLL misses
set-valued correctness (v2 pass@k arm); Phase 2 budgets are fixed-D.
