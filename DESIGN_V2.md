# DESIGN_V2 — repository-context sufficiency on fixed targets (G2.5)

Status: **PRE-PILOT FROZEN after joint adversarial review**; V2-c still
requires the post-pilot human scale approval in §10. The G3 sweep is
exploratory/motivational; THIS design carries the claims. Nothing here
runs before its own reviewed pilot gate (§10).

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
- E2 known-dependency context gain (vs seeded random nondependency;
  formerly "relevance gain" — renamed because static resolution is
  incomplete, so the random arm is only guaranteed non-KNOWN-dependency):
  C(t|k5,B) − C(t|k4,B) (equal budget); stratified by the per-target
  resolution-coverage metric where resolution is weak (Python)
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
- Python: direct module-body functions/classes, including legitimate repeated
  bindings of the same name; closure from the stdlib AST resolved-import
  graph (same-repo only). A declaration is a source span, so Python identity
  is `(module, name, start_byte)`, not merely `module.name`.
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
k5 seeded random NONDEPENDENCY same-corpus context (excludes the known
   closure ∪ target file ∪ near-duplicates; control for k3/k4)
k6 retrieved context (BM25 over corpus, top chunks to budget)
k7 full-repo topo prefix (G3-style contiguous stream, budget-capped)

Byte-matching (frozen at amendment; supersedes both the tautological
B_eff=min rule and the ±tolerance draft): each arm's text is formed
DETERMINISTICALLY (selection rule of §14.1), then truncated ONLY at the
farthest boundary to the largest UTF-8-valid length <= B (shortfall <=
3 bytes; partial trailing unit recorded) — nominal matching, no padding,
and nesting becomes literal query-adjacent SUFFIX containment of the
rendered text across budgets. Whole-unit <= B is a sensitivity.
Availability below B is complete-case exclusion, and eligibility is
CONTRAST-SPECIFIC (frozen estimand->arm map, §14.2): only the presence
arms of THAT estimand must fill B, E3/E4 require their full budget grid,
and the omnibus all-arm panel is a sensitivity — per-estimand N and the
overlap matrix are always reported since populations differ. k5 matches
the arm it controls for (k3/k4) at the same budget; the k1-vs-k4
contrast (E1a — "k1-vs-k3" here was a typo, resolved to k4 to match §1
and §14.2, logged in PREREG §13) uses k5 at the same budget as the
named length control, so "context helps" is separated from "any tokens
present". (For k2's local
suffix, containment is suffix containment, not string-prefix identity;)
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

- Generation: sample n completions of the target body per (t, k1/k3/k4/
  k5/k6, B=16KB), temperature 0.8, per model (0.5B–7B ladder; big rungs
  only if approved); n is pilot-selected per §14.22 (default 8), seeds
  0..n−1. k5 is REQUIRED in the behavioral arm (§14.15):
  without it a behavioral k4 gain cannot be attributed to dependency
  RELEVANCE over generic same-corpus conditioning. max_new_tokens=512;
  target eligibility and outcome classes are frozen in §14.15.
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
  (Chen et al. 2021) with n samples per (t, condition, model) — n
  pilot-selected per §14.22 (default 8), seeds 0..n−1 — temperature
  0.8, top-p 0.95, no early stopping. Reported k is FIXED at {1, 8}
  for every n >= 8: pass@8 (unbiased from n samples) is the constant
  cross-tier comparison metric even when §14.22 raises n, so tiers
  with different n stay comparable; pass@n may be reported as
  DESCRIPTIVE but never replaces pass@8. F1's behavioral outcome
  remains the per-target pass probability c/n (§8).
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

