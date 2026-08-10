# V2-c feasibility amendment — DRAFT FOR REVIEW (not adopted)

Date: 2026-08-09 EDT. Status: DRAFT. Adoption requires independent
adversarial review AND the explicit human scale gate that already governs
V2-c (DESIGN_V2 §10). Nothing here authorizes a draw, a score, or a
governance recomputation.

## Boundary honesty — what has been seen

This draft is written AFTER the exploratory NLL-only reveal: per-repo pilot
means, intervals, and the blind governance verdicts are public. It is
therefore NOT a pre-outcome amendment for NLL quantities, and no rule below
may be tuned to the revealed means. Every operative quantity is restricted
to (a) constants frozen before the pilot (margins, budget grid, caps),
(b) the BLIND governance outputs (σ_b², σ_w², cluster structure — computed
and committed before the reveal), and (c) assembly-manifest structure
(eligibility fractions), which contains no model output at all. The
behavioral arm's salt remains sealed; behavioral confirmatory status is
unaffected by this draft. V2-c NLL results under an amended rule carry the
label `confirmatory-with-post-pilot-amended-governance` — never presented
as if the original pre-outcome governance had passed — and every artifact
bearing that label must embed this amendment's adopted-commit sha256 plus
the one-line provenance "governance amended post-reveal" so the label
travels with its boundary statement (review concern).

## Problem 1 — the absolute-precision rule is scale-mismatched

The frozen rule (§15.A14) selects the smallest N ∈ [200, 400] with 95%
halfwidth ≤ 0.02 b/B. The blind pilot variance components make this
unreachable for 4/5 repos (mathlib4 E1a σ_b² = 0.187 ⇒ N ≈ 1,900 ≫ 400):
the rule demands absolute precision that only sympy-scale variances can
deliver. Verdict `infeasible` was returned honestly; the criterion, not the
science, failed.

**Proposed replacement (uses only σ_b², σ_w², cluster sizes):** per
(repo, contrast family), N is the smallest integer in [40, 400] whose
projected one-sided test at α = 0.025 has power ≥ 0.9 against a true mean
of **0.5 · σ_target** (a half-SD standardized effect; σ_target² = σ_b² +
σ_w² from the committed blind governance components), using the same
frozen-plan projected module sizes and t degrees of freedom as §15.A14.
The standardized anchor 0.5 is a conventional medium effect — AND
(review blocker c, disclosed): it was chosen while revealed
standardized pilot effects were publicly visible, so the choice
predictably sorts repos into powered/under-powered classes; the V2-c
report must therefore carry a PREDECLARED anchor sensitivity
re-projecting N at anchors 0.2 and 0.8 alongside the operative 0.5,
and repos whose true standardized effects are smaller than the anchor
are explicitly under-powered and reported as such. The absolute 0.02 b/B halfwidth is retained as a REPORTED
diagnostic, never a feasibility gate. E1b keeps its frozen absolute margin
(+0.02 b/B) untouched — non-inferiority margins are substantive constants,
not precision targets; its N requirement uses the same standardized power
rule applied to the margin-shifted test. Repo N = max over families;
insufficient-clusters and no-eligible-targets verdicts unchanged.

## Problem 2 — exact-B eligibility deletes whole repos

Complete-case E1a requires k4 to fill exactly 16,384 bytes. Assembly
manifests (model-free) show this excludes 20/20 batteries targets (closure
maxima ≈ 10 KB), 18/20 physlib (same-repo closure near-empty; the external
spine is the k4x arm), and 16/20 astropy. The pilot population is thereby
"deep-closure declarations", a selection correlated with the measured
mechanism.

**Proposal:** per repo, the PRIMARY budget B*_repo for V2-c is the largest
grid budget {4096, 16384, 65536} at which ≥ 60% of that repo's CANDIDATE
population (assembly-manifest eligibility, computed on the full candidate
table before any draw) fills the k4 arm; 16,384 stays primary wherever it
meets the floor (mathlib4, sympy on pilot composition). A repo meeting the
floor at no grid budget is declared structurally-ineligible-for-E1a (its
targets remain context units). Cross-repo comparisons at unequal B*_repo
are labeled budget-heterogeneous and additionally reported at the largest
COMMON feasible budget. The frozen exact-byte-suffix rendering, complete-
case rule, and whole-unit-≤B sensitivity are unchanged — only which grid
point is called primary per repo moves, decided by model-free assembly
structure. **Steering containment (review blocker a):** the per-repo dose
panels at {4096, 65536} were PUBLIC when this rule was designed, so for
every repo whose primary moves off 16,384 the V2-c report MUST co-report
(i) the original-rule 16,384 panel and (ii) the full per-budget panel
curve — not only the largest-common-budget view — and MUST record a
threshold sensitivity re-deriving B*_repo at 50% and 70% floors alongside
the operative 60%. The candidate tables and assembly manifests from which
the eligibility fractions are computed are pinned by sha256 in the
governance implementation (review blocker b).

## Problem 3 — Python target composition

10/15 surviving sympy targets and 3/4 astropy targets are `test_*`
functions in test modules: the realized Python estimand drifts toward
"tests given their testees". **Proposal:** the V2-c Hamilton quotas gain a
frozen test-module stratum (module path matches the frozen regex
`(^|/)tests?/|(^|/)test_|(^|\.)tests?(\.|$)|(^|\.)test_|(^|/|\.)conftest(\.|$)|(^|/|\.)testing(/|\.|$)`,
applied to BOTH slash-form file paths and dot-form module paths — review
concern: the earlier form missed `test_*.py` outside `tests/` directories
and conftest/testing infrastructure), with quota shares fixed to the
candidate-table proportions; every headline estimand is additionally
reported excluding the test stratum as a predeclared sensitivity.
Composition is observable without any model output.

## Explicitly out of scope

The sealed pilot artifacts, the ladder amendment, behavioral governance,
the A6 outcome, and every frozen rendering/eligibility byte rule. This
draft creates no new blind and no confirmatory claim by itself.

## Adoption checklist (all required)

1. Independent adversarial review of this draft. (Round 1: FIX-FIRST,
   2026-08-10 — blockers a/b/c and both concerns folded into this
   revision; re-review of the revised draft required.)
2. Human V2-c scale approval (DESIGN_V2 §10) with this draft's hash.
3. PREREG §13 entry recording the post-reveal boundary honestly.
4. Governance analyzer changes implemented with tests, committed before
   any V2-c draw, AND adversarially delta-reviewed as their own step
   (review blocker b — every other stage has an implementation review;
   this one does too), with the candidate tables and manifests feeding
   the eligibility fractions pinned by sha256 in the implementation; the
   V2-c draw excludes the 20 pilot identities per repo through the
   existing exclude_keys path.
