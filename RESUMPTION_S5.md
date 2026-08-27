# RESUMPTION_S5 — behavioral-arm (S5) state map and execution plan

Date: 2026-08-27. Status: **engineering state map + plan; not preregistered;
reads no model score and reveals nothing.** PREREG.md and DESIGN_V2.md remain
the source of truth; where this document proposes changing a frozen rule it
does so as a PROPOSED AMENDMENT, never by editing the frozen text.

Boundary at resumption: the NLL side is complete through the V2-c confirmatory
reveal (752b40a, 2026-08-12). The behavioral side (DESIGN_V2 §5, §14.15,
§14.22–§14.25, §15.A17–§15.A21) has never generated a token, verified a body,
or produced an outcome. The exploratory NLL ladder amendment
(`results_v2/v2b/NLL_LADDER_EXPLORATORY_AMENDMENT.md`) and the V2-c reveal
publicly unmasked per-target NLL results for the pilot, supplement, and V2-c
draws, so the behavioral arm MUST run on a fresh target sample (§5 below).

## 1. What exists and its verified state

### 1.1 §15.A21 audited envelope (main, commit 6109346) — "stack A"

| file | role | state |
|---|---|---|
| `v2b_behavior_verify.py` | S5 manifest/certificate/transcript contract (`v2b_lean_verify_manifest_v3`), truth tables, baseline certificate derivation | committed, audited |
| `run_v2b_lean_verify.py` | production execution envelope: GO-nonce handshake, durable journal, bubblewrap sandbox, content-addressed immutable attempt bundles, structurally blocked outcome-selective retries | committed, audited |
| `lean_drivers/V2BVerifyCommand.lean` | two-process driver (fresh baseline process; fresh candidate process that elaborates the generated body and then the original suffix) | committed, audited |
| `tests/test_run_v2b_lean_verify.py`, `tests/test_v2b_behavior_verify.py` | truth-table + envelope tests; real-driver integration legs | committed |

Audit verdict was COMMITTABLE-AS-IS with three recorded TODOs:
**complete-artifact producer, corpus integration + S5 launcher, first
real-bwrap cluster smoke** — none of which existed at the pause.

**Instrument caveat discovered after landing (recorded in the salvaged WIP):**
stack A's candidate process holds the immutable original suffix in its
`FileMap`/reconstructed file while elaborating generated syntax. A generated
metaprogram (`Lean.getFileMap`, `IO.FS`, spawned children) can therefore read
the held-out original body region's downstream consequences (e.g. a suffix
theorem `target = 0`) during verification — an oracle channel. Stack A is
audited as an *envelope*, but as an *instrument* it is superseded by stack B
below. This supersession is an engineering finding, not yet a governance act
(§7, proposed amendment P2).

### 1.2 Salvaged four-phase WIP (merged here from `origin/codex/s5-four-phase-runner`, tip 2cb69b5) — "stack B"

Lineage: forked from 0cbe6e3 (the commit *before* 6109346), i.e. written
beside, not on top of, the audited envelope. Merged into this branch
unmodified (merge commit preserves the verbatim salvage).

| file | role | state |
|---|---|---|
| `S5_ORACLE_SAFE_ARCHITECTURE.md` | the oracle audit finding, the four-phase design, §7.2 gap list, release gates | prospective note; explicitly **not preregistered** |
| `v2b_lean_frames.py` | framed-stdin source channel + per-phase views (byte-preserving body masking) | complete, tested (fixture tests run everywhere) |
| `v2b_s5_visibility.py` | exact-file child visibility projection (`v2b_s5_visibility_v1`), import-closure join (`v2b_s5_import_closure_v1`) | complete, tested (fixture tests run everywhere) |
| `lean_drivers/V2BOracleSafeProbe.lean` | four-phase driver: baseline-target / baseline-suffix / candidate-target / candidate-suffix, canonical kernel bundle transport, `Lean.addDecl` replay | compiles/tested only under the pinned toolchains |
| `lean_drivers/V2BS5ExpandSetup.lean` + fixtures | transitive ModuleSetup expansion helper (`setupEditedModule` path) | integration-tested only under pinned toolchains |
| `run_v2b_s5_four_phase.py` | per-invocation execution envelope: hash-only plan, phase manifests, GO-nonce + durable journal per phase, attempt immutability, summary derivation + full byte revalidation | complete for one invocation; see gaps |
| `tests/test_run_v2b_s5_four_phase.py` | adversarial oracle/tamper/kill tests | **all 8 tests are gated on elan toolchains `v4.32.0` and `v4.33.0-rc2` and SILENTLY PASS AS SKIPS where absent** |

