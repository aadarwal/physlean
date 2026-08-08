#!/usr/bin/env python3
"""Localhost-only human reviewer for the committed blind V2-b A6 pack.

The mutable autosave draft and the write-once final label artifact are
different paths.  This preserves the frozen requirement that the final label
path has exactly one touching commit while still allowing an interrupted
112-pair review to resume safely.
"""
import argparse
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from v2b_a6_blind import BLIND_RUBRIC, require_committed
from v2b_common import (A6_BLIND_SCHEMA, A6_LABELS_SCHEMA, V2BError,
                        artifact_binding, write_new_json)


DRAFT_SCHEMA = "v2b_a6_review_draft_v1"
LABELS = ("duplicate", "not-duplicate")


def _atomic_draft(path, value):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".v2b-review-", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=1, sort_keys=True,
                      ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_answers(presentation, answers, complete=False):
    if not isinstance(answers, dict):
        raise V2BError("review answers must be an object")
    pair_ids = [row["pair_id"] for row in presentation["pairs"]]
    if len(pair_ids) != len(set(pair_ids)):
        raise V2BError("blind presentation repeats a pair id")
    if not set(answers).issubset(pair_ids):
        raise V2BError("review draft contains a foreign pair id")
    normalized = {}
    for pair_id, row in answers.items():
        if not isinstance(row, dict) or set(row) != {"label", "note"} \
                or row.get("label") not in LABELS \
                or not isinstance(row.get("note"), str):
            raise V2BError(f"malformed review answer for {pair_id}")
        normalized[pair_id] = dict(label=row["label"], note=row["note"])
    if complete and set(normalized) != set(pair_ids):
        missing = len(set(pair_ids) - set(normalized))
        raise V2BError(f"blind review is incomplete: {missing} labels missing")
    return normalized


def final_labels(presentation, presentation_sha, labeler, answers):
    answers = validate_answers(presentation, answers, complete=True)
    if not isinstance(labeler, str) or not labeler.strip():
        raise V2BError("labeler must be a non-empty string")
    return dict(
        schema=A6_LABELS_SCHEMA,
        label_state="blind-complete",
        rubric=BLIND_RUBRIC,
        labeler=labeler.strip(),
        presentation_sha256=presentation_sha,
        labels=[dict(pair_id=row["pair_id"], **answers[row["pair_id"]])
                for row in presentation["pairs"]])


