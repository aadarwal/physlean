/-
Pinned-toolchain semantic verifier for V2-b Lean behavioral completions.

This driver starts from the same exact ModuleSetup/frontend state as
V2BParseCommand.lean.  Production invokes it in two separate fresh processes:
baseline mode verifies the trusted original file and emits a strict kernel-type
certificate; candidate mode verifies one already-S4-retained reconstruction
against that certificate without elaborating the original target or suffix.
A successful sample must create exactly one constant whose rendered name is the
committed target name, with the expected declaration kind and an
alpha-normalized kernel type definitionally equal to the independently produced
baseline.  Generated proof terms may differ and may create normal compiler
auxiliaries, but no newly-created constant may be an axiom, unsafe, partial,
implemented_by, depend on a newly-created axiom, or depend on the absolute
forbidden trusted axioms sorryAx/ofReduceBool/ofReduceNat.

Generated code is elaborated here, so the production wrapper must execute this
driver in a staged, resource-bounded process.  The wrapper sends a fresh
256-bit channel nonce as the first stdin line before any target elaboration.
Every compact-JSON evidence record carries that nonce in its marker.  The
nonce is absent from argv, environment, manifests, and child-visible files, so generated
metaprograms and inherited-stdout subprocesses cannot forge verifier records;
trusted command output is additionally isolated.  After trusted branch
prevalidation the driver emits a start record and blocks for the exact
GO:<nonce> line plus EOF; candidate-generated parsing and either branch's target
elaboration begin only after the wrapper has durably journaled that start record.
-/
import Lean

open Lean

namespace V2BVerifyCommand

def MANIFEST_SCHEMA := "v2b_lean_verify_manifest_v3"
def OUTPUT_SCHEMA := "v2b_lean_verify_result_v3"
def OUTPUT_MARKER_PREFIX := "@@V2B_LEAN_VERIFY:"
def OUTPUT_MARKER_SUFFIX := "@@"
def MAX_SAMPLE_ID_UTF8_BYTES : Nat := 256

structure SampleSpec where
  id : String
  reconstructedFile : String
  reconstructedSha256 : String
  retainedEndByte : Nat
  extractedBodySha256 : String
  s4EvidenceSha256 : String
  deriving FromJson

structure OptionOverride where
  name : String
  value : String
  deriving FromJson

structure BaselineCertificate where
  schema : String
  baselineEvidenceSha256 : String
  baselineInvocationBinding : String
  semanticContextBinding : String
  baselineRuntimeSha256 : String
  nPriorCommands : Nat
  targetName : String
  targetInfoKind : String
  nLevelParams : Nat
  typeExpression : Json
  typeExpressionSha256 : String
  deriving FromJson

structure Manifest where
  schema : String
  mode : String
  invocationBinding : String
  originalFile : String
  logicalFileName : String
  originalSha256 : String
  moduleSetupFile : String
  moduleSetupSha256 : String
  moduleName : String
  targetName : String
  targetKind : String
  targetStartByte : Nat
  targetEndByte : Nat
  headerEndByte : Nat
  bodyDelimiter : String
  boundaryArtifactSha256 : String
  spanId : String
  s4ContractSha256 : String
  s4DriverSha256 : String
  s5ContractSha256 : String
  s5DriverSha256 : String
  semanticContextBinding : String
  runtimeSha256 : String
  optionOverrides : Array OptionOverride
  baselineCertificate : Option BaselineCertificate
  samples : Array SampleSpec
  deriving FromJson

structure Prepared where
  parserState : Parser.ModuleParserState
  originalNextParserState : Parser.ModuleParserState
  commandState : Elab.Command.State
  originalStx : Syntax
  nPriorCommands : Nat

structure Verified where
  targetNameRaw : Name
  targetName : String
  targetInfoKind : String
  typeFingerprint : String
  typeExpr : Expr
  typeExpression : Json
  nLevelParams : Nat
  nNewConstants : Nat
  nAxioms : Nat

def hard {α : Type} (message : String) : IO α :=
  throw <| IO.userError s!"V2B trusted-input error: {message}"

def rawPos (byteIdx : Nat) : String.Pos.Raw := ⟨byteIdx⟩

def requireRawPosition (label : String) (source : String)
    (byteIdx : Nat) : IO Unit := do
  unless (rawPos byteIdx).isValid source do
    hard s!"{label}={byteIdx} is not a valid UTF-8 byte position"

def slice (source : String) (startByte endByte : Nat) : String :=
  String.Pos.Raw.extract source (rawPos startByte) (rawPos endByte)

def allMessageCount (messages : MessageLog) : Nat :=
  messages.reportedPlusUnreported.size

def hardenState (state : Elab.Command.State) : Elab.Command.State :=
  match state.scopes with
  | [] => state
  | scope :: scopes =>
      let opts := debug.skipKernelTC.set scope.opts false
      let opts := opts.setBool `debug.proofAsSorry false
      let scope := { scope with opts }
      { state with scopes := scope :: scopes }

def parserContext (state : Elab.Command.State) : Parser.ParserModuleContext :=
  let scope := state.scopes.head!
  let options := debug.skipKernelTC.set scope.opts false
  let options := options.setBool `debug.proofAsSorry false
  { env := state.env
    options
    currNamespace := scope.currNamespace
    openDecls := scope.openDecls }

