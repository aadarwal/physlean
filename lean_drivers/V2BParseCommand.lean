/-
Pinned-toolchain parser driver for V2-b Lean behavioral body extraction.

The trusted original module is parsed and elaborated only up to (but never
including) the target command.  That reconstructs file-local syntax,
namespaces, scopes, and notation.  Each spliced module is then parsed for one
command at the exact target position.  The generated command is never
elaborated.

This is intentionally a parser boundary, not a verifier.  It reports the raw
UTF-8 byte boundary of the first complete command so the Python producer can
retain exactly the generated body prefix and hash it.  Every result is emitted
as a single JSON object after OUTPUT_MARKER; unrelated output from trusted
prior commands therefore cannot be mistaken for driver evidence.
-/
import Lean

open Lean

namespace V2BParseCommand

def MANIFEST_SCHEMA := "v2b_lean_parse_manifest_v1"
def OUTPUT_SCHEMA := "v2b_lean_parse_result_v1"
def OUTPUT_MARKER := "@@V2B_LEAN_PARSE@@"

structure SampleSpec where
  id : String
  splicedFile : String
  generatedEndByte : Nat
  deriving FromJson

structure OptionOverride where
  name : String
  value : String
  deriving FromJson

structure Manifest where
  schema : String
  originalFile : String
  moduleSetupFile : String
  moduleName : String
  targetIdentity : String
  targetKind : String
  targetStartByte : Nat
  targetEndByte : Nat
  headerEndByte : Nat
  bodyDelimiter : String
  optionOverrides : Array OptionOverride
  samples : Array SampleSpec
  deriving FromJson

structure Prepared where
  parserState : Parser.ModuleParserState
  commandState : Elab.Command.State
  originalKind : Name
  originalHeaderProjection : String
  nPriorCommands : Nat

def hard {α : Type} (message : String) : IO α :=
  throw <| IO.userError s!"V2B trusted-input error: {message}"

def allMessageCount (messages : MessageLog) : Nat :=
  messages.reportedPlusUnreported.size

def rawPos (byteIdx : Nat) : String.Pos.Raw := ⟨byteIdx⟩

def requireRawPosition (label : String) (source : String)
    (byteIdx : Nat) : IO Unit := do
  let pos := rawPos byteIdx
  unless pos.isValid source do
    hard s!"{label}={byteIdx} is not a valid UTF-8 boundary"

def slice (source : String) (startByte endByte : Nat) : String :=
  String.Pos.Raw.extract source (rawPos startByte) (rawPos endByte)

partial def tokenCrosses (stx : Syntax) (boundary : String.Pos.Raw) : Bool :=
  match stx with
  | .missing => false
  | .atom info _ | .ident info _ _ _ =>
      match info.getRange? (canonicalOnly := true) with
      | some range => range.start < boundary && boundary < range.stop
      | none => false
  | .node _ _ args => args.any fun arg => tokenCrosses arg boundary

partial def headerProjection? (stx : Syntax)
    (boundary : String.Pos.Raw) : Option Json :=
  match stx with
  | .missing => none
  | .atom info value =>
      match info.getRange? (canonicalOnly := true) with
      | some range =>
          if range.stop <= boundary then
            some <| .arr #[toJson "atom", toJson value,
              toJson range.start.byteIdx, toJson range.stop.byteIdx]
          else
            none
      | none => none
  | .ident info rawValue value _ =>
      match info.getRange? (canonicalOnly := true) with
      | some range =>
          if range.stop <= boundary then
            some <| .arr #[toJson "ident", toJson rawValue.toString,
              toJson value.toString, toJson range.start.byteIdx,
              toJson range.stop.byteIdx]
          else
            none
      | none => none
  | .node _ kind args =>
      let children : Array Json := Id.run do
        let mut result := #[]
        for index in [:args.size] do
          if let some child := headerProjection? args[index]! boundary then
            result := result.push <| .arr #[toJson index, child]
        return result
      if children.isEmpty then
        none
      else
        some <| .arr #[toJson "node", toJson kind.toString, .arr children]

def headerProjection (stx : Syntax) (boundary : Nat) : IO String := do
  let boundary := rawPos boundary
  if tokenCrosses stx boundary then
    hard "a trusted original syntax token crosses the header/body boundary"
  let some projection := headerProjection? stx boundary
    | hard "trusted target has no syntax projection before its body"
  pure projection.compress

