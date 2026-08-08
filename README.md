# physlean — repository-context predictability of code (Lean 4 focal case)

Motivated by gwern.net/lean-scaling; run as a gated, adversarially
reviewed measurement campaign. **The source of truth is `PREREG.md`**
(estimand, contamination protocol, analysis plan, frozen gates) and
**`DESIGN_V2.md`** (the confirmatory fixed-target design). Nothing in
this repo claims a "software/codebase scaling law": every measurement
here manipulates *available context*, not codebase scale or growth
(PREREG §1; DESIGN_V2 sketches the only design that could address
growth as a separate, gated longitudinal arm).

## What is measured

Byte-normalized, source-span-grouped, teacher-forced code length of
pinned base models on byte-budget-matched corpus streams (Lean 4 /
Python / C++ / a LaTeX-source reference corpus — raw arXiv e-print bundles, not clean prose; physics-domain-matched), as a function of
window-relative context — **descriptive and model-relative**, with
window/document sample-size discipline and frozen fit-acceptance gates.
Contamination control is clean-target masking (post-cutoff targets in
natural context), reported as a temporal-generalization (cohort) gap.

## How work proceeds

Gates G0…G6 (PREREG §11): every boundary ships commit hashes, exact
commands, machine-checked preflight evidence, and a disagreement log.
Execution is fail-closed end to end (`preflight_check.py`,
`fix_cluster.sh`, `submit_all.sh` — sentinel-first: the first science
run is one small model, reviewed for instrument viability before any
expansion). Phase 2 (from-scratch training) is hard-blocked pending
redesign (PREREG §10).

Key entry points: `fix_cluster.sh` (acquisition, fail-closed) ·
`validity_battery.py` (plumbing invariants, G2) · `run_phase1.py`
(resumable grid runner) · `analyze_v2.py` (analyzer v3) ·
`make_plots.py` (descriptive plots only). Tests: `tests/`.

## Legacy

`legacy/` holds the original CPU-pilot harness — historical,
non-evidentiary, guarded against accidental use (its β/L∞ framing is
explicitly not endorsed; see `legacy/README.md`). `HANDOFF.md` and
`RESUME.md` are historical documents kept for provenance; where they
conflict with `PREREG.md`, PREREG wins.