def settleSnapshotTasks (state : Elab.Command.State) :
    IO (Except Unit Elab.Command.State) := do
  for task in state.snapshotTasks do
    let tree := task.get
    for snapshot in tree.getAll do
      if snapshot.diagnostics.msgLog.hasErrors then
        return .error ()
  unless (state.env.toKernelEnv.find? ``True).isSome do
    hard "settled checked environment lost the Init.True declaration"
  pure <| .ok { state with snapshotTasks := #[] }

def settleTrustedSnapshotTasks (state : Elab.Command.State) :
    IO Elab.Command.State := do
  match ← settleSnapshotTasks state with
  | .ok state => pure state
  | .error _ =>
      hard "a trusted command before the target has an asynchronous error"

def elaboratePriorCommand (inputCtx : Parser.InputContext)
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
  let (_, result) <- IO.FS.withIsolatedStreams (isolateStderr := true) <|
    EIO.toIO' <|
      ((Elab.Command.elabCommandTopLevel stx #[]) context).run state
  match result with
  | Except.error exception =>
      hard s!"prior-command elaboration raised: \
        {← exception.toMessageData.toString}"
  | Except.ok (_, nextState) =>
      if nextState.messages.hasErrors then
        hard "a trusted command before the target does not elaborate"
      else
        let nextState <- settleTrustedSnapshotTasks nextState
        pure <| hardenState { nextState with messages := {} }

partial def prepareAtTarget (inputCtx : Parser.InputContext)
    (targetStart targetEnd : Nat)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (nPrior : Nat) : IO Prepared := do
  let commandState := hardenState commandState
  let cmdPos := parserState.pos
  let (stx, nextParserState, parseMessages) :=
    Parser.parseCommand inputCtx (parserContext commandState) parserState {}
  if allMessageCount parseMessages != 0 || nextParserState.recovering ||
      stx.hasMissing then
    hard "trusted original source required parser recovery"
  if Parser.isTerminalCommand stx then
    hard "reached a terminal command before the committed target"
  let some range := stx.getRange? (canonicalOnly := true)
    | hard "trusted original command has no canonical source range"
  let startByte := range.start.byteIdx
  let endByte := range.stop.byteIdx
  if startByte == targetStart then
    if endByte != targetEnd then
      hard s!"original target end {endByte} != committed {targetEnd}"
    pure { parserState, originalNextParserState := nextParserState,
           commandState, originalStx := stx,
           nPriorCommands := nPrior }
  else
    if startByte > targetStart || endByte > targetStart then
      hard s!"committed target start {targetStart} lies inside or before \
        original command [{startByte},{endByte})"
    let nextCommandState <-
      elaboratePriorCommand inputCtx cmdPos stx commandState
    prepareAtTarget inputCtx targetStart targetEnd nextParserState
      nextCommandState (nPrior + 1)

def parseExactTarget (source fileName : String) (prepared : Prepared)
    (targetStart targetEnd : Nat) :
    IO (Syntax × Parser.ModuleParserState) := do
  let inputCtx := Parser.mkInputContext source fileName
  let (stx, parserState, messages) :=
    Parser.parseCommand inputCtx (parserContext prepared.commandState)
      prepared.parserState {}
  unless allMessageCount messages == 0 && !parserState.recovering &&
      !stx.hasMissing && !Parser.isTerminalCommand stx do
    hard "S5 input no longer parses as one clean nonterminal target command"
  let some range := stx.getRange? (canonicalOnly := true)
    | hard "S5 target command has no canonical source range"
  unless range.start.byteIdx == targetStart &&
      range.stop.byteIdx == targetEnd do
    hard s!"S5 target range [{range.start.byteIdx},{range.stop.byteIdx}) \
      != committed [{targetStart},{targetEnd})"
  pure (stx, parserState)

def forbiddenTokenReason? (token : String) : Option String :=
  match token with
  | "sorry" | "admit" | "sorryAx" => some "sorry"
  | "native_decide" | "Lean.ofReduceBool" | "Lean.ofReduceNat" =>
      some "native-reflection"
  | "implemented_by" => some "implemented-by"
  | "unsafe" => some "unsafe-or-partial"
  | _ => none

partial def forbiddenGeneratedSyntax? (stx : Syntax)
    (headerEnd : String.Pos.Raw) : Option String :=
  match stx with
  | .missing => none
  | .atom info value =>
      match info.getRange? (canonicalOnly := true),
          forbiddenTokenReason? value with
      | some range, some reason =>
          if headerEnd <= range.start then some reason else none
      | _, _ => none
  | .ident info rawValue value _ =>
      let reason? := (forbiddenTokenReason? rawValue.toString).orElse fun _ =>
        forbiddenTokenReason? value.toString
      match info.getRange? (canonicalOnly := true), reason? with
      | some range, some reason =>
          if headerEnd <= range.start then some reason else none
      | _, _ => none
  | .node _ _ args =>
      args.foldl (init := none) fun found child =>
        found.orElse fun _ => forbiddenGeneratedSyntax? child headerEnd

def targetInfoKind : ConstantInfo -> String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quotient"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def kindMatches (targetKind : String) (info : ConstantInfo) : Bool :=
  match targetKind, info with
  | "theorem", .thmInfo _ => true
  | "lemma", .thmInfo _ => true
  | "def", .defnInfo _ => true
  | _, _ => false

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

/-!
The baseline/candidate process boundary uses a locally owned, deliberately
small certificate codec.  Tagged arrays make both arity and constructor choice
unambiguous.  Binder names and metadata are erased before encoding; free and
meta variables (including universe metavariables) have no representation.
-/

partial def certificateNameToJson : Name -> Json
  | .anonymous => Json.arr #[.str "anonymous"]
  | .str parent value =>
      Json.arr #[.str "str", certificateNameToJson parent, toJson value]
  | .num parent value =>
      Json.arr #[.str "num", certificateNameToJson parent, toJson value]

partial def certificateNameFromJson? : Json -> Except String Name
  | .arr #[.str "anonymous"] => .ok .anonymous
  | .arr #[.str "str", parent, .str value] =>
      return .str (← certificateNameFromJson? parent) value
  | .arr #[.str "num", parent, value] =>
      return .num (← certificateNameFromJson? parent)
        (← fromJson? value : Nat)
  | value => .error s!"invalid certificate Name: {value}"

def certificateBinderInfoToJson : BinderInfo -> Json
  | .default => .str "default"
  | .implicit => .str "implicit"
  | .strictImplicit => .str "strict-implicit"
  | .instImplicit => .str "instance-implicit"

def certificateBinderInfoFromJson? : Json -> Except String BinderInfo
  | .str "default" => .ok .default
  | .str "implicit" => .ok .implicit
  | .str "strict-implicit" => .ok .strictImplicit
  | .str "instance-implicit" => .ok .instImplicit
  | value => .error s!"invalid certificate BinderInfo: {value}"

partial def certificateLevelToJson? : Level -> Except String Json
  | .zero => .ok <| Json.arr #[.str "zero"]
  | .succ level =>
      return Json.arr #[.str "succ", ← certificateLevelToJson? level]
  | .max left right =>
      return Json.arr #[.str "max", ← certificateLevelToJson? left,
        ← certificateLevelToJson? right]
  | .imax left right =>
      return Json.arr #[.str "imax", ← certificateLevelToJson? left,
        ← certificateLevelToJson? right]
  | .param name =>
      .ok <| Json.arr #[.str "param", certificateNameToJson name]
  | .mvar _ => .error "universe metavariable is forbidden in a certificate"

partial def certificateLevelFromJson? : Json -> Except String Level
  | .arr #[.str "zero"] => .ok .zero
  | .arr #[.str "succ", level] =>
      return .succ (← certificateLevelFromJson? level)
  | .arr #[.str "max", left, right] =>
      return .max (← certificateLevelFromJson? left)
        (← certificateLevelFromJson? right)
  | .arr #[.str "imax", left, right] =>
      return .imax (← certificateLevelFromJson? left)
        (← certificateLevelFromJson? right)
  | .arr #[.str "param", name] =>
      return .param (← certificateNameFromJson? name)
  | value => .error s!"invalid certificate Level: {value}"

def certificateLevelsToJson? (levels : List Level) : Except String Json := do
  let encoded ← levels.toArray.mapM certificateLevelToJson?
  pure <| Json.arr encoded

def certificateLevelsFromJson? : Json -> Except String (List Level)
  | .arr values => values.toList.mapM certificateLevelFromJson?
  | value => .error s!"invalid certificate level array: {value}"

partial def certificateExprToJson? : Expr -> Except String Json
  | .bvar index => .ok <| Json.arr #[.str "bvar", toJson index]
  | .fvar _ => .error "free variable is forbidden in a certificate"
  | .mvar _ => .error "metavariable is forbidden in a certificate"
  | .sort level =>
      return Json.arr #[.str "sort", ← certificateLevelToJson? level]
  | .const name levels =>
      return Json.arr #[.str "const", certificateNameToJson name,
        ← certificateLevelsToJson? levels]
  | .app fn argument =>
      return Json.arr #[.str "app", ← certificateExprToJson? fn,
        ← certificateExprToJson? argument]
  | .lam _ type body binderInfo =>
      return Json.arr #[.str "lam", ← certificateExprToJson? type,
        ← certificateExprToJson? body,
        certificateBinderInfoToJson binderInfo]
  | .forallE _ type body binderInfo =>
      return Json.arr #[.str "forall", ← certificateExprToJson? type,
        ← certificateExprToJson? body,
        certificateBinderInfoToJson binderInfo]
  | .letE _ type value body nondependent =>
      return Json.arr #[.str "let", ← certificateExprToJson? type,
        ← certificateExprToJson? value, ← certificateExprToJson? body,
        toJson nondependent]
  | .lit (.natVal value) =>
      .ok <| Json.arr #[.str "lit-nat", toJson value]
  | .lit (.strVal value) =>
      .ok <| Json.arr #[.str "lit-string", toJson value]
  | .mdata _ _ => .error "metadata is forbidden in a certificate"
  | .proj typeName index value =>
      return Json.arr #[.str "proj", certificateNameToJson typeName,
        toJson index, ← certificateExprToJson? value]

