# DESIGN_V2 — repository-context sufficiency on fixed targets (G2.5, DRAFT)

Status: DRAFT for joint review; the confirmatory design PREREG §9 points
to. The G3 sweep is exploratory/motivational; THIS design carries the
claims. Nothing here runs before its own review and a piloted gate (§10).

NAMING (conceptual fork, recorded): this experiment manipulates AVAILABLE
REPOSITORY CONTEXT for fixed targets. It does NOT manipulate codebase
scale or growth, and its results are context-budget response curves —
never to be presented as a "software/codebase scaling law". The
codebase-growth question needs the separate longitudinal arm (§11).

## 1. Question and estimands

For a fixed target t (a declaration) in corpus s, model m, context
condition k, and byte budget B, measure the byte-normalized code length
of the target BODY C_m(t | k, B) and downstream task success. Headline
estimands, all within-target contrasts (each target is its own control):

- E1a repository-context gain: C(t|k1) − C(t|k4,B)  (k1 = query-only absence arm; k4 = dependency-closure REFERENCE, called a reference, not an oracle, because optimality is not established)
- E1b interface sufficiency: NON-INFERIORITY of interface-only context —
  the ONE-SIDED UPPER 95% CI of the paired mean C(t|k3,B) − C(t|k4,B)
  must lie <= 0.02 b/B (margin frozen; point estimates alone never
  establish non-inferiority — review fix). This k3-vs-k4 separation is
  precisely the mechanism the Lean/formality hypothesis cares about: do
  types/signatures carry the predictive load?
- E2 relevance gain: C(t|random-matched,B) − C(t|k4,B) (equal budget)
- E3 excess over the dependency-closure REFERENCE (not proven optimal — review fix): sum over the budget grid of
  [C(t|k,B) − C(t|k4,B)] with trapezoid weights on log2 B
  (weights frozen: 1, 2, 1 across {4,16,64}KB)
- E4 context need at the tested grid, ĉ_eps(t): smallest TESTED B with
  isotonic-regressed C(t|k4,B) − C(t|k4,B_max) <= eps
  (eps = 0.05 b/B; 0.10 sens.; monotonicity enforced by isotonic fit
  because three budgets cannot support a raw threshold crossing)
- E5 behavioral: pass@k under each condition (compiler/verifier/test)
- E6 mutation sensitivity: detection (§5 localization metric) and repair
  rate under each condition

E1/E2/E3/E4 are NLL-based; E5/E6 are behavioral. The NLL-as-proxy
falsifier (§8) ties them together.

## 2. Units: fixed targets

- Lean: theorem/lemma/def declarations. Dependency closure from ELABORATED
  references (.ilean declarations/references/directImports; LeanDojo-v2 /
  ExtractData-style extraction for source spans + premises; caches on
  POOL) — regex module imports serve only as the coarse baseline
  condition. Target = the declaration's source span; scoring covers the
  BODY ONLY — the signature and local syntactic shell are the common
  unscored query prefix of every arm (§3), never scored anywhere.
- Python: top-level functions/classes; closure from AST +
  resolved import graph (jedi/ast; same-repo only).
- C++ (stretch): function definitions; include-graph closure; only if
  Lean+Python land cleanly.

Per corpus N >= 200 targets (sized by §9 power sketch), stratified by:
length tercile, module centrality (in-degree tercile), first-add date
(pre/post family cutoffs — the clean-target arm), and for lean-zip the
verification strata below.

## 3. Context conditions

COMMON UNSCORED QUERY PREFIX (review fix — the target signature must not
leak into scoring, and every arm must see the same immediate query): the
target's signature and local syntactic shell (namespace/section openers)
form an unscored prefix present in EVERY condition; only the BODY is
scored. Conditions differ only in the repository context ABOVE that
prefix:

k1 none: no additional repository context (the signature/shell prefix
   only). k1 is an ABSENCE arm: it cannot be byte-matched to B and is
   never claimed to be.
k2 local-file prefix (bytes immediately above the target)
k3 dependency closure (reference) — INTERFACE-ONLY variant
k4 dependency closure (reference) — implementation-bearing variant
k5 random matched-byte same-corpus context (relevance control for k3/k4)
k6 retrieved context (BM25 over corpus, top chunks to budget)
k7 full-repo topo prefix (G3-style contiguous stream, budget-capped)