def disableAsync (state : Elab.Command.State) : Elab.Command.State :=
  match state.scopes with
  | [] => state
  | scope :: scopes =>
      let scope := { scope with opts := Elab.async.set scope.opts false }
      { state with scopes := scope :: scopes }

def parserContext (state : Elab.Command.State) : Parser.ParserModuleContext :=
  let scope := state.scopes.head!
  { env := state.env
    options := Elab.async.set scope.opts false
    currNamespace := scope.currNamespace
    openDecls := scope.openDecls }

def elaboratePriorCommand (inputCtx : Parser.InputContext)
    (cmdPos : String.Pos.Raw) (stx : Syntax)
    (state : Elab.Command.State) : IO Elab.Command.State := do
  let state := disableAsync { state with messages := {} }
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
        pure <| disableAsync { nextState with messages := {} }

partial def prepareAtTarget (inputCtx : Parser.InputContext)
    (targetStart targetEnd headerEnd : Nat)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (nPrior : Nat) : IO Prepared := do
  let commandState := disableAsync commandState
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
    let originalHeaderProjection <- headerProjection stx headerEnd
    pure { parserState
           commandState
           originalKind := stx.getKind
           originalHeaderProjection
           nPriorCommands := nPrior }
  else
    if startByte > targetStart || endByte > targetStart then
      hard s!"committed target start {targetStart} lies inside or before \
        original command [{startByte},{endByte})"
    let nextCommandState <-
      elaboratePriorCommand inputCtx cmdPos stx commandState
    prepareAtTarget inputCtx targetStart targetEnd headerEnd nextParserState
      nextCommandState (nPrior + 1)

def emit (value : Json) : IO Unit :=
  IO.println s!"{OUTPUT_MARKER}{Json.compress value}"

def emitPrevalidation (manifest : Manifest) (prepared : Prepared) : IO Unit :=
  emit <| Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "prevalidation"),
    ("module_name", toJson manifest.moduleName),
    ("target_identity", toJson manifest.targetIdentity),
    ("target_kind", toJson manifest.targetKind),
    ("target_start_byte", toJson manifest.targetStartByte),
    ("target_end_byte", toJson manifest.targetEndByte),
    ("header_end_byte", toJson manifest.headerEndByte),
    ("body_delimiter", toJson manifest.bodyDelimiter),
    ("syntax_kind", toJson prepared.originalKind.toString),
    ("header_syntax_projection", toJson
      prepared.originalHeaderProjection),
    ("n_prior_commands", toJson prepared.nPriorCommands),
    ("generated_target_elaborated", toJson false)
  ]

def failureJson (sampleId reason : String) (nParseMessages : Nat := 0)
    (recovering : Bool := false) (hasMissing : Bool := false) : Json :=
  Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "sample"),
    ("sample_id", toJson sampleId),
    ("status", toJson "extraction-failure"),
    ("reason", toJson reason),
    ("n_parse_messages", toJson nParseMessages),
    ("recovering", toJson recovering),
    ("has_missing", toJson hasMissing),
    ("generated_target_elaborated", toJson false)
  ]

def successJson (sampleId : String) (prepared : Prepared)
    (manifest : Manifest) (endByte : Nat) : Json :=
  Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "sample"),
    ("sample_id", toJson sampleId),
    ("status", toJson "extracted"),
    ("start_byte", toJson manifest.targetStartByte),
    ("end_byte", toJson endByte),
    ("body_start_byte", toJson manifest.headerEndByte),
    ("body_bytes", toJson (endByte - manifest.headerEndByte)),
    ("syntax_kind", toJson prepared.originalKind.toString),
    ("n_parse_messages", toJson (0 : Nat)),
    ("recovering", toJson false),
    ("has_missing", toJson false),
    ("generated_target_elaborated", toJson false)
  ]

def validateSplice (original spliced : String) (manifest : Manifest)
    (sample : SampleSpec) : IO Unit := do
  requireRawPosition "headerEndByte(original)" original
    manifest.headerEndByte
  requireRawPosition "targetEndByte(original)" original
    manifest.targetEndByte
  requireRawPosition "headerEndByte(spliced)" spliced
    manifest.headerEndByte
  requireRawPosition "generatedEndByte(spliced)" spliced
    sample.generatedEndByte
  unless manifest.headerEndByte <= sample.generatedEndByte do
    hard s!"sample {sample.id}: generated end precedes its start"
  unless slice original 0 manifest.headerEndByte ==
      slice spliced 0 manifest.headerEndByte do
    hard s!"sample {sample.id}: prefix through target header drifted"
  unless slice original manifest.targetEndByte original.utf8ByteSize ==
      slice spliced sample.generatedEndByte spliced.utf8ByteSize do
    hard s!"sample {sample.id}: suffix after target drifted"

