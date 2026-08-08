#!/usr/bin/env python3
"""V2-a extraction validation: byte-exact span round-trips against the
LIVE source files, header/body partition and non-empty-body checks,
closure diagnostics (direct/transitive size, same-file vs cross-file vs
external mass per §14.3), and corpus-level reference-coverage
summaries. NO model outcome is computed here.

HONEST GATE SCOPE (review fix — never over-claim): this is EXTRACTION
validation only. The full DESIGN_V2 §10 gate ALSO requires standalone
compilation of the 20 spans and an independent elaborator closure
check, NEITHER of which runs here — the report carries them as NOT-RUN
and `gate_complete: false` until they actually execute (cluster-side,
toolchain-dependent). Target sampling here uses the §14.19 hash
priorities GLOBALLY, WITHOUT the within-stratum quotas — recorded as
missing, not claimed as §14.19 compliance; pilot sampling must
implement strata.

Fail-closed: any round-trip mismatch, span error, repo-tag mismatch,
or an eligible pool too small to fill the requested selection is a
counted failure and the exit code is nonzero; the JSON report is
written either way (quarantine-on-rerun, evidence rule).
"""
import argparse, hashlib, json, os, sys, tempfile, time

from extract_lean import (ELIGIBLE_KINDS, K4_CLOSURE_DEFINITION,
                          classify_decl_kind, split_header_body,
                          target_priority, transitive_closure)
from extract_python import extract_file as reextract_python_file

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "results_v2", "v2a")
N_TARGETS = 20
LEAN_SCHEMA = "v2a_lean_extract_v2"
PYTHON_SCHEMA = "v2a_python_extract_v2"


def _python_priority(repo, fqname):
    """Frozen §14.19 Python identity (module is already in fqname)."""
    return hashlib.sha256(
        f"v2a:20260808:{repo}:{fqname}".encode()).hexdigest()


def _python_transitive_closure(edges, root):
    adj = {}
    for src, dst in edges:
        adj.setdefault(src, set()).add(dst)
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    seen.discard(root)
    return seen


def _lean_eligible(ex):
    """Eligible = frozen §2 target kind (theorem/lemma/def), found
    split, non-empty body, AND contained selection range (review
    finding: generated macro-rule decls share an enclosing selection —
    valid files, ineligible targets). Excluded/unknown kinds and
    uncontained selections are counted by lean_exclusions()."""
    for f in ex["files"]:
        for name, d in f["decls"].items():
            if (d.get("eligible_kind") and d.get("selection_contained")
                    and d["split_kind"] and d["body_bytes"] > 0):
                yield name, f, d


def lean_exclusions(ex):
    """Corpus-level accounting of every ineligibility reason plus the
    full command-kind histogram — the record that shows the §2 filter
    is load-bearing rather than silently thinning the corpus."""
    kinds = {}
    out = dict(kind_excluded=0, unknown_kind=0,
               selection_uncontained=0, no_split=0, empty_body=0)
    for f in ex["files"]:
        for d in f["decls"].values():
            kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1
            if not d.get("eligible_kind"):
                out["kind_excluded"] += 1
                if d.get("kind") == "unknown":
                    out["unknown_kind"] += 1
            elif not d.get("selection_contained"):
                out["selection_uncontained"] += 1
            elif not d["split_kind"]:
                out["no_split"] += 1
            elif d["body_bytes"] <= 0:
                out["empty_body"] += 1
    out["kind_histogram"] = dict(sorted(kinds.items()))
    return out


def _py_eligible(ex):
    for f in ex["files"]:
        for name, t in f["targets"].items():
            yield f"{f['module']}.{name}", f, t


