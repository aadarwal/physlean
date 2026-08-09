# DIRECT_SCALING_STUDY — testing the context-position proxy for code predictability

Status: **PROSPECTIVE DESIGN, NOT FROZEN.** Nothing here has been run, no
artifact exists, and no constant is preregistered until P0. Revision 5, after
fourth root review. Separate from `DESIGN_V2.md`/`PREREG.md`; amends neither.

Motivating source: Gwern, ["Lean Software Scaling
Laws"](https://gwern.net/lean-scaling) (June 2026). Quoted phrases are from
that essay.

---

## 0. Relationship to V2-b

V2-b measures **repository-context sufficiency on fixed targets**: within-target
contrasts at three byte budgets, each target its own control. Its naming fork
records that it "does NOT manipulate codebase scale or growth" and that results
are "never to be presented as a 'software/codebase scaling law'"
(`DESIGN_V2.md:11`); `PREREG.md:20` bars per-language scaling-law claims.

That scope is correct for V2-b. It does mean V2-b cannot produce the essay's
proposed per-language curve or crossover. This study tests the essay's **cheap
context-position proxy** directly and then asks whether it survives a stronger
fixed-target control. It does not silently turn visible context into physical
repository size. A genuine codebase-growth effect remains an observational
question in Arm B.

The two share corpora, harness, and evidence discipline, and share **no
estimand and no claims**. Neither is evidence for the other.

The failed-range/density results of the earlier G3 sweep informed this design.
Therefore a run on the same bytes/checkpoints is a **prospective exploratory
follow-up, not an independent confirmation**. P0 must either reserve and bind
previously unscored repositories/source spans as a confirmatory panel or retain
the exploratory label in every result. This status is decided before any A0/A1
loss is read.

---

## 1. Arms, and exactly what each can support

| Arm | Manipulated | Follows the essay? | Strongest admissible claim |
|---|---|---|---|
| **A0** stream position | scored token position within a frozen source window | **Yes — all-token curve is the literal cheap-protocol replication** | descriptive loss-vs-visible-prefix curve, model-relative |
| **A1** paired context dose | preceding bytes for the same fixed target | Tests the proxy | within-target effect of additional visible context, model-relative |
| **B** event-level growth | repository size at the commit that introduced the code | Extends it | association between repository size and predictability of newly written code, conditional on repository |
| **C** matched incremental specs | language of expression, specification held fixed | Implements its proposed control | paired representation contrast for matched specification sequences |
| **D** pool support (*diagnostic*) | subsample size at fixed budget | No | retrieval/pool support only — **no exponent, no crossover** |

Four statements govern the document and must appear in any write-up:

1. **Only A0-all follows the cheap measurement recipe literally.** Its
   exposure status is unknown and it is reported beside a prospectively
   filtered temporal-generalization A0 curve. A1 is the paired identification
   check; B and C are extensions. A0/A1 measure visible context/position, not
   physical codebase growth (§2).
2. **No arm alone identifies a causal language effect.** A and B are
   observational across languages. Arm C manipulates *representation* under a
   matched specification — it does not manipulate intrinsic language design,
   ecosystem, or programmer population, and it is small and non-representative.
3. **No crossover is extrapolated beyond measured support.** An A0/A1
   crossover is explicitly a *visible-context proxy crossover*, reported only
   inside the context range measured in both languages. Arm B reports no
   crossover. No output is called a physical-codebase-size crossover.
4. **Every quantity is model-relative.** `L = H(true conditional) + KL(model
   mismatch)`; no result is an intrinsic property of a language.

---

## 2. The obstruction

The G3 exploratory sweep already ran a version of the essay's protocol and
produced **no reportable exponents** across all 44 cells (`PREREG.md:969-1001`),
for two independent structural reasons:

- **Range.** G3's context axis is byte position within a 32k-token window,
  topping out near 100 KB — roughly three decades below mathlib4's scale.
- **Density.** Matched 2.4 MB streams gave ~20 windows per cell
  (`PREREG.md:285`), far too few for a three-parameter fit.

Arms A0/A1 fix density and push visible-context range as far as native,
validated attention allows. They still cannot identify physical codebase
growth, which is why B exists, or isolate representation, which is why C
exists.

---

## 3. Arm A0/A1 — literal proxy and paired identification

The essay's cheap core is: build “a single large text file (with appropriate
metadata headers),” run forward passes, normalize target loss into bytes,
“average by token position in the context window,” and fit per language. It
mentions artificial context limits separately under a correctness/rollout
cross-check. A0 implements the cheap core; A1 adapts the context-limit idea to
NLL with fixed targets.

### 3.1 Two estimands that must not be conflated

**A0 — literal stream-position curve.** For a committed source window origin
`w`, exact checkpoint `m`, language `ℓ`, repository `r`, and ordering `o`, one
forward pass produces NLL at every eligible source token position. Metadata is
visible but not scored. For literal position bin `q_stream`, defined by exact
preceding stream bytes (including metadata headers) from the start of that
window:

    L_A0(q_stream) = Σ NLL_nats(scored source tokens in q-bin)
                     / (ln(2) · Σ covered source bytes)

This implements the essay's “perplexity per token position” followed by
“average by token position.” Different positions contain different target
bytes, so A0 is descriptive: code-position/composition and context exposure can
both contribute to its slope.

Header density is not comparable across languages: at the same `q_stream`, a
panel of many small files has exposed more header bytes and fewer source bytes
than a panel of large files. Every A0 token therefore also records
`q_source`, the cumulative scored-eligible **source bytes** preceding its bin,
excluding metadata bytes. The literal Gwern replication remains the
`q_stream` curve. Cross-language A0 comparisons use the companion `q_source`
axis and report the metadata-byte fraction by `q_stream` bin; an ordering that
appears only on one axis is denominator/stream-construction sensitive, not a
language result.

Two outcome-blind token cohorts are always reported from the same forward
passes. **A0-all** scores every structurally eligible source token and is the
literal whole-stream replication; because model exposure is generally
unknown, it is labeled exposure-unknown/possibly contaminated. **A0-temporal**
applies the frozen file-first-public cutoff screen from §8.1 and is the
temporal-generalization curve used in the A0/A1 compatibility gate. It is not
called a literal whole-stream replication. Neither cohort may be selected or
promoted after seeing loss.

**A1 — fixed-target context-dose curve.** For target block `t`, language `ℓ`,
repository `r`, model `m`, ordering `o`, and available context length `c`:

    L_A1(c) = NLL_nats( bytes(s(t)) | the c stream bytes preceding t under ordering o )
              / (ln(2) · D_score(t))

estimated per `(ℓ, r, m, o)` cell over the nested `c` grid. The cell object is
the curve `c ↦ L_A1(c)`. Every fitted A1 curve uses the **same target identities
at every included `c`**. A1 identifies the within-target benefit of more
visible prefix; it is not a repository-size manipulation.

Here `s(t)` is the first 512 source bytes of the realized target, ending at the
last UTF-8 scalar boundary at or before that limit, and `D_score(t)` is the
exact source-byte coverage of fully contained scored tokens. This short,
identical outcome horizon keeps the external context treatment `c` from being
swamped by several kilobytes of earlier target tokens. Loss over the complete
realized block is retained as a secondary horizon sensitivity from the same
forward pass.

A0-all is the direct replication; A0-temporal and A1 are the stronger
generalization/internal-validity checks. All are always published. Agreement
supports interpreting the temporal A0 slope as context learning rather than
target-position drift. Disagreement makes the cheap proxy ambiguous; A0 is not
rescued by relabeling A1 as a codebase scaling law.

### 3.2 Streams

One stream per `(repo, ordering)`: source files concatenated with exact frozen
metadata headers, ordered by

- **shuffled** (**cross-language headline**) — seeded permutation of the same
  file set; this applies one comparable construction even when build graphs
  differ in observability;