def _load_draft(path, presentation_sha, presentation):
    if not os.path.exists(path):
        return {}
    try:
        value = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise V2BError(f"cannot read review draft: {err}") from err
    if not isinstance(value, dict) or value.get("schema") != DRAFT_SCHEMA \
            or value.get("presentation_sha256") != presentation_sha:
        raise V2BError("review draft belongs to a different presentation")
    return validate_answers(presentation, value.get("answers"))


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V2-b blind duplicate review</title>
<style>
:root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; padding: 18px; background: Canvas; color: CanvasText; }
header, .controls, .status { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
header { justify-content: space-between; margin-bottom: 12px; }
.rubric { max-width: 1000px; margin: 8px 0 14px; color: GrayText; }
.pair { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 12px; }
.side { min-width: 0; }
.side h2 { font-size: 14px; font-weight: 500; margin: 0 0 5px; }
pre { box-sizing: border-box; margin: 0; padding: 12px; width: 100%; min-height: 220px;
  max-height: 62vh; overflow: auto; white-space: pre; tab-size: 2;
  border: 1px solid GrayText; border-radius: 6px; background: Field; color: FieldText; }
button, input { font: inherit; padding: 8px 11px; }
button[aria-pressed="true"] { outline: 3px solid Highlight; background: Highlight; color: HighlightText; }
button:disabled { opacity: .5; }
#note { min-width: min(520px, 80vw); }
.saved { color: GrayText; }
.error { color: #c33; }
@media (max-width: 760px) { .pair { grid-template-columns: 1fr; } pre { max-height: 40vh; } }
</style></head><body>
<header><strong>Blind duplicate review</strong><div class="status">
<span id="position"></span><span id="progress"></span><span id="language"></span>
</div></header>
<div class="rubric" id="rubric"></div>
<main class="pair"><section class="side"><h2>Left</h2><pre id="left"></pre></section>
<section class="side"><h2>Right</h2><pre id="right"></pre></section></main>
<div class="controls" style="margin-top:12px">
<button id="prev" type="button">← Previous</button>
<button id="dup" type="button">D · Duplicate</button>
<button id="not" type="button">N · Not duplicate</button>
<button id="next" type="button">Next →</button>
</div>
<div class="controls" style="margin-top:10px">
<label for="note">Optional note</label><input id="note" type="text">
<span id="saved" class="saved"></span>
</div>
<div class="controls" style="margin-top:14px">
<button id="finalize" type="button" disabled>Finalize write-once labels</button>
<span id="result"></span>
</div>
<script>
let state, index = 0, answers = {}, saveTimer;
const byId = id => document.getElementById(id);
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
  const response = await fetch(path, options); const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`); return value;
}
function row() { return state.presentation.pairs[index]; }
function render() {
  const current = row(), answer = answers[current.pair_id];
  byId('position').textContent = `${index + 1} / ${state.presentation.n_pairs}`;
  byId('progress').textContent = `${Object.keys(answers).length} labeled`;
  byId('language').textContent = current.language;
  byId('left').textContent = current.left; byId('right').textContent = current.right;
  byId('note').value = answer?.note || '';
  byId('dup').setAttribute('aria-pressed', answer?.label === 'duplicate');
  byId('not').setAttribute('aria-pressed', answer?.label === 'not-duplicate');
  byId('prev').disabled = index === 0; byId('next').disabled = index + 1 === state.presentation.n_pairs;
  byId('finalize').disabled = Object.keys(answers).length !== state.presentation.n_pairs || state.final_exists;
  byId('saved').textContent = '';
}
async function save() {
  clearTimeout(saveTimer); saveTimer = undefined;
  try { const value = await api('/save', {answers}); byId('saved').textContent = `saved ${value.n_labeled}`; }
  catch (error) { byId('saved').textContent = error.message; byId('saved').className = 'error'; }
}
function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(save, 180); }
function choose(label) {
  const current = row(); answers[current.pair_id] = {label, note:byId('note').value}; scheduleSave(); render();
  if (index + 1 < state.presentation.n_pairs) { index += 1; render(); }
}
byId('prev').onclick = () => { if (index > 0) { index -= 1; render(); } };
byId('next').onclick = () => { if (index + 1 < state.presentation.n_pairs) { index += 1; render(); } };
byId('dup').onclick = () => choose('duplicate'); byId('not').onclick = () => choose('not-duplicate');
byId('note').oninput = () => { const current=row(); if (answers[current.pair_id]) { answers[current.pair_id].note=byId('note').value; scheduleSave(); } };
byId('finalize').onclick = async () => { try { await save(); const value=await api('/finalize',{answers}); state.final_exists=true; byId('result').textContent=`Final labels written: ${value.path} (${value.sha256.slice(0,12)})`; render(); } catch(error) { byId('result').textContent=error.message; byId('result').className='error'; } };
document.addEventListener('keydown', event => { if (event.target === byId('note')) return; if (event.key.toLowerCase()==='d') choose('duplicate'); else if (event.key.toLowerCase()==='n') choose('not-duplicate'); else if (event.key==='ArrowLeft') byId('prev').click(); else if (event.key==='ArrowRight') byId('next').click(); });
(async () => { state=await api('/state'); answers=state.answers; byId('rubric').textContent=state.presentation.rubric; render(); })().catch(error => { byId('result').textContent=error.message; byId('result').className='error'; });
</script></body></html>"""


def serve(presentation_path, labeler, draft_path, out_path, host, port):
    require_committed(presentation_path)
    binding, presentation = artifact_binding(presentation_path,
                                              A6_BLIND_SCHEMA)
    if presentation.get("label_state") != "awaiting-human" \
            or presentation.get("rubric") != BLIND_RUBRIC \
            or presentation.get("n_pairs") != len(presentation.get("pairs", [])):
        raise V2BError("blind presentation is malformed")
    answers = _load_draft(draft_path, binding["sha256"], presentation)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("[v2b-review] " + fmt % args)

        def _json(self, status, value):
            blob = json.dumps(value, ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self):
            if self.path == "/":
                blob = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            elif self.path == "/state":
                self._json(200, dict(presentation=presentation,
                                     answers=answers, labeler=labeler,
                                     final_exists=os.path.exists(out_path)))
            else:
                self._json(404, dict(error="not found"))

        def do_POST(self):
            nonlocal answers
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise V2BError("invalid review request size")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise V2BError("review request must be an object")
                submitted = validate_answers(
                    presentation, request.get("answers"),
                    complete=self.path == "/finalize")
                if self.path == "/save":
                    answers = submitted
                    _atomic_draft(draft_path, dict(
                        schema=DRAFT_SCHEMA,
                        presentation_sha256=binding["sha256"],
                        answers=answers))
                    self._json(200, dict(n_labeled=len(answers)))
                elif self.path == "/finalize":
                    value = final_labels(presentation, binding["sha256"],
                                         labeler, submitted)
                    digest = write_new_json(out_path, value)
                    self._json(200, dict(path=os.path.abspath(out_path),
                                         sha256=digest))
                else:
                    self._json(404, dict(error="not found"))
            except (V2BError, ValueError, TypeError,
                    json.JSONDecodeError) as err:
                self._json(400, dict(error=str(err)))

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[v2b-review] http://{host}:{server.server_port} — "
          f"{len(answers)}/{presentation['n_pairs']} labeled")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation", required=True)
    parser.add_argument("--labeler", required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        serve(args.presentation, args.labeler, args.draft, args.out,
              args.host, args.port)
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err


if __name__ == "__main__":
    main()
