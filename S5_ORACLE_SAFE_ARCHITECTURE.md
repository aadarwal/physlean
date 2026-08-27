# S5 oracle-safe kernel-continuation architecture

Status: **prospective engineering/design note; not preregistered, not a
production verifier, and not outcome evidence.** This note records the narrow
architecture that survived the S5 oracle audit. It must be reviewed and folded
into `DESIGN_V2.md` / `PREREG.md` before any Lean behavioral generation.

## 1. Why the prior S5 process is not a valid instrument

Generated Lean bodies may execute metaprograms. In particular they can read the
current `FileMap`, use `IO.FS`, or spawn processes. The previous candidate
process had three independent oracle channels:

1. the full original repository source was mounted read-only;
2. the full reconstruction was mounted read-only; and
3. the target elaborator's `FileMap.source` contained the immutable suffix.

Thus a completion could inspect the held-out original body or downstream test
oracle while being verified. Hiding only filesystem paths is insufficient:
`Lean.getFileMap` exposes the source already resident in the process. Truncating
the `FileMap` is also insufficient if the same process later receives the
suffix, because target code can install callbacks, extensions, background
tasks, or process-global state that observe it.

The minimum sound boundary is therefore an OS-process boundary. Candidate
syntax and immutable suffix bytes must never coexist in one process.

## 2. Scientific qualification

The process boundary changes the exact estimand. A fresh suffix process can
receive kernel declarations, but it cannot safely receive the candidate's
arbitrary `Command.State` or frontend extensions. Consequently the new metric
is:

> Does the generated body elaborate to the exact target kind/name and a
> baseline-equivalent kernel type, produce only independently replayable safe
> kernel declarations, and preserve the immutable suffix under a fresh,
> normalized kernel continuation?

It is **not** literal compilation of the reconstructed file under every
candidate-created frontend side effect. Candidate-added simp/instance
registrations, options, macros, equation/compiler metadata, `IO.Ref` state, and
background hooks are intentionally discarded. Baseline and candidate take the
same path; an original target whose suffix needs discarded effects is
prospectively `replay_feasible=false`.

Before generation, the preregistration must:

- name this the **oracle-safe kernel-body / normalized-continuation** estimand;
- make baseline replay feasibility an arm-independent pre-generation screen;
- freeze which constant kinds and header surfaces are supported;
- report feasibility attrition by corpus and target kind; and
- forbid describing the result as exact arbitrary frontend continuation.

If literal candidate extension effects are scientifically indispensable, this
architecture is insufficient. They would require a separately audited,
explicitly whitelisted extension codec; serializing `Command.State` is not an
acceptable shortcut.

## 3. Four fresh phases

Every phase is a fresh bubblewrap process with its own authenticated nonce and
durable GO handshake:

1. `baseline-target`
2. `baseline-suffix`
3. `candidate-target`
4. `candidate-suffix`

Notation for one branch:

- `P`: trusted bytes before the target command;
- `H`: trusted bytes through the committed body introducer/header boundary;
- `T`: branch source from byte zero through the retained target end;
- `S`: exact immutable original suffix;
- `E`: retained target end in the reconstructed branch.

Target phase inputs are `P`, `H`, and `T`. Candidate `T` contains the trusted
prefix/header and generated continuation only; it never contains the original
body or `S`. Baseline `T` contains the original target and no suffix.

Suffix phase inputs are:

- `P`;
- `suffix_view = H + mask(T[|H|:E]) + S`; and
- the canonical declaration bundle from the corresponding target phase.

`mask` preserves exact UTF-8 byte length, codepoint count, newline positions,
and whitespace. Each non-whitespace scalar is replaced by one canonical scalar
of the same UTF-8 width. The suffix `FileMap` therefore preserves trusted
prefix/header text and all branch-relative positions while containing neither
original nor generated body bytes.

The target phase rebuilds pre-target state from `P` only, proves that `T`
extends exact `H`, emits `phase-start`, waits for GO+EOF, then parses and
elaborates exactly one target command. The suffix phase independently rebuilds
the same pre-target state, validates/replays the bundle, initializes
`ModuleParserState` at `E` with `recovering=false, hasLeading=false`, and
elaborates `S` through terminal EOF. It never parses the candidate command.

The original repository path remains the logical filename in every phase, but
no source file exists at that path inside the target sandbox.

## 4. Canonical declaration bundle

Only tagged arrays with exact arity are accepted. The allowed rows are:

```text
["defn",  Name, [levelParams], TypeExpr, ValueExpr, ReducibilityHints]
["thm",   Name, [levelParams], TypeExpr, ValueExpr]
["opaque",Name, [levelParams], TypeExpr, ValueExpr]
```

No row represents an axiom, quotient, inductive, constructor, recursor,
unsafe/partial declaration, or frontend extension. Ordinary auxiliary names
need not be under the target namespace: the frozen S5 estimand already permits
different ordinary auxiliary sets. Freshness, closed terms, dependency checks,
and kernel replay are the trust boundary—not a naming heuristic.

The expression grammar covers only `bvar`, `sort`, `const`, `app`, `lam`,
`forall`, `let`, nat/string literals, and projection. It rejects free/meta
variables, universe metavariables, loose bound variables, undeclared universe
parameters, wrong arities, trailing fields, duplicate names, and noncanonical
re-encodings. Binder names and the `letE nondep` bit are preserved because a
trusted suffix can use named arguments and elaborator-visible binder data.
Metadata is stripped prospectively and symmetrically.

Before a bundle is accepted:

1. the exact target name is absent before and present exactly once after;
2. target kind is `thmInfo` for theorem/lemma or `defnInfo` for def;
3. target type refers only to pre-target constants;
4. every bundle name is unique and fresh;
5. every constant dependency is pre-target or another bundle member;
6. the dependency graph is acyclic/topologically replayable;
7. no new axiom, unsafe/partial declaration, `implemented_by` drift, or
   forbidden axiom closure exists;
8. strict decode then re-encode is byte/canonical-JSON equal;
9. declaration, byte, node, and depth caps are satisfied; and
10. the target type matches the bound baseline certificate by universe arity
    and `Kernel.isDefEq` in the immutable pre-target environment.

`Environment.replay` independently checks the complete new-constant map in the
producer. Its returned environment is not used for suffix elaboration because
it drops frontend extensions. In the fresh suffix `Command.State`, each decoded
declaration is independently kernel-checked and registered synchronously
through the stable public CoreM `Lean.addDecl` path, with `Elab.async=false`,
`debug.skipKernelTC=false`, and `debug.proofAsSorry=false`. It is deliberately
**not compiled**: the normalized continuation transports kernel declarations,
not candidate runtime IR or an executable extension surface. A baseline whose
suffix requires executable target code is therefore replay-ineligible. The
process then verifies that the exact replayed constant-name set equals the
bundle and that `implemented_by` state did not drift.

Trusted suffix declarations are elaborated and kernel-checked with
`compiler.postponeCompile=true` and a normalized noncomputable scope; the
verifier never flushes or executes postponed IR. This lets ordinary suffix
definitions/theorems refer to the replayed kernel target while keeping
candidate runtime code out of the suffix process. Commands that intrinsically
require executable target/meta code may therefore fail the symmetric baseline
screen and are reported as replay-ineligible.

An earlier audit suggestion to call `Kernel.Environment.addDecl` directly on a
Lean `Environment` is not type-correct on either pin: that API consumes a
kernel environment. The shared-source probe therefore uses public CoreM
`Lean.addDecl` plus independent `Environment.replay`.

## 5. Baseline certificate and eligibility

The original body must traverse the same target-bundle and fresh-suffix path.
A target is behavior-eligible only if both baseline phases finish cleanly. The
certificate binds at least:

- semantic-context, runtime, visibility, driver, and contract hashes;
- baseline target and suffix invocation/evidence hashes;
- canonical bundle hash;
- exact target name/kind and pre-target command count;
- level-parameter arity and canonical target-type expression;
- trusted header/prefix and original suffix hashes; and
- `status = replay-eligible`.

Candidate manifests embed or reference the exact validated certificate. Every
reader revalidates the baseline evidence and recomputes certificate equality;
no caller-supplied shape-valid certificate is trusted.

Baseline semantic/replay failure is arm-independent prospective ineligibility.
Baseline timeout, abnormal termination, or provenance/evidence failure is
`HARNESS-INVALID`, never silent exclusion. Candidate target or suffix semantic
failure is zero. Candidate termination after durable GO intent is immutable
zero and is never retried.

## 6. Mode-framed source transport

Source bytes are length-framed over stdin and are never stored in a
child-visible file:

```text
<nonce>\n
FRAME <role> <byte-length>\n
<exact bytes>\n
...
ENDFRAMES\n
```

