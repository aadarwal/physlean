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
POOL; AST extraction for Python. Driver `eval_paired.py` reuses
`layout.py` + the evaluator's chunked-NLL core under §15.A9's exact
harness binding and schema discipline. Generation determinism: fixed seeds
0..n−1 (n pilot-selected per §14.22, default 8), max 512 new tokens
per sample, run to the token cap with NO stop sequences
(decoding-level no-early-stopping); the declaration boundary is
applied by the frozen deterministic post-hoc extraction rule of
§14.24 (this supersedes the earlier stop-at-first-declaration
wording). Compute accounting lives
in §9 only (the earlier duplicate estimate is removed).
Gates: V2-a extraction validated on 20 targets/corpus (spans compile
standalone; closures verified against elaborator output);
`finalize_v2a.py` hash-verifies the job envelope and combines the otherwise
independent extraction, raw-closure, and boundary-compile reports; no
individual validator may set this gate complete by itself.
The combiner also hard-binds the frozen extraction-code commit, corpus
revisions, Lean artifact report, Python interpreter binary, and the
PhysLib-manifest-pinned mathlib revision; merely well-formed or mutually
self-consistent revision claims are insufficient.
`finalize_v2a_cohort.py` then requires exactly the five frozen corpus gates,
rehashes each gate report and every transitive evidence file, and refuses a
missing, duplicate, drifted, mixed-commit, or partially passing cohort; that
cohort artifact is V2-b's structural input.
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

14.1 Selection & rendering (operational form frozen in §15.A4/§15.A4b,
adopted pre-outcome): every presence arm builds ONE canonical maximal
target-level rendering and every budget is an exact byte suffix of it
(§3). For k3/k4/k4x the order is the §15.A4 canonical SCC-DAG order:
dependencies precede dependents (Kahn over the reversed rendering
graph); TOPOLOGY IS PRIMARY, descending BFS distance is only the
ready-set tie-break, then the frozen seeded hash. The earlier
parenthetical claiming topological and distance orders always agree
was WRONG for same-shell dependency edges (t->a, t->b, a->c, c->b) and
is RETRACTED. SCCs (mutual recursion) are collapsed, identified by
their lexicographically smallest member, and rendered internally in
stable-name order. Nesting across budgets is literal query-adjacent
byte-suffix containment BY CONSTRUCTION, property-tested for every
arm and budget pair (§15.A4b).

14.2 Frozen estimand -> presence-arm map for contrast-specific
eligibility: E1a {k4}; E1b {k3, k4}; E2 {k5, k4}; E3 per-arm over the
full budget grid; E4 {k4} over the full grid. Per-estimand eligible-N
and the pairwise overlap matrix are always reported; cross-estimand
comparisons carry a population label.

14.3 Closures are SAME-REPO, CROSS-FILE elaborated (Lean) / static
(Python) dependency context for both languages; excluded same-file mass is
RECORDED per target as bytes + counts. External-package reference mass is
recorded as exact extracted occurrence counts, but its byte mass is `null`
with an explicit unbound-source reason unless that arm binds a separately
pinned external source snapshot (physlib k4x does; same-repo k3/k4 do not).
Inventing bytes from an installed or corpus-HEAD package would silently bind
the wrong external version; this supersedes the earlier blanket
`external_package_mass: bytes + counts` requirement.
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
retaining identifiers/literals, plus the VERBATIM-TOKEN exact hash
always (LAYOUT-PRESERVING typed token stream, comment-stripped,
identifiers retained, JSON-serialized — §15.A6);
identifier-NORMALIZED-hash exclusion is a separately validated
candidate rule, primary only per §15.A6's collision-audit gate;
0.70/0.90 sensitivities; unit = declaration with a >= 20-token floor
(short tactic bodies would flood any threshold). Operational freeze in
§15.A6: per-language lexers (Python stdlib tokenize; Lean identifier
predicates ported from the pinned toolchain, unicode-correct), exact
no-false-negative size+prefix candidate filtering computed once at
t = 0.70, and TWO fully deterministic blind audits — the Jaccard-bin
calibration (40 labeled pairs per language, mechanical label->outcome
mapping onto {0.70, 0.80, 0.90, lexical-inconclusive}) and the
normalized-hash collision audit (group-sampled, per language x length
band) — with no human choice after labeling.

14.7 k7 = per-target variants of ONE canonical locked full-corpus topo
order, built ONLY from the PREFIX of that order ending immediately
before the target file's original position: remove the target file,
near-duplicate docs, AND every file containing at least one unit of
the target's transitive reverse closure under the FROZEN EXTRACTED
GRAPH of that language (§15.A8; removed files/bytes recorded), then
take the query-adjacent SUFFIX of the remainder to the exact budget B
(far-boundary truncation, §3). The filtered stream is the arm's ONE
maximal rendering (§15.A4b): the filter is applied once per target,
never per budget. Files at or after the target's position NEVER enter
k7 — no future-relative-to-target leakage — and k7 is NOT the 2.4MB G3
sample. Targets too early in the order to fill B are recorded as
k7-ineligible at that budget (contrast-specific eligibility, §14.2
reporting rules apply).

14.8 k6 = declaration-unit BM25, highest score closest to the query;
query = the common unscored prefix text; universe = corpus declaration
units minus that target's exclusions; IDF frozen over the full unit
universe, document frequency computed over the SAME CORPUS's
declaration units (documents = declaration units). Equal-score ties
break by the frozen k6tie hash with the LOWER hash NEARER the query:
the arm's one maximal rendering is ordered top-to-bottom by
(score ascending, tie-hash descending) and every budget is a byte
suffix of it (§15.A4b).

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
overlap with the closure recorded); k7 uses the §14.7 prefix,
REVERSE-DEPENDENCY-FILTERED WITH RESPECT TO THE FROZEN EXTRACTED
GRAPH of its language — never claimed globally reverse-dep-free: the
Lean elaborated graph supports the removal, while the Python static
graph is best-effort (§14.4), so residual dynamic/alias/dispatch
reverse-dependency leakage is RECORDED as a standing Python k7
caveat — and additionally drops same-SCC members (cycle-mates are
mutual dependencies). Post-hoc repair was considered
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
residual dedup risk depends on the §15.A6 collision-audit outcome:
where identifier-normalized exclusion is ACTIVATED, the residual is
NON-EXACT rename variants (exact identifier-only renames with
otherwise identical token streams are caught) plus the audit's
measured false-positive rate; where it is sensitivity-only,
under-20-token rename-clones are uncaught in the primary (the Jaccard
floor) and this is recorded as a leakage-direction risk. Both counts
reported.

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
pilot completions per (behavior-eligible pilot target, arm) under
masked labels; all 20 committed identities remain in the masked table,
while a model-cap, baseline, or class-verifier exclusion carries null
outcomes and never an imputed failure;
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
to compile-only. The coverage/test-subset machinery is frozen in
§15.A7; a python-semantic pass means PASS UNDER THE FROZEN CAPPED
VERIFIER (first 4 seeded covering test nodes). Deterministic
arm-independent test SELECTION does not make capped-verifier
MISCLASSIFICATION arm-uncorrelated — arms can produce different bug
types escaping the same tests at different rates — so
semantic-correctness CONTRASTS may carry differential measurement
error; that caveat attaches wherever python-semantic contrasts are
reported, and absolute python-semantic pass rates are never headline
quantities. If §15.A7's per-repo pre-generation feasibility gate
declares a repo's semantic verification infeasible, Python
confirmatory F1 is INFEASIBLE FOR THAT REPO (§14.22/§8 restrict F1
to semantic strata; compile-only never substitutes); NLL estimands
proceed and compile-only outcomes remain descriptive.

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

## 15. V2-b implementation freeze (adopted pre-outcome)

Consolidates the jointly reviewed A1-A10 specification (v4 + v5
addendum + the A6 hash addendum). Conflicting older wording in §14.1,
§14.6-14.8, §14.14, §14.18, and §14.23 has been reconciled IN PLACE
above; where any residual tension is found, this section governs.
Adopted before any pilot sample draw, any paired scoring, and any
calibration or collision label. Conventions: hash keys are SHA256 over
UTF-8 canonical compact JSON arrays (ensure_ascii=False,
separators=(",",":")); seed family "v2b:20260808"; identities are Lean
[module, declName] and Python [module, name, start_byte], spliced flat
into key arrays; "recorded" means emitted in run evidence.