- **build-resolved topological** (realism sensitivity) — Kahn min-heap over
  exact environment-resolved intra-repo dependencies, ties broken by file sort
  order;
- **reverse-topological** (orientation/locality sensitivity) — reversal of the
  resolved order. It preserves unordered adjacency but flips which neighbor is
  in the left context, so it is explicitly **not** called a pure dependency
  null.

All three orderings permute the **same file set**; only file order differs.

At high `c`, shuffled cross-file context is dependency-arrival dominated: it
approaches a random repository-support draw rather than an ordered development
history. The shuffled curve remains the comparable cross-language primary,
but no shuffled language ordering or crossover is called a context or
predictability crossover unless the build-resolved topological curve agrees
in direction on the same complete targets and support. A repository that
fails the graph gate can report its shuffled descriptive curve but supplies no
crossover claim. This corroboration rule prevents Arm D's pool-support
construct from being relabeled a scaling exponent.

The existing regex graph in `prep_streams.py` is a proposal generator, not
scientific evidence. P1a must build and bind language-native graphs: Lean from
the exact Lake/Lean import environment, Python from parsed imports resolved in
the frozen environment, and C/C++ from compile commands/dependency scanning.
P0 freezes resolution/participation gates (proposal: ≥95% of internal
dependency references resolved, ≥30 resolved edges, and ≥10% of source files
participating). A repository that fails is excluded from topological/reverse
inference; its shuffled curve remains eligible. Edge coverage and every
exclusion are reported. No lexicographic degeneration is labeled
“topological.”

#### A0 source-window origins

A0 window origins are sampled once as anchored file identities from the
lexicographically sorted source inventory and reused across orderings and checkpoints. A window begins
at that file's exact metadata-header start. The P0 seed and systematic origin
formula are committed; no origin is chosen or topped up after a loss is seen.
For a fitted position range, only origins whose window is structurally present
and model-eligible through the largest included `q` are used at **every** `q`.
Thus the origin cohort is complete across position, even though the scored
  source tokens at different positions are necessarily different—the limitation
that makes A0 descriptive rather than paired.

The same source origins are used for checkpoint comparisons. Each checkpoint
has an exact safe/effective attention reach; cross-checkpoint and cross-language
curves use only common `q` support. Metadata/header tokens condition the model
but never enter the NLL numerator or source-byte denominator. Every scored
token is mapped back to one exact source file/span, and tokens crossing
metadata/source boundaries are excluded and counted.

### 3.3 A1 target identities — sampled once, reused across orderings

**Target identities are sampled exactly once from a lexicographically sorted
file-body inventory, and are then held fixed for every ordering, every model, and every `c`.
Targets are never resampled per ordering.** Resampling per ordering would make
the ordering contrast a comparison of different targets rather than of
different contexts.

A target identity is `(repo, file_path, file_byte_start, file_byte_end)` —
anchored to a file, not to a stream offset. Because orderings permute whole
files, the same identity resolves to a different stream offset in each
ordering while denoting byte-identical content.

Sampling rule:

1. **Blocks never cross a file boundary or a metadata header.** Candidate
   blocks are drawn from the concatenated *file-body* byte axis, with headers
   excluded from that axis; a block must lie entirely inside one file's body.
2. Target block length `T` is frozen (proposal: 4 KiB, where 1 KiB = 1024
   bytes). A block starts at the first line boundary at or after its sampled
   offset within the file. It ends at the last UTF-8 scalar boundary at or
   before `start + T`, truncated at end of file body. A realized span shorter
   than `T/2` is ineligible. Thus a pathological long line cannot create an
   unbounded target. **The realized span is recorded and denominates the
   full-block sensitivity; the primary first-512-byte horizon uses its exact
   token-covered source-byte denominator. `T` is never used as a
   denominator.**
3. Offsets are a **seeded systematic sample** over the file-body axis:
   `o_j = o_0 + j·Δ`, with `o_0` from a committed seed. Systematic rather than
   i.i.d. for even, reproducible coverage.
4. `Δ ≥ T` on the nominal file-body axis. Line alignment can still map two
   nominal offsets into overlapping realized spans (for example, on a very
   long line), so candidates are processed in canonical offset order and any
   realized overlap is rejected. The overlap-rejection count is recorded and
   the retained target spans are proven pairwise disjoint. Files shorter than
   the minimum realized span host no block; the count of such files is
   recorded.
5. A candidate is dropped if it is >50% comment or blank by byte, or if it
   fails the near-duplicate eligibility rule (§8.1). Drops are counted and the
   realized sample is published (§12).

### 3.4 A1 sampling trade-off, stated plainly

Contexts that never contain another target require stream length
≥ `n · (c_max + T)`. At `c_max = 1 MiB` and `n = 200` that is ~201 MiB per
stream — more than several repositories in the panel. **Maximum context length and
independent sample size trade off directly.** This is a structural constraint;
GPU memory/time may independently bind because long-context prefill is
nonlinear and the nested suffixes do not generally share a reusable prefix.

Overlap is therefore permitted, recorded, and modeled: §12 reports both the
disjoint capacity and the realized overlap fraction, and §9.3 carries target-
block and stream-region random effects so effective sample size is estimated
rather than assumed. Floors (§9.1) are evaluated against eligible blocks, not
nominal ones.

The **seeded shuffled ordering** defines K1 and the cross-language headline curve.
Each ordering separately must satisfy the ordinary block/file floor. An
ordering contrast has a stricter floor: the *intersection* of blocks eligible
under all three orderings at that `c` must itself satisfy the bin floor. This
is frozen before P1b; a thin complete-case intersection cannot be rescued by
using three different target sets.

### 3.5 A1 context construction

For a target at ordering-`o` stream offset `p` and grid level `c`, the context
is `bytes[p−c, p)` in the exact metadata-bearing stream — a **suffix ending at the target start**, so contexts for
one target are strictly nested: `c₁ < c₂ ⇒ context(c₁) ⊂ context(c₂)`. This is
the only construction that makes the `c` axis interpretable.

- If fewer than `c` bytes precede `p`, the cell is **undefined and recorded
  missing** — never zero-padded, truncated, or imputed.
- **With-file curve:** includes the target's own file prefix, because that is
  what the essay's stream protocol does.
- **Cross-file-only curve:** bytes belonging to the target's own file are
  **skipped, and the context is backfilled with earlier stream bytes until it
  again contains exactly `c` bytes.** This holds `c` constant and varies only
  content; blanking would confound content with context length. If fewer than
  `c` non-target-file bytes exist before the target after skipping, that cell
  is undefined rather than shortened or padded.

These are a required decomposition, not an optional sensitivity. A shuffled
with-file curve changes regime when `c` exceeds the available target-file
prefix: low rungs are local continuation, while high rungs draw from other
files. The exhaustion threshold is target- and language-dependent. P1a
therefore publishes its distribution by repository/language, and both curves
are fit separately on the same targets. A cross-language slope-difference
sign, language ordering, or within-support crossover claim is admissible only
if its conclusion is stable across the with-file and cross-file-only pair;
otherwise the result is explicitly file-granularity/context-composition
sensitive.

For the A0/A1 compatibility gate or any cross-language context claim, the
fitted range must also reach at least **one full decade above the cell's median
same-file exhaustion point**. P1a/P1b report that point and the fraction of
delivered context drawn from the target file at every rung. A range confined
to local continuation can still be plotted, but it cannot support a
repository-context interpretation.

### 3.6 Exact eligibility and effective attention reach

Eligibility is decided **after joint tokenization, against each model's exact
token limit** — never by a byte proxy. For each `(model, target, ordering, c)`:
tokenize context and target jointly; the cell is eligible only if total tokens
≤ the model's context limit and the target's scored token set is non-empty.
Ineligible cells are missing, never truncated into eligibility.

