/-
Oracle-safe two-process architecture probe for the V2-b behavioral S5 verifier.

This is NOT the production verifier.  It prototypes the architecture required
by the S5 oracle audit so it can be ported into `V2BVerifyCommand.lean` after
independent review.  Three properties are structural here, not incidental:

  1. VIEW-LIMITED TARGET PROCESS.  The process that elaborates generated syntax
     receives ONLY `prefixView` (bytes [0, targetStart)) and `targetView`
     (bytes [0, retainedEnd)).  The original declaration body, the original
     module, and the immutable suffix are never in its address space, so
     neither the candidate body NOR any trusted prefix metaprogram can read or
     persist future bytes.  Sources arrive as mode-framed stdin payloads under
     a LOGICAL repository filename that need not exist, so `include_str` and
     `IO.FS.readFile` cannot recover them either.

  2. NORMALIZED CONSTANT BUNDLE.  After its kernel/trust/type checks the target
     process emits a strict canonical bundle: definitions, theorems and opaque
     constants only, every `Expr` closed and mdata-stripped with binder names
     normalized away, no axioms, no unsafe/partial, and no environment
     extension state.  A bundle is the ONLY thing that crosses the process
     boundary.

  3. FRESH SUFFIX PROCESS.  A second process rebuilds the exact pre-target
     state from `prefixView` alone, strictly decodes the bundle, replays it in
     dependency order through `Lean.addDecl` (so the kernel rechecks every
     constant against the pre-target environment), seeks the parser to
     `retainedEnd`, and only then elaborates the trusted suffix.  It never
     imports candidate `Command.State`, never re-parses candidate syntax, and
     receives no candidate attribute, instance, macro, or background state.

Baseline verification uses the identical bundle + fresh-suffix path, so the
reference body is measured by the same instrument as every candidate.
-/
import Lean

open Lean

namespace V2BOracleSafeProbe

def MANIFEST_SCHEMA := "v2b_lean_oracle_probe_manifest_v2"
def OUTPUT_SCHEMA := "v2b_lean_oracle_probe_result_v2"
def BUNDLE_SCHEMA := "v2b_lean_constant_bundle_v1"
def OUTPUT_MARKER_PREFIX := "@@V2B_ORACLE_PROBE:"
def OUTPUT_MARKER_SUFFIX := "@@"
def TARGET_FRAME_ROLES : Array String := #["prefix", "header", "target"]
def SUFFIX_FRAME_ROLES : Array String := #["prefix", "header", "suffix", "bundle"]

structure Manifest where
  schema : String
  mode : String
  logicalFile : String
  targetName : String
  targetKind : String
  targetStartByte : Nat
  headerEndByte : Nat
  retainedEndByte : Nat
  deriving FromJson

structure Frame where
  role : String
  payload : String

def hard {α : Type} (message : String) : IO α :=
  throw <| IO.userError s!"V2B probe trusted-input error: {message}"

def rawPos (byteIdx : Nat) : String.Pos.Raw := ⟨byteIdx⟩

def requireRawPosition (label : String) (source : String)
    (byteIdx : Nat) : IO Unit := do
  unless (rawPos byteIdx).isValid source do
    hard s!"{label}={byteIdx} is not a valid UTF-8 byte position"

def slice (source : String) (startByte endByte : Nat) : String :=
  String.Pos.Raw.extract source (rawPos startByte) (rawPos endByte)

def allMessageCount (messages : MessageLog) : Nat :=
  messages.reportedPlusUnreported.size

def emit (channelNonce : String) (value : Json) : IO Unit := do
  IO.println s!"{OUTPUT_MARKER_PREFIX}{channelNonce}{OUTPUT_MARKER_SUFFIX}{Json.compress value}"
  (← IO.getStdout).flush

/-! ## Mode-framed in-memory source channel -/

partial def readExact (stream : IO.FS.Stream) (n : Nat)
    (acc : ByteArray := .empty) : IO ByteArray := do
  if acc.size >= n then
    return acc
  let chunk ← stream.read (USize.ofNat (n - acc.size))
  if chunk.isEmpty then
    hard "channel closed inside a source frame"
  readExact stream n (acc ++ chunk)