- F1 (NLL-as-proxy): the context NLL gain is C(t|k1) − C(t|k4,16KB)
  (positive = context helps; k4 matches the E1a headline contrast —
  the earlier k3 here was the same resolved typo; the k3-gain variant
  is reported as a sensitivity only). The behavioral outcome is the per-target
  pass@1 gain estimated as c/n (c = verifier-passing samples of the
  pilot-selected n per §14.22, default 8) — pass@n at the full n is
  tie-saturated/binary and cannot support a rank correlation (review
  fix). Decision levels (harmonized with §14.16/§14.25): REJECTION
  uses one-sided 97.5% bounds (alpha=0.025 per co-primary, Bonferroni
  so union rejection controls FWER <= 0.05); SUPPORT uses one-sided
  95% bounds (intersection-union, no correction needed). REJECT NLL as
  the working proxy for a model family if the within-target Spearman
  correlation between NLL gain and pass@1 gain has a 97.5% UPPER bound
  below 0.3; CO-PRIMARY with it (per §12) is the trial-level
  hierarchical logistic model
  success ~ condition * target_NLL_gain + (1|target) — the INTERACTION
  carries the proxy question (does NLL improvement predict behavioral
  improvement?); condition + gain alone would only model target
  difficulty (review fix); its rejection bound is the 97.5% upper
  bound below 0. F1 outcomes are three-way per co-primary: REJECTED
  (97.5% upper bound below the null bound), SUPPORTED (95% lower bound
  above it), else INCONCLUSIVE — failing rejection is never itself
  evidence of support; the JOINT rule is §14.16. The pilot (V2-b)
  tunes the design and is NEVER pooled into this confirmatory test.
  V2-b additionally gates a DIRECTIONAL floor/ceiling viability rule,
  predeclared: if the pilot pass@1 rate under k4 at 16KB is below 0.05
  for a model tier, that tier moves ONE capability tier UP; above
  0.95, ONE tier DOWN; if the needed adjacent tier does not exist, F1
  is INFEASIBLE for that slot — by this rule alone, never by
  condition-contrast direction. Confirmatory F1 uses ONLY the semantic
  outcome strata (§14.23): lean-theorem-proof and
  python-semantic-covered targets, analyzed per (repo, class)
  separately — lean-def-typecheck and compile-only NEVER enter F1,
  and the k4 floor/ceiling aggregate is computed on the applicable
  semantic stratum.
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

Compute (explicit call accounting, corrected with §14.21-14.24): NLL
arm ~200 targets x 6 presence conditions x 3 budgets x ~4k tok ~= 14M
scored tokens/model/corpus, plus the k5 seed-sensitivity (2 extra
seeds at B* only: +400 cells ~= +1.6M tokens), the k6-realistic
sensitivity (§14.26: +200 cells at B* ~= +0.8M tokens), and, for
physlib only, the k4x arm (+1 condition on its grid). Generation arm:
200 x 5 conditions (k1/k3/k4/k5/k6, §14.24) x n samples = 1,000n
generations/model/corpus (8,000 at the default n=8, ~3.2M generated
tokens at 400-token bodies; the §14.22 rule may set n=16 or 32,
scaling linearly to 16,000 or 32,000). Mutation arm: DETECTION 200 x
3 mutations x 5 conditions = 3,000 scorings; REPAIR 200 x 3 x 2
conditions (k1/k4) x 4 samples = 4,800 repair generations/model/corpus
— repair is FROZEN to the 1.5B sentinel model; any expansion requires
its own preregistered gate (there is no pilot-justifies-more clause).
Lean checking = generation + repair verifications:
~12.8k `lake` checks/corpus at n=8 (8,000 + 4,800) up to ~36.8k at
n=32 (32,000 + 4,800), on CPU Slurm arrays; baseline-pass and
mutant-kill screening (§14.23, §12) add ~200 + ~600 checks/corpus on
top.

## 10. Pipeline, compute, and gates

