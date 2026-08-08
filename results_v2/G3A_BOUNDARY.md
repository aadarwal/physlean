# G3a sentinel boundary

Date: 2026-08-08  
Measurement source: `ac9a66574f749ccc302607ad6a979c06327f74ef`  
Evidence commit: `570c433`  
GPU job: `19904528` (`COMPLETED 0:0`, 00:32:48, L40S)  
Dependent analysis job: `19904915` (`COMPLETED 0:0`, 00:02:08)

## Commands

The frozen launcher performed the G3a preflight and submitted the 44-cell
Qwen2.5-Coder-0.5B sentinel:

```bash
bash submit_all.sh
```

The dependent CPU job ran only after Slurm reported success for the GPU job:

```bash
.venv/bin/python preflight_check.py --gate sentinel-post
.venv/bin/python analyze_v2.py
.venv/bin/python make_plots.py
```

## Integrity result

- 44/44 expected cells verified; no gaps.
- 15/15 phase variants defined.
- 88 raw dump/meta artifacts inventoried; 0 quarantined.
- 44 cells analyzed; 0 analyzer errors.
- 5 base streams phase-paired; 0 pairing problems.
- Every headline base cell exceeded the frozen quantitative floors.
- Each meta records schema v4, chunk 2048, bf16/CUDA, resolved SDPA,
  harness hash `2425bbdf...`, environment fingerprint `cece9b716...`, and an
  unchanged source tree during evaluation.

## Scientific boundary verdict

This is an **instrument pass**, not a scaling-law result.

The same-group phase ablation shows a consistent benefit from more preceding
context for identical content. All 15 corpus/phase document-bootstrap
intervals are above zero. The ordinary stream curves also show large
nonparametric early-to-late context gains.

The frozen power-law holdout gate rejects all five headline base curves. Only
2 of 64 quantitative strata accept the proposed `A*c^(-beta)+Linf` form, and
neither is a headline base. No exponent or asymptote is reportable for a
rejected fit. The curves remain descriptive and nonparametric.

Order, per-document reset, and especially selection seed move aggregate BPB
enough to make stream-level cross-corpus readings noise-limited. Those
sensitivities are useful diagnostics, but they are not repaired by adding more
model sizes. The next claim-bearing step is therefore G3.5: fixed targets,
paired context conditions, and each target as its own control. The optional
G3b grid remains behind its explicit human scale gate.

The V2-a measurement code is constrained to new standalone files:
`eval_incontext.py` and `layout.py` remain untouched and the Python dependency
lock does not move. Its commit also adds the deferred `cell_done` identity
test: an older cell's recorded whole-source hash may differ while its current
harness and environment identities still validate. This signoff commit moves
the whole-source hash, so the existing short battery rerun is required before
any later G3b launch; no identity whitelist is weakened.

The two agent reviews agreed on this verdict. The long-title clipping observed
in one descriptive plot is cosmetic; the renderer is repaired after this
evidence commit and does not alter any numeric artifact.
