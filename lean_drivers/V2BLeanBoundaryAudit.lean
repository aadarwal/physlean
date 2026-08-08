/-
Parser-backed prospective audit of Lean declaration body boundaries.

The V2-a extraction's lexical header/body fields are diagnostic only.  For
each exact committed command span, this driver enumerates exact canonical
`:=`, `where`, and `|` token starts from the original syntax tree, tests them
in raw-byte order with a same-form minimal sentinel, and selects the first
sentinel-valid declaration-value boundary.  Sentinels are parsed but never
elaborated.  Trusted original commands are elaborated synchronously only so
later commands see the exact file-local parser/elaborator state.

One invocation handles one source file and one exact Lake ModuleSetup.  A
Python producer partitions the corpus manifest into these module invocations,
binds every input byte, validates marker-only output, and aggregates rows in
the committed corpus order.
-/
import Lean

open Lean

namespace V2BLeanBoundaryAudit

def MANIFEST_SCHEMA := "v2b_lean_boundary_driver_manifest_v1"
def OUTPUT_SCHEMA := "v2b_lean_boundary_driver_output_v1"
def OUTPUT_MARKER := "@@V2B_LEAN_BOUNDARY@@"
def BODY_DELIMITERS : Array String := #[":=", "where", "|"]

def isLowerHexSha256 (value : String) : Bool :=
  value.length == 64 && value.toList.all fun char =>
    ('0' <= char && char <= '9') || ('a' <= char && char <= 'f')

structure OptionOverride where
  name : String
  value : String
  deriving FromJson

structure SpanSpec where
  id : String
  startByte : Nat
  endByte : Nat
  deriving FromJson, Inhabited

structure Manifest where
  schema : String
  invocationBinding : String
  originalFile : String
  moduleSetupFile : String
  moduleName : String
  optionOverrides : Array OptionOverride
  spans : Array SpanSpec
  deriving FromJson

structure Resolution where
  status : String
  reason : Option String
  headerEndByte : Option Nat
  delimiter : Option String
  syntaxKind : Option String
  nCandidateStartsTotal : Nat
  nTested : Nat
  nUntestedAfterChoice : Nat
  rejectedStarts : Array Nat

def hard {α : Type} (message : String) : IO α :=
  throw <| IO.userError s!"V2B trusted-input error: {message}"

def allMessageCount (messages : MessageLog) : Nat :=
  messages.reportedPlusUnreported.size

def rawPos (byteIdx : Nat) : String.Pos.Raw := ⟨byteIdx⟩

def requireRawPosition (label : String) (source : String)
    (byteIdx : Nat) : IO Unit := do
  unless (rawPos byteIdx).isValid source do
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

