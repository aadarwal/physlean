# THEORY — what arXiv:2602.07488 changes about this campaign

Bridge doc, written 2026-08-27, after a full read of **Cagnetta, Raventós,
Ganguli, Wyart, "Deriving neural scaling laws from the statistics of natural
language," ICML 2026 (PMLR 306), arXiv:2602.07488v3, DOI
10.48550/arxiv.2602.07488** ("the paper" below; companion code:
github.com/fracagnetta/small-language-modelling, verified live 2026-08-27).
**PREREG.md and DESIGN_V2.md remain the campaign's source of truth.** This
doc motivates a NEW, complementary arm (design frozen separately in
ARM_CS.md, adversarial review required before adoption); §0.5 reconciles it
with the V2 program that actually ran 2026-08-07..12. §2/§4 below were
first drafted against the pre-V2 HANDOFF framing; read them through §0.5.

## 0. TL;DR

The paper is the missing *theory layer* for this experiment, and it does not
scoop any of it. It proves (under two measurable hypotheses about a corpus)
that the **data-limited training scaling exponent is determined by two corpus
statistics**:

- γ — how fast next-token conditional entropy decays with context length:
  H_n − H_∞ ∝ n^(−γ)  (Hilberg's hypothesis). **This is the quantity the
  HANDOFF-era Phase-1 fit form was aiming at under the name β — but no
  legacy exponent is reinterpreted**: pretrained curves measure H_n + KL_n,
  the G3a holdout gate rejected that global fit form (no headline G3
  exponent exists), and the arm's γ is a NEW intrinsic estimand from
  from-scratch models (ARM_CS §1/§6).
- β — how fast token–token correlations decay with separation:
  ‖C(n)‖_op ∝ n^(−β), where C(n) is the lag-n token co-occurrence covariance
  matrix. **Model-free, computable from the corpus on a CPU. We don't measure
  it yet. We should, immediately.**

Their result (fast-learning regime): **α_D = γ/(2β)** with zero free
parameters, verified on TinyStories (γ=0.325±0.003, β=0.88±0.06 →
α_D=0.185±0.013) and WikiText-103 (γ=0.265±0.016, β=0.94±0.16 →
α_D=0.141±0.025). The α_D/collapse evidence is GPT-2 (APE/RoPE) and LLaMA;
Mamba and infini-gram support the architecture-independence of the limiting
𝓛_n-vs-n curve on TinyStories — γ as a property of the *dataset*. Mechanism: with P training
tokens you can only resolve correlations at lag n if their strength beats the
sampling-noise floor O(P^(−1/2)), giving a data-dependent prediction horizon
n*(P) ∝ P^(1/(2β)); the loss then tracks the entropy at that horizon,
H_{n*(P)}, i.e. L(P) − H_∞ ∝ P^(−γ/(2β)). They validate it a second,
sharper way: the n-gram loss curves 𝓛_n(P) collapse onto one master curve
under rescaling. NOTE the operative form is the SHIFTED one,
(𝓛_n − H_∞)·n^γ vs P/n^(2β) (their App. A Eq. 43); the headline Eq. 9/42
print the unshifted shorthand, which cannot collapse asymptotically when
H_∞ > 0 — ARM_CS §6 freezes the shifted form as primary.

They test this on **natural language only** (TinyStories = GPT-generated
children's stories; WikiText = encyclopedia prose), at horizons of **a few
tens of tokens**, and explicitly solicit extensions to larger contexts and
other data. No code, no formal language, no cross-corpus comparison as the
object of study, no pretrained/transfer setting, no model-size axis.

**So: physlean stops being "measure per-language curves" and becomes the
first out-of-domain stress test of a first-principles scaling theory — on
exactly the axis (formal vs informal language) where the theory's inputs
should differ most.** Every outcome is a result: if the theory transfers,
per-language (γ, β) *predict* per-language training laws from corpus
statistics alone, and Gwern's hypothesis becomes two measurable numbers; if
it breaks on code, we've mapped the first boundary of the theory's
universality class (their own conclusions section asks precisely this
question).

## 0.5 Reconciliation with the V2 program (added 2026-08-27, same day)

This doc was first written against the 2026-08-07 HANDOFF snapshot. The
campaign that actually ran (Aug 7–12, 91+123 commits) went further and
differently; what follows reconciles the bridge with that reality.

**What happened.** G3a sentinel (44 cells, Qwen2.5-Coder-0.5B): instrument
pass; context gains robustly positive (all 15 phase-ablation doc-bootstrap
CIs > 0) — but the **frozen holdout gate rejected the global
`A·c^(−β)+L∞` power-law form on every headline base curve** (2/64 strata
accepted), and selection-seed sensitivity made stream-level cross-corpus
readings noise-limited. The program pivoted to fixed targets with paired
context conditions (DESIGN_V2). V2-b ran a five-tier NLL-only exploratory
ladder (0.5B–14B; 32B infeasible, recorded) plus dose curves. **V2-c
confirmatory reveal (Aug 12, Qwen2.5-Coder-1.5B, within-target paired,
Holm-corrected): E1a repository-context gain CONFIRMED** — one-sided lower
95% bound 0.319 b/B on mathlib4 (35 analyzed E1a targets) and 0.090 b/B on
sympy (49 analyzed; the intersection sensitivities use 34/42 targets with
bounds 0.320/0.100; 52/77 are the planned repo Ns);
E2 (dependency relevance beats random same-corpus context) confirmed; E1b
interface-only non-inferiority NOT established (adjusted p ≈ 1.0) —
implementations carry predictive load beyond signatures. Behavioral arm:
S5 verification envelope landed (main, Aug 9) with TODOs open
(complete-artifact producer, corpus integration + S5 launcher, bwrap
smoke); the NLL reveal destroyed the pilot targets' blind, so behavioral
confirmatory needs a FRESH sample.

**Consequences for this doc.**

1. G3a's rejection of the global 3-parameter power-law fit on pretrained
   streams *vindicates the paper's estimation protocol* (γ from the
   initial decay only; asymptote via grid-search-R²; collapse as the joint
   instrument) and converts "H_n is a power law for code" from an
   assumption into this arm's explicit empirical question. The rejection
   also has a theory-side reading: pretrained curves measure H_n + KL_n
   (transfer gap), and there is no reason KL_n's shape preserves a power
   law even where H_n has one.
2. DESIGN_V2 §8 F2 forbids confirmatory *language-level* claims from ~5
   repos. The corpus-statistics arm is the complementary route to
   language-level statements: γ, β, H_∞ are properties of corpora, with
   no model, no target clustering, no contamination — though language
   pools are still repo compositions, so per-repo strata are always
   reported next to pooled-language numbers.
3. Frozen-instrument discipline: `eval_incontext.py`, `layout.py`,
   `analyze_v2.py`, and the dependency lock are governance-bound. This
   arm builds NEW standalone files only — `lang_stats.py` (corpus
   statistics), `analyze_cs.py` (arm analyzer), `results_cs/` (arm
   namespace) — and never retrofits frozen machinery or G3 artifacts.
4. Naming stays PREREG-compliant: this arm reports corpus-intrinsic
   statistics and per-language *from-scratch data-scaling exponents* —
   never "software/codebase scaling laws"; the growth question remains
   the longitudinal arm's (DESIGN_V2 §11).
5. Zero-GPU bonus: the G3a run left 44 per-position NLL dumps (88
   dump+meta artifacts) on POOL. The transfer-side γ̂_transfer can be
   estimated from those EXISTING dumps with the arm's frozen estimator —
   per-corpus DESCRIPTIVE only; it may return "no reportable exponent"
   (the G3 holdout verdict stands), and no cross-corpus or cross-language
   ordering is read from it (PREREG §6 bar).
6. V2-c's E1b negative (interfaces insufficient) is itself a datum for
   the formality story this arm quantifies: if types alone carried the
   predictive load, one would expect interface-only context to approach
   closure context; it doesn't at 1.5B — the arm's β/γ decomposition asks
   the same question at the level of corpus statistics.

## 1. The dictionary (fix the naming collision NOW, before results exist)

| physlean today | the paper | adopt |
|---|---|---|
| fitted exponent `beta` in BPB(c)=A·c^(−β)+L∞ | targets the same object as their **γ** (Eq. 6/21) | legacy `beta` is a MODEL-RELATIVE fit parameter whose global form G3a REJECTED — never reinterpreted as γ; the arm's **γ** is a new intrinsic estimand (ARM_CS §6) |
| `Linf` | analog of **H_∞** | legacy Linf stays "asymptotic model BPB" (PREREG §1); the arm's **Ĥ_∞** is a new intrinsic estimand |
| in-context bytes `c` / `ctxb` | context length **n** (theirs in tokens; ours in bytes) | keep bytes; state units |
| — (not measured) | **β** (correlation-decay exponent), Eq. 7/29 | NEW: measure per corpus |
| Phase 2 data-scaling slope | **α_D = γ/(2β)** (Eq. 8; in general min{δ, γ/2β}, Eq. 40) | the prediction to test |
| — | **n*(P) ∝ P^(1/(2β))** prediction horizon; **P*_n ∝ n^(2β)** data threshold (Eq. 30–31) | use for run sizing (§5) |
| — | **δ_n**: decay of within-horizon excess loss 𝓔_n(P) (Eq. 25–26) | measure per language (regime check) |

Both exponents must use the same n-units when forming γ/(2β); we use
**bytes** for both (byte-level C(n) matches the 1-token=1-byte Phase 2
models). α_D is a ratio of log-log slopes, so units cancel as long as they
match.

## 2. What each phase now *means*

**Phase 1 (pretrained ladder, in-context axis).** A pretrained model's loss
on our corpora is L_n = H_n + KL_n(corpus ‖ model): an upper bound with a
transfer gap that itself varies with n. The paper's footnote 1 makes this
point against Scheibner et al. (arXiv:2512.24969): a model not trained on the
distribution cannot converge to its entropy. So Phase 1's fitted exponent is
an **effective, transfer-inclusive γ̂** — precisely Gwern's question ("how
predictable is Lean *to frontier models* as context grows"), but not the
corpus-intrinsic γ. Say so in the writeup, and quote footnote 1 preemptively.

**Phase 2 (from-scratch byte models).** This is the paper's own setting. The
per-position-NLL dump at final val (already implemented, same CSV schema as
eval_incontext) *is* their family of n-gram losses: 𝓛_n = mean NLL at
context n. From-scratch models at the largest data rung, evaluated on
held-out same-distribution files, give the paper-style estimator of the
**intrinsic γ and Ĥ_∞ per language mixture** — corpus-intrinsic ESTIMANDS
with a model-assisted estimator (one architecture + a capacity guard here;
architecture-independence is the paper's TinyStories/WikiText result and is
imported, not re-established by this arm).

**From the two together (per-corpus, descriptive only):** the transfer-side
curve shape vs the intrinsic curve shape for the SAME corpus — does
pretraining change the shape of the context curve or only its level? This
stays per-corpus and estimator-gated (either side may return "no reportable
exponent"); no cross-language transfer-gap ordering is claimed (PREREG §6).

## 3. The upgraded claim ladder (what we're now testing)

H1 (NEW registration for this arm; the `ab2a4c6` trail hypothesis about
pretrained in-context curves remains its own, unresolved, and is NOT
co-opted): intrinsic γ̂(lean pool) > γ̂(python pool) and Ĥ_∞(lean pool) <
Ĥ_∞(python pool), from CS-2 under ARM_CS §6.

H2 (NEW, model-free): the correlation exponent orders languages by
formality: **β_lean < β_python < β_prose** (formal code has slower-decaying
long-range structure — imports, binders, theorem reuse). Falsifier: byte- and
BPE-level C(n) both show Lean decaying as fast or faster than Python.

H3 (NEW, the theory test): per language, the measured Phase 2 data exponent
matches the zero-parameter prediction: **α̂_D ≈ γ/(2β)**, and the n-gram
loss curves collapse under (γ, β) rescaling. Falsifiers, each informative:
(a) collapse quality differs qualitatively between code and the LaTeX arm
→ suggestive of domain-specificity (the raw-TeX arm is a FORMAT DIAGNOSTIC
only, per PREREG §2 — no prose/formality claim rides on it); (b) α̂_D = δ < γ/(2β) → code sits in the *slow-learning*
regime (within-horizon learning is the bottleneck — their Eq. 40 still
holds via min{δ, γ/2β}, with a log P correction at the δ = γ/2β boundary,
their Eq. 39; measure δ_n per their §5 protocol to confirm);
(c) collapse holds with different exponents than measured → estimation
problem, revisit fits.

H4 (NEW, the compound headline if H1+H2 hold): α_D(lean) > α_D(python) —
**formal languages have steeper data-scaling laws**, i.e. reward data (and
unlock long context, via n*(P) ∝ P^(1/2β)) faster than informal ones. This
is the quantitative version of the essay's "formal languages win in the AI
era," and it is *predictable before training and confirmable after* — the
cleanest possible experimental design.

Stretch: with (γ, β) measured on The Stack v2 control slices, check whether
γ/(2β) reproduces the per-language exponent ordering of arXiv:2512.13472
("Every Programming Language Matters") — explaining *why* languages differ,
not just that they do.

## 4. Concrete adoption plan (priority order)

**P0 — `lang_stats.py` (new, CPU-only, ~a day of work, run before any GPU
time).** Per corpus pool (shuffled variant, not topo — the theory assumes
stationary sampling):
- Lag-n covariance C(n) of one-hot byte tokens (256×256), n log-spaced
  1…~4096; top singular value + Frobenius norm (paper: both give the same
  decay; report both). Their App. B noise floor: ‖Ξ‖_op ≲ √(σ²·log V / P);
  with 30–500 MB pools the floor sits ~1e-4 — usable lags reach thousands of
  bytes, far past the paper's n≤200. Subtract/threshold accordingly.
- Fit β by linear regression on (log n, log‖C(n)‖_op) with bootstrap over
  lag points (their App. C.1); **detect broken power laws** (WikiText already
  needed a two-stage fit with the break at n≈32, and a lag-10 peak; code
  will be worse: line-length and indentation periodicities, and Lean's
  multi-byte Unicode (∀ = 3 bytes) adds 2–3-byte harmonics). Fit regimes
  separately; the break locations are themselves language constants worth
  reporting. Robustness: repeat with a shared 8k BPE trained on the union
  pool (paper-style tokens); if byte vs BPE β disagree wildly, that's a
  finding about scale, not noise.
- Bonus crosscheck: count-based n-gram estimates of H_n for n ≤ 5 bytes
  (they showed n-gram models land on the same limiting 𝓛_n curve at small n).

**P0.5 — the arm's analyzer (`analyze_cs.py`, NEW standalone module —
`analyze_v2.py` is governance-frozen G3 machinery and is not touched),
testable on synthetic curves and on the existing G3a dumps.**
- Keep no global 3-param fits. γ per the arm's OWN frozen estimator
  (ARM_CS §6): initial-decay window with a convergence rule, asymptote by
  grid-search-R² ALONG n. Attribution note (review B6): the paper's γ
  protocol fits a fixed small-n window of the largest-P curve judged
  converged (their §4.1.1/App. C.1), and their §5 grid searches a separate
  H_n per fixed n ALONG THE P AXIS to extract δ_n; our along-n H_∞ grid is
  an adaptation, not their method.
- Add the **collapse machinery**: given per-rung dumps, plot 𝓛_n·n^γ vs
  P/n^(2β); a collapse-residual score scanned over (γ, β) gives an
  independent joint estimate and reproduces their sensitivity analysis
  (Figs. 8–12) per language.
- Add the **envelope estimator of α̂_D** (their App. C.3): lower envelope of
  L(P) across context lengths T, fit first m points on log-log, bootstrap
  CIs, report the m-sweep table like theirs.
- Error discipline: propagate (Δγ, Δβ) → Δα_D by their Eq. 56:
  Δα = √((Δγ/2β)² + (γΔβ/2β²)²).

**P1 — reshape Phase 2 into the theory test (the core change).**
Current plan is a size ladder at full pools. Add the **data ladder**:
- Fixed model size (start 10m; verify per their protocol that a bigger model
  at the largest rung doesn't improve val loss — if it does, move up a size),
  P ∈ ~6 log-spaced rungs per language (e.g. pool × {1/64 … 1}), ≥2 context
  lengths T (512 and 4096 bytes; 16k where cheap), ≥3 seeds at small rungs.
- Per-rung hyperparameter honesty: the paper grid-searches lr/wd/epochs/batch
  per (P, model) and warns undertuned rungs fake curvature; adopt at least
  the Kim-et-al local-optimality shortcut they use (tune at the smallest
  rung, carry forward, spot-check at each next rung).
- Dump per-position NLL at **every rung** (the trainer already writes the
  CSV at final val — just run per rung), feeding the collapse + envelope
  analyses. Explicit data-subset rungs, each trained to its tuned optimum,
  are the estimator; checkpoint-based curves are a free bonus, not a
  substitute (multi-epoch training breaks the tokens-seen ≡ P equation).
- Compute cost is fine: their GPT-2-98M runs were minutes→2 days on one
  H100 at P up to ~5×10⁸ tokens; our 10m byte models at ≤5×10⁸ bytes on
  L40S/H200 are strictly cheaper, and the sharded Slurm runner exists.

**P2 — Phase 1 runs as planned.** No design change; new interpretation
(§2), plus the transfer-vs-intrinsic γ comparison once Phase 2 rungs exist.

**P3 — stretch:** Stack-slice (γ, β) vs 2512.13472's exponent table.

## 5. Sizing math we couldn't do before

P*_n ≈ σ²·log V / ‖C(n)‖²_op (their Eq. 28/54, constants from App. B) says
how much data is needed before lag-n structure is even *detectable*. Once
`lang_stats.py` gives per-language ‖C(n)‖_op curves, compute n*(P_pool) per
language: if e.g. the 30 MB Python-physics pool yields n*(P) of only a few
hundred bytes, then flat long-context curves in Phase 2 at that pool size
are *predicted*, not disappointing — and pool-enlargement priorities follow
from the equation instead of vibes. If Lean's β is smaller (H2), formal
languages unlock long-range context at *smaller* P — itself a testable,
headline-adjacent sub-claim.

## 6. Pre-registered ways the theory may break on code (each is a finding)

1. **C(n) not a clean power law** — syntax periodicity (line length ~60–100
   bytes, indentation cycles, brackets) → oscillations/peaks; broken fits
   per regime; envelope-of-peaks as robustness. (They already saw a lag-10
   peak and a broken law on WikiText and handled it by fitting the short-lag
   regime; do the same, transparently.)
2. **H_n decay faster than power-law** for formal text (once a theorem
   statement is fixed, the proof is near-determined → possible
   quasi-exponential approach to a low H_∞, showing as log-log curvature).
   Diagnose curvature before asserting γ; report the fit window.
3. **Slow-learning regime** (δ < γ/2β): within-horizon learning, not data
   detection, may bottleneck code (cf. their kernel-method counterexample —
   shallow/kernel models fall outside the universality class). Their Eq. 40
   min{δ, γ/2β} still applies; measuring δ_n per language (their Fig. 6
   protocol) tells us which regime each language occupies — arguably the
   deepest possible finding here about *what makes code hard*.
4. **Non-stationarity of topo streams**: run the statistics and the collapse
   arm on shuffled streams; keep topo-vs-shuffled as its own ablation (it
   measures curriculum/order, a different question).
5. **Byte-vocab artifacts**: V=256 vs their 8192-BPE; exponents should be
   scale-robust (their op-vs-Frobenius and first-few-singular-values checks),
   but verify with the shared-BPE robustness pass in P0.

## 7. Novelty ledger (post-read; cold-screener verdict appended below)

The paper does NOT: touch code or any formal language; compare corpora as
the object of study; evaluate pretrained/transfer models (explicitly
excluded, footnote 1); probe horizons beyond ~tens of tokens (their stated
limitation: n*(P_max) ~ "a few sentences"; they explicitly hope larger-T/P
studies follow); measure the model-size axis. Everything in HANDOFF §1's
novelty claim stands. What changes is stature: physlean is no longer just
"first measurement of an essay's proposal" — it is the first test of an
ICML-2026 first-principles scaling theory outside natural language, with the
theory's authors having pre-committed (in print) to caring about the answer.

Positioning for the writeup: frame via the Hilberg lineage (Hilberg 1990;
Takahira et al. 2016; Dębowski arXiv:2512.13491) — Gwern's "predictability"
exponent *is* the Hilberg exponent of a programming language, now with a
first-principles link to training scaling laws; the open question we answer
is "which universality class do formal languages occupy?" (their conclusions
pose the universality-class question explicitly). This also upgrades the
gwern email: the essay's proposed measurement turns out to target the
quantity that a new theory says controls data-scaling.

> **Cold-screen verdict** (fresh-context screener agent, full-text read,
> 2026-08-27): **ADJACENT** — "same area — scaling of LM loss with context
> length and data, per-corpus exponents and asymptotes — but a different
> question: a mechanistic theory of *why* exponents arise from
> natural-language statistics, not a comparison of formal vs informal
> languages." No source code or formal language anywhere in its experiments;
> "no sought claim is preempted, but the quantities and machinery overlap
> substantially"; "the closest theoretical neighbor … should be cited/used
> as the interpretive frame, not treated as competition."

## 8. Cost of adoption

P0 + P0.5 are CPU-and-analysis work (~1–2 days), independent of cluster
access, and de-risk everything downstream. P1 adds ~hundreds of short
byte-GPT runs (minutes each on L40S) to the existing Slurm fan-out — well
inside the already-planned envelope. No change to corpora, streams,
contamination machinery, or the pretrained lanes — for the exploratory
first pass (the CS-3 confirmatory replication adds new, named, manifested
corpora).
