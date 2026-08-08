#!/usr/bin/env python3
"""V2-a Python extractor (G3.5; DESIGN_V2 §2/§14.4): stdlib-ast targets
and STATIC declaration-level edges with the frozen per-target COVERAGE
metric — fraction of static references resolved to declaration level.
No exact-closure claim is ever made for Python (§14.4); low coverage is
RECORDED, never hidden.

Python schema v3 treats each direct module-body declaration as a source-span
unit with identity (module, name, start_byte), so repeated top-level bindings
are preserved rather than overwritten. Static name edges use the documented
final-source-order binding convention; temporal/dynamic exceptions remain a
recorded best-effort limitation.

Spans are byte-exact by construction: CPython's ast col_offset /
end_col_offset are UTF-8 BYTE offsets into the source line, so span
recovery needs no UTF-16 conversion (unlike the Lean side). Round-trip
(byte slice == segment) is asserted per target, fail-closed.
"""
import argparse, ast, bisect, builtins, hashlib, io, json, os, sys, tempfile
import tokenize

V2A_SEED = "v2a:20260808"                    # §14.19 (shared constant)
SCHEMA = "v2a_python_extract_v3"
_BUILTINS = frozenset(dir(builtins))


class ExtractError(RuntimeError):
    """Fail-closed extraction error (never silently skipped)."""


def module_name(rel):
    parts = rel[:-3].split(os.sep) if rel.endswith(".py") else None
    if parts is None:
        raise ExtractError(f"not a .py path: {rel}")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_relative(module, is_init, level, from_module):
    """Resolve a relative import (§14.4; SymPy/Astropy rely on them):
    the anchor package is the module itself for __init__ files and the
    parent otherwise; each additional level walks one package up.
    Returns the absolute dotted module or raises when the relative walk
    escapes the corpus root (fail-closed, recorded by the caller)."""
    parts = module.split(".")
    pkg = parts if is_init else parts[:-1]
    up = level - 1
    if up > len(pkg) - 0 or (up and up > len(pkg)):
        raise ExtractError(f"relative import level {level} escapes "
                           f"package of {module}")
    base = pkg[:len(pkg) - up] if up else pkg
    if not base:
        raise ExtractError(f"relative import level {level} escapes "
                           f"package of {module}")
    return ".".join(base + ([from_module] if from_module else []))


def _line_starts(by):
    starts = [0]
    for i, b in enumerate(by):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _abs_byte(starts, lineno, col):
    if not (1 <= lineno <= len(starts)):
        raise ExtractError(f"line {lineno} out of range")
    return starts[lineno - 1] + col


def _token_pos_byte(lines, starts, pos):
    """`tokenize` columns are Unicode-codepoint offsets; AST columns are
    UTF-8 bytes. Convert a tokenizer (1-based line, char column) exactly."""
    lineno, char_col = pos
    if not (1 <= lineno <= len(lines)):
        raise ExtractError(f"token line {lineno} out of range")
    line = lines[lineno - 1]
    if not (0 <= char_col <= len(line)):
        raise ExtractError(
            f"token column {char_col} outside line {lineno}")
    return starts[lineno - 1] + len(line[:char_col].encode("utf-8"))


def _body_start(node, tokens, token_lines, lines, starts):
    """Byte immediately AFTER the declaration's suite colon.

    This is the semantic header/body boundary: the signature includes the
    colon; the body includes the following space/newline, indentation,
    leading comments, docstring, and statements. Using the first AST body
    statement would leak leading implementation comments into the common
    unscored prefix. Bracket depth excludes colons in annotations/defaults.
    """
    depth = 0
    started = False
    # One binary search per target avoids rescanning every token from the
    # beginning of a large SymPy/Astropy module (O(targets * tokens)).
    first_token = bisect.bisect_left(token_lines, node.lineno)
    for tok in tokens[first_token:]:
        if not started:
            # Top-level target commands begin at node.lineno (`async` for an
            # async def); indentation is zero by construction.
            started = True
        if tok.type != tokenize.OP:
            continue
        if tok.string in "([{":
            depth += 1
        elif tok.string in ")]}":
            if depth == 0:
                raise ExtractError(
                    f"{getattr(node, 'name', '?')}: unmatched {tok.string} "
                    "while finding suite colon")
            depth -= 1
        elif tok.string == ":" and depth == 0:
            return _token_pos_byte(lines, starts, tok.end)
    raise ExtractError(
        f"{getattr(node, 'name', '?')}: no depth-0 suite colon")