15.A1 STRATUM QUOTAS. Sampler-crossed strata per corpus: length
tercile x module-centrality tercile x first-add cohort = 18 cells
(<20-token stratum, Python duplicate stratum, Lean renderability
coverage stay recorded covariates only). Length = the extraction-
recorded scored-body size body_bytes. Population = all extraction-
eligible targets (Lean: eligible_kind AND selection_contained AND
split_kind != null; Python: every direct module-body def/class);
near-dup and <20-token targets included. Terciles: sort ascending;
q1 = value at floor((n-1)/3), q2 = at floor(2*(n-1)/3); tercile(v) =
1 if v <= q1 else 2 if v <= q2 else 3; ties share a tercile, realized
imbalance recorded. Quotas: proportional Hamilton — floors, then one
seat each in descending fractional-remainder order, remainder ties by
ascending cell label "L<t>-D<t>-C<pre|post>"; within-cell fill in
ascending §14.19 priority order; shortfalls recorded, never
rebalanced. Pilot: identical rule at N = 20 per corpus; unsampled
cells recorded.

15.A2 FIRST-ADD DATE (a CONSERVATIVE PROXY for content age, not
target-creation time). Per file:
git log --follow --find-renames=50% --diff-filter=A
--format=%H|%aI|%cI -- <path>; parse EVERY add record. With >= 1 record,
first_add = the MINIMUM over every author AND
committer timestamp of ALL records; timestamp ties choose the
lexicographically smallest commit hash as recorded provenance. If any
author date precedes the repository's first commit, set
author_date_anomalous — RECORD ONLY, never a fallback (a later date
could mint a false-clean). vendor_flagged = OR over ALL add commits of
(a) the existing audited prep_streams subject-based vendor/port/copy
classifier (same function, no parallel regex), (b) bulk-import (the
commit added >= 100 files), (c) path segment in {vendor, third_party,
external}; per-commit signal values recorded; diagnostic only, with
the vendor-excluding clean-cohort sensitivity predeclared. Cutoff:
post/clean requires first_add STRICTLY LATER than 2024-11-12T23:59:59Z
(boundary day = PRE); raw dates stored so other family cutoffs
recompute at analysis. Shallow clones refused; git version recorded.
The zero-add merge-topology case uses ONLY the conservative PRE witness
rule in §15.A12; it can never mint a post/clean target.

15.A3 CENTRALITY (per §2, MODULE-level). centrality(target) =
in-degree of the target's module in the same-repo module import graph
(distinct importing corpus modules; Lean direct_imports, Python
resolved imports; deduplicated). Terciles over the target population
by the 15.A1 rule; ties recorded. Declaration-level in-degree is
recorded per target as an analysis covariate, never sampler-crossed.

15.A4 k4/k4x CANONICAL ORDER. Raw edges are DEPENDENT -> DEPENDENCY.
SCC-condense the same-repo declaration graph. Context universe of t =
SCC-units forward-reachable from the TARGET SCC'S OUTGOING EDGES; the
entire target SCC (t + cycle-mates) is EXCLUDED (cycle-mates are
reverse-dependency leak channels, §3), excluded mass recorded.
Canonical order = Kahn topological sort in which every dependency
precedes its dependents (Kahn over the reversed rendering graph);
ready-set tie-break: BFS distance from t DESCENDING, then ascending
SHA256(json(["k4sel:v2b:20260808", <repo>, <target-identity...>,
<unit-identity...>])). Topology is primary; no global farthest-first
claim. Collapsed-SCC unit identity = lexicographically smallest
member; members render internally in stable-name order. The nearest-
rendered end is query-adjacent.
  CHUNKS AND SEPARATORS. normalize(payload): let r = the trailing LF
  run length (0x0A only; other whitespace is payload). r = 0: append
  one LF (n_removed_terminal_lf=0, n_appended_terminal_lf=1); r >= 1:
  remove r-1 (n_removed_terminal_lf=r-1, n_appended_terminal_lf=0).
  Idempotent; counts recorded; all non-terminal-LF bytes preserved
  (whitespace-only terminal lines survive). chunk(unit) = banner_line
  + LF + normalize(payload); rendering = chunks joined by exactly ONE
  additional LF (exactly one empty line between chunks); the join LF
  belongs to the PRECEDING (farther) chunk's span; the last chunk has
  none. Every byte belongs to exactly one chunk span, so each suffix
  has at most one partial unit, byte-exactly.
  BUDGETS. Budget B = the largest UTF-8-valid byte suffix <= B of the
  one canonical rendering (§3 far-boundary truncation = the leading
  partial span). Selected units at B are DERIVED from the suffix and
  recorded. Rendering shorter than B => ineligible at B (§14.2).
  Implementation note (frozen, property-tested byte-equivalent): the
  full unit-id order is always computed; materialization may run from
  the query-adjacent end backward past B_max plus one unit span.
  k4x: identical construction over the combined internal+external
  graph at the lake-manifest-pinned snapshot (§14.20/§14.27).

15.A4b ALL-ARMS SUFFIX INVARIANT (required by §3 for k2-k7). ONE
maximal target-level rendering per (arm, target[, seed]); every budget
is an exact byte suffix; per-budget reselection or re-rendering is
PROHIBITED in every arm. Orders: k2 = the target file's bytes strictly
above the target span with near-duplicate-of-target spans excised once
(splice points recorded); k3 = the 15.A4 canonical order rendered
interface-style (15.A5); k4/k4x = 15.A4; k5 (per seed) = top-to-bottom
DESCENDING §14.21 priority hash, so the LOWEST hash (best selection
priority) is query-nearest and every suffix reproduces the frozen draw;
k6 = top-to-bottom (score ascending, k6tie hash DESCENDING) — highest
score nearest, lower hash nearer within an equal-score block, tie key
SHA256(json(["k6tie:v2b:20260808", <repo>, <target-identity...>,
<unit-identity...>])), scores recorded, §14.26 variant same rule over
its universe; k7 = the §14.7/15.A8 filtered stream. Sole exception: k1
(absence arm, nothing to render). §14.13's T* = 4096 equal-token
sensitivity is the token-count suffix of the SAME maximal rendering
under each scoring tokenizer, never a reselection. The assembly
validator asserts byte-suffix nesting for EVERY arm and budget pair as
a HARD failure.

15.A5 k3 BODY-OMITTED MARKER (payload definitions; banners/joins come
only from 15.A4). indent(x) = leading whitespace bytes of line x.
Python: H = header bytes through the suite colon; I = indent(first
original body line) if the body starts on its own line, else
indent(header first line) + 4 spaces; payload = H + LF + I +
"...  # ctx: body omitted" + LF. Lean: H = header bytes (the split
token :=/where/| belongs to the body and is dropped); I =
indent(delimiter line) if the delimiter starts its line, else
indent(declaration first line) + 2 spaces (RELATIVE, never absolute);
payload = H + LF + I + "-- ctx: body omitted" + LF. C++ (stretch):
H + " { /* ctx: body omitted */ }" + LF. No sorry anywhere. Recorded
asymmetry: Python k3 units parse, Lean k3 units do not elaborate;
§14.5's exported-type sensitivity probes this choice.

