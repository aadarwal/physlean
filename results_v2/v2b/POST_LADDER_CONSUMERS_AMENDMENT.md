# Prospective amendment: post-ladder frozen consumers (budget dose-response + PhysLib k4x)

Date: 2026-08-09 EDT (late). Status: DRAFT — adoption requires independent
adversarial review. Nothing below reads any new cell until the analyzer is
committed and this amendment is adopted.

Adoption boundary: the five-tier ladder analyses (`017ca02`) are public at
B* = 16384 only. Every completion already contains the full committed
budget grid {4096, 16384, 65536} and, for PhysLib, the k4x cells — scored
in the same ledger-bound runs; no cell at any other budget or in any k4x
arm has ever been consumed. No behavioral generation or verifier outcome
exists. This amendment authorizes NO new scoring: both consumers below read
only the twenty-five ledger-bound completions (5 tiers x 5 repos).

## One analyzer, two modes (`analyze_v2b_dose.py`)

Both modes inherit the ladder analyzer's entire anti-shopping surface
unchanged: the frozen full tier set, the committed completion ledger row
equality, the pinned reveal / five-manifest / scoring-tree sha256
constants, registry battery filenames + completion battery binding, the
run-identity tier match, one-assembly-per-repo, and the amendment file
hash-bound into every artifact. Chain validation is performed by running
the UNCHANGED B3 producer as a validator (its full completion/manifest/
sample/candidates checks and grid-equality assertions) before any cell is
read; deltas are then extracted with the UNCHANGED `_load_target` /
`_cell_bpb` helpers, and inference is the UNCHANGED `_inference` /
Student-t / Holm machinery with the frozen +0.02 non-inferiority margin
and active-assay rule applied per budget.

### Mode `budget` — dose-response over the committed grid (all repos)

Per (repo, tier, budget B in {4096, 16384, 65536}): E1a@B = k1 − k4:B
(eligible iff k4:B), E1b@B = k3:B − k4:B (both), E2@B = k5:0:B − k4:B
(both). Complete-case populations DIFFER across budgets by construction;
every panel reports its own N/G and, separately, the ALL-BUDGETS COMMON
subset (targets eligible at k4:4096, k4:16384, and k4:65536
simultaneously) whose within-target means form the composition-stable
dose curve. Holm is applied within (repo, tier, budget) over the three
contrasts; B* = 16384 panels must REPRODUCE the committed ladder
artifacts' inference exactly (a standing consistency anchor), and the
sealed q25c-1.5b B* centering must reproduce the committed reveal
exactly, as in the ladder. PhysLib's k4-based statuses remain FORCED
`uninterpretable-pending-k4x-sensitivity` in this mode. Claim status:
`exploratory-nll-only-budget-response`; cross-budget presentation is a
descriptive dose curve; NO fitted functional form, exponent, or
extrapolation is licensed.

### Mode `k4x` — the PhysLib sensitivity (lifts the forced status)

PhysLib only, all five tiers, all three budgets, contrasts with the
lake-manifest-pinned combined-graph arm in the reference slot:
E1a-x@B = k1 − k4x:B (eligible iff k4x:B), E1b-x@B = k3:B − k4x:B,
E2-x@B = k5:0:B − k4x:B. The manifest's k4x binding (external repo
mathlib4 @ 81a5d257c8e410db227a6665ed08f64fea08e997) is asserted. No
forced status applies — THIS is the separately frozen sensitivity the
reveal amendment required — and the E1b-x assay rule is the frozen one.
Recorded honestly: k5:0 draws from the PhysLib-internal non-dependency
universe, so E2-x's control is same-repo text against a partly-external
reference arm (asymmetry stated in the artifact); cross-corpus
near-duplicate screening of snapshot units follows the sealed A6 outcome
as already bound at assembly. There is no reveal centering anchor for
k4x contrasts (none was ever computed); the sealed tier is anchored via
its ledger row and completion binding only. Claim status:
`exploratory-nll-only-physlib-k4x-sensitivity`. Substantive PhysLib
E1a/E1b interpretation is licensed EXCLUSIVELY through this mode's
committed artifacts, in the ladder vocabulary, still exploratory,
still never a language claim.

## Sequencing and reading rule

Analyzer + tests committed; independent review recorded; adoption commit
(this file flips to ADOPTED with the review record; PREREG §13 entry);
then one production run per mode per repo from the committed tree; the
per-repo artifacts (`v2b_budget_response_v1`, `v2b_k4x_sensitivity_v1`)
are committed as evidence and are the only reading surface. The
behavioral-arm sequencing disclosure of the ladder amendment carries
over: these artifacts join the pre-formal-unblinding information
environment and must be listed by any later behavioral amendment.