Extraction (CPU, login/compute nodes): Lean .ilean/LeanDojo caches on
POOL; AST extraction for Python. Driver `eval_paired.py` (to be built,
reusing layout.py + the evaluator's chunked-NLL core; same schema
discipline, schema_version bump). Generation determinism: fixed seeds
0..n−1 (n pilot-selected per §14.22, default 8), max 512 new tokens
per sample, run to the token cap with NO stop sequences
(decoding-level no-early-stopping); the declaration boundary is
applied by the frozen deterministic post-hoc extraction rule of
§14.24 (this supersedes the earlier stop-at-first-declaration
wording). Compute accounting lives
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

## 14. V2-a implementation freeze (amendment, adopted pre-implementation)

14.1 Selection & rendering: dependency inclusion ranked by direct/short
graph distance; selected units rendered dependency-topologically with
nearer dependencies closest to the query (topological and distance
orders agree since dependencies lie at greater distance); SCCs (mutual
recursion) are collapsed and ordered by stable name. Nesting across
budgets is set inclusion by selection and, given §3's far-boundary
truncation, literal query-adjacent suffix containment of rendered text.

14.2 Frozen estimand -> presence-arm map for contrast-specific
eligibility: E1a {k4}; E1b {k3, k4}; E2 {k5, k4}; E3 per-arm over the
full budget grid; E4 {k4} over the full grid. Per-estimand eligible-N
and the pairwise overlap matrix are always reported; cross-estimand
comparisons carry a population label.

14.3 Closures are SAME-REPO, CROSS-FILE elaborated (Lean) / static
(Python) dependency context for both languages; excluded same-file and
external-package dependency mass are RECORDED per target
(same_file_mass, external_package_mass: bytes + counts).
Imported-package context is a later sensitivity — and for physlib,
whose mathematical spine is mathlib (external), that sensitivity is
expected to be LOAD-BEARING for any physlib-vs-mathlib E1b reading;
recorded here so no one is surprised at analysis time.
  LEAN SOURCE-RENDERABILITY (amended 2026-08-08, PRE-OUTCOME): a
  referenced constant with no declaration span first folds through an
  explicit length-5 .ilean parentDecl. For a parentless length-4
  definition location, it folds ONLY to the UNIQUE SMALLEST source
  declaration span enclosing that definition range; ambiguity or no
  enclosure remains recorded-unrenderable. This is geometric source
  provenance, not a name heuristic. The independent Lean-4.32 core
  machinery audit (2,433 modules; not a study corpus outcome) found
  110,224/123,621 residue occurrences position-recoverable with ZERO
  ambiguous smallest spans; the implemented full-tree replay reached
  98.34% occurrence-weighted renderability, leaving 13,239 occurrences
  explicit. Per-target internal renderability counts/coverage and
  name-prefix-agreement diagnostics are always reported. Coverage is a
  diagnostic covariate/stratum, NEVER a target-eligibility gate: gating
  would structurally select against projection-/proof-heavy targets.
  Coverage-floor analyses, if shown, are labeled sensitivities and do
  not replace the full frozen target population.
  LEAN FOREIGN DECLINFO (amended 2026-08-08, PRE-OUTCOME): a module's
  `.ilean.decls` table can include source information for an IMPORTED
  constant touched by a local attribute command; it is not necessarily a
  table of declarations defined by the paired source. Live exact-pair
  evidence: the 56-line `Mathlib.Tactic.ToDual` file carries six
  `Init.Core` constants with their original-source ranges near line 2458,
  while the `.ilean.references` identities place their usages at the local
  `to_dual` attribute commands. Lean extraction schema v3 excludes a decl
  entry at ANY range only when the reference table maps that exact constant
  name to exactly one defining module different from the embedded `.ilean`
  module. The entry, foreign module, ranges, and whether those ranges happen
  to fit the paired source are recorded, and every supporting usage range
  must resolve inside the paired source. Missing identity evidence, multiple
  defining modules, or an impossible range for a current-module declaration
  remains a hard source/artifact-integrity failure. The explicit assumption
  is that the attribute mechanism emits the foreign reference occurrence that
  caused its DeclInfo; without that mapping an impossible range fails, while
  a coincidentally in-range no-mapping foreign entry is a residual
  unmeasurable risk. This identity rule avoids
  both the observed out-of-range crash and the more dangerous coincidental
  in-range foreign slice; v2 artifacts are rejected.

