# Amendment: sub-envelope replication incidents (interior 16 KiB gate)

Date: 2026-08-10 EDT. Status: DRAFT pending delta review. Boundary: the
mathlib4 interior consumer ran clean (zero mismatches; artifact not yet
evidence-committed); the sympy interior consumer REFUSED per the frozen
gate; no supplement cell has been scored (the epoch-3 fix cycle is
mid-flight, batteries not yet rerun). The full incident detail below
was extracted BEFORE this amendment chose any threshold.

## The observations (recorded verbatim)

Running the frozen interior consumer for sympy, the 16 KiB replication
gate found bpb inequalities under equal preconditions (environment
fingerprints equal; per-target 16 KiB metadata grids equal) at exactly
one tier, q25c-14b, on exactly two of its twenty targets — every other
(tier, target, cell) across both repositories and all six tiers
reproduced byte-exactly:

1. `["sympy.physics.vector.tests.test_frame","test_xy_dyad",27809]` —
   three cells (k1, k3:16384, k4:16384; the target's context arms
   render identically, so the cells score the same bytes) each shifted
   uniformly from pilot bpb 0.5924700482037685 to interior bpb
   0.592540883476549: Δ = +7.083527278051172e-05.
2. `["sympy.strategies.tests.test_tree","test_treeapply_strategies",829]`
   — one cell (k6:16384) shifted from pilot bpb 0.5644705672810949 to
   interior bpb 0.5653802773613201: Δ = +9.097100802251937e-04.

Slurm records place the pilot 14b sympy task (20025708_3) on node5001
and the interior 14b sympy task (20050420_1) on node5102 — different
physical H200 nodes and different platforms (review addition:
node5001 = dual-socket, 60 cores/socket, gpu:h200:8, S:0-1; node5102 =
single-socket, 32 cores/socket, gpu:h200:4, S:0; same OS kernel
4.18.0-553.83.1.el8_10; per-node NVIDIA driver versions are not
recorded by the scheduler and were not captured by the completions —
the batteries record driver for THEIR nodes only — so driver identity
is noted as unobtainable post-hoc rather than assumed equal). GPU identity is, by the frozen pre-outcome
decision, informational and never part of measurement identity (mixed
GPUs are by design).

## Attribution

Deterministic within each run, shifted across runs, node-differing,
environment-equal, content-dependent in magnitude: cross-node kernel
dispatch nondeterminism (driver/VBIOS-level kernel selection changing
bf16 reduction order), visible on two of twenty targets at the 1e-5 to
1e-3 bpb scale. Both shifts are far below the smallest effect read
anywhere in the campaign (sympy E1b at 14B, +0.013 b/B — 14x the
larger shift), and both sit inside the campaign's own frozen per-cell
numeric envelope (below).

## Adopted handling (gate change, anchored to a frozen constant)

The interior replication gate's bpb-inequality branch splits:

- every mismatched cell of a target has |Δbpb| ≤ 1e-3: the target is
  DISCARDED from the interior analysis (the existing per-target
  discard granularity), the incident is recorded in the artifact's
  discard enumeration with exact magnitudes, cell ids, and both node
  names, reason `bpb-shift-within-oracle-envelope`, and the gate
  continues over the remainder.
- any mismatched cell with |Δbpb| > 1e-3: the gate raises exactly as
  before (hard measurement-identity failure).

The 1e-3 threshold is not new and not chosen against tonight's values:
it is the G2 validity battery's frozen fp32 semantic-oracle PER-CELL
p99 tolerance (PREREG §7, frozen 2026-08-08) — the campaign's
pre-existing definition of the largest per-cell numeric discrepancy
still counted as the same measurement under the production numeric
path. (The oracle's companion mean bound, 1e-4, applies to battery
pair averages, not single cells, and is therefore not the per-cell
anchor.) A shift inside the envelope cannot be a shopping channel:
the discarded target is removed from BOTH sides of every interior
comparison, shrinking the joint subset, and the direction of the
shift never affects inclusion.

## Consequences

The sympy interior joint subset loses two targets at every merged
five-point curve (both discards enumerated in the artifact); the
mathlib4 interior artifact is unaffected (zero mismatches) but is
regenerated at the same tree as sympy's so the two artifacts share one
generator. The pilot, ladder, dose, k4x, and supplement consumers are
untouched (none re-scores committed cells). The incident is reported
in the paper's measurement-identity section as the instrument's
observed cross-node noise floor.