After all branch-specific trusted validation, the driver emits and flushes a
nonce-authenticated `phase-start` and blocks. The host fsyncs the raw prefix and
GO intent, rechecks cap/deadline/child state, sends exact `GO:<nonce>\n`, and
closes stdin. The driver requires GO plus EOF, emits/flushed
`phase-go-accepted`, and only then begins outcome-bearing work.

Frame digests, bundle digest, target evidence, and suffix manifest are joined
outside the child. Deterministic manifests never contain the random nonce.

## 7. Per-row visibility projection

The child must not see the corpus root, `.git`, original/reconstructed source,
current-module artifacts, search-root contents, or the private attempt journal.
It sees only:

- the exact toolchain/runtime;
- the row's transitive imported `.olean`/related artifacts;
- required plugins/dynlibs and their loader closure;
- prospectively declared prefix resources; and
- empty directory skeletons needed to preserve logical paths.

Raw `lake query +Module:setup` output cannot be assumed transitive on both
pins. Lake 4.32's queried setup facet contains direct artifacts, whereas the
language-server path calls `Lake.setupServerModule`, whose
`setupEditedModule` invokes `fetchTransImportArts` on **both** frozen pins.
`V2BS5ExpandSetup.lean` uses that public path with an explicit bound toolchain
root and `BuildConfig.noBuild=true`; a missing artifact is a hard failure and
is never rebuilt. The helper is built as a pinned native `lean_exe` with
`supportInterpreter=true`, and the executable hash is part of the producer
contract. Its real three-module integration fixture proves that both
the direct and transitive `.olean` are present and that deleting one causes a
failure without recreation under Lean/Lake 4.32.0 and 4.33.0-rc2.

`noBuild=true` is not by itself a zero-write guarantee: Lake may emit
`*.nobuild` trace state, and workspace loading may materialize a missing
manifest dependency. Production therefore requires an already complete
manifest/dependency tree, disables network/cache, mounts the entire frozen
workspace/dependency closure read-only, and verifies its hashes before and
after the helper. Any attempted write or missing dependency invalidates the
setup run.

The raw setup JSON remains immutable evidence. The producer normalizes only
for visibility derivation: 4.32 encodes each `importArts` value as a flat path
array, while 4.33 encodes artifact groups as nested path arrays. Both forms
must pass exact-key/type validation and flatten to the same role-indexed path
set. Every normalized path/hash must join the frozen broad setup-index
artifact table. Production then proves the per-row subset under bubblewrap
with corpus search-root contents absent and no fallback lookup. Prefix resource
discovery may be used only to propose a frozen allowlist; a
candidate-triggered lazy load must fail rather than widen visibility.

### 7.1 Exact-file visibility producer

`v2b_s5_visibility.py` is the first bounded production implementation of this
projection.  Its `v2b_s5_visibility_v1` artifact content-binds the target
module/source, raw expanded setup bytes and normalized semantics, toolchain pin
and Lean executable, native expansion helper, visibility-producer bytes,
explicit runtime closure, frozen broad setup index, and every projected file.
The child-facing table contains only exact regular files and content-bound
internal symlinks.  It contains no search directory, corpus root, source, setup
JSON, helper, producer, index, or closure evidence.  The mount policy explicitly
forbids binding the workspace, toolchain, or search roots; source remains framed
stdin data.

There is no sound way to infer an omitted transitive import from a
potentially-truncated `ModuleSetup` object alone.  The narrow fail-closed choice
is therefore to require a separately frozen `v2b_s5_import_closure_v1`
artifact, bound to the exact module and source hash, and require exact equality
between that module set and `importArts`.  Each member must contribute at least
one `.olean` artifact.  Direct `imports`, when present, must be a subset.  The
real helper integration independently checks the known direct-plus-transitive
three-module fixture on both pins.  Production still needs a prospective
closure-artifact producer whose output is joined to the frozen dependency
graph; deriving that artifact from the same untrusted setup would defeat this
check.

Both Lake encodings are accepted only in their homogeneous form: a 4.32 flat
path list or a 4.33 list of nonempty path groups.  Empty/mixed groups,
duplicates, relative/noncanonical paths, missing files, current-module
artifacts, source files, and paths outside the workspace `.lake` or exact
toolchain root fail closed.  Every setup/runtime path and safe symlink target
must match the path and live content hash in the frozen broad setup index.
Safe symlinks bind both their literal link text and resolved regular target;
link chains, `..`, unindexed links/targets, and root escapes are rejected.

