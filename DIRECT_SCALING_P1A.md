# Direct-scaling P1a structural census

`direct_scaling_feasibility.py` produces and validates the model-free Arm-A
structural portion of `v2c_direct_scaling_feasibility_v1`. It reads UTF-8
source blobs and Git history at the exact revisions in `corpora_lock.json`.
It does not import a tokenizer, load a model, score a target, or read a V2
outcome.

## Frozen inputs and authority

Every scientific constant comes from a complete
`v2c_direct_scaling_protocol_v1` P0 artifact. The producer verifies the
protocol's `protocol_binding`, binds the exact raw protocol-file SHA256 and
corpus-lock SHA256, and checks every protocol repository URL and revision
against the corpus lock. It rejects dirty corpus checkouts, including
untracked files, then reads tracked blobs directly from the locked Git object
tree.

The production protocol freezes 12 context rungs from 512 bytes through
1 MiB. Only the rungs through 256 KiB participate in structural headline
decisions. The 512 KiB and 1 MiB rows are retained as diagnostic occupancy and
cannot rescue a failed gate.

P0 power evidence is deliberately a separate later gate. This census neither
reads nor copies a power decision. Consequently its artifact always records
`power_decision_consumed: false` and
`loss_scoring_licensed_by_this_artifact: false`. A successful structural
census is necessary evidence, not authorization to start model scoring.

## Evidence produced

For each locked repository, the artifact records:

- exact commit, tree, history and source-file-set identities;
- composition-invariant shuffled, topological and reverse-topological
  streams, including deterministic order and byte-stream hashes;
- A0 occupancy on both `q_stream` and metadata-free `q_source`, with exact
  source/metadata byte accounting at every rung;
- deterministic systematic A1 targets, capped at the P0-planned cohort size,
  with the same identities reused across all orderings and rungs;
- A1 with-file and exact skip/backfill cross-file-only occupancy, same-file
  exhaustion, and an explicit shared complete-case cohort for paired regime
  fitting;
- frozen lexical eligibility and cross-repository near-duplicate exclusions;
- dependency evidence, graph coverage, cycle counts and the graph gate;
- repository-dependence components and structural K2 evidence; and
- ordinary and floor-removed contiguous ranges plus the frozen 10-times-
  median-exhaustion reach cascade. The cascade checks both A0 axes, both A1
  regimes and their shared cohort at the implied endpoint.

Python dependency graphs use the locked blobs and Python AST. This revision
does not have a frozen native Lean environment or an exact per-translation-
unit C/C++ dependency scan. Lean imports are therefore proposal-only and C/C++
graph evidence is unavailable; both graph gates fail closed. They are never
labelled native or complete.

Arm B event construction is also unimplemented here. Arm-B arrays are empty,
all K5/K6 entries are forced false, and `complete_p1a_claim` is false. This is
an Arm-A structural producer, not a complete P1a artifact for every study arm.

The ordinary validator checks bindings, schema, all derived ranges and
decisions, shared-cohort identities, graph fail-closed state, and internal
hashes. `validate --deep` rebuilds the entire artifact from the locked source
objects and catches re-signed raw-summary tampering. The cluster wrapper always
uses deep validation before declaring completion.

## Local commands

```bash
python3 direct_scaling_feasibility.py produce \
  --protocol results_v2/v2c/direct_scaling_protocol_v1.json \
  --protocol-sha256 b32f1ebb7de3e18230cd8f0c28633871e9543408788d07acf7cc2c916d160291 \
  --corpora-lock corpora_lock.json \
  --corpora-root corpora \
  --out results_v2/v2c/p1a/direct_scaling_feasibility.json

python3 direct_scaling_feasibility.py validate \
  --protocol results_v2/v2c/direct_scaling_protocol_v1.json \
  --protocol-sha256 b32f1ebb7de3e18230cd8f0c28633871e9543408788d07acf7cc2c916d160291 \
  --corpora-lock corpora_lock.json \
  --corpora-root corpora \
  --artifact results_v2/v2c/p1a/direct_scaling_feasibility.json \
  --deep
```

On Engaging, from the committed project checkout:

```bash
sbatch --export=ALL,\
V2C_PROTOCOL=/orcd/pool/008/aadarwal/physlean/results_v2/v2c/direct_scaling_protocol_v1.json,\
V2C_PROTOCOL_SHA256=b32f1ebb7de3e18230cd8f0c28633871e9543408788d07acf7cc2c916d160291,\
V2C_CORPORA_ROOT=/orcd/pool/008/aadarwal/physlean/corpora \
slurm/v2c_direct_scaling_feasibility.sbatch
```

The wrapper keeps `HOME`, caches and temporary files on POOL, requires the P0
protocol and corpus lock to be tracked and unchanged, refuses a dirty source
tree, writes a new job-specific artifact, and then performs deep reproduction.