partial def certificateExprFromJson? : Json -> Except String Expr
  | .arr #[.str "bvar", index] =>
      return .bvar (← fromJson? index : Nat)
  | .arr #[.str "sort", level] =>
      return .sort (← certificateLevelFromJson? level)
  | .arr #[.str "const", name, levels] =>
      return .const (← certificateNameFromJson? name)
        (← certificateLevelsFromJson? levels)
  | .arr #[.str "app", fn, argument] =>
      return .app (← certificateExprFromJson? fn)
        (← certificateExprFromJson? argument)
  | .arr #[.str "lam", type, body, binderInfo] =>
      return .lam .anonymous (← certificateExprFromJson? type)
        (← certificateExprFromJson? body)
        (← certificateBinderInfoFromJson? binderInfo)
  | .arr #[.str "forall", type, body, binderInfo] =>
      return .forallE .anonymous (← certificateExprFromJson? type)
        (← certificateExprFromJson? body)
        (← certificateBinderInfoFromJson? binderInfo)
  | .arr #[.str "let", type, value, body, nondependent] =>
      return .letE .anonymous (← certificateExprFromJson? type)
        (← certificateExprFromJson? value)
        (← certificateExprFromJson? body)
        (← fromJson? nondependent : Bool)
  | .arr #[.str "lit-nat", value] =>
      return .lit (.natVal (← fromJson? value : Nat))
  | .arr #[.str "lit-string", .str value] =>
      .ok <| .lit (.strVal value)
  | .arr #[.str "proj", typeName, index, value] =>
      return .proj (← certificateNameFromJson? typeName)
        (← fromJson? index : Nat) (← certificateExprFromJson? value)
  | value => .error s!"invalid certificate Expr: {value}"