Input length is necessary but not sufficient. A checkpoint is eligible at
distance `c`/`q` only if its **effective causal attention reach** covers that
distance. P0 classifies native full attention, sliding-window/recurrent/hybrid
attention, and rope/YaRN extension separately. P1b validates the frozen scoring
path with a committed far-context causal probe at every grid rung. An extended-
context adapter is a separate treatment and is never pooled with native reach.
Failure or ambiguity makes that rung ineligible; a advertised config number is
not accepted as evidence that early bytes can influence the scored token.

Special-token policy, BOS handling, tokenizer revision, and any long-context
rope/config override are exact fields in the P0 model ledger. No chat template
is introduced unless that checkpoint's frozen scoring adapter requires it.
The shared target pool is never topped up after a loss or curve is observed.

For any fitted A1 range, eligibility is intersected over **all `c` values in
that range** before loss is read, so the target cohort is identical along the
curve. An ordering contrast additionally intersects all three orderings; a
checkpoint comparison intersects the exact checkpoints being compared. The
occupancy tensor records every discarded target and reason. This prevents an
apparent context slope caused by early/foundational targets disappearing at
large `c`.

Only tokens lying entirely inside the realized target span are scored;
boundary-straddling tokens are excluded and counted, reusing the
scored/straddled accounting in `eval_paired.py:171-199`.

A consequence to report, not hide: bytes-per-token differs by language, so at a
fixed model token limit **the maximum eligible `c` in bytes is
language-dependent**. Cross-language comparisons are made only over the `c`
range eligible in both languages (§9.2 common-support rule).

### 3.7 Averaging and diagnostics

- **A0 primary:** for every scored source token, record its exact
  post-tokenization position and preceding source bytes from the window origin
  (after the frozen special-token adapter), then average by the frozen
  `q_stream` and `q_source` bins.
  This is the essay's literal position-average quantity.
- **A0 cross-language companion:** record cumulative scored-eligible source
  bytes from the same origin, excluding metadata, and rebin the identical
  token losses on that axis. Literal within-language replication uses
  `q_stream`; cross-language A0 comparisons use `q_source`, with both curves
  and their metadata fractions shown.
- **A1 primary:** mean loss over the first 512 source bytes of the target as a
  function of the **external** preceding source-byte budget `c`. Exact token
  coverage is the denominator. The complete realized-block mean is a
  secondary horizon sensitivity reported only for rungs with `c ≥ 4T`
  (16 KiB); every token's effective position `c + j` is recorded for both.
  The full-block secondary is descriptive only. Its admissible rungs span at
  most 16 KiB–1,024 KiB = 1.81 decades, below the §9.2 minimum, so it is
  never fitted and never enters a range, common-support, or crossover
  decision.
- **Secondary recency diagnostic:** mean loss by decile of relative position
  *within* the target block. This is an additional diagnostic showing whether
  benefit is concentrated at block start; it is **not** the essay's position
  averaging and never headlines.

### 3.8 Grids

`q_stream`, `q_source`, and `c` use the same frozen bin edges
{512 B, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 KiB}, with `q_stream`
measured in metadata-bearing stream bytes, `q_source` in source-only bytes,
and `c` in delivered context bytes. Each level is subject to §3.6
eligibility. P0 freezes an **enumerated checkpoint ledger**, including
exact model/revision/tokenizer/config/adapter hashes. The design target is ≥3
families × ≥3 sizes with ≥32k-token context for descriptive coverage, plus
headline checkpoints with ≥128k effective reach and one ≥262k-context
checkpoint if available and licensable. P1b may mark a frozen checkpoint unavailable; it
may not substitute a more favorable checkpoint after any loss is observed.
The currently locked three-family × three-size ladder is **descriptive and
checkpoint-specific**, not a pooled headline roster: its native reach does not
prospectively establish three headline-eligible families. A checkpoint may
earn a model-specific headline only through K1. Unless at least three distinct
families independently earn that status, K3 is recorded as **unevaluable** and
there is no pooled family-stability claim or pooled crossover.

---

## 4. Arm B — event-level rolling origins

### 4.1 What the previous versions got wrong

Revision 1 subsampled a corpus and retrieved a fixed budget: that measures
whether the needed dependency is in the pool, not corpus-size scaling. It also
scored 2026 targets against 2023 snapshots, which can require APIs that did not
exist. Revision 2 replaced this with six-month intervals in which each snapshot
predicted the *next interval's* code — better, but still false in a specific
way: code committed five months after a snapshot was **not written against that
snapshot**, it was written against the repository as it stood at the moment of
its own commit. Revision 3 removes that claim.

### 4.2 Event-level estimand

Walk the repository's **first-parent chain**. For each commit `E` on that chain
that introduces new source blocks:

- **Target:** maximal contiguous added-source-line runs in the exact
  zero-context diff from `parent(E)` to `E`, including additions to existing
  files as well as new files. Runs longer than `T` are split deterministically
  at the last line boundary at or before `T`, falling back to the last UTF-8
  scalar boundary when one line itself exceeds `T`; runs shorter than the frozen Arm-B minimum
  (proposal: 256 source bytes) are ineligible. Any per-event/repository cap and
  its outcome-blind systematic subsample are frozen at P0. Renames with no
  added content contribute no target.
- **Context:** the strict source-stream prefix ending at that target in the
  repository tree at `E`, in the build-resolved order when that repository
  passes the graph gate (otherwise Arm B is ineligible),
  truncated to fixed budget `B*` (and optionally the §3.8 `c` grid). This may
  include target-file prefix and same-commit material that precedes the target
  under the frozen ordering; it never includes the target or later stream
  bytes.
- **Size covariate:** `size(E) =` source bytes of the repository at that
  immediate first parent.

    L_B( size(E) ) = NLL_nats( block introduced at E | strict prefix at E )
                     / (ln(2) · D(t))

This is an **event-time source-order conditional**, not a reconstruction of the
developer's hidden authoring state. A Git commit has no trustworthy within-
commit authoring order. The dependency/file order is therefore an explicit
measurement convention: same-commit APIs before the target are visible and
same-commit material after it is not. No target is predicted from a later
commit, and the parent-tree size remains the pre-change scale covariate.

**Six-month snapshots have no scoring role.** They are used only to bin and
display events along the size and time axes.

### 4.3 Identification requirements — frozen now

Within one repository, size and calendar date are nearly collinear, so a
single-repository Arm B cannot separate size from age, toolchain, or style
drift. Identification comes from **between-repository variation in growth
rate**. The following are frozen at P0 and are preconditions, not diagnostics:

- **≥3 repositories per language** contributing events.
- **≥5 origins per repository** after all eligibility filters.
- **≥1 decade of within-repository post-cutoff size change** in each of at
  least three repositories, and ≥2 decades across their union.
- **Full-rank design matrix** for {log size, calendar date, repository fixed
  effects, toolchain}.
- **|residual size–date correlation| < 0.8**, computed after removing
  repository fixed effects.
- **VIF < 5** for `log size` in that design.

If any fails, **no Arm-B size coefficient is reported** (K5) and Arm B is
reported as unidentified for that panel.

These conditions establish usable residual variation, **not a causal size
effect**. The frozen primary descriptive regression is two-sided:

    L ~ log(size_at_parent) + calendar_time + toolchain + repository_FE

with model-checkpoint interactions and uncertainty clustered at repository and
event (blocks within one commit are not independent). The exact calendar basis,
toolchain coding, weighting, spline diagnostic, and small-cluster inference are
frozen and simulation-tested at P0. Repository fixed effects make the size
coefficient a within-repository association; between-repository growth-rate
variation supplies the leverage needed to distinguish it from calendar time.