def validate(ex, repo, n_targets):
    schema = ex.get("schema")
    if schema not in (LEAN_SCHEMA, PYTHON_SCHEMA):
        raise ValueError(f"unknown extraction schema: {schema!r}")
    lean = schema == LEAN_SCHEMA
    failures = []
    if not isinstance(n_targets, int) or isinstance(n_targets, bool) \
            or n_targets <= 0:
        failures.append(f"invalid-n-targets:{n_targets!r}")
    # repo-tag identity (review fix: a mismatched extraction must never
    # masquerade as this corpus's validation)
    ex_repo = ex.get("repo") or ""
    if repo not in (ex_repo, os.path.basename(ex_repo.rstrip("/"))):
        failures.append(f"repo-tag-mismatch:{ex_repo!r}!={repo!r}")
    if lean and ex.get("k4_closure_definition") != K4_CLOSURE_DEFINITION:
        failures.append(
            "k4-closure-definition-mismatch:"
            f"{ex.get('k4_closure_definition')!r}!="
            f"{K4_CLOSURE_DEFINITION!r}")
    elig = list(_lean_eligible(ex) if lean else _py_eligible(ex))
    ranked = sorted(
        elig,
        key=(lambda x: target_priority(repo, x[1]["module"], x[0]))
        if lean else (lambda x: _python_priority(repo, x[0])))
    chosen = ranked[:n_targets] if isinstance(n_targets, int) \
        and not isinstance(n_targets, bool) and n_targets > 0 else []
    # under-filled selection is a HARD failure (review fix: fewer than
    # the requested targets previously exited 0)
    if isinstance(n_targets, int) and not isinstance(n_targets, bool) \
            and n_targets > 0 and len(chosen) < n_targets:
        failures.append(
            f"insufficient-eligible:{len(chosen)}<{n_targets}")
    g = ex["graph"]
    edges = g["edges"]
    if not lean and ex.get("n_failed", 0):
        failures.append(f"failed-source-files:{ex['n_failed']}")
    n_import_errors = (None if lean else sum(
        len(f.get("import_errors", [])) for f in ex.get("files", [])))
    py_live_cache = {}
    targets = []
    for fq, f, d in chosen:
        module = f.get("module")
        rec = dict(name=fq, module=module,
                   identity=([module, fq] if lean else fq),
                   diagnostics=[])
        try:
            src = f.get("source") or f.get("rel")
            by = open(src, "rb").read() if os.path.isabs(src) or \
                os.path.exists(src) else None
            if by is None:
                raise FileNotFoundError(src)
            if hashlib.sha256(by).hexdigest() != f["source_sha256"]:
                raise RuntimeError("source changed since extraction")
            s, e = d["start_byte"], d["end_byte"]
            if not (isinstance(s, int) and isinstance(e, int)
                    and 0 <= s < e <= len(by)):
                raise RuntimeError(
                    f"span outside source: {s},{e} of {len(by)}")
            span = by[s:e]
            rec.update(source=src, source_sha256=f["source_sha256"],
                       start_byte=s, end_byte=e, span_bytes=e - s)
            span_text = span.decode("utf-8")   # must decode cleanly
            if lean:
                hb, bb = d["header_bytes"], d["body_bytes"]
                if hb + bb != len(span):
                    raise RuntimeError(
                        f"partition {hb}+{bb} != span {len(span)}")
                rec.update(header_bytes=hb, body_bytes=bb,
                           split_kind=d["split_kind"],
                           shell_commands=d["shell"],
                           n_shell=len(d["shell"]))
                # §14.9 spirit: header + body re-concatenation IS the
                # span (partition), asserted at extraction; re-checked
                # here against live bytes
                if span_text.encode("utf-8") != span:
                    raise RuntimeError("span re-encode mismatch")
                live_header, live_body, live_split = split_header_body(
                    span_text)
                live_kind, live_token = classify_decl_kind(span_text)
                if (len(live_header.encode("utf-8")) != hb
                        or len(live_body.encode("utf-8")) != bb
                        or live_split != d["split_kind"]
                        or live_kind != d["kind"]
                        or live_token != d["kind_token"]):
                    raise RuntimeError(
                        "live Lean kind/header/body recomputation differs "
                        "from extraction")
                ss, se = d["sel_start_byte"], d["sel_end_byte"]
                live_contained = s <= ss <= se <= e
                if live_contained != d["selection_contained"]:
                    raise RuntimeError(
                        "live Lean selection containment differs")
                if (live_kind not in ELIGIBLE_KINDS or not live_contained
                        or live_split is None or not live_body):
                    raise RuntimeError(
                        "selected Lean target is not live-eligible")
            else:
                hb, bb = d["header_bytes"], d["body_bytes"]
                body_start = d["body_start_byte"]
                if hb <= 0 or bb <= 0 or hb + bb != len(span):
                    raise RuntimeError(
                        f"partition {hb}+{bb} != span {len(span)}")
                if body_start != s + hb:
                    raise RuntimeError(
                        f"body_start {body_start} != {s}+{hb}")
                if span_text.encode("utf-8") != span:
                    raise RuntimeError("span re-encode mismatch")
                if src not in py_live_cache:
                    py_live_cache[src] = reextract_python_file(src, f["rel"])
                local_name = fq[len(module) + 1:] \
                    if fq.startswith(module + ".") else None
                live_target = py_live_cache[src]["targets"].get(local_name)
                if live_target is None:
                    raise RuntimeError(
                        "selected Python target absent on live reparse")
                for field in ("start_byte", "end_byte", "body_start_byte",
                              "header_bytes", "body_bytes", "docstring_bytes",
                              "kind"):
                    if live_target.get(field) != d.get(field):
                        raise RuntimeError(
                            f"live Python {field} differs from extraction")
                rec.update(header_bytes=hb, body_bytes=bb,
                           body_start_byte=body_start,
                           docstring_bytes=d.get("docstring_bytes", 0))
            if lean:
                root = (module, fq)
                clo = transitive_closure(edges, root)
                direct = {(dm, dd) for sm, sd, dm, dd in edges
                          if (sm, sd) == root}
                same = sum(1 for dm, _ in clo if dm == module)
            else:
                clo = _python_transitive_closure(edges, fq)
                direct = {b for a, b in edges if a == fq}
                mod_of = {}
                for ff in ex["files"]:
                    for nn in ff.get("targets", {}):
                        mod_of[f"{ff['module']}.{nn}"] = ff["module"]
                same = sum(1 for c in clo if mod_of.get(c) == module)
            rec.update(n_direct=len(direct), n_transitive=len(clo),
                       n_same_file_in_closure=same,
                       n_cross_file_in_closure=len(clo) - same,
                       roundtrip_ok=True)
            if lean:
                rec["external_ref_occurrences"] = \
                    g.get("external_ref_counts_by_target", {}).get(
                        module, {}).get(fq, 0)
                rec["internal_renderability"] = \
                    g.get("internal_renderability_by_target", {}).get(
                        module, {}).get(fq, dict(
                            n_internal_occurrences=0,
                            n_renderable_occurrences=0,
                            n_unrenderable_occurrences=0,
                            coverage=None))
            else:
                rec["reference_coverage"] = \
                    g.get("target_coverage", {}).get(fq)
        except Exception as err:
            rec.update(roundtrip_ok=False, error=repr(err))
            failures.append(fq)
        targets.append(rec)
    summary = dict(
        repo=repo, schema=ex["schema"],
        k4_closure_definition=(ex.get("k4_closure_definition")
                               if lean else None),
        eligible_kinds=(list(ELIGIBLE_KINDS) if lean else None),
        exclusions=(lean_exclusions(ex) if lean else None),
        n_internal_unrenderable=(g.get("n_internal_unrenderable")
                                 if lean else None),
        n_folded_generated=(g.get("n_folded_generated")
                            if lean else None),
        definition_site_diagnostics=(
            _definition_site_totals(ex) if lean else None),
        n_external_reference_edges=(
            len(g.get("external_reference_edges", [])) if lean else None),
        n_eligible=len(elig), n_selected=len(chosen),
        n_edges=len(edges),
        n_same_file=g.get("n_same_file"),
        n_cross_file=g.get("n_cross_file"),
        external_by_root=g.get("external_by_root"),
        parent_decl_coverage=(g.get("parent_decl_coverage")
                              if lean else None),
        internal_renderability_coverage=(
            _lean_renderability(g) if lean else None),
        target_coverage_mean=(None if lean else _mean_cov(g)),
        n_failed_source_files=(None if lean else ex.get("n_failed")),
        n_import_errors=n_import_errors,
        n_failures=len(failures), failures=failures,
        # HONEST §10 accounting (review fix): extraction validation is
        # a PARTIAL gate; these stay NOT-RUN until they truly execute
        design_v2_s10=dict(extraction_validation="RUN",
                           standalone_compile="NOT-RUN",
                           elaborator_closure_check="NOT-RUN"),
        gate_complete=False,
        sampling=("global seeded §14.19 hash priority WITHOUT "
                  "within-stratum quotas — validation-only sampling; "
                  "pilot must implement strata"))
    return dict(summary=summary, targets=targets)