partial def readFrames (stream : IO.FS.Stream) (allowed : Array String)
    (acc : Array Frame := #[]) : IO (Array Frame) := do
  let line ← stream.getLine
  let line := line.trimAsciiEnd.toString
  if line == "ENDFRAMES" then
    return acc
  match line.splitOn " " with
  | ["FRAME", role, lengthText] =>
      unless allowed.contains role do
        hard s!"frame role {role} is not permitted in this mode"
      if acc.any (fun frame => frame.role == role) then
        hard s!"duplicate source frame role {role}"
      let some byteLen := lengthText.toNat?
        | hard s!"frame length {lengthText} is not a natural number"
      let payload ← readExact stream byteLen
      let terminator ← stream.read 1
      unless terminator == "\n".toUTF8 do
        hard "source frame is not newline-terminated at its declared length"
      let some text := String.fromUTF8? payload
        | hard "source frame payload is not valid UTF-8"
      unless text.utf8ByteSize == byteLen do
        hard "source frame payload length disagrees with its header"
      readFrames stream allowed (acc.push { role, payload := text })
  | _ => hard s!"malformed source frame header: {line}"

def frameOf (frames : Array Frame) (role : String) : IO String := do
  match frames.find? (fun frame => frame.role == role) with
  | some frame => pure frame.payload
  | none => hard s!"missing required source frame {role}"

def awaitAuthorization (stream : IO.FS.Stream)
    (channelNonce : String) : IO Unit := do
  let line ← stream.getLine
  unless line == s!"GO:{channelNonce}\n" do
    hard "channel start authorization is missing or malformed"
  let trailing ← stream.read 1
  unless trailing.isEmpty do
    hard "channel stdin must end immediately after start authorization"

/-! ## Canonical constant bundle codec -/

def nameToJson (name : Name) : Json :=
  let rec go : Name → Array Json
    | .anonymous => #[]
    | .str parent part => (go parent).push (.arr #[toJson "s", toJson part])
    | .num parent index => (go parent).push (.arr #[toJson "n", toJson index])
  .arr (go name)

def nameFromJson (value : Json) : Except String Name := do
  let parts ← value.getArr?
  let mut name := Name.anonymous
  for part in parts do
    let row ← part.getArr?
    unless row.size == 2 do throw "malformed name component"
    let tag ← row[0]!.getStr?
    if tag == "s" then
      name := .str name (← row[1]!.getStr?)
    else if tag == "n" then
      name := .num name (← row[1]!.getNat?)
    else
      throw s!"unknown name component tag {tag}"
  pure name

partial def levelToJson : Level → Except String Json
  | .zero => pure <| .arr #[toJson "zero"]
  | .succ l => do pure <| .arr #[toJson "succ", ← levelToJson l]
  | .max a b => do pure <| .arr #[toJson "max", ← levelToJson a, ← levelToJson b]
  | .imax a b => do pure <| .arr #[toJson "imax", ← levelToJson a, ← levelToJson b]
  | .param n => pure <| .arr #[toJson "param", nameToJson n]
  | .mvar _ => throw "level metavariable in a bundled constant"

partial def levelFromJson : Json → Except String Level
  | .arr #[.str "zero"] => pure .zero
  | .arr #[.str "succ", level] => do pure <| .succ (← levelFromJson level)
  | .arr #[.str "max", left, right] => do
      pure <| .max (← levelFromJson left) (← levelFromJson right)
  | .arr #[.str "imax", left, right] => do
      pure <| .imax (← levelFromJson left) (← levelFromJson right)
  | .arr #[.str "param", name] => do pure <| .param (← nameFromJson name)
  | value => throw s!"malformed or unknown level node: {value}"

def binderInfoToNat : BinderInfo → Nat
  | .default => 0
  | .implicit => 1
  | .strictImplicit => 2
  | .instImplicit => 3

def binderInfoFromNat : Nat → Except String BinderInfo
  | 0 => pure .default
  | 1 => pure .implicit
  | 2 => pure .strictImplicit
  | 3 => pure .instImplicit
  | n => throw s!"unknown binder info {n}"

/-- Canonical encoding: mdata stripped, binder names normalized away, and any
free/meta variable rejected outright, so a bundle is closed by construction. -/
partial def exprToJson : Expr → Except String Json
  | .bvar index => pure <| .arr #[toJson "bvar", toJson index]
  | .sort level => do pure <| .arr #[toJson "sort", ← levelToJson level]
  | .const name levels => do
      let encoded ← levels.mapM levelToJson
      pure <| .arr #[toJson "const", nameToJson name, .arr encoded.toArray]
  | .app fn arg => do
      pure <| .arr #[toJson "app", ← exprToJson fn, ← exprToJson arg]
  | .lam name type body info => do
      pure <| .arr #[toJson "lam", nameToJson name, ← exprToJson type,
                     ← exprToJson body, toJson (binderInfoToNat info)]
  | .forallE name type body info => do
      pure <| .arr #[toJson "forall", nameToJson name, ← exprToJson type,
                     ← exprToJson body, toJson (binderInfoToNat info)]
  | .letE name type value body nondependent => do
      pure <| .arr #[toJson "let", nameToJson name, ← exprToJson type,
                     ← exprToJson value, ← exprToJson body,
                     toJson nondependent]
  | .lit (.natVal n) => pure <| .arr #[toJson "lit", toJson "nat", toJson n]
  | .lit (.strVal s) => pure <| .arr #[toJson "lit", toJson "str", toJson s]
  | .mdata _ inner => exprToJson inner
  | .proj typeName index struct => do
      pure <| .arr #[toJson "proj", nameToJson typeName, toJson index,
                     ← exprToJson struct]
  | .fvar _ => throw "free variable in a bundled constant"
  | .mvar _ => throw "metavariable in a bundled constant"

