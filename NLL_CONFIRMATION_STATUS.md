# NLL confirmation pipeline — status map (four-model fresh-SymPy E2 study)

Date: 2026-08-27. Written after merging `origin/codex/nll-confirmation`
(salvage tip `bb94c0f`) into this worktree branch and completing the three
files the WIP's own freeze closure required but never created. This document
maps the pipeline, records what is complete, and ends with the gated launch
sequence. It decides nothing scientific; PREREG.md and DESIGN_V2.md remain
the source of truth, and the frozen protocol JSON is the study's contract.

Study: `v2b-nll-e2-fresh-sympy-q25c-ladder-20260809`
(`results_v2/v2b/NLL_E2_CONFIRMATION_PROTOCOL.json`, schema
`v2b_nll_e2_confirmation_protocol_v1`, raw sha256 `06c179e0fae57330...` —
the committed blob validates against the hashes baked into
`v2b_nll_confirmation.py`; verified on this tree).

## 1. What this study is

Outcome-informed hypothesis confirmation, disclosed as such. The exploratory
NLL-only pilot (revealed `job20007464`, analyzed `job20013803`) selected ONE
hypothesis: SymPy E2 — known dependency context beats equal-budget frozen
random non-dependency context (`BPB(k5:0:16384) − BPB(k4:16384) > 0`) at
Qwen2.5-Coder-1.5B (pilot mean 0.02175 bpb, Holm p 0.02427, exploratory
only). The confirmation re-tests that single hypothesis on a fresh cohort:

- **Repo/language**: sympy only (corpus sha `c0a595d78fb2...`). No language
  pooling; no cross-family model claim.
- **Primary**: `E2_q25c_1p5b_seed0` at the 1.5B checkpoint, one-sided
  module-MoM Student-t, alpha 0.05, success = p ≤ .05 AND one-sided 95%
  lower bound > 0, with a 0.02-bpb realized-halfwidth precision gate
  (precision gate, never an effect margin).
- **Secondary family (gated on primary confirmation, Holm FWER 0.05)**:
  E1a at 1.5B; E2 at 0.5B, 3B, 7B; a per-target OLS log2-size slope over
  the four checkpoints ("finite Qwen2.5-Coder ladder trend"; the protocol
  forbids calling it a scaling law). E1b is descriptive-only. k5 seeds 1/2
  are diagnostics that "cannot rescue" the primary.
- **Decision labels** are frozen in precedence order, ending in
  `e2-positive-confirmed-fresh-sympy-1p5b` /
  `e2-opposite-direction-descriptive` / `e2-inconclusive`.

### Models and checkpoints (the answer to "which models")

FOUR tiers — not the five-tier exploratory ladder, and NOT 14B:

| id | model | revision (== models.json pin) | role |
|---|---|---|---|
| q25c-0.5b | Qwen/Qwen2.5-Coder-0.5B | 8123ea2e9354afb7ffcc6c8641d1b2f5ecf18301 | gated-secondary |
| q25c-1.5b | Qwen/Qwen2.5-Coder-1.5B | df3ce67c0e24480f20468b6ef2894622d69eb73b | **sole-primary** |
| q25c-3b | Qwen/Qwen2.5-Coder-3B | 09d9bc5d376b0cfa0100a0694ea7de7232525803 | gated-secondary |
| q25c-7b | Qwen/Qwen2.5-Coder-7B | 0396a76181e127dfc13e5c5ec48a8cee09938b02 | key-generalization-gated-secondary |

All four revisions match the append-only `models.json` pins exactly
(verified on this tree). Scoring identity: bfloat16, cuda, SDPA-family
attention, `layout.PRODUCTION_CHUNK_TOKENS` chunking, via the frozen
`eval_incontext` kernel. Battery + scoring Slurm wrappers pin L40S.

### Does the draw produce a FRESH sealed sample? (the critical question)

