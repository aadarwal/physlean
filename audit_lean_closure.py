#!/usr/bin/env python3
"""Independent raw-.ilean closure audit for the V2-a selected targets.

This module deliberately does NOT import ``extract_lean``. It parses the
compact v5 JSON a second time, reconstructs module-qualified direct source
reference edges (including generated-declaration folding), and compares the
exact resolved / external / internal-unrenderable partition against a
``v2a_lean_extract_v3`` artifact. Agreement is therefore evidence against a
self-consistent bug shared by extraction and validation.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile


PAIR_SCHEMA = "v2a_ilean_pairs_v2"
EXTRACT_SCHEMA = "v2a_lean_extract_v3"
AUDIT_SCHEMA = "v2a_raw_ilean_closure_audit_v1"
ILEAN_VERSION = 5


class AuditError(RuntimeError):
    """An input or raw compact-.ilean invariant failed closed."""


class _SourceIndex:
    """Independent UTF-16 LSP-position to UTF-8 byte converter."""

    def __init__(self, path):
        data = open(path, "rb").read()
        if b"\r" in data:
            raise AuditError(f"{path}: CR in Lean source")
        try:
            text = data.decode("utf-8")
        except UnicodeError as err:
            raise AuditError(f"{path}: source is not UTF-8") from err
        self.lines = text.split("\n")
        self.starts = []
        offset = 0
        for line in self.lines:
            self.starts.append(offset)
            offset += len(line.encode("utf-8")) + 1

    def pos(self, line, char16):
        if not (isinstance(line, int) and 0 <= line < len(self.lines)
                and isinstance(char16, int) and char16 >= 0):
            raise AuditError("invalid LSP position")
        units = byte_count = 0
        for char in self.lines[line]:
            if units == char16:
                break
            width = 2 if ord(char) > 0xFFFF else 1
            if units + width > char16:
                raise AuditError("LSP position splits surrogate pair")
            units += width
            byte_count += len(char.encode("utf-8"))
        else:
            if units != char16:
                raise AuditError("LSP position beyond line")
        return self.starts[line] + byte_count

    def span(self, coords):
        if not (isinstance(coords, (list, tuple)) and len(coords) == 4):
            raise AuditError("range must have four coordinates")
        start = self.pos(coords[0], coords[1])
        end = self.pos(coords[2], coords[3])
        if start > end:
            raise AuditError("inverted source range")
        return start, end


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise AuditError(f"cannot read JSON {path}: {err}") from err


def _const_identity(raw_key, where):
    try:
        ident = json.loads(raw_key)
    except (TypeError, json.JSONDecodeError) as err:
        raise AuditError(f"{where}: malformed reference key") from err
    if not isinstance(ident, dict) or len(ident) != 1:
        raise AuditError(f"{where}: reference identity is not a singleton")
    if "f" in ident:
        fvar = ident["f"]
        if not (isinstance(fvar, dict)
                and isinstance(fvar.get("m"), str)
                and isinstance(fvar.get("i"), str)):
            raise AuditError(f"{where}: malformed fvar identity")
        return None
    const = ident.get("c")
    if not (isinstance(const, dict)
            and set(const) == {"m", "n"}
            and isinstance(const["m"], str) and const["m"]
            and isinstance(const["n"], str) and const["n"]):
        raise AuditError(f"{where}: malformed const identity")
    return const["m"], const["n"]


def _location(value, where, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, list) or len(value) not in (4, 5):
        raise AuditError(f"{where}: location must have length 4 or 5")
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 0
           for x in value[:4]):
        raise AuditError(f"{where}: invalid location coordinates")
    if len(value) == 5 and (not isinstance(value[4], str) or not value[4]):
        raise AuditError(f"{where}: invalid parent declaration")
    return value[4] if len(value) == 5 else None


def _read_raw(path, expected_module):
    raw = _load_json(path)
    if not isinstance(raw, dict) or raw.get("version") != ILEAN_VERSION:
        raise AuditError(f"{path}: expected compact .ilean v5")
    for key in ("module", "decls", "references"):
        if key not in raw:
            raise AuditError(f"{path}: missing {key}")
    if raw["module"] != expected_module:
        raise AuditError(
            f"{path}: embedded module {raw['module']!r} != "
            f"{expected_module!r}")
    if not isinstance(raw["decls"], dict):
        raise AuditError(f"{path}: decls is not an object")
    if not all(isinstance(name, str) and name for name in raw["decls"]):
        raise AuditError(f"{path}: invalid declaration name")
    if not isinstance(raw["references"], dict):
        raise AuditError(f"{path}: references is not an object")
    return raw


def _load_pairs(path):
    raw = _load_json(path)
    if not isinstance(raw, dict) or raw.get("schema") != PAIR_SCHEMA:
        raise AuditError(f"{path}: wrong pairs schema")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise AuditError(f"{path}: empty pairs list")
    out = {}
    for i, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise AuditError(f"pair[{i}] is not an object")
        required = ("module", "match_kind", "source", "ilean", "source_sha256",
                    "ilean_sha256")
        if any(k not in pair for k in required):
            raise AuditError(f"pair[{i}] missing identity fields")
        module = pair["module"]
        if not isinstance(module, str) or not module or module in out:
            raise AuditError(f"pair[{i}] duplicate/invalid module")
        if pair["match_kind"] not in ("exact", "srcdir_suffix"):
            raise AuditError(f"pair[{i}] invalid match kind")
        for file_key, hash_key in (("source", "source_sha256"),
                                   ("ilean", "ilean_sha256")):
            got = _sha256(pair[file_key])
            if got != pair[hash_key]:
                raise AuditError(
                    f"{module}: {file_key} changed since pairing")
        out[module] = pair
    return out


def _definition_state(pairs):
    """First independent pass: local declarations, foreign DeclInfo, and
    generated-parent maps. Foreign classification is reimplemented here from
    raw reference identities; no extractor code or output is trusted."""
    decls = {}
    generated = {}
    foreign_declinfos = {}
    for module, pair in sorted(pairs.items()):
        raw = _read_raw(pair["ilean"], module)
        index = _SourceIndex(pair["source"])
        reference_rows = []
        reference_modules_by_name = {}
        for raw_key, info in raw["references"].items():
            ident = _const_identity(raw_key, f"{module}:reference")
            if not isinstance(info, dict) or set(info) != {
                    "definition", "usages"}:
                raise AuditError(f"{module}: malformed RefInfo")
            if not isinstance(info["usages"], list):
                raise AuditError(f"{module}: usages is not a list")
            reference_rows.append((ident, info))
            if ident is not None:
                defining_module, name = ident
                reference_modules_by_name.setdefault(name, set()).add(
                    defining_module)
        spans = {}
        module_foreign = {}
        for name, arr in raw["decls"].items():
            if not (isinstance(arr, list) and len(arr) == 8
                    and all(isinstance(x, int) and not isinstance(x, bool)
                            and x >= 0 for x in arr)):
                raise AuditError(f"{module}: malformed decl span for {name}")
            defining_modules = sorted(
                reference_modules_by_name.get(name, ()))
            if len(defining_modules) > 1:
                raise AuditError(
                    f"{module}:{name}: multiple defining modules")
            if defining_modules and defining_modules[0] != module:
                n_local_usages = 0
                for ident, info in reference_rows:
                    if ident != (defining_modules[0], name):
                        continue
                    for j, loc in enumerate(info["usages"]):
                        _location(loc, f"{module}:foreign-usage[{j}]")
                        index.span(loc[:4])
                        n_local_usages += 1
                if n_local_usages == 0:
                    raise AuditError(
                        f"{module}:{name}: foreign DeclInfo has no local "
                        "usage occurrence")
                module_foreign[name] = defining_modules
                continue
            spans[name] = index.span(arr[:4])
        decls[module] = set(spans)
        if module_foreign:
            foreign_declinfos[module] = module_foreign
        parents = {}
        parentless_sites = {}
        for ident, info in reference_rows:
            parent = _location(info["definition"],
                               f"{module}:definition", nullable=True)
            if ident is not None and parent is not None:
                defining_module, name = ident
                if defining_module != module:
                    raise AuditError(
                        f"{module}: foreign definition for {defining_module}")
                old = parents.get(name)
                if old is not None and old != parent:
                    raise AuditError(
                        f"{module}: conflicting parent for {name}")
                parents[name] = parent
            elif ident is not None and info["definition"] is not None:
                defining_module, name = ident
                if defining_module != module:
                    raise AuditError(
                        f"{module}: foreign definition for {defining_module}")
                coords = tuple(info["definition"][:4])
                old = parentless_sites.get(name)
                if old is not None and old != coords:
                    raise AuditError(
                        f"{module}: conflicting definition sites for {name}")
                parentless_sites[name] = coords
        for name, coords in parentless_sites.items():
            if name in decls[module] or name in parents:
                continue
            ds, de = index.span(coords)
            candidates = sorted(
                (end - start, decl) for decl, (start, end) in spans.items()
                if start <= ds <= de <= end)
            if not candidates:
                continue
            smallest = candidates[0][0]
            winners = [decl for size, decl in candidates
                       if size == smallest]
            if len(winners) == 1:
                parents[name] = winners[0]
        generated[module] = parents
    return decls, generated, foreign_declinfos


def _fold(module, name, decls, generated):
    seen = set()
    cur = name
    for _ in range(8):
        if cur in decls.get(module, set()):
            return cur
        if cur in seen or cur not in generated.get(module, {}):
            return None
        seen.add(cur)
        cur = generated[module][cur]
    return None


def _derive_selected(pairs, selected, decls, generated):
    edges = set()
    external = set()
    unrenderable = set()
    counts = {ident: dict(n_internal_occurrences=0,
                          n_renderable_occurrences=0,
                          n_unrenderable_occurrences=0,
                          n_external_occurrences=0)
              for ident in selected}
    selected_by_module = {}
    for module, name in selected:
        if module not in pairs or name not in decls.get(module, set()):
            raise AuditError(f"selected target absent from raw data: "
                             f"{module}:{name}")
        selected_by_module.setdefault(module, set()).add(name)

    corpus_modules = set(pairs)
    for module, names in sorted(selected_by_module.items()):
        raw = _read_raw(pairs[module]["ilean"], module)
        for raw_key, info in raw["references"].items():
            ident = _const_identity(raw_key, f"{module}:reference")
            if ident is None:
                continue
            if not isinstance(info, dict) or not isinstance(
                    info.get("usages"), list):
                raise AuditError(f"{module}: malformed RefInfo usages")
            defining_module, const_name = ident
            for j, loc in enumerate(info["usages"]):
                parent = _location(loc, f"{module}:usage[{j}]")
                if parent not in names:
                    continue
                if defining_module == module and const_name == parent:
                    continue
                if defining_module not in corpus_modules:
                    counts[(module, parent)]["n_external_occurrences"] += 1
                    external.add((module, parent, defining_module,
                                  const_name))
                    continue
                counts[(module, parent)]["n_internal_occurrences"] += 1
                rendered = (const_name if const_name in
                            decls[defining_module] else
                            _fold(defining_module, const_name,
                                  decls, generated))
                if rendered is None:
                    counts[(module, parent)][
                        "n_unrenderable_occurrences"] += 1
                    unrenderable.add((module, parent, defining_module,
                                      const_name))
                elif not (defining_module == module and rendered == parent):
                    counts[(module, parent)]["n_renderable_occurrences"] += 1
                    edges.add((module, parent, defining_module, rendered))
                else:
                    counts[(module, parent)]["n_renderable_occurrences"] += 1
    for value in counts.values():
        total = value["n_internal_occurrences"]
        value["coverage"] = (value["n_renderable_occurrences"] /
                             total) if total else None
    return edges, external, unrenderable, counts


def audit(extraction, validation, pairs_path):
    if extraction.get("schema") != EXTRACT_SCHEMA:
        raise AuditError("wrong Lean extraction schema")
    if extraction.get("pairs_manifest_sha256") != _sha256(pairs_path):
        raise AuditError("pairs manifest hash differs from extraction")
    if validation.get("summary", {}).get("schema") != EXTRACT_SCHEMA:
        raise AuditError("validation/extraction schema mismatch")
    if validation.get("summary", {}).get("repo") != extraction.get("repo"):
        raise AuditError("validation/extraction repo mismatch")
    pairs = _load_pairs(pairs_path)
    selected = []
    for i, target in enumerate(validation.get("targets", [])):
        ident = target.get("identity")
        if not (isinstance(ident, list) and len(ident) == 2
                and all(isinstance(x, str) and x for x in ident)):
            raise AuditError(
                f"validation target[{i}] lacks module-qualified identity")
        selected.append(tuple(ident))
    if not selected or len(set(selected)) != len(selected):
        raise AuditError("selected identities are empty or duplicated")

    decls, generated, raw_foreign = _definition_state(pairs)
    expected_foreign = {
        module: {
            row["name"]: row["defining_modules"] for row in rows}
        for module, rows in extraction.get(
            "foreign_declaration_infos_by_module", {}).items()}
    foreign_partition_match = raw_foreign == expected_foreign
    raw_edges, raw_external, raw_unrenderable, raw_counts = _derive_selected(
        pairs, selected, decls, generated)
    graph = extraction.get("graph", {})
    expected_edges = {tuple(e) for e in graph.get("edges", [])
                      if tuple(e[:2]) in set(selected)}
    expected_external = {
        tuple(e) for e in graph.get("external_reference_edges", [])
        if tuple(e[:2]) in set(selected)}
    expected_unrenderable = {
        tuple(e) for e in graph.get("internal_unrenderable_references", [])
        if tuple(e[:2]) in set(selected)}

    target_rows = []
    failures = []
    global_failures = []
    if not foreign_partition_match:
        global_failures.append("foreign-declaration-info-partition")
    for ident in selected:
        def subset(values):
            return sorted(list(v) for v in values if v[:2] == ident)
        raw_parts = (subset(raw_edges), subset(raw_external),
                     subset(raw_unrenderable))
        expected_parts = (subset(expected_edges), subset(expected_external),
                          subset(expected_unrenderable))
        expected_counts = dict(
            extraction.get("graph", {}).get(
                "internal_renderability_by_target", {}).get(
                    ident[0], {}).get(ident[1], dict(
                        n_internal_occurrences=0,
                        n_renderable_occurrences=0,
                        n_unrenderable_occurrences=0,
                        coverage=None)))
        expected_counts["n_external_occurrences"] = \
            extraction.get("graph", {}).get(
                "external_ref_counts_by_target", {}).get(
                    ident[0], {}).get(ident[1], 0)
        match = (raw_parts == expected_parts
                 and raw_counts[ident] == expected_counts)
        if not match:
            failures.append(list(ident))
        target_rows.append(dict(
            identity=list(ident), match=match,
            raw=dict(edges=raw_parts[0], external=raw_parts[1],
                     internal_unrenderable=raw_parts[2],
                     occurrence_counts=raw_counts[ident]),
            extraction=dict(edges=expected_parts[0],
                            external=expected_parts[1],
                            internal_unrenderable=expected_parts[2],
                            occurrence_counts=expected_counts)))
    return dict(
        schema=AUDIT_SCHEMA,
        extraction_schema=EXTRACT_SCHEMA,
        extraction_pairs_manifest_sha256=_sha256(pairs_path),
        summary=dict(n_selected=len(selected),
                     n_passed=len(selected) - len(failures),
                     n_failed=len(failures) + len(global_failures),
                     failures=failures,
                     global_failures=global_failures,
                     foreign_declaration_info_partition_match=(
                         foreign_partition_match),
                     n_foreign_declaration_infos=sum(
                         len(rows) for rows in raw_foreign.values()),
                     elaborator_closure_check=(
                         "PASS" if not failures and not global_failures
                         else "FAIL")),
        targets=target_rows)


def _write_new(path, value):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise AuditError(f"refusing to overwrite audit: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".closure-audit-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise AuditError(f"refusing to overwrite audit: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--validation", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    extraction = _load_json(args.extraction)
    validation = _load_json(args.validation)
    extraction_sha = _sha256(args.extraction)
    if validation.get("extraction_sha256") != extraction_sha:
        raise AuditError("validation is not bound to this extraction hash")
    report = audit(extraction, validation, args.pairs)
    report["inputs"] = dict(
        extraction=args.extraction,
        extraction_sha256=extraction_sha,
        validation=args.validation,
        validation_sha256=_sha256(args.validation),
        pairs=args.pairs,
        pairs_sha256=_sha256(args.pairs))
    _write_new(args.out, report)
    s = report["summary"]
    print(f"[closure-audit] {s['n_passed']}/{s['n_selected']} exact "
          f"target partitions -> {args.out}")
    sys.exit(1 if s["n_failed"] else 0)


if __name__ == "__main__":
    main()