14.4 Python closures: declaration-level static name/import edges where
resolvable; module-level fallback recorded with a per-target COVERAGE
metric = fraction of static references resolved to declaration level
(reported per target and per corpus); high-coverage subset is a
sensitivity, and no exact-closure claim is ever made for Python. Python may
legitimately bind the same module/name more than once (overload stubs,
dispatch registrations, compatibility definitions), so extraction schema v3
identifies every direct module-body declaration by
`[module, name, start_byte]`; graph edges are source/destination identity
sextuples. Ordinary name references resolve to the final source-order
module-body def/class binding; accordingly a same-name call in an earlier
twin points to the final twin, while final-twin self-recursion is removed.
This is an explicit best-effort convention: decorators, defaults,
annotations, class-body execution, later imports/assignments, conditional
rebinding, alias capture, and dynamic dispatch can observe a different
temporal binding. Each declaration
records binding count/ordinal/finality and duplicate-stratum membership;
these are diagnostics, never a V2-a eligibility gate. Within each Python
repo, every headline V2 estimand MUST also be reported after excluding
duplicate-stratum targets, as a mandatory sensitivity, because
identical/similar sibling declarations can
make the query-only prompt ambiguous or affect arms asymmetrically. The
primary remains the frozen per-declaration target population. §3's
near-duplicate removal still applies to EVERY arm, including local/topological
arms; the duplicate sensitivity covers same-name siblings below that lexical
threshold. For behavioral outcomes, §14.23's measured target-span test
coverage—not lexical finality or underscore naming—determines whether a
Python target enters the semantic-covered class.

14.5 Lean k3 rendering: extractor-derived verbatim declaration
header/body boundary plus an explicit FIXED body-omitted marker; prompt
text need not elaborate; exported-type canonical rendering is a
sensitivity. Implementation note (frozen with this doc): the environment
walk cannot recover the source-level shell, so the extractor adds a
SYNTACTIC pass (namespace/section/open/variable commands lexically above
the declaration); the §10 round-trip validation (prefix + body ==
original span; standalone re-elaboration) is the check that this
reconstruction is right.

14.6 Near-duplicates: primary lexical token 5-gram Jaccard >= 0.80
retaining identifiers/literals, plus exact normalized-hash always;
0.70/0.90 sensitivities; unit = declaration with a >= 20-token floor
(short tactic bodies would flood any threshold); threshold calibrated
ONLY by blind manual pair audit.

14.7 k7 = per-target variants of ONE canonical locked full-corpus topo
order, built ONLY from the PREFIX of that order ending immediately
before the target file's original position: remove the target file and
near-duplicate docs from that prefix, then take the query-adjacent
SUFFIX of the remainder to the exact budget B (far-boundary
truncation, §3). Files at or after the target's position NEVER enter
k7 — no future-relative-to-target leakage — and k7 is NOT the 2.4MB G3
sample. Targets too early in the order to fill B are recorded as
k7-ineligible at that budget (contrast-specific eligibility, §14.2
reporting rules apply).

14.8 k6 = declaration-unit BM25, highest score closest to the query;
query = the common unscored prefix text; universe = corpus declaration
units minus that target's exclusions; IDF frozen over the full unit
universe.