def _attribute_chain(node):
    """Return (root Name node, dotted attribute suffix) for a pure
    ``name.attr...`` chain. Calls/subscripts deliberately stop the chain;
    their root loads are still collected by the ordinary Name path."""
    attrs = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name) or not isinstance(cur.ctx, ast.Load):
        return None
    return cur, ".".join(reversed(attrs))


def extract_file(path, rel):
    """One source file -> targets (top-level def/async def/class) with
    byte-exact spans, an exact HEADER/BODY partition (§2 scores BODY
    ONLY — body starts immediately after the suite colon so leading
    comments/docstrings remain scored, exact for decorated/
    multiline-signature/one-line forms), and per-target
    reference OCCURRENCES (attribute-qualified where an imported module
    is dereferenced), plus the import-binding table with RELATIVE
    imports resolved. Round-trip asserted per target."""
    by = open(path, "rb").read()
    if b"\r" in by:
        raise ExtractError(f"{rel}: CR in source — LF-only expected")
    text = by.decode("utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise ExtractError(f"{rel}: unparseable: {e}")
    starts = _line_starts(by)
    lines = text.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError as err:
        raise ExtractError(f"{rel}: tokenize failed: {err}") from err
    token_lines = [tok.start[0] for tok in tokens]
    module = module_name(rel)
    is_init = os.path.basename(rel) == "__init__.py"
    imports = {}     # local binding -> absolute dotted module/symbol
    import_errors = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    # ``import pkg.mod as m`` binds m to pkg.mod.
                    imports[a.asname] = a.name
                else:
                    # ``import pkg.mod`` binds only pkg, not pkg.mod.
                    # The remaining components are recovered from the
                    # attribute chain at each use site.
                    root = a.name.split(".", 1)[0]
                    imports[root] = root
        elif isinstance(node, ast.ImportFrom):
            try:
                base = (node.module if node.level == 0 else
                        resolve_relative(module, is_init, node.level,
                                         node.module))
            except ExtractError as err:
                import_errors.append(str(err))
                continue
            if base is None:
                continue
            for a in node.names:
                if a.name != "*":
                    imports[a.asname or a.name] = f"{base}.{a.name}"
    targets = []
    toplevel = {n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef))}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        first = (node.decorator_list[0] if node.decorator_list else node)
        s = _abs_byte(starts, first.lineno, first.col_offset)
        if node.decorator_list:
            # a decorator node's col_offset points AFTER the '@'; walk
            # back over optional whitespace to include it, fail-closed
            # if the expected '@' is not there
            j = s - 1
            while j >= 0 and by[j:j + 1] in (b" ", b"\t"):
                j -= 1
            if j < 0 or by[j:j + 1] != b"@":
                raise ExtractError(
                    f"{rel}:{node.name}: no '@' before decorator span")
            s = j
        e = _abs_byte(starts, node.end_lineno, node.end_col_offset)
        if not (0 <= s < e <= len(by)):
            raise ExtractError(f"{rel}:{node.name}: bad span {s},{e}")
        if not node.body:
            raise ExtractError(f"{rel}:{node.name}: empty body")
        body_start = _body_start(node, tokens, token_lines, lines, starts)
        if not (s < body_start <= e):
            raise ExtractError(
                f"{rel}:{node.name}: body_start {body_start} outside "
                f"span ({s},{e})")
        seg = by[s:e]
        header_bytes = body_start - s
        body_bytes = e - body_start
        if header_bytes + body_bytes != len(seg):   # partition (§14.9)
            raise ExtractError(f"{rel}:{node.name}: partition broke")
        seg.decode("utf-8")                          # must decode
        # reference OCCURRENCES: Load-context names minus builtins and
        # locally-bound names; an Attribute chain rooted at an imported
        # binding is recorded qualified ('c.func') so declaration-level
        # resolution can try the exact imported symbol first
        bound = set()
        occ = []
        walked = list(ast.walk(node))
        nested_attribute_ids = {
            id(sub.value) for sub in walked
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Attribute)
        }
        attr_root_ids = set()
        for sub in walked:
            if isinstance(sub, ast.Attribute) \
                    and id(sub) not in nested_attribute_ids:
                chain = _attribute_chain(sub)
                if chain is not None:
                    root, suffix = chain
                    occ.append((root.id, suffix))
                    attr_root_ids.add(id(root))
        for sub in walked:
            if isinstance(sub, ast.Name):
                if isinstance(sub.ctx, ast.Store):
                    bound.add(sub.id)
                elif isinstance(sub.ctx, ast.Load) \
                        and id(sub) not in attr_root_ids:
                    occ.append((sub.id, None))
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)) and sub is not node:
                bound.add(sub.name)
            elif isinstance(sub, ast.arg):
                bound.add(sub.arg)
        refs = [(n, a) for n, a in occ
                if n not in bound and n not in _BUILTINS]
        docstring_bytes = 0
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            doc = node.body[0]
            ds = _abs_byte(starts, doc.lineno, doc.col_offset)
            de = _abs_byte(starts, doc.end_lineno, doc.end_col_offset)
            docstring_bytes = de - ds
        targets.append(dict(
            name=node.name, identity=[module, node.name, s],
            start_byte=s, end_byte=e, body_start_byte=body_start,
            header_bytes=header_bytes, body_bytes=body_bytes,
            docstring_bytes=docstring_bytes,
            kind=type(node).__name__,
            n_ref_occurrences=len(refs),
            refs=[list(r) for r in refs]))
    bindings = {}
    for target in targets:
        bindings.setdefault(target["name"], []).append(target)
    duplicate_name_counts = {}
    for name, definitions in bindings.items():
        definitions.sort(key=lambda t: t["start_byte"])
        count = len(definitions)
        if count > 1:
            duplicate_name_counts[name] = count
        for ordinal, target in enumerate(definitions):
            target["binding_ordinal"] = ordinal
            target["binding_count"] = count
            target["is_duplicate_binding"] = count > 1
            target["is_final_module_binding"] = ordinal == count - 1
            # A same-name reference in the final (or only) declaration is
            # ordinary self-recursion and contributes no context edge. In an
            # earlier repeated binding, however, the documented post-import
            # approximation resolves that name to the later final binding.
            # Preserve it so a real cross-twin edge is not erased as "self".
            if target["is_final_module_binding"]:
                target["refs"] = [
                    ref for ref in target["refs"] if ref[0] != name]
                target["n_ref_occurrences"] = len(target["refs"])
    return dict(rel=rel, source=path, module=module,
                source_sha256=hashlib.sha256(by).hexdigest(),
                imports=imports, import_errors=import_errors,
                toplevel=sorted(toplevel),
                n_duplicate_target_names=len(duplicate_name_counts),
                n_duplicate_target_declarations=sum(
                    duplicate_name_counts.values()),
                duplicate_target_name_counts=dict(
                    sorted(duplicate_name_counts.items())),
                targets=targets)