partial def kernelTypeExpr : Expr -> Expr
  | .bvar i => .bvar i
  | .fvar f => .fvar f
  | .mvar m => .mvar m
  | .sort u => .sort u
  | .const n us => .const n us
  | .app f a => .app (kernelTypeExpr f) (kernelTypeExpr a)
  | .lam _ t b bi =>
      .lam .anonymous (kernelTypeExpr t) (kernelTypeExpr b) bi
  | .forallE _ t b bi =>
      .forallE .anonymous (kernelTypeExpr t) (kernelTypeExpr b) bi
  | .letE _ t v b nondep =>
      .letE .anonymous (kernelTypeExpr t) (kernelTypeExpr v)
        (kernelTypeExpr b) nondep
  | .lit value => .lit value
  | .mdata _ value => kernelTypeExpr value
  | .proj typeName index value =>
      .proj typeName index (kernelTypeExpr value)

def canonicalLevels (n : Nat) : List Level :=
  (List.range n).map fun index =>
    .param (.num (.str .anonymous "v2b_universe") index)

def isCanonicalLevelName (n : Nat) (name : Name) : Bool :=
  (List.range n).any fun index =>
    name == .num (.str .anonymous "v2b_universe") index

partial def levelParamsAreCanonical (n : Nat) : Level -> Bool
  | .zero => true
  | .succ level => levelParamsAreCanonical n level
  | .max left right | .imax left right =>
      levelParamsAreCanonical n left && levelParamsAreCanonical n right
  | .param name => isCanonicalLevelName n name
  | .mvar _ => false

partial def exprLevelParamsAreCanonical (n : Nat) : Expr -> Bool
  | .bvar _ => true
  | .fvar _ | .mvar _ => false
  | .sort level => levelParamsAreCanonical n level
  | .const _ levels => levels.all (levelParamsAreCanonical n)
  | .app fn argument =>
      exprLevelParamsAreCanonical n fn &&
        exprLevelParamsAreCanonical n argument
  | .lam _ type body _ | .forallE _ type body _ =>
      exprLevelParamsAreCanonical n type &&
        exprLevelParamsAreCanonical n body
  | .letE _ type value body _ =>
      exprLevelParamsAreCanonical n type &&
        exprLevelParamsAreCanonical n value &&
        exprLevelParamsAreCanonical n body
  | .lit _ => true
  | .mdata _ value => exprLevelParamsAreCanonical n value
  | .proj _ _ value => exprLevelParamsAreCanonical n value

partial def exprConstantsArePreexisting (env : Environment) : Expr -> Bool
  | .bvar _ => true
  | .fvar _ | .mvar _ => false
  | .sort _ => true
  | .const name _ => (env.find? name).isSome
  | .app fn argument =>
      exprConstantsArePreexisting env fn &&
        exprConstantsArePreexisting env argument
  | .lam _ type body _ | .forallE _ type body _ =>
      exprConstantsArePreexisting env type &&
        exprConstantsArePreexisting env body
  | .letE _ type value body _ =>
      exprConstantsArePreexisting env type &&
        exprConstantsArePreexisting env value &&
        exprConstantsArePreexisting env body
  | .lit _ => true
  | .mdata _ value => exprConstantsArePreexisting env value
  | .proj typeName _ value =>
      (env.find? typeName).isSome && exprConstantsArePreexisting env value

def canonicalType (info : ConstantInfo) : IO Expr := do
  let value := info.type.instantiateLevelParams info.levelParams
    (canonicalLevels info.levelParams.length)
  if value.hasMVar || value.hasFVar || value.hasLooseBVars then
    hard "elaborated constant type contains free/meta/loose-bound variables"
  pure <| kernelTypeExpr value

def kernelTypeEqual (env : Environment) (left right : Expr) : Except String Bool :=
  match Kernel.isDefEq env {} left right with
  | .ok equal => .ok equal
  | .error _ => .error "kernel-defeq-error"

def decodeBaselineType (env : Environment)
    (certificate : BaselineCertificate) : IO Expr := do
  let value <- match certificateExprFromJson? certificate.typeExpression with
    | .ok expression => pure expression
    | .error message => hard s!"baseline type certificate decode failed: {message}"
  if value.hasMVar || value.hasFVar || value.hasLooseBVars then
    hard "baseline type certificate contains free/meta/loose-bound variables"
  unless exprLevelParamsAreCanonical certificate.nLevelParams value do
    hard "baseline type certificate has noncanonical universe parameters"
  unless exprConstantsArePreexisting env value do
    hard "baseline type certificate refers outside the pre-target environment"
  let encoded <- match certificateExprToJson? value with
    | .ok json => pure json
    | .error message => hard s!"baseline type certificate encode failed: {message}"
  unless encoded == certificate.typeExpression do
    hard "baseline type certificate is not in canonical Expr JSON form"
  let inferredType <- match Kernel.check env {} value with
    | .ok type => pure type
    | .error _ => hard "baseline type certificate fails independent kernel checking"
  match Kernel.whnf env {} inferredType with
  | .ok (.sort _) => pure ()
  | _ => hard "baseline type certificate is not itself a well-typed type"
  pure value

def findTarget (env : Environment) (newNames : Array Name)
    (targetIdentity : String) : IO (Except String (Name × ConstantInfo)) := do
  let expectedName := targetIdentity.toName
  let matched := newNames.filter fun name => name == expectedName
  unless matched.size == 1 do return .error "target-name-drift"
  let name := matched[0]!
  let some info := env.find? name
    | hard "new target constant is absent from the elaborated environment"
  pure <| .ok (name, info)