15.A6 NEAR-DUPLICATES, OPERATIONAL. Lexers produce LAYOUT-PRESERVING
TYPED TOKEN STREAMS: Python stdlib tokenize retains ordinary lexical
tokens {NAME, OP, NUMBER, STRING, FSTRING_*} AND maps INDENT, DEDENT,
and logical NEWLINE to canonical typed sentinel records, dropping
ONLY COMMENT, NL, ENCODING, ENDMARKER (block layout is semantic —
dropping it would let different nestings collide); Lean: one sequential
scanner skips `--` and nested `/- -/` comments while RETAINING ordinary,
raw, and character literals as typed records (the extraction code_mask
also masks strings and therefore is not the A6 lexer), identifier
characters per the pinned
toolchain's isIdFirst/isIdRest (isLetterLike + isSubScriptAlnum)
transcribed verbatim and cited, «...» single tokens, numeric/string
literals, any other non-space char a token, plus a typed LAYOUT
SENTINEL emitted whenever a token begins on a later physical line
than the previous token, carrying that line's exact leading
horizontal whitespace (strings/comments cannot manufacture
sentinels: sentinels are typed records, never text); unicode
fixtures pinned.
  TWO EXACT HASHES, one serialization: the hash input is the UTF-8
  canonical compact JSON array of typed token records
  [[kind, value], ...] including layout sentinels — never a
  delimiter join (Lean quoted identifiers/literals can contain
  spaces; JSON typing length-delimits every record). (1) VERBATIM-
  TOKEN hash — identifiers retained — is self-validating and ALWAYS
  PRIMARY at all lengths. (2) IDENTIFIER-NORMALIZED hash — each
  non-keyword identifier record replaced by a typed ["IDRANK", <k>]
  integer record, k = first-occurrence rank. Python exempts the pinned
  runtime's exact `keyword.kwlist ∪ keyword.softkwlist`. Lean does NOT use a
  curated vocabulary: before any A6 corpus hash, inspect the parser state after
  each pinned `Mathlib`, `Batteries`, and `Physlib` umbrella import. Record two
  sections separately: (1) `Lean.Parser.getTokenTable` values and (2) the exact
  simple-name keys from every registered parser category's leading and trailing
  dispatch tables. The second section is necessary because Lean intentionally
  keeps contextual tactic heads usable as ordinary identifiers, so they are
  dispatch keys rather than reserved tokens. Exclude and record the complete
  builtin literal-kind key set (`choice`, `ident`, `str`, `num`, `scientific`,
  `char`, `name`) plus non-simple pseudo keys; never treat those internal keys
  as source-language words. Retain exactly the remaining values satisfying the
  same pinned isIdFirst/isIdRest predicates; seal the sorted union, per-token
  section/corpus provenance, excluded-key counts, and canonical JSON SHA256 as
  a write-once language-wide keyword freeze. All three source sections and the
  union are source/revision bound and test-pinned. This includes contextual
  tactic heads such as `rfl`, `simp`, and `omega` without pretending Lean has a
  small static reserved-word list. Exempting a spelling
  later used as an ordinary binder can only under-normalize that rename-clone
  (recall/sensitivity), never make two unequal token streams collide.
  Identifier normalization — conflates rename-clones with
  same-skeleton distinct entities (sin-vs-cos wrappers; Lean
  same-skeleton proofs) and is therefore a CANDIDATE rule requiring
  its own validation. Jaccard 5-grams remain over the LEXICAL
  records excluding layout sentinels (similarity is formatting-
  robust; identity is layout-exact); the 20-token floor counts
  lexical records only. Required fixtures: identical lexical tokens
  with different Python nesting hash differently; identical Lean
  tokens at different layout columns hash differently; a
  space-containing quoted identifier/literal hashes differently
  from the corresponding multi-token sequence.
  COLLISION AUDIT (blind, group-sampled). Collision group = maximal
  within-corpus unit set sharing one normalized hash with >= 2
  distinct verbatim-token hashes; a group's FULL normalized typed-record
  count INCLUDING layout sentinels fixes its length band (under20 / geq20),
  distinct from the lexical-record-only Jaccard floor. Per (language, band):
  within each corpus rank groups by ascending
  SHA256(json(["a6hashgrp:v2b:20260808", <repo>, <normalized-hash>]));
  interleave corpora round-robin (ascending repo name); take up to 8
  groups; from each group label exactly ONE member pair via the
  O(m log m) SEEDED MEMBER RULE (a minimum over all member pairs is
  O(m^2) and recreates the pair explosion inside a giant group):
  rank members by ascending
  SHA256(json(["a6hashmember:v2b:20260808", <repo>,
  <normalized-hash>, <member-identity...>])); the pair = the
  first-ranked member plus the first later-ranked member with a
  DIFFERENT verbatim-token hash (exists by the group definition);
  both members' ranks and verbatim hashes recorded.
  Underfilled bands label all groups
  (recorded); max 32 labels over both languages; labels committed
  before unblinding, seeded-shuffled presentation. Clone rubric:
  duplicate = same implementation/specification modulo ONE systematic
  identifier renaming — not merely a shared syntax skeleton; differing
  API calls or referenced constants = not a clone.
  ACTIVATION (per language x band; a thin or empty band can never
  vacuously validate): normalized-hash exclusion joins the PRIMARY
  dedup for band b of language L iff >= 8 labeled collision pairs
  exist in (L, b) AND ALL 8 are labeled true clones (8/8 — with
  n = 8 the only precision meeting the frozen >= 0.90 bar; one
  false positive demotes the band). Otherwise it is a
  LABELED SENSITIVITY for that band and the primary is verbatim-token
  hash + calibrated Jaccard. Residual risk recorded per §14.18.
  BLIND PRESENTATION BOUNDARY: collision and Jaccard selections are merged
  into one seeded-interleaved stream; if one source pair is selected by both,
  it is shown once and its one label serves both gates. The presentation
  exposes EXACTLY opaque seeded pair id, language, and the two verbatim source
  spans, with a deterministic seeded side swap. It omits corpus, identities,
  audit origin, bin/band, similarity statistics, and token hashes. Each span is
  recovered through the hash-bound extraction, its live source is rehashed,
  and both token hashes are recomputed against the sealed A6 unit before
  display. Every pair uses one binary `duplicate` / `not-duplicate` answer
  under the frozen clone rubric above; optional notes are diagnostic only.
  The exact complete label file and presentation must equal committed HEAD
  blobs, and the label path must have exactly one touching commit, before a
  separate write-once unblinder reconstructs the hidden mapping
  and invokes either mechanical gate. This is procedural, not adversarial,
  blindness because the labeler is also an experimenter: accidental leakage is
  prevented and deliberate inspection of the sealed packet remains auditable,
  not technically impossible.
  JACCARD AT SCALE (exact at t = 0.70 so all sensitivity thresholds
  derive from one candidate set): size filter |A|/|B| >= 0.70; global
  canonical 5-gram order = ascending (same-corpus declaration-unit
  document frequency, tie ascending SHA256 of gram bytes); prefix =
  first floor(0.30*s)+1 grams; inverted index over prefixes; exact
  verification. REQUIRED equivalence test vs brute force on slices
  <= 2,000 units with boundary-Jaccard fixtures. Units under 20
  tokens: exact hashes only.
  JACCARD CALIBRATION (blind, deterministic). Bins B1=[0.70,0.75)
  B2=[0.75,0.80) B3=[0.80,0.85) B4=[0.85,0.90) B5=[0.90,1.0]; 8 pairs
  per bin PER LANGUAGE, repo-balanced round-robin, within repo by
  ascending SHA256(json(["a6cal:v2b:20260808", <repo>,
  <pair identities sorted...>])); underfilled bins recorded; labels
  committed before unblinding. Mapping, evaluated in order per
  language (D(bin) = duplicate fraction; prec(>=x) over labeled pairs
  with J >= x; a zero-label bin's D() condition is vacuously true,
  recorded; < 8 labeled pairs at J >= 0.80 => lexical-inconclusive):
   1. prec(>=0.80) >= 0.9 AND D(B1) < 0.5            -> 0.80
   2. else prec(>=0.90) >= 0.9 AND D(B1) < 0.5
          AND D(B2 u B3 u B4) < 0.5                  -> 0.90
   3. else D(B1) >= 0.5 AND prec(>=0.70) >= 0.9      -> 0.70
   4. else                                           -> lexical-
                                                        inconclusive
  On lexical-inconclusive: primary near-dup exclusion = the exact
  hashes alone (per the activation rule); the 0.80 lexical exclusion
  runs only as a labeled sensitivity. All labels and the mechanical
  outcome are committed evidence.

15.A7 PYTHON BEHAVIORAL VERIFICATION. Stage 1 (once per target,
unmodified repo): test discovery = name-matched (test_<mod-stem>.py
under tests/ directories from the target's directory upward, nearest
first) union import-matched (static imports resolving to the target's
module, same resolver as extraction); 50-file cap by ascending
SHA256(json(["a7tests:v2b:20260808", <repo>, <target-identity...>,
<test-relpath>])), cap recorded. coverage.py session with per-node
attribution via a committed conftest plugin (no pytest-cov):
pytest_runtest_logstart(nodeid, location) -> COV.switch_context(
nodeid). COVERING_NODES = sorted node ids covering >= 1 executable
line of the target span; empty => compile-only ceiling (no-subset
recorded distinctly from timeout). Stage 2 (per completion): rerun
ONLY the first 4 covering nodes by ascending
SHA256(json(["a7nodes:v2b:20260808", <repo>, <target-identity...>,
<node-id>])); node timeout 120 s, subset 600 s; timeout = completion
FAILURE. Estimand: PASS UNDER THE FROZEN CAPPED VERIFIER (differential
measurement error caveat per §14.23). Feasibility (per repo,
pre-generation): projected stage-2 cost = sum over targets of
(min(4, n_covering) x median node wall-clock x 5 arms x 32
completions) measured on the 20 structural targets; > 200 CPU-hours
=> that repo's python-semantic behavioral pilot is INFEASIBLE before
any generation — generation and compile-only verification still run,
NLL proceeds, §14.22 emits descriptive reliability for the outcome classes
that exist while only python-semantic-covered governs semantic-F1 completion
n, and Python confirmatory F1 is infeasible for that repo (§14.23). No
per-target caps or exceptions.