**Yes — fresh cohort, fresh blind.** This is exactly the remedy the
reveal amendment demanded ("a confirmatory … co-primary requires a fresh
independently sealed target sample and salt"):

- **Fresh targets**: N=200 SymPy targets drawn by the UNCHANGED pilot draw
  law (`v2b_metadata.build_sample_plan`, seed `v2a:20260808`, full-table
  cutpoints/strata/priorities) over the 19,926-candidate universe with
  `exclude_keys = source-ineligible ∪ every candidate in any of the 19
  pilot source modules ∪ the exact 20 pilot target keys`. The sampler
  hard-verifies module disjointness from the pilot, so pilot and
  confirmation never share a modeled dependence cluster, and the protocol
  forbids pooling them (no "N=220"). The exploratory reveal burned the old
  sealed sample only as a *blind*; its 20 identities and 19 modules are
  used here solely as an exclusion list, bound by sha256.
- **Fresh salt**: a NEW confirmation-only 32-byte random salt (pilot salt
  explicitly forbidden), created outside Git, mode 0600, no reroll, with a
  public SHA256 commitment committed before any score; scores are masked
  into 4 models × 5 opaque `fam-` contrast families × 200 fixed-width
  ciphertexts with eligibility/padding invisible, and mapped only by one
  registered reveal after the blind fixed-N execution gate.
- **Honest caveat, already in the protocol**: the target *identities* are
  deterministic from committed model-free artifacts (the draw law is
  public), and pilot NLL knowledge exists — the study is therefore framed
  as outcome-informed confirmation on untouched targets, with the pilot's
  blind N-governance verdict (`job20005942_3_sympy`, verdict feasible,
  repo_n 200) supplying only an N=200 *rationale*, never a power claim.
  What is sealed is the score→contrast mapping until the registered
  reveal, plus fail-closed execution completeness before it.

## 2. Relation to the V2-c machinery

V2-c (mathlib4+sympy paired confirmatory study at 1.5B) and this pipeline
are sibling confirmations that share the same governance substrate but are
deliberately separate programs:

- **Shared by import** (the freeze closure pins each file's hash):
  `v2b_metadata.build_sample_plan` (the frozen pilot draw law — V2-c calls
  it with `test_stratum=True` + pilot-target exclusion; the confirmation
  calls it with the pilot law defaults + its module-disjoint exclusion),
  `v2b_n_governance.variance_components` (the module-MoM estimator, reused
  by `v2b_nll_confirmation_stats`), `finalize_v2b_sample._validate_candidate_table`,
  the renderer/source internals (`v2b_assemble`, `prepare_v2b_assembly`
  loaders), `eval_incontext`/`eval_paired` numerical kernels,
  `provenance` (source_clean / source_tree_hash / env fingerprint) and
  `v2b_a6_blind.require_committed`.
- **NOT used**: `v2b_v2c_governance.py`. V2-c's N came from that module's
  standardized-power plan (`v2c_governance_plan_v1`, anchor 0.5·sigma);
  the confirmation's N=200 is instead the pilot's committed BLIND
  governance verdict, embedded and hash-validated in the protocol. No
  governance artifact is produced by this pipeline; the analyzer's gates
  (G ≥ 20 modules, effective clusters ≥ 10, 0.02 halfwidth) play that role.
- **Same evidence-chain idiom as V2-c's reveal** (opaque `fam-` HMAC
  families, salt commitment, registered one-shot reveal, decision labels,
  binding stanzas — compare `results_v2/v2c/V2C_REVEAL.json`), but
  re-implemented confirmation-specific: its own six-cell enumerator (the
  pilot 23-cell enumerator is *forbidden* by the protocol), its own
  `v2b_nll_e2_confirmation_*` schema family, and its own crypto domains
  (`v2b-nll-e2-confirmation-family-v1` / `-payload-v1`).

Timeline: the protocol and the first five pipeline commits landed
2026-08-09 (sealed parent `d70f335`), i.e. BEFORE the V2-c amendment
adoption (2026-08-10) and V2-c execution (through 2026-08-12); the
remaining stages were mid-build at the 2026-08-12 pause and were salvaged
verbatim to `bb94c0f` on 2026-08-27. No confirmation stage has ever
executed: no freeze, census, sample, battery, score, mask, or reveal
artifact exists anywhere under `results_v2/`.

## 3. Script map and evidence chain

Chain (each stage fail-closes on: dirty tree outside results_v2, any
uncommitted required predecessor, any hash/binding mismatch, and re-checks
that nothing — including HEAD and the private salt — changed during its
own run):

```
protocol (committed, hash-baked)
  -> implementation freeze         freeze_v2b_nll_confirmation.py
  -> source gate: fragment xN      prepare_v2b_nll_confirmation_gate.py fragment
  -> source gate: reduce           prepare_v2b_nll_confirmation_gate.py reduce
  -> sample draw (N=200)           finalize_v2b_nll_confirmation_sample.py
  -> six-cell assembly manifest    prepare_v2b_nll_confirmation_assembly.py
  -> salt + public commitment      prepare_v2b_nll_confirmation_salt.py
  -> 4x instrument battery (GPU)   v2b_nll_confirmation_battery.py
  -> 4x scoring shards (GPU)       eval_v2b_nll_confirmation.py score
  -> 4x model reducer              eval_v2b_nll_confirmation.py reduce-model
  -> study reducer                 eval_v2b_nll_confirmation.py reduce-study
  -> fixed-width masking           prepare_v2b_nll_confirmation_masked.py
  -> blind fixed-N gate            finalize_v2b_nll_confirmation_fixed_n.py
  -> registered one-shot reveal    finalize_v2b_nll_confirmation_reveal.py
  -> registered analysis           analyze_v2b_nll_confirmation.py
```

Per-file status (all COMPLETE unless noted; "complete" = implemented,
imports clean, CLI parses, covered by synthetic tests, no stubs):

| file | role | notes |
|---|---|---|
| `results_v2/v2b/NLL_E2_CONFIRMATION_PROTOCOL.json` | protocol-data | frozen `frozen-before-confirmation-sample-and-score`; validates against baked hashes |
| `v2b_nll_confirmation.py` | protocol-validator | hard-codes model rows, cells, labels, slope coefficients; every producer imports it |
| `freeze_v2b_nll_confirmation.py` | implementation-freeze | 49-file explicit closure (protocol JSON + 15 confirmation modules + 9 shared modules + 15 tests + 9 sbatch); `validate_live_freeze` re-hashes every file against HEAD blobs, requires the freeze artifact to be one-touch; dry-run of the producer succeeds on this tree (49/49) |
| `v2b_nll_confirmation_context.py` | source-context | exact bitset SCC closure + additive render-length math shared by gate and assembly; loads candidates/extraction/k7/neardup/a6 by protocol-bound sha256; verifies `corpora/sympy` checkout identity |
| `prepare_v2b_nll_confirmation_gate.py` | source-gate | model/outcome-free census: k4 and k5-seed-0 maximal rendering byte totals for all 19,926 candidates, sharded fragments + fail-closed reducer; publishes eligible keys, pilot intersections (expected 15), module-disjoint post-pilot population (must be ≥ 200) |
| `finalize_v2b_nll_confirmation_sample.py` | sampler | the only draw entry point; consumes the reduced gate, re-verifies every key-set relation, delegates to frozen `build_sample_plan`; requires exact N=200, module disjointness, pre-score cluster gate (≥20 modules, eff. clusters ≥10) |
| `prepare_v2b_nll_confirmation_assembly.py` | assembly | six cells `k1, k3:16384, k4:16384, k5:{0,1,2}:16384`; manifest binds hashes only (contexts re-materialized + verified at scoring); k1/k4/k5:0 must be eligible for all 200; ineligible diagnostic cells carry explicit None contexts |
| `v2b_nll_confirmation_crypto.py` | cryptography | 32-byte salt, SHA256 commitment, HMAC `fam-` ids, 8-byte XOR fixed-width ciphertexts, +0.0 padding; 0600/no-symlink salt file discipline |
| `v2b_nll_confirmation_stats.py` | statistics | module-MoM inference reusing `v2b_n_governance.variance_components`; frozen t-tables df 1..199; Holm |
| `prepare_v2b_nll_confirmation_salt.py` | salt-commitment | private salt outside Git + private receipt + public commitment artifact; crash retry reuses the salt, never rerolls |
| `v2b_nll_confirmation_battery.py` | model-battery | per-model pre-score gate: tokenizes every eligible registered prompt with that model's tokenizer (any overflow aborts the study — the all-model tokenizer-fit gate), then a target-free synthetic production-kernel probe (repeat determinism, causal masking, throughput, peak memory); emits `recommended_shard_count`; write-once |
| `eval_v2b_nll_confirmation.py` | scorer-reducers | `score` (target-atomic 0600 write-once, deterministic resume, one model + shard per job; requires salt-commitment adoption commit to be an ancestor of the scoring commit), `reduce-model` (exact 200-file union, value-free), `reduce-study` (exact four completions, one identical cohort); no reducer logs any value |
| `prepare_v2b_nll_confirmation_masked.py` | masker | consumes study reducer + all 800 committed target artifacts, computes the five contrasts, emits exactly 4×5×200 ciphertexts; public rows carry no eligibility/contrast info |
| `finalize_v2b_nll_confirmation_fixed_n.py` | fixed-n-gate | blind execution-completeness gate: replays every ciphertext from raw scores with the private salt but publishes no value/family/eligibility |
| `finalize_v2b_nll_confirmation_reveal.py` | reveal | registered one-shot reveal: validates committed masked+fixed-N, replays the mask, publishes salt, maps families, decrypts all 4,000 payloads; padding filtered only via the committed assembly eligibility ledger |
| `analyze_v2b_nll_confirmation.py` | analyzer | frozen decision-label precedence, gated Holm secondary family, E1b descriptive assay, k5 seed sensitivity, static-reference coverage bins; committed (and freeze-bound) before any score |
| `slurm/v2b_nll_confirmation_{gate,prepare,battery,score,reduce,mask,fixed_n}.sbatch` | slurm | thin env-var wrappers; CPU stages on `mit_normal`, battery/score on `mit_normal_gpu` + `gpu:l40s:1`; score has `--requeue` |
| `slurm/v2b_nll_confirmation_reveal.sbatch` | slurm | **was missing from the WIP; added on this branch** (`0d82da3`), style-matched, passes the full chain incl. `--fixed-n`/`--implementation-freeze` |
| `slurm/v2b_nll_confirmation_analysis.sbatch` | slurm | **was missing; added** (`0d82da3`); deliberately never receives the private salt |
| `tests/test_v2b_nll_confirmation_jobs.py` | test | **was missing; added** (`b0a6b3f`): binds the on-disk sbatch set to the freeze closure, partition split, 06:00:00 walltime, four-model battery array, mode dispatch, and the salt boundary |
| 14 other `tests/test_*confirmation*.py` | test | synthetic, GPU-free; all pass |

The salvage commit's five small deltas wired `validate_live_freeze` into
gate/sample/assembly/salt/battery — i.e. the WIP's last act was closing the
"stage runs on a tree whose files drift from the freeze" hole; the newly
added files complete the closure that this validator (and the freeze
producer itself) requires to exist.

**Broken pieces found: none besides the three missing closure files.** All
15 modules import; all 11 CLIs parse; the committed protocol validates; the
freeze producer dry-runs to a scratch path (not committed — the real freeze
must be created once, post-adoption, on the canonical branch).

**Present only on the cluster checkout** (hash-bound by the protocol,
never git-tracked): `results_v2/v2b/candidates/job19982184_3_sympy.json`,
`results_v2/v2a/job19915852_0_sympy/extraction.json`,
`results_v2/v2b/k7/job19921318_3_sympy.json`,
`results_v2/v2b/neardup/job19929883_3_sympy.json`, and the `corpora/sympy`
checkout at the pinned sha. The gate/assembly stages therefore only run
there. The pilot sample `results_v2/v2b/sample/job19989076_sample.json` and
A6 outcome ARE committed.

## 4. Test state

Baseline after the clean merge of `bb94c0f` (before any fix): the five
commanded test files passed — `.venv/bin/python -m pytest
tests/test_analyze_v2b_nll_confirmation.py
tests/test_finalize_v2b_nll_confirmation_fixed_n.py
tests/test_finalize_v2b_nll_confirmation_reveal.py
tests/test_freeze_v2b_nll_confirmation.py
tests/test_prepare_v2b_nll_confirmation_masked.py -x` → **32 passed**;
full suite → **629 passed, 0 failed, 0 skipped**. The WIP's breakage was
not in the tests but in the freeze closure (three declared files absent —
the freeze producer, and every stage's `validate_live_freeze`, would have
refused to run).

After completing the closure: five files → **32 passed**; full suite →
**638 passed, 0 failed, 0 skipped** (the +9 are the new jobs contract
test). Note: the local repo venv had no pytest; `pytest 9.1.1` was
installed into `.venv` offline from the local uv cache (no network).

## 5. Launch sequence (every step DO-NOT-RUN; the conductor gates all of it)

Conventions: run from the frozen cluster checkout root; `$PY=.venv/bin/python`;
proposed artifact home `results_v2/v2b/nll_confirmation/` (any path under
`results_v2/` satisfies the evidence-path guard); commit boundaries are
mandatory — every stage refuses uncommitted required predecessors, and
evidence-only commits do not move the source tree hash, so the freeze
survives them.

**Step 0 — DO-NOT-RUN (governance, local).** Adopt before anything
executes: merge this branch to the execution branch; obtain the
independent fresh-context adversarial review of the protocol + this
implementation (the repo's FIX-FIRST → ADOPTABLE pattern); write the
PREREG §13 ADOPTED entry (see §6) hash-binding the protocol
(`06c179e0...`) and the review; commit. This adoption commit is the
ancestor every later artifact must descend from.

**Step 1 — DO-NOT-RUN (freeze; CPU, login node or local, cluster checkout).**
```
$PY freeze_v2b_nll_confirmation.py \
  --out results_v2/v2b/NLL_E2_CONFIRMATION_IMPLEMENTATION_FREEZE.json
git add results_v2/v2b/NLL_E2_CONFIRMATION_IMPLEMENTATION_FREEZE.json && git commit  # ONE-touch: no later commit may ever touch this file
export V2B_CONFIRM_FREEZE=$PWD/results_v2/v2b/NLL_E2_CONFIRMATION_IMPLEMENTATION_FREEZE.json
```

**Step 2 — DO-NOT-RUN (source census; CPU array).** Conductor picks the
shard count (env `V2B_CONFIRM_GATE_SHARD_COUNT`, e.g. 16).
```
export V2B_CONFIRM_GATE_SHARD_COUNT=16
export V2B_CONFIRM_GATE_FRAGMENT_DIR=$PWD/results_v2/v2b/nll_confirmation/source_gate_fragments
export V2B_CONFIRM_GATE=$PWD/results_v2/v2b/nll_confirmation/source_gate.json
sbatch --array=0-15 --export=ALL,V2B_CONFIRM_GATE_MODE=fragment slurm/v2b_nll_confirmation_gate.sbatch
# after ALL fragments succeed:
sbatch --export=ALL,V2B_CONFIRM_GATE_MODE=reduce slurm/v2b_nll_confirmation_gate.sbatch
git add results_v2/v2b/nll_confirmation/ && git commit   # reduced gate (+ fragments) as evidence
```
Abort-before-sample if the module-disjoint eligible population < 200.

**Step 3 — DO-NOT-RUN (sample draw; CPU).**
```
export V2B_CONFIRM_CANDIDATES=$PWD/results_v2/v2b/candidates/job19982184_3_sympy.json
export V2B_CONFIRM_PILOT_SAMPLE=$PWD/results_v2/v2b/sample/job19989076_sample.json
export V2B_CONFIRM_SAMPLE_OUT=$PWD/results_v2/v2b/nll_confirmation/sample.json
sbatch --export=ALL,V2B_CONFIRM_PREPARE_MODE=sample slurm/v2b_nll_confirmation_prepare.sbatch
git add results_v2/v2b/nll_confirmation/sample.json && git commit
export V2B_CONFIRM_SAMPLE=$V2B_CONFIRM_SAMPLE_OUT
```
Abort (no redraw, no replacement) on any shortfall or cluster-gate failure.

**Step 4 — DO-NOT-RUN (six-cell assembly; CPU).**
```
export V2B_CONFIRM_ASSEMBLY_OUT=$PWD/results_v2/v2b/nll_confirmation/assembly.json
sbatch --export=ALL,V2B_CONFIRM_PREPARE_MODE=assembly slurm/v2b_nll_confirmation_prepare.sbatch
git add results_v2/v2b/nll_confirmation/assembly.json && git commit
export V2B_CONFIRM_ASSEMBLY=$V2B_CONFIRM_ASSEMBLY_OUT
```

**Step 5 — DO-NOT-RUN (salt + public commitment; CPU).** Private salt and
receipt live OUTSIDE the repo, mode 0600, never tracked; only the public
commitment is committed. Its adoption commit is the ancestry anchor the
scorer enforces.
```
export V2B_CONFIRM_PRIVATE_SALT=$HOME/v2b_confirmation_private/salt.bin
export V2B_CONFIRM_PRIVATE_RECEIPT=$HOME/v2b_confirmation_private/receipt.json
export V2B_CONFIRM_SALT_OUT=$PWD/results_v2/v2b/nll_confirmation/salt_commitment.json
sbatch --export=ALL,V2B_CONFIRM_PREPARE_MODE=salt slurm/v2b_nll_confirmation_prepare.sbatch
git add results_v2/v2b/nll_confirmation/salt_commitment.json && git commit
export V2B_CONFIRM_SALT_COMMITMENT=$V2B_CONFIRM_SALT_OUT
```

**Step 6 — DO-NOT-RUN (four instrument batteries; GPU L40S, array 0-3).**
All four must pass before ANY score; any failure = no scoring, no model
substitution. This step is also the all-model tokenizer-fit gate (any
overflow aborts the sealed study).
```
export V2B_CONFIRM_BATTERY_DIR=$PWD/results_v2/v2b/nll_confirmation/battery
sbatch --array=0-3 slurm/v2b_nll_confirmation_battery.sbatch
git add results_v2/v2b/nll_confirmation/battery/ && git commit
export V2B_CONFIRM_BATTERY_0=$V2B_CONFIRM_BATTERY_DIR/q25c-0.5b.json
export V2B_CONFIRM_BATTERY_1=$V2B_CONFIRM_BATTERY_DIR/q25c-1.5b.json
export V2B_CONFIRM_BATTERY_2=$V2B_CONFIRM_BATTERY_DIR/q25c-3b.json
export V2B_CONFIRM_BATTERY_3=$V2B_CONFIRM_BATTERY_DIR/q25c-7b.json
```

**Step 7 — DO-NOT-RUN (scoring; GPU L40S; one submission per model).**
Read `sharding.recommended_shard_count` (call it S_m) from EACH committed
battery artifact; submit each model once as `--array=0-(S_m-1)`. Slurm may
requeue a preempted task (write-once + deterministic resume make that
safe); do not re-submit a model's array fresh.
```
for M in q25c-0.5b q25c-1.5b q25c-3b q25c-7b:   # conductor expands manually
  export V2B_CONFIRM_MODEL_ID=$M
  export V2B_CONFIRM_TARGET_DIR=$PWD/results_v2/v2b/nll_confirmation/targets/$M
  sbatch --array=0-$((S_m-1)) slurm/v2b_nll_confirmation_score.sbatch
```

**Step 8 — DO-NOT-RUN (reducers; CPU).** After each model's 200 target
files exist, reduce it; then commit ALL score evidence (800 target files +
4 model completions), then reduce the study and commit it.
```
# per model M:
export V2B_CONFIRM_REDUCE_MODE=model V2B_CONFIRM_MODEL_ID=$M
export V2B_CONFIRM_TARGET_DIR=$PWD/results_v2/v2b/nll_confirmation/targets/$M
export V2B_CONFIRM_MODEL_COMPLETE_OUT=$PWD/results_v2/v2b/nll_confirmation/model_complete/$M.json
sbatch slurm/v2b_nll_confirmation_reduce.sbatch
# then:
git add results_v2/v2b/nll_confirmation/targets results_v2/v2b/nll_confirmation/model_complete && git commit
export V2B_CONFIRM_MODEL_COMPLETE_0=.../model_complete/q25c-0.5b.json   # ... 1,2,3 in model order
export V2B_CONFIRM_STUDY_COMPLETE_OUT=$PWD/results_v2/v2b/nll_confirmation/study_complete.json
sbatch --export=ALL,V2B_CONFIRM_REDUCE_MODE=study slurm/v2b_nll_confirmation_reduce.sbatch
git add results_v2/v2b/nll_confirmation/study_complete.json && git commit
export V2B_CONFIRM_STUDY_COMPLETE=$V2B_CONFIRM_STUDY_COMPLETE_OUT
```

**Step 9 — DO-NOT-RUN (mask; CPU).**
```
export V2B_CONFIRM_MASKED_OUT=$PWD/results_v2/v2b/nll_confirmation/masked.json
sbatch slurm/v2b_nll_confirmation_mask.sbatch
git add results_v2/v2b/nll_confirmation/masked.json && git commit
export V2B_CONFIRM_MASKED=$V2B_CONFIRM_MASKED_OUT
```

**Step 10 — DO-NOT-RUN (blind fixed-N gate; CPU).** Value-free
execution-completeness verdict; any gap = the whole confirmation is
`execution-incomplete-not-analyzed` (no partial N, no redraw, no reroll).
```
export V2B_CONFIRM_FIXED_N_OUT=$PWD/results_v2/v2b/nll_confirmation/fixed_n_gate.json
sbatch slurm/v2b_nll_confirmation_fixed_n.sbatch
git add results_v2/v2b/nll_confirmation/fixed_n_gate.json && git commit
export V2B_CONFIRM_FIXED_N=$V2B_CONFIRM_FIXED_N_OUT
```

**Step 11 — DO-NOT-RUN (registered one-shot reveal; CPU).** Only after
masked + fixed-N are exact committed HEAD blobs. Exactly one reveal.
```
export V2B_CONFIRM_REVEAL_OUT=$PWD/results_v2/v2b/nll_confirmation/reveal.json
sbatch slurm/v2b_nll_confirmation_reveal.sbatch
git add results_v2/v2b/nll_confirmation/reveal.json && git commit
export V2B_CONFIRM_REVEAL=$V2B_CONFIRM_REVEAL_OUT
```

**Step 12 — DO-NOT-RUN (registered analysis; CPU).** The analyzer wrapper
never receives the private salt.
```
export V2B_CONFIRM_ANALYSIS_OUT=$PWD/results_v2/v2b/nll_confirmation/analysis.json
sbatch slurm/v2b_nll_confirmation_analysis.sbatch
git add results_v2/v2b/nll_confirmation/analysis.json && git commit
```
Results are read ONLY through this committed artifact; the PREREG §13
boundary entry for the outcome cites it and its decision label.

Preconditions the conductor must verify before Step 2 (all cluster-side):
`corpora/sympy` at `c0a595d7...`; the four hash-bound uncommitted inputs
present with exact sha256s; the four model snapshots cached at the pinned
revisions; `requirements-cluster.lock` + `results_v2/env/freeze-cluster.txt`
matching the venv (the scorer enforces `env_matches_lock`).

## 6. Pre-outcome amendment still required (per the repo's amendment discipline)

The protocol JSON is committed and self-consistent, but **no PREREG §13
entry adopts it** — PREREG does not mention this confirmation at all, and
every prior route (exploratory reveal, ladder, V2-c, epoch-2, …) executed
only after an ADOPTED disagreement-log entry backed by an independent
fresh-context adversarial review. Before Step 1 (and therefore before any
census/sample/score), the campaign needs one **pre-outcome adoption
amendment** that:

1. records the adoption boundary honestly: protocol frozen 2026-08-09 at
   sealed parent `d70f335`; NO confirmation artifact of any kind exists;
   lists the evidence that has since become public and is disclosed as
   known at adoption — the five-tier exploratory ladder reveals, the
   dose/interior/supplement artifacts, and the V2-c mathlib4+sympy reveal
   (`results_v2/v2c/V2C_REVEAL.json`) — none of which touches the
   confirmation population's scores (the protocol's `adoption_basis`
   predates V2-c and lists only the pilot; the §13 entry must close that
   gap);
2. hash-binds `NLL_E2_CONFIRMATION_PROTOCOL.json` (raw `06c179e0...`,
   semantic `2faacb2a...`) and the implementation tree (the 49-file freeze
   closure) at the adoption commit;
3. records the independent adversarial review verdict (FIX-FIRST →
   ADOPTABLE pattern) of protocol + implementation, including the three
   files completed on this branch after the salvage;
4. states the claim status the analysis will carry (the protocol's
   labels; confirmation of a hypothesis selected from revealed pilot
   outcomes, fresh module-disjoint cohort, never pooled with the pilot).

Open questions that exceed the WIP's own intent (recorded here, NOT
decided; for the adoption review):

