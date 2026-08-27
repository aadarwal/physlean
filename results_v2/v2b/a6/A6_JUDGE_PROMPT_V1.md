# A6 independent blind-judge contract

Read only the supplied committed `a6_blind_job19930941.json`. Do not open or
search for any packet, repository, source identity, similarity statistic,
hidden role, sample, salt, result, or model outcome.

For every opaque pair, apply exactly the presentation rubric:

> duplicate = the same implementation/specification modulo ONE systematic
> identifier renaming; a shared syntax skeleton alone is not enough, and
> differing API calls or referenced constants are not-duplicate

Judge the actual implementation/specification, not whether both snippets have
similar formatting. Use `duplicate` only when the change is a systematic
template/identifier substitution and the operational proof/code is otherwise
the same. If an API call, referenced constant, literal, control flow, theorem
claim, or proof operation differs materially, use `not-duplicate`.

Write one JSON object and nothing else to your assigned output path:

```json
{
  "schema": "v2b_a6_blind_judgments_v1",
  "adjudicator": {"id": "ASSIGNED_ID", "model": "EXACT_MODEL_LABEL", "fresh_context": true},
  "independence_declaration": "fresh-context;blind-presentation-only;no-packet-identities-statistics-roles-sample-salt-or-outcomes",
  "presentation_sha256": "SUPPLIED_SHA256",
  "rubric": "SUPPLIED_EXACT_RUBRIC",
  "judgments": [
    {"pair_id": "P-...", "label": "duplicate|not-duplicate", "reason": "1-500 character pair-local reason"}
  ]
}
```

Cover every presentation pair exactly once in presentation order. Do not infer
or mention repository/corpus identities. Do not coordinate with another judge.
