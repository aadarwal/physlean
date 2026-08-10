# Amendment: frozen t-table extension for supplement-scale cluster counts

Date: 2026-08-10 EDT. Status: DRAFT pending delta review. Boundary: the
supplement consumer REFUSED before producing any artifact ("no frozen
Student-t quantiles for df=102"); no supplement or pooled panel has
been computed or read. The refusal itself is outcome-blind: it fired
on cluster COUNT (the n=120 supplement spans ~100+ modules), a
model-free property of the draw, before any delta entered inference.

## The gap

The frozen inference machinery carries exact Student-t quantiles for
df 1–19 — the pilot's cluster counts never exceeded 20 modules. The
supplement's predeclared pooled panels have ~103 modules (df ≈ 102),
outside the table, and the consumer correctly refused rather than
substitute anything.

## Adopted rule (additive, conservative, frozen values)

The two frozen tables gain the classic printed-table breakpoints
{20, 25, 30, 40, 60, 80, 120} with their standard published values.
Lookup: an exact df hit uses its entry (df 1–19 behavior is therefore
BYTE-IDENTICAL to every committed artifact — this change can alter no
existing number); a df ≥ 20 without an exact entry uses the LARGEST
tabulated breakpoint ≤ df. Because Student-t quantiles strictly
decrease in df, substituting a lower-df quantile is conservative in
every reported direction: two-sided CIs widen, one-sided bounds
loosen, and derived p-values grow. df = 102 therefore uses the df=80
quantiles (t.975 = 1.990063421 vs the exact 1.9835 at df=102). No
interpolation, no runtime statistics library, no data-dependent
choice: the breakpoint set and values are the standard table, fixed
here before any supplement panel existed.

## Scope

analyze_v2b_nll_exploratory's T_095_BY_DF / T_0975_BY_DF and their
lookup only. Every committed artifact reproduces unchanged (df ≤ 19
paths untouched); the expansion-consistency verifier's guarantees are
unaffected. Consumers that never see df > 19 are behaviorally
identical.
