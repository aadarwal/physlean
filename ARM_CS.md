# ARM_CS — corpus-statistics & from-scratch scaling arm (Cagnetta test)

Status: **DRAFT v0 — awaiting independent fresh-context adversarial review**
(the campaign's standard adoption path: reviewer verdict ADOPTABLE /
FIX-FIRST with blockers/concerns/nits; adoption commit records the verdict
and appends the PREREG §13 registration entry). Rationale and the paper
mapping live in THEORY.md; this file freezes the design. Nothing here reads
a model outcome before its gate.

Relationship to the frozen program: NEW STANDALONE FILES ONLY
(`lang_stats.py`, `analyze_cs.py`, `train_scratch.py` additive flags,
`results_cs/` namespace). `eval_incontext.py`, `layout.py`,
`analyze_v2.py`, the dependency lock, and every `results_v2/` artifact are
untouched. This arm makes NO repository-context, security, or
"software scaling law" claim; its quantities are corpus statistics and
from-scratch data-scaling exponents.

## 1. Questions and estimands

Per corpus s (per-repo strata) and pooled per language L ∈ {lean, python,
cpp, latex-reference}:

- **β̂_corr(L)** — correlation-decay exponent: ‖C(n)‖_op ≍ n^(−β) where C(n) is
  the lag-n byte–byte covariance matrix (256×256), doc-interior pairs only.
  Secondary: Frobenius-norm decay; top-10 singular values (spectral
  breadth); broken-power-law break locations; periodicity peak lags.
- **Ĥ_k(L)** — small-k conditional entropies (bits/byte), k ≤ 6, via k-gram
  chain rule (plug-in + Miller–Madow), doc-interior.
- **γ̂(L)** — entropy-decay (Hilberg) exponent of the from-scratch byte-LM
  n-gram loss curve 𝓛_n at the largest data rung, initial-decay estimator.
- **Ĥ_∞(L)** — grid-search asymptote of the same curve.
- **α̂_D(L)** — data-limited exponent from the lower envelope of L(P)
  across context lengths T.
- **The theory test**: α̂_D vs the zero-parameter prediction γ̂/(2β̂_corr), per
  language, with propagated uncertainty (paper Eq. 56); plus per-language
  scaling-collapse quality of 𝓛_n·n^γ̂ vs P/n^(2β̂).
- **Transfer diagnostic (descriptive only)**: γ̂_transfer from the EXISTING
  G3a 0.5B per-position dumps (5 corpora), same estimators; compared with
  from-scratch γ̂ per corpus. No new pretrained runs; no cross-family
  claims; G3 numeric cross-language inference stays barred (PREREG §6).

Hypotheses on the trail (registered at adoption): H2 β_corr(lean) <
β_corr(python) (slower correlation decay in formal code); H3 α̂_D ≈
γ̂/(2·β_corr) per language (theory transfers) with predeclared failure
readings (THEORY.md §3/§6); H4 α_D(lean) > α_D(python) if H2 and the γ
ordering both hold.

Relationship to sibling lanes (following DIRECT_SCALING_STUDY §0's
pattern): V2 (DESIGN_V2.md) measures repository-context sufficiency on
fixed targets; DIRECT (DIRECT_SCALING_STUDY.md, P0-frozen 2026-08-09,
branches codex/direct-scaling-*) tests the essay's context-position proxy
with pretrained checkpoints. Both declare every quantity model-relative
(L = H + KL) and neither trains models nor measures corpus-intrinsic
statistics. This arm shares corpora and evidence discipline with both and
shares NO estimand and NO claims: its confirmatory path contains no
pretrained checkpoint anywhere. Notation: this arm's correlation exponent
is always written **β_corr** in outputs and prose, never bare β, to avoid
collision with DIRECT's β_position_*/β_paired_* coefficients; the entropy
exponent is always **γ**, never "beta" (the legacy G3 column name).

## 2. Data

- Collection rule: identical to `prep_pools.py` (same POOLS map, exclusion
  dirs, UTF-8 validity, ≥64-byte floor, SHA1 content dedup) but WITHOUT
  caps/stride, WITH per-file repo labels and doc boundaries; the collector
  imports `prep_pools.POOLS`/`EXCLUDE_DIRS` so the two cannot drift.
- Doc discipline: every pair/k-gram statistic uses within-document pairs
  only (mask by doc id at each lag); no cross-file leakage into C(n).
- Strata: per-language pooled (headline) + per-repo where repo ≥ 5 MB.
- Sensitivities: (a) matched-P — seeded (seed 13) whole-doc subsample of
  every language to the smallest language total, same estimators; (b)
  tokenizer scale — shared 8k BPE trained on the union pool, C(n) in token
  units (CS-1b, cluster; byte-level remains headline since the from-scratch
  models are byte-level and γ/β must share units).

## 3. Frozen estimator constants (CS-1, CPU)

- Lag set: {1..32} ∪ round(logspace(32→8192, 20 points)), deduped.
- Marginals: left marginal over valid left positions, right marginal over
  valid right positions at that lag (paper App. B, Eq. 44–46 analog).
- Noise floor: within-document byte-shuffle surrogate (seed 4242, 1 rep,
  same lag set, same masking) AND the analytic √(σ²·log V / N_pairs) bound;
  a lag is VALID iff ‖C(n)‖_op ≥ 3× max(floors). β̂_corr fits use the
  maximal contiguous valid prefix range.
- β̂_corr: OLS on (log n, log‖C(n)‖_op) over the valid range; uncertainty =
  bootstrap over lag points (1000 resamples) AND over documents (200
  resamples of the doc set, recomputing C(n)) — report both.
