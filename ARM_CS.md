# ARM_CS — corpus-statistics & from-scratch scaling arm (Cagnetta test)

Status: **DRAFT v1 — post-review revision awaiting re-review.** v0 received
an independent fresh-context adversarial review (verdict FIX-FIRST: 10
blockers, 6 concerns, 6 nits; reviewer transcript summarized in the v1
commit). v1 addresses every blocker as recorded per-section below.
Adoption = re-review verdict ADOPTABLE + adoption commit carrying the
PREREG §13 registration entry and the §0 G6 statement.

Rationale and the paper mapping live in THEORY.md; this file freezes the
design. The paper: Cagnetta, Raventós, Ganguli, Wyart, ICML 2026,
arXiv:2602.07488 ("the paper").

## 0. Governance position

- **G6 fulfillment (review B1).** PREREG §10 blocks Phase 2 (G6) pending a
  redesign with "smaller N and/or much more D, >= 3 seeds, and an explicit
  statement of what the fixed-budget comparison identifies." This arm IS
  that redesign: fixed SMALL N (10m primary; 30m only as capacity guard),
  the data-limited multi-epoch regime (the paper's setting — model size
  chosen so it does not constrain), **seeds {0,1,2} at every rung**, and
  the explicit estimand statement of §1 (data-limited exponent α_D at
  capacity-unconstrained N; no L(N,D) surface is claimed). Adoption
  formally unblocks G6 as CS-2/CS-3, preserving the human GPU-scale gate
  at CS-3. Human compute authorization for this program recorded
  2026-08-27 (user: "do whatever you think is best … feel free to
  parallelize on the cluster"); the adoption commit quotes it.
- **Siblings.** V2 (DESIGN_V2.md) measures repository-context sufficiency
  on fixed targets; DIRECT (DIRECT_SCALING_STUDY.md, P0-frozen, pinned at
  commit 4a49240) tests the context-position proxy with pretrained
  checkpoints. Both declare every quantity model-relative (L = H + KL);
  neither trains models nor measures corpus statistics. This arm shares
  corpora and evidence discipline and shares NO estimand. Notation:
  **β_corr** (never bare β — DIRECT owns β_position_*/β_paired_*);
  **γ** (never "beta").
- **Frozen instruments untouched**: `eval_incontext.py`, `layout.py`,
  `analyze_v2.py`, `requirements-cluster.lock`, everything in
  `results_v2/`. This arm's files: `lang_stats.py`, `analyze_cs.py`,
  `cs2_pools.py`, `cs2_launch.py`, `slurm/cs2_rungs.sbatch`, additive
  flags on `train_scratch.py` (defaults preserve Phase-2 behavior), and
  the `results_cs/` + `data/cs2/` namespaces.
- **CS source-clean rule (review B4)**: before any CS measurement job,
  `git status --porcelain -- . ':(exclude)results_v2' ':(exclude)results_cs'`
  must be empty; every CS artifact records the commit, a dirty flag, and
  input-manifest hashes (§3, §4). CS outputs are mode-suffixed and
  write-once (`--force` required to overwrite).
- **Pre-adoption exposure disclosure (review B4; PREREG's own pattern)**:
  before this v1 was frozen, the instrument's selftest, a 3MB/language
  quick smoke, and one full LOCAL lang_stats pass (incomplete python pool)
  had been run and observed, and one 0.77MB lean training smoke (rung
  1/64, ctx 512, val 8.0→5.43 b/B). Observed then: pooled local
  β_corr ≈ 0.60 (lean) / 1.00 (python) / 0.32 (cpp) / 0.33 (latex) under
  the v0 estimator. Every CS-1 claim therefore rests on the CANONICAL
  cluster run under the v1 estimator, and the whole first pass is labeled
  exploratory regardless (§5); these observations are disclosed, not
  erased.

## 1. Questions, estimands, non-claims

Per corpus scope (pooled language mixture; per-repo strata >= 5 MB):

- **β̂_corr** — DOC-INTERIOR sequential correlation decay: the declared
  estimand is the decay of ‖C(n)‖_op computed from within-document byte
  pairs, i.e. sequential structure net of document identity. The
  between-document composition covariance ‖Σ_d w_d (p_d − p̄)(p_d − p̄)ᵀ‖_op
  is REPORTED alongside as its own quantity, never folded into β_corr
  (review B7: this is an estimand declaration, not a bias claim).
- **Ĥ_k** — chain-rule conditional entropies, k ≤ 6, doc-interior,
  Miller–Madow-corrected (§3).
- **γ̂, Ĥ_∞** — intrinsic estimands of the corpus (entropy-decay exponent
  and entropy rate), estimated MODEL-ASSISTEDLY (review C2) from the
  largest-rung from-scratch byte models under the frozen §6 estimator;
  reportable only if the §6 convergence rule passes.
- **α̂_D** — data-limited exponent from the rung ladder (§6).
- **The theory test (H3)**: per language mixture, compare α̂_D with
  α_pred = γ̂/(2β̂_corr). Frozen criterion: SUPPORTED iff α̂_D's 95%
  interval lies within [α_pred − Δ, α_pred + Δ], Δ = max(propagated 95%
  half-width via THEORY.md Eq. 56 analog, 0.03); REFUTED iff the two 95%
  intervals are disjoint AND the point gap exceeds Δ; else INDETERMINATE.
  All three are real outcomes.
- **H2**: β̂_corr(lean pool) < β̂_corr(python pool), doc-block-bootstrap
  95% intervals disjoint. Registered as a claim about THESE locked corpus
  mixtures; any "language" phrasing is exploratory (review C6; cpp is
  single-repo and is always labeled so).
- **H4** (conditional on H2 and the γ̂ ordering): α_D(lean pool) >
  α_D(python pool), same interval rule. Uses INTRINSIC CS-2 γ̂ only —
  never any legacy G3 quantity (review B3).
- **Transfer diagnostic** (descriptive, per-corpus): γ̂_transfer from the
  existing G3a 0.5B dumps under the §6 estimator, which may return "no
  reportable exponent" (G3's own holdout verdict stands). NO cross-corpus
  or cross-language ordering is read from it (review B3); PREREG §6's bar
  on G3 numeric cross-language inference binds here.
- **LaTeX arm (review B2)**: the raw arXiv TeX bundle is a self-budgeted
  FORMAT DIAGNOSTIC per PREREG §2/§13 — excluded from matched-P, excluded
  from every formality contrast and from H2/H3/H4. No Lean-vs-prose claim
  is made from it. (A genuine prose corpus would require a PREREG
  amendment; none is made here.)
- Non-claims: nothing here is a "software/codebase scaling law"; no
  security claim; no confirmatory language-level claim (repo/domain/
  ecosystem confounds remain; review C6).

## 2. Data

- Collection: identical rule to `prep_pools.py` (shared collector import;
  dedup, exclusions, UTF-8, ≥64 B), ALL files, repo labels + doc
  boundaries kept.
- Scopes: pooled per language; per-repo strata ≥ 5 MB; matched-P
  sensitivity over {lean, python, cpp} ONLY (seeded whole-doc subsample,
  seed 13, to the smallest of the three; latex excluded per §1).
- **Common-support sensitivity (review B7)**: all pair statistics
  recomputed on the sub-corpus of documents ≥ 8192 B, so the document
  mixture is IDENTICAL at every lag ≤ 4096; divergence between headline
  and common-support β̂_corr is reported, and where they disagree beyond
  the doc-block CI the common-support value is primary.
- **Nested-subset sensitivity (review C4)**: one independent rung ladder
  (rung seed 31 instead of 29) for lean; actual rung byte totals (from
  the manifest, not nominal fractions) enter every analysis.
- BPE robustness (CS-1b, cluster): shared 8k BPE on the union pool,
  token-level C(n); byte-level remains headline (units must match CS-2's
  byte models).

## 3. CS-1 estimator freeze (v1)

- Lag set: {1..32} ∪ round(logspace(33 → 8192, 20 points)), deduped.
- C(n): doc-interior pairs; marginals from the masked left/right endpoint
  sets at that lag; op norm + Frobenius + top-10 singular values.
- **Floors (review B7)**: FIVE within-document permutations (seeds
  4242..4246), identical masking and estimator; floor(n) = max of the
  five op-norms; a lag is VALID iff ‖C(n)‖_op ≥ 1.5 × floor(n). The
  permutation spread is reported. The analytic bound
  2√V·√(p_max·q_max/N_pairs) is a reported diagnostic only.
- **Fit window (review B7, selection-bias fix)**: window = [1, n_max]
  where n_max is the last lag before the first run of ≥3 consecutive
  INVALID lags. ALL lags in the window enter the OLS — valid and invalid
  alike (dips are data; nothing inside the window is dropped on its
  outcome).
- **Adequacy gate**: no β̂_corr is reported unless n_max ≥ 10 (≥1 decade)
  AND window OLS R² ≥ 0.7; otherwise the scope reports "no reportable
  β_corr" with its curve (mirrors G3's fit discipline).
- **Run-length diagnostic**: the Frobenius-norm slope β_corr_fro over the
  SAME window is always reported next to the op-norm β̂_corr. The op norm
  is a max statistic and can be dominated by long constant byte runs
  (whitespace/heavy-tail segments — demonstrated on a Pareto renewal
  synthetic in the selftest); op-vs-fro slope divergence beyond the
  doc-block CI flags run-length domination, and the fro slope is then
  primary for that scope (recorded, not silent).
- **Broken law (review B8)**: continuous hinge
  y = a + b₁·min(x − x₀, 0) + b₂·max(x − x₀, 0), x = log n, knot x₀
  gridded over window lags; 4 parameters; adopted iff ΔBIC ≤ −6 vs the
  single line; (β_corr_short, β_corr_long, n_break) reported.
- **Peaks (review B8)**: |residual| > 2 × (1.4826 × MAD) from the single
  OLS line over the window; peak lags reported with sign. Peaks are a
  GATE-INDEPENDENT diagnostic — reported even when the adequacy gate
  withholds β̂_corr (oscillation-dominated scopes are exactly where they
  matter).
- **H_k (review B8)**: H_cond(k) = H_k − H_{k−1} (joint k-gram entropies,
  doc-interior); Miller–Madow-corrected conditional
  H_cond_mm(k) = H_cond(k) + (m_k − m_{k−1})/(2N ln 2) with m_k the
  distinct joint k-gram count and m_{k−1} the distinct context count;
  UNRELIABLE iff |(m_k − m_{k−1})/(2N ln 2)| > 0.02 b/B. Corrected values
  are what the summary exports.
- **Uncertainty (review B8)**: document-BLOCK bootstrap, 500 blocks (docs
  hashed to blocks by seeded shuffle), 200 resamples with block-resample
  indices fixed across lags (coherent curves), applied to pooled, matched,
  AND per-repo scopes (100 resamples for strata); reported explicitly as
  a block bootstrap. Lag-point bootstrap (1000) secondary.
- **Provenance (review B4)**: every output records git commit + dirty
  flag + per-scope document-manifest SHA256 (over sorted doc SHA1s) +
  the estimator constants; quick-mode writes `*.quick.json` (never the
  canonical name); existing outputs are never overwritten without
  `--force`.

## 4. CS-2 training freeze (v1)

- Model: 10m byte-GPT (`train_scratch.py`), vocab 256, 1 tok = 1 B.
- Context arms: T = 4096 (PRIMARY) and T = 512, trained separately. The
  learned-positional-parameter delta (~1.15M) is disclosed; sensitivity
  (review B9): the T=4096 lean model evaluated with 512-byte doc-reset
  windows vs the T=512 lean model, reported side by side. α̂_D's primary
  curve is the T=4096 arm; the envelope across both T is a sensitivity.
- Rungs: nested whole-doc byte-prefix boundaries (cs2_pools manifest),
  fractions {1/64 … 1}; ACTUAL bytes recorded and used in fits.
- **Seeds {0,1,2} at EVERY rung** (review B1); seed is the torch/np init
  and batch-order seed.
- **Eval (review B9)**: the final per-position NLL dump uses DOC-RESET
  windows from the val manifest (`--val-manifest`): every document scored
  from its own start; positions are within-document context lengths; doc
  id recorded. L_n and L(P) are therefore doc-interior, matching §1's
  estimand declarations. (Training batches still cross concatenation
  joins — a standard-practice model-side choice, disclosed, affecting
  the model not the estimator.)
- **HP state machine (review B9, frozen)**: metric = final val b/B
  (doc-reset), seed 0, T = 4096. Rung 1: full grid lr ∈ {3e-4, 1e-3,
  3e-3} × epochs ∈ {1, 2, 4}. Rung r > 1: evaluate incumbent plus
  neighbors {lr×3, lr/3} × {epochs, epochs×2} (5 runs); the winner is the
  new incumbent. Seeds 1–2 and the T=512 arm reuse the per-rung
  incumbent. All HP runs are recorded; none is deleted.
- **Capacity guard (review B9)**: at the largest rung, a tuned 30m run
  (seed 0, T=4096, incumbent HP with the rung-1-style neighbor check)
  must not beat 10m by > 0.01 b/B; if it does, the language gets a
  COMPLETE separate 30m ladder (all rungs, all seeds) and the 10m ladder
  is reported as the undersized arm — curves are never spliced.
- Grid arithmetic (review C5): per language ≈ 9 + 6×5 HP runs + 7×2×3
  ladder runs + 1 capacity run ≈ 82; × 3 matched languages + latex
  diagnostic ladder (seeds {0,1,2}, no formality claims) + the lean
  rung-seed-31 sensitivity ladder ≈ **~350 runs**, each minutes→~2 h on
  one L40S. `mit_normal_gpu`, array-throttled.

## 5. Sequencing (v1 — what is and is not blind; review B10)

0. `analyze_cs.py` is frozen, selftested, and COMMITTED before any ladder
   run; its constants are §6's.
1. CS-1 canonical run (cluster, full clones, v1 estimator) committed;
   **β̂_corr registration commit**: all languages simultaneously, binding
   the lang_stats artifact hash, code commit, and constants.
2. CS-2 runs (HP walk + ladder + capacity guard).
3. **γ̂ + prediction registration commit** BEFORE the envelope phase:
   `analyze_cs.py --phase gamma` reads ONLY the top-two-rung artifacts
   (top rung for the estimate, second for the §6 convergence rule),
   writes the per-language {γ̂, Ĥ_∞, β̂_corr, α_pred} registration with
   input hashes; the commit is pushed. `--phase envelope` refuses to run
   unless that registration file is committed, clean, and byte-identical
   to HEAD's blob.
4. Envelope + collapse (§6), comparison under §1's frozen criterion.
5. **Honesty statement**: per-rung HP tuning and training logs expose val
   losses along the way, so steps 2–4 are an ORDERING discipline, not
   blindness; the registration commits bind what was derived from what,
   and the ENTIRE first pass is EXPLORATORY by declaration. The
   confirmatory pass is a disjoint-corpus replication with this exact
   frozen pipeline; its corpora are named and manifested at CS-3 (python/
   cpp: held-out Stack-v2 slices; lean: Lake packages disjoint from the
   pool) — "named at CS-3" means no confirmatory claim exists until that
   registration exists.

## 6. Analysis freeze (v1; reviews B5, B6)

All estimators below are THIS ARM'S (adapted from the paper's protocols
but frozen here; the paper's own γ protocol fits a fixed small-n window of
the largest-P curve and grids per-n asymptotes along the P axis — ours
differ and are not attributed to it).

- **L_n**: mean doc-reset NLL at within-doc position n (1 tok = 1 B),
  averaged over seeds; per (language, T, rung).
- **γ̂ / Ĥ_∞** (largest rung, T=4096): window n ∈ [4, 64]. Convergence
  rule: seed-mean L_n of the top rung and of the second rung must agree
  within 0.02 b/B over the window, else "γ̂ not reportable — curve still
  data-limited". Estimator: grid H ∈ [0, min_n L_n] step 0.005; OLS of
  log(L_n − H) on log n over the window; H* maximizes R²; γ̂ = −slope,
  Ĥ_∞ = H*. Uncertainty: seed triplet spread × window sensitivity
  ([4,32] and [8,128] refits); reported as an interval envelope.
- **Collapse (review B5)**: PRIMARY is the shifted form
  (L_n(P) − Ĥ_∞)·n^γ̂ vs P/n^(2β̂_corr) over n ∈ [4, 64] × all rungs;
  the raw published form (L_n·n^γ) is a labeled replication sensitivity
  only. Collapse quality metric: interpolate each rung's shifted curve
  onto a 20-point common log grid; metric = mean cross-rung variance ÷
  variance of the pooled master curve (smaller is better); reported as a
  DESCRIPTIVE number with a (γ, β) sensitivity sweep reproducing the
  paper's qualitative deterioration figures. No joint (γ, β) estimator
  claim (the paper defines none; review B6).
- **α̂_D**: PRIMARY = OLS slope of log(L(P) − Ĥ_∞) vs log P, where L(P)
  = seed-mean overall doc-reset val b/B at T=4096, over rungs with
  L(P) − Ĥ_∞ ≥ 0.02 (asymptote-contaminated rungs excluded by this
  frozen rule, not by eye). Sensitivities: envelope across T; first-m
  sweep (m = 4..7); leave-one-rung-out spread; seed spread; RAW
  (unshifted) envelope slope as the replication sensitivity. If fewer
  than 4 rungs survive the shift rule, α̂_D is "not reportable".
- **Regime gates (review B6)**: (a) fast-learning check — δ_n per the
  paper's §5 protocol (grid H_n along the P axis per n ∈ {1..12}; δ_n =
  −slope of log(L_n(P) − H_n)); report min_n δ_n against γ̂/(2β̂_corr);
  if min δ_n < γ̂/(2β̂_corr), the operative prediction becomes
  min{δ, γ/2β} (Eq. 40) and H3 is evaluated against it (with the
  boundary-case log correction noted); (b) horizon check — verify
  n*(P_max) ≪ T via the CS-1 floor-crossing constant (P*_n ≈ n^{2β̂}
  scaled at the measured detectability point); (c) capacity check — §4's
  guard. Each gate's failure reading is predeclared in §8.