15.A8 k7 ORDER + FILTER. Order: the G3 regex source-import graph and
the audited prep_streams ordering function (Kahn, min-heap on file
sort index, existing cycle handling) over ALL corpus source files of
the language at the locked revision (k7 keeps its G3-style identity;
the physlib near-lexicographic caveat is restated verbatim). Filter
(per target, applied once): remove the target file, near-duplicate
docs, and every file containing >= 1 unit of the target's transitive
reverse closure under the frozen extracted graph of that language —
Lean elaborated (supports the removal), Python static best-effort
(§14.4; residual dynamic/alias/dispatch leakage is a standing recorded
Python k7 caveat). Removed files/bytes recorded. Artifact
{schema: "v2b_k7_order_v1", repo, corpus_git_sha, order_rule:
"g3_full_topo_kahn_minheap_v1", n_edges, n_cycle_break_events,
files: [[relpath, normalized_bytes, source_sha256, file_scc_id], ...]},
committed and hash-bound; assembly consumes only this artifact plus
the extraction graph for the filter; a target file absent from the
order is a hard error.

15.A9 PAIRED IDENTITY + ASSEMBLY BINDING. paired_harness_hash =
SHA256(UTF-8(json.dumps([["eval_paired.py", <sha256hex>],
["eval_incontext.py", <sha256hex>], ["layout.py", <sha256hex>]],
ensure_ascii=False, separators=(",",":")))); eval_paired.py MUST
import the chunked-NLL core from eval_incontext.py (hard import,
never a copy). Assembly manifest ({schema:
"v2b_assembly_manifest_v1"}, per corpus) binds per (target, arm,
budget) cell: rendered-context SHA256 + bytes, common-query-prefix
SHA256 + bytes, scored-body SHA256 + bytes, the source extraction
artifact's SHA256 and schema string, the corpus revision, and the
unit list with spans; the manifest's own SHA256 joins every paired
cell identity. Before scoring, eval_paired REHASHES prefix, context,
AND body against the manifest — fail-closed on any mismatch — plus
paired_harness_hash and env-lock refusal before model load.
PAIRED_SCHEMA_VERSION = 1; source_tree_hash-alone drift stays
acceptable per the adopted cell_done rule. Property suite: byte-suffix
nesting asserted for every arm and budget pair across {k2, k3, k4,
k4x, k5(s0,s1,s2), k6, k6-realistic, k7} plus per-arm partial-unit
accounting; any violation is an assembly hard failure.

15.A10 E1b SAME-DEPENDENCY-SET (k3s/k4s). Unit set = ONLY the
declaration units WHOLLY contained in the k4 B* suffix; the at-most-
one leading partial unit is EXCLUDED from both sides (identity and
partial bytes recorded). Render that exact list twice with the frozen
rendering — k4s implementation-style, k3s interface-style — NO
truncation, NO reselection; both byte lengths recorded and
deliberately unequal. k4s is a distinct sibling of the k4 cell and
never conflated with it. Both are budget-UNMATCHED labeled
sensitivities; the equal-budget E1b primary is untouched.

15.A11 IMPLEMENTATION-BLOCKER RESOLUTIONS (adopted pre-artifact,
pre-label, pre-sample, and pre-score). This subsection resolves choices
that A1-A10 left operationally underdetermined; it governs any conflicting
older sentence.

  BANNERS, k2, AND UNIT UNIVERSE. k2 is NOT unit-rendered: its maximal
  core is the target file's raw bytes strictly above the target span after
  merging and excising all wholly-earlier near-duplicate spans; retained
  intervals are concatenated byte-exactly and every splice is recorded.
  It has no banner (a path banner would reveal the target file). The
  identical banner/chunk machinery in §14.17 therefore applies to k3-k7,
  not k2. A k3-k7 banner is the rendered unit's own repo-relative path;
  equality with the target path is a hard failure. The overbroad older
  prohibition on a target module/declaration-name SUBSTRING in another
  unit's path is retracted: generic short names make that rule undefined
  and false-positive prone; banners never synthesize a target identity.
  The declaration-unit universe is every current-corpus declaration with a
  source span (Lean: after v3 foreign-DeclInfo removal; Python: every direct
  module-body unit), independent of TARGET eligibility, kind, selection
  containment, or split availability. Closure traversal includes same-file
  nodes so their cross-file dependencies remain reachable, then filters
  every target-file unit only at rendering and records same_file_mass. A k3
  unit with split_kind=null is rendered verbatim—no boundary is invented—and
  n_unsplit_units/bytes is recorded per cell; k3/k4 retain the same unit set.

  COMMON LEAN PREFIX + EXTERNAL MASS. The Lean common-prefix byte encoding is
  `UTF8("".join(command + "\n" for command in shell)) + declaration[:header_bytes]`:
  extraction stores active shell commands without terminal LFs and in active
  outer-to-inner/source order; zero shell commands contribute zero bytes.
  The round-trip assertion removes that synthetic shell prefix, then requires
  the exact header suffix plus scored body to equal the live declaration span.
  For external-package mass, the Lean extraction's nested
  `external_ref_counts_by_target[module][decl]` and the Python extraction's
  identity-keyed `graph.target_coverage[].n_external` are the binding sources.
  Neither extraction binds external source spans. Therefore their byte field
  remains `null` with an explicit reason until a separately pinned external
  snapshot supplies the bytes; no ambient installed package is consulted.

  BM25 (frozen, untuned). Terms are §15.A6 lexical typed records [kind,
  value], excluding layout sentinels; query and documents use that same
  lexer, never a third tokenizer. A document is one declaration unit,
  |u| is its lexical-record count, and avgdl and document frequencies are
  frozen over the full same-corpus unit universe, never recomputed per
  target. For N documents,
  IDF(x)=ln(1+(N-df(x)+0.5)/(df(x)+0.5)); k1=1.2 and b=0.75. The score is
  the IEEE-754-double evaluation of the sum over DISTINCT query terms of
  qtf*IDF*tf*(k1+1)/(tf+k1*(1-b+b*|u|/avgdl)), with raw linear qtf. Query
  is the exact common unscored prefix. Scores are recorded at full JSON
  float precision; equal scores use §15.A4b's frozen k6tie direction.

  k7 ADMITTED FILES AND CYCLES. "ALL" in §15.A8 means the exact audited
  prep_streams collector universe at the locked revision: configured
  roots/extensions/exclusions, UTF-8-decodable files of at least 64 bytes,
  with the collector's one-terminal-LF emission normalization. Walked but
  excluded counts/bytes are recorded by reason. Ordering calls the existing
  topo_order verbatim: Kahn min-heap followed by cyclic residue in file-index
  order. The artifact field is n_cycle_nodes (not the inaccurate
  n_cycle_break_events—no forced-pop event exists). file_scc_id is
  diagnostic, computed on the same resolved edge set and named by the SCC's
  lexicographically smallest relpath; it never changes topo_order. Every
  admitted file is then passed through §15.A4 normalize() before its k7 byte
  count/hash is bound. The raw bytes, collector-emitted bytes, normalized
  bytes, all three hashes, and appended/removed terminal-LF counts are
  recorded. This preserves the exact collector universe while handling real
  tracked files with multiple terminal LFs; every non-terminal-LF byte is
  unchanged and the canonical payload has exactly one terminal LF.

  FINAL SEPARATOR AND k1. For every nonempty arm, ONE separator LF is
  appended to the arm's core maximal rendering BEFORE any byte/token suffix
  is taken; it counts toward B and belongs to the final context span. Thus a
  normalized unit arm has one empty line before the common query prefix.
  This overrides §15.A4's statement that the last chunk owns no join byte
  only for this final context-to-query separator. k2 receives the same one
  separator LF after its raw spliced core. k1 has exactly b"" context, SHA256
  of the empty byte string, zero units, and separator_bytes=0; it has one
  manifest cell per target with budget_bytes=null and is reused by every
  contrast rather than duplicated as three pseudo-observations. Prompt bytes
  are context + prefix + body with no unrecorded delimiter.
  EMPTY PRESENCE-ARM RENDERINGS. If a k2-k7 maximal core is empty (empty
  closure, pool, universe, or admitted-file set), no separator is invented,
  but every budget cell that arm/seed ordinarily owns is still emitted with
  context=b"", context_bytes=0, the empty SHA256, zero units, and
  eligible=false. Thus emptiness is visible without treating a zero-byte
  context as filling B or entering a complete-case E1/E2 contrast; k5 seeds
  1/2 retain only B*. If k4 at B* contains no wholly included unit, k3s/k4s
  are likewise explicit empty, budget-unmatched sensitivities rather than
  absent objects, with the excluded partial unit still recorded when present.

  BODY TOKEN BOUNDARY (primary plus sensitivity). Tokenize the exact prompt
  once with no special tokens and reuse layout.token_spans' overlap groups
  and charged-byte ledger. PRIMARY body NLL includes a token-overlap group
  iff its entire charged source-byte interval lies inside the scored body;
  the single group straddling prefix/body, if any, is unscored, and its
  body-side bytes/codepoints are removed from the primary BPB/BPC
  denominator. Record the group, n_boundary_straddle_tokens,
  straddled_body_bytes/codepoints, and assert
  scored_body_bytes+straddled_body_bytes=exact_body_bytes. The boundary
  ledger/signature must be identical across arms for one target x tokenizer
  or every such cell is invalid. A predeclared descriptive sensitivity adds
  the full straddling-group NLL and uses the full exact body denominator.
  Because prefix tail and body bytes are arm-identical and equality is hard
  asserted, neither convention can introduce an arm-specific boundary rule.