14.9 Common prefix = only the syntactically ACTIVE shell + the exact
target header; all prompt overhead is separately recorded; appending the
original body must round-trip byte-exactly in validation.
  DOCSTRING ASYMMETRY (amended 2026-08-08, PRE-OUTCOME, PREREG §13):
  the two languages place documentation on OPPOSITE sides of the
  header/body split. A Python docstring is a literal expression INSIDE
  the function suite, so its bytes remain in the SCORED BODY; a Lean
  doc comment (/-- ... -/) precedes the declaration and lands in the
  unscored shell/header side. Consequences, frozen: (a) the Python
  extractor records a docstring_bytes diagnostic per target;
  (b) any analysis touching body size or body NLL across languages
  MUST stratify by (or explicitly condition on) docstring_bytes —
  naive cross-language body-size or per-byte comparisons are FORBIDDEN
  as confounded by documentation placement, not model capability;
  (c) docstring bytes are NOT stripped from the scored body — the
  round-trip byte-exactness rule above stays primary, and stripping
  would silently change the scored object.

14.10 lean-zip/zlib acquisition is DEFERRED to an explicit V2
security-pair gate after the core extraction pilot; the G1 repo set is
not enlarged.

14.11 Schema constants: the paired driver uses PAIRED_SCHEMA_VERSION
(layout.py), independent of MEASUREMENT_SCHEMA_VERSION, so V2 evolution
never invalidates G3-path artifacts (PREREG §11 sequencing rule; G3b
requires a battery rerun whenever the source tree hash moved).

14.12 HEADLINE BUDGET: B* = 16 KiB is the single confirmatory budget
for E1a/E1b/E2 (frozen pre-implementation to close budget
selection-after-results). Every other budget on the grid is E3/E4
descriptive or sensitivity. Eligibility at B* under the §14.2 map
defines the primary populations; per-estimand N at B* is always
reported.

14.13 TOKEN-CAP ELIGIBILITY + EQUAL-TOKEN SENSITIVITY: a (target,
condition, budget, model) cell is eligible only if prefix + context +
target fits the model's position budget with ZERO truncation (asserted
at assembly; a truncated cell is invalid, never silently clipped).
Byte-matched B remains primary; an EQUAL-TOKEN sensitivity re-runs the
B* contrasts with context truncated to a fixed token count T* = 4096
tokens under each scoring model's tokenizer (equal bytes put the
target at different token positions per tokenizer x corpus — this
separates that mechanical confound from the linguistic claim). Target
token-position distributions are reported per corpus x tokenizer.

14.14 CANDIDATE UNIVERSE (one, target-relative, leak-free): U(t) =
corpus declaration units minus the target's file, minus its
near-duplicates (§14.6), minus the TRANSITIVE REVERSE dependency
closure of the target (reverse dependencies quote the target's
name/signature/usage — leaving them in k5 biases E2 toward null and
inflates k6; excluded mass recorded per target). k3/k4 draw the forward
closure; k5 draws from U(t) minus the forward closure; k6 retrieves
over U(t) WITH forward deps allowed (retrieval realism; retrieved
overlap with the closure recorded); k7 uses the §14.7 prefix, which is
reverse-dep-free by topology, and additionally drops same-SCC members
(cycle-mates are mutual dependencies). Post-hoc repair was considered
and REJECTED: it makes k5's realized sampling outcome-correlated and
cannot fix the frozen BM25 IDF universe.

14.15 BEHAVIORAL FREEZES: (a) k5 joins generation at B* only. (b)
Decoding as §5 (n samples with n pilot-selected per §14.22, default 8;
seeds 0..n-1; temperature 0.8, top-p 0.95, max_new_tokens=512, no
early stopping). (c) Eligibility: reference
body <= 448 tokens under the generating model's tokenizer (headroom
under the 512 cap; longer targets are structural failures in every arm
and length correlates with closure richness — an arm-correlated bias,
not just power loss); ineligible targets recorded. (d) Outcome
classes: Lean verification = re-elaboration of the UNMODIFIED repo
file with the generated body in the target's exact environment;
forbidden escapes are the UNIFIED §12 list as frozen in §14.23
(sorry/sorryAx/admit, new axioms, native_decide, implemented_by,
unsafe — no new trusted surface); a fixed 300s elaboration timeout
counts as FAILURE (never exclusion — timeouts correlate with
difficulty and exclusion would be arm-correlated missingness). Class
structure, baseline-pass, and coverage requirements are frozen in
§14.23 (four classes, never pooled).