Verified on this branch (2026-08-27, macOS, python 3.12/3.14): the full repo
suite — 565 tests — passes. Honest reading of that number for S5: 28 tests
print `[skip]` because the pinned toolchains are not installed (this host has
only `v4.33.0`). In particular **the four-phase runner had zero executable
coverage on a host without the pinned toolchains** before this branch;
coverage that actually executed was the visibility/frames fixture tests and
stack A's pure truth-table tests. This branch adds a protocol-faithful stub
driver (§6) so the entire host-side envelope executes everywhere.

Rough edges the WIP itself records (`S5_ORACLE_SAFE_ARCHITECTURE.md` §7.2),
all confirmed by reading the code:

1. baseline phases are re-run inside every candidate-bearing invocation
   (no arm-independent one-shot baseline certificate artifact yet);
2. plan offsets (`targetStartByte`/`headerEndByte`/retained ends) are
   source-byte-proven but not yet hash-joined to the frozen S4
   extraction/boundary artifacts;
3. host target-type gate is exact canonical equality, not the frozen
   `Kernel.isDefEq` certificate path (conservative, narrower than §15.A21);
4. no canonical pre-target environment digest is emitted;
5. no separately audited recovery adjudicator for a partial post-GO attempt
   (it fails closed, which is safe but requeue-manual);
6. pathname-swap window between pre/post hash checks (content-addressed
   staging still to do);
7. Linux ELF loader closure + real Engaging bubblewrap probes +
   ModuleSetup options/plugins application under both pins = release gates.

### 1.3 Downstream consumers already frozen and waiting

`v2b_behavioral_governance.py` (§15.A17) and `v2b_behavior_tier.py` (§15.A18)
are committed and validate masked artifacts whose bindings REQUIRE a
`v2b_behavior_verified_complete_v1` evidence artifact, a
`v2b_behavior_plan_v1`, and a `v2b_behavior_baseline_coverage_v1` — none of
which had producers. The behavioral salt commitment
(`results_v2/v2b/behavior_salt_commitment_v1.json`) is committed and REMAINS
SEALED; §15.A15's production unblinder stays mechanically disabled until the
generator/verifier/governance chain is real (anti-forgery gate).

## 2. The three TODOs, honestly scored (before this branch)

| TODO (6109346) | state found | state after this branch |
|---|---|---|
| complete-artifact producer | absent; only the schema string existed | `v2b_s5_complete.py`: file-based, duplicate-key-rejecting producer that re-materializes the per-target/arm/draw table from revalidated four-phase run evidence; fail-closed harness accounting (§7 P3); tested |
| corpus integration + S5 launcher | absent; visibility producer existed but nothing joined corpus artifacts → plans → runs → evidence | `run_v2b_s5_launcher.py`: launch-spec + generation-table joins, per-target visibility production, per-(arm,draw) four-phase execution, complete-artifact production; `--dry-run` seam stubs the model call and the Lean toolchain; tested end-to-end on a toy target |
| first real-bwrap cluster smoke | not run; Engaging bubblewrap untested | still not run — requires the cluster; plan in §6, commands marked DO-NOT-RUN |

Still genuinely missing after this branch (unchanged claims, no code written
for them here): the import-closure artifact *producer* (the projection
consumes `v2b_s5_import_closure_v1` but nothing derives it from the frozen
dependency graph — deriving it from the same untrusted setup would defeat the
check); the S4→plan hash join (gap 2 above); the one-shot baseline
certificate artifact (gap 1); real generation (GPU) and S4 extraction
producers; the masking producer and baseline-coverage producer of §15.A17;
the four-phase estimand amendment (§7 P2); and the fresh-sample amendment
(§7 P1).

## 3. Which stack runs

Recommendation (engineering, pending the P2 governance decision): build on
**stack B**. Stack A's envelope discipline survives in B (same GO-nonce,
journal, immutability, classification vocabulary); B closes A's oracle
channel and is what the salvaged campaign was actively building. The pieces
added by this branch (launcher/producer) sit ABOVE `run_four_phase` and bind
its contract hashes, so a P2 rejection (falling back to stack A) would cost
the thin orchestration layer only, not the evidence design.

## 4. What this branch adds (deliverables B and C)

- Merge of the salvaged WIP, verbatim (`git log` shows 2cb69b5 unmodified).
- `v2b_s5_dryrun.py` — explicitly NON-EVIDENCE dry-run seams: a
  protocol-faithful stub Lean driver (speaks the exact frame/nonce/GO/marker
  protocol of `V2BOracleSafeProbe.lean`), a toy corpus/workspace builder, and
  a deterministic stub generation table ("the model call"). The stub
  toolchain is consumed through the runner's existing `none-test-only`
  backend, which is opt-in at the Python API and never oracle-isolation
  evidence.