The producer intentionally does not claim that an explicit runtime file list
is a sufficient dynamic-loader closure or that individual mounts work under
Engaging's bubblewrap/kernel configuration.  Those remain empirical release
gates: generate the graph-derived closure, trace/freeze the native driver and
loader closure, instantiate only the manifest allowlist in bubblewrap, prove
all broad lookup roots are absent, and run positive/negative probes on both
pinned cluster toolchains.  The live validator deterministically rebuilds the
entire manifest from its content-bound inputs before a launcher may consume it.

### 7.2 Bounded four-phase execution layer

`run_v2b_s5_four_phase.py` is the next bounded implementation layer. Its
strict, hash-only plan joins the exact source decomposition, supplied offsets,
visibility contract, pinned Lean and driver, runner bytes, sandbox contract,
and resource policy. It derives a separate exact-key manifest for each of the
four phases and launches one fresh process at a time. Candidate target frames
end exactly at `candidateRetainedEndByte`; candidate suffix frames contain only
the trusted prefix/header, byte-position-preserving masked body, immutable
suffix, and strictly revalidated kernel bundle. Plan, visibility, and private
attempt evidence are never mounted in a child.

Each attempt records an immutable open record and exact frame hashes before
launch. After a nonce-authenticated `phase-start`, the host fsyncs the raw
start prefix and GO intent before writing `GO:<nonce>` and closing stdin. Raw
stdout/stderr, any authenticated GO acknowledgement, and a terminal record are
then written with complete hash joins. Re-entry reconstructs phase inputs and
classification from those bytes rather than trusting the summary. A
GO-committed partial or tampered attempt fails closed and is never retried; at
most two pre-GO attempts are retained. The target driver additionally parses
through terminal EOF after the single target command, so a bad retained range
cannot hide a second generated command that suffix mode would skip.

The default backend requires the content-bound canonical `/usr/bin/bwrap`, an
empty root, fresh namespaces, no `/proc` or network, private `/tmp`, and exact
file mounts from `v2b_s5_visibility_v1`. The only other backend is named
`none-test-only`, is opt-in at the Python API, receives a scrubbed environment,
and exists solely to execute the real Lean protocol on non-Linux development
hosts. Its filesystem results are not oracle-isolation evidence.

This layer is still **not a production-scientific verifier**. In particular:

- baseline phases are currently joined to a candidate-bearing invocation,
  rather than emitted once as an arm-independent prospective certificate;
- the supplied target/header/end offsets still need a direct hash join to the
  frozen S4 extraction artifact rather than only source-byte reconstruction;
- the host uses exact canonical target-type equality. Definitionally equal
  but nonidentical inferred types require the frozen `Kernel.isDefEq`
  certificate path before production;
- the four prefix rebuilds bind their inputs and prior-command count but do
  not yet emit a canonical pre-target environment/semantic-context digest;
- a scheduler/host crash after durable GO fails closed on the partial attempt,
  but there is not yet a separately audited recovery adjudicator that turns a
  partial candidate attempt into an immutable zero;
- live projection/driver/runtime hashes are checked before and after each
  phase, but content-addressed staging or open-fd mounts are still required to
  eliminate the remaining pathname swap interval completely; and
- the graph-derived Linux ELF loader closure, real Engaging bubblewrap probes,
  and exact application of expanded `ModuleSetup` options/plugins remain
  release gates.

## 8. Release gates

No behavioral S5 outcome is scientific until all are true:

- exact ModuleSetup integration works under frozen mathlib/Batteries and
  PhysLib states;
- baseline certificate/type equality is integrated;
- all four phases have production manifests, journals, evidence, resume rules,
  and complete-artifact joins;
- strict bundle byte/node/depth/constant caps are frozen;
- target/header/range joins bind S4 exactly;
- per-row expanded visibility works under real Engaging bubblewrap;
- both Lean 4.32.0 and 4.33.0-rc2 compile and pass integration tests;
- adversarial tests cover FileMap, logical-path reads, corpus walks, current
  `.olean`, `/proc`/fd access, inherited stdout forgery, delayed children,
  target extension leakage, malformed bundles, kill/restart boundaries, and
  baseline symmetry; and
- the prospective estimand amendment is committed before generation.

The independent paired NLL experiment never executes generated Lean and does
not depend on these S5 release gates.