partial def exprFromJson : Json → Except String Expr
  | .arr #[.str "bvar", index] => do pure <| .bvar (← index.getNat?)
  | .arr #[.str "sort", level] => do pure <| .sort (← levelFromJson level)
  | .arr #[.str "const", name, .arr levels] => do
      pure <| .const (← nameFromJson name)
        (← levels.toList.mapM levelFromJson)
  | .arr #[.str "app", fn, argument] => do
      pure <| .app (← exprFromJson fn) (← exprFromJson argument)
  | .arr #[.str "lam", name, type, body, info] => do
      pure <| .lam (← nameFromJson name) (← exprFromJson type)
        (← exprFromJson body) (← binderInfoFromNat (← info.getNat?))
  | .arr #[.str "forall", name, type, body, info] => do
      pure <| .forallE (← nameFromJson name) (← exprFromJson type)
        (← exprFromJson body) (← binderInfoFromNat (← info.getNat?))
  | .arr #[.str "let", name, type, assigned, body, nondependent] => do
      pure <| .letE (← nameFromJson name) (← exprFromJson type)
        (← exprFromJson assigned) (← exprFromJson body)
        (← fromJson? nondependent : Bool)
  | .arr #[.str "lit", .str "nat", value] => do
      pure <| .lit (.natVal (← value.getNat?))
  | .arr #[.str "lit", .str "str", .str value] =>
      pure <| .lit (.strVal value)
  | .arr #[.str "proj", typeName, index, struct] => do
      pure <| .proj (← nameFromJson typeName) (← index.getNat?)
        (← exprFromJson struct)
  | value => throw s!"malformed or unknown expression node: {value}"

partial def exprConstantNames : Expr → Array Name
  | .bvar _ | .fvar _ | .mvar _ | .sort _ | .lit _ => #[]
  | .const name _ => #[name]
  | .app fn argument => exprConstantNames fn ++ exprConstantNames argument
  | .lam _ type body _ | .forallE _ type body _ =>
      exprConstantNames type ++ exprConstantNames body
  | .letE _ type value body _ =>
      exprConstantNames type ++ exprConstantNames value ++ exprConstantNames body
  | .mdata _ value => exprConstantNames value
  | .proj typeName _ value => #[typeName] ++ exprConstantNames value

partial def levelParamsAllowed (allowed : NameSet) : Level → Bool
  | .zero => true
  | .succ level => levelParamsAllowed allowed level
  | .max left right | .imax left right =>
      levelParamsAllowed allowed left && levelParamsAllowed allowed right
  | .param name => allowed.contains name
  | .mvar _ => false

partial def exprLevelParamsAllowed (allowed : NameSet) : Expr → Bool
  | .bvar _ | .lit _ => true
  | .fvar _ | .mvar _ => false
  | .sort level => levelParamsAllowed allowed level
  | .const _ levels => levels.all (levelParamsAllowed allowed)
  | .app fn argument =>
      exprLevelParamsAllowed allowed fn && exprLevelParamsAllowed allowed argument
  | .lam _ type body _ | .forallE _ type body _ =>
      exprLevelParamsAllowed allowed type && exprLevelParamsAllowed allowed body
  | .letE _ type value body _ =>
      exprLevelParamsAllowed allowed type &&
        exprLevelParamsAllowed allowed value && exprLevelParamsAllowed allowed body
  | .mdata _ value => exprLevelParamsAllowed allowed value
  | .proj _ _ value => exprLevelParamsAllowed allowed value

def hintsToJson : ReducibilityHints → Json
  | .abbrev => .arr #[toJson "abbrev"]
  | .opaque => .arr #[toJson "opaque"]
  | .regular height => .arr #[toJson "regular", toJson height.toNat]

def hintsFromJson : Json → Except String ReducibilityHints
  | .arr #[.str "abbrev"] => pure .abbrev
  | .arr #[.str "opaque"] => pure .opaque
  | .arr #[.str "regular", height] => do
      let value ← height.getNat?
      if value > 4294967295 then
        throw "regular reducibility height exceeds UInt32"
      pure <| .regular (UInt32.ofNat value)
  | value => throw s!"malformed or unknown reducibility hint: {value}"

/-- A bundled constant: only value-carrying, kernel-checkable kinds. -/
structure BundledConstant where
  kind : String
  name : Name
  levelParams : List Name
  type : Expr
  value : Expr
  hints : ReducibilityHints

def validateBundledConstant (constant : BundledConstant) : Except String Unit := do
  if constant.name.isAnonymous then
    throw "bundled constant has an anonymous name"
  let mut levelNames : NameSet := {}
  for name in constant.levelParams do
    if name.isAnonymous || levelNames.contains name then
      throw "bundled constant has duplicate/anonymous universe parameters"
    levelNames := levelNames.insert name
  if constant.type.hasMVar || constant.type.hasFVar ||
      constant.type.hasLooseBVars || constant.value.hasMVar ||
      constant.value.hasFVar || constant.value.hasLooseBVars then
    throw "bundled constant contains free/meta/loose-bound variables"
  unless exprLevelParamsAllowed levelNames constant.type &&
      exprLevelParamsAllowed levelNames constant.value do
    throw "bundled constant uses an undeclared universe parameter"

def bundledToJson (constant : BundledConstant) : Except String Json := do
  let levelParams := .arr ((constant.levelParams.map nameToJson).toArray)
  match constant.kind with
  | "defn" => pure <| .arr #[.str "defn", nameToJson constant.name,
      levelParams, ← exprToJson constant.type, ← exprToJson constant.value,
      hintsToJson constant.hints]
  | "thm" => pure <| .arr #[.str "thm", nameToJson constant.name,
      levelParams, ← exprToJson constant.type, ← exprToJson constant.value]
  | "opaque" => pure <| .arr #[.str "opaque", nameToJson constant.name,
      levelParams, ← exprToJson constant.type, ← exprToJson constant.value]
  | kind => throw s!"bundled constant kind {kind} is not permitted"