- Broken power law: two-segment continuous piecewise-linear scan over
  breaks at valid lags; adopt the break iff ΔBIC ≤ −6; then report
  (β_corr_short, β_corr_long, n_break) alongside the global β̂_corr.
- Periodicity: peaks flagged where |residual| > 2×MAD(residuals); peak
  lags reported (expected: code line lengths; Lean multi-byte UTF-8
  harmonics at n ∈ {2,3}).
- H_k: plug-in k-gram entropies via chain rule with Miller–Madow
  correction; report distinct-context counts and the correction magnitude
  (estimator bias grows with k; k ≤ 6 only, and any k where the
  correction exceeds 0.02 b/B is flagged unreliable).

## 4. CS-2 training grid (from-scratch rungs; GPU, pilot scale)

- Model: 10m byte-GPT (`train_scratch.py`), byte vocab 256, 1 tok = 1 B.
- Rungs: P = pool × {1/64, 1/32, 1/16, 1/8, 1/4, 1/2, 1} (whole-doc seeded
  subsets, nested: each rung's docs ⊂ next rung's).
- Context lengths: T ∈ {512, 4096} bytes (both required for the envelope
  α̂_D); seeds {0,1,2} at rungs ≤ 1/16, seed {0} above.
- Hyperparameters: lr × epochs grid at the SMALLEST rung
  (lr ∈ {3e-4, 1e-3, 3e-3} × epochs ∈ {1, 2, 4}); winner carried forward
  rung-to-rung with a one-step spot-check at each rung (Kim-et-al local
  optimality, as in the paper App. E); batch fixed by memory.
- Capacity guard (paper protocol): at the largest rung, a 30m model must
  not beat the tuned 10m by > 0.01 b/B on val; if it does, the affected
  language escalates to 30m for its top rungs (recorded, not silent).
- Outputs per run: final-val per-position NLL CSV (existing schema) +
  val-loss ledger → results_cs/runs/.
- Cluster: single-GPU L40S jobs, `mit_normal_gpu`, resumable; ~4 langs ×
  7 rungs × 2 T × (1–3 seeds) ≈ 70–90 runs, minutes→~2 h each — well
  inside one night of the normal partition.

## 5. Reveal sequencing (order-of-operations discipline)

1. CS-1 corpus statistics computed and COMMITTED (β̂_corr per language) before
   any CS-2 training begins. (Statistics precede model outcomes; no blind
   needed, but the commit fixes β̂ against later temptation.)
2. CS-2 runs; from the LARGEST rung only, γ̂ and Ĥ_∞ are estimated and the
   prediction α_D^pred = γ̂/(2β̂_corr) per language is COMMITTED in a
   registration commit BEFORE any cross-rung envelope/L(P) analysis is
   executed or plotted (`analyze_cs.py` enforces the two-phase split:
   `--phase gamma` refuses to read more than the largest rung;
   `--phase envelope` refuses to run unless the registration commit for
   that language exists and is clean in git).
3. Only then is α̂_D revealed and compared. The whole first pass is
   labeled EXPLORATORY regardless (same data trained the estimates);
   the confirmatory replication path is a second, disjoint corpus set
   per language (e.g. The Stack v2 slices for python/cpp; Lean Lake
   packages held out of the lean pool) with the SAME frozen constants.

## 6. Gates

- **CS-0 adoption**: fresh-context adversarial review of this document +
  THEORY.md; verdict + fixes recorded in the adoption commit; PREREG §13
  entry appended at adoption (PREREG changes are review boundaries).
- **CS-1 stats**: `lang_stats.py` run (local first look, cluster
  canonical with full clone set); committed JSON/CSV + summary; β̂_corr
  registration commit.
- **CS-1b**: BPE-tokenized C(n) sensitivity (cluster, CPU).
- **CS-2 pilot**: smallest-rung HP grid + one full rung ladder for ONE
  language (lean), reviewed for optimizer health (val curves monotone,
  no divergence) before the multi-language fan-out.
- **CS-3 full grid**: the ≈90-run fan-out. Compute is modest but this is
  the arm's main GPU spend; launches only after CS-2 pilot review.
- **CS-4 analysis**: two-phase analyze_cs per §5; figures; writeup
  integration.

## 7. Compute & storage

CS-1: CPU-only, ≈30–60 min/language (local M5 or one cluster CPU node);
outputs < 5 MB. CS-2/3: ≈70–90 single-L40S runs; checkpoints discarded,
only NLL CSVs + ledgers kept (< 200 MB total on POOL). No preemptable
partitions (trainings not requeue-safe).

## 8. Threats and predeclared readings

- C(n) non-power-law/oscillatory for code → fit regimes + peaks are the
  finding, not a failure; β_short vs β_long both enter the α_D prediction
  as a sensitivity band.
- H_n non-power-law (curvature in log-log) → report curvature diagnostic;
  the collapse scan over (γ, β) is the sharper instrument and can reject
  the ansatz family wholesale — that outcome = "code sits outside the
  paper's universality class", reportable.
- Slow-learning regime (α̂_D = δ < γ/(2β̂)) → measure δ_n (paper §5
  protocol) and report the regime per language.
- Byte-level vocabulary artifacts (UTF-8 harmonics in Lean) → peaks
  reported; CS-1b BPE sensitivity bounds the tokenization-scale effect.
- Repo-composition confound in "language" pools → per-repo strata always
  shown; language claims labeled with composition.
- Undertuned rungs fake curvature in L(P) → §4 HP protocol + optimizer
  health review at CS-2.