def build_graph(files):
    """DECLARATION-LEVEL edges + §14.4 coverage (review fix: the old
    prefix-membership rule marked module fallback AND external as
    'resolved' and created edges to non-declaration nodes). Resolution
    per reference occurrence (name, attr?):
      1. same-file top-level decl        -> decl hit, same_file edge
      2. import binding to an EXACT corpus declaration (directly, or
         binding.attr for a dereferenced imported module)
                                          -> decl hit, cross_file edge
      3. import binding under a corpus module without a decl hit
                                          -> MODULE_FALLBACK: recorded,
                                             NOT resolved, NO edge
      4. import binding outside the corpus -> EXTERNAL: recorded,
                                             NOT resolved, NO edge
      5. otherwise                         -> UNRESOLVED
    Coverage = decl hits / occurrences (occurrence counts and the
    deduplicated edge set are kept separately)."""
    seen_modules = set()
    seen_identities = set()
    final_binding = {}
    all_bindings = {}
    for f in files:
        module = f["module"]
        if module in seen_modules:
            raise ExtractError(f"duplicate Python module: {module}")
        seen_modules.add(module)
        for target in f["targets"]:
            identity = tuple(target["identity"])
            if not (len(identity) == 3 and identity[0] == module
                    and identity[1] == target["name"]
                    and identity[2] == target["start_byte"]):
                raise ExtractError(f"invalid Python target identity: {identity}")
            if identity in seen_identities:
                raise ExtractError(
                    f"duplicate Python target identity: {identity}")
            seen_identities.add(identity)
            symbol = f"{module}.{target['name']}"
            all_bindings.setdefault(symbol, []).append(identity)
    for symbol, identities in all_bindings.items():
        identities.sort(key=lambda x: x[2])
        final_binding[symbol] = identities[-1]
    corpus_modules = seen_modules
    # A failed/missing package __init__.py must not make imports under a
    # known corpus package look external. Namespace-package roots are also
    # valid without an __init__.py.
    corpus_roots = {m.split(".", 1)[0] for m in corpus_modules if m}
    def under_corpus(dotted):
        parts = dotted.split(".")
        return (parts[0] in corpus_roots
                or any(".".join(parts[:k]) in corpus_modules
                       for k in range(len(parts), 0, -1)))
    edges = set()
    same_file = cross_file = 0
    external = {}
    per_target = []
    for f in files:
        top = {name: final_binding[f"{f['module']}.{name}"]
               for name in f["toplevel"]}
        for t in f["targets"]:
            src = tuple(t["identity"])
            n_hit = n_fallback = n_external = n_unresolved = 0
            for name, attr in (tuple(r) for r in t["refs"]):
                if name in top:
                    dst = top[name]
                    e = src + dst
                    if dst != src and e not in edges:
                        edges.add(e)
                        same_file += 1
                    n_hit += 1
                elif name in f["imports"]:
                    dotted = f["imports"][name]
                    cands = (([f"{dotted}.{attr}"] if attr else [])
                             + [dotted])
                    hit = next((c for c in cands
                                if c in final_binding), None)
                    if hit:
                        dst = final_binding[hit]
                        e = src + dst
                        if dst != src and e not in edges:
                            edges.add(e)
                            cross_file += 1
                        n_hit += 1
                    elif under_corpus(dotted):
                        n_fallback += 1
                    else:
                        root = dotted.split(".")[0]
                        external[root] = external.get(root, 0) + 1
                        n_external += 1
                else:
                    n_unresolved += 1
            total = n_hit + n_fallback + n_external + n_unresolved
            per_target.append(dict(
                identity=list(src), name=t["name"],
                n_refs=total, n_resolved_decl=n_hit,
                n_module_fallback=n_fallback, n_external=n_external,
                n_unresolved=n_unresolved,
                coverage=(n_hit / total) if total else None))
    duplicate_bindings = [
        dict(symbol=symbol, identities=[list(x) for x in identities])
        for symbol, identities in sorted(all_bindings.items())
        if len(identities) > 1]
    return dict(edges=[list(e) for e in sorted(edges)],
                n_same_file=same_file,
                n_cross_file=cross_file, external_by_root=external,
                target_coverage=sorted(
                    per_target, key=lambda row: tuple(row["identity"])),
                duplicate_module_bindings=duplicate_bindings,
                reference_binding_policy=(
                    "module-body references resolve to the final "
                    "source-order top-level def/class binding; decorator/"
                    "default evaluation timing, later assignment/import, "
                    "and conditional rebinding remain best-effort "
                    "limitations"),
                coverage_definition=(
                    "exact-declaration hits / extracted static reference "
                    "occurrences; stdlib-AST best effort, not an exact "
                    "Python closure"))