- `v2b_s5_complete.py` — the complete-artifact producer
  (`v2b_behavior_verified_complete_v1`), execution-mode-labeled
  (`production-bubblewrap` vs `dry-run-stub-not-evidence`); production mode
  additionally requires a clean tracked source tree and refuses stub
  evidence.
- `run_v2b_s5_launcher.py` — launch spec (`v2b_s5_launch_spec_v1`),
  generation table (`v2b_s5_generation_table_v1`), corpus-integration join
  (visibility → plan → four-phase run per (target, arm, draw)), then the
  complete producer. `--dry-run` builds/uses stub seams; production mode
  requires canonical `/usr/bin/bwrap` and refuses everywhere else.
- Toolchain-free tests for all of the above, including a full four-phase
  end-to-end execution of `run_v2b_s5_four_phase.run_four_phase` under the
  stub driver (pass, ordinary-zero, baseline-ineligible, type-drift,
  tamper/fail-closed, evidence reuse).

Local end-to-end demo (safe, no cluster, no GPU, no Lean toolchain):

    .venv/bin/python run_v2b_s5_launcher.py --dry-run-demo <scratch-dir>

## 5. Fresh-sample requirement and how to draw it

What is now public (blind destroyed): per-target NLL outcomes for the pilot
sample `job19989076` (20 identities × 5 corpora, revealed at FIVE model tiers
by the ladder amendment), the mathlib4 dose/supplement draw (120 identities),
and the V2-c confirmatory draw (52 mathlib4 + 77 sympy). §15.A17's masked
behavioral table was specified against "the committed 20-target sample";
running the behavioral pilot on those identities now would let arm-outcome
expectations form against known NLL results. Therefore:

- The behavioral pilot draws a FRESH sample. Machinery that can be reused
  unchanged: the sealed `v2b_candidates_v2` tables (their SHA256s are inside
  the committed sample artifacts; the bytes live on POOL), the §15.A1
  stratification and `build_sample_plan`'s exclude-and-select mechanism —
  exactly how `v2b_n_governance.py` already projects V2-c plans with the
  pilot excluded — and `finalize_v2b_sample.py`'s binding/sealing pattern.
- Two things CANNOT be reused as-is and need a small new gated entry point
  (`finalize_v2b_behavior_sample.py`, to be written under the P1 amendment):
  (a) the priority key: `SAMPLING_SEED = "v2a:20260808"` priorities are
  frozen INTO the candidate tables and are now partially outcome-adjacent
  (the next-by-priority identities are predictable from public bytes), so
  the fresh draw ranks by a NEW frozen seed domain (proposed:
  `"v2bbehavior-sample:<adoption-date>"`) recomputed from the identity
  fields, adopted in P1 BEFORE any draw; (b) `exclude_keys` = every identity
  in `results_v2/v2b/sample/job19989076_sample.json`,
  `results_v2/v2b/sample/supplement_sample.json`, and
  `results_v2/v2c/v2c_sample.json` (the P1 amendment enumerates these files
  by hash).
- The behavioral salt/commitment is untouched and stays sealed; the P1
  amendment must carry the ladder amendment's sequencing-disclosure
  obligation (list every unmasked NLL artifact existing at adoption).
- §15.A17/§15.A18 texts bind "the committed 20-target sample/eligibility
  table"; P1 rebinds those clauses to the fresh sample artifact by hash,
  changing no estimator, threshold, or edge rule.

## 6. First cluster smoke — ordered plan (ALL COMMANDS DO-NOT-RUN; conductor gates cluster submission)

Goal: one target, q25c-1.5b generation, real bubblewrap, both pinned
toolchains exercised, zero scientific claims (smoke evidence is labeled
non-scientific; it precedes P1/P2 adoption and uses a THROWAWAY target that
the fresh draw will exclude — record its identity in the P1 amendment).

1. **Preflight (login node, CPU):** verify pinned toolchains + bwrap.
   DO-NOT-RUN: `elan toolchain list && /usr/bin/bwrap --version`
2. **Lean-side integration tests under both pins (compute node, CPU):**
   DO-NOT-RUN: `.venv/bin/python -m pytest tests/test_v2b_oracle_safe_probe.py tests/test_v2b_s5_expand_setup.py tests/test_run_v2b_s5_four_phase.py -q`
   (these stop skipping once the pinned toolchains exist; they must PASS,
   not skip — assert `[skip]` count is zero in the job log).