Target composition can still drift with repository age: later commits may add
different kinds of declarations, tests, interfaces, or generated/boilerplate
code than earlier commits. P0 therefore freezes a language-specific,
outcome-blind syntactic target-class mapping and minimum class floors. P1a
reports class-by-repository-by-time/size occupancy. The primary coefficient is
accompanied by class fixed effects and a within-class/standardized sensitivity;
large class imbalance or coefficient instability is reported as residual
composition confounding. This audit does not upgrade the descriptive
association to a causal effect.

### 4.4 Post-training-cutoff support gate, and permission to die

Arm B's absolute level is contaminated wherever events predate a model's
training cutoff. Headline Arm-B cells therefore use only events **after** that
model's cutoff. If the post-cutoff event count fails the §9.1 floors for a
model, Arm B is infeasible for that model; if it fails for all models, **Arm B
is declared infeasible and dropped**, and the growth question is reported as
unanswered. Arm B is explicitly allowed to die here rather than be rescued by
relaxing a floor.

The §4.3 identification tests are recomputed **inside each model's post-cutoff
event subset**, not once on the full history. P1a reports their joint
feasibility surface by `(language, model)`: repository/origin counts, rank,
residual size–date correlation, VIF, and size range. K5 and K6 are evaluated on
the same rows, because a panel that is identified only before the cutoff or
adequately populated only after adding collinear events is not usable.

### 4.5 Construction

Events come from the first-parent chain with per-block first-appearance
attribution derived from exact zero-context Git diffs/blobs. Renames and copied/vendor code
are handled by the same conservative provenance and near-duplicate rules as
§8.1. `prepare_longitudinal_inventory.py` already walks first-parent history
and records tree mass, toolchain drift, and path churn, while correctly stating
that its current snapshot artifact "cannot estimate a scaling exponent"; it is
an input to, not an implementation of, this event construction. Full history
is available because `corpus_lock.py checkout` retains it.

---

## 5. Arm C — matched incremental specifications

The essay's own proposed control: "write a specification in two different
languages, up to the same measured quality, and *then* measure perplexity
differences." Arm C makes it incremental — one **growing** specification
implemented in two languages in matched increments, predictability measured at
each increment.

**What it contrasts: representation under a matched task.** Arm C varies the
language in which a fixed specification is expressed. It does **not** isolate
intrinsic language design, ecosystem maturity, tooling, programmer population,
verbosity, or proof-obligation burden, and its result does not transfer to
repositories written by different communities for different purposes.

Frozen before any implementation: multiple independent specification
sequences and increment boundaries; language-neutral blinded acceptance tests
plus an explicit obligation ledger; randomized/counterbalanced implementation
order; and authorship crossed across languages where feasible. Unless P0
commits at least six specifications and two independent implementations per
language with that counterbalancing, Arm C is labeled a **paired case-study
contrast**, not an effect. No exponent unless the increment count independently
satisfies §9.2's range rule, which is unlikely.

---

## 6. Arm D — pool-support diagnostic

Nested subsamples of one snapshot at a fixed retrieval budget. Retained because
it measures something real — how loss depends on whether the needed dependency
is present in the retrievable pool — and because it calibrates how much of any
Arm-B association is pool support. Reported as a support curve with retrieval
hit-rate on the same axis. **No exponent, no crossover, no scaling language.**

---

## 7. Evaluation denominators

- **Primary: bits per source byte.** The essay's choice; comparable to prior
  work. Written out in full throughout; "b/B" is not used.
- **Sensitivity: bits per Unicode codepoint.** Lean is Unicode-dense
  (`∀ → ≤ ⟨⟩`), Python near-ASCII, so the two can order languages differently.
  Exact scored-span codepoint accounting exists (`DESIGN_V2.md:238-240`;
  `eval_paired.py:171-199`).
- **Not cross-language comparable: semantic-unit and AST-node denominators.** A
  Lean AST node and a Python AST node are not the same object, so a ratio
  across languages in those units is undefined. Within-language descriptives
  only; never used for a cross-language ordering or crossover.

**Frozen interpretation rule.** Headline results are bits per source byte. If
the bits-per-codepoint sensitivity reverses a cross-language ordering, both are
reported and the ordering is declared **denominator-dependent**; no single
ordering is asserted.

---

## 8. Leakage and dependence

### 8.1 Leakage and near-duplicate eligibility

- A target is excluded from its own context by construction (contexts are
  strict prefixes ending at the target start).
- **Near-duplicate rule (eligibility, not filtering):** a candidate target is
  **ineligible** if a near-duplicate of it could enter **any headline context**
  — that is, under any ordering, at any `c` up to `c_max`, in any headline arm.
  The test is over the union of headline contexts, evaluated once at sampling
  time; targets are dropped whole rather than filtered per context, so the
  target set stays identical across orderings and `c` levels. Screening reuses
  A6 / `prepare_v2b_neardup.py`, extended cross-repository (vendored and
  re-exported code is a much larger risk in a multi-repo panel than in V2-b's
  five corpora).
- Arm-B event targets use the analogous event-time rule: a target is
  ineligible if a near-duplicate exists in its strict prefix/context or in the
  cross-repository pre-cutoff index. The decision is made before scoring and is
  recorded per event.
- Contamination: per-checkpoint first-add-date cutoffs
  (`prep_streams.py:100-207`). A0-all intentionally retains every structurally
  eligible token to replicate the essay's whole-stream protocol and is always
  labeled exposure-unknown/possibly contaminated. A0-temporal and A1 use
  post-cutoff units, and Arm B uses the §4.4 gate. The date is a conservative
  **file-first-public** screen: new text added later to an old file remains
  excluded. It is not advertised as proof of model non-exposure. A checkpoint
  with no documented, auditable cutoff is ineligible for temporal-generalization
  A0/A1 headlines but remains reportable in the explicitly exposure-unknown
  A0-all replication.
- Overlapping contexts may contain other target blocks (§3.4): recorded and
  modeled (§9.3), never silently allowed.

### 8.2 Cross-repository dependence

Nearly every substantial Lean 4 repository imports mathlib, so a nominal
ten-repository Lean panel may be close to one effective independent draw, and
repo-level bootstrap intervals would be anticonservative. The rule is frozen
uniformly for **every language**: strip build-pinned external closure from each
repository's scored stream; then build a repository-dependence graph from
direct repo imports and exact/near-duplicate own-source sharing using P0-frozen
thresholds. Connected components are the resampling clusters and their count is
the effective independent-repository count. Language-general inference
requires ≥3 components. With one or two, report repository/component-specific
curves only—cluster-robust errors are not invoked with an invalid cluster
count. The graph, thresholds, edges, and leave-one-component-out forest plot
are published. Python and C++ receive exactly the same gate.

### 8.3 Domain and programmer confound

The essay names this as its own primary worry: estimates are "at least
partially confounded by the programmers and domains". Mitigations in increasing
strength: topic-matched pairs inside the science-Python panel; the
physlib↔mathlib within-Lean pair; and Arm C, which is the only manipulation —
of representation, not of language design (§5). The residual, stated in the
abstract rather than buried: A and B remain observational across languages.

---

## 9. Fits and diagnostics

### 9.1 Floors — in blocks and files, planned versus reporting

G3's window/document floors do not transfer silently. A0's repeated unit is a
window origin; A1's repeated unit is a target block. **The planned sample and
the reporting floor are different objects and are never conflated:**

- **A0 planned:** 200 structurally eligible shared window origins per
  repository. **A1 planned:** 200 structurally eligible shared target
  identities per repository. Both are sizing targets before
  model/order/position eligibility, not promises about a final cell.
- **A0 reporting gate:** every fitted `q_stream` bin (within-language literal
  curve) or `q_source` bin (cross-language curve) has ≥20 complete window
  origins and scored bytes from ≥10 distinct target files; a quantitative cell
  has ≥100 complete origins and ≥30 distinct scored files over the fitted
  range. A0-all and A0-temporal occupancy are reported separately.
