# Prospective NLL-only exploratory reveal amendment

Date: 2026-08-09 EDT

Adoption boundary: the five-corpus sample, assembly manifests, and final-source
1.5B validity battery were sealed. The paired NLL array had **not** been
submitted. No paired completion, target score, masked delta, governance
artifact, contrast mean, opaque-family mapping, private sign, or salt had been
observed. The existing private NLL salt remained sealed and untracked.

## Why this is a separate analysis

The formal V2-b pilot in DESIGN_V2 §15.A15 is a joint NLL-plus-behavioral
program. Its production unblinder correctly remains disabled until both the
blind N governance constant and the independently masked behavioral
reliability constant exist and deterministically replay. The behavioral
producer/verifier/governance chain is not yet production-complete.

Waiting for that chain is not necessary to answer the narrower teacher-forced
question already measured by the frozen paired NLL pilot. We therefore freeze
one distinct **exploratory NLL-only reveal** before scoring. It is not formal
V2-b unblinding, does not satisfy the behavioral co-primary, and cannot be
reported as confirmatory evidence for the joint program.

## Frozen ordering and evidence gate

The exploratory reveal may run only in this order:

1. Score the already sealed five-corpus, 20-target-per-corpus paired pilot at
   the final validated 1.5B scoring source and committed assembly manifests.
2. Produce all five masked-delta artifacts with the pre-score committed salt
   commitment and existing B3 producer. Commit those exact artifacts without
   inspecting the private salt or any named-arm contrast.
3. Run the existing blind N-governance analyzer for all five corpora and
   commit the exact governance artifacts. No named arm, sign, contrast mean,
   or per-target raw delta may be exposed during governance.
4. Only after the commitment, five masked artifacts, and five governance
   artifacts are exact committed HEAD blobs may a separate NLL-exploratory
   reveal entry point open the salt. It must deterministically reconstruct
   every committed masked object and governance object from the hash-bound
   completions before emitting anything.

The formal `finalize_v2b_unblinding.py` and `slurm/v2b_unblind.sbatch` remain
unchanged and behavior-gated. The exploratory path must use a different
schema, output directory, program, and completion marker; it may never accept
or emit a formal `v2b_unblinding_v1` artifact.

## Frozen interpretation

- E1a (`k1-k4`), E1b (`k3-k4`), and E2 (`k5:0-k4`) retain their already
  frozen orientations, 16-KiB budget, primary bits-per-byte metric,
  complete-case eligibility, and target/module structure. No new contrast or
  subgroup is introduced after reveal.
- Every estimate is labeled **exploratory, NLL-only, one-checkpoint pilot**.
  E1b's previously frozen 0.02-bits/byte margin may be shown as an exploratory
  compatibility/noninferiority diagnostic, never as a confirmatory joint-pilot
  decision.
- The reveal cannot establish behavioral correctness, NLL-as-behavioral
  proxy validity, a causal language effect, a physical-codebase scaling law,
  or a model-independent language ordering.
- Any future behavioral study on these revealed targets is separate and
  nonconfirmatory. A confirmatory behavioral co-primary requires a fresh
  independently sealed target sample and salt, or a fully frozen independent
  protocol whose governance was completed before this reveal.
- The original joint V2-b status remains **not unblinded / not completed**.
  This exploratory artifact cannot be supplied where a formal behavioral-
  governance artifact is required.

This amendment changes no model input, target, context, budget, metric,
masking rule, N-governance estimator, or paired-scoring implementation. Its
only change is the prospective claim boundary and a strictly separated reveal
route for the already-defined NLL pilot.
