import Batteries

open Lean Elab Command

private def sortDedup (values : Array String) : Array String := Id.run do
  let sorted := values.qsort (fun a b => a < b)
  let mut dedup := #[]
  for value in sorted do
    if dedup.back? != some value then
      dedup := dedup.push value
  return dedup

/-- Identifier-valued parser dispatch heads are intentionally not all
reserved Lean tokens (notably tactic names).  Extract the exact registered
leading/trailing keys separately, excluding literal-kind and non-simple
pseudo keys rather than mistaking `ident`, `num`, etc. for source syntax. -/
private def parserDispatchKeys (env : Environment) :
    Array String × Array String := Id.run do
  let state := Parser.parserExtension.getState env
  let literalKinds := [Lean.choiceKind, Lean.identKind, Lean.strLitKind,
    Lean.numLitKind, Lean.scientificLitKind, Lean.charLitKind,
    Lean.nameLitKind]
  let mut dispatch := #[]
  let mut excluded := literalKinds.toArray.map Name.toString
  let visit (token : Name) (dispatch excluded : Array String) :=
    if literalKinds.contains token then
      (dispatch, excluded.push token.toString)
    else
      match token with
      | .str .anonymous value => (dispatch.push value, excluded)
      | _ => (dispatch, excluded.push token.toString)
  for (_, category) in state.categories do
    for (token, _) in category.tables.leadingTable do
      let next := visit token dispatch excluded
      dispatch := next.1
      excluded := next.2
    for (token, _) in category.tables.trailingTable do
      let next := visit token dispatch excluded
      dispatch := next.1
      excluded := next.2
  return (sortDedup dispatch, sortDedup excluded)

/-- Emit the reserved-token table, contextual dispatch keys, and excluded
pseudo keys after the pinned Batteries umbrella import. -/
elab "#v2b_dump_parser_tokens" : command => do
  let some reservedOut ← liftIO <| IO.getEnv "V2B_RESERVED_OUT"
    | throwError "V2B_RESERVED_OUT is required"
  let some dispatchOut ← liftIO <| IO.getEnv "V2B_DISPATCH_OUT"
    | throwError "V2B_DISPATCH_OUT is required"
  let some excludedOut ← liftIO <| IO.getEnv "V2B_EXCLUDED_OUT"
    | throwError "V2B_EXCLUDED_OUT is required"
  let env ← getEnv
  let reserved := sortDedup (Parser.getTokenTable env).values
  let (dispatch, excluded) := parserDispatchKeys env
  for (name, values) in [("reserved", reserved), ("dispatch", dispatch),
                          ("excluded", excluded)] do
    if values.isEmpty || values.any fun token =>
        token.contains '\n' || token.contains '\r' then
      throwError "{name} parser evidence is empty or contains LF/CR"
  liftIO <| IO.FS.writeFile reservedOut
    (String.intercalate "\n" reserved.toList ++ "\n")
  liftIO <| IO.FS.writeFile dispatchOut
    (String.intercalate "\n" dispatch.toList ++ "\n")
  liftIO <| IO.FS.writeFile excludedOut
    (String.intercalate "\n" excluded.toList ++ "\n")

#v2b_dump_parser_tokens