def bundledFromJson : Json → Except String BundledConstant
  | .arr #[.str "defn", name, .arr levelParams, type, value, hints] => do
      let decodedName ← nameFromJson name
      let decodedParams ← levelParams.toList.mapM nameFromJson
      let decodedType ← exprFromJson type
      let decodedValue ← exprFromJson value
      let decodedHints ← hintsFromJson hints
      pure <| BundledConstant.mk "defn" decodedName decodedParams
        decodedType decodedValue decodedHints
  | .arr #[.str "thm", name, .arr levelParams, type, value] => do
      let decodedName ← nameFromJson name
      let decodedParams ← levelParams.toList.mapM nameFromJson
      let decodedType ← exprFromJson type
      let decodedValue ← exprFromJson value
      pure <| BundledConstant.mk "thm" decodedName decodedParams
        decodedType decodedValue .opaque
  | .arr #[.str "opaque", name, .arr levelParams, type, value] => do
      let decodedName ← nameFromJson name
      let decodedParams ← levelParams.toList.mapM nameFromJson
      let decodedType ← exprFromJson type
      let decodedValue ← exprFromJson value
      pure <| BundledConstant.mk "opaque" decodedName decodedParams
        decodedType decodedValue .opaque
  | value => throw s!"malformed or forbidden bundled constant: {value}"

def bundledDeclaration (constant : BundledConstant) : Declaration :=
  match constant.kind with
  | "thm" => .thmDecl { name := constant.name,
                        levelParams := constant.levelParams,
                        type := constant.type, value := constant.value }
  | "opaque" => .opaqueDecl { name := constant.name,
                              levelParams := constant.levelParams,
                              type := constant.type, value := constant.value,
                              isUnsafe := false }
  | _ => .defnDecl { name := constant.name,
                     levelParams := constant.levelParams,
                     type := constant.type, value := constant.value,
                     hints := constant.hints, safety := .safe }

/-! ## Shared frontend state -/

def hardenState (state : Elab.Command.State) : Elab.Command.State :=
  match state.scopes with
  | [] => state
  | scope :: scopes =>
      let opts := debug.skipKernelTC.set scope.opts false
      let opts := opts.setBool `debug.proofAsSorry false
      { state with scopes := { scope with opts } :: scopes }

/-- Suffix checking is a kernel/elaboration continuation, not a code-generation
continuation.  Postpone ordinary runtime compilation so trusted suffix
definitions can refer to replayed kernel declarations without installing or
executing candidate IR in this process. -/
def hardenSuffixState (state : Elab.Command.State) : Elab.Command.State :=
  match (hardenState state).scopes with
  | [] => hardenState state
  | scope :: scopes =>
      let opts := Compiler.compiler.postponeCompile.set scope.opts true
      { hardenState state with
        scopes := { scope with opts, isNoncomputable := true } :: scopes }

def parserContext (state : Elab.Command.State) : Parser.ParserModuleContext :=
  let scope := state.scopes.head!
  { env := state.env
    options := (debug.skipKernelTC.set scope.opts false).setBool
      `debug.proofAsSorry false
    currNamespace := scope.currNamespace
    openDecls := scope.openDecls }

def initialOptions : Options :=
  let options := Lean.internal.cmdlineSnapshots.setIfNotSet ({} : Options) true
  let options := Elab.async.setIfNotSet options true
  let options := debug.skipKernelTC.set options false
  options.setBool `debug.proofAsSorry false

def settleSnapshotTasks (state : Elab.Command.State) :
    IO (Except Unit Elab.Command.State) := do
  for task in state.snapshotTasks do
    let tree := task.get
    for snapshot in tree.getAll do
      if snapshot.diagnostics.msgLog.hasErrors then
        return .error ()
  let _ := state.env.checked.get
  pure <| .ok { state with snapshotTasks := #[] }

def elaborateTrustedCommand (inputCtx : Parser.InputContext)
    (cmdPos : String.Pos.Raw) (stx : Syntax)
    (state : Elab.Command.State) : IO Elab.Command.State := do
  let state := hardenState { state with messages := {} }
  let context : Elab.Command.Context := {
    cmdPos
    fileName := inputCtx.fileName
    fileMap := inputCtx.fileMap
    snap? := none
    cancelTk? := none
  }
  let (_, result) ← IO.FS.withIsolatedStreams (isolateStderr := true) <|
    EIO.toIO' <|
      ((Elab.Command.elabCommandTopLevel stx #[]) context).run state
  match result with
  | Except.error exception =>
      hard s!"trusted command raised: {← exception.toMessageData.toString}"
  | Except.ok (_, nextState) =>
      if nextState.messages.hasErrors then
        let mut detail := ""
        for message in nextState.messages.reportedPlusUnreported do
          detail := detail ++ (← message.toString) ++ " | "
        hard s!"a trusted command does not elaborate: {detail}"
      else
        match ← settleSnapshotTasks nextState with
        | .error _ => hard "a trusted command has an asynchronous error"
        | .ok settled => pure <| hardenState { settled with messages := {} }

structure Prepared where
  parserState : Parser.ModuleParserState
  commandState : Elab.Command.State
  nPriorCommands : Nat

/-- Build the pre-target state from `prefixView` ONLY.  The FileMap here stops
at `targetStart`, so a trusted prefix metaprogram cannot read or persist the
body or the suffix: those bytes are not in this process. -/
def preparePrefix (prefixView logicalFile : String)
    (options : Options) : IO (Parser.InputContext × Prepared) := do
  let inputCtx := Parser.mkInputContext prefixView logicalFile
  let (header, headerState, headerMessages) ← Parser.parseHeader inputCtx
  if allMessageCount headerMessages != 0 || headerState.recovering
      || header.raw.hasMissing then
    hard "trusted prefix header required parser recovery"
  let (environment, importMessages) ←
    Elab.processHeader header options {} inputCtx
      (leakEnv := true) (mainModule := `V2BOracleProbe)
  if importMessages.hasErrors then
    hard "trusted prefix imports did not load"
  let mut state := hardenState (Elab.Command.mkState environment {} options)
  let mut parserState := headerState
  let mut nPrior := 0
  repeat
    if parserState.pos.byteIdx >= prefixView.utf8ByteSize then
      break
    let cmdPos := parserState.pos
    let (stx, nextParserState, parseMessages) :=
      Parser.parseCommand inputCtx (parserContext state) parserState {}
    if allMessageCount parseMessages != 0 || nextParserState.recovering
        || stx.hasMissing then
      hard "trusted prefix required parser recovery"
    if Parser.isTerminalCommand stx then
      break
    state ← elaborateTrustedCommand inputCtx cmdPos stx state
    parserState := nextParserState
    nPrior := nPrior + 1
  pure (inputCtx, { parserState, commandState := state,
                    nPriorCommands := nPrior })