14.16 JOINT F1 DECISION RULE + INFERENCE SPEC: the two co-primaries
(within-target Spearman; hierarchical logistic interaction, §8) form
ONE decision by intersection-union — PROXY SUPPORTED only if BOTH
support (Spearman one-sided 95% LOWER bound > 0.3 AND interaction
one-sided 95% LOWER bound > 0); PROXY REJECTED if EITHER rejects at
its Bonferroni level (Spearman one-sided 97.5% UPPER bound < 0.3 OR
interaction one-sided 97.5% UPPER bound < 0, per §14.25); else
INCONCLUSIVE. Union-rejection is
deliberate: any failing co-primary disqualifies NLL as the working
proxy. No alpha adjustment is needed for the IUT support claim; all
other §8 tests keep §7's Holm control within repo, and the F1 family
is per (repo, model family) with results reported for ALL families
(no family selection). CIs for both co-primaries: cluster bootstrap by
FILE (targets nested in files; 2000 resamples, seed 20260808); the
logistic keeps (1|target) random intercepts. Generation samples are
never resampled as independent units.

14.17 RENDERING FREEZE: context units are joined by ONE blank line;
each unit carries a single one-line comment banner in the language's
comment leader ("-- ctx: <repo-relative-path>", "# ctx: ...",
"// ctx: ..."). Banners NEVER contain the target's own path, module
name, or declaration name (a banner on a leaking unit is itself a leak
channel). Identical rendering machinery across k2-k7 — a delimiter
difference would be a hidden condition. Banner and delimiter bytes
count toward B and are recorded per cell.

14.18 SHORT-TARGET DEDUP: identifier-normalized EXACT-hash duplicate
detection applies at ALL lengths (the 5-gram Jaccard floor of §14.6
stays at 20 tokens — lowering it floods). Targets under 20 tokens form
a recorded stratum with a predeclared exclusion sensitivity; their
rename-variant near-duplicates are acknowledged as residually
uncontrolled below the floor.

14.19 DETERMINISTIC TARGET SAMPLING: within each §2 stratum, targets
are ranked by a seeded priority key and quotas filled in ascending
priority order — corpus-size-independent, rerun-stable, and blind to
outcomes (same discipline as the G3 seeded-priority selection).
Under-filled strata are recorded and never rebalanced after any data
peek.
  PRIORITY KEY (amended 2026-08-08, PRE-OUTCOME — before any committed
  extraction or pilot sample existed; PREREG §13): the target identity
  is source-tree-qualified. Fully-elaborated Lean names are unique per
  ENVIRONMENT, not per source tree — the live compiler-source stress
  run hit `main` defined in both LakeMain and LeanChecker — so a bare
  name does not identify a node. Frozen LEAN encoding: the key is
    SHA256(UTF-8(json.dumps(["v2a:20260808", <repo>, <module>,
                             <declName>],
                            ensure_ascii=False,
                            separators=(",",":"))))
  i.e. SHA256 over the canonical compact-JSON array. JSON string
  escaping length-delimits every field, so quoted Lean identifiers
  («...») that may contain arbitrary punctuation (including ':')
  cannot re-split into a different (repo, module, decl) — plain
  delimiter concatenation could not guarantee this. The earlier
  colon-concatenated, non-module-qualified form was never used to draw
  any sample. Python schema v3 uses the analogous key
    SHA256(UTF-8(json.dumps(["v2a:20260808", <repo>, <module>,
                             <name>, <start_byte>],
                            ensure_ascii=False,
                            separators=(",",":"))))
  because repeated module/name bindings are legal; the preceding v2
  module-qualified-fqname key was never used to draw a Python sample.