def forbiddenAxiom (name : Name) : Bool :=
  name == ``sorryAx || name == ``Lean.ofReduceBool ||
    name == ``Lean.ofReduceNat

partial def collectAxiomClosure (env : Environment) (pending : List Name)
    (seen axioms : NameSet := {}) : NameSet :=
  match pending with
  | [] => axioms
  | name :: rest =>
      if seen.contains name then
        collectAxiomClosure env rest seen axioms
      else
        let seen := seen.insert name
        match env.find? name with
        | none => collectAxiomClosure env rest seen axioms
        | some info =>
            let axioms := match info with
              | .axiomInfo _ => axioms.insert name
              | _ => axioms
            collectAxiomClosure env
              (info.getUsedConstantsAsSet.toArray.toList ++ rest)
              seen axioms

def verifyEnvironment (before : Environment) (after : Environment)
    (targetIdentity targetKind : String) : IO (Except String Verified) := do
  let newNames := newConstantNames before after
  unless implementedByStateEqual before after do
    return .error "implemented-by"
  let targetResult <- findTarget after newNames targetIdentity
  let .ok (targetName, targetInfo) := targetResult
    | return .error "target-name-drift"
  unless kindMatches targetKind targetInfo do
    return .error "target-kind-drift"
  unless exprConstantsArePreexisting before targetInfo.type do
    return .error "target-type-new-constant"
  let mut axiomSet : NameSet := {}
  for name in newNames do
    let some info := after.find? name
      | hard "environment delta name is not resolvable"
    if let .axiomInfo _ := info then
      return .error "new-axiom"
    if info.isUnsafe || info.isPartial then
      return .error "unsafe-or-partial"
    if (Lean.Compiler.getImplementedBy? after name).isSome then
      return .error "implemented-by"
    for ax in (collectAxiomClosure after [name]).toArray do
      axiomSet := axiomSet.insert ax
      if forbiddenAxiom ax then
        return .error (if ax == ``sorryAx then "sorry" else
          "native-reflection")
      match before.find? ax with
      | some (.axiomInfo _) => pure ()
      | _ => return .error "new-axiom-dependency"
  try
    let _ <- before.replay (newConstantMap after newNames)
    pure ()
  catch _ =>
    return .error "kernel-replay-failed"
  let canonical <- canonicalType targetInfo
  let encoded <- match certificateExprToJson? canonical with
    | .ok json => pure json
    | .error message => hard s!"verified target type encode failed: {message}"
  pure <| .ok {
    targetNameRaw := targetName
    targetName := targetName.toString
    targetInfoKind := targetInfoKind targetInfo
    typeFingerprint := reprStr canonical
    typeExpr := canonical
    typeExpression := encoded
    nLevelParams := targetInfo.levelParams.length
    nNewConstants := newNames.size
    nAxioms := axiomSet.size
  }