/-! ## Target mode -/

def newConstantNames (before after : Environment) : Array Name :=
  after.checked.get.constants.foldStage2 (fun names name _ =>
    if (before.find? name).isSome then names else names.push name) #[]

def newConstantMap (after : Environment) (names : Array Name) :
    Std.HashMap Name ConstantInfo := Id.run do
  let mut result : Std.HashMap Name ConstantInfo := {}
  for name in names do
    if let some info := after.find? name then
      result := result.insert name info
  return result

def implementedByStateEqual (before after : Environment) : Bool :=
  let beforeMap := (Lean.Compiler.implementedByAttr.ext.getState before).2
  let afterMap := (Lean.Compiler.implementedByAttr.ext.getState after).2
  beforeMap.foldl (fun equal name value =>
    equal && afterMap.find? name == some value) true &&
  afterMap.foldl (fun equal name value =>
    equal && beforeMap.find? name == some value) true

def forbiddenAxiom (name : Name) : Bool :=
  name == ``sorryAx || name == ``Lean.ofReduceBool ||
    name == ``Lean.ofReduceNat

partial def axiomClosure (env : Environment) (pending : List Name)
    (seen axioms : NameSet := {}) : NameSet :=
  match pending with
  | [] => axioms
  | name :: rest =>
      if seen.contains name then axiomClosure env rest seen axioms
      else
        let seen := seen.insert name
        match env.find? name with
        | none => axiomClosure env rest seen axioms
        | some info =>
            let axioms := match info with
              | .axiomInfo _ => axioms.insert name
              | _ => axioms
            let valueDependencies := match info.value? with
              | some value => exprConstantNames value
              | none => #[]
            let dependencies :=
              exprConstantNames info.type ++ valueDependencies
            axiomClosure env (dependencies.toList ++ rest) seen axioms

/-- Strict bundle construction: only value-carrying safe kinds, no axioms, no
unsafe/partial, no `implemented_by`, and every expression closed.  Ordinary
auxiliary names may differ from the baseline, as in the frozen S5 estimand;
freshness and kernel replay, not a naming heuristic, are the trust boundary. -/
def buildBundle (before after : Environment) (targetName : Name)
    (targetKind : String) : IO (Except String (Array BundledConstant)) := do
  let names := newConstantNames before after
  unless implementedByStateEqual before after do
    return .error "implemented-by"
  unless names.any (fun name => name == targetName) do
    return .error "target-name-drift"
  let mut bundle : Array BundledConstant := #[]
  for name in names do
    let some info := after.find? name
      | hard "environment delta name is not resolvable"
    if info.isUnsafe || info.isPartial then
      return .error "unsafe-or-partial"
    if (Lean.Compiler.getImplementedBy? after name).isSome then
      return .error "implemented-by"
    for ax in (axiomClosure after [name]).toArray do
      if forbiddenAxiom ax then
        return .error (if ax == ``sorryAx then "sorry" else "native-reflection")
      match before.find? ax with
      | some (.axiomInfo _) => pure ()
      | _ => return .error "new-axiom-dependency"
    let (kind, value, hints) ← match info with
      | .defnInfo v => pure ("defn", v.value, v.hints)
      | .thmInfo v => pure ("thm", v.value, ReducibilityHints.opaque)
      | .opaqueInfo v => pure ("opaque", v.value, ReducibilityHints.opaque)
      | .axiomInfo _ => return .error "new-axiom"
      | _ => return .error "unbundleable-constant-kind"
    if name == targetName then
      let expected := if targetKind == "def" then "defn" else "thm"
      unless kind == expected do
        return .error "target-kind-drift"
      unless (exprConstantNames info.type).all
          (fun dependency => (before.find? dependency).isSome) do
        return .error "target-type-new-constant"
    let constant : BundledConstant :=
      { kind, name, levelParams := info.levelParams,
        type := info.type, value, hints }
    match validateBundledConstant constant with
    | .error reason => return .error reason
    | .ok _ => bundle := bundle.push constant
  try
    let _ ← before.replay (newConstantMap after names)
    pure ()
  catch _ =>
    return .error "kernel-replay-failed"
  pure <| .ok bundle