15.A12 PRE-SAMPLE HARDENING (adopted during the metadata/k7/A6 pre-sample
gates, but before any target sample, near-duplicate packet or label, assembly
artifact, model score, or behavioral outcome). This resolves the fail-closed
implementation discoveries recorded below; none uses outcome information.

  ZERO-ADD MERGE TOPOLOGY. The exact §15.A2 all-add rule remains primary. If
  and only if it returns zero records for a tracked file in a full, clean
  checkout, run the same single-path `git log --follow --find-renames=50%`
  WITHOUT a diff filter and parse every `%H|%aI|%cI` history record. Let the
  history witness be the minimum author-or-committer timestamp, with the same
  commit/source tie rule. The file is accepted ONLY when that witness is at or
  before the frozen cutoff, and is assigned PRE; otherwise generation fails.
  Record `provenance_mode=no-add-pre-witness`, `n_add_records=0`, the raw
  witness dates/commit/source and history-record count,
  `exact_add_unresolved=true`, and `vendor_unknown=true`; vendor_flagged is
  conservatively true. This is a one-sided age bound: a truly post file may be
  demoted to PRE, but an unobserved old add can never be promoted to POST.
  Counts and file paths are reported. For any alternate cutoff earlier than
  the witness, the file is unresolved and excluded rather than reclassified.

  A6 LEXER AND LABEL BINDING. The Lean scanner rule in §15.A6 supersedes the
  earlier literal use of extraction `code_mask`; ordinary strings, raw strings
  `r#*"..."#*`, interpolated `s!`/`m!`-style strings, and character literals
  (including escapes and embedded comment markers) each emit one literal
  record, and their internal newlines emit no layout sentinel. An interpolated
  string balances its `{term}` regions while skipping nested ordinary/raw/
  interpolated strings, characters, quoted identifiers, and nested comments,
  then retains the ENTIRE interpolation verbatim as one `STR`. Identifiers
  inside interpolation terms are therefore not rename-normalized: this is a
  conservative near-duplicate false-negative risk, never a way to manufacture
  a normalized collision. Unterminated comments/literals fail. Required fixtures make
  distinct literal contents hash differently and pin numeric bases,
  underscores, fractions, and exponents. Because this deliberately table-free
  scanner emits every other non-space symbol one character at a time, a prime
  embedded in a registered multi-character notation atom (`]'`, `∑'`, `×'`)
  reaches the apostrophe branch alone: the strict character-literal grammar
  wins when it matches; otherwise ONLY its exact missing-close failure after a
  non-whitespace symbol emits a retained `OP("'")`, including when the prime
  is the final character of an extracted declaration unit (for example
  `⟦(1 : ℤ)⟧'`). Other malformed escapes and standalone/space-preceded
  unterminated character literals still fail. Thus the
  synthetically ambiguous `xs[i]'h'` is deterministically classified with
  `'h'` as a character literal rather than parser-table maximal-munch; this is
  a recorded hash-consistent lexer limitation. Calibration-pair order is the two
  validated identities sorted by canonical compact-JSON bytes; the seeded
  `a6cal` and `a6calshow` arrays flat-splice the first identity then the second,
  per §15's global convention—never two pre-serialized identity strings.
  Mechanical label application is bound to the exact deterministic packet:
  unknown, duplicate, omitted, or altered packet entries fail. Collision
  activation accepts exactly the capped eight labels and requires 8/8 clones;
  >8 is invalid rather than a way around the cap. Jaccard outcomes likewise
  reject labels not forming the packet's exact selected set.

  A6 KEYWORD FREEZE. A hand-enumerated Lean exemption draft omitted core
  contextual parser heads (the minimal counterexample was `by simp` versus
  `by omega`). No real A6 corpus artifact or label existed. The definitive
  list is therefore derived mechanically before A6 from each of the three
  pinned umbrella environments, filtered by the same identifier predicates,
  then exact-unioned into one write-once, hash-bound language freeze. The first
  attempted implementation used only `getTokenTable`; its positive smoke gate
  failed at job 19921335 because Lean intentionally dispatches non-reserved
  tactic heads (`rfl`, `simp`, `omega`) through parser-category tables. Before
  any A6 corpus artifact existed, the derivation was corrected to the exact
  union of reserved-token values and simple leading/trailing dispatch keys,
  while separately recording and excluding builtin literal-kind/non-simple
  pseudo keys. Per-token corpus/section provenance and exclusion counts are
  sealed. A6 generation requires and records that freeze; missing or drifted
  sections/list/hash fail closed. There is no manual token addition or deletion
  between dump and union. Token-array job 19921330 and failed freeze job
  19921335 are quarantined; the cancelled dependent A6 array 19921337 produced
  no corpus artifact.

  A6 NOTATION-PRIME CORRECTION. The corrected token/freeze chain
  `19924314`/`19924365` completed and sealed the intended 950-token vocabulary.
  The first dependent A6 scale array `19924368` then failed closed before any
  artifact write: Batteries task 1 and PhysLib task 2 encountered legal primes
  inside the registered notation atoms `]'` and `∑'`, respectively, which the
  table-free per-character symbol scanner had mistaken for malformed character
  literals; Python tasks 3/4 were cancelled without artifacts. The exact narrow
  fallback above was adopted before any packet, label, sample, assembly, model
  score, or behavioral outcome. The incomplete array is quarantined and the
  exact five corpora rerun under one amended generator commit.

  A6 DECLARATION-END PRIME + INTERPOLATED-STRING CORRECTION. The amended source-locked token/freeze
  chain `19928513`/`19928515` completed, and small-corpus A6 array `19928520`
  produced four passing scale artifacts. The gated mathlib task `19929004`
  then failed closed before artifact write on
  `CategoryTheory.ShortComplex.ShortExact.singleδ`: its exact extracted span
  ends with the registered shift-notation atom `⟧'`. Unlike the earlier
  in-unit atoms, the prime had no next character, so the strict char scanner
  reported its zero-payload unterminated shape outside the narrow fallback.
  Read-only diagnostic `19929108` identified the exact source-bound span. The
  zero-payload shape is now the same private missing-close class, so the same
  preceding-nonspace fallback applies at a unit boundary; standalone and
  space-preceded apostrophes still fail. Before commit or rerun, the extended
  full-extraction preflight `19929236` then exposed a second mathlib unit,
  `Mathlib.Linter.Style.setOption.setOptionLinter`: an `m!` message interpolates
  the term `"', '".intercalate ...`, whose nested quotes had prematurely ended
  the draft ordinary-string scan. Interpolated strings now use the balanced,
  whole-literal rule above. A pre-commit working copy (SHA256
  `021a3446466a5a0a424cf818acbb668525ee45c6a330d3357b12929630469e24`)
  first passed every pinned mathlib extraction span in read-only job
  `19929429` (`LEX-ALL-PASS`, 32 seconds, 1.85 GB), but that evidence is
  superseded because later test hardening changed the adopted file bytes.
  The exact scanner committed at `00a0025` (SHA256
  `c26cba3eff7980c861081a7cca94ad2ff48092c1d41a89cd22ab43782d30b08a`)
  replayed every span in job `19929789` and returned `LEX-ALL-PASS`
  (49 seconds, 2.39 GB). No mathlib artifact, packet, label, sample, model
  score, or behavioral outcome existed. The four small artifacts are
  quarantined as a mixed-source cohort, and all token/freeze/A6 inputs are
  rerun once under the amended exact-five generator commit.

  A6 BLIND LABEL BOUNDARY. Before any packet or label existed, the operational
  label handoff was fixed as one interleaved presentation across both audit
  mechanisms, deduplicated by exact source pair. Its schema whitelist exposes
  only opaque seeded id, language, and two revalidated verbatim spans; seeded
  order and side swap are deterministic. One binary label under §15.A6's
  already-frozen rubric feeds every hidden role of that pair. The unblinder
  refuses packet, presentation, or label bytes that are not exact committed
  HEAD blobs, requires exactly one commit ever to have touched the label path
  (recorded in the outcome), rejects missing/extra/duplicate labels, and
  deterministically rebuilds
  the presentation, and only then computes the frozen outcomes. Sampling
  remains explicitly `not-drawn` throughout this boundary.

  k7 TERMINAL-LF NORMALIZATION. The first k7 array (job 19920847) failed
  closed on one PhysLib file with two terminal LFs and one SymPy file with
  three; the earlier zero-change tripwire had assumed such tracked files did
  not exist. No sample, packet/label, assembly, or score existed. Keep the
  exact collector universe and apply the already-frozen §15.A4 normalizer to
  each collector-emitted file, binding normalized bytes for budgets while
  recording raw/emitted/normalized hashes and LF deltas. The incomplete array
  is quarantined and all five corpora rerun under one amended generator.