def startsWithAt (source : String) (startByte : Nat) (token : String) : Bool :=
  let endByte := startByte + token.utf8ByteSize
  endByte <= source.utf8ByteSize && slice source startByte endByte == token

structure ParsedCommand where
  stx : Syntax
  parserState : Parser.ModuleParserState
  messages : MessageLog

def parseOneCommand (source fileName : String) (prepared : Prepared) :
    ParsedCommand :=
  let inputCtx := Parser.mkInputContext source fileName
  let (stx, parserState, messages) :=
    Parser.parseCommand inputCtx (parserContext prepared.commandState)
      prepared.parserState {}
  { stx, parserState, messages }

def parseSample (original : String) (manifest : Manifest)
    (prepared : Prepared) (sample : SampleSpec) : IO Json := do
  if sample.id.isEmpty then
    hard "sample id is empty"
  if sample.splicedFile.isEmpty then
    hard s!"sample {sample.id}: spliced file path is empty"
  let spliced <- IO.FS.readFile sample.splicedFile
  validateSplice original spliced manifest sample
  if sample.generatedEndByte == manifest.headerEndByte then
    return failureJson sample.id "empty-body"
  unless startsWithAt spliced manifest.headerEndByte
      manifest.bodyDelimiter do
    return failureJson sample.id "body-delimiter-drift"
  let truncated := slice spliced 0 sample.generatedEndByte
  let parsed := parseOneCommand truncated sample.splicedFile prepared
  let stx := parsed.stx
  let nMessages := allMessageCount parsed.messages
  if nMessages != 0 || parsed.parserState.recovering then
    pure <| failureJson sample.id "parse-error-in-target" nMessages
      parsed.parserState.recovering stx.hasMissing
  else if stx.hasMissing then
    pure <| failureJson sample.id "has-missing" nMessages false true
  else if Parser.isTerminalCommand stx then
    pure <| failureJson sample.id "terminal-command"
  else
    let some range := stx.getRange? (canonicalOnly := true)
      | pure <| failureJson sample.id "missing-source-range" nMessages
    let startByte := range.start.byteIdx
    let endByte := range.stop.byteIdx
    if startByte != manifest.targetStartByte then
      pure <| failureJson sample.id "target-start-drift" nMessages
    else if stx.getKind != prepared.originalKind then
      pure <| failureJson sample.id "syntax-kind-drift" nMessages
    else if tokenCrosses stx (rawPos manifest.headerEndByte) then
      pure <| failureJson sample.id "token-crosses-header-boundary"
    else if (headerProjection? stx (rawPos manifest.headerEndByte)).map
        Json.compress != some prepared.originalHeaderProjection then
      pure <| failureJson sample.id "header-syntax-drift"
    else if endByte <= manifest.headerEndByte then
      pure <| failureJson sample.id "empty-body" nMessages
    else if endByte > sample.generatedEndByte then
      pure <| failureJson sample.id "end-beyond-generated-region" nMessages
    else
      let reconstructed := slice spliced 0 endByte ++
        slice original manifest.targetEndByte original.utf8ByteSize
      let full := parseOneCommand reconstructed sample.splicedFile prepared
      let fullMessages := allMessageCount full.messages
      let fullRange? := full.stx.getRange? (canonicalOnly := true)
      let fullProjection? :=
        (headerProjection? full.stx (rawPos manifest.headerEndByte)).map
          Json.compress
      if fullMessages != 0 || full.parserState.recovering ||
          full.stx.hasMissing || Parser.isTerminalCommand full.stx ||
          full.stx.getKind != prepared.originalKind ||
          tokenCrosses full.stx (rawPos manifest.headerEndByte) ||
          fullProjection? != some prepared.originalHeaderProjection ||
          fullRange?.map (fun fullRange =>
            (fullRange.start.byteIdx, fullRange.stop.byteIdx)) !=
              some (startByte, endByte) then
        pure <| failureJson sample.id "reconstructed-module-parse-drift"
          fullMessages full.parserState.recovering full.stx.hasMissing
      else
        pure <| successJson sample.id prepared manifest endByte

def readManifest (path : String) : IO Manifest := do
  let contents <- IO.FS.readFile path
  let json <- match Json.parse contents with
    | Except.ok value => pure value
    | Except.error message => hard s!"manifest JSON parse failed: {message}"
  match fromJson? json with
  | Except.ok manifest => pure manifest
  | Except.error message => hard s!"manifest schema decode failed: {message}"

