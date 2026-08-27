# Prospective V2-b amendment: E1b assay sensitivity

Recorded 2026-08-08 EDT before any V2-b sample draw, paired NLL scoring,
masked contrast, arm-label reveal, or salt reveal. No target-level generation,
behavioral result, paired NLL result, A6 label, arm label, or salt was inspected
for this amendment. The outcome-independent 1.5B instrument battery had passed
and was committed at `860e526`; it contains no E1a/E1b target contrast.

E1b's interface-only versus implementation-bearing non-inferiority comparison
requires an active assay. For each `(repo, model family, B)` cell, report the
E1a and E1b estimates jointly. E1b may support the substantive phrase
"interface sufficiency" only when both of these prospective conditions hold:

1. The existing E1b rule holds: the one-sided upper 95% confidence bound for
   the paired mean `C(t | k3, B) - C(t | k4, B)` is at most `0.02 bits/byte`.
2. In the same cell and on the E1a/E1b target intersection, the one-sided lower
   95% confidence bound for the paired mean
   `C(t | k1) - C(t | k4, B)` is at least `0.02 bits/byte`.

If condition 2 fails, the E1b estimate and interval are still reported, but its
mechanistic interpretation is **ASSAY-INSENSITIVE / INCONCLUSIVE**, never
"interfaces suffice". This prevents a model that makes no effective use of
either repository-context arm from passing the non-inferiority criterion
vacuously. The `0.02 bits/byte` sensitivity threshold is fixed equal to the
already-preregistered E1b non-inferiority margin; it is not estimated from the
pilot.

No second primary margin is introduced. Bits/byte remains the preregistered
primary code-length unit: each E1b pair scores the identical target bytes,
confirmatory language pooling is prohibited, and bits/codepoint is already a
required sensitivity. Corpus-specific conversion of the primary margin after
observing corpus composition would change rather than merely restate the
estimand.
