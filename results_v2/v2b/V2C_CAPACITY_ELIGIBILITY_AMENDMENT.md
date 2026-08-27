# Amendment: model-capacity cell exclusion (V2-c scoring incident)

Date: 2026-08-11 EDT. Status: DRAFT pending delta review. Boundary:
the V2-c mathlib4 completion (52 targets) is written but not yet
evidence-committed or read (its deltas are salt-masked by design);
the sympy scoring job died at target 22/77 BEFORE writing that
target, on the incident below; no V2-c cell has been consumed by any
analyzer; the V2-c blind is intact.

## The incident (recorded verbatim)

V2-c sympy scoring (job 20156668, tier q25c-1.5b) failed fatally:
"paired prompt has 32856 tokens, exceeding model maximum 32768" — a
64 KiB cell whose context + prefix + body tokenizes past the model's
context window. The 20-target pilot never drew such a combination;
the n=77 stratified confirmatory draw did. The failure fired on a
model-free structural property of the draw (byte sizes and tokenizer
arithmetic), before any masked delta existed for the target.

## Adopted handling

An over-window prompt is a MODEL-CAPACITY exclusion, handled exactly
like the frozen empty-rendering rule's spirit: never truncated, never
silently absent, never fatal to the run.

- eval_paired: the capacity guard raises a typed CapacityExceeded;
  the per-target cell loop records the cell UNSCORED with the
  manifest's grid metadata untouched (the frozen grid-equality
  surfaces are unaffected — `eligible` is manifest metadata and is
  not modified) plus three recorded facts: capacity_excluded=true,
  capacity_prompt_tokens, capacity_model_max. No primary is recorded.
- Consumption: a new shared helper `cell_scoreable(cell)` — assembly
  eligible AND not capacity-excluded — replaces the raw eligibility
  checks in the complete-case paths (the B3 masked producer and the
  dose extract/reference machinery). A capacity-excluded cell
  therefore drops its target from exactly the contrasts that need
  that cell, per the standing complete-case rule.
- Attrition reporting: capacity exclusions are visible in the
  completions (the recorded facts above) and must be reported in any
  artifact reading the affected corpus, alongside the standing
  eligibility attrition.

The pilot, ladder, dose, interior, and supplement artifacts are
unaffected: no committed completion contains a capacity-excluded cell
(the guard was previously fatal, so any occurrence would have aborted
the producing run — none did).

## Scope

eval_paired's cell loop and capacity guard; v2b_common.cell_scoreable;
the two complete-case call sites named above; the V2-c rerun of the
sympy scoring (same out dir; the resume audit accepts the 21 already
written targets whose grids are unchanged). Nothing else. The V2-c
blind and salt machinery are untouched.