def run (manifestPath : String) : IO Unit := do
  let manifest <- readManifest manifestPath
  unless manifest.schema == MANIFEST_SCHEMA do
    hard s!"manifest schema {manifest.schema} != {MANIFEST_SCHEMA}"
  if manifest.originalFile.isEmpty || manifest.moduleSetupFile.isEmpty ||
      manifest.moduleName.isEmpty || manifest.targetIdentity.isEmpty then
    hard "original/setup/module/target identity fields must be nonempty"
  unless ["theorem", "lemma", "def"].contains manifest.targetKind do
    hard "targetKind is not an eligible frozen Lean kind"
  unless [":=", "where", "|"].contains manifest.bodyDelimiter do
    hard "bodyDelimiter is outside the frozen Lean delimiter set"
  if manifest.samples.isEmpty then
    hard "manifest sample list is empty"
  let mut seenOptions : Array String := #[]
  for option in manifest.optionOverrides do
    if option.name.isEmpty || option.value.isEmpty ||
        seenOptions.contains option.name then
      hard "option override is empty or duplicated"
    if option.name == "Elab.async" then
      hard "Elab.async cannot be overridden by a manifest"
    seenOptions := seenOptions.push option.name
  let mut seenIds : Array String := #[]
  for sample in manifest.samples do
    if sample.id.isEmpty || sample.splicedFile.isEmpty ||
        seenIds.contains sample.id then
      hard "sample id/path is empty or its id is duplicated"
    seenIds := seenIds.push sample.id
  let original <- IO.FS.readFile manifest.originalFile
  requireRawPosition "targetStartByte" original manifest.targetStartByte
  requireRawPosition "headerEndByte" original manifest.headerEndByte
  requireRawPosition "targetEndByte" original manifest.targetEndByte
  unless manifest.targetStartByte < manifest.headerEndByte &&
      manifest.headerEndByte < manifest.targetEndByte do
    hard "target/header/end byte order is invalid"
  unless startsWithAt original manifest.headerEndByte
      manifest.bodyDelimiter do
    hard "trusted original body delimiter disagrees with the manifest"
  let inputCtx := Parser.mkInputContext original manifest.originalFile
  let (header, parserState, headerMessages) <- Parser.parseHeader inputCtx
  if allMessageCount headerMessages != 0 || parserState.recovering ||
      header.raw.hasMissing then
    hard "trusted original module header required parser recovery"
  let setup <- ModuleSetup.load manifest.moduleSetupFile
  unless setup.name == manifest.moduleName.toName do
    hard s!"module setup name {setup.name} != {manifest.moduleName}"
  let baseOptions := Elab.async.set ({} : Options) false
  let options := baseOptions.mergeBy (fun _ _ setupValue => setupValue)
    setup.options.toOptions
  unsafe Lean.enableInitializersExecution
  let (_, (environment, importMessages)) <-
    IO.FS.withIsolatedStreams (isolateStderr := true) do
      setup.dynlibs.forM Lean.loadDynlib
      Elab.processHeaderCore (leakEnv := true)
        (Elab.HeaderSyntax.startPos header)
        (setup.imports?.getD (Elab.HeaderSyntax.imports header))
        (strictOr setup.isModule (Elab.HeaderSyntax.isModule header))
        options {} inputCtx 0
        setup.plugins setup.name setup.package? setup.importArts
        (headerStx? := header) (origHeaderStx? := header)
  if importMessages.hasErrors then
    for message in importMessages.reportedPlusUnreported do
      IO.eprintln (← message.toString)
    hard "trusted original module imports did not load"
  let mut options <- Language.Lean.reparseOptions options
  for option in manifest.optionOverrides do
    options := options.set option.name.toName option.value
  options <- Language.Lean.reparseOptions options
  options := Elab.async.set options false
  let commandState := Elab.Command.mkState environment {} options
  let prepared <- prepareAtTarget inputCtx manifest.targetStartByte
    manifest.targetEndByte manifest.headerEndByte parserState commandState 0
  emitPrevalidation manifest prepared
  for sample in manifest.samples do
    emit (← parseSample original manifest prepared sample)

end V2BParseCommand

def main (args : List String) : IO UInt32 := do
  match args with
  | [manifestPath] =>
      V2BParseCommand.run manifestPath
      pure 0
  | _ =>
      IO.eprintln "usage: V2BParseCommand <manifest.json>"
      pure 2
