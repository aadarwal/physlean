# V2-b assembly operational amendment after job 19989241

Date: 2026-08-09

State at amendment: the A6 outcome and five-corpus sample were already sealed,
and no paired NLL model job had been submitted. Assembly array `19989241` ran
from source commit `84251217f5c0e2cd41ed83df854150430e07b998`.

## Observed failure

Tasks 0 (mathlib4), 1 (Batteries), and 2 (PhysLib) failed before manifest
publication with:

```text
V2BError: Lean boundary artifact is not the exact candidate/sample sealed input
```

Tasks 3 (SymPy) and 4 (Astropy) completed, producing uncommitted manifests with
SHA256 `c60986bbaf39019549253f046382f8b04e8cc2ab40bd9552e42629a7bf857a20`
and `72305c57c4e49adcba8490f652ab808717b446ec37036d7ab8dcbbd6212c5401`.
Those partial-array artifacts are quarantined: they are not committed and must
not be used downstream.

The failure was an operational path-string mismatch, not an evidence-content
mismatch. Candidate and sample artifacts retained the original absolute paths
under `/orcd/pool/008/aadarwal/physlean`. The dedicated launch checkout linked
the same bytes under `/orcd/pool/008/aadarwal/physlean-nll-launch`, while
`artifact_binding` deliberately records `abspath` rather than `realpath`.
Consequently the full binding objects differed only in `path`.

The same strict full-binding comparison applies to the Lean keyword freeze via
the sealed near-duplicate artifacts, so both strict Lean inputs must use their
original canonical paths. Other assembly inputs are checked by SHA256 plus
schema/repository identity and need no path substitution.

## Frozen operational repair

The rerun entry point is
`results_v2/v2b/assembly/v2b_assembly_canonical_paths.sbatch`, SHA256
`a1f71da49b589f25dbe1321865d02083f4a1f585a2e1eef11f2df6fa2827e864`.
It is an exact copy of `slurm/v2b_assembly.sbatch`, SHA256
`656a9590d3cb0a92b8703546b1c36ff6c9987db0e27d98051ccdfe0146d016b9`,
except for exactly two assignment lines:

1. Lean boundaries use
   `$V2B_POOL_BASE/physlean/results_v2/v2b/lean_boundaries/...`.
2. The Lean keyword freeze uses
   `$V2B_POOL_BASE/physlean/results_v2/v2b/lean_keywords/...`.

No candidate, sample, outcome, label, salt, source corpus, measurement code,
prompt construction, or scoring rule changes. The wrapper and this amendment
live under `results_v2`, which is excluded from `source_tree_hash`; the paired
pilot battery remains bound to source hash
`dd80a32f12d6c2e39ced22dd7b833272391b65fa0b45d3d32fee8ffb16c2c253`.

Canonical evidence SHA256 values are unchanged:

- mathlib4 boundaries: `3107ffc466b2a02c25147dfb19e0dea7ee7f8aff5c10cd4df7bd877252995a0c`
- Batteries boundaries: `7c3b1dee6a908c500023e0428ea6c17a8735db817d4c96428f9fbf3b47b79f81`
- PhysLib boundaries: `eac251d024b54840ac243788da8f52e9cf57166244c110199b1c2e2e8f1eb5da`
- Lean keyword freeze: `79e71f929f489ee7c15b4492c36b77ff9e31060fe71ce9b18118bbd5ca0dd51e`

The repair must rerun all tasks 0--4 under one new Slurm array ID. Only if all
five tasks finish `COMPLETED 0:0` may the five new manifests be committed and
the paired L40S array be submitted.
