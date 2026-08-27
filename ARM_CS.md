# ARM_CS — corpus-statistics & from-scratch scaling arm (Cagnetta test)

Status: **ADOPTED (2026-08-28, CS-0)** after a TEN-ROUND independent
fresh-context adversarial review (v0..v9 each FIX-FIRST with executed
counter-examples; the catches included the shifted collapse form, the
same-population Miller–Madow correction, an empirically demonstrated
γ-identifiability failure, a statistically reversed H3 rule, two
bootstrap formula errors, and a chain of evidence-substitution attacks —
all fixed and asserted in the selftests). Round-10 split its residue into
three non-deferrables (fixed at 0d1dbaf) and one deferrable, recorded
with its mitigation as an ACCEPTED OPEN DISAGREEMENT in the PREREG §13
adoption entry, which also carries the G6-fulfillment statement and the
adoption-time instrument source-blob pins. Section annotations below
retain the round-by-round fix history for provenance.

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
- **The theory test (H3)** (round-2 NB2 fix — equivalence testing done
  right): per H3-eligible language mixture ({lean, python, cpp}; never
  latex), compare α̂_D with α_pred = γ̂/(2β̂_corr) under a FIXED scientific
  equivalence margin **M = 0.05** (frozen; uncertainty can only make
  support harder, never easier). Let c = α̂_D − α_pred and
  h = √(hw_α² + hw_pred²), the quadrature of the §6 half-widths
  (worst-case stacking of every sensitivity was shown to make M
  unreachable even for a clean synthetic; quadrature of uncorrelated
  components is the frozen combination rule). **H3 is a frozen
  ROBUSTNESS verdict, not a coverage-calibrated statistical test**
  (round-3 fix): the half-widths are predeclared sensitivity aggregates,
  not confidence intervals, and NO coverage for c is claimed — outcomes
  are therefore named **CONSISTENT / INCONSISTENT / INDETERMINATE**
  (never "supported/refuted" in any claim text). A coverage-calibrated
  version belongs to the CS-3 replication design, if built. GATES, in
  order, each of which forces INDETERMINATE(<gate>) when failed: regime
  (fast learning, §6), horizon (n̂*(P_top) ≤ T/4, §6), capacity
  (adjudicated un-fired, §4). Then: CONSISTENT iff |c| + h ≤ M;
  INCONSISTENT iff |c| − h > M; else INDETERMINATE. Predeclared
  sensitivity refits that FAIL (a window refit failing the §6 gates, an
  Ĥ_∞±step refit with too few rungs) WITHHOLD the result entirely —
  failure never shrinks h (round-3 fix). Scaling collapse is **H3b**, a
  separate DESCRIPTIVE report (metric + sensitivity sweep, no acceptance
  threshold) — it is not a conjunct of H3.
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
- **Common-support sensitivity (review B7; round-3 demotion)**: all pair
  statistics recomputed on the sub-corpus of documents ≥ 8192 B. This
  keeps the document SET fixed across lags but not the pair WEIGHTS
  (each doc contributes L_d − n pairs), so it is a SENSITIVITY, never a
  primary: the primary β̂_corr always comes from the pooled scope — the
  same mixture γ̂ and α̂_D are trained on (estimand pairing) — and
  material pooled-vs-csupport divergence (beyond the doc-block CI
  half-width) is REPORTED as `csupport_divergence`, not substituted.
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
- **Centered sequential estimator (round-2 B7 fix — PRIMARY)**: the
  permutation-mean matrix estimates the composition + sampling structure
  that survives within-document shuffling, so
  C_seq(n) = Ĉ_data(n) − mean_perms(Ĉ_perm(n)) isolates sequential
  structure as a MATRIX difference before any norm is taken. β̂_corr is
  the slope of ‖C_seq(n)‖_op; the raw-op slope is a reported sensitivity.
  Centered floors: leave-one-permutation-out —
  floor_seq(n) = max_i ‖Ĉ_perm_i(n) − mean_{j≠i} Ĉ_perm_j(n)‖_op.
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
- **Run-length diagnostic (round-2 NB3 fix)**: the Frobenius slope of the
  centered matrix over the SAME window is always reported next to
  β̂_corr. The op norm is a max statistic dominated by long constant byte
  runs (demonstrated on a Pareto renewal synthetic in the selftest). On
  material divergence — |β̂_corr − β̂_fro| > max(0.1, the doc-block CI
  half-width) — the scope's β̂_corr is **WITHHELD** (flag
  `divergence_withhold`; the theory's β is the operator norm, so no
  silent substitution is licensed) and any language relying on it drops
  out of H3. Frobenius stays a sensitivity, never the primary.
