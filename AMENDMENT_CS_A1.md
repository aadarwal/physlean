# AMENDMENT CS-A1 (PROPOSED — not adopted, not in force)

**Status: DRAFT for human review.** Nothing in this file changes any
frozen instrument, any registered artifact, or the meaning of any result
already produced. It proposes a protocol repair for a defect found on
2026-08-30, states the evidence, and lists the options with a
recommendation. ARM_CS §5 and PREREG §13 govern; adoption requires the
human's decision and a PREREG entry, exactly as CS-0 did.

## 1. The defect

CS-2 records `final_val_bpb` — the validation loss at the *last* step —
as the measured loss L(P) for each data budget P. At the top rungs most
runs reach their best validation loss partway through training and then
degrade, so the recorded endpoint is not what the model achieved.

Per-seed evidence (10m arm, T=4096, `scripts/cs2_diagnose_top.py`):

| language | rung | seed | epochs | final | best | degradation |
|---|---|---|---|---|---|---|
| python | 1.0000 | 0 | 32 | 2.8487 | 0.9432 | +1.906 after step 5904 |
| python | 1.0000 | 1 | 32 | 1.1434 | 1.0640 | +0.079 after step 9840 |
| latex | 1.0000 | 0 | 128 | 4.2653 | 1.5559 | +2.709 after step 9428 |
| latex | 0.5000 | 2 | 128 | 2.9726 | 1.5154 | +1.457 after step 2432 |
| cpp | 0.2500 | 0 | 64 | 0.9847 | 0.8305 | +0.154 after step 1962 |
| cpp | 1.0000 | 0 | 64 | 0.7166 | 0.6918 | +0.025 after step 15744 |
| **lean** | 1.0000 | 1 | 32 | **0.7472** | 0.7914 | none (still improving) |

Seed-mean L(P) over the ladder (T=4096) is monotone for lean
(4.66 → 4.16 → 3.88 → 1.36 → 1.06 → 0.86 → 0.76) and for cpp
(4.39 → … → 0.68), but **rises** over the last two rungs for python
(1.10 → 1.51 → 1.64) and latex (1.79 → 2.31 → 3.00): more data, worse
recorded loss, which no healthy data-scaling curve can do.

## 2. Why the pipeline could not catch it

1. The HP walk's neighbour set is `{lr×3, lr, lr/3} × {epochs, 2·epochs}`
   — epochs can only ever **double**. Top rungs therefore run 32 (lean),
   64 (cpp), 128 (latex) epochs, and a rung that destabilises cannot be
   rescued by the search.
2. Selection and analysis both read `final_val_bpb`, so an unstable run
   is scored by its damaged endpoint at every stage.
3. The trainer is not at fault in the obvious way: cosine decay to
   `0.1·lr` and `clip_grad_norm_(…, 1.0)` are already present.

## 3. What this invalidates

- **The capacity adjudication for python, cpp, latex.** The guard fired
  (30m beat 10m by > 0.01 b/B) against 10m runs that had degraded. Its
  prescribed remedy — a complete separate 30m ladder per language,
  ≈50 GPU-h each at our 6-GPU ceiling — would be spent chasing an
  artifact. **Not launched.** cpp is the ambiguous case: its curve stayed
  monotone, so its verdict may survive a repair, but it cannot be read
  from the present evidence either.
- **γ for python, cpp, latex** on this pass: γ is fitted from the top
  rungs, which are the damaged ones.
- Nothing about **lean** (clean at every rung, guard un-fired) and
  nothing about **CS-1 / β_corr**, which involves no training.

## 4. Options

- **A. Best-checkpoint evaluation.** Define L(P) as the minimum recorded
  validation loss rather than the final one. Cheap (the histories already
  exist; no retraining for the estimate itself), but it changes the
  estimand mid-flight and biases downward by taking a minimum over a
  noisy trace — the bias grows with the number of evaluations, which
  differs across rungs, so it is not obviously safe for an exponent fit.
- **B. Epoch cap + a halving neighbour.** Add `epochs/2` to the walk's
  neighbour set and cap epochs, then re-run the affected rungs. Fixes the
  cause rather than the symptom; costs a partial re-tune plus re-runs for
  three languages (order 100+ GPU-h at our width).
- **C. Stability guard.** Declare a run INVALID when
  `final > best + τ` (τ predeclared, e.g. 0.02 b/B), and re-run it at
  `lr/3`; a rung whose runs cannot pass becomes INDETERMINATE(stability).
  Keeps `final_val_bpb` as the estimand, makes the failure explicit
  rather than silently absorbed, and localises the extra compute.
- **D. Report as-is.** Let the analyzer's convergence/R² gates return
  INDETERMINATE for the affected languages, and report the instability as
  a finding of the pass. Zero extra compute; yields no γ for three of
  four languages.

**Recommendation: C, with D as the immediate reporting posture** — i.e.
report this pass honestly now (lean registered, others INDETERMINATE with
the instability documented), and adopt C for the repair pass so the
estimand is unchanged and every excluded run is excluded by a stated,
predeclared rule. A is tempting and cheap but silently redefines the
measured quantity; B is the cleanest science but the most expensive, and
is the natural CS-3 design rather than a mid-pass patch.

## 5. What adoption would require

1. Human decision recorded, with the option chosen and the reason.
2. A PREREG §13 entry stating: the defect, the evidence above, the τ (if
   C), the affected languages, and that the first CS-2 pass remains
   EXPLORATORY by declaration (ARM_CS §5.5) — no confirmatory claim is
   rescued or created by this amendment.
3. Re-running only the affected rungs; lean's runs are untouched, and any
   re-run keeps the frozen canonical run identity.
4. The capacity adjudication for python/cpp/latex re-derived from
   repaired 10m runs before any 30m ladder is considered.