def elaborateTarget (source fileName : String) (stx : Syntax)
    (prepared : Prepared) (targetIdentity targetKind : String) :
    IO (Except (String × Bool) (Elab.Command.State × Verified)) := do
  let inputCtx := Parser.mkInputContext source fileName
  let state := hardenState { prepared.commandState with messages := {} }
  let context : Elab.Command.Context := {
    cmdPos := prepared.parserState.pos
    fileName := inputCtx.fileName
    fileMap := inputCtx.fileMap
    snap? := none
    cancelTk? := none
  }
  let elaborated? <- try
    let (_, result) <- IO.FS.withIsolatedStreams (isolateStderr := true) <|
      EIO.toIO' <|
        ((Elab.Command.elabCommandTopLevel stx #[]) context).run state
    pure <| some result
  catch _ =>
    pure none
  let some elaborated := elaborated?
    | return .error ("elaboration-exception", false)
  match elaborated with
  | Except.error _ => pure <| .error ("elaboration-exception", false)
  | Except.ok (_, nextState) =>
      if nextState.messages.hasErrors then
        pure <| .error ("elaboration-error", false)
      else
        match ← settleSnapshotTasks nextState with
        | .error _ => pure <| .error ("elaboration-error", false)
        | .ok nextState =>
            let nextState := hardenState { nextState with messages := {} }
            match ← verifyEnvironment state.env nextState.env targetIdentity
                targetKind with
            | .error reason => pure <| .error (reason, true)
            | .ok verified => pure <| .ok (nextState, verified)

partial def elaborateSuffix (source fileName : String)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (nCommands : Nat := 0) :
    IO (Except String Nat) := do
  let commandState := hardenState commandState
  let inputCtx := Parser.mkInputContext source fileName
  let cmdPos := parserState.pos
  let (stx, nextParserState, parseMessages) :=
    Parser.parseCommand inputCtx (parserContext commandState) parserState {}
  if allMessageCount parseMessages != 0 || nextParserState.recovering ||
      stx.hasMissing then
    return .error "suffix-parse-error"
  if Parser.isTerminalCommand stx then
    return .ok nCommands
  let context : Elab.Command.Context := {
    cmdPos
    fileName := inputCtx.fileName
    fileMap := inputCtx.fileMap
    snap? := none
    cancelTk? := none
  }
  try
    let (_, result) <- IO.FS.withIsolatedStreams (isolateStderr := true) <|
      EIO.toIO' <|
        ((Elab.Command.elabCommandTopLevel stx #[]) context).run
          { commandState with messages := {} }
    match result with
    | Except.error _ => pure <| .error "suffix-elaboration-exception"
    | Except.ok (_, nextState) =>
        if nextState.messages.hasErrors then
          pure <| .error "suffix-elaboration-error"
        else
          match ← settleSnapshotTasks nextState with
          | .error _ => pure <| .error "suffix-elaboration-error"
          | .ok nextState =>
              elaborateSuffix source fileName nextParserState
                (hardenState { nextState with messages := {} })
                (nCommands + 1)
  catch _ =>
    pure <| .error "suffix-elaboration-exception"

def emit (channelNonce : String) (value : Json) : IO Unit :=
  do
    IO.println s!"{OUTPUT_MARKER_PREFIX}{channelNonce}{OUTPUT_MARKER_SUFFIX}{Json.compress value}"
    let stdout <- IO.getStdout
    stdout.flush

def outcomeClass (targetKind : String) : String :=
  if targetKind == "def" then "lean-def-typecheck"
  else "lean-theorem-proof"

def isForbiddenFailure (reason : String) : Bool :=
  ["new-axiom", "unsafe-or-partial", "implemented-by", "sorry",
   "native-reflection", "new-axiom-dependency"].contains reason

def verifiedJson (recordType : String) (sampleId : Option String)
    (verified : Verified) (typeKernelEqual : Json)
    (targetKind : String) : Json :=
  Json.mkObj <| [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson recordType),
    ("status", toJson "verified"),
    ("outcome_class", toJson (outcomeClass targetKind)),
    ("target_name", toJson verified.targetName),
    ("target_info_kind", toJson verified.targetInfoKind),
    ("type_fingerprint", toJson verified.typeFingerprint),
    ("type_expression", verified.typeExpression),
    ("type_kernel_equal", typeKernelEqual),
    ("n_level_params", toJson verified.nLevelParams),
    ("n_new_constants", toJson verified.nNewConstants),
    ("n_axioms", toJson verified.nAxioms),
    ("forbidden_surfaces", Json.arr #[]),
    ("elaboration_attempted", toJson true),
    ("elaboration_succeeded", toJson true)
  ] ++ match sampleId with
    | some id => [("sample_id", toJson id)]
    | none => []

def failureJson (recordType reason targetKind : String)
    (sampleId : Option String := none) (attempted : Bool := true)
    (elaborationSucceeded : Bool := false) : Json :=
  Json.mkObj <| [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson recordType),
    ("status", toJson "verification-failure"),
    ("reason", toJson reason),
    ("outcome_class", toJson (outcomeClass targetKind)),
    ("forbidden_surfaces", if isForbiddenFailure reason then
      Json.arr #[toJson reason] else Json.arr #[]),
    ("elaboration_attempted", toJson attempted),
    ("elaboration_succeeded", toJson elaborationSucceeded)
  ] ++ match sampleId with
    | some id => [("sample_id", toJson id)]
    | none => []

def emitPrevalidation (channelNonce : String) (manifest : Manifest)
    (prepared : Prepared) : IO Unit :=
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "prevalidation"),
    ("mode", toJson manifest.mode),
    ("invocation_binding", toJson manifest.invocationBinding),
    ("module_name", toJson manifest.moduleName),
    ("target_name", toJson manifest.targetName),
    ("target_kind", toJson manifest.targetKind),
    ("target_start_byte", toJson manifest.targetStartByte),
    ("target_end_byte", toJson manifest.targetEndByte),
    ("header_end_byte", toJson manifest.headerEndByte),
    ("body_delimiter", toJson manifest.bodyDelimiter),
    ("logical_filename", toJson manifest.logicalFileName),
    ("original_sha256", toJson manifest.originalSha256),
    ("module_setup_sha256", toJson manifest.moduleSetupSha256),
    ("boundary_artifact_sha256", toJson manifest.boundaryArtifactSha256),
    ("span_id", toJson manifest.spanId),
    ("s4_contract_sha256", toJson manifest.s4ContractSha256),
    ("s4_driver_sha256", toJson manifest.s4DriverSha256),
    ("s5_contract_sha256", toJson manifest.s5ContractSha256),
    ("s5_driver_sha256", toJson manifest.s5DriverSha256),
    ("semantic_context_binding", toJson manifest.semanticContextBinding),
    ("runtime_sha256", toJson manifest.runtimeSha256),
    ("baseline_evidence_sha256", match manifest.baselineCertificate with
      | some certificate => toJson certificate.baselineEvidenceSha256
      | none => Json.null),
    ("n_prior_commands", toJson prepared.nPriorCommands)
  ]

def emitCandidateStart (channelNonce : String) (manifest : Manifest)
    (sample : SampleSpec) : IO Unit := do
  let some certificate := manifest.baselineCertificate
    | hard "candidate-start marker has no baseline certificate"
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "candidate-start"),
    ("invocation_binding", toJson manifest.invocationBinding),
    ("sample_id", toJson sample.id),
    ("baseline_evidence_sha256", toJson certificate.baselineEvidenceSha256)
  ]

def emitCandidateGoAccepted (channelNonce : String) (manifest : Manifest)
    (sample : SampleSpec) : IO Unit := do
  let some certificate := manifest.baselineCertificate
    | hard "candidate GO-acceptance marker has no baseline certificate"
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "candidate-go-accepted"),
    ("invocation_binding", toJson manifest.invocationBinding),
    ("sample_id", toJson sample.id),
    ("baseline_evidence_sha256", toJson certificate.baselineEvidenceSha256)
  ]

def emitBaselineStart (channelNonce : String) (manifest : Manifest) : IO Unit :=
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "baseline-start"),
    ("invocation_binding", toJson manifest.invocationBinding)
  ]

def emitBaselineGoAccepted (channelNonce : String) (manifest : Manifest) : IO Unit :=
  emit channelNonce <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "baseline-go-accepted"),
    ("invocation_binding", toJson manifest.invocationBinding)
  ]

def awaitAuthorization (stdin : IO.FS.Stream) (channelNonce : String) : IO Unit := do
  let line <- stdin.getLine
  unless line == s!"GO:{channelNonce}\n" do
    hard "channel start authorization is missing or malformed"
  let trailing <- stdin.read 1
  unless trailing.isEmpty do
    hard "channel stdin must end immediately after start authorization"