15.A13 k4x EXTERNAL SNAPSHOT CONTRACT (adopted pre-artifact: no k4x graph,
assembly cell, or score exists; resolves §14.20/§14.27/§15.A4 operationally
after the cluster lake-manifest read).

  PINNED SNAPSHOT. physlib e882411d1b6bcbdfdd336d4c509c6cc72e96842d's
  lake-manifest pins mathlib at
  81a5d257c8e410db227a6665ed08f64fea08e997, which DIFFERS from the
  corpus-lock mathlib HEAD 87adeaebd370a3b6a41ac4f044fddd4bf81803ad, so the
  no-skew reuse case is unavailable: k4x binds a bounded v3 extraction of
  exactly `.lake/packages/mathlib` at that revision (verified checked-out
  clean at generation). That exact extraction ALREADY exists: V2-a job
  19916781_2 emitted it from the same 81a5 pin (repo tag
  `physlib_pinned_mathlib`, 8,275 files, whole-file SHA256
  9f4a192059ede347093c4f424940198e45cc93b9140f0ef8e5b8a465e0b6f796); it is
  REUSED, never rerun, and the production generator gate refuses any other
  bytes. The repo TAG never enters k4x ordering keys (those carry
  repo="physlib") and stays distinct from the CONCEPTUAL banner prefix,
  which remains "mathlib4/<rel>". The snapshot binds the pinned mathlib
  package ONLY; physlib references to any other external root (Lean core,
  Std, batteries, ...) remain §14.3 counts-only with bytes null.

  RESOLUTION. The physlib v3 extraction's preserved external reference
  quadruples [src_module, src_decl, defining_module, const_name] resolve
  against the snapshot extraction's decl tables under the IDENTICAL fold
  rule (a const with a span; else the definition_parents chain within the
  defining module, bounded 8 and cycle-guarded; None = recorded-
  unresolved). Resolved edges with direct/folded provenance, per-target
  unresolved counts, and out-of-snapshot counts by root are sealed into one
  artifact {schema: "v2b_k4x_external_graph_v1"} that hash-binds the
  physlib extraction, the snapshot extraction, the exact lake-manifest
  bytes, and the frozen revision constant.

  COMBINED GRAPH AND RENDERING. §14.27/§15.A4 unchanged: nodes = physlib
  units + snapshot units (module-qualified identity; a key collision is a
  hard error, unrepresentable under disjoint module namespaces), edges =
  physlib-internal + resolved external + snapshot-internal; identical
  Kahn/SCC/distance/tie-break with repo="physlib" in the frozen k4sel key;
  identical chunk/budget machinery and the §15.A11 empty-rendering rule.
  Snapshot units render with banner path "mathlib4/<rel>"; equality with
  any physlib relpath is a hard error. Snapshot units can never be reverse
  dependencies or same-file (no mathlib->physlib edge exists; asserted, not
  assumed). Internal vs external unit counts and byte masses are recorded
  per cell; §14.3's external byte mass is definable for this arm only.

  CROSS-CORPUS NEAR-DUP SCREENING (supersedes any reading that defines
  target-to-external duplication away). For each sampled physlib target,
  every snapshot unit in its combined closure is screened target-to-
  external under the SAME sealed label-resolved A6 rules: verbatim-hash
  twins always excluded; normalized-hash collisions excluded exactly in the
  bands the sealed Lean collision activation activated (band by the frozen
  full-record-count literal); 5-gram Jaccard pairs excluded at the sealed
  calibrated threshold, hash-only when the sealed outcome is lexically
  inconclusive or either side is under the frozen 20-lexical floor.
  Normalization of snapshot units uses the SAME sealed corpus-lock Lean
  keyword freeze; non-re-derivation at the pin is a RECORDED limitation
  (token-set skew across the two revisions affects only the normalized
  channel and is visible in the freeze provenance, never silent). Screened
  units and their byte mass are recorded per target. This closes the
  recorded cross-repo duplication threat FOR k4x; the full cross-corpus
  duplication diagnostic remains a separate pre-interpretation boundary.

15.A14 BLIND N GOVERNANCE (§14.22 operationalized; adopted PRE-SCORE — no
model score, masked delta, or governance artifact exists). The V2-c
per-repo sample size N in [200, 400] is a DETERMINISTIC function of
masked pilot data, never an analyst choice.

  ESTIMATOR. Input = per-target paired B* deltas for each masked contrast
  family (opaque family ids; arm names never enter). Cluster = the
  target's source MODULE (identity[0]). One-way random-effects
  method-of-moments on unequal clusters: MSW = within mean square (df
  n-G), MSB = between mean square (df G-1),
  n0 = (n - sum n_g^2 / n)/(G - 1); sigma_w^2 = MSW;
  sigma_b^2 = max(0, (MSB - MSW)/n0). All-singleton pilots (every
  n_g = 1) use the conservative fallback sigma_b^2 = sample variance
  (ddof=1), sigma_w^2 = 0. NO upper ICC clamp: extreme clustering may
  correctly render every N in range infeasible, and that verdict must be
  representable. G < 2 (or n < 2) is the fail-closed verdict
  "insufficient-clusters" — never a silent default.

  PROJECTION. For every integer N in [200, 400], the projected V2-c
  module sizes m_g are the EXACT realized sizes of the frozen §15.A1
  plan machinery run at n = N over the sealed candidate table with the
  20 pilot identities EXCLUDED (build_sample_plan exclude_keys: original
  cutpoints/strata still validated against the FULL sealed table; quotas
  and per-cell priority selection over the excluded pool; every excluded
  key must exist). Var(mean) = sigma_b^2 * sum m_g^2 / N^2 +
  sigma_w^2 / N; halfwidth = t(0.975, G_pilot - 1) * sqrt(Var), with the
  Student-t quantile taken from the FROZEN df 1..19 table embedded in
  v2b_n_governance.py (no runtime quantile computation; df > 19 is
  impossible under a 20-target pilot and refuses). Family N = smallest
  integer N with halfwidth <= 0.02 (paired-delta bits/byte); repo
  N = max over the E1a/E1b/E2 families; any infeasible or
  insufficient-clusters family makes the repo verdict infeasible.

  HARDENED INPUT CONTRACT (adversarial-review adoption, same boundary).
  The masked-deltas artifact must declare metric="bpb" at
  budget_bytes=16384 (B* is the only governed budget), hash-bind the
  EXACT bound-sample and candidate-table artifacts the analyzer
  receives, and carry EXACTLY three canonical opaque families
  (fam-<16 hex>); the bound sample plan must itself have been drawn
  from that same candidate table (candidates_sha256 equality), the
  pilot must contain exactly 20 identities whose arity matches the
  candidate language, and every family row must be a pilot target. A
  projected N whose pilot-excluded plan does NOT fill to exactly N is
  recorded as null and can never be chosen — the projection never
  returns a requested N over a smaller realized denominator. The
  analyzer additionally RECOMPUTES the frozen deterministic pilot draw
  (build_sample_plan at n=20 over the bound candidate table, with the
  sampler's candidates_sha256 stamp) and requires the bound sample's
  plan to EQUAL it — the 20 exclusions bind to the frozen draw itself,
  never to a merely self-consistent 20-row JSON. Delta computation,
  per-family eligibility filtering, paired-completion provenance, and
  the sealed arm-to-opaque-id mapping live SOLELY in the masked-delta
  generator (B3), which requires arm identities this analyzer must
  never see: that generator is a MANDATORY PRE-SCORE implementation
  boundary of this subsection, not an implicit follow-up — no model
  score may be taken while it is missing.

  GOVERNANCE ANTI-FORGERY CHAIN (final adversarial adoption, same
  boundary). A schema-correct masked JSON must not bypass B3: the
  analyzer additionally takes the paired COMPLETION artifact as a
  fourth non-unblinding input (it carries no arm-level information)
  and verifies, fail-closed — the completion file hash equals the
  masked completion binding; completion run_identity and run-identity
  hash equal the masked artifact's; completion language/corpus and
  assembly binding agree, and its eval_paired.py generator shares the
  masking generator's source commit/tree; the masked run_identity internally
  names the assembly binding (manifest_sha256 equality); assembly/
  completion/salt-commitment bindings are schema- and hash-well-formed;
  masked language/corpus equal the candidate table's;
  n_rows_by_family equals the family rows; the masked generator is a
  well-formed prepare_v2b_masked_deltas.py stamp. The production gate
  further requires the masked artifact AND the salt-commitment path it
  names to be exact committed HEAD blobs whose bytes/hash and salt
  digest equal the masked binding, and the masked generator's
  source tree to equal the CURRENT source tree (HEAD itself may differ
  by the evidence-only commit landing the masked artifact). Forgery is
  thereby a committed, auditable act with a fully consistent fabricated
  chain — and the salt reveal remains the structural backstop, since
  published residuals must reconstruct from the hash-bound target
  artifacts.

  B3 MASKED-DELTA PRODUCER (same boundary; pre-score code only, no
  model execution). prepare_v2b_masked_deltas.py reconstructs every
  target artifact from one hash-bound paired complete.json, verifying
  completion/target/manifest/sample/candidates hashes, the run
  identity, each target's manifest-row rebinding (assembly_target
  sha + prefix/body hashes), and every used cell's
  cell_manifest_sha256. Frozen orientations and complete-case
  eligibility at B*, primary bpb only: E1a = k1 - k4 (eligible k4);
  E1b = k3 - k4 (eligible k3 AND k4); E2 = k5:0 - k4 (eligible k5
  seed-0 AND k4). A family emptied by eligibility is emitted with zero
  rows and becomes the recorded governance verdict
  "no-eligible-targets" (repo infeasible), never a crash. MASKING: one
  32-byte private salt generated pre-score (write-once, mode 0600,
  POOL storage, never committed or printed) with a write-once public
  SHA256 commitment artifact that is committed before scoring; opaque
  ids and a private +/- sign derive from
  domain-separated HMAC-SHA256(salt, contrast), so the mapping is
  unrecomputable until the salt is revealed after governance. Public
  rows are sign * (delta - family mean): published family means are
  zero to ulp-scale floating residue and the MoM components are
  invariant up to floating roundoff (property-tested).
  No private sidecar exists — after salt reveal, raw deltas and means
  reconstruct deterministically from the hash-bound target artifacts.

  BLINDNESS AND EXCLUSION. The governance artifact records variance
  components, cluster counts, per-N halfwidths, chosen N, and verdicts
  ONLY — no means, signs, or per-target deltas; family ids stay opaque
  until the sealed unblinding. The 20 pilot identities are excluded from
  EVERY V2-c draw via the same exclude_keys path the projection uses.
  RECORDED LIMITATIONS: V2-c eligibility attrition is not modeled (the
  projection assumes all N targets contribute to each family), and pilot
  variance-component uncertainty is only partially covered by the
  t(G-1) quantile; both are governance conservatism trade-offs frozen
  here, not tunable knobs.

