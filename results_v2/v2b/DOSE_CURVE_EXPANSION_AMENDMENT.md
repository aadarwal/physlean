# Prospective amendment: dose-curve expansion package (32B rung; interior budgets; deep-closure supplement)

Date: 2026-08-09 EDT (night). Status: DRAFT — one umbrella design review
covers all three parts; parts B and C additionally require a delta review
of their implementation commits before execution. Human authorization for
maximal compute/parallel execution: recorded 2026-08-09 ("max compute +
max parallelization").

Adoption boundary: the six committed evidence sets are public (1.5B
reveal/analysis, five-tier ladder, budget dose-response, PhysLib k4x). No
32B battery/score, no cell at any budget outside {4096, 16384, 65536},
and no target beyond the sealed 20-per-repo pilot has ever been scored or
read. The 32B snapshot download was outcome-free acquisition.

## Part A — q25c-32b rung (executable at adoption)

Extends the frozen tier set to SIX:
{q25c-0.5b, q25c-1.5b, q25c-3b, q25c-7b, q25c-14b, q25c-32b}, with
q25c-32b = Qwen/Qwen2.5-Coder-32B @
2e12b5f7bc878d424d222e224ed40aee564ec45f (models.json pin), family
q25c-32b, predeclared param range (30.0e9, 35.0e9), battery file
battery_pilot_32b.json, H200-only for BOTH battery and scoring (launch
constraint; GPU stays informational-never-gated in evidence). HONEST
RISK, recorded before running: the battery's fp32 semantic leg needs
~128GB of weights on a 141GB H200 — marginal; an OOM there is recorded
as 32b-tier infeasibility and the tier is dropped from the six-tier set
by a logged follow-up, never worked around by weakening the leg.
Everything else is the unchanged ladder machinery: per-tier write-once
battery at the scoring tree, one scoring submission, ledger v2
(COMPLETION_LEDGER_V2.json, six rows per repo, sealed rows carried
forward byte-identically), and six-tier reruns of the frozen ladder and
dose consumers as NEW artifact files (the committed five-tier v1
artifacts remain untouched evidence; six-tier artifacts must reproduce
every five-tier panel exactly — same rows, same inference — as a
standing consistency gate enforced by comparison before commit).

## Part B — interior budget points {8192, 32768} (delta review before execution)

One new assembly for mathlib4 AND sympy only (the headline contrast
pair), same sealed sample, same frozen renderer and chain bindings, with
budget grid (8192, 16384, 32768) — B* included because the frozen k3s/k4s
definition requires it. The 16384 cells DUPLICATE already-scored public
cells by construction; they are declared REPLICATION GATES: at scoring
time each 16384 cell's primary bpb must equal the committed completion's
value exactly (same device class, frozen chunk, verified repeat
determinism), and any inequality aborts the tier's interior run as a
measurement-identity incident. Two-phase pinning (the assembly job id
cannot be known at adoption): (1) this amendment authorizes the assembly
run; (2) a pre-scoring evidence commit pins the produced manifests by
sha256 into the interior consumer and the interior launcher, reviewed as
a delta; (3) scoring (six tiers x two repos), ledger extension, and a
frozen interior-budget consumer run that merges {4,8,16,32,64}KiB into
one five-point dose curve per (repo, tier) under the existing non-B*
READING RULE (descriptive dose-curve context; B* remains the only
headline cell). Budgets above 65536 are OUT OF SCOPE here: they exceed
the validated native context regime and belong to the direct-scaling
study's long-context gate.

## Part C — deep-closure mathlib supplement, n=120 (delta review before execution)

A supplemental mathlib4 target draw to fatten the 64KiB dose point from
n=7 toward ~40 complete cases, under the SAME DRAW LAW as the sealed
pilot: the frozen §14.19 seeded-priority stratified machinery
(build_bound_sample internals) at n=120, from the same committed
candidate table and sealed A6 outcome, with the 20 pilot identities
excluded through the existing exclude_keys path. NO closure-size
precondition enters the draw (deep-closure membership materializes at
assembly, exactly as in the pilot; conditioning the draw on closure mass
would change the population definition). The supplement is assembled at
the full grid {4096, 16384, 65536} (+ interior points if Part B is then
live), scored across all six tiers, and consumed by a frozen supplement
consumer whose panels are reported SEPARATELY from the sealed-pilot
panels and also pooled as a predeclared combined panel (pilot +
supplement, same draw law, disjoint identities); the pooled panel is the
intended headline-fattening estimate and is labeled
exploratory-nll-only-supplemented-pilot. Attrition, per-budget N, and
the pilot/supplement contrast are always reported.

## Compute posture (user-directed)

Maximal breadth: batteries and scoring arrays fan across all idle L40S
(small tiers) and preemptable H200 (14b/32b) simultaneously; per-job
semantics stay single-GPU at the frozen chunk (measurement identity —
no multi-GPU or batching changes, ever). Sequencing constraints are only
the frozen ones: battery committed before its tier scores; assembly
manifests committed before scoring; consumers before reading.

## Sequencing

1. Umbrella review of this amendment + the Part A code (registry, both
   launchers, six-tier FULL_TIER_SET, tests) → adoption commit →
   32b battery (H200) → commit → 32b scoring (H200, array 0-4) →
   ledger v2 → six-tier consumer reruns → evidence commit.
2. Part B implementation (assembly invocation script, interior consumer,
   replication gates, tests) → delta review → assembly → pin commit →
   scoring → consumer → evidence.
3. Part C implementation (supplement sampler module, supplement/pooled
   consumer, tests) → delta review → draw + assembly → pin commit →
   scoring (six tiers) → consumer → evidence.
Parts B and C run concurrently on the cluster once each clears its delta
review; behavioral-arm sequencing disclosure carries over to every new
artifact.