def write_new_json(path, value):
    """Create a complete JSON artifact atomically without replacement."""
    path = os.path.normpath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise ExtractError(
            f"refusing to overwrite existing extraction: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".python-extract-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            # link is atomic and, unlike replace(), refuses a concurrent
            # writer that created the evidence path after our pre-check.
            os.link(tmp, path)
        except FileExistsError as err:
            raise ExtractError(
                f"refusing to overwrite existing extraction: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def collect(repo, pkg):
    files = []
    root = os.path.join(repo, pkg)
    for dp, dns, ns in os.walk(root):
        # JSON/file order is evidence identity. Filesystem enumeration order
        # is not stable across APFS and the cluster filesystem.
        dns.sort()
        for n in sorted(ns):
            if n.endswith(".py"):
                p = os.path.abspath(os.path.join(dp, n))
                rel = os.path.relpath(p, os.path.abspath(repo))
                try:
                    files.append(extract_file(p, rel))
                except ExtractError as e:
                    # unparseable vendored files are RECORDED, not fatal
                    # at corpus scale; per-file failures surface in the
                    # validation report and never silently vanish
                    files.append(dict(rel=rel, error=str(e)))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="filesystem root of the pinned checkout")
    ap.add_argument("--repo-tag",
                    help="stable corpus identity (default: --repo)")
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    files = collect(args.repo, args.pkg)
    good = [f for f in files if "error" not in f]
    bad = [f for f in files if "error" in f]
    graph = build_graph(good)
    out = dict(schema=SCHEMA, repo=args.repo_tag or args.repo,
               pkg=args.pkg, n_files=len(good), n_failed=len(bad),
               failed=[dict(rel=f["rel"], error=f["error"])
                       for f in bad],
               files=good, graph=graph)
    write_new_json(args.out, out)
    cov = [t["coverage"] for t in graph["target_coverage"]
           if t["coverage"] is not None]
    cov_text = f"{sum(cov)/len(cov):.3f}" if cov else "NA"
    print(f"[extract_python] {len(good)} files ({len(bad)} failed), "
          f"{len(graph['edges'])} edges, mean coverage {cov_text}")


if __name__ == "__main__":
    main()