15.A15 FORMAL PILOT UNBLINDING (§14.22 sequencing made operational;
adopted PRE-SCORE — no model score, behavioral completion, masked delta,
governance artifact, or contrast mean exists). Setting N is necessary but
NOT sufficient to open the V2-b pilot. The private NLL salt remains sealed
until BOTH mechanically chosen constants exist: per-repo target N from
§15.A14 and behavioral completion n from §14.22's arm-anonymous reliability
gate. The sole earlier disclosure remains the already-frozen k4 aggregate
pass rate used only for the floor/ceiling tier rule. An NLL reveal after N
governance alone would be a separately amended exploratory analysis, never
completion of the preregistered V2-b pilot; this study does not schedule it.

  RECONSTRUCTION. The formal unblinder re-runs the exact B3 producer over
  every hash-bound paired completion under the opened salt and requires the
  reconstructed public masked object to equal the committed object exactly
  (generator stamp excluded; committed file bytes remain hash-bound). It
  also re-runs the frozen N-governance analyzer and requires exact object
  equality with every committed governance artifact. B3's two-pass
  floating centering publishes, at reveal, removed_mean, fsum_correction,
  and total_centering = their sum; raw delta = sign * published residual +
  total_centering to floating roundoff and is exact under forward replay of
  B3. The reveal records the salt, contrast-to-opaque-family mapping, signs,
  centering values, and all input bindings in one write-once artifact.

  BEHAVIORAL ANTI-FORGERY GATE. Five JSON files merely carrying a future
  behavioral-governance schema do not satisfy §14.22. The production reveal
  entry point is mechanically DISABLED until the behavioral generator,
  parser/verifier evidence chain, reliability estimator (including all edge
  rules), and deterministic governance recomputation check are implemented,
  tested, and committed. Enabling that entry point is itself an auditable
  source amendment; hand-written schema-shaped artifacts cannot open the
  salt under the frozen code.

15.A16 SOURCE-TOKEN NLL ATTRIBUTION (prospectively supersedes the §5 phrase
"AST-class split" for the implemented analysis; adopted PRE-SCORE — no
model score or contrast mean exists). The current implementation makes NO
AST-node claim. A future parser-derived AST map may be added as a separately
named analysis, but the frozen primary here is additive lexical SOURCE-TOKEN
attribution over the exact scored declaration body.

  SOURCE PARTITION. Before scoring, each exact assembly body is strictly
  UTF-8 decoded and partitioned without gaps or overlaps in both codepoint
  and byte coordinates into six source classes:
    word    = Lean IDENT or Python NAME, INCLUDING keyword/tactic-head
              spellings (there is no semantic identifier claim);
    literal = Lean NUM/STR/CHAR or Python NUMBER/STRING/f-string literal
              pieces;
    symbol  = Lean/Python OP (operator, delimiter, or punctuation);
    comment = explicit language-lexer comment span;
    layout  = spaces, tabs, newlines, indentation, and token gaps;
    other   = any positive-width source token not covered above.
  Lean uses a span-aware transcription of the audited A6 scanner, including
  Unicode/quoted identifiers, nested comments, raw/interpolated strings,
  character literals, and the frozen apostrophe ambiguity rule; projecting
  its lexical records and layout sentinels MUST equal v2b_neardup.lex_lean
  exactly. Python uses the frozen Python runtime's stdlib tokenize positions;
  projecting its A6-relevant records MUST equal v2b_neardup.lex_python.
  Lean interpolated strings remain whole literals and are tagged as compound
  rather than silently attributing their embedded term. Every span carries
  char/byte bounds, raw kind, source class, and a spelling hash. The
  source-only write-once ledger binds the assembly and classifier/harness
  hashes, records the exact char-to-UTF8-byte boundary table and runtime,
  explicitly states ast_node_attribution=false, and contains no score or arm
  contrast.

  MODEL GROUPS AND BOUNDARY. Attribution consumes eval_paired's existing
  raw_body_token_rows, so it needs no GPU rerun. The excluded prefix/body
  straddling group remains excluded exactly as in §15.A11. Starting at that
  excluded body's codepoint extent, primary tokenizer tokens are grouped by
  layout.token_spans' frozen transitive-overlap rule; offset gaps charge to
  the next opener. Charged groups must partition the primary scored-body
  complement exactly, conserve scored bytes, reproduce the stored token
  layout signature in every arm, and their raw token NLL fsum must equal the
  stored primary NLL exactly.

  PRIMARY GROUP LABEL. One model overlap-group is atomic; its NLL is never
  split across primary classes. Let core support be the positive-byte subset
  of {word,literal,symbol,other}. Exactly one core class labels the group,
  even when layout/comment bytes carry that lexical core. More than one core
  class is retained as mixed_core. With no core, comment only -> comment,
  layout only -> layout, and both -> comment_layout. Thus the eight primary
  model-group classes are word, literal, symbol, other, mixed_core, comment,
  layout, and comment_layout. No mixed group is redistributed. A separately
  named noncausal byte_overlap_apportionment_sensitivity allocates group NLL
  in proportion to charged source-byte overlaps; it never replaces primary.

  ADDITIVE ESTIMAND. For target t, cell j, group g, let L_tjg be group NLL
  in nats, S_t the TOTAL primary scored-body bytes shared across arms, and
  ell(g) its primary class. Then

      C_tjc = sum_{g:ell(g)=c} L_tjg / (ln(2) * S_t),
      C_tj  = sum_c C_tjc + explicit_roundoff_residual = primary BPB.

  A within-class byte denominator is not a primary estimand and may appear
  only as a labeled sensitivity. Nested floating summation may differ by at
  most 16 ulps of max(|primary NLL|,1); the residual is published and no
  scientific class absorbs it. At B*=16 KiB, the ONLY preregistered source
  contrasts retain B3's orientations and complete-case target sets:
  E1a=k1-k4, E1b=k3-k4, E2=k5:0-k4. For a-b,

      Delta_t,c = C_ta,c - C_tb,c,
      Delta_t   = sum_c Delta_t,c + roundoff residual.

  Repo summaries are target-equal means over the SAME contrast-specific
  targets for every class. Classes never introduce their own exclusions.
  Contrast reconstruction has a frozen 32-ulp bound. Percentage effect
  shares are not computed: they are unstable near zero and when class
  contributions cancel. Descriptive class totals at other cells never become
  additional confirmatory contrasts.

  BLIND EXECUTION ORDER. The source-only ledger is safe to generate and
  commit before scoring. The classification, group reconstruction, additive
  denominator, residual rules, contrasts, and adversarial tests are likewise
  committed before any contrast is opened. Production attributed outcomes
  are not generated or inspected until §15.A15's formal joint N+behavioral-n
  unblinding; freezing the postprocessor now prevents class definitions from
  being chosen after seeing class or aggregate effects.