def validateCandidateSplice (original reconstructed : String)
    (manifest : Manifest) (sample : SampleSpec) : IO Unit := do
  requireRawPosition "retainedEndByte" reconstructed sample.retainedEndByte
  unless manifest.headerEndByte < sample.retainedEndByte do
    hard s!"sample {sample.id}: retained target body is empty"
  unless slice original 0 manifest.headerEndByte ==
      slice reconstructed 0 manifest.headerEndByte do
    hard s!"sample {sample.id}: trusted target prefix drifted"
  unless slice original manifest.targetEndByte original.utf8ByteSize ==
      slice reconstructed sample.retainedEndByte reconstructed.utf8ByteSize do
    hard s!"sample {sample.id}: immutable post-target suffix drifted"

def readManifest (path : String) : IO Manifest := do
  let contents <- IO.FS.readFile path
  let json <- match Json.parse contents with
    | Except.ok value => pure value
    | Except.error message => hard s!"manifest JSON parse failed: {message}"
  match fromJson? json with
  | Except.ok manifest => pure manifest
  | Except.error message => hard s!"manifest schema decode failed: {message}"

def run (stdin : IO.FS.Stream) (channelNonce manifestPath : String) : IO Unit := do
  let manifest <- readManifest manifestPath
  unless manifest.schema == MANIFEST_SCHEMA do
    hard s!"manifest schema {manifest.schema} != {MANIFEST_SCHEMA}"
  if manifest.invocationBinding.length != 64 ||
      manifest.originalSha256.length != 64 ||
      manifest.moduleSetupSha256.length != 64 ||
      manifest.boundaryArtifactSha256.length != 64 ||
      manifest.spanId.length != 64 || manifest.s4ContractSha256.length != 64 ||
      manifest.s4DriverSha256.length != 64 ||
      manifest.s5ContractSha256.length != 64 ||
      manifest.s5DriverSha256.length != 64 ||
      manifest.semanticContextBinding.length != 64 ||
      manifest.runtimeSha256.length != 64 then
    hard "manifest SHA/binding field is not 64 characters"
  if manifest.originalFile.isEmpty || manifest.logicalFileName.isEmpty ||
      manifest.moduleSetupFile.isEmpty || manifest.moduleName.isEmpty ||
      manifest.targetName.isEmpty then
    hard "original/logical/setup/module/target identity fields must be nonempty"
  unless ["theorem", "lemma", "def"].contains manifest.targetKind do
    hard "targetKind is not an eligible frozen Lean kind"
  unless [":=", "where", "|"].contains manifest.bodyDelimiter do
    hard "bodyDelimiter is outside the frozen Lean delimiter set"
  unless ["baseline", "candidate"].contains manifest.mode do
    hard "S5 mode is outside {baseline,candidate}"
  match manifest.mode, manifest.baselineCertificate, manifest.samples.size with
  | "baseline", none, 0 => pure ()
  | "candidate", some certificate, 1 =>
      if certificate.schema != "v2b_lean_baseline_certificate_v1" ||
          certificate.baselineEvidenceSha256.length != 64 ||
          certificate.baselineInvocationBinding.length != 64 ||
          certificate.semanticContextBinding != manifest.semanticContextBinding ||
          certificate.baselineRuntimeSha256 != manifest.runtimeSha256 ||
          certificate.typeExpressionSha256.length != 64 ||
          certificate.targetName != manifest.targetName ||
          certificate.targetInfoKind !=
            (if manifest.targetKind == "def" then "definition" else "theorem") then
        hard "candidate baseline certificate identity is malformed"
  | _, _, _ =>
      hard "baseline mode needs zero samples/no certificate; candidate mode needs one sample/certificate"
  let mut seenOptions : Array String := #[]
  for option in manifest.optionOverrides do
    if option.name.isEmpty || option.value.isEmpty ||
        seenOptions.contains option.name || option.name == "Elab.async" then
      hard "option override is empty, duplicated, or controls Elab.async"
    seenOptions := seenOptions.push option.name
  let mut seenIds : Array String := #[]
  for sample in manifest.samples do
    if sample.id.isEmpty || sample.id.utf8ByteSize >
        MAX_SAMPLE_ID_UTF8_BYTES || sample.reconstructedFile.isEmpty ||
        sample.reconstructedSha256.length != 64 ||
        sample.extractedBodySha256.length != 64 ||
        sample.s4EvidenceSha256.length != 64 || seenIds.contains sample.id then
      hard "sample id/path/SHA is malformed or duplicated"
    seenIds := seenIds.push sample.id
  let original <- IO.FS.readFile manifest.originalFile
  requireRawPosition "targetStartByte" original manifest.targetStartByte
  requireRawPosition "headerEndByte" original manifest.headerEndByte
  requireRawPosition "targetEndByte" original manifest.targetEndByte
  unless manifest.targetStartByte < manifest.headerEndByte &&
      manifest.headerEndByte < manifest.targetEndByte do
    hard "target/header/end byte order is invalid"
  let inputCtx := Parser.mkInputContext original manifest.logicalFileName
  let (header, parserState, headerMessages) <- Parser.parseHeader inputCtx
  if allMessageCount headerMessages != 0 || parserState.recovering ||
      header.raw.hasMissing then
    hard "trusted original module header required parser recovery"
  let setup <- ModuleSetup.load manifest.moduleSetupFile
  unless setup.name == manifest.moduleName.toName do
    hard s!"module setup name {setup.name} != {manifest.moduleName}"
  let mut commandLineOptions : Options := {}
  for option in manifest.optionOverrides do
    commandLineOptions := commandLineOptions.set option.name.toName option.value
  commandLineOptions := Lean.internal.cmdlineSnapshots.setIfNotSet
    commandLineOptions true
  commandLineOptions := Elab.async.setIfNotSet commandLineOptions true
  commandLineOptions := debug.skipKernelTC.set commandLineOptions false
  commandLineOptions := commandLineOptions.setBool `debug.proofAsSorry false
  let options := commandLineOptions.mergeBy (fun _ _ setupValue => setupValue)
    setup.options.toOptions
  unsafe Lean.enableInitializersExecution
  let (_, (environment, importMessages)) <-
    IO.FS.withIsolatedStreams (isolateStderr := true) do
      setup.dynlibs.forM Lean.loadDynlib
      Elab.processHeaderCore (leakEnv := true)
        (Elab.HeaderSyntax.startPos header)
        (setup.imports?.getD (Elab.HeaderSyntax.imports header))
        (strictOr setup.isModule (Elab.HeaderSyntax.isModule header))
        options {} inputCtx 0 setup.plugins setup.name setup.package?
        setup.importArts (headerStx? := header) (origHeaderStx? := header)
  if importMessages.hasErrors then
    hard "trusted original module imports did not load"
  let mut options <- Language.Lean.reparseOptions options
  options := debug.skipKernelTC.set options false
  options := options.setBool `debug.proofAsSorry false
  let commandState := Elab.Command.mkState environment {} options
  let prepared <- prepareAtTarget inputCtx manifest.targetStartByte
    manifest.targetEndByte parserState commandState 0
  unless manifest.targetName.toName.toString == manifest.targetName do
    hard "targetName is not in canonical round-trip Lean Name form"
  if (prepared.commandState.env.find? manifest.targetName.toName).isSome then
    hard "committed target name already exists before the target command"
  emitPrevalidation channelNonce manifest prepared
  if let some certificate := manifest.baselineCertificate then
    unless certificate.nPriorCommands == prepared.nPriorCommands do
      hard "baseline certificate pre-target command count drifted"
  if manifest.mode == "baseline" then
    emitBaselineStart channelNonce manifest
    awaitAuthorization stdin channelNonce
    emitBaselineGoAccepted channelNonce manifest
    let baseline <- elaborateTarget original manifest.logicalFileName
      prepared.originalStx prepared manifest.targetName manifest.targetKind
    match baseline with
    | .error (reason, succeeded) =>
        emit channelNonce <| failureJson "baseline" reason manifest.targetKind none true
          succeeded
    | .ok (baselineState, verified) =>
        match ← elaborateSuffix original manifest.logicalFileName
            prepared.originalNextParserState baselineState with
        | .error reason =>
            emit channelNonce <| failureJson "baseline" reason manifest.targetKind
              none true true
        | .ok _ =>
            emit channelNonce <| verifiedJson "baseline" none verified Json.null
              manifest.targetKind
  else
    let some certificate := manifest.baselineCertificate
      | hard "candidate mode lost its baseline certificate"
    let #[sample] := manifest.samples
      | hard "candidate mode lost its single sample"
    let baselineType <- decodeBaselineType prepared.commandState.env certificate
    let reconstructed <- IO.FS.readFile sample.reconstructedFile
    validateCandidateSplice original reconstructed manifest sample
    emitCandidateStart channelNonce manifest sample
    awaitAuthorization stdin channelNonce
    emitCandidateGoAccepted channelNonce manifest sample
    let (stx, nextParserState) <- parseExactTarget reconstructed
      manifest.logicalFileName prepared
      manifest.targetStartByte sample.retainedEndByte
    match forbiddenGeneratedSyntax? stx (rawPos manifest.headerEndByte) with
    | some reason =>
        emit channelNonce (failureJson "sample" reason manifest.targetKind
          (some sample.id) false false)
    | none =>
      match ← elaborateTarget reconstructed manifest.logicalFileName stx
          prepared manifest.targetName manifest.targetKind with
      | .error (reason, succeeded) => emit channelNonce (failureJson "sample" reason
          manifest.targetKind (some sample.id) true succeeded)
      | .ok (candidateState, verified) =>
          match kernelTypeEqual prepared.commandState.env
              baselineType verified.typeExpr with
          | .error reason =>
              emit channelNonce (failureJson "sample" reason manifest.targetKind
                (some sample.id) true true)
          | .ok typesEqual =>
              if verified.nLevelParams != certificate.nLevelParams ||
                  !typesEqual then
                emit channelNonce (failureJson "sample" "target-type-drift"
                  manifest.targetKind (some sample.id) true true)
              else
                match ← elaborateSuffix reconstructed manifest.logicalFileName
                    nextParserState candidateState with
                | .error reason =>
                    emit channelNonce (failureJson "sample" reason manifest.targetKind
                      (some sample.id) true true)
                | .ok _ =>
                    emit channelNonce <| verifiedJson "sample" (some sample.id) verified
                      (toJson true) manifest.targetKind

end V2BVerifyCommand

def main (args : List String) : IO UInt32 := do
  match args with
  | [manifestPath] =>
      let stdin <- IO.getStdin
      let nonceLine <- stdin.getLine
      unless nonceLine.length == 65 && nonceLine.endsWith "\n" do
        IO.eprintln "V2B channel nonce must be one 64-hex line"
        return 2
      let channelNonce := (nonceLine.dropEnd 1).toString
      unless channelNonce.all fun c =>
          ('0' <= c && c <= '9') || ('a' <= c && c <= 'f') do
        IO.eprintln "V2B channel nonce must be lowercase hexadecimal"
        return 2
      V2BVerifyCommand.run stdin channelNonce manifestPath
      pure 0
  | _ =>
      IO.eprintln "usage: V2BVerifyCommand <manifest.json>"
      pure 2