partial def canonicalTokenRangesStartingAt (stx : Syntax)
    (boundary : String.Pos.Raw) : Array (Nat × Nat) :=
  match stx with
  | .missing => #[]
  | .atom info _ | .ident info _ _ _ =>
      match info.getRange? (canonicalOnly := true) with
      | some range =>
          if range.start == boundary then
            #[(range.start.byteIdx, range.stop.byteIdx)]
          else
            #[]
      | none => #[]
  | .node _ _ args => args.foldl (init := #[]) fun ranges arg =>
      ranges ++ canonicalTokenRangesStartingAt arg boundary

def hasExactCanonicalTokenAt (stx : Syntax) (source : String)
    (boundary : Nat) (spelling : String) : Bool :=
  let ranges := canonicalTokenRangesStartingAt stx (rawPos boundary)
  let expectedEnd := boundary + spelling.utf8ByteSize
  ranges == #[(boundary, expectedEnd)] &&
    expectedEnd <= source.utf8ByteSize &&
    slice source boundary expectedEnd == spelling

partial def allowedCanonicalTokenStarts (stx : Syntax) (source : String)
    (commandStart commandEnd : Nat) : Array Nat :=
  match stx with
  | .missing => #[]
  | .atom info _ | .ident info _ _ _ =>
      match info.getRange? (canonicalOnly := true) with
      | some range =>
          let startByte := range.start.byteIdx
          let endByte := range.stop.byteIdx
          if commandStart < startByte && endByte <= commandEnd &&
              BODY_DELIMITERS.contains
                (slice source startByte endByte) then
            #[startByte]
          else
            #[]
      | none => #[]
  | .node _ _ args => args.foldl (init := #[]) fun starts arg =>
      starts ++ allowedCanonicalTokenStarts arg source commandStart commandEnd

def sortedUniqueAllowedStarts (stx : Syntax) (source : String)
    (commandStart commandEnd : Nat) : Array Nat :=
  let unique := (allowedCanonicalTokenStarts stx source commandStart
    commandEnd).foldl (init := #[]) fun starts startByte =>
      if starts.contains startByte then starts else starts.push startByte
  unique.qsort fun left right => left < right

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

def elaborateTrustedCommand (inputCtx : Parser.InputContext)
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
      hard s!"trusted-command elaboration raised: \
        {← exception.toMessageData.toString}"
  | Except.ok (_, nextState) =>
      if nextState.messages.hasErrors then
        hard "a trusted original command does not elaborate"
      else if !nextState.snapshotTasks.isEmpty then
        hard "a trusted original command spawned asynchronous tasks"
      else
        pure <| disableAsync { nextState with messages := {} }

def lineIndentBefore (source : String) (byteIdx : Nat) : String :=
  let before := slice source 0 byteIdx
  let linePrefix := (before.splitOn "\n").getLastD ""
  String.ofList <| linePrefix.toList.takeWhile fun char =>
    char == ' ' || char == '\t'

def bodyBoundaryProbeSuffix (source : String) (targetStart : Nat)
    (bodyDelimiter : String) : IO String := do
  match bodyDelimiter with
  | ":=" => pure ":= _"
  | "|" => pure "| _ => _"
  | "where" =>
      let indent := lineIndentBefore source targetStart
      pure s!"where\n{indent}  __v2b_probe := _"
  | other => hard s!"unsupported body-boundary probe delimiter {other}"

def bodyBoundaryProbeValid (inputCtx : Parser.InputContext)
    (targetStart headerEnd : Nat) (bodyDelimiter : String)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (originalStx : Syntax) : IO Bool := do
  if tokenCrosses originalStx (rawPos headerEnd) ||
      !hasExactCanonicalTokenAt originalStx inputCtx.inputString headerEnd
        bodyDelimiter then
    return false
  let some originalProjection :=
      headerProjection? originalStx (rawPos headerEnd)
    | return false
  let originalHeaderProjection := originalProjection.compress
  let suffix <- bodyBoundaryProbeSuffix inputCtx.inputString targetStart
    bodyDelimiter
  let probeSource := slice inputCtx.inputString 0 headerEnd ++ suffix
  let probeCtx := Parser.mkInputContext probeSource inputCtx.fileName
  let (probeStx, probeParserState, probeMessages) :=
    Parser.parseCommand probeCtx (parserContext commandState) parserState {}
  if allMessageCount probeMessages != 0 || probeParserState.recovering ||
      probeStx.hasMissing || Parser.isTerminalCommand probeStx then
    return false
  let some probeRange := probeStx.getRange? (canonicalOnly := true)
    | return false
  pure <| probeRange.start.byteIdx == targetStart &&
    probeRange.stop.byteIdx == probeSource.utf8ByteSize &&
    probeStx.getKind == originalStx.getKind &&
    !tokenCrosses probeStx (rawPos headerEnd) &&
    hasExactCanonicalTokenAt probeStx probeSource headerEnd bodyDelimiter &&
    (headerProjection? probeStx (rawPos headerEnd)).map Json.compress ==
      some originalHeaderProjection

def resolveBoundary (inputCtx : Parser.InputContext) (span : SpanSpec)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (stx : Syntax) : IO Resolution := do
  let starts := sortedUniqueAllowedStarts stx inputCtx.inputString
    span.startByte span.endByte
  if starts.isEmpty then
    return {
      status := "unsplit"
      reason := some "no-canonical-candidate"
      headerEndByte := none
      delimiter := none
      syntaxKind := some stx.getKind.toString
      nCandidateStartsTotal := 0
      nTested := 0
      nUntestedAfterChoice := 0
      rejectedStarts := #[] }
  let mut rejected : Array Nat := #[]
  let mut nTested := 0
  for startByte in starts do
    nTested := nTested + 1
    let ranges := canonicalTokenRangesStartingAt stx (rawPos startByte)
    if ranges.size == 1 then
      let (tokenStart, tokenEnd) := ranges[0]!
      let delimiter := slice inputCtx.inputString tokenStart tokenEnd
      if BODY_DELIMITERS.contains delimiter &&
          hasExactCanonicalTokenAt stx inputCtx.inputString startByte
            delimiter &&
          (← bodyBoundaryProbeValid inputCtx span.startByte startByte
            delimiter parserState commandState stx) then
        return {
          status := "resolved"
          reason := none
          headerEndByte := some startByte
          delimiter := some delimiter
          syntaxKind := some stx.getKind.toString
          nCandidateStartsTotal := starts.size
          nTested
          nUntestedAfterChoice := starts.size - nTested
          rejectedStarts := rejected }
    rejected := rejected.push startByte
  pure {
    status := "unsplit"
    reason := some "no-sentinel-valid-candidate"
    headerEndByte := none
    delimiter := none
    syntaxKind := some stx.getKind.toString
    nCandidateStartsTotal := starts.size
    nTested
    nUntestedAfterChoice := 0
    rejectedStarts := rejected }

def notExactResolution : Resolution := {
  status := "unsplit"
  reason := some "not-exact-command-span"
  headerEndByte := none
  delimiter := none
  syntaxKind := none
  nCandidateStartsTotal := 0
  nTested := 0
  nUntestedAfterChoice := 0
  rejectedStarts := #[] }

def optionalJson {α : Type} [ToJson α] (value : Option α) : Json :=
  match value with
  | some item => toJson item
  | none => .null

def spanJson (span : SpanSpec) (resolution : Resolution) : Json :=
  Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "span"),
    ("span_id", toJson span.id),
    ("status", toJson resolution.status),
    ("reason", optionalJson resolution.reason),
    ("start_byte", toJson span.startByte),
    ("end_byte", toJson span.endByte),
    ("header_end_byte", optionalJson resolution.headerEndByte),
    ("delimiter", optionalJson resolution.delimiter),
    ("syntax_kind", optionalJson resolution.syntaxKind),
    ("n_candidate_starts_total", toJson
      resolution.nCandidateStartsTotal),
    ("n_tested", toJson resolution.nTested),
    ("n_untested_after_choice", toJson
      resolution.nUntestedAfterChoice),
    ("rejected_starts", toJson resolution.rejectedStarts),
    ("sentinels_elaborated", toJson false)
  ]

partial def auditCommands (inputCtx : Parser.InputContext)
    (spans : Array SpanSpec) (spanIndex : Nat)
    (parserState : Parser.ModuleParserState)
    (commandState : Elab.Command.State) (nCommands : Nat)
    (rows : Array Json) : IO (Array Json × Nat) := do
  if spanIndex == spans.size then
    return (rows, nCommands)
  let commandState := disableAsync commandState
  let cmdPos := parserState.pos
  let (stx, nextParserState, parseMessages) :=
    Parser.parseCommand inputCtx (parserContext commandState) parserState {}
  if allMessageCount parseMessages != 0 || nextParserState.recovering ||
      stx.hasMissing then
    hard "trusted original source required parser recovery"
  if Parser.isTerminalCommand stx then
    let mut rows := rows
    for index in [spanIndex:spans.size] do
      rows := rows.push <| spanJson spans[index]! notExactResolution
    return (rows, nCommands)
  let some range := stx.getRange? (canonicalOnly := true)
    | hard "trusted original command has no canonical source range"
  let commandStart := range.start.byteIdx
  let commandEnd := range.stop.byteIdx
  let mut index := spanIndex
  let mut rows := rows
  while index < spans.size && spans[index]!.startByte < commandEnd do
    let span := spans[index]!
    let resolution <-
      if span.startByte == commandStart && span.endByte == commandEnd then
        resolveBoundary inputCtx span parserState commandState stx
      else
        pure notExactResolution
    rows := rows.push <| spanJson span resolution
    index := index + 1
  let nextCommandState <- elaborateTrustedCommand inputCtx cmdPos stx
    commandState
  auditCommands inputCtx spans index nextParserState nextCommandState
    (nCommands + 1) rows

def emit (value : Json) : IO Unit :=
  IO.println s!"{OUTPUT_MARKER}{Json.compress value}"

def moduleJson (manifest : Manifest) (nCommands : Nat) : Json :=
  Json.mkObj [
    ("schema", toJson OUTPUT_SCHEMA),
    ("record_type", toJson "module"),
    ("invocation_binding", toJson manifest.invocationBinding),
    ("module_name", toJson manifest.moduleName),
    ("n_spans", toJson manifest.spans.size),
    ("n_commands_parsed", toJson nCommands),
    ("trusted_original_commands_elaborated", toJson true),
    ("sentinels_elaborated", toJson false)
  ]

def readManifest (path : String) : IO Manifest := do
  let contents <- IO.FS.readFile path
  let json <- match Json.parse contents with
    | Except.ok value => pure value
    | Except.error message => hard s!"manifest JSON parse failed: {message}"
  match fromJson? json with
  | Except.ok manifest => pure manifest
  | Except.error message => hard s!"manifest schema decode failed: {message}"

def validateOptions (options : Array OptionOverride) : IO Unit := do
  let mut seen : Array String := #[]
  for option in options do
    if option.name.isEmpty || option.value.isEmpty ||
        seen.contains option.name then
      hard "option override is empty or duplicated"
    if option.name == "Elab.async" then
      hard "Elab.async cannot be overridden by a manifest"
    seen := seen.push option.name

def run (manifestPath : String) : IO Unit := do
  let manifest <- readManifest manifestPath
  unless manifest.schema == MANIFEST_SCHEMA do
    hard s!"manifest schema {manifest.schema} != {MANIFEST_SCHEMA}"
  unless isLowerHexSha256 manifest.invocationBinding do
    hard "invocationBinding is not a lowercase 64-hex SHA256"
  if manifest.originalFile.isEmpty || manifest.moduleSetupFile.isEmpty ||
      manifest.moduleName.isEmpty || manifest.spans.isEmpty then
    hard "original/setup/module/spans fields must be nonempty"
  validateOptions manifest.optionOverrides
  unless manifest.optionOverrides.isEmpty do
    hard "boundary audit optionOverrides must be empty"
  let original <- IO.FS.readFile manifest.originalFile
  let mut seenIds : Array String := #[]
  let mut previous : Option (Nat × Nat) := none
  for span in manifest.spans do
    if span.id.isEmpty || seenIds.contains span.id then
      hard "span id is empty or duplicated"
    requireRawPosition s!"span {span.id} start" original span.startByte
    requireRawPosition s!"span {span.id} end" original span.endByte
    unless span.startByte < span.endByte do
      hard s!"span {span.id} has invalid byte order"
    if let some (previousStart, previousEnd) := previous then
      if span.startByte < previousStart ||
          (span.startByte == previousStart && span.endByte <= previousEnd) then
        hard "span list is not in strict (start,end) order"
    seenIds := seenIds.push span.id
    previous := some (span.startByte, span.endByte)
  let inputCtx := Parser.mkInputContext original manifest.originalFile
  let (header, parserState, headerMessages) <- Parser.parseHeader inputCtx
  if allMessageCount headerMessages != 0 || parserState.recovering ||
      header.raw.hasMissing then
    hard "trusted original module header required parser recovery"
  let setup <- ModuleSetup.load manifest.moduleSetupFile
  unless setup.name == manifest.moduleName.toName do
    hard s!"module setup name {setup.name} != {manifest.moduleName}"
  let mut commandLineOptions : Options := {}
  for option in manifest.optionOverrides do
    commandLineOptions := commandLineOptions.set option.name.toName
      option.value
  commandLineOptions := Lean.internal.cmdlineSnapshots.setIfNotSet
    commandLineOptions true
  commandLineOptions := Elab.async.set commandLineOptions false
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
        options {} inputCtx 0
        setup.plugins setup.name setup.package? setup.importArts
        (headerStx? := header) (origHeaderStx? := header)
  if importMessages.hasErrors then
    for message in importMessages.reportedPlusUnreported do
      IO.eprintln (← message.toString)
    hard "trusted original module imports did not load"
  let mut options <- Language.Lean.reparseOptions options
  options := Elab.async.set options false
  let commandState := Elab.Command.mkState environment {} options
  let (rows, nCommands) <- auditCommands inputCtx manifest.spans 0
    parserState commandState 0 #[]
  unless rows.size == manifest.spans.size do
    hard "internal boundary row count does not match the manifest"
  emit <| moduleJson manifest nCommands
  for row in rows do
    emit row

end V2BLeanBoundaryAudit

def main (args : List String) : IO UInt32 := do
  match args with
  | [manifestPath] =>
      V2BLeanBoundaryAudit.run manifestPath
      pure 0
  | _ =>
      IO.eprintln "usage: V2BLeanBoundaryAudit <module-manifest.json>"
      pure 2
