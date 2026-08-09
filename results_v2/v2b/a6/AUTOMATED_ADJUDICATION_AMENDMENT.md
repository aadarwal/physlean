# A6 blinded-adjudication amendment — before labels, sampling, or outcomes

Date: 2026-08-09

The original A6 packet and presentation remain unchanged. The browser UI
started one local draft label, but no complete `v2b_a6_blind_labels_v1`
artifact was written, committed, finalized, or consumed; the V2-b sample has
not been drawn and no paired/model outcome has run. The user then correctly
objected that one fatigued, nonexpert human is not intrinsically a more
reliable code-equivalence judge than a reproducible blinded ensemble.

The complete labels will therefore be produced prospectively as follows:

1. Three fresh adjudicators receive only the committed blind presentation and
   this rubric: duplicate means the same implementation/specification modulo
   one systematic identifier renaming; a shared syntax skeleton is
   insufficient, and changed API calls or referenced constants mean
   not-duplicate. They receive no packet, repository, source identity,
   similarity statistic, hidden role, sample, salt, or model outcome.
2. Each adjudicator must label every opaque pair independently in the exact
   `v2b_a6_blind_judgments_v1` schema. Missing, extra, duplicate, or malformed
   judgments abort publication.
3. A deterministic lexical gate reuses the frozen A6 Lean/Python lexers. After
   canonicalizing layout widths, all nonidentifier token kinds and values must
   be identical. Identifier correspondence must be a consistent bijection
   with at most one nonidentity mapping. This gate can reject a duplicate vote
   but cannot create one.
4. The final label is `duplicate` iff the lexical gate passes and at least two
   of the three blinded adjudicators vote `duplicate`; otherwise it is
   `not-duplicate`. No tie, manual override, relabeling, or post-outcome appeal
   exists. Exact judge files, script, contract, presentation, keyword freeze,
   per-pair votes, and decisions are hash-bound and committed with the labels.
5. The primary research agent does not vote. The user pasted one visible pair
   to it while questioning the procedure; this occurred before this amendment
   and does not enter any adjudicator context or decision.

This changes only the blinded A6 adjudicator. It does not alter the sealed pair
selection, rubric, hidden projection, threshold/collision truth tables,
candidate universe, battery, model, sample seed, or analysis. The existing
post-commit A6 finalizer remains the sole path from complete labels to an
outcome, and the label path must still have exactly one touching commit.