15.A17 ARM-ANONYMOUS BEHAVIORAL n GOVERNANCE (reliability estimator frozen
PRE-GENERATION and PRE-SCORE — no model score, generated completion,
verification outcome, pass rate, or contrast exists). This subsection fixes
the mathematical half of §14.22's behavioral gate. It does NOT claim that the
still-required generation, post-hoc completion parser, Lean/Python verifier,
masking producer, or k4 tier revealer has run.

  INPUT AND BLINDNESS. One artifact governs one exact (repo, final model)
  SLOT; reliability is never pooled across repos or model tiers. The final
  tier is selected first by §8's sole permitted k4 floor/ceiling aggregate,
  and reliability is then measured at that exact tier; reliability from the
  initial 1.5B tier cannot substitute after a move to 0.5B or 3B. If execution
  later needs one shared draw count, operational_n is the maximum chosen n
  across FEASIBLE slots; an infeasible slot remains infeasible. Model name and
  40-hex revision must equal one of the pinned q25c 0.5B/1.5B/3B bindings.

  The governance input carries exactly five opaque arm ids arm-<16 hex> for
  the exact named set {k1,k3,k4,k5,k6}. Under a behavioral salt/commitment
  distinct from the NLL salt,

    arm_id = "arm-" + HMAC-SHA256(
      salt, canonical_json(["v2bbehavior-arm:v1",named_arm]))[:16 hex].

  The salt commitment is committed BEFORE generation; collisions fail, and
  the mapping plus the complete masked object must replay exactly at joint
  reveal. Every opaque arm has exactly the same 20 canonical target keys and
  the same arm-independent outcome class plus three-boolean eligibility
  projection: reference_body_le_448_tokens, baseline_pass, and
  class_verifier_feasible. Eligibility is their conjunction. An eligible row
  has exactly 32 INTEGER {0,1} verifier outcomes, with position i both draw
  index and generation seed i; an excluded row has JSON null outcomes and is
  never imputed or analyzed. Thus the committed pilot-set invariant survives
  §14.15(c)/§14.23 exclusions without turning them into failures. Target keys
  are the exact keys in the committed 20-target sample/eligibility table
  (never outcome-aware aliases), and class/eligibility assignment comes from
  the committed plan and baseline/coverage gates. Missing, duplicate,
  float/bool, unequal-length, unsorted, extra-field, eligible/null mismatch,
  or cross-arm target/class/eligibility drift fails closed. Exact top-level,
  binding, model, generator, and row key sets exclude free-form metadata
  channels. Public bindings carry only frozen schemas and hashes (no
  paths/notes) for behavior plan, sample, assembly, baseline-coverage,
  completion/verifier evidence, behavioral salt commitment, and the repo's
  NLL masked artifact. The output is constructed from validated fields; it
  records eligibility counts and reliability nuisance quantities under opaque
  ids, never a pass-rate mean or copied arbitrary metadata.

  STRATUM SEMANTICS. Allowed Lean classes are {lean-theorem-proof,
  lean-def-typecheck}; allowed Python classes are {python-semantic-covered,
  compile-only}. Consistent with §8/§14.23, ONLY lean-theorem-proof governs a
  Lean slot's semantic-F1 n and ONLY python-semantic-covered governs a Python
  slot's semantic-F1 n. Other allowed classes receive explicitly
  descriptive-only reliability cells and never make semantic F1 feasible or
  set its n. A missing or underfilled governing semantic class makes that
  repo-model slot infeasible even if a descriptive class is reliable.

  RESPLITS. Candidate n is evaluated in the frozen order {8,16,32}. For each
  n, each of 200 resplits, each opaque arm x outcome class x target, rank draw
  indices 0..31 by ascending

    SHA256(canonical_json(["v2brel:v1",20260808,opaque_arm,
                            outcome_class,n,resplit,target_key,draw_index]))

  with draw index as the collision tie-break. The first n/2 ranks form half A,
  the next n/2 form half B, and the remaining draws are unused. Compute each
  target's pass proportion in A and B, then Pearson correlation ACROSS targets
  inside that opaque arm and outcome class. The raw half-length correlation r
  is projected to full length n by Spearman-Brown 2r/(1+r). Thus a 32-draw
  split's raw correlation describes 16-draw halves; the correction is what
  projects it to n=32.

  EDGE RULES (frozen because they change n). At least EIGHT independent,
  behavior-eligible targets are required in the governing semantic class; 200
  resplits reduce draw-split Monte Carlo noise but do not manufacture target
  replication, and eight remains a pragmatic lower bound for a 20-target
  pilot. Fewer than eight makes that repo-model semantic-F1 slot infeasible.
  Descriptive-only cells require three eligible targets to display a
  reliability number but never enter the gate. Pearson with zero variance is
  defined as zero; nonpositive Pearson
  maps to zero; Spearman-Brown is clamped to [0,1]. Each cell's 200 corrected
  values use the ordinary median (average of the middle pair). For candidate n
  the semantic-F1 gate is the MINIMUM cell median across the five opaque arms
  in the single governing class. Completion n is the first candidate whose
  minimum is >=0.8. If none meets, behavioral F1 is infeasible exactly as
  §14.22 says. One complete governance-contract object hashes the candidate
  set, draw/resplit counts, seed/domain/key fields, threshold, class-gating and
  small-cell rules, split construction, and edge rules; the future production
  artifact additionally binds the committed analyzer source tree.

  REMAINING EXECUTION BOUNDARY. A schema-shaped table cannot satisfy this
  gate. Before any behavioral generation, the study must still commit: exact
  512-token/no-EOS generation; language-parser-based declaration extraction;
  baseline/forbidden-escape verification; §15.A7 executable target-line
  coverage; a separate behavioral commit-reveal masker; the narrow k4-only
  tier aggregate; and a file-based producer that duplicate-key-rejects and
  re-materializes the exact target/class/pass table from all hash-bound
  completion/verifier evidence. The current canonical-object self-hash is not
  an exact file binding and is never sufficient by itself. §15.A15's
  production unblinder stays disabled until the producer binds the exact
  committed masked-outcomes file and deterministic recomputation succeeds.

15.A18 SOLE EARLY k4 TIER AGGREGATE (decision helper frozen PRE-GENERATION;
no completion, verifier outcome, or pass rate exists). One file-based future
producer reconstructs ONLY the k4 rows for one exact repo/model slot from the
hash-bound verifier evidence. The pure helper validates canonical form and
exact membership in the supplied committed 20-key pilot set, the frozen
arm-independent classes and eligibility projection, and outcomes. Eligible
rows have 32 integers; excluded rows have null outcomes. It retains only
behavior-eligible rows in the language's governing semantic class. Fewer than
eight eligible governing targets makes the slot infeasible without exposing a
rate. Otherwise it emits the sole §8/§14.22 permitted aggregate: semantic
successes, trials, and pass rate under named arm k4; no target rows or
other-arm statistic enters the artifact.

  Threshold decisions use integer cross-products, never rounded floats:
  successes*100 < trials*5 moves exactly one q25c tier UP;
  successes*100 > trials*95 moves exactly one tier DOWN; equality at 0.05 or
  0.95 STAYS. A required adjacent tier outside the pinned
  {0.5B,1.5B,3B} ladder makes the slot infeasible. This is a single move from
  the supplied tier, not an outcome-driven repeated search. If it moves, the
  destination is final: the five arms are generated there for reliability,
  but the tier revealer is not rerun and cannot make a second move. The helper
  hashes the entire rule, supplied pilot target set, and normalized k4
  projection. It is not a production gate: the missing wrapper must
  re-materialize k4 from the exact committed/private verified-evidence chain;
  explicitly bind the behavior plan, sample, baseline/coverage, verifier file,
  source tree, and exact verifier-file SHA; recheck sample membership; and
  publish an exact-keyed write-once artifact before its output can select the
  final model used by §15.A17.