Byte-matching (review fix — the earlier B_eff=min rule was tautological):
PRIMARY analysis is NOMINAL-B COMPLETE-CASE — a target enters the paired
analysis at budget B only if EVERY compared presence arm can supply
exactly B within tolerance (no padding, ever); targets failing this are
excluded from that budget's primary analysis and their count reported.
Pair-specific effective budgets B_eff(t,B) are computed ONLY as a
labeled sensitivity analysis, never headline. k5 is matched to the arm
it controls for (k3/k4) at the same budget. The k1-vs-k3 contrast uses
k5 at the same budget as the named length control, so "context helps"
is separated from "any tokens present". Budgets are NESTED as SET INCLUSION across B (the
16KB context contains the 4KB context's content; for local-suffix arms
like k2 this means suffix containment, not string-prefix identity),
closures EXCLUDE the target itself and any declaration that
(transitively) references the target (cycle leakage), and every (t,k,B)
records bytes AND tokens shown plus its eligibility status.

## 4. Measurement

Identical semantics to the G3 harness (source-span groups, byte ledger,
pinned revisions, schema_version): score the target span only, context
never scored. Additional per-target outputs: AST-class ΔNLL split
(identifiers / operators / literals / keyword-boilerplate) via
tree-sitter (Lean: syntax classes from the extractor), so gains on
semantic tokens separate from formatting predictability.

## 5. Behavioral arm

- Generation: sample k=8 completions of the target body per (t, k1/k3/k4/
  k6, B=16KB), temperature 0.8, per model (0.5B–7B ladder; big rungs only
  if approved).
- Verification: Lean — `lake env lean` check of the file with the
  generated body (mathlib/physlib toolchain pinned per corpus lock);
  Python — module import + targeted pytest subset where one exists;
  compile-only otherwise (recorded as weaker outcome class).
- Mutation probes: per target, 3 seeded mutations (operator swap,
  boundary constant, identifier swap within scope). Detection metric
  (frozen): the RANK of the mutated source-span group in the per-group
  ΔNLL ordering (mutated vs original text under the same condition);
  reported as top-1/top-5 localization accuracy. Repair = constrained
  regeneration of the mutated declaration; success = verifier pass.
- pass@k estimator (frozen): the unbiased combinatorial estimator
  (Chen et al. 2021) with n=8 samples per (t, condition, model),
  temperature 0.8, top-p 0.95, seeds 0–7, no early stopping, k in {1, 8}.
- LICENSE: LeanPhysBench is NOT used (CC BY-NC, explicit no-AI-eval
  clause) absent written permission. lean-zip (Apache-2.0) and zlib
  (zlib license) are fine.

## 6. Corpora and pairs

Primary: physlib, mathlib4, batteries (Lean); sympy, astropy (Python);
geant4 (C++ stretch). Semantic pair: lean-zip vs zlib, STRATIFIED (2026-04
fuzzing audit): {proved implementation modules; unproved application
modules (e.g. Archive.lean parser — DoS found); trusted Lean-C++ runtime
boundary (heap overflow found)}; specification coverage recorded per
target; whole-repo claims never made without stratum labels. FormalScience
pairs added if a vetted mapping exists at build time.

## 7. Statistics

PRIMARY: repo-specific paired estimates — within-target condition
contrasts (E1a/E1b/E2/E3/E4) estimated per (repo, model family) with
nonparametric intervals (sign/Wilcoxon on within-target deltas) and a
per-repo mixed model with random intercepts for target NESTED IN
module/file (shared notation and style cluster below repo). Language-level
aggregation is EXPLORATORY ONLY (consistent with F2's identification
bar): reported as forest plots of the repo-specific estimates, never as
a pooled confirmatory language coefficient. Model families are separate
strata (never pooled across attention regimes). Multiple-comparison
control: Holm over the preregistered contrast family, within repo.
No extrapolation beyond observed B support; bytes and tokens both
reported; "total target bits per matched semantic unit" reported for
the lean-zip/zlib pair (per function/theorem pairing).

## 8. Preregistered falsifiers / decision rules

