# Prospective NLL-only exploratory analysis amendment

Date: 2026-08-09 EDT

Adoption boundary: paired NLL scoring, public masking, and blind N-governance
had completed for the sealed five-corpus pilot. Only job states, completion
markers, artifact hashes, and chain-validity checks had been observed. No
private salt, arm-to-family mapping, private sign, contrast mean, target delta,
governance family value, governance verdict, or named-arm NLL result had been
read. The private salt remained sealed. This amendment and its analyzer must
be committed before the separate NLL-only reveal opens that salt.

## Scope

This amendment operationalizes inference for the already-prospective
`v2b_nll_exploratory_reveal_v1` route. It changes no target, arm, context,
budget, model input, score, complete-case rule, contrast, or masking rule. It
does not convert the exploratory reveal into formal V2-b unblinding.

The analysis is an **exploratory, NLL-only, one-checkpoint, 20-target-per-repo
pilot** for `Qwen/Qwen2.5-Coder-1.5B` at revision
`df3ce67c0e24480f20468b6ef2894622d69eb73b`. Repositories are analyzed
separately. There is no language-pooled estimate, model-independent ordering,
behavioral claim, NLL-as-correctness claim, or software/codebase scaling-law
claim.

## Frozen scalars, orientations, and target sets

The primary metric is body-only bits per scored byte at `B*=16384` bytes,
recomputed as `primary_nll_nats / (ln(2) * scored_body_bytes)`. The three
within-target deltas retain B3's orientations and exact complete-case sets:

- E1a: `k1 - k4:16384`, eligible exactly when `k4:16384` is eligible.
  Positive values mean dependency context reduces code length.
- E1b: `k3:16384 - k4:16384`, eligible exactly when both cells are eligible.
  Smaller values favor interface-only compatibility; the frozen
  noninferiority margin is `+0.02 bits/byte`.
- E2: `k5:0:16384 - k4:16384`, eligible exactly when both cells are eligible.
  Positive values mean known-dependency context beats the seeded random
  nondependency control.

Every target has equal weight. The canonical target key determines its source
module as identity field zero; that module is the inference cluster. Every
summary reports target N, module G, descending cluster sizes, the exact target
keys, and all three pairwise and three-way target-set intersections.

The analyzer must accept only the exact five-corpus exploratory reveal schema
and its frozen claim/status strings. For each corpus it loads the reveal-bound
masked object and the masked object's completion, assembly, sample,
candidate, and salt-commitment inputs. It re-runs the frozen B3 producer with
the revealed salt, requires object equality with the committed masked object,
and requires the reconstructed contrast mapping to equal the reveal mapping.
Each raw row is then reconstructed as

`delta = sign * published_centered_residual + total_centering_bpb`.

Duplicate/malformed target keys, nonfinite values, binding drift, mapping
drift, B3 replay drift, or a reconstructed-family mean inconsistent with the
published centering aborts the whole artifact.

## Frozen point estimates and intervals

For a contrast with deltas `d_i`, the repo point estimate is
`fsum(d_i) / N`. Reuse, without alteration, the unequal-cluster one-way
random-effects method-of-moments estimator already frozen for blind N
governance, including its all-singleton conservative fallback and lack of an
upper ICC clamp. On the actual eligible target set,

`SE^2 = sigma_b^2 * sum_g(n_g^2) / N^2 + sigma_w^2 / N`.

Degrees of freedom are `G-1`. The two-sided 95% interval is
`mean +/- t(0.975,G-1)*SE`; one-sided 95% lower and upper bounds are
`mean -/+ t(0.95,G-1)*SE`. The existing frozen `t(0.975)` table for df 1--19
is reused. The analyzer freezes a corresponding nine-decimal `t(0.95)` table
for df 1--19 in source and tests its complete vector.

If `N=0`, `G<2`, or the variance calculation is unavailable, the point may be
described when it exists but every interval is null, every inferential p-value
is `1`, and status is `insufficient-clusters`. If `SE` is exactly zero, the
point remains descriptive, intervals are null, p-values are `1`, and status is
`degenerate-zero-se`; a constant tiny pilot can never mechanically establish a
claim. Nonfinite arithmetic aborts.

For nondegenerate rows, one-sided Student-t p-values use the same `G-1`
degrees of freedom:

- `p_E1a = P(T >= mean_E1a/SE_E1a)`;
- `p_E2 = P(T >= mean_E2/SE_E2)`;
- `p_NI = P(T <= (mean_E1b-0.02)/SE_E1b)`;
- `p_active = P(T >= (mean_E1a_intersection-0.02)/SE_intersection)`.

The implementation evaluates the Student-t CDF without a runtime statistics
library: the standard incomplete-beta identity, `math.lgamma`, and a frozen
continued fraction with at most 256 iterations, tolerance `3e-14`, and floor
`1e-300`. Survival probabilities use distributional symmetry rather than
subtracting a positive-tail CDF from one. Synthetic fixed vectors guard the
entire implementation. This is a prospective operational choice, not a
post-outcome model selection.

## E1b active-assay rule and multiplicity

The assay set is the exact E1a/E1b target intersection (equivalently the E1b
complete-case set). E1a is recomputed on that set with its own module
components and bounds. The E1b intersection-union p-value is
`max(p_NI, p_active)`.

Within each repository, Holm's step-down adjustment is applied to exactly
`[p_E1a, p_E1b_IUT, p_E2]`, sorting ties by contrast name. No adjustment or
pooling occurs across repositories. The adjusted p-values are descriptive
exploratory multiplicity controls; they create no confirmatory claim.

The E1b interpretation label is deterministic:

1. unavailable/degenerate bound -> `inference-unavailable`;
2. E1b one-sided upper bound above `0.02` ->
   `noninferiority-not-established`;
3. E1b bound passes but the intersection E1a one-sided lower bound is below
   `0.02` -> `assay-insensitive-inconclusive`;
4. both bounds pass but Holm-adjusted E1b p-value is above `0.05` ->
   `multiplicity-not-established`;
5. both bounds pass and the adjusted p-value is at most `0.05` ->
   `interface-sufficiency-compatible-exploratory`.

The last phrase is never shortened to equivalence, confirmation, or
"interfaces suffice."

## Frozen reporting surface

The machine artifact contains the exact reconstructed target rows, module
labels, point estimates, variance components, intervals, raw and
Holm-adjusted p-values, assay result, overlaps, governance verdict/N as design
metadata, and complete provenance bindings. A later renderer may make only:

- a per-repo forest plot for E1a/E1b/E2 with no pooled diamond, a zero line,
  and the E1b `+0.02` margin;
- an E1b assay panel pairing its upper bound with the intersection E1a lower
  bound;
- complete-case N/G/cluster-size/overlap tables; and
- target-delta strip or ECDF plots labeled by repo and contrast.

No source-token attribution is licensed by this reveal. Bits/codepoint,
boundary-inclusive scores, extra k5 seeds, same-dependency-set arms, Python
coverage/duplicate subsets, and PhysLib external-context sensitivities require
their own prospectively frozen consumer before inspection and are absent from
this primary exploratory artifact.