3. **Corpus artifacts for one module (CPU):** broad setup index row for the
   chosen module must already exist from the §15.A20 corpus audit chain
   (`prepare_v2b_lean_setups.py`); expand the ModuleSetup via the pinned
   native helper; produce the import-closure artifact (BLOCKED on the
   missing closure producer — for the smoke, a hand-derived closure is
   acceptable ONLY if labeled non-evidence in the run directory).
   DO-NOT-RUN: `.venv/bin/python v2b_s5_visibility.py --module <M> --source <F> --workspace <corpus> --toolchain <root> --helper <v2bS5ExpandSetup> --setup <expanded.json> --closure <closure.json> --index <broad-index.json> --runtime <lean> <libleanshared...> --out visibility.json`
4. **Stub-path rehearsal ON the cluster (CPU, minutes):** the same dry-run
   demo as §4 runs on a compute node first; it proves the envelope,
   journaling, and producer on cluster filesystems before any GPU minute is
   spent. DO-NOT-RUN: `.venv/bin/python run_v2b_s5_launcher.py --dry-run-demo $SCRATCH/s5-smoke-dry`
5. **Generation (GPU, one L40S, ~minutes):** n=2 seeds, arms {k1,k4} only,
   temperature 0.8/top-p 0.95/512 tokens/no stop sequences (§14.15(b)),
   1.5B pinned revision; writes body files + a `v2b_s5_generation_table_v1`.
   (Generator entry point is part of the still-missing S2 producer; the
   smoke may use a minimal script that records model/revision/seeds/params
   into the table — labeled non-evidence.)
6. **S4 extraction** for the generated continuations via the §15.A20 driver
   chain (exists: `v2b_lean_boundaries.py` / `V2BParseCommand.lean` path);
   retained ends feed the launch spec.
7. **Four-phase S5 under real bubblewrap (CPU):**
   DO-NOT-RUN: `.venv/bin/python run_v2b_s5_launcher.py --launch-spec spec.json --generation-table table.json --run-root $SCRATCH/s5-smoke --out complete.json`
   (production mode: canonical `/usr/bin/bwrap`, clean tree, live hash
   revalidation before and after every phase).
8. **Adversarial probes on the cluster** (from the architecture doc's release
   gates): FileMap canary, logical-path read, corpus walk, `/proc` absence,
   inherited-stdout forgery, kill/restart mid-phase → re-entry revalidation.
   These are the §7.2/§8 release-gate probes, run as tests, not as science.
9. **Report:** smoke report committed (hashes of every artifact; the
   `[skip]`-free test logs; bwrap version; kernel), then P1/P2 amendments go
   to review before ANY scored generation.

Failure handling: any harness-invalid or pre-GO exhaustion in step 7 is an
infrastructure finding — fix, new run root, document; never edit evidence in
place (immutability is enforced by the envelope).

## 7. Proposed amendments (NOT adopted by this document)

- **P1 — fresh behavioral sample.** New seed domain + exclusion set +
  rebinding of §15.A17/§15.A18's "committed 20-target sample" clauses, with
  the full sequencing disclosure (§5). Adopt after independent review,
  before any behavioral draw.
- **P2 — oracle-safe estimand.** Fold `S5_ORACLE_SAFE_ARCHITECTURE.md` §2
  into DESIGN_V2 §15 (name the oracle-safe kernel-body/normalized-
  continuation estimand; make baseline replay feasibility an arm-independent
  pre-generation screen; report feasibility attrition; forbid describing the
  result as literal frontend continuation), reconciling §15.A21's
  FULL-FILE-CONSEQUENCE wording with the four-phase suffix replay. Adopt
  before any generation; until then stack B remains engineering.
- **P3 — evidence-invalid candidate accounting.** 6109346 carried this
  forward verbatim: "count-as-zero or reported exclusion category" must be
  prespecified before any behavioral score. The producer built here
  implements the conservative third option — REFUSE to finalize while any
  cell is unresolved/harness-invalid — which makes the P3 choice impossible
  to dodge silently; P3 should either ratify refuse-and-requeue or pick a
  reporting category, before any scored run.
- **P4 — import-closure provenance.** Specify the closure-artifact producer
  (joined to the frozen dependency graph, not derived from the untrusted
  setup) and add it to the §15.A20/§15.A21 evidence chain.

## 8. Verification state on this branch

- Full suite: 565 passed before the new work; all new tests pass with it
  (see final commit messages for exact counts).
- Silent-skip inventory (this host): 28 `[skip]` lines across the S5 chain,
  all for missing pinned elan toolchains — eliminated as *coverage* holes by
  the stub-driver tests, but the real-Lean legs still REQUIRE a
  pinned-toolchain host (cluster smoke, §6 step 2).
- Nothing under `results_v2/`, no governance-frozen file
  (`eval_incontext.py`, `layout.py`, `analyze_v2.py`,
  `requirements-cluster.lock`), and no frozen DESIGN/PREREG text was
  modified on this branch.