- **A1 reporting gate:** every `c` bin has ≥20 identical-cohort blocks from ≥10
  distinct source files; a quantitative cell has ≥100 blocks from ≥30 files
  over the fitted range. Below either arm's gate, that cell is descriptive with
  no fit.

Distinct-file counts are required alongside unit counts because observations
from one file are strongly dependent.

Nonparametric first: log-spaced bins with per-bin means and medians, and
bootstrap over blocks with files as the resampling cluster. **No fit is
reported for a cell whose binned curve is not also shown.**

### 9.2 Parametric forms, range, and support

For **A0 and A1 separately**, reuse the `analyze_v2.py:120-190` machinery:
`L(x) = A·x^(−β) + L∞`, where `x=q_stream` for the literal within-language A0
curve, `x=q_source` for cross-language A0 comparisons, and `x=c` for each A1
decomposition, against a
saturating exponential and a log-linear alternative, all under the same
contiguous fit/holdout split with the frozen relative-error gate. P0 overrides
the older analyzer's grid and 8-KiB split: fit rungs are 512 B through 32 KiB;
the fixed validation holdout is 64, 128, and 256 KiB; and eligible 512- and
1,024-KiB rungs are nonparametric diagnostics only. The two diagnostics do not
enter fitting, holdout validation, the 10× exhaustion gate, or range/common-
support licensing. The total validated fit-plus-holdout support—not the fit
subset alone—must pass the range rules below. Fits retain the existing
equal / sqrt / byte weightings. The outputs are explicitly
`β_position_stream`, `β_position_source`, `β_paired_with_file`, and
`β_paired_cross_file`; they are never pooled into one coefficient. β
never headlines alone; `L∞` is reported as **asymptotic model bits per source
byte**, never as irreducible entropy or a physical-codebase asymptote.

**Arm B does not inherit that decreasing power-law form.** Its primary model is
the preregistered two-sided coefficient of `log size` with repository, calendar
date, and toolchain controls from §4.3; a frozen spline is a shape diagnostic.
The coefficient may be positive, negative, or unidentified. Arm B reports no
`L∞`, exponent, or crossover: fixed visible budget and event-time observational
growth are not the essay's context-access scaling curve.

- **Range rule:** an A0/A1 fit is reported only over a contiguous valid range
  spanning **≥2 decades** on its declared `q_stream`, `q_source`, or `c` axis,
  at eligible sample size. Arm B instead
  uses the stricter within-repository/union support gate in §4.3; passing it
  licenses only the controlled association coefficient, not a scaling-law
  exponent. The A1 full-block secondary horizon is exempt from this rule
  because it is never fitted; the rule is not relaxed to accommodate it.
- **Floor-robust headline rule:** the A0/A1 compatibility gate and every
  cross-language slope/crossover additionally require ≥2 decades after
  removing the 512-B floor rung, plus §3.5's one-decade-above-median-
  exhaustion condition. A cell that reaches two decades only because of the
  floor rung is labeled `floor-dependent-descriptive`; its curve is reported
  but it cannot headline. The 512-B rung is never silently dropped after loss.
- **Common-support rule:** cross-language A0 comparisons use only common
  `q_source` support; cross-language A1 comparisons use common `c` support.
  The applicable common axis must itself span ≥2 decades. Common
  `q_stream` support alone cannot license a cross-language A0 claim.
- **Support rule:** a crossover is reported only inside measured common
  support. Outside it the output is "not reached within measured support (upper
  bound: X)". The primary crossover is read from the **nonparametric common-
  rung language-difference curve**, never from extrapolating the parametric
  fit: posterior draws are interpolated linearly in `log₂(x)` only between
  adjacent measured rungs, and a crossover is reported only when ≥95% of draws
  contain exactly one within-support crossing. Otherwise the frozen output is
  "no stable unique crossover within measured support". A parametric crossing
  is a labeled sensitivity only. Extrapolated crossovers appear in no figure,
  table, or abstract.

A0/A1 compatibility is a frozen interpretation gate, not a model-selection
step, and uses A0-temporal on `q_stream` versus A1 with-file on `c`. Within a
checkpoint, opposite slope signs declare the stream-position proxy
composition-sensitive. Otherwise the 95% interval for
`β_position_stream−β_paired_with_file` against the P0 equivalence ROPE
`[-0.02, 0.02]` has
three outcomes, and only three: wholly inside → compatible, and A0's slope may
be read as context learning; wholly outside → composition-sensitive;
straddling either bound → indeterminate, with both curves reported and the
precision shortfall stated, asserting neither verdict. Indeterminate is a
real outcome, not a reason to move the ROPE. In every non-compatible outcome
both curves remain reported, neither is promoted as a general context scaling
coefficient, and no proxy crossover headlines.

### 9.3 Arm-A checkpoint-first hierarchical structure

The primary curve and slope are per exact checkpoint, cohort, axis, and
context regime: the four coefficients named in §9.2 are never collapsed into
generic `β_{ℓ,m,A0}`/`β_{ℓ,m,A1}` storage. Cross-language comparisons pair
languages under the same checkpoint; a pooled coefficient corresponding to no
actual model is not primary.

The hierarchical model has a fixed ordering effect and its language
interaction, random effects for **repository**, **window origin or target
block** (one shared intercept across repeated positions/orderings), **stream
region** (absorbing §3.4 overlap), and **checkpoint nested inside the frozen
model-family ladder**. It includes prespecified
`language × log(x) × log(parameter-count)` and family interactions, so a model-
size trend is estimated rather than averaged away:

    L ~ language×arm×log(x)×log(params) + ordering
        + repo + unit + region + family/checkpoint

Checkpoint size is therefore not pooled as if three sizes in one family were
replicates of one model. The exact nonlinear likelihood, positivity
constraints, priors, stream-region construction, and two offset tilings used
to check region-boundary sensitivity are frozen at P0 and implemented in a
simulation-tested consumer before any outcome. A finite-panel language summary
may be reported only after §9.5's stability gate and is labeled an average over
the enumerated checkpoints, not a population law over future models.
Checkpoint-specific crossovers and intervals come from the corresponding joint
language curves; any finite-panel summary propagates repository, unit,
checkpoint, and family variance.

### 9.4 Ordering contrasts — complete cases only

Topological versus shuffled versus reverse contrasts are computed on
**complete cases**: A0 uses window origins structurally/model-eligible through
the fitted declared-axis range under all three orderings; A1 uses target blocks eligible
through the fitted `c` range under all three. Eligibility differs by ordering,
because a file's stream position changes. Contrasting incomplete sets would
confound ordering with which units survived. Complete-case count and discarded
fraction are reported with every contrast. The arm-specific §9.1 bin floor
applies to the intersection itself; ordinary within-ordering curves use their
own floors. K1 remains a gate on the seeded shuffled headline curve, not on the
optional ordering contrast.

### 9.5 Family stability by ROPE — δ_β frozen now

For the finite frozen model panel, declare the language slope
**family-stable** iff the posterior between-family standard deviation
`τ_family` has 95% upper bound below **δ_β = 0.02**, frozen here and not
revisited after P1. With only three purposively selected families this is a
panel-stability diagnostic, not an estimate of variance in a super-population
of model families; indeterminate is expected when precision is inadequate.

Three outcomes, and only three:

- `τ_family` 95% upper bound `< δ_β` → **stable**; a finite-panel mean may be
  reported beside the checkpoint-specific slopes.
- 95% lower bound `> δ_β` → **unstable**; β is model-relative and reported per
  family, with no pooled crossover. A family-specific within-support crossover
  may be reported as such.
- Interval straddling `δ_β` → **indeterminate**; β is reported per family with
  the precision shortfall stated. Indeterminate is a real outcome, not a reason
  to move δ_β.