- F1 (NLL-as-proxy): the context NLL gain is C(t|k1) − C(t|k3,16KB)
  (positive = context helps). The behavioral outcome is the per-target
  pass@1 gain estimated as c/n (c = verifier-passing samples of n=8) —
  pass@8 with n=8 is tie-saturated/binary and cannot support a rank
  correlation (review fix). REJECT NLL as the working proxy for a model
  family if the within-target Spearman correlation between NLL gain and
  pass@1 gain has an UPPER 95% CI bound below 0.3 (one frozen one-sided
  null); CO-PRIMARY with it (per §12) is the trial-level hierarchical
  logistic model
  success ~ condition * target_NLL_gain + (1|target) — the INTERACTION
  carries the proxy question (does NLL improvement predict behavioral
  improvement?); condition + gain alone would only model target
  difficulty (review fix). F1 outcomes are three-way: REJECTED (upper
  95% CI < 0.3), SUPPORTED (lower 95% CI > 0.3), else INCONCLUSIVE —
  failing rejection is never itself evidence of support. The pilot
  (V2-b) tunes the design and is NEVER pooled into this confirmatory
  test. V2-b additionally gates a BEHAVIORAL FLOOR/CEILING viability
  rule, predeclared: if the pilot pass@1 rate under k4 at 16KB lies
  outside [0.05, 0.95] for a model tier, that tier is swapped for the
  next capability tier by this rule alone — never by outcome direction.
- F2 (consistency, NOT identification): with only ~3 Lean and ~2 Python
  repos, language and repo are weakly identified; NO confirmatory
  language-level effect is claimed from this design regardless of
  outcome. What is preregistered instead: sign-consistency checks of the
  Lean-vs-Python contrast across model families and across the
  lean-zip/zlib semantic pairing; any flip demotes the finding to
  repo-level. A confirmatory language claim requires a follow-up with
  many more repos (>= 10 per language) or many semantic pairs.
- F3 (security relevance): security-adjacent claims are made ONLY within
  the lean-zip verification-gradient strata with specification coverage
  reported; no claim extends to the trusted runtime boundary.
- F4 (formality mechanism): the within-Lean ablation is a
  PARSE-PRESERVING INFORMATION ABLATION APPLIED ONLY IN THE PROMPT
  CONTEXT — type ascriptions/signatures in k3 context text are replaced
  by parse-valid placeholders (e.g. `_` holes). The ablated text is
  never elaborated and never enters the repo: stripping signatures while
  requiring elaboration is generally impossible (review fix), so
  verification of generated bodies always runs against the UNMODIFIED
  repository. Frozen rule: the mechanism claim is REJECTED unless the
  one-sided within-target sign test (ablated worse than intact) is
  significant at alpha = 0.05 AND the median within-target delta
  >= 0.005 b/B.
- Model and tokenizer are inseparable in pretrained checkpoints: no
  "tokenizer robustness" claim is made anywhere. The v2 schema records
  the EXACT Unicode codepoint count of every scored target span (targets
  are fixed spans, so this is exact, unlike G3 stream positions), and
  results are reported in bits/byte AND bits/codepoint; G3 numeric
  cross-language inference remains barred (PREREG §6).
- Numeric constants above are frozen with this document; changes are
  logged amendments, reviewed before any data peek.

## 9. Power sketch (with clustering)

Within-target paired contrasts with an ILLUSTRATIVE sigma_delta ~= 0.15
b/B (a pure placeholder: battery item E emits mean nats/token over coarse
post-declaration suffixes and does NOT measure this quantity; the real
sigma_delta comes from the V2-b pilot, and no claim rests on the
placeholder): N=200 targets gives nominal 95% CI
half-width ~0.02 b/B per contrast per model, BUT targets cluster in
repos: with ~3 repos/language and a conservative intra-repo correlation
rho=0.3, the design effect ~ 1 + (200/3 - 1) * 0.3 means the EFFECTIVE
N for any between-language reading is closer to the repo count — which
is why F2 forbids confirmatory language claims (§8). Targets also
cluster WITHIN modules/files (shared notation, imports, style); the §7
models therefore include module/file as a nested grouping factor below
repo, and even within-repo contrast precision degrades below the naive
N=200 figure; effective power is estimated from the pilot, not assumed.
Behavioral power is ILLUSTRATIVE ONLY until the V2-b pilot estimates
the per-target pass@1 variance; no detectability claim is asserted
pre-pilot (review fix).

Compute (explicit call accounting): NLL arm ~200 targets x 6 presence
conditions x 3 budgets x ~4k tok ~= 14M scored tokens/model/corpus.
Generation arm: 200 x 4 conditions x 8 samples = 6,400 generations/model
/corpus (~2.5M generated tokens at 400-token bodies). Mutation arm:
200 x 3 mutations x (1 scoring + 8 repair samples) x 4 conditions ~=
2,400 scorings + 19,200 repair generations/model/corpus — repair is the
dominant cost and runs ONLY for the 1.5B sentinel model unless the pilot
justifies more. Lean checking ~11k-30k `lake` invocations/corpus on CPU
Slurm arrays.