def elaborateGenerated (inputCtx : Parser.InputContext) (prepared : Prepared)
    : IO (Except String Elab.Command.State) := do
  let (stx, nextParserState, parseMessages) :=
    Parser.parseCommand inputCtx (parserContext prepared.commandState)
      prepared.parserState {}
  if allMessageCount parseMessages != 0 || nextParserState.recovering
      || stx.hasMissing then
    return .error "parse-error"
  if Parser.isTerminalCommand stx then
    return .error "terminal-command"
  let state := hardenState { prepared.commandState with messages := {} }
  let context : Elab.Command.Context := {
    cmdPos := prepared.parserState.pos
    fileName := inputCtx.fileName
    fileMap := inputCtx.fileMap
    snap? := none
    cancelTk? := none
  }
  let outcome ← try
    let (_, result) ← IO.FS.withIsolatedStreams (isolateStderr := true) <|
      EIO.toIO' <|
        ((Elab.Command.elabCommandTopLevel stx #[]) context).run state
    pure (some result)
  catch _ => pure none
  let some result := outcome | return .error "elaboration-exception"
  match result with
  | Except.error _ => pure <| .error "elaboration-exception"
  | Except.ok (_, nextState) =>
      -- Generated diagnostics are never echoed: the marker transcript is the
      -- entire evidence surface.
      if nextState.messages.hasErrors then
        pure <| .error "elaboration-error"
      else
        match ← settleSnapshotTasks nextState with
        | .error _ => pure <| .error "elaboration-error"
        | .ok settled => pure <| .ok settled

def runTargetMode (stream : IO.FS.Stream) (channelNonce : String)
    (manifest : Manifest)
    (frames : Array Frame) : IO Unit := do
  let prefixView ← frameOf frames "prefix"
  let headerView ← frameOf frames "header"
  let targetView ← frameOf frames "target"
  unless prefixView.utf8ByteSize == manifest.targetStartByte do
    hard "prefix view length disagrees with the committed target start"
  unless targetView.utf8ByteSize == manifest.retainedEndByte do
    hard "target view length disagrees with the committed retained end"
  unless headerView.utf8ByteSize == manifest.headerEndByte do
    hard "header view length disagrees with the committed header end"
  unless slice headerView 0 manifest.targetStartByte == prefixView do
    hard "trusted header does not extend the exact trusted prefix"
  unless slice targetView 0 manifest.headerEndByte == headerView do
    hard "target view does not extend the exact trusted header"
  requireRawPosition "targetStartByte" targetView manifest.targetStartByte
  requireRawPosition "headerEndByte" targetView manifest.headerEndByte
  let options := initialOptions
  let (_, prepared) ← preparePrefix prefixView manifest.logicalFile options
  unless prepared.parserState.pos.byteIdx == manifest.targetStartByte do
    hard "trusted prefix did not stop exactly at the committed target start"
  let targetName := manifest.targetName.toName
  if (prepared.commandState.env.find? targetName).isSome then
    hard "committed target name already exists before the target command"
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "prevalidation"),
    ("mode", toJson "target"),
    ("n_prior_commands", toJson prepared.nPriorCommands),
    ("prefix_view_bytes", toJson prefixView.utf8ByteSize),
    ("header_view_bytes", toJson headerView.utf8ByteSize),
    ("target_view_bytes", toJson targetView.utf8ByteSize)
  ]
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "phase-start"),
    ("mode", toJson "target")
  ]
  awaitAuthorization stream channelNonce
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "phase-go-accepted"),
    ("mode", toJson "target")
  ]
  let targetCtx := Parser.mkInputContext targetView manifest.logicalFile
  match ← elaborateGenerated targetCtx prepared with
  | .error reason =>
      emit channelNonce <| Json.mkObj [
        ("schema", toJson OUTPUT_SCHEMA),
        ("record_type", toJson "target"),
        ("status", toJson "verification-failure"),
        ("reason", toJson reason)
      ]
  | .ok afterState =>
      match ← buildBundle prepared.commandState.env afterState.env targetName
          manifest.targetKind with
      | .error reason =>
          emit channelNonce <| Json.mkObj [
            ("schema", toJson OUTPUT_SCHEMA),
            ("record_type", toJson "target"),
            ("status", toJson "verification-failure"),
            ("reason", toJson reason)
          ]
      | .ok bundle =>
          match bundle.mapM bundledToJson with
          | .error message =>
              emit channelNonce <| Json.mkObj [
                ("schema", toJson OUTPUT_SCHEMA),
                ("record_type", toJson "target"),
                ("status", toJson "verification-failure"),
                ("reason", toJson "unencodable-constant"),
                ("detail", toJson message)
              ]
          | .ok encoded =>
              emit channelNonce <| Json.mkObj [
                ("schema", toJson OUTPUT_SCHEMA),
                ("record_type", toJson "target"),
                ("status", toJson "verified"),
                ("n_bundled_constants", toJson bundle.size),
                ("bundle", .arr #[.str BUNDLE_SCHEMA,
                    toJson manifest.targetName, .arr encoded])
              ]