- **Broken law (review B8)**: continuous hinge
  y = a + b₁·min(x − x₀, 0) + b₂·max(x − x₀, 0), x = log n, knot x₀
  gridded over window lags; 4 parameters; adopted iff ΔBIC ≤ −6 vs the
  single line; (β_corr_short, β_corr_long, n_break) reported.
- **Peaks (review B8)**: |residual| > 2 × (1.4826 × MAD) from the single
  OLS line over the window; peak lags reported with sign. Peaks are a
  GATE-INDEPENDENT diagnostic — reported even when the adequacy gate
  withholds β̂_corr (oscillation-dominated scopes are exactly where they
  matter).
- **H_k (round-2 B8 fix — same-population conditional)**: at each k, the
  context entropy is computed over the (k−1)-prefixes OF THE SAME masked
  k-gram positions (prefix = packed_kgram div V), so joint and context
  statistics share one sample population exactly:
  H_cond(k) = H_k(joint) − H_{k−1}(prefix); MM-corrected conditional
  H_cond_mm = H_cond + (m_k − m_prefix)/(2 N_k ln 2); UNRELIABLE iff the
  joint undersampling (m_k − 1)/(2 N_k ln 2) > 0.02 b/B or
  |correction| > 0.02. Corrected values are what the summary exports.
- **Uncertainty (review B8; round-3 exact-centered fix)**: document-BLOCK
  bootstrap, 500 blocks (docs hashed to blocks by seeded shuffle), 200
  resamples with block-resample indices fixed across lags (coherent
  curves), applied to pooled, matched, AND per-repo scopes (100 for
  strata); reported explicitly as a block bootstrap. The bootstrap is
  the PLUG-IN of the point estimand on each resample (round-4 fix — the
  earlier block-wise cancellation claim was FALSE: within-document
  shuffles preserve whole-document histograms but not lag-masked
  endpoint marginals): covariance is NONLINEAR in counts, so EACH
  permutation's covariance is rebuilt per resample and then averaged —
  op(C(J_data^r) − mean_i C(J_perm,i^r)) — never the covariance of
  averaged counts (round-5 fix). Shuffles are held fixed across
  resamples (a paired block resample of the (data, perm) statistics).
  The selftest compares the production path against an independent
  naive reference implementation to 1e-9 and exercises n_boot>0
  end-to-end. Lag-point bootstrap (1000) secondary.
- **β positivity gate**: β̂_corr ≤ 0.02 (indistinguishable from flat) →
  "no reportable β_corr" for the scope.
- **Provenance (review B4, hardened at round 2)**: every output records
  git commit + dirty flag + per-scope document-manifest SHA256 (over
  sorted doc SHA1s) + the estimator constants; quick-mode writes
  `*.quick.json` (never the canonical name); existing outputs are never
  overwritten without `--force`; non-quick runs REFUSE to start on a
  dirty CS source tree (`--allow-dirty` overrides for local exploration
  and records itself in the output). CS-2 side: pool manifests record
  commit/dirty + collection SHA; run jsons record train/val/manifest
  SHA256s; the γ registration binds the SHA256 of every dump and run
  json it read.

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
- **Eval (review B9; round-2 NB4 fix)**: the final per-position NLL dump
  uses DOC-RESET windows from the val manifest (`--val-manifest`): every
  document scored from its own start; positions are within-document
  context lengths; doc id recorded. L_n and L(P) are therefore
  doc-interior, matching §1's estimand declarations. (Training batches
  still cross concatenation joins — a standard-practice model-side
  choice, disclosed, affecting the model not the estimator.) The VAL
  SPLIT is drawn from the seeded-SHUFFLED doc order (every 10th shuffled
  doc), so validation matches the pooled training mixture rather than
  the collection order; VAL_CAP = 12 MB (γ's per-position noise scales
  as 1/√windows — round-2 NB1 showed the estimator needs the larger
  sample); the trainer validates the manifest fail-closed (language
  match, strictly increasing offsets, exact final byte count) and skips
  idempotently when its result exists.