## 10. Pipeline, compute, and gates

Extraction (CPU, login/compute nodes): Lean .ilean/LeanDojo caches on
POOL; AST extraction for Python. Driver `eval_paired.py` (to be built,
reusing layout.py + the evaluator's chunked-NLL core; same schema
discipline, schema_version bump). Generation determinism: fixed seeds
0–7, max 512 new tokens per sample, deterministic stop at the first
complete declaration (elaborator/AST-detected) or the token cap —
whichever first; no other stopping heuristics. Compute accounting lives
in §9 only (the earlier duplicate estimate is removed).
Gates: V2-a extraction validated on 20 targets/corpus (spans compile
standalone; closures verified against elaborator output);
V2-b pilot (20 targets, q25c-1.5b, all conditions) reviewed against §8
metric definitions — pilot data NEVER pools into confirmatory tests;
V2-c full run (human approves scale);
V2-d analysis per §7. Every gate follows the PREREG §11 boundary
protocol (commit hash, commands, results, disagreements).

## 11. Longitudinal repo-growth arm (separate, optional, own gate)

The only design that can speak to CODEBASE-SCALE claims: pin historical
snapshots of one repo (e.g. mathlib4 at ~6-month intervals, full-history
checkouts at recorded SHAs), hold the evaluation units FIXED and
GENUINELY FUTURE relative to every snapshot (post-2026 declarations,
backported syntactically where elaboration permits — feasibility is
itself a gate), and measure C(t | k3/k7 context drawn from snapshot S) as
the repository grows across S. Confounds logged up front: toolchain and
style drift across years, backport selection bias, snapshot-size vs
snapshot-age collinearity. This arm has its own design review before any
build; nothing in §1–§10 depends on it.

## 12. Corrections applied in review

FormalPhysics -> the intended reference is the FormalScience collection;
pairs enter only if a vetted semantic mapping exists at build time.

Logged build requirements (recorded at the boundary; implementation at
V2-a/b — these amend the sections cited):
- (§3) leakage rules, precise: EVERY arm excludes the target
  declaration span and NEAR-DUPLICATES of the target anywhere in the
  corpus (dedup by normalized-content similarity, threshold set at
  V2-a). Non-target portions of the target's OWN FILE are permitted
  ONLY in the predeclared k2 local-file arm — that is k2's definition —
  and are excluded from every other arm (k3-k7 draw from other files
  only), so k2 is the sole same-file condition and remains well-defined.
- (§1 E1b) interface-vs-implementation needs TWO sensitivity framings:
  equal-budget (as specified) AND same-dependency-set (equal B changes
  WHICH dependencies fit; holding the dependency set fixed and varying
  only interface-vs-implementation text is the cleaner mechanism probe).
- (§5) Lean verification REJECTS sorry/admit, new axioms, and unsafe
  escape hatches (native_decide, implemented_by, etc.); a "pass" is a
  kernel-checked proof/def with no new trusted surface.
- (§5) mutation probes retain ONLY verifier/test-killed mutants
  (non-killed mutants measure nothing); when a mutation changes
  tokenization, ΔNLL alignment is defined over source-span groups of the
  UNCHANGED regions plus the mutated span as one unit.
- (§1 E4) ĉ_eps is the smallest TESTED grid budget satisfying the
  criterion — a grid point, never a continuous causal threshold.
- (§8 F1) n=8 pass@1 ties/noise may attenuate Spearman: the hierarchical
  trial-level interaction model is CO-PRIMARY with the correlation rule,
  and the pilot decides whether n increases before V2-c.

## 13. Relation to prior work

Lean4Physics (arXiv:2510.26094) shows a behavioral PhysLib-context effect
(+11.9pp pass@16 on 200 theorems) — external validation for E5's
direction; our contribution is the controlled context-selection curve
(conditions k1–k7, budget-matched, contamination-stratified) + the NLL
bridge (F1) + cross-language pairing. OctoLong motivates k3/k4 vs k7;
the perplexity-caveat literature (2608.00624, 2410.23771) motivates the
AST-class split and the behavioral falsifier rather than aggregate-BPB
claims.