### 9.6 Required diagnostics

Per cell: arm-appropriate complete-origin/target occupancy and distinct files;
effective-attention probe by rung; A0/A1 slope compatibility; residuals vs `log x`;
holdout relative error for all three forms; leave-one-repository-out refit of
`β_ℓ`; leave-one-family-out refit; complete-case ordering contrasts (§9.4);
required with-file/cross-file decomposition and stability gate (§3.5);
position-in-window curves and the
within-block decile diagnostic (§3.7); and the bits-per-source-byte versus
bits-per-codepoint pair (§7).

### 9.7 Compressor baselines — bounded specificity check

Run gzip, PPM, and a byte n-gram over identical streams; report alongside every
cross-language ordering.

- A **difference** between the compressor ordering and the model ordering
  indicates the model captures structure beyond what **those specific
  compressors** capture. It does not establish sensitivity to semantics, types,
  or formal structure.
- **Agreement** indicates the ordering is reproducible by simple redundancy
  measures, which weakens a formality reading without refuting it — a
  compressor and a model can agree for unrelated reasons.

Mandatory reporting rule, not a kill criterion, and in neither direction a
proof.

---

## 10. Kill criteria and warnings

**Stops (frozen at P0).** K1's all-languages clause — if no common checkpoint
supports both A0 and A1 for at least two languages, the study stops and
publishes the reachable-range bound. K5 — Arm B produces no size coefficient
for that panel. K6 — Arm B is dropped as infeasible.

**Scope limiters and reporting rules (frozen at P0; they bound the claim, not
the run).** K1's per-cell clause — no slope for a failing
`(arm, language, checkpoint)` cell. K2 — no language-general exponent or
crossover; component-specific results remain reportable. K3 — no pooled β or
pooled crossover; family-specific curves remain reportable. K4 — no crossover
outside measured common support, and no physical-codebase crossover from
A0/A1 at all.

- **K1 — range.** Fewer than 2 contiguous decades of valid support at eligible
  sample size on the declared axis/regime for an
  `(A0|A1, language, checkpoint)` → no slope for that cell. Within-language
  A0 is gated on `q_stream`, cross-language A0 on common `q_source`, and A1 on
  `c` separately for with-file and cross-file-only contexts. A headline K1
  pass also requires the §9.2 floor-robust and §3.5 exhaustion conditions;
  floor-dependent cells are descriptive rather than silently counted as
  passes.
  If no common checkpoint supports both A0 and A1 for at least two languages,
  stop and publish the reachable-range bound.
- **K2 — language-panel independence.** For any language with fewer than three
  §8.2 independent components, no language-general exponent or crossover. A
  clearly labeled repository/component-specific context exponent may still be
  reported if K1 holds.
- **K3 — family instability.** `τ_family` above the ROPE (§9.5) → no pooled β
  or pooled crossover. Family-specific curves remain reportable within support.
- **K4 — support.** No visible-context proxy crossover outside measured common
  support, ever; no physical-codebase crossover from A0/A1 at all.
- **K5 — Arm B identification.** Any §4.3 precondition unmet → no Arm-B size
  coefficient.
- **K6 — Arm B feasibility.** Post-cutoff events below floor for all models
  (§4.4) → Arm B dropped as infeasible.

**Warnings (mandatory reporting, never stops).** Compressor agreement or
difference, bounded per §9.7; denominator-dependent ordering (§7); Arm C
non-representativeness (§5); Arm D reported only as pool support (§6).

---

## 11. Compute tiers

Cost is expressed first in **countable invocations and exact token lengths**,
not `Σ` source bytes as if attention were linear. Long-context prefill,
sliding/hybrid attention, KV memory, and kernel behavior vary by checkpoint.
GPU-hour and feasibility figures are omitted until P1b benchmarks the exact
scoring path, including peak memory, OOM, numerical equivalence, and seconds at
every rung on the actual hardware.

| Tier | Scope | Unit cost |
|---|---|---|
| T0 | P1a structural feasibility, compressor baselines | CPU only |
| T1 | A0/A1 MVP: 3 languages × best-populated repos × 3 families × 3 sizes | exact invocation/token-length ledger, priced by P1b |
| T2 | Arm A full panel, all three orderings | ~3× T1 |
| T3 | Arm B event-level panel | invocations = `Σ_repos Σ_events n_blocks`, each at fixed `B*` + target tokens; priced by P1b |
| T4 | Arm C case study | small; dominated by human implementation time |

Tier costs are computed from P1b's measured throughput, not from this table.
Stream length versus `c_max` (§3.4) and GPU feasibility are separate gates;
neither is assumed away.

---

## 12. Feasibility artifacts

Two artifacts, because structural feasibility needs no model and throughput
needs one.

### 12.1 P1a — `v2c_direct_scaling_feasibility_v1` (CPU only, no model load)

```
{
  "schema": "v2c_direct_scaling_feasibility_v1",
  "generator": {"source_commit","source_tree_hash","program"},
  "corpora_lock_sha256": "...", "frozen_constants_sha256": "...",
  "repos": [{
    "repo","language","locked_sha",
    "n_files","n_files_too_short_for_block",
    "source_bytes","source_codepoints","bytes_per_codepoint",
    "stream_bytes_topo","stream_bytes_shuffled","stream_bytes_reverse",
    "metadata_bytes_fraction_by_q_stream_ordering",
    "dependency_references","dependency_references_resolved",
    "dependency_edges","dependency_participating_files","graph_gate_ok",
    "n_A0_origins_structural",
    "A0_structural_occupancy_by_ordering_axis_rung",
    "A0_max_contiguous_structural_decades_by_ordering_axis",
    "n_blocks_sampled","n_blocks_eligible","n_distinct_files_with_blocks",
    "same_file_prefix_exhaustion_bytes_distribution",
    "same_file_context_fraction_by_c_ordering",
    "n_blocks_structurally_complete_by_c_ordering_regime",
    "n_blocks_disjoint_capacity_by_c_ordering",
    "overlap_fraction_by_c_ordering",
    "n_blocks_rejected_realized_overlap",
    "n_blocks_dropped_comment","n_blocks_dropped_neardup",
    "external_import_fraction","shared_content_fraction_with_panel",
    "first_add_date_min","first_add_date_max",
    "n_blocks_post_cutoff_by_model": {"<model_id>@<revision>": n}
  }],
  "languages": [{
    "language","n_repos","n_independent_components",
    "repository_dependence_edges","component_membership",
    "panel_bytes","bytes_per_codepoint_mean",
    "min_rung_meeting_10x_exhaustion_bytes",
    "implied_min_context_bytes_for_headline",
    "structural_headline_reachable"
  }],
  "arm_b_events": [{
    "repo","event_sha","parent_sha","event_date","toolchain",
    "size_bytes_at_parent","n_new_blocks","target_class_counts",
    "n_new_blocks_post_cutoff_by_model"
  }],
  "arm_b_panels_by_model": [{
    "language","model_id","revision","cutoff_date",
    "n_repos","min_origins_per_repo","n_events","size_range_decades",
    "design_full_rank","residual_size_date_correlation","vif_log_size",
    "K5_identified","K6_post_cutoff_floor_ok"
  }],
  "decisions": { ... }        // structural block only; see §12.3
}
```

### 12.2 P1b — `v2c_direct_scaling_runtime_v1` (GPU, non-outcome)

Loads models but scores **no target and emits no loss**. It measures
tokenization and throughput only. Eligibility tokenization may read the frozen
target bytes, but the throughput forward pass uses a committed, outcome-free
calibration stream and discards logits; it never runs a target continuation or
writes an NLL.

Exact model survey table, one row per model:

| field | meaning |
|---|---|
| `model_id` | HF identifier |
| `family` | model family for the crossed effect |
| `revision` | pinned commit SHA |
| `params` | parameter count |
| `context_limit_tokens` | exact limit |
| `attention_pattern`, `native_reach_tokens` | full/sliding/hybrid/recurrent mechanism and native causal reach |
| `context_adapter` | exact native or extended rope/YaRN configuration; separate treatment |
| `tokenizer_id`, `tokenizer_sha256` | tokenizer identity |
| `dtype`, `attention_impl` | numerics and kernel |
| `license` | usability |
| `training_cutoff_date` | for the contamination gate |
| `bytes_per_token_by_language` | measured on panel streams |
| `A0_eligibility_by_repo_ordering_axis_q` | exact complete origins and distinct scored files for A0-all/A0-temporal on both `q_stream` and `q_source` |
| `A1_eligibility_by_repo_ordering_regime_c` | identical-cohort targets and distinct files for with-file/cross-file regimes over each candidate fitted range |
| `complete_case_counts_by_arm_repo_x` | three-ordering intersections for A0 origins and A1 targets |
| `far_context_probe_by_rung` | committed causal-reach result and threshold at every `q_stream`/`q_source`/`c` rung |
| `range_decades_with_and_without_floor_by_cell` | contiguous eligible decades with all rungs and after removing 512 B, plus median exhaustion and one-decade-above-exhaustion status |
| `max_eligible_c_bytes_by_language` | descriptive upper envelope, not itself a K1 gate |
| `runtime_by_rung` | exact seconds, peak accelerator memory, OOM, and units/hour for the frozen scoring path |
| `chunk_equivalence` | full versus production chunking/logit comparison on committed non-target bytes |

K1 is recomputed from the A0/A1 eligibility tensors on the seeded shuffled
headline ordering: each valid contiguous declared-axis range must span two
decades and meet that arm's unit/file floors at every included bin. A0
within-language literal fits use `q_stream`; cross-language A0 uses
`q_source`; A1 uses `c` in each context regime. A single maximum cannot
establish that and is descriptive only. The complete-case table separately
governs ordering contrasts.

### 12.3 Decisions are recomputed, never trusted

Each artifact carries only the decisions its own inputs determine. P1a loads no
model, so it cannot evaluate K1 — that depends on exact per-checkpoint
tokenization counts, which exist only after P1b. P1a may use the already-frozen
checkpoint cutoff metadata for Arm B without loading model weights. Requiring
K1 in both blocks would force P1a to fabricate a field it cannot compute.

P1a — structural decisions:

```
"decisions": {
  "K2_independence_ok_by_language": {"<lang>": bool},
  "K5_K6_arm_b_by_model": {"<lang>/<model_id>@<revision>": bool},
  "headline_conditions_structurally_reachable_by_language": {
    "<lang>": bool
  },
  "headline_requires_top_rung_by_language": {"<lang>": bool},
  "unit_file_floors_structurally_reachable_by_arm_axis_regime_language": {
    "<arm>/<axis>/<regime>/<lang>": bool
  }
}
```

P1b — range decision, plus the joint arm gate, which is computable only once
both artifacts exist and is emitted only by P1b:

```
"decisions": {
  "p1a_sha256": "<binds the exact structural artifact this consumed>",
  "K1_range_ok_by_arm_axis_regime_language_model": {
    "<cohort-or-arm>/<axis>/<regime>/<lang>/<model>@<rev>": bool
  },
  "range_support_class_by_arm_axis_regime_language_model": {
    "<cohort-or-arm>/<axis>/<regime>/<lang>/<model>@<rev>":
      "headline|floor-dependent-descriptive|insufficient"
  },
  "arm_A0_A1_go_by_language": {"<lang>": bool},
  "arm_B_go_by_language_model": {"<lang>/<model_id>@<revision>": bool}
}
```

The K1 map has an exact admissible key set: A0-all and A0-temporal each carry
`q_stream/na` and `q_source/na`; A1 carries `c/with-file` and
`c/cross-file-only`. Every frozen `(language, model@revision)` appears once for
each admissible combination. Missing, extra, or structurally impossible
axis/regime combinations invalidate the artifact. The K1 Boolean is true only
for `headline`; it is never true for a floor-dependent descriptive cell.

**Every consumer recomputes its artifact's block from that artifact's raw
fields and the frozen constants, and refuses on any mismatch.** A consumer of
P1b additionally re-reads the P1a artifact named by `p1a_sha256` and recomputes
the structural decisions before evaluating the per-arm gates. The stored blocks are a
convenience and a cross-check, never an input a gate trusts — the same
discipline the V2-b evidence chain applies to every derived field.

---

## 13. Go/no-go sequence

**P0 — freeze.** Freeze §14 constants including `δ_β = 0.02` and the §4.3 Arm-B
preconditions; freeze §9.1 floors, §9.2 rules, §10 kill criteria, the exact
corpus/panel and exploratory-versus-reserved-confirmatory status, checkpoint
ledger/scoring adapters, metadata-header bytes, sampling seed and cohort-size
rule, primary arm/checkpoint/language contrasts, multiplicity policy, power
simulation, and the complete analysis model. The power simulation must use the
512-byte primary score horizon and the frozen clustering structure. The
compatible and outside-ROPE cases are its adequacy gates. The boundary case
measures nominal interval coverage at the positive ROPE boundary and is
diagnostic only; a wide interval cannot count as power. The simulation reports,
for every frozen effective-repository count and unit-slope standard deviation,
the largest contiguous tested repository-slope standard deviation for which
both adequacy cases reach the 0.80 target. It does not require an incoherent
conjunction over deliberately falsifying sensitivity cells.

The central `unit_slope_sd = 0.08` and `repository_slope_sd = 0.005` values are
explicitly assumptions at the 512-byte horizon, not empirical measurements and
not a power certificate. Before any primary or holdout loss is scored, a frozen
disjoint calibration cohort—never used in a primary or holdout fit—must emit
only pooled unit- and repository-slope variance (no means, condition contrasts,
or crossover). A language-general score run is authorized only when those
calibrated variances fall inside the simulated adequacy boundary at that
language's effective repository count. Otherwise the language is prospectively
restricted to repository-specific description. If planned support is
inadequate, increase units or the score horizon and, when repository variance
binds, add genuinely independent repositories/components—never widen the
scientific ROPE to fit the budget. Fewer than three independent repositories
always implies repository-specific description only. The model-free P1a census
may proceed before calibration because it reveals no loss or effect estimate.
→ *Output:* constants table plus its sha256. *Go:* review sign-off.

**P1a — structural feasibility. CPU only; no model is loaded.** Emit
`v2c_direct_scaling_feasibility_v1`: stream and block census, eligibility and
drop accounting, effective independence audit, bytes-per-codepoint ratios,
metadata-byte fractions across every A0 position rung, event series, Arm-B
target-class occupancy, and Arm-B panel statistics.
It also reports the maximum contiguous structural decades separately for
`q_stream` and `q_source`, plus target-complete occupancy in both A1 context
regimes, before any tokenizer/runtime work. → *Go:* the applicable arm/axis/
regime unit/file floors and
`headline_conditions_structurally_reachable_by_language` are true for ≥2
languages. A language whose implied minimum context bytes exceed the most
generous frozen checkpoint's optimistic byte reach is dropped from headline
scope before P1b. Any language with
`headline_requires_top_rung_by_language=true` is prospectively recorded as at
risk of a single-checkpoint headline for which the K3 family-stability ROPE is
unevaluable. K2 independently determines whether each remaining language
supports a language-general rather than repository/component-specific claim.
*No-go:* stop and publish the bound.