- **HP state machine (review B9, frozen)**: metric = final val b/B
  (doc-reset), seed 0, T = 4096. Rung 1: full grid lr ∈ {3e-4, 1e-3,
  3e-3} × epochs ∈ {1, 2, 4}. Rung r > 1: evaluate the incumbent plus the
  five neighbors {lr×3, lr, lr/3} × {epochs, epochs×2} \ {incumbent}
  (6 runs total); the winner is the new incumbent. Seeds 1–2 and the T=512 arm reuse the per-rung
  incumbent. All HP runs are recorded; none is deleted.
- **Capacity guard (review B9; round-2 NB5 fix)**: at the largest rung,
  a TUNED 30m probe — the full 6-run neighbor set around the 10m
  incumbent HP (seed 0, T=4096) — must not beat the tuned 10m by
  > 0.01 b/B; if it does, the language gets a COMPLETE separate 30m
  ladder (all rungs, all seeds) and the 10m ladder is reported as the
  undersized arm — curves are never spliced (the analyzer filters by
  size everywhere; a mixed-size curve is impossible by construction).
  ADJUDICATION IS AN ARTIFACT (round-3 fix; schema-complete per round
  5): `cs2_launch --stage capacity-verdict` requires EXACTLY the six
  frozen neighbor probes (no duplicates), refuses overwrites, and
  writes results_cs/capacity_verdict.json with schema
  cs_capacity_verdict_v1 carrying the selected run identities and
  result-json SHA256s for all six probes AND the 10m incumbent. The
  GAMMA phase VERIFIES the evidence, not the shape (round-6 fix): every
  listed result-json is re-hashed, losses must be finite, and the fired
  flag is RECOMPUTED from the recorded losses — mismatch, staleness, or
  NaN refuses registration; the registration sha-binds the verdict so
  post-registration tampering is refused. HP `pick` likewise requires
  EXACTLY the frozen candidate set (grid 9 / walk 6; extras, gaps, or
  duplicates fail-closed) and records the full candidate set with
  result SHA256s in hp_incumbents.json; BOTH analyzer phases verify
  every primary-arm run's (lr, epochs) against its rung's registered
  incumbent and sha-bind the incumbents file. The trainer's identity
  (recorded and skip-verified) includes device, effective micro-batch,
  and step_tokens — a same-tag MPS smoke can never stand in for a
  canonical CUDA run.
- **Fail-closed execution (round-2 NB5 fix)**: `pick` refuses (nonzero
  exit, driver aborts) when a rung's candidate set is incomplete or
  contains non-doc-reset runs; `ladder` refuses on any missing
  incumbent; the analyzer requires exactly seeds {0,1,2} at the rungs it
  reads and filters size=10m. Checkpoints are RETAINED on POOL (≈14 GB)
  so the T=4096-model-at-512-windows sensitivity is a pure eval pass at
  CS-4.
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
2. CS-2 runs (HP walk → capacity probes + `capacity-verdict` artifact →
   ladder). The verdict must exist UN-FIRED before step 3: γ̂/Ĥ_∞ may
   not be registered from a model the adjudication would declare
   undersized (round-4 fix; the gamma phase enforces this).