14.20 PHYSLIB EXTERNAL-CONTEXT HARD GATE: physlib closure results
(E1a/E1b and any physlib-vs-mathlib reading) are UNINTERPRETABLE until
the k4x arm exists: k4 plus the build-pinned EXTERNAL closure rendered
under the same rules, drawn from the mathlib REVISION pinned in
physlib's lake-manifest (recorded and locked; NOT the corpus-lock
mathlib HEAD — version skew would leak anachronistic content).
physlib's mathematical spine is external, so same-repo k4 is
structurally handicapped relative to mathlib's internally-complete k4;
§14.3's warning is upgraded to this hard gate. Python external
closures stay recorded-only (asymmetry logged, §14.3).

14.21 k5 SEED POLICY: the k5 draw for target t ranks U(t) minus the
forward closure by per-(target, seed) hash priorities, so draws are
independent ACROSS targets by construction (no shared global
permutation; the one draw per target is genuinely dispersed).
  KEY (amended 2026-08-08, PRE-OUTCOME — no k5 draw has ever been
  made; same collision as §14.19, PREREG §13): the key is
    SHA256(UTF-8(json.dumps(["k5:<seed>", <repo>,
                             <target-identity...>,
                             <unit-identity...>],
                            ensure_ascii=False,
                            separators=(",",":"))))
  where the identity fields are spliced flat into the array. For LEAN
  targets/units the identity is the pair <module>, <declName> (bare
  fully-elaborated names collide across modules — LakeMain vs
  LeanChecker `main`); for PYTHON schema v3 it is the triple <module>,
  <name>, <start_byte> (module-qualified names can be rebound). Canonical
  compact JSON
  length-delimits every field, so quoted Lean identifiers containing
  ':' cannot re-split (same rationale as §14.19); the prior
  colon-concatenated form —
  SHA256("k5:<seed>:<repo>:<target-fqname>:<unit-id>") — was never
  used to draw anything.
Primary = seed 0 everywhere. Seeds 1 and 2 re-run the k5 NLL ARM ONLY,
at B* over the full eligible set, as a frozen seed-sensitivity;
behavioral k5 is seed 0 only. Statistical note (recorded): E2 rests on
N independent per-target draws, and draw variance propagates through
target-level resampling — the multi-seed arm is a diagnostic, not a
repair.

14.22 V2-b PILOT GOVERNANCE: pilot analysis is BLINDED to contrasts —
arm labels are anonymized before analysis, and only nuisance
quantities are computed (eligibility yields, ICC/cluster variances,
per-target pass-probability reliability, timeout and
extraction-failure rates). Two design constants are then set
MECHANICALLY: (a) confirmatory target N per repo, in [200, 400] = the
MAXIMUM across E1a, E1b, and E2 of each estimand's smallest N whose
projected CI half-width at B* meets the frozen 0.02 b/B precision
figure under pilot-estimated nuisance; (b) completion n = the
smallest of {8, 16, 32} whose ARM-ANONYMOUS target-level
pass-probability reliability is >= 0.8 — made empirically
IDENTIFIABLE by generating (or adaptively accumulating) UP TO 32
pilot completions per (pilot target, arm) under masked labels;
reliability at each candidate n = REPEATED random half-splits (200
resplits, seed 20260808) of n draws subsampled from those masked
completions, computed SEPARATELY inside each anonymized arm and the
applicable semantic outcome stratum. Each resplit uses the Pearson
correlation of per-target pass proportions across halves and the
SPEARMAN-BROWN correction to project the half-split (length n/2)
estimate to full length n; the gate uses the MINIMUM median corrected
reliability across arms, so pooling arm-level rate differences cannot
inflate it. A raw 32-draw split would estimate n=16 reliability, not
n=32. If no N <= 400 meets precision for all
three estimands, or no n <= 32 meets reliability, F1 is DECLARED
INFEASIBLE and reported as such — never discretionarily redesigned.
The ONLY unblinded pilot aggregate is the k4-arm aggregate pass rate
per model tier, exposed solely for the frozen DIRECTIONAL
floor/ceiling tier rule (§8: <0.05 moves one tier UP, >0.95 one tier
DOWN, missing adjacent tier -> infeasible for that slot); no condition
contrasts or contrast directions are exposed. Pilot data
never pools into confirmatory tests (§8).

