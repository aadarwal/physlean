import Physlib

open Lean Elab Command

/-- Emit the complete parser token table after the pinned PhysLib umbrella
import. `V2B_TOKEN_OUT` is supplied by the source-locked cluster job. -/
elab "#v2b_dump_parser_tokens" : command => do
  let some out ← liftIO <| IO.getEnv "V2B_TOKEN_OUT"
    | throwError "V2B_TOKEN_OUT is required"
  let tokens := (Parser.getTokenTable (← getEnv)).values.qsort (fun a b => a < b)
  if tokens.any fun token => token.contains '\n' || token.contains '\r' then
    throwError "parser token table contains an LF/CR token"
  liftIO <| IO.FS.writeFile out
    (String.intercalate "\n" tokens.toList ++ "\n")

#v2b_dump_parser_tokens