- **δ-report**: δ_n values and their fit windows are part of the arm's
  outputs (they are the paper's second mechanism and diagnose WHERE code
  deviates, which is a finding either way).

## 7. Gates

- **CS-0 adoption**: this v1 re-reviewed (fresh-context); verdict +
  fixes recorded in the adoption commit; PREREG §13 entry + §0 G6
  statement land there.
- **CS-1**: canonical cluster stats under v1 + registration commit.
- **CS-1b**: BPE sensitivity (cluster, CPU).
- **CS-2**: HP walk + pilot ladder (lean) reviewed for optimizer health;
  then the full fan-out (§4). Runs under the CS source-clean rule.
- **CS-3**: replication/scale step — HUMAN GATE (named disjoint corpora
  with manifests; any 30m escalation ladders; any model-family widening).
- **CS-4**: analysis per §6; figures; writeup integration.

## 8. Threats and predeclared readings

1. C(n) non-power-law / oscillatory → hinge + peaks are findings;
   β_corr_short/long enter H3's prediction as a sensitivity band; if no
   adequate window exists (§3 gate), H3 is WITHHELD for that scope, never
   patched from a band (review B7).
2. Document-mixture drift across lags → common-support sensitivity is
   primary on disagreement (§2).
3. Between-document composition covariance → excluded from β_corr by
   declaration, reported separately; large values flag corpus
   heterogeneity as its own result.
4. H_n non-power-law (log-log curvature) → γ̂ window sensitivities +
   collapse-metric deterioration; "code sits outside the paper's ansatz
   family" is a reportable outcome.
5. Slow-learning regime (min δ_n < γ/2β) → H3 evaluated against
   min{δ, γ/2β}; the regime label per language is itself a headline
   result.
6. UTF-8 harmonics (lean lags 2–4) → peaks reported; CS-1b BPE pass
   bounds tokenization-scale effects.
7. Repo-composition confound → per-repo strata always shown; claims
   attach to locked mixtures (§1).
8. HP-walk outcome visibility → §5 honesty statement; first pass
   exploratory; confirmatory only via CS-3 replication.
9. Positional-parameter confound between T arms → §4 sensitivity; α̂_D
   primary is single-T.
10. Undertuned rungs faking curvature → §4 state machine; optimizer
    health review at CS-2 pilot; all HP runs retained.
