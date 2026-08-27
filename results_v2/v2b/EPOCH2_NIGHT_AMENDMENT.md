# Prospective amendment: epoch-2 night batch (interior scoring; deep supplement; six-battery re-freeze)

Date: 2026-08-10 EDT (overnight, user-directed full completion). Status: **ADOPTED** after two-round independent review (round 1: four
findings — interior-ledger repo scoping, per-target discard granularity,
rebind basename preservation with launcher ordering enforcement,
supplement print field — all resolved; round 2 blocker: the replication
gate's reference side was unpinned — resolved by pinning the pilot
manifest sha, requiring --pilot-ledger row equality, and enforcing
per-tier pilot tree pins; final verdict ADOPTABLE, provenance nit
(pilot_ledger_sha256 in bindings) applied in the adoption commit). Boundary: epoch-1 (32b battery rerun + 32b scoring
at the 4ddf92e tree) is running or complete; both interior manifests
exist unread (mathlib4 interior_job20042050_0 sha 1ea57d0c…, sympy
interior_job20040278_1 sha da996920…); no interior or supplement cell has
ever been scored or read; no supplement draw exists.

## The epoch discipline (root cause of tonight's two sequencing aborts)

The frozen same-tree rule (battery and its scoring share one source
tree; assemblies abort on mid-run drift) interacts badly with
incremental code commits. Epoch-2 therefore lands EVERY remaining code
artifact in ONE batch producing ONE tree, then re-freezes instruments
once: rebind ALL six committed tier batteries INTO
results_v2/battery/epoch1/ WITH BASENAMES PRESERVED (review fix: the
consumers' registry-basename checks must keep holding when sealed-pilot
analyses pass the rebound files; every sealed artifact byte and sha
survives; sealed completions keep binding their original battery shas;
the epoch battery launcher refuses to run until the rebound committed
copy exists) and rerun all six at the epoch-2 tree via a
new `slurm/battery_epoch.sbatch` that accepts every tier INCLUDING
q25c-1.5b — the 1.5b "never rerun" launcher arm protected the sealed
PILOT battery file, which the rebind preserves untouched; the fresh run
is a NEW instrument record for NEW (interior/supplement) cells only.
Sealed-pilot consumers continue to receive the original battery files;
epoch-2 consumers receive the epoch-2 files. No git operation may touch
the cluster worktree between the epoch-2 sync and queue drain.

## Interior scoring (Part B phase 2-3, manifests now pinned)

`slurm/v2b_paired_interior.sbatch`: array 0-1 (mathlib4, sympy) x
V2B_MODEL_TIER over all six tiers, scoring the two interior manifests
pinned BY SHA256 in the launcher (values above) with the epoch-2 tier
batteries. One submission per (tier, repo). The interior consumer (`analyze_v2b_interior.py`) reads interior completions at
budgets {8192, 16384, 32768}; its 16384 REPLICATION GATE follows the
adopted preconditions — env-fingerprint and per-target 16384 grid
equality against the committed pilot completions; equal preconditions
make any bpb inequality a measurement-identity incident — with
PER-TARGET discard granularity (review fix: an environment-fingerprint
mismatch is tier-wide, but every other mismatch discards ONLY that
target and the incident check continues over the remainder; discarded
targets are enumerated in the artifact) and merges committed
{4,16,64}KiB panels with new {8,32}KiB panels into five-point dose
curves per (repo, tier) under the standing non-B* reading rule.

## Deep-closure supplement (Part C, mathlib4)

`finalize_v2b_supplement_sample.py`: mathlib4-only draw of n=120 via the
UNCHANGED `build_sample_plan(candidates, 120, exclude_keys=<the 20
committed pilot identities>)` — the same draw law by construction, no
closure precondition, pilot disjointness by exclusion. Assembly via the
existing pinned-input launcher pattern at the full grid {4096, 16384,
65536}; scoring across all six tiers with epoch-2 batteries; a frozen
supplement consumer reports supplement-only panels AND the predeclared
pooled panel (pilot + supplement, disjoint identities, same law) labeled
`exploratory-nll-only-supplemented-pilot`, with per-budget N/attrition
and the pilot-vs-supplement contrast always shown.

## 32b integration

After epoch-1 drains: the 32b scoring-tree pin commit fills
PINNED_SCORING_TREE_BY_TIER["q25c-32b"] with the 4ddf92e-lineage tree
recorded in its ledger-bound completions; ledger v2 is written with
--prior-ledger byte-carry; six-tier ladder and dose reruns must pass
`verify_v2b_expansion_consistency` against every committed five-tier
artifact before their evidence commits. Interior/supplement completions
bind their own per-tier epoch-2 pins, added by the same post-scoring pin
pattern.

## Reading and reporting

All new artifacts carry their existing claim statuses and the non-B*
reading rule; nothing here creates a confirmatory claim, a language
claim, or a trend statistic. The morning research paper reports every
number exclusively from committed consumer artifacts, with the
exploratory labels and this amendment history disclosed.
