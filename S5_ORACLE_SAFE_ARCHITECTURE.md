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