3. **γ̂ + prediction registration commit** BEFORE the envelope phase:
   `analyze_cs.py --phase gamma` reads ONLY the top-two-rung artifacts
   (top rung for the estimate, second for the §6 convergence rule),
   writes the per-language {γ̂, Ĥ_∞, β̂_corr, α_pred} registration with
   input hashes; the commit is pushed. `--phase envelope` re-hashes the
   registration's bound inputs and refuses on any mismatch, and refuses
   to overwrite an existing analysis without `--force` (round-3 B4
   fix); it also refuses to run
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
- **γ̂ / Ĥ_∞** (largest rung, T=4096; round-2 NB1 fix): the L_n curve is
  LOG-BINNED over n ∈ [4, 512] (24 log-spaced bins, byte-weighted bin
  means) — binning plus the 12 MB val plus 3-seed pooling is what buys
  the per-point noise the estimator needs. Refit rules (round-5
  reconciliation — these ARE the frozen §6 gates for refits): a
  sensitivity-window refit must satisfy the fatal gates (γ > 0.05,
  interior H*) and its OWN identifiability cap (profile width ≤ 0.3,
  the degenerate threshold — narrow windows have less H-leverage);
  failure WITHHOLDS γ̂. The R² certification applies to the primary fit
  only. An identifiable refit contributes its POINT to the window
  component plus, when its profile width exceeds the primary standard
  (0.15), half the EXCESS — borderline-identifiable refits widen hw_γ,
  never narrow it. Requirements before any γ̂ is reported: exactly
  seeds {0,1,2} present at the top two rungs (exactly three artifacts
  each — duplicate-seed artifact sets fail);
  convergence (top-two-rung binned curves agree within 0.02 b/B over the
  window); and the IDENTIFIABILITY GATES on the H-grid fit (grid
  H ∈ [0, min L_n] step 0.005, OLS of log(L_n − H) on log n): γ̂ > 0.05;
  H* strictly interior to the grid; profile width — the γ range over
  {H : R² ≥ R²_max − 0.001} — at most 0.15 (empirically calibrated: clean
  binned fits span ≈0.10, degenerate profiles ≥0.3); R²_max ≥ 0.9. Any failure →
  "γ̂ not reportable" with the failed gate named. Uncertainty half-width
  hw_γ = quadrature of {profile-width/2, window-sensitivity spread/2
  ([4,128] and [16,512] refits)}; per-seed single-run fits are a
  DIAGNOSTIC, not an uncertainty component (~3× noisier by
  construction). hw_pred = α_pred·√((hw_γ/γ̂)² + (hw_β/β̂)²) with hw_β
  the doc-block CI half-width.
- **Collapse = H3b, descriptive (review B5; round-2 NB2; round-3
  window/outputs fix)**: the form is the shifted one
  (L_n(P) − Ĥ_∞)·n^γ̂ vs P/n^(2β̂_corr) over n ∈ [4, 64] (frozen — the
  code window equals this) × all rungs; outputs include the shifted
  metric, the RAW published-form metric as a labeled replication
  sensitivity, and a 3×3 (γ̂±0.1, β̂±0.1) sweep of the shifted metric
  reproducing the paper's qualitative deterioration check; the metric is
  evaluated POINTWISE over the union grid on points covered by ≥3 rung
  curves (full common support is arithmetically empty at a 64× P range
  with the frozen n window);
  only. Collapse quality metric: interpolate each rung's shifted curve
  onto a 20-point common log grid; metric = mean cross-rung variance ÷
  variance of the pooled master curve (smaller is better); reported as a
  DESCRIPTIVE number with a (γ, β) sensitivity sweep reproducing the
  paper's qualitative deterioration figures. No joint (γ, β) estimator
  claim (the paper defines none; review B6).