**P1b — runtime battery. GPU, non-outcome: no loss is emitted.** Emit
`v2c_direct_scaling_runtime_v1` with the §12.2 survey, exact
eligibility/count tables, descriptive maximum, and throughput.
→ *Go:* recomputed `K1_range_ok_by_arm_axis_regime_language_model` is true for A0 on
common `q_source` and A1 on `c` in both required context regimes, for at least
two languages under at least one common frozen checkpoint. Literal
within-language A0 additionally records the separate `q_stream` decision.
*No-go:* **K1** — stop; the reachable-range bound is the result.

**P2 — compressor baselines. CPU only.** Ordering per denominator over the
exact streams P3 will score. A **warning input to interpretation (§9.7), never
a gate**; P3 proceeds regardless.

**P3 — A0/A1 (T1, then T2).** A0-all/A0-temporal exact position averaging on
both committed axes and A1 nested `c` grids in both required context regimes,
three orderings on shared origins/target identities, exact checkpoint ladders.
→ *Pooled claim:* §9.2 range/common-support rules pass and `τ_family` is
stable. *K3:* report family-specific curves instead; do not pool or extrapolate.

**P4 — Arm B (T3), scientifically independent of K3.** Event-level rolling
origins may run whenever its own prospective P1a/P1b gates pass; an unstable
Arm-A family effect is not a reason to suppress a separately identified growth
association.
→ *Go:* the exact `(language, model)` row in
`K5_K6_arm_b_by_model` is true. *No-go:* Arm A alone, growth question reported
unanswered for that panel.

**P5 — Arm C (T4).** Matched incremental specifications, as a case study.

---

## 14. Constants frozen at P0

| Constant | Value |
|---|---|
| Target block length `T` | 4 KiB; realized span in `[T/2,T]`; primary score horizon is first 512 source bytes with exact token-covered denominator, full span is secondary |
| Target identities | sampled once from the lexicographic file-body inventory; reused across all orderings, models, and `c` |
| A0 origins | anchored file-header origins sampled once from the lexicographic inventory; complete over every fitted `q_stream`/`q_source` range |
| Block containment | entirely within one file body; never crosses a file or metadata boundary |
| Sampling | seeded systematic on the file-body axis, `Δ ≥ T`; line-aligned realized spans are checked pairwise disjoint |
| Position/context grids `q_stream`,`q_source`,`c` | same frozen edges {512 B,1,2,4,8,16,32,64,128,256,512,1024 KiB} on their declared byte axes |
| Fit / validation / diagnostic rungs | fit 512 B–32 KiB; fixed holdout 64/128/256 KiB; 512/1024 KiB nonparametric diagnostics only and excluded from the 10× exhaustion and range/common-support gates |
| Context eligibility | exact, after joint tokenization, per model token limit |
| Attention eligibility | effective causal reach at each rung; native and extended adapters separate |
| A1 context regimes | with-file and cross-file-only (skip/backfill to the same `c`) are both required; claim must be stable across them |
| A1 full-block secondary | reported only at `c ≥ 4T = 16 KiB`; descriptive only, never fitted, and exempt from the §9.2 range rule; primary first-512-byte horizon remains at every eligible rung |
| Orderings | seeded shuffled (cross-language headline), build-resolved topological, reverse-topological sensitivity |
| Fixed budget `B*` (Arms B, D) | 128 KiB |
| Arm B origins | event-level first-parent; six-month snapshots bin/display only |
| Arm B target units | contiguous added-line runs, split at `T`; minimum 256 source bytes; exact cap/subsample frozen at P0 |
| Arm B preconditions | ≥3 repos/language, ≥5 origins/repo, full rank, \|residual size–date r\| < 0.8, VIF < 5 |
| Denominators | bits per source byte primary; bits per codepoint sensitivity; AST/semantic within-language only |
| Bin floor | ≥20 eligible blocks from ≥10 distinct files |
| Cell floor | ≥100 eligible blocks from ≥30 distinct files |
| Planned sample | 200 A0 origins and 200 A1 shared targets per repository (sizing only, not cell floors) |
| Min contiguous fit range | 2 decades, at eligible sample size |
| Headline range robustness | ≥2 decades after removing 512-B rung and maximum rung ≥10× cell median same-file exhaustion |
| Locked ladder scope | the 3-family × 3-size ladder is descriptive/checkpoint-specific; model-specific K1 headlines remain possible, but K3 and every pooled claim are unevaluable unless ≥3 families independently pass K1 |
| Crossover source | posterior interpolation of adjacent nonparametric common-rung differences; ≥95% of draws must contain exactly one measured-support crossing; parametric crossing is sensitivity only |
| ROPE `δ_β` | 0.02, frozen; straddling ⇒ indeterminate |
| A0/A1 slope-equivalence ROPE | `β_position_stream−β_paired_with_file ∈ [−0.02,0.02]` by 95% interval |
| Stops and claim limiters | K1–K6 classifications exactly as written in §10 |

The P0 constants artifact also contains values that cannot safely remain prose:
the systematic-sample seed and exact `Δ` formula; metadata-header format;
comment/blank and near-duplicate thresholds; exact checkpoint/tokenizer/config
ledger and special-token adapters; model cutoff evidence/unknown policy;
bootstrap seeds; nonlinear likelihood, priors, fit/holdout split, region
construction, and optimizer/convergence gates. A missing value is a P0 failure,
not discretion delegated to the outcome consumer.

---

## 15. Reuse of this repository

1. **`analyze_v2.py`** — log bins (`:39`), bootstrap (`:89-117`), three
   functional forms with the gated holdout fit (`:120-190`). Reuse the fitting
   core but replace its `EDGES` and `HOLDOUT_SPLIT=8192` with §9.2's explicit
   direct-study grid and fit/holdout/diagnostic partition; replace
   window/document floors with §9.1 block/file floors and add the §9.3
   hierarchical layer.
2. **`prep_streams.py`** — Kahn ordering (`:228-272`), shuffle/seed arms
   (`:13-16`), git first-add dates (`:100-207`), and regex import proposals
   (`:219-224`). Replace proposal edges with the §3.2 environment-resolved
   graph before using a topological label; extend with file-body block
   sampling, suffix-nested contexts, reverse ordering, and skip-and-backfill.
3. **`prepare_longitudinal_inventory.py`** — first-parent walk, tree mass,
   toolchain drift; supplies Arm B's event and size series.
4. **Corpus lock trio** — `fetch_corpora.sh` (full-history clones),
   `corpus_lock.py` (write/checkout, retains full history for contamination
   dating), `corpora_lock.json` (14 repositories pinned at exact SHAs).
5. **`extract_lean.py`, `extract_python.py`, `v2b_lean_boundaries.py`** —
   declaration spans and parser-witnessed boundaries.
6. **`v2b_common.py`, `provenance.py`, `preflight_check.py`, `slurm/*.sbatch`**
   — write-once artifacts, sorted-JSON nested self-hashes, source-clean gates,
   requeue-safe jobs, and the recompute-never-trust discipline of §12.3.
7. **`eval_incontext.py`** — scoring loop and meta discipline.
8. **A6 / `prepare_v2b_neardup.py`** — near-duplicate eligibility, extended
   cross-repository.

Panel expansion adds `CORPORA` entries in `prep_streams.py:47-57` (five stream
configs exist today: `physlib`, `mathlib`, `qutip`, `sympy`, `geant4`); it does
not require re-locking repositories.

---

## 16. What this study cannot answer

It does not measure design quality, security, or maintainability, and `L∞` is a
finite-model extrapolated asymptote, not intrinsic language entropy. It does not
establish that formality causes predictability: Arms A and B are observational
across languages, and Arm C manipulates representation under a matched
specification on a small non-representative sample. It says nothing about
codebases larger than the largest measured, and no crossover is extrapolated
beyond measured common support. Every quantity is model-relative: an ordering is
a statement about specific models, on specific repositories, at a specific date.

Positioning relative to existing literature is deferred to a dedicated search at
write-up time; no novelty claim is made here.
