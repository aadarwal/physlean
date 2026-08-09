# Prospective V2-b amendment: exploratory NLL model ladder

Date: 2026-08-09 EDT. Status: DRAFT — adoption requires independent
adversarial review; nothing below authorizes a paired score until that
review is recorded and the frozen ladder analyzer exists.

Adoption boundary: the sealed five-corpus pilot has been scored, masked,
governed, and revealed at exactly one checkpoint (`Qwen/Qwen2.5-Coder-1.5B`
@ `df3ce67c0e24480f20468b6ef2894622d69eb73b`; exploratory NLL-only reveal
`job20007464`, analysis `job20013803`). No paired NLL score, instrument
battery, masked artifact, governance row, or analysis exists for any other
checkpoint. Model snapshot downloads are outcome-free acquisition. No
behavioral generation, verifier outcome, or S4/S5 artifact exists for any
tier. This amendment reads no new score and reveals nothing.

## Scope

Extend the already-prospective exploratory NLL-only route to three further
tiers of the SAME model family, on the SAME evidence chain:

- identical committed assembly manifests
  `results_v2/v2b/assembly/job19991210_{0..4}_*.json` (hash-bound, reused
  byte-identically — context text is model-independent);
- identical sealed 20-identity sample `job19989076` and candidate chain;
- identical primary metric (body-only bits per scored byte at
  `B* = 16384`), orientations (E1a = k1−k4, E1b = k3−k4, E2 = k5:0−k4),
  complete-case eligibility, and per-repo inference machinery
  (unequal-cluster MoM, frozen t-tables, Holm over the three contrasts).

It changes no target, arm, context byte, budget, eligibility rule,
contrast, margin, governance rule, or behavioral contract, and it does not
touch the sealed 1.5B artifacts.

## Frozen tier identities

Revisions are the append-only `models.json` pins recorded at acquisition;
param ranges are predeclared loader sanity bounds fixed here, before any
tier battery outcome exists.

| tier tag | model | revision | family | param range | battery file |
|---|---|---|---|---|---|
| q25c-0.5b | Qwen/Qwen2.5-Coder-0.5B | 8123ea2e9354afb7ffcc6c8641d1b2f5ecf18301 | q25c-0p5b | (0.3e9, 0.7e9) | battery_pilot_0p5b.json |
| q25c-1.5b | Qwen/Qwen2.5-Coder-1.5B | df3ce67c0e24480f20468b6ef2894622d69eb73b | q25c-1p5b | (1.2e9, 1.8e9) | battery_pilot_1p5b.json |
| q25c-3b | Qwen/Qwen2.5-Coder-3B | 09d9bc5d376b0cfa0100a0694ea7de7232525803 | q25c-3b | (2.5e9, 3.5e9) | battery_pilot_3b.json |
| q25c-7b | Qwen/Qwen2.5-Coder-7B | 0396a76181e127dfc13e5c5ec48a8cee09938b02 | q25c-7b | (6.0e9, 8.5e9) | battery_pilot_7b.json |
| q25c-14b | Qwen/Qwen2.5-Coder-14B | f2ad5164aade432d6d56c24bb71589184d5d613d | q25c-14b | (13.0e9, 16.0e9) | battery_pilot_14b.json |

The q25c-1.5b row restates the existing frozen constants and is complete
(its battery is `860e526`-lineage evidence, already committed); it is never
re-scored under this amendment. q25c-14b was human-authorized on 2026-08-09;
because its fp32 battery semantic leg exceeds L40S device memory, its
battery (and, for uniformity, its scoring) runs on H200, and the battery
launcher refuses a non-H200 device for that tier. Rungs above 14B remain
unauthorized.

## Instrument gating (per tier)

Each new tier receives its own prospective write-once instrument battery
before any paired score at that tier — the exact 1.5B semantics of the
2026-08-08 pilot-battery adoption, evaluated on that tier's pinned
revision with its predeclared loader param range: production-path
8192-token repeat/causality gates, the fp32 semantic leg, SDPA required,
`plumbing_pass` and unchanged-identity requirements, separate artifact
file, never overwriting any other battery. Batteries are target-, draw-,
salt-, and outcome-independent instrument validation and may run before
this amendment's adoption; every battery binds the source tree that will
take the scores. The paired launcher refuses to score a tier whose battery
is missing, untracked, drifted from HEAD, or bound to a different tree.

## No new blind, and the analyzer-first rule

The B3 masking salt is public since the exploratory reveal, so no
family-level blind exists for ladder deltas and none is claimed. In its
place, the reveal discipline is kept: a single frozen ladder analyzer
(`analyze_v2b_nll_ladder.py`) must be committed, with tests, as an
ancestor of the scoring tree BEFORE any ladder tier is scored, and ladder
results may be read only through its committed per-repo artifacts
(`v2b_nll_ladder_analysis_v1`). Its frozen mechanics, chosen for exact
reuse of already-frozen code paths rather than re-derivation:

- deltas come from the unchanged B3 producer `build_masked_deltas`,
  invoked with the fixed PUBLIC salt of 32 zero bytes (declared
  non-blinding) and a synthetic public salt-commitment note; raw rows
  reconstruct through the reveal-frozen `_reconstruct_family` identity
  (`sign * published + total_centering`), so every completion/manifest/
  sample/candidates binding check and the bpb-recompute rule apply
  byte-identically to every tier;
- per-repo inference is the unchanged `_analyze_repo_rows` (same MoM,
  frozen t-tables, Holm, E1b intersection-union assay, PhysLib forcing);
- each tier's completion `run_identity` must match the registry tier
  exactly (model, revision, dtype bfloat16, chunk 2048) and its
  `pilot_battery_sha256` must equal the committed tier battery file's
  hash; every tier of one repo must bind the same assembly manifest;
- ladder tiers carry governance verdict
  `not-run-ladder-exploratory` / `repo_n = null` (blind N governance was
  a 1.5B-pilot artifact and is not recomputed);
- the q25c-1.5b tier is INCLUDED by recomputation from its sealed
  completion and must reproduce the committed reveal's per-contrast
  `removed_mean_bpb`/`fsum_correction`/`total_centering_bpb` exactly —
  a standing consistency check tying the ladder to the sealed pilot.

Claim status string: `exploratory-nll-only-multi-checkpoint-pilot`.

## Prespecified reading

Reported per (tier, repo, contrast): complete-case N/G, target-equal mean,
two-sided 95% interval, one-sided p, Holm-adjusted p, and the same
interpretation-status vocabulary as the 1.5B analysis, including the E1b
active-assay rule and PhysLib's forced
`uninterpretable-pending-k4x-sensitivity`. Cross-tier presentation is a
DESCRIPTIVE forest plot per repo/contrast over the four tiers. No pooled
cross-tier trend statistic, no confirmatory scale claim, no
language-pooled estimate, no behavioral claim, and no NLL-as-correctness
claim is licensed. The motivating question — whether the Lean
dependency-context gain persists, grows, or shrinks with model scale
(structural predictability vs missing-prior substitution) — is answered
qualitatively from that forest, labeled exploratory.

## Sequencing

1. (may precede adoption) model snapshots at pinned revisions; per-tier
   batteries on the committed scoring tree.
2. Adoption of this amendment after independent adversarial review,
   recorded in PREREG §13 with this file's hash.
3. Frozen ladder analyzer + tests committed (ancestor of scoring tree).
4. Paired scoring per tier via the ladder launcher (assembly job
   19991210 bound), L40S, one tier at a time.
5. Analyzer artifact committed as evidence; then read.