- **α̂_D**: PRIMARY = OLS slope of log(L(P) − Ĥ_∞) vs log P, where L(P)
  = seed-mean overall doc-reset val b/B at T=4096, over rungs with
  L(P) − Ĥ_∞ ≥ 0.02 (asymptote-contaminated rungs excluded by this
  frozen rule, not by eye). hw_α (used by H3) = quadrature of
  {seed-slope spread/2, leave-one-rung-out spread/2, Ĥ_∞±grid-step
  refit spread/2 — the last is the dominant systematic near the shift
  floor and re-applies the shift rule at each H variant}. Sensitivities
  reported: envelope across T;
  first-m sweep (m = 4..7); RAW (unshifted) slope as the replication
  sensitivity. If fewer than 4 rungs survive the shift rule, α̂_D is
  "not reportable". If either Ĥ_∞±step refit cannot satisfy the shift
  rule with ≥4 rungs, α̂_D is WITHHELD (sensitivity failure never
  shrinks hw; §1). The envelope-across-T sensitivity is ALL-OR-NOTHING:
  reported only when the complete T=512 ladder exists, otherwise
  withheld with a note (round-6: job completion order must not move
  any reported exponent). For an H3-eligible language, α̂_D and H3 are
  WITHHELD
  outright if any primary-arm (T=4096) rung is missing, has an
  incomplete seed set, missing dumps, or duplicate (rung, seed) runs —
  fail-closed, with the defect named (round-3 fix; descriptive-only
  scopes may drop-with-note instead). The primary gate covers T=4096
  ONLY (round-5 fix): T=512 groups enter the envelope sensitivity when
  complete and are otherwise noted, never withholding — job completion
  order cannot change the verdict. hw_pred is defined in the γ̂ bullet above.
- **Regime gates (review B6; round-2 NB2 fix — evaluated BEFORE H3)**:
  (a) fast-learning check — δ_n per the paper's §5 protocol (grid H_n
  along the P axis per n ∈ {1..12}; δ_n = −slope of log(L_n(P) − H_n)).
  FAST LEARNING IS ESTABLISHED iff at least 9 of the 12 δ_n exceed
  γ̂/(2β̂_corr) with fit R² ≥ 0.9 each; if not established, H3 =
  INDETERMINATE(regime) — the zero-parameter test is simply not licensed
  — and the slow-regime comparison of α̂_D against min{δ̂, γ/2β} is
  reported DESCRIPTIVELY, never as H3 in any regime (the min of twelve
  noisy δ̂s is not a zero-parameter prediction, and the δ = γ/2β
  boundary carries a log P correction, paper Eq. 39). (b) horizon check —
  n̂*(P_top) = n_det · (P_top/P_corpus)^{1/(2β̂_corr)} with n_det the
  largest valid lag of the registered CS-1 scope and P_corpus its byte
  count; horizon-limited OK iff n̂*(P_top) ≤ T/4 (number reported
  either way). (c) capacity check — §4's guard. Each gate's failure
  reading is predeclared in §8.
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
- Explicitly SCHEDULED (not pre-adoption) implementation items: the
  lean rung-seed-31 nested-subset sensitivity ladder runs at CS-3; the
  T=4096-model-at-512-windows positional sensitivity (a pure eval over
  retained checkpoints) runs at CS-4. Neither blocks adoption; both
  block their own gate's sign-off.
- **CS-3**: replication/scale step — HUMAN GATE (named disjoint corpora
  with manifests; any 30m escalation ladders; any model-family widening).
- **CS-4**: analysis per §6; figures; writeup integration.

## 8. Threats and predeclared readings

1. C(n) non-power-law / oscillatory → hinge + peaks are findings;
   β_corr_short/long are reported DESCRIPTIVELY — H3 uses the global
   β̂_corr only, never a band (round-4 consistency fix); if no adequate
   window exists (§3 gate), H3 is WITHHELD for that scope (review B7).
2. Document-mixture drift across lags → the common-support sensitivity
   is recorded (`csupport_divergence`); it is never primary (§2).
3. Between-document composition covariance → excluded from β_corr by
   declaration, reported separately; large values flag corpus
   heterogeneity as its own result.
4. H_n non-power-law (log-log curvature) → γ̂ window sensitivities +
   collapse-metric deterioration; "code sits outside the paper's ansatz
   family" is a reportable outcome.
5. Slow-learning regime (fast learning not established) → H3 =
   INDETERMINATE(regime); the min{δ, γ/2β} comparison is reported
   descriptively only (§1/§6); the regime label per language is itself
   a headline result.
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