def _mean_cov(g):
    vals = [t["coverage"] for t in g.get("target_coverage", {}).values()
            if t.get("coverage") is not None]
    return (sum(vals) / len(vals)) if vals else None


def _lean_renderability(g):
    counts = [c for targets in
              g.get("internal_renderability_by_target", {}).values()
              for c in targets.values()]
    total = sum(c["n_internal_occurrences"] for c in counts)
    rendered = sum(c["n_renderable_occurrences"] for c in counts)
    return dict(n_internal_occurrences=total,
                n_renderable_occurrences=rendered,
                n_unrenderable_occurrences=total - rendered,
                coverage=(rendered / total) if total else None)


def _definition_site_totals(ex):
    totals = {}
    for file_rec in ex.get("files", []):
        for key, value in file_rec.get(
                "definition_site_diagnostics", {}).items():
            totals[key] = totals.get(key, 0) + value
    totals["position_name_mismatch_records"] = sum(
        len(file_rec.get("definition_position_name_mismatches", []))
        for file_rec in ex.get("files", []))
    return dict(sorted(totals.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=N_TARGETS)
    ap.add_argument("--out",
                    help="report path (default: results_v2/v2a/validation_REPO.json)")
    args = ap.parse_args()
    ex = json.load(open(args.extraction))
    rep = validate(ex, args.repo, args.n)
    rep["extraction_file"] = args.extraction
    rep["extraction_sha256"] = hashlib.sha256(
        open(args.extraction, "rb").read()).hexdigest()
    out = (os.path.abspath(args.out) if args.out else
           os.path.join(OUT_DIR, f"validation_{args.repo}.json"))
    out_dir = os.path.dirname(out) or "."
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(out):        # evidence is never overwritten
        ts = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        os.rename(out, f"{out}.quarantine-{ts}")
        print(f"[validate] prior report -> quarantine-{ts}")
    fd, tmp = tempfile.mkstemp(prefix=".validation-", suffix=".json",
                               dir=out_dir)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    s = rep["summary"]
    print(f"[validate] {args.repo}: {s['n_selected']}/{s['n_eligible']} "
          f"targets, {s['n_failures']} failures -> {out}")
    print("[validate] EXTRACTION-VALIDATION ONLY — DESIGN_V2 §10 gate "
          "NOT complete (standalone compile + elaborator closure check "
          "NOT-RUN; stratified sampling NOT implemented)")
    sys.exit(1 if s["n_failures"] else 0)


if __name__ == "__main__":
    main()