14.23 OUTCOME CLASSES (supersedes the §14.15(d) two-way split): FOUR
classes, never pooled, per-class N always reported —
lean-theorem-proof (kernel-checked proof), lean-def-typecheck
(well-typed body; semantically weaker, stated as such),
python-semantic-covered, compile-only (any language). Lean passes use
the UNIFIED §12 forbidden-escape list: sorry/sorryAx/admit, new
axioms, native_decide, implemented_by, unsafe — a pass adds NO new
trusted surface. BASELINE-PASS is required: the reference body must
pass the same harness verifier or the item is excluded as
HARNESS-INVALID (excluded counts reported per corpus). The
python-semantic class requires MEASURED execution coverage of the
target span by the selected test subset; without it the item demotes
to compile-only.

14.24 GENERATION-ARM CONSISTENCY: five behavioral arms
{k1, k3, k4, k5, k6} at B* (§9 cost accounting corrected to match).
Mutation DETECTION scores all five arms; mutation REPAIR runs only
{k1, k4} at B* with n=4 samples, same decoding. "No early stopping" is
DECODING-LEVEL only: every sample runs to the fixed 512-token budget
with no stop sequences (stop-strings tokenize differently per model
and would bias across families); the declaration boundary is applied
by a frozen deterministic POST-HOC EXTRACTION rule — first complete
declaration body via the language's parser; extraction failure =
outcome failure. §10's earlier stop-at-first-declaration wording is
superseded by this rule.

14.25 AGGREGATION + FWER: within-repo confirmatory estimates weight
targets EQUALLY (the estimand is per-target sufficiency; byte
weighting would let a few long targets dominate); byte-weighted is a
reported sensitivity. F1 REJECTION arms are Bonferroni-corrected: each
co-primary rejection test is one-sided at alpha = 0.025 (97.5% CI
bounds), so union rejection controls FWER <= 0.05; SUPPORT is
unchanged (intersection-union with 95% one-sided lower bounds needs no
correction).

14.26 k6-REALISTIC SENSITIVITY: a labeled reverse-deps-allowed
retrieval variant (BM25 over U(t) plus the transitive reverse closure)
runs as a SENSITIVITY only — it never enters any §14.2 estimand
contrast — with retrieved reverse-dependency mass reported per target.
Primary k6 stays leak-free (§14.14).

14.27 k4x CONSTRUCTION: no special-casing — the §14.1 selection rule
runs over the COMBINED internal + external dependency graph at the
lake-manifest-pinned external snapshot (§14.20); identical
distance/SCC/tie-break and rendering rules, same budgets and §14.2
map; internal vs external context mass recorded per cell.

14.28 TRUNCATION GATE: exact-B far-boundary truncation stays PRIMARY
(whole-unit-primary REJECTED: arms differ systematically in unit-size
distributions, so whole-unit matching reintroduces directional
per-arm byte shortfalls — a first-order confound traded for a smaller
symmetric artifact at the maximally distant boundary; content-aware
cleanup is rejected on the same grounds). Per-cell truncation
reporting: truncated-unit fraction and partial-unit bytes, per arm.
Predeclared gate on the frozen whole-unit sensitivity, computed on
the COMMON eligible-target set (targets eligible under BOTH the
exact-B and whole-unit schemes; per-scheme eligible N reported — a
population shift must never masquerade as a truncation effect): a
sign flip on a confirmatory contrast, or divergence exceeding
max(0.005 b/B, 50% of the absolute primary point estimate) — the
floor keeps the gate well-defined for near-zero estimates — labels
the result TRUNCATION-SENSITIVE in all reporting, both numbers shown.