- **Protocol staleness vs. baked hashes.** The V2-c reveal postdates the
  frozen protocol. If the reviewer wants the wider information environment
  disclosed INSIDE the protocol JSON (not just in the §13 entry), the
  protocol's raw/semantic hashes baked into `v2b_nll_confirmation.py` (and
  transitively every schema/test) must be regenerated — a code change the
  WIP clearly did not intend. The WIP-consistent path is adoption-as-is
  with the disclosure in the §13 entry.
- **7B secondary vs. the ladder's 7B reveal.** The exploratory ladder
  revealed 7B (and 14B) pilot results after this protocol froze its
  "key-generalization" role for 7B. Whether that widened information
  environment changes how the 7B gated-secondary should be labeled is a
  review question; the mechanics (Holm within the five-endpoint family)
  are unaffected.
- **Raw target scores enter git pre-reveal** (the masker requires all 800
  mode-0600 target artifacts committed). The blind is procedural (no
  inspection; masking/fixed-N never read values), matching the pilot's
  practice — the reviewer should confirm this is acceptable for a study
  labeled confirmation, or require the target files be committed only
  by-hash (a design change the WIP did not make).
- **Walltime for the 7B battery/score legs** is pinned at 06:00:00 by the
  freeze's execution policy; if the dry benchmark recommends shard counts
  that cannot fit, the remedy is more shards (frozen mechanism), never a
  walltime edit after the freeze.

## 7. Branch state

- Branch: `worktree-agent-acf49e50b8791e2e3`; head `b0a6b3f`.
- `4690ff2` merge of `origin/codex/nll-confirmation` (`bb94c0f`, clean);
- `0d82da3` reveal + analysis sbatch wrappers (freeze closure);
- `b0a6b3f` static Slurm-jobs contract test (freeze closure).
- Tests: 638 passed, 0 failed (five commanded files: 32 passed with `-x`).
- Untouched, per instructions: `eval_incontext.py`, `layout.py`,
  `analyze_v2.py`, `requirements-cluster.lock`, everything under
  `results_v2/`, `PREREG.md`, `DESIGN_V2.md`, and the ARM_CS/CS lane.
