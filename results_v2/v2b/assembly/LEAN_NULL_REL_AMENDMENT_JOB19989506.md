# V2-b Lean extraction compatibility amendment after job 19989506

Date: 2026-08-09

State at amendment: the blind A6 outcome and deterministic five-corpus sample
were sealed. No paired NLL model job had been submitted and no assembly
manifest had been accepted or committed.

Recovery array `19989506` passed the corrected boundary and keyword-freeze
bindings, then the three Lean tasks failed before manifest publication in
`prepare_v2b_assembly._corpus_root`. The production Lean v3 extraction schema
stores an exact `module` and absolute `source` for every file but leaves the
optional cross-language `rel` field null. The assembly helper had only been
tested against synthetic rows with a string `rel`, making every production
Lean extraction structurally unusable at the k7 root-reconstruction step.
Python rows retain their string `rel` and are unaffected.

Before any model outcome, `_corpus_root` is amended prospectively as follows:

- A null `rel` is accepted only for the frozen Lean extraction schema.
- Files present in the independently sealed k7 ledger are joined using both
  raw source SHA256 and a path-boundary suffix match. Multiple matches fail
  closed, and the matched anchors must imply exactly one nonempty root.
- K7 intentionally omits 20 of 9,125 extracted Lean files. An unmatched file
  is accepted only when its absolute source remains strictly beneath the one
  root proven by the matched anchors. No root may be inferred from unmatched
  paths alone.
- This handles the production Batteries module `runLinter`, whose source
  intentionally lives at `scripts/runLinter.lean` and therefore cannot be
  recovered from its module name alone.
- Every matched source must end in its exact sealed k7 relative path, and all
  matched files must imply the same corpus root.
- Null Python `rel`, source/module drift, and inconsistent roots remain hard
  failures.

This is a schema-compatibility repair. It does not alter extraction bytes,
file identities, sample membership, contexts, budgets, labels, salts, or any
scoring rule. A production-shaped null-rel regression is added. All five
assembly tasks must be rerun under one new array ID; artifacts from jobs
`19989241` and `19989506` remain uncommitted and unusable downstream.

Because this changes tracked measurement source, the existing 1.5B validity
battery cannot authorize paired scoring. Assembly is run first to expose any
remaining outcome-independent integration failures; once all five manifests
are green, the pilot battery must be rerun exactly once against the final
source-tree hash and committed before the L40S paired array is submitted.
