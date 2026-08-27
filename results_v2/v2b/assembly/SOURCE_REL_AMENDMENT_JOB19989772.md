# V2-b canonical Lean source-path amendment after job 19989772

Date: 2026-08-09

State at amendment: the blind A6 outcome and deterministic five-corpus sample
were sealed. No paired NLL model job had been submitted, no NLL outcome had
been produced, and no assembly manifest from jobs `19989241`, `19989506`, or
`19989772` had been accepted or committed.

Array `19989772` passed the canonical input-binding and Lean null-`rel` root
gates, then all three Lean tasks failed before manifest publication at the
target join. An exhaustive outcome-independent audit of the 60 sampled Lean
targets found exactly one discrepancy: candidate rows used the canonical
corpus-relative path produced during candidate generation, while assembly's
unit index substituted the absolute extraction `source` whenever the optional
Lean v3 `rel` field was null. All 60 target body-byte counts, parser-backed
span IDs, sample span IDs, resolution statuses, and span arithmetic agreed.

Before any model outcome, assembly is amended prospectively as follows:

- The independently sealed k7 ledger first proves the one main-corpus root
  under the rules frozen after job `19989506`.
- That exact root is supplied to the unit index. For a Lean row with null
  `rel`, the unit index derives the normalized root-relative path with the
  same fail-closed helper used by candidate generation. For a non-null `rel`,
  the stored path must exactly equal the root-derived path. Duplicate derived
  relative paths fail closed.
- The pinned PhysLib→Mathlib k4x extraction is at revision
  `81a5d257c8e410db227a6665ed08f64fea08e997`, not the main Mathlib corpus
  revision. It therefore must not borrow the main Mathlib k7 ledger. Instead,
  assembly requires the absolute normalized `snapshot_root` already sealed
  inside the k4x graph artifact and derives every external relative path
  strictly beneath that root before adding the `mathlib4/` banner prefix.
- Missing, relative, non-normalized, or nonexistent snapshot roots; source
  escapes; and relative-path collisions remain hard failures.

The sealed production inputs were audited before this amendment: the main
60/60 sampled target joins differed only in the path representation described
above. For the external snapshot, all 8,275 extraction sources were absolute,
normalized regular files strictly beneath the sealed snapshot root; all live
source hashes matched; the derived relative paths were nonempty and unique;
and the graph, extraction, checkout, and frozen revision bindings agreed.

This repair changes no extraction byte, identity, boundary, sample membership,
context-selection rule, budget, label, salt, or model score. A full new
five-corpus assembly array is required. Because tracked measurement source has
changed, the 1.5B validity battery must be rerun at the final source-tree hash
after assembly is green and before paired NLL scoring is submitted.