/-! ## Suffix mode -/

/-- Replay bundled constants in dependency order through `Lean.addDecl`, so the
kernel rechecks each one against the PRE-TARGET environment. -/
partial def replayBundle (state : Elab.Command.State)
    (context : Elab.Command.Context) (pending : List BundledConstant)
    (added : Nat := 0) :
    IO (Except String (Elab.Command.State × Nat)) := do
  if pending.isEmpty then
    return .ok (state, added)
  let ready := pending.filter fun constant =>
    (exprConstantNames constant.type ++ exprConstantNames constant.value).all
      fun name => (state.env.find? name).isSome
  if ready.isEmpty then
    return .error "bundle-replay-unordered"
  let mut current := state
  for constant in ready do
    -- Kernel-check and register the declaration WITHOUT compiling candidate
    -- code.  `addAndCompile` would make an effectful candidate definition
    -- executable inside this suffix process, where it could inspect the
    -- suffix FileMap.  A baseline whose suffix needs executable target code is
    -- therefore prospectively replay-ineligible under this narrow estimand.
    -- Async is disabled for the replay itself and `debug.skipKernelTC` is
    -- already forced off by the surrounding command state.
    let result ← EIO.toIO' <|
      ((Elab.Command.liftCoreM <|
        withOptions (Elab.async.set · false) do
          Lean.addDecl (bundledDeclaration constant)) context).run current
    match result with
    | .error _ => return .error "bundle-replay-rejected"
    | .ok (_, nextState) => current := nextState
  let remaining := pending.filter fun constant =>
    !ready.any (fun done => done.name == constant.name)
  replayBundle current context remaining (added + ready.length)

def runSuffixMode (stream : IO.FS.Stream) (channelNonce : String)
    (manifest : Manifest)
    (frames : Array Frame) : IO Unit := do
  let prefixView ← frameOf frames "prefix"
  let headerView ← frameOf frames "header"
  let suffixView ← frameOf frames "suffix"
  let bundleText ← frameOf frames "bundle"
  unless prefixView.utf8ByteSize == manifest.targetStartByte do
    hard "prefix view length disagrees with the committed target start"
  unless headerView.utf8ByteSize == manifest.headerEndByte do
    hard "header view length disagrees with the committed header end"
  unless slice headerView 0 manifest.targetStartByte == prefixView do
    hard "trusted header does not extend the exact trusted prefix"
  unless manifest.headerEndByte <= suffixView.utf8ByteSize &&
      slice suffixView 0 manifest.headerEndByte == headerView do
    hard "suffix view does not preserve the exact trusted header"
  requireRawPosition "retainedEndByte" suffixView manifest.retainedEndByte
  let bundleJson ← match Json.parse bundleText with
    | .ok value => pure value
    | .error message => hard s!"bundle JSON parse failed: {message}"
  let (bundleTarget, constantsJson) ← match bundleJson with
    | .arr #[.str schema, .str bundleTarget, .arr constants] =>
        if schema == BUNDLE_SCHEMA then pure (bundleTarget, constants)
        else hard s!"bundle schema {schema} != {BUNDLE_SCHEMA}"
    | _ => hard "bundle must be the exact tagged-array schema"
  unless bundleTarget == manifest.targetName do
    hard "bundle target name disagrees with the manifest"
  let targetName := manifest.targetName.toName
  let mut constants : Array BundledConstant := #[]
  let mut bundledNames : NameSet := {}
  for entry in constantsJson do
    match bundledFromJson entry with
    | .error message => hard s!"strict bundle decode failed: {message}"
    | .ok constant =>
        match validateBundledConstant constant with
        | .error message => hard s!"invalid bundled constant: {message}"
        | .ok _ => pure ()
        let encoded ← match bundledToJson constant with
          | .ok value => pure value
          | .error message => hard s!"bundle re-encode failed: {message}"
        unless encoded == entry do
          hard "bundled constant is not in canonical JSON form"
        if bundledNames.contains constant.name then
          hard "bundle contains a duplicate constant name"
        bundledNames := bundledNames.insert constant.name
        constants := constants.push constant
  unless constants.any (fun constant => constant.name == targetName) do
    hard "bundle does not contain the committed target"
  let options := initialOptions
  let (_, prepared) ← preparePrefix prefixView manifest.logicalFile options
  for constant in constants do
    if (prepared.commandState.env.find? constant.name).isSome then
      hard "bundle constant is not fresh in the pre-target environment"
    for dependency in
        (exprConstantNames constant.type ++ exprConstantNames constant.value) do
      unless (prepared.commandState.env.find? dependency).isSome ||
          bundledNames.contains dependency do
        hard "bundle constant has a dependency outside pre-target/bundle state"
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "prevalidation"),
    ("mode", toJson "suffix"),
    ("n_prior_commands", toJson prepared.nPriorCommands),
    ("n_decoded_constants", toJson constants.size)
  ]
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "phase-start"),
    ("mode", toJson "suffix")
  ]
  awaitAuthorization stream channelNonce
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "phase-go-accepted"),
    ("mode", toJson "suffix")
  ]
  let replayInputCtx := Parser.mkInputContext prefixView manifest.logicalFile
  let replayContext : Elab.Command.Context := {
    cmdPos := rawPos manifest.targetStartByte
    fileName := manifest.logicalFile
    fileMap := replayInputCtx.fileMap
    snap? := none
    cancelTk? := none
  }
  let replayStart := hardenSuffixState prepared.commandState
  match ← replayBundle replayStart replayContext constants.toList with
  | .error reason =>
      emit channelNonce <| Json.mkObj [
        ("schema", toJson OUTPUT_SCHEMA),
        ("record_type", toJson "suffix"),
        ("status", toJson "verification-failure"),
        ("reason", toJson reason)
      ]
  | .ok (replayed, nAdded) =>
      let replayedNames := newConstantNames prepared.commandState.env replayed.env
      unless replayedNames.size == constants.size &&
          replayedNames.all bundledNames.contains do
        emit channelNonce <| Json.mkObj [
          ("schema", toJson OUTPUT_SCHEMA),
          ("record_type", toJson "suffix"),
          ("status", toJson "verification-failure"),
          ("reason", toJson "bundle-replay-name-drift")
        ]
        return
      unless implementedByStateEqual prepared.commandState.env replayed.env do
        emit channelNonce <| Json.mkObj [
          ("schema", toJson OUTPUT_SCHEMA),
          ("record_type", toJson "suffix"),
          ("status", toJson "verification-failure"),
          ("reason", toJson "bundle-replay-implemented-by-drift")
        ]
        return
      -- Seek to the retained end: candidate syntax is never re-parsed.
      let suffixCtx := Parser.mkInputContext suffixView manifest.logicalFile
      let mut state := hardenSuffixState replayed
      let mut parserState : Parser.ModuleParserState :=
        { pos := rawPos manifest.retainedEndByte, recovering := false,
          hasLeading := false }
      let mut nSuffix := 0
      let mut failure : Option String := none
      repeat
        let cmdPos := parserState.pos
        let (stx, nextParserState, parseMessages) :=
          Parser.parseCommand suffixCtx (parserContext state) parserState {}
        if allMessageCount parseMessages != 0 || nextParserState.recovering
            || stx.hasMissing then
          failure := some "suffix-parse-error"
          break
        if Parser.isTerminalCommand stx then
          break
        match ← elaborateGenerated suffixCtx
            { parserState, commandState := state,
              nPriorCommands := prepared.nPriorCommands } with
        | .error _ =>
            failure := some "suffix-elaboration-error"
            break
        | .ok nextState =>
            state := nextState
            parserState := nextParserState
            nSuffix := nSuffix + 1
      match failure with
      | some reason =>
          emit channelNonce <| Json.mkObj [
            ("schema", toJson OUTPUT_SCHEMA),
            ("record_type", toJson "suffix"),
            ("status", toJson "verification-failure"),
            ("reason", toJson reason),
            ("n_replayed_constants", toJson nAdded)
          ]
      | none =>
          emit channelNonce <| Json.mkObj [
            ("schema", toJson OUTPUT_SCHEMA),
            ("record_type", toJson "suffix"),
            ("status", toJson "verified"),
            ("n_replayed_constants", toJson nAdded),
            ("n_suffix_commands", toJson nSuffix)
          ]

