# PREREG — shared design document (source of truth)

Status: **v1 FROZEN THROUGH THE PRE-PILOT GATES after joint adversarial
review**; V2-c/full-grid scale still requires the explicit human approval
below. Supersedes any single pane's summary. Changes to this document are
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
**LaTeX-source reference** corpus — DEMOTED to an OPTIONAL preserved
artifact + separately-gated format diagnostic (decided before any
outcomes; §13): it holds NO cells in any core grid and is excluded from
ALL budget matching (targets compute over CORE code corpora only;
optional streams are self-budgeted and always unmatched). Query windows
were 2023H1 and 2026-05..08, but the earliest-70-per-category listing
makes the REALIZED corpus a convenience sample: old = 2023-01-01..05
(132 files), new = 2026-05-01..04 (133 files), 4 predeclared skips.
Construction confound, logged: RAW CONCATENATED LaTeX SOURCE BUNDLES —
macros, auxiliary files, possible included-file duplication — not clean
prose; NO Lean-vs-prose formality claim is drawn from this arm (the true
formality test is V2's fixed-content pairs). Integrity is tri-state:
absent -> non-blocking; present -> must validate, failure blocks G1.
arXiv pinning contract: every non-skipped source is pinned to an EXPLICIT
version — v1 for ALL migrated entries EXCEPT where v1/v2 carry no TeX
source: the single such case (2301.00502 -> v4, byte-identical to the
inherited file, revision dated 2023-03-31, pre-all-cutoffs) is documented
in-manifest and in §13; future fresh listings record the Atom-listed
version — and
fetched as /e-print/{id}{vN}; the committed manifest records per-file
version, byte count, and SHA256, and refetch/preflight validate exact
per-key hash equality plus the exact expected key set (no missing, no
extra on-disk files — prep ingests the directory). The one-time migration
from the byte-only snapshot is a reviewed two-commit adoption; weak
byte-only pins fail the science gates. Clean-arm cleanliness is unaffected
by versioning (revisions cannot predate submission); the v1 pin makes the
historical arm's "extant at submission" reading exact for every file
except the documented v4 case above.
batteries and astropy are staged now and enter as v2 corpora at G3.5;
a second C++ repo (e.g. LAMMPS) is DEFERRED and currently unstaged. Until
multi-repo cells exist, all corpus-level claims are labeled single-repo.
Known artifact confound: Lean cells are theorem/proof corpora, Python
cells executable libraries; domain-matched, not artifact-matched.

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
order orders the selected set only. Second stated limitation (§13
amendment): topo ordering resolves SOURCE-LEVEL imports, and streams
record their resolved `dependency_edges` count — physlib exposes only
8 source import directives across 538 files, so with ~zero edges
Kahn's min-heap pops file-sort indices and physlib's `full_topo`
degrades to mostly LEXICOGRAPHIC PATH ORDER (selection stays seeded;
the ORDER does not). Its order ablation (`full_topo` vs
`full_shuffled`) therefore compares two nearly-arbitrary orders and
CANNOT be interpreted like mathlib's richly-constrained one; a null
physlib order effect must never be read as "order doesn't matter". Corpus-sampling sensitivity is a
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
independent samples (adjacent windows share a repo and its conventions).
The chunked KV-cache forward implements teacher forcing under the
checkpoint's attention semantics on one frozen bf16 numerical path, with
fp32 log-softmax; it is NOT numerically identical across chunk shapes or
to one-shot, so chunk = 2048 is fixed and identity-gated (§7/§13). The
semantic and causal-mask invariants are battery assertions. This is also
NOT "full attention over all c bytes": StarCoder2's 4k sliding window and
Qwen3.5's hybrid attention do not expose all in-window bytes, and each
cell's meta records the resolved attention mechanism note. ctx_bytes
for a target = bytes of preceding tokens within its window (bytes present,
not necessarily attended). The window-phase ablation (phases {8192, 16384,
24576} on the sentinel 0.5B, paired same-group analysis) runs IN G3a and
gates expansion; until it reports, all position curves are descriptive.

Reproducibility pinning: evaluator loads model weights at the revision
recorded in models.json (resolved SHA in cell meta); meta also records the
SHA256 of the stream and its manifest, the harness commit, and whether the
working tree was clean.

**Measurement identity (schema v4, adopted pre-launch)**: every cell
additionally records (a) the MEASUREMENT-HARNESS HASH — sha256 over
exactly eval_incontext.py + layout.py, the files whose code determines
dump content given identical inputs (orchestration and provenance
plumbing are deliberately excluded: their changes cannot alter measured
bytes and gates always execute from current code) — and (b) the
canonical SOFTWARE-ENVIRONMENT FINGERPRINT: python runtime + the sha256
of the RESOLVED base interpreter binary (two builds of the same version
string are different environments — see the §13 Triton incident) +
torch CUDA build + every installed distribution as sorted name==version
lines (one shared implementation in provenance.py used by the
evaluator, cell_done, the battery, and preflight); and (c) the frozen
PRODUCTION_CHUNK_TOKENS = 2048, checked by cell_done. A production eval (non-dev,
non-random-init, any device) REFUSES before model load unless the live
environment equals both the committed wheel lock
(requirements-cluster.lock, python contract included) and the
write-once software-only freeze, and re-checks harness and environment
at end of run; cell_done requires both recorded identities to equal
the CURRENT ones, so a grid can never silently mix cells from
different evaluator code or environments. GPU model and driver are
recorded INFORMATIONALLY and are never part of any gate (frozen
decision: mixed L40S/H200 grids are by design; the battery overlap
item is the cross-hardware instrument). The freeze file is evidence:
replacing it requires explicit REFREEZE=1 and quarantines the old one.

## 5. Contamination protocol

Primary: **clean-target masking** — score only tokens of documents whose
rename-aware first-add date (min of author/committer; --follow-verified)
postdates the model family cutoff, inside the FULL topo stream, so targets
are post-cutoff while context keeps the natural (old) dependency
distribution. Computed from existing full_topo dumps (doc_id -> date join).
**Code corpora only**: the LaTeX corpus's two eras are disjoint stream
universes (full_topo = 2023 era contains no post-cutoff targets), so LaTeX
is excluded from this protocol entirely. Since the arXiv demotion
(§13), the optional corpus's streams are SELF-BUDGETED and unmatched —
no matched era-vs-era comparison exists; any old-vs-new reading is an
optional DESCRIPTIVE format/era diagnostic only, never a contamination
control and never budget-comparable to any code corpus or to the other
era.
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

**ARM-FEASIBILITY MANIFEST (frozen at G1, before any battery/grid
outcome, from
deterministic streams_stats — floors NEVER move post-hoc).** Floors:
masking viable = >=20 post-cutoff docs AND >=300KB post-cutoff bytes
INSIDE the sampled full_topo stream; all-new matched = >=150KB
post-cutoff bytes corpus-wide (MIN_MATCHED). Frozen realized sets:

| cutoff   | masking viable      | all-new matched                     |
|----------|---------------------|-------------------------------------|
| c2024_11 | physlib, mathlib    | physlib, mathlib, sympy, geant4     |
| c2025_04 | physlib, mathlib    | physlib, mathlib, sympy, geant4     |
| c2026_02 | physlib             | physlib, mathlib                    |

Realized c2026_02 rows (cluster streams_stats at the adoption
boundary): masking — physlib 47 docs / 632,246B (viable); mathlib
25 / 167,496B (fails the 300KB floor); qutip 2 / 8,416B; sympy
1 / 3,868B; geant4 1 / 7,975B. All-new corpus-wide — physlib
1,498,325B, mathlib 5,000,507B, qutip 8,416B, sympy 54,843B, geant4
130,834B. (c2024_11 and
c2025_04 numeric rows are recorded in the committed G1 preflight
report; the frozen SETS above are the gated content.) Near-misses
recorded, floors unmoved: geant4 c2026_02 all-new at 130,834B and
mathlib c2026_02 masking at 167,496B — the floors predate this G1
feasibility inspection and are not adjusted after seeing which cells
they admit.

Three consequences, stated plainly. (1) The MASKING arm is LEAN-ONLY
at every cutoff: qutip/sympy/geant4 never reach the in-stream floors.
Mechanism, not accident: masking operates inside the fixed 2.4MB
seeded full_topo sample, and seeded whole-corpus selection dilutes
recent files proportionally for large corpora — mathlib holds 5.0MB
of post-2026-03 code corpus-wide, but only 167KB landed in-stream.
(A recency-stratified sample is a possible FUTURE sensitivity behind
its own gate; not a change now.) (2) Cross-language contamination
control for c2024_11/c2025_04 rests on the ALL-NEW arm (4 corpora,
qutip below floor). (3) The Qwen3.5 family (c2026_02) has NO
cross-language contamination-controlled arm in this corpus set — see
the claim bar in §6. Preflight verifies realized sets equal this
manifest EXACTLY at every science gate (arm-feasibility-frozen;
g3a re-checks its own c2024_11 row), so feasibility cannot drift
silently under a re-prep.

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
   The realized per-cutoff arm sets are FROZEN in the §5 feasibility
   manifest and exact-set-gated (the earlier >=3-matched-cells scalar is
   superseded: it could be satisfied by a single-language pair).
   **QWEN3.5 CLAIM BAR (frozen before any battery/grid outcome)**: the
   c2026_02 family has
   NO cross-language contamination-controlled arm in this corpus set —
   masking is physlib-only and the matched all-new cells are the Lean
   pair. Accordingly: no cross-language contamination-controlled claim
   is made for Qwen3.5 at ANY cell count; in scope for that family are
   (i) contaminated full-stream curves across all five corpora, labeled
   uncontrolled, (ii) the physlib-only masking cohort gap, labeled
   single-corpus, (iii) the physlib-mathlib all-new pair, descriptive,
   within-Lean, with the §5 dilution note attached to mathlib. Lifting
   this bar requires a corpus amendment (fast-moving Python/C++ repos)
   through its own reviewed gate — never a threshold change. The
   216/152/44 grid is unchanged: unmatched c2026_02 cells stay in the
   grid flagged unmatched=True and are excluded from claims by the
   existing fail-closed stream_unmatched machinery plus this bar.
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
A. RE-SPECIFIED as **A_fixed_chunk_semantics** (per the §13 incident
   rule, after the follow-up falsifier PASSED): production-path
   invariants at exactly 8192 tokens per bf16 family — loader
   class/param sanity; production-chunk repeat determinism; the
   structural CAUSALITY probe (perturb input p=4095, protected
   rows 0..4093 unchanged, non-vacuity required) on the exact
   production kernel with resolved attention impl recorded — plus the
   q25c fp32 semantic leg (TF32 off asserted, model impl gated ==
   'sdpa', torch SDP backend forced MATH, chunk 512 vs
   PRODUCTION_CHUNK_TOKENS). NO bf16 cross-shape or one-shot gate
   exists: bf16 kernel-shape divergence is characterized (§13), not
   gated, and every production cell runs the ONE frozen chunk
   (layout.PRODUCTION_CHUNK_TOKENS = 2048; chunk is part of the
   cell_done measurement identity), so the measurement never crosses
   kernel shapes.
B. Zero-byte-row mass per corpus x tokenizer family (rows share; NLL share
   on two corpora) — gates the merge fix.
C. Nested-context monotonicity: same 512-token targets scored under true
   prefixes {1k,4k,16k,32k}; report violations.
D. Duplicate/boilerplate control: file repeated 8x -> later copies must
   collapse toward ~0 BPB (in-context copying sensitivity).
E. (lite) Dependency-vs-irrelevant context: designated-corpus targets
   (mathlib — see the §13 infeasibility amendment below) under direct
   import context vs equal-byte random same-corpus context.

Gating semantics: **A and B are plumbing invariants** — failures block
G3. FROZEN numeric gates (code and PREREG must agree; A's gates
re-specified per the §13 incident rule): A passes iff the PURE verdict
(a_fixed_chunk_verdict) holds — top-level production chunk and EXACT
family coverage; per family: bf16 dtype, text-generation class within
the predeclared parameter range, chunk == PRODUCTION_CHUNK_TOKENS,
exactly 8192 tokens, repeat max in [0, 1e-6], and the exact causality
partition/perturbation identity with protected max in [0, 1e-6],
finite nonnegative excluded row, and
downstream > 1e-6; q25c fp32 leg: float32 dtype, exactly 8192 tokens,
resolved impl == 'sdpa', SDP backend == MATH, TF32 off (matmul+cudnn
false, precision highest), mean |ΔNLL| < 1e-4 AND p99 < 1e-3
(pre-incident oracle bounds), repeat <= 1e-6, chunks exactly
[512, PRODUCTION_CHUNK_TOKENS].
NaN/non-finite or structurally invalid metrics, missing/extra families,
wrong dispatch, and wrong chunk all fail closed. B passes iff group
aggregation conserves NLL exactly (fp64 tolerance 1e-6 relative) and
byte union equals raw bytes exactly, on real corpora and the synthetic
and real-offset probes, for all four tokenizer families. **C, D, E are characterization controls**,
scientific outcomes reported against predeclared relative expectations
(C: mean NLL non-increasing in prefix length within noise; D: copies ≥ 2
collapse by ≥ 5x vs copy 1; E: direction reported, no threshold) — surprises
there inform interpretation and the G3.5 design, and only a plumbing-level
anomaly (e.g. NLL non-conservation) blocks.
**Item E designated corpus = mathlib (amendment, §13)**: the pinned
physlib snapshot exposes only 8 source import directives across 538
files (QuantumInfo 0; `import all` support still yields zero eligible)
— its dependency graph lives at the ELABORATED level and is reserved
for the V2-a extractor. The identical parser on pinned mathlib finds
81 eligible files under the unchanged criteria (>= 2 internal imports,
declaration present, 4-20KB). E carries a NON-VACUOUS eligibility
floor equal to its own sample size (8) — the smallest structurally
defensible value, fixed before any E outcome existed — and fails
closed below it; battery.json records corpus/parser/counts and
preflight rejects an empty or wrong-corpus E. SCOPE: E is machinery
validation only — it is not physlib evidence and not the §9/V2-b
grounding pilot.

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
and reviewed at **G2.5, before G3 submission**. The battery's item E is
MACHINERY VALIDATION ONLY of the paired-context measurement path (on
its designated corpus, §7) — it does not ground this design; the
paired grounding pilot is V2-b (DESIGN_V2 §9/§14.22), whose blinded
nuisance estimates set N and n. (Earlier wording calling item E the
grounding probe is superseded; the §13 log retains it historically.)

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
G3a SENTINEL run (Qwen2.5-Coder-0.5B only; 44 frozen cells: full+clean,
   XL, shuffled, per-doc, window phases {8192,16384,24576}, and the
   second-selection-seed streams): stop/go on INSTRUMENT VIABILITY ONLY —
   realized windows/docs vs preflight estimates, byte-ledger and
   quarantine cleanliness, per-cell runtime (grounds all later ETAs),
   order/reset sensitivity magnitudes, the PAIRED SAME-GROUP phase
   analysis (same content under shifted window positions — the direct
   probe of content-position confounding in the phase-0 curve), and
   sampling-seed sensitivity. Never on whether any language looks
   favorable. Sequencing (schema v4): the environment freeze must exist
   and preflight env-frozen must pass BEFORE the first sentinel dump —
   every G3a cell is produced and accepted at v4 identity (§4) ->
G3.5 v2 EXTRACTION VALIDATION + PILOT (V2-a/V2-b) — adopted strategic
   ordering: the paired fixed-target experiment that directly identifies
   repository-context sufficiency precedes any grid expansion, because
   the stream grid is position/content-confounded by construction ->
G3b small/mid grid expansion (human approves, explicit --smallmid;
   152 frozen cells; OPTIONAL descriptive breadth after G3.5). G3b
   additionally requires battery-plumbing to match the CURRENT source
   tree hash: any V2 code merged after the battery run intentionally
   invalidates it, and the remedy is a battery RERUN at the merged tree
   (minutes on one L40S) — the hash whitelist is never weakened. G3a may
   precede V2 code changes; the paired driver carries its own
   PAIRED_SCHEMA_VERSION so V2 evolution never invalidates G3-path
   artifacts ->
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

- ADOPTED (V2-a structural-evidence boundary, 2026-08-08,
  PRE-OUTCOME): source/artifact pairing schema is v2 and records
  exact-vs-srcDir-suffix match kind, absolute paths, both file hashes,
  repository HEAD, and an optional hard expected-HEAD check; consumers
  reject v1 and rehash both files. Lean/Python extraction artifacts are
  atomic new-file-only. Selected targets are live-reparsed rather than
  trusting recorded kind/split metadata; any Python source parse
  failure makes the structural gate fail. Two separate audits are
  required for each selected Lean target: (1) a stdlib-only raw-.ilean
  parser, sharing no extractor code, must reproduce the exact resolved /
  external / unrenderable partition and occurrence counts; (2) the
  unchanged file and a full-source copy with an inert comment inserted
  at the extracted body boundary must both compile in the pinned Lake
  environment. These checks are CPU-only structural validation, not a
  scored pilot result; the validation report remains gate_complete=false
  until their reports are combined and reviewed.
  FOLLOW-UP ADOPTED same day, still pre-outcome: each selected Python
  target receives the analogous unchanged-file plus boundary-marked
  `py_compile` audit under the locked Python binary (comment marker for
  indented suites, inert string statement for one-line suites; imports
  and target code are never executed). Python closure verification is
  explicitly NOT-APPLICABLE as an exact check: §14.4's AST closure is
  best-effort and its declaration-resolution coverage remains the
  reported diagnostic rather than being relabeled elaborator truth.
- ADOPTED (V2-a Lean source-renderability amendment, 2026-08-08,
  PRE-OUTCOME — compiler-core machinery audit only, no study-corpus or
  model outcome): the first module-qualified live sweep left 123,621
  internal source-reference occurrences unrenderable after explicit
  length-5 parent folds. Independent raw-.ilean audit classified
  110,224 (89.2%; 6,686 identities) as parentless length-4 definition
  sites inside a UNIQUE smallest enclosing declaration, 0 ambiguous,
  7,483 with no enclosing span, 4,348 with null definition, and 1,566
  with no definition entry. DESIGN_V2 §14.3 now permits only the unique
  smallest geometric fold (no name heuristic); the full implemented
  replay recovered 7,071 definition sites, raised occurrence-weighted
  renderability from 84.50% to 98.34%, and left 13,239 occurrences
  explicit. Per-target coverage and position/name-prefix diagnostics
  are mandatory. Reviewer recommendation adopted: STRATIFY/diagnose,
  never gate target eligibility on coverage, because such a gate would
  select against the projection-/proof-heavy code under study.
- ADOPTED (V2-a docstring-asymmetry amendment, 2026-08-08, PRE-OUTCOME
  — no scored pilot output exists; from the adversarial read-only
  review of the extractors): Python docstrings are literal expressions
  inside the function suite and so remain in the SCORED BODY, while
  Lean doc comments (/-- ... -/) precede the declaration and fall on
  the unscored shell/header side of the §14.9 split. DESIGN_V2 §14.9
  is amended: the Python extractor records a per-target
  docstring_bytes diagnostic (implemented in extract_python.py);
  cross-language body-size / body-NLL analyses
  MUST stratify by or condition on docstring_bytes, and naive
  cross-language body-size comparisons are FORBIDDEN as confounded by
  documentation placement; docstring bytes are NOT stripped from the
  scored body (byte-exact round-trip stays primary — stripping would
  silently change the scored object).
- ADOPTED (G3.5 V2-a pre-outcome identity amendment, 2026-08-08 —
  BEFORE any committed extraction, any pilot sample draw, or any use of
  the §14.19 priority key): Lean graph node identity is MODULE-
  QUALIFIED (module, declName), not bare fully-elaborated name. Cause:
  the live compiler-source stress run (2,433 modules of the installed
  v4.32 toolchain) raised `ExtractError: decl main in both LakeMain and
  LeanChecker` — fully-elaborated names are unique per ENVIRONMENT, not
  per source tree, so a corpus containing more than one executable (or
  any legitimate cross-module name reuse) is unrepresentable under bare
  names. Adopted contract (Lean extraction schema v1 -> v2,
  "v2a_lean_extract_v2"; consumers MUST reject v1): graph edges and the
  preserved external_reference_edges / internal_unrenderable_references
  lists are QUADRUPLES [src_module, src_decl, dst_module, dst_decl];
  external reference counts nest {module: {decl: count}};
  generated-parent (definition-parent) maps are per-module and folding
  chases parents strictly WITHIN the defining module; transitive
  closure roots and returns (module, decl) pairs. The §14.19 priority
  key is amended pre-outcome to SHA256 of the canonical compact-JSON
  array ["v2a:20260808", repo, module, declName] (UTF-8,
  ensure_ascii=False) — reviewer strictness adopted: JSON escaping
  length-delimits fields so quoted Lean identifiers containing ':'
  cannot re-split; plain colon concatenation could not guarantee this,
  and the prior colon form never drew any sample. Second reviewer
  strictness adopted: duplicate module records in a corpus fail closed
  (ExtractError) instead of silently overwriting decls_by_module.
  Cross-module duplicate DECL names are now legal by construction and
  regression-tested (LakeMain/LeanChecker `main`); the old corpus-wide
  bare-name duplicate check is removed as unrepresentable. FOLLOW-UP
  ADOPTED same day (2026-08-08, still pre-outcome — no k5 draw has
  ever been made): §14.21's k5 key had the identical collision
  (colon-concatenated bare fqname) and is amended to the same
  canonical compact-JSON encoding — SHA256 over ["k5:<seed>", repo,
  <target-identity...>, <unit-identity...>] with identities spliced
  flat: Lean targets AND units carry the (module, declName) pair;
  At that boundary Python kept its single module-qualified fqname (module
  path is already embedded); this is superseded by the immediately following
  amendment before any use. The prior colon form never drew anything.
- ADOPTED (G3.5 V2-a Python source-identity amendment, 2026-08-08,
  PRE-OUTCOME — the first CPU-only corpus extraction computed no model
  score and drew no §14.19 sample): the fail-closed v2 extractor stopped
  on 52 SymPy and 6 Astropy files. Postmortem classified every one as a
  syntactically valid repeated direct top-level-name condition and found zero
  syntax/CR
  failures. Python permits this in overload, singledispatch-registration,
  and compatibility patterns; a `module.name` dictionary therefore erased
  real declaration units. Adopted contract (Python extraction schema v2 ->
  v3, `v2a_python_extract_v3`; consumers reject v2): targets are a list and
  each declaration identity is `[module, name, start_byte]`; graph edges are
  sextuples containing the source and destination triples; target-coverage
  rows carry the same explicit identity; binding count, ordinal, and finality
  plus duplicate-name counts are recorded. The §14.19 priority key is
  SHA256 of canonical compact JSON
  `["v2a:20260808", repo, module, name, start_byte]`; §14.21 Python target
  and unit identities use the same triple. Neither preceding key was ever
  used to draw a Python sample. Ordinary static references resolve to the
  final source-order module-body def/class binding, explicitly as BEST-EFFORT:
  decorators, defaults, annotations, class bodies, later imports/assignments,
  alias capture, conditional rebinding, and dynamic dispatch may observe
  other temporal bindings, so §14.4's
  no-exact-Python-closure claim remains unchanged. All direct module-body
  declarations remain V2-a eligible; excluding shadowed or underscore-named
  declarations would silently redefine the frozen per-declaration population
  and discard dispatch registrations. Duplicate-stratum status is a mandatory
  fixed report column, and within each Python repo every headline V2 estimand
  is repeated excluding that stratum as a predeclared sensitivity. §3's
  every-arm near-duplicate
  exclusion handles lexically detected twins; the sensitivity handles
  same-name siblings below that threshold and identical-header query
  ambiguity. Semantic behavioral pooling still requires measured execution
  coverage of the exact target span under §14.23, so lexical finality is not
  substituted for runtime evidence.
- ADOPTED (post-G3a boundary, evidence commit 570c433; first grid
  outcomes): the Qwen2.5-Coder-0.5B sentinel is an INSTRUMENT PASS but
  provides NO POWER-LAW SUPPORT. Slurm 19904528 completed all 44/44
  frozen cells with production identity and no gaps in 00:32:48;
  dependent job 19904915 passed `sentinel-post` (44 verified cells, all
  15 phase variants, 88 raw artifacts, zero quarantine), analyzed 44
  cells with zero errors, paired five base streams with zero problems,
  and rendered the descriptive plots. Every headline base clears the
  quantitative floors (windows/docs: geant4 21/342, mathlib 25/220,
  physlib 25/170, qutip 21/183, sympy 23/139). The paired SAME-GROUP
  phase probe is positive under every phase and corpus: document-
  bootstrap 95% intervals exclude zero, with byte-weighted gains across
  phases of 0.0155--0.0160 (geant4), 0.0372--0.0394 (mathlib),
  0.0511--0.0586 (physlib), 0.0244--0.0351 (qutip), and
  0.0128--0.0158 (sympy) b/B. Thus the evaluator detects a real
  same-content benefit from more preceding context. However, all five
  headline `A*c^(-beta)+Linf` fits FAIL the frozen holdout gate; only
  2/64 quantitative strata accept, neither a headline base. Rejected
  fits emit no reportable exponents. The supported statement is
  nonparametric context gain and instrument viability, NEVER a universal
  scaling law, Lean advantage, or cross-language numeric ordering.
  Sensitivity floors are material: shuffled-minus-base BPB ranges
  +0.0038 to +0.0308; per-doc-reset-minus-base -0.0034 to +0.0440;
  second-selection-seed-minus-base -0.0365 to -0.0029. Physlib's order
  contrast retains the standing lexicographic-order caveat. Decision:
  proceed to the already-frozen G3.5 fixed-target extraction/pilot,
  whose within-target contrasts remove stream sample composition as a
  first-order confound. Its measurement implementation lands in new
  standalone files: `eval_incontext.py` and `layout.py` stay untouched,
  no Python dependency is added, and the V2-a commit must add the
  deferred `cell_done` regression in which a differing recorded
  source-tree hash remains acceptable when the current harness and
  environment identities match. G3b remains optional and
  human-approved; its ROI must be argued as descriptive within-family
  robustness, not exponent estimation. This signoff commit itself moves
  the source-tree identity, so the frozen short battery rerun remains
  mandatory before any G3b launch. No disagreement remains between the
  two agent reviews.
- ADOPTED (pre-launch, at acquisition boundary 132fb5a): measurement
  identity package — MEASUREMENT_SCHEMA_VERSION 3->4 (harness hash over
  eval_incontext.py+layout.py; canonical all-distribution software
  fingerprint incl. python runtime and torch CUDA build; GPU/driver
  informational only, never gated), committed 66-pin wheel lock with a
  python==3.12.13 runtime contract, lock-synced installs (uv pip sync
  --strict), write-once software-only freeze with REFREEZE quarantine +
  separate informational runtime notes, eval refusal before model load
  keyed on non-dev status (NOT device), end-of-run harness/environment
  re-checks, and identity gating in cell_done/battery/preflight. Cost
  recorded: the bump invalidates any pre-v4 dump; the sentinel runs
  once, at v4, after the freeze exists.
- ADOPTED (pre-implementation, same boundary): V2-a freeze additions
  DESIGN_V2 §14.12-14.20 — 16KiB headline budget; token-cap
  eligibility + equal-token sensitivity; single leak-free candidate
  universe with transitive-reverse-closure and same-SCC exclusions
  (post-hoc repair REJECTED); behavioral k5 arm, <=448-token
  eligibility, outcome classes with compile-only Python barred from
  semantic pooling, Lean sorry/axiom/native_decide bars and
  timeout=failure; joint F1 intersection-union decision rule with
  file-cluster bootstrap; frozen unit rendering (target-name-free
  banners, delimiter bytes counted); short-target exact-hash dedup
  rule; deterministic seeded-priority target sampling; physlib
  external-mathlib (lake-manifest-pinned revision) hard interpretation
  gate. The k1-vs-k3 inconsistency in DESIGN_V2 §3/§8 is resolved to
  k4 (matching E1a and the §14.2 map).
- ADOPTED (item-A RESOLUTION: follow-up PASS + re-specification):
  the follow-up falsifier PASSED with exact outcomes — F2 (fp32,
  TF32 off/highest, resolved sdpa, SDP MATH forced, chunk 512 vs
  2048): mean 3.1806e-6, p99 2.4080e-5, max 6.485e-5, repeat 0;
  CAUSALITY (bf16 production path): protected 4094 rows max EXACTLY
  0, downstream max 5.141, excluded row delta 16.406. Both targeted
  semantic-bug probes passed for q25c on the tested window:
  no shared chunked-prefill discrepancy or production-path causal leak
  was detected. The
  claim adopted is deliberately bounded: the bf16 cross-shape
  divergence is CONSISTENT WITH accumulated-KV kernel-shape numerics
  — the probes passed; the numerics mechanism is not "proven".
  Re-specification adopted per the frozen rule: (a)
  PRODUCTION_CHUNK_TOKENS = 2048 in layout.py (measurement-harness
  file; hash moves, no accepted cell predates it) — ONE chunk shape
  for every production cell, replacing the 1024-if-big ternary that
  crossed kernel shapes inside families (incl. 7B vs its own
  ladder); chunk joins the cell_done identity (field already in
  meta; acceptance tightens, no schema bump); (b) battery item A
  becomes A_fixed_chunk_semantics (§7): production-path invariants
  (repeat determinism, causality probe per family, q25c fp32/MATH
  semantic leg) with a PURE completeness/finiteness/dispatch/chunk
  fail-closed verdict; its probe helpers are imported from
  diag_item_a_followup so the implemented partition/perturbation/load
  semantics cannot drift from the falsifier; the old A_chunk_equality
  key is RETIRED; (c)
  NO bf16 cross-shape or one-shot gate anywhere — that comparison is
  a characterized non-production contrast; (d) consequence for
  interpretation: absolute BPB carries the frozen chunk shape as
  part of measurement identity; all within-grid comparisons share
  it, and cross-study comparisons must note it.
- ADOPTED (G3a scheduler-envelope repair, before any sentinel
  allocation or model outcome): the first sentinel `sbatch` was
  rejected because `mit_normal_gpu` has `MaxTime=06:00:00` while the
  shared Phase-1 script requested 08:00:00. No job was created and no
  cell artifact was written. The launcher now passes explicit
  partition-valid walltimes: 06:00:00 for every normal/L40S shard and
  the original 08:00:00 for preemptable/H200 shards (whose partition
  permits up to two days); the script default is 06:00:00. This is an
  orchestration-only repair, but the strict source-tree identity moves,
  so the short validity battery is rerun before G3a submission.
- ADOPTED (item-A FOLLOW-UP falsifier after diagnostic 19903226, still
  before any grid outcome): the first diagnostic HARD-STOPPED per its
  frozen rule — oracle, cache_position, and repeat PASSED while ALL 12
  bf16 chunk-vs-prod stability pairs FAILED. Established for q25c:
  chunked cache logic is semantically correct under fp32 EAGER; still open:
  SDPA-path semantics vs bf16 accumulated-KV shape divergence.
  Reviewer challenge ADOPTED: the proposed same-shape bf16 eager-vs-
  SDPA gate is NON-IDENTIFYING (a backend swap perturbs every layer's
  arithmetic order and the KV projections themselves, so exceeding
  numeric bounds cannot separate a mask defect from legitimate bf16
  divergence — the original item-A false dichotomy recreated); it is
  dropped entirely (not even characterization, for speed). Frozen
  follow-up (diag_item_a_followup.py), two IDENTIFYING gates: (F2)
  q25c 8192 tokens FLOAT32, TF32 off (matmul+cudnn+highest precision,
  asserted and recorded), model attention implementation GATED ==
  'sdpa' post-load, torch SDP backend EXPLICITLY FORCED to MATH
  (never inferred from dtype), production eval_window chunk 512 vs
  2048 at the PRE-INCIDENT oracle bounds (mean < 1e-4, p99 < 1e-3;
  repeat-2048 <= 1e-6) — scoped as SHARED cache/model/mask-
  construction semantics, NOT bf16 flash-kernel validation; and
  (CAUSALITY) q25c bf16 production path chunk 2048, resolved impl
  gated == 'sdpa', perturb input token p=4095 (last position of chunk
  2), protected rows 0..4093 must be unchanged (max <= 1e-6 = the
  verified determinism bound; correct causal masking makes past
  logits EXACTLY independent of future tokens — threshold-free in
  spirit), row 4094 EXCLUDED (its TARGET changed; scoring, not
  leakage), and >= one downstream row must change (> 1e-6,
  non-vacuity). Exactly 8192 tokens required fail-closed; vocab for
  the perturbation from the TEXT CONFIG vocab_size (always a valid
  embedding row). Per-position SIGNED profiles persisted (the first
  diagnostic aggregated the fingerprint away). Branches, frozen: F2
  fail -> shared chunked-prefill semantic defect (fix code, hard
  stop); causality fail -> production-kernel mask defect or vacuous
  probe (fix code / rerun, hard stop); wrong dispatch -> INVALID RUN,
  no scientific conclusion; both gates pass -> the targeted semantic-
  bug probes pass and the observed cross-family pattern is CONSISTENT
  with accumulated-KV bf16 numerics, permitting the re-specification
  branch: chunked-vs-chunked
  item A at ONE unified production chunk (CHUNK_TOKENS=2048,
  replacing the 1024-if-big ternary that crossed chunk shapes INSIDE
  families), chunk joins the cell_done identity (field already
  recorded in meta; acceptance tightens, no schema bump), and the
  --big battery mode gains 131k/32B chunk-2048 feasibility probes.
- ADOPTED (item-A incident + FROZEN diagnostic decision rule, before
  any grid outcome — no cell exists): battery 19902567 FAILED item A
  only: chunked-vs-one-shot bf16 deltas ABOVE the frozen 5e-3/5e-2
  bounds on ALL FOUR families (mean/p99: q25c .02329/.1919, q3
  .02126/.1716, q35 .01484/.1256, sc2 .01316/.1157). All non-A items
  completed (B conservation, C, D, E with exact 8 rows, and all
  identity checks). Recorded facts: the only pre-freeze end-to-end
  validation environment was M5/MPS (RESUME) — the bounds were never
  CUDA-calibrated; the uniform cross-family magnitude and small size
  MOTIVATE, but do not establish, the kernel-shape-numerics hypothesis
  over a cache/position bug (completion of non-A items does not
  distinguish the two) — the hypothesis is NOT adopted, it is TESTED.
  Frozen diagnostic
  (diag_item_a.py + slurm/diag_item_a.sbatch, bounds fixed pre-run):
  (1) production stability — all 4 families, 8192 tokens, PRODUCTION
  eval_window, bf16 chunks {512,1024,4096} each vs prod-2048 must meet
  the ORIGINAL 5e-3 mean / 5e-2 p99, and a repeat-2048 must be
  deterministic (max <= 1e-6); (2) q25c fp32 EAGER oracle at 2048
  tokens — one-shot vs chunk-512 mean < 1e-4 / p99 < 1e-3, and
  implicit-vs-explicit cache_position max <= 1e-6; (3) bf16 TRUE
  one-shot (use_cache=False — the exact battery-A comparator path) is
  CHARACTERIZATION only (production never executes that kernel shape).
  Verdict inputs are COMPLETENESS-GATED: exactly 12 alternate pairs
  and all 4 repeat families required; NaN/non-finite values fail.
  Reported per pair: signed/abs quantiles, first-chunk/
  boundary/interior strata (boundary spikes = the cache-bug
  fingerprint), and argmax agreement (only argmax IDs are retained;
  logits are not written). DECISION, frozen: ANY production-stability or oracle
  failure HARD-STOPS (treat as bug; fix code; no gate discussion);
  only if ALL gates pass may item A be RE-SPECIFIED — production-path
  invariance gate (chunked-vs-chunked at the original bounds) + fp32
  eager semantic gate + bf16 one-shot demoted to reported
  characterization — before any battery rerun. Diagnostic runs under
  current lock/freeze/source identity and writes a quarantine-on-rerun
  JSON (results_v2/diag/item_a_diag.json).
- ADOPTED (item-E infeasibility + transparency, before any E outcome —
  E aborted at eligibility determination): the pinned physlib snapshot
  has 8 source import directives / 538 files (QuantumInfo 0; only two
  files with any internal direct import, max one; optional
  `import all` support still yields zero eligible), so lite E is
  STRUCTURALLY infeasible on physlib. Adopted: (a) designated E corpus
  = mathlib (81 eligible under unchanged criteria; the unique
  alternative Lean corpus — a forced feasibility switch, not corpus
  shopping); (b) non-vacuous floor = E's sample size (8), fail-closed
  in the battery AND pinned in preflight (corpus, floor, counts all
  gated; an empty E once reached the gate); (c) E scoped as machinery
  validation only — not physlib evidence, not the §9/V2-b grounding
  pilot; (d) physlib dependency structure reserved for the ELABORATED
  V2-a extractor, which this finding validates as the load-bearing
  instrument (§2's regex-imports-as-coarse-baseline stands); (e) Lean
  import parser extended for `import all` (prep + battery); (f)
  streams_stats records per-corpus resolved dependency_edges, and §2
  discloses that physlib's full_topo order degrades to lexicographic
  path order — its order ablation is not interpretable like mathlib's;
  (g) second-pass audit: EXACT realized-row gating (an E with skips
  could pass under-filled — the 0-row masquerade at n=1; battery
  raises below E_SAMPLE rows and preflight pins n==8 with an empty
  skip list), pool sufficiency judged against bytes ACTUALLY SHOWN
  (min(closure, 16KB cap), not the full closure — full-closure
  sufficiency was a latent closure-size-correlated selection bias),
  and the live §9 sentence calling item E the design-grounding probe
  corrected (V2-b is the grounding pilot; machinery-only scope). The
  realized 81-eligible count is VERIFIED documentary-exact by a
  read-only scan of the pinned mathlib snapshot: zero indented import
  lines; every ^import...import candidate is one prose line or a
  normal import whose trailing comment contains the word "import" —
  never a second command; `import all` occurs and the amended parser
  resolves it (nothing gated depends on the 81 — gates pin corpus,
  floor, eligibility, and exact rows).
  Grid counts, floors, and the measurement harness unchanged. Note:
  `import all` is CONFIRMED present in the pinned mathlib snapshot, so
  the parser fix raises mathlib's recorded dependency_edges at the
  next re-prep; topo order and stream bytes change ONLY where a newly
  resolved edge contradicts the previously realized order within the
  selected subset — either outcome is valid, and doc_set_sha256 is
  invariant by construction (selection is order-independent).
  Deterministic re-prep at the next fix_cluster run re-locks stream
  identity in all cases, and no accepted cell predates it.
- ADOPTED (incident + amendment, before any accepted battery item or
  grid cell): battery job 19900858 FAILED CLOSED at its first Triton
  JIT compile — the venv was built on the OS /usr/bin/python3.12,
  whose headers (/usr/include/python3.12) do not exist on ORCD
  compute images. Fix adopted, A-over-B (header/CPATH injection
  REJECTED: it marries one build's binary to another build's
  pyconfig.h and leaves the interpreter silently OS-mutable): venv
  rebuilt on a uv-MANAGED CPython 3.12.13 on POOL;
  UV_PYTHON_PREFERENCE=only-managed + UV_PYTHON_INSTALL_DIR make a
  system interpreter structurally unselectable; fix_cluster verifies
  venv identity (managed base + Python.h via INCLUDEPY) fail-closed
  and idempotently, with migration requiring EXPLICIT REBUILD_VENV=1
  (old venv quarantined) alongside the existing REFREEZE=1. The
  environment fingerprint gains a python-binary line (sha256 of the
  resolved base interpreter binary) — the incident showed two builds
  of '3.12.13' are different environments invisible to the version
  string. Composition change only: no MEASUREMENT_SCHEMA_VERSION bump
  (meta still carries one fingerprint hash), no accepted artifact
  predates it, the measurement harness (eval_incontext.py, layout.py)
  is untouched, and Triton is never disabled (that would change the
  kernel execution path and the measurement numerics).
- ADOPTED (at the first G1 run on the cluster, before any battery/grid
  outcome): arm-feasibility amendment. Realized feasibility
  (deterministic dates/bytes; prior engineering smokes remain disclosed
  in §1, but no battery/grid or model-comparison outcome was seen):
  masking viable = {physlib, mathlib} at c2024_11 and
  c2025_04, {physlib} at c2026_02; all-new matched = {physlib, mathlib,
  sympy, geant4} at c2024_11/c2025_04, {physlib, mathlib} at c2026_02.
  Decisions: (a) frozen §5 feasibility manifest + arm-feasibility-frozen
  exact-set preflight check at every science gate (g3a re-checks its
  own c2024_11 row); (b) clean-matched-cells becomes exact-set-per-tag
  vs the manifest — the >=3 scalar is superseded, NOT lowered: two
  same-language cells cannot support a cross-language claim at any
  threshold, so narrowness is scoped, never gated away; (c) Qwen3.5
  cross-language contamination-controlled claims BARRED (§6) pending a
  corpus amendment through its own gate; (d) floors (150KB matched;
  20 docs + 300KB masking) UNMOVED — geant4 c2026_02 all-new (131KB)
  and mathlib c2026_02 masking (167,496B) recorded as near-misses;
  (e) mathlib masking infeasibility explained as seeded-sample dilution
  (5.0MB corpus-wide vs 167KB in-stream), logged as a limitation with
  recency-stratified sampling as a future gated sensitivity;
  (f) disk-headroom now stats BASE (the autofs parent 0-stats before
  automount — the check measured the automounter) with inode detail;
  (g) grid identity 216/152/44 and all expected-cells manifests
  unchanged. G1 records feasibility without blocking the Qwen2.5
  sentinel; G3a hard-requires its own family's row.
- ADOPTED (pre-pilot, adversarial design review of V2-a): DESIGN_V2
  §14.21-14.28 — k5 per-(target,seed) hash priorities with seeds 1-2 as
  NLL-only sensitivity (reviewer's multi-seed-everywhere form DECLINED:
  one independent draw per target already disperses; recorded); blinded
  V2-b pilot governance with mechanical caps (N in [200,400] from
  blinded nuisance precision; n = smallest of {8,16,32} with
  arm-anonymous pass-probability reliability >= 0.8; either cap
  unmeetable -> F1 declared INFEASIBLE, never redesigned; only the
  k4 aggregate exposed, solely for the frozen floor/ceiling tier
  rule); four never-pooled outcome classes with baseline-pass and
  measured-coverage requirements and the UNIFIED §12 Lean
  forbidden-escape list; five behavioral arms with corrected §9 cost
  arithmetic, detection over all five arms, repair only {k1,k4} at
  n=4, decoding-level no-early-stopping + deterministic post-hoc
  extraction (supersedes §10 stop wording); target-equal primary
  aggregation; F1 rejection arms Bonferroni at 0.025 (FWER <= 0.05
  under union rejection; support IUT unchanged); k6-realistic
  reverse-deps-allowed labeled sensitivity; k4x via the §14.1 rule
  over the combined internal+external graph; exact-B truncation kept
  PRIMARY (whole-unit-primary REJECTED as reintroducing directional
  byte confounds) with per-cell truncation reporting and a
  max(0.005 b/B, 50% of |primary|) divergence gate. Final consistency
  pass (same boundary): §8/§14.16/§14.25 harmonized — rejection at
  one-sided 97.5% upper bounds, support at one-sided 95% lower bounds;
  §5/§10/§14.15 harmonized to pilot-selected n with seeds 0..n-1;
  pilot reliability made empirically identifiable (up to 32 masked
  pilot completions; repeated half-splits with the SPEARMAN-BROWN
  correction per candidate n — a raw 32-draw split estimates n=16, not
  n=32); N = MAXIMUM requirement across E1a/E1b/E2; floor/ceiling
  tier rule made DIRECTIONAL (<0.05 one capability tier UP, >0.95 one
  tier DOWN, missing adjacent tier -> F1 infeasible for that slot;
  "never by condition-contrast direction"); confirmatory F1 restricted
  to SEMANTIC outcome strata (lean-theorem-proof,
  python-semantic-covered; per (repo, class); def-typecheck and
  compile-only never enter; the k4 floor/ceiling aggregate uses the
  applicable semantic stratum); mutation repair FROZEN to the 1.5B
  sentinel (expansion requires its own preregistered gate); pass@8
  FIXED as the cross-tier metric for every n >= 8 (pass@n descriptive
  only; F1 remains c/n); §9 costs corrected (k6-realistic sensitivity
  included; Lean checks ~12.8k at n=8 to ~36.8k at n=32); whole-unit
  truncation gate computed on the common eligible-target set.
- ADOPTED (pre-outcomes, at manifest adoption): arXiv arm DEMOTED to
  optional preserved artifact + separately-gated format diagnostic; all
  9 sentinel / 43 total core cells removed BEFORE any outcomes existed
  (grids 259->216, sentinel 53->44, small/mid 183->152); budget math
  decoupled (CORE vs OPTIONAL corpora); tri-state integrity gating
  (absent non-blocking; present must validate, failure blocks G1);
  synthetic LaTeX battery probe added so format plumbing coverage never
  depends on the optional corpus. The one honest cost, recorded: the
  core grid now has no non-code reference point, so "curve shape is
  code-specific" observations require an explicitly approved diagnostic
  run.
- ADOPTED (arXiv v4 exception): 2301.00502 v1/v2 are PDF-only; the
  inherited file is byte-identical to v4 (75,800B, sha aba85b52…,
  revision 2023-03-31, pre-all-cutoffs) and is pinned to v4 as the
  single documented exception. For this file the "extant at submission"
  reading does NOT hold — its content is the March 2023 revision;
  realized SUBMISSION ranges are unchanged. Migration re-ran end-to-end
  from the corrected pin set with zero per-file interventions.
- ADOPTED (sequencing): G3b battery-rerun-at-current-hash rule (above);
  source_tree_hash whitelist never weakened; PAIRED_SCHEMA_VERSION
  introduced for the V2 driver.

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
closure; window position/content confound (DIAGNOSED, not eliminated,
by the same-group phase pairs; shuffles probe order, not position);
git dates bound only in-repo publication; exact-reference NLL misses
set-valued correctness (v2 pass@k arm); Phase 2 budgets are fixed-D.