def readManifest (path : String) : IO Manifest := do
  let contents ← IO.FS.readFile path
  let json ← match Json.parse contents with
    | .ok value => pure value
    | .error message => hard s!"manifest JSON parse failed: {message}"
  match fromJson? json with
  | .ok manifest => pure manifest
  | .error message => hard s!"manifest decode failed: {message}"

def run (stream : IO.FS.Stream) (channelNonce manifestPath : String) :
    IO Unit := do
  let manifest ← readManifest manifestPath
  unless manifest.schema == MANIFEST_SCHEMA do
    hard s!"manifest schema {manifest.schema} != {MANIFEST_SCHEMA}"
  unless ["theorem", "lemma", "def"].contains manifest.targetKind do
    hard "targetKind is not an eligible frozen Lean kind"
  unless manifest.targetStartByte < manifest.headerEndByte &&
      manifest.headerEndByte < manifest.retainedEndByte do
    hard "target/header/retained offsets are not strictly ordered"
  unsafe Lean.enableInitializersExecution
  match manifest.mode with
  | "target" =>
      let frames ← readFrames stream TARGET_FRAME_ROLES
      runTargetMode stream channelNonce manifest frames
  | "suffix" =>
      let frames ← readFrames stream SUFFIX_FRAME_ROLES
      runSuffixMode stream channelNonce manifest frames
  | other => hard s!"unknown probe mode {other}"

end V2BOracleSafeProbe

def main (args : List String) : IO UInt32 := do
  match args with
  | [manifestPath] =>
      let stream ← IO.getStdin
      let nonceLine ← stream.getLine
      let channelNonce := nonceLine.trimAsciiEnd.toString
      if channelNonce.isEmpty then
        IO.eprintln "missing channel nonce on the first stdin line"
        return 2
      V2BOracleSafeProbe.run stream channelNonce manifestPath
      pure 0
  | _ =>
      IO.eprintln "usage: V2BOracleSafeProbe <manifest.json> (nonce on stdin)"
      pure 2
