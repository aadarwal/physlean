#!/usr/bin/env python3
"""V2-a Lean extractor (G3.5; DESIGN_V2 §2/§14): stdlib-only parsing of
Lean 4.32 .ilean v5 JSON into source-resolved declaration edges and
byte-exact declaration spans with a validated header/body partition.

NEW-FILES-ONLY constraint (PREREG §13): this module never touches
eval_incontext.py / layout.py and adds no dependency, so no raw cell or
environment identity moves.

.ilean v5 COMPACT shape consumed — verified against the installed
v4.32 toolchain sources (Lean/Server/References.lean,
Lean/Data/Lsp/Internal.lean), fail-closed on any deviation; version
must be EXACTLY 5:
  - directImports: array of [module, isPrivate, isAll, isMeta]
    (Lsp.ImportInfo ToJson = Json.arr).
  - decls: object declName -> FLAT 8-INT array [rangeStartLine,
    rangeStartChar, rangeEndLine, rangeEndChar, selStartLine,
    selStartChar, selEndLine, selEndChar] (DeclInfo ToJson; lines are
    0-based, chars are UTF-16 code units).
  - references: object whose KEYS are the COMPRESSED JSON of
    RefIdentJsonRepr — '{"c":{"m":<defining module>,"n":<const name>}}'
    for constants, '{"f":{"m":...,"i":...}}' for fvars (local; skipped
    for edges, counted) — and whose values are RefInfo objects
    {"definition": null | [l0,c0,l1,c1(,parentDecl)],
     "usages": [[l0,c0,l1,c1(,parentDecl)], ...]}: location arrays of
    length 4 OR 5, parentDecl fifth; a length-4 location means the
    reference IS itself a declaration (upstream stores parentDecl as
    the empty string and omits it in serialization).
The const key carrying the DEFINING module `m` means external-vs-corpus
attribution needs no name heuristics. All positions are UTF-16 code
units — the byte-exact conversion (surrogate-pair aware; beyond-BMP
glyphs like 𝔸 are 2 units / 4 bytes while ∀ is 1 unit / 3 bytes) lives
in LineIndex and is the correctness-critical step for every span.

K4 CLOSURE DEFINITION — EXPLICIT FREEZE, FOR REVIEW BEFORE THE PILOT
(design-doc ratification is the design owner's): the PRIMARY k4 graph
is the SOURCE-REFERENCE closure derived from .ilean references —
deterministic, dependency-free, and covering every source-visible
usage. The KERNEL-PREMISE closure (getUsedConstants via the Lake
companion) is STRICTLY LARGER — implicit typeclass instances and
simp-fired lemmas are genuine premises that never appear as source
references — and is planned as a RECORDED per-target coverage metric at
the pilot (analogous to §14.4's Python coverage), not as the primary
definition. Changing this constant is a logged design amendment.
"""
import argparse, hashlib, json, os, re, sys, tempfile

K4_CLOSURE_DEFINITION = "source-reference"   # vs "kernel-premise"
ILEAN_VERSION = 5
V2A_SEED = "v2a:20260808"                    # §14.19 target-sampling seed


class ExtractError(RuntimeError):
    """Fail-closed extraction error (never silently skipped)."""


# ---------------------------------------------------------------- UTF-16

class LineIndex:
    """Byte-exact LSP position resolution for one source file.

    Rejects '\r' outright (fail-closed: mathlib/physlib are LF-only and
    a CR would silently shift every downstream byte offset)."""

    def __init__(self, text):
        if "\r" in text:
            raise ExtractError("CR in source text — LF-only expected")
        self.text = text
        self.lines = text.split("\n")
        self.line_start = []
        pos = 0
        for ln in self.lines:
            self.line_start.append(pos)
            pos += len(ln.encode("utf-8")) + 1   # + '\n'
        self.total_bytes = len(text.encode("utf-8"))

    def pos_to_byte(self, line, char16):
        """(line, UTF-16 column) -> absolute byte offset. Errors on an
        out-of-range line, a column beyond line end, or a column landing
        INSIDE a surrogate pair (all indicate a mis-parse)."""
        if not (isinstance(line, int) and 0 <= line < len(self.lines)):
            raise ExtractError(f"line {line} out of range")
        if not (isinstance(char16, int) and char16 >= 0):
            raise ExtractError(f"bad UTF-16 column {char16!r}")
        units = 0
        nbytes = 0
        for ch in self.lines[line]:
            if units == char16:
                break
            u = 2 if ord(ch) > 0xFFFF else 1
            if units + u > char16:
                raise ExtractError(
                    f"UTF-16 column {char16} splits a surrogate pair at "
                    f"line {line}")
            units += u
            nbytes += len(ch.encode("utf-8"))
        else:
            if units != char16:
                raise ExtractError(
                    f"UTF-16 column {char16} beyond end of line {line} "
                    f"({units} units)")
        return self.line_start[line] + nbytes

    def range_to_bytes(self, rng):
        """LSP range object -> (start_byte, end_byte), start <= end."""
        try:
            s = self.pos_to_byte(rng["start"]["line"],
                                 rng["start"]["character"])
            e = self.pos_to_byte(rng["end"]["line"],
                                 rng["end"]["character"])
        except (KeyError, TypeError) as ex:
            raise ExtractError(f"malformed range {rng!r}: {ex}")
        if s > e:
            raise ExtractError(f"inverted range {rng!r}")
        return s, e


# ---------------------------------------------------------------- ilean

def _lsp_range(l0, c0, l1, c1):
    return dict(start=dict(line=l0, character=c0),
                end=dict(line=l1, character=c1))


def _ints(xs, where, n=None):
    if n is not None and len(xs) != n:
        raise ExtractError(f"{where}: expected {n} ints, got {len(xs)}")
    for x in xs:
        if not isinstance(x, int) or isinstance(x, bool) or x < 0:
            raise ExtractError(f"{where}: bad position value {x!r}")
    return xs


def _location(arr, where):
    """RefInfo.Location array: [l0,c0,l1,c1] or [l0,c0,l1,c1,parent].
    Length 4 == the location IS itself a declaration (no parent)."""
    if not isinstance(arr, list) or len(arr) not in (4, 5):
        raise ExtractError(f"{where}: location must be a length-4/5 "
                           f"array, got {arr!r}")
    _ints(arr[:4], where)
    parent = arr[4] if len(arr) == 5 else None
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise ExtractError(f"{where}: bad parentDecl {parent!r}")
    return dict(range=_lsp_range(*arr[:4]), parentDecl=parent)


def parse_ilean(raw):
    """STRICT compact .ilean v5 parse (see module docstring for the
    verified upstream shape) -> dict(module, direct_imports, decls,
    references, n_fvar_refs, n_definitions). decls: name ->
    {range, selectionRange} in nested LSP shape; references: one entry
    PER CONST USAGE: {name, module (DEFINING), parentDecl|None, range}.
    fvar identifiers are local references — skipped for edges, counted.
    Definition sites (the 'definition' field) are counted, not edges."""
    if not isinstance(raw, dict):
        raise ExtractError(f"ilean root is {type(raw).__name__}, not dict")
    if raw.get("version") != ILEAN_VERSION:
        raise ExtractError(f"ilean version {raw.get('version')!r} != "
                           f"{ILEAN_VERSION} — toolchain drift, refusing")
    for k in ("module", "directImports", "decls", "references"):
        if k not in raw:
            raise ExtractError(f"ilean missing {k!r}; keys={sorted(raw)}")
    module = raw["module"]
    if not isinstance(module, str) or not module:
        raise ExtractError(f"bad module {module!r}")

    imports = []
    if not isinstance(raw["directImports"], list):
        raise ExtractError("directImports is not a list")
    for i, imp in enumerate(raw["directImports"]):
        if not (isinstance(imp, list) and len(imp) == 4
                and isinstance(imp[0], str)
                and all(isinstance(b, bool) for b in imp[1:])):
            raise ExtractError(f"directImports[{i}]: expected "
                               f"[module, isPrivate, isAll, isMeta], "
                               f"got {imp!r}")
        imports.append(dict(module=imp[0], isPrivate=imp[1],
                            isAll=imp[2], isMeta=imp[3]))

    decls = {}
    if not isinstance(raw["decls"], dict):
        raise ExtractError(f"decls is {type(raw['decls']).__name__}")
    for name, arr in raw["decls"].items():
        if not isinstance(name, str) or not name:
            raise ExtractError(f"bad decl name {name!r}")
        if not isinstance(arr, list):
            raise ExtractError(f"decl {name}: expected 8-int array")
        _ints(arr, f"decl {name}", n=8)
        decls[name] = dict(range=_lsp_range(*arr[:4]),
                           selectionRange=_lsp_range(*arr[4:]))

    refs = []
    definition_parents = {}
    definition_sites = {}
    n_fvar = n_defs = 0
    if not isinstance(raw["references"], dict):
        raise ExtractError(
            f"references is {type(raw['references']).__name__}")
    for key, info in raw["references"].items():
        try:
            ident = json.loads(key)
        except (TypeError, ValueError) as e:
            raise ExtractError(f"unparseable RefIdent key {key!r}: {e}")
        if not isinstance(ident, dict) or len(ident) != 1:
            raise ExtractError(f"bad RefIdent {ident!r}")
        # RefInfo shape is UNIFORM for const and fvar (review tighten):
        # BOTH keys must be present, usages must be a list, and every
        # location — usage or definition — validates as length 4/5
        if not isinstance(info, dict) or "usages" not in info \
                or "definition" not in info:
            raise ExtractError(
                f"RefInfo for {key!r} missing usages/definition; keys="
                f"{sorted(info) if isinstance(info, dict) else info!r}")
        if not isinstance(info["usages"], list):
            raise ExtractError(f"usages for {key!r} not a list")
        def_loc = None
        if info["definition"] is not None:
            def_loc = _location(info["definition"],
                                f"definition of {key!r}")
            n_defs += 1
        if "f" in ident:
            fv = ident["f"]
            if not (isinstance(fv, dict)
                    and isinstance(fv.get("m"), str)
                    and isinstance(fv.get("i"), str)):
                raise ExtractError(f"bad fvar ident {ident!r}")
            for u in info["usages"]:
                _location(u, f"fvar usage in {module}")
            n_fvar += len(info["usages"])
            continue
        if "c" not in ident:
            raise ExtractError(f"unknown RefIdent kind {ident!r}")
        c = ident["c"]
        if not (isinstance(c, dict) and isinstance(c.get("m"), str)
                and c["m"] and isinstance(c.get("n"), str) and c["n"]):
            raise ExtractError(f"bad const ident {ident!r}")
        if def_loc is not None:
            if c["m"] != module:
                raise ExtractError(
                    f"definition of {c['n']} claims foreign module "
                    f"{c['m']} while parsing {module}")
            site = dict(range=def_loc["range"],
                        parentDecl=def_loc["parentDecl"])
            if c["n"] in definition_sites \
                    and definition_sites[c["n"]] != site:
                raise ExtractError(
                    f"conflicting definition sites for {c['n']}")
            definition_sites[c["n"]] = site
        # a length-5 DEFINITION gives the source GENERATING declaration
        # of an otherwise-unrenderable const (constructors, projections,
        # private/generated helpers — review finding: 7,823 in core):
        # captured for k4 folding in build_corpus_graph
        if def_loc is not None and def_loc["parentDecl"] is not None:
            if c["n"] in definition_parents \
                    and definition_parents[c["n"]] != def_loc["parentDecl"]:
                raise ExtractError(
                    f"conflicting definition parents for {c['n']}")
            definition_parents[c["n"]] = def_loc["parentDecl"]
        for u in info["usages"]:
            loc = _location(u, f"usage of {c['n']}")
            refs.append(dict(name=c["n"], module=c["m"],
                             parentDecl=loc["parentDecl"],
                             range=loc["range"]))
    return dict(module=module, direct_imports=imports, decls=decls,
                references=refs, definition_parents=definition_parents,
                definition_sites=definition_sites,
                n_fvar_refs=n_fvar, n_definitions=n_defs)


# ------------------------------------------------- comment/string masking

def code_mask(text):
    """True per char iff CODE (not comment/string). Handles line
    comments `--`, NESTED block comments `/- -/`, and string literals
    with backslash escapes. Apostrophes are NOT delimiters (identifiers
    like `foo'`). Known limitation, recorded: char literals and «»
    identifiers containing quote characters are not special-cased —
    the 20-target validation surfaces any misparse via round-trip."""
    n = len(text)
    mask = [True] * n
    i = 0
    depth = 0            # nested block comments
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if depth > 0:
            mask[i] = False
            if two == "-/":
                mask[i + 1] = False
                depth -= 1
                i += 2
                continue
            if two == "/-":
                mask[i + 1] = False
                depth += 1
                i += 2
                continue
            i += 1
            continue
        if two == "/-":
            mask[i] = mask[i + 1] = False
            depth += 1
            i += 2
            continue
        if two == "--":
            while i < n and text[i] != "\n":
                mask[i] = False
                i += 1
            continue
        if c == '"':
            mask[i] = False
            i += 1
            while i < n:
                mask[i] = False
                if text[i] == "\\" and i + 1 < n:
                    mask[i + 1] = False
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        i += 1
    if depth > 0:
        raise ExtractError("unterminated block comment")
    return mask


_OPEN = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
_CLOSE = {v: k for k, v in _OPEN.items()}


def split_header_body(decl_text):
    """FROZEN v1 split rule (§14.5 syntactic pass; reviewed with this
    module): body starts at the EARLIEST depth-0, code-masked occurrence
    of `:=`, the keyword `where`, or a match-arm `|` after the first
    whitespace of the declaration. Returns (header, body, kind) with
    header + body == decl_text EXACTLY (partition — §14.9 round-trip is
    trivial by construction; SCIENTIFIC boundary correctness is what the
    20-target validation checks). kind None => no split found (e.g.
    axiom/abbrev-style): recorded, excluded from body-target
    eligibility, never a crash."""
    mask = code_mask(decl_text)
    depth = 0
    n = len(decl_text)
    i = 0
    seen_space = False
    while i < n:
        if not mask[i]:
            i += 1
            continue
        c = decl_text[i]
        if c.isspace():
            seen_space = True
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and seen_space:
            if decl_text.startswith(":=", i):
                return decl_text[:i], decl_text[i:], ":="
            if c == "|" and not decl_text.startswith("|>", i) \
                    and not decl_text.startswith("||", i):
                return decl_text[:i], decl_text[i:], "|"
            if decl_text.startswith("where", i):
                before = decl_text[i - 1] if i else " "
                after = decl_text[i + 5:i + 6] or " "
                if not (before.isalnum() or before in "_'") \
                        and not (after.isalnum() or after in "_'"):
                    return decl_text[:i], decl_text[i:], "where"
        i += 1
    return decl_text, "", None


# DESIGN_V2 §2 frozen Lean target kinds. Everything else — instances,
# macros/macro_rules, unexpander-attributed defs stay defs by command
# keyword — is recorded and INELIGIBLE, never sampled. Real-corpus
# necessity (review finding on 85,353 core decls): without this filter
# the seeded sampler would draw macros/instances/notations as targets.
ELIGIBLE_KINDS = ("theorem", "lemma", "def")
KNOWN_KINDS = ELIGIBLE_KINDS + (
    "abbrev", "instance", "structure", "class", "inductive", "opaque",
    "axiom", "example", "macro", "macro_rules", "notation", "syntax",
    "elab", "elab_rules", "declare_syntax_cat", "deriving", "mutual",
    "initialize", "builtin_initialize", "attribute", "alias")
_MODIFIERS = frozenset((
    "private", "protected", "noncomputable", "unsafe", "partial",
    "scoped", "local", "meta", "public", "nonrec"))
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def classify_decl_kind(decl_text):
    """FAIL-CLOSED source command-kind classifier (review finding:
    naive startswith is defeated by doc comments, @[...] attributes,
    and modifier stacks). Skips code-masked comments (doc comments are
    /-- ... -/ block comments to the mask), whole @[...] attribute
    groups (bracket-matched, string-safe), and known modifiers; returns
    (kind, token): kind in KNOWN_KINDS, or 'unknown' with the offending
    token preserved — unknown is INELIGIBLE and counted, never a
    crash."""
    mask = code_mask(decl_text)
    n = len(decl_text)
    i = 0
    while i < n:
        if not mask[i] or decl_text[i].isspace():
            i += 1
            continue
        if decl_text.startswith("@[", i):
            depth = 0
            while i < n:
                if mask[i]:
                    if decl_text[i] == "[":
                        depth += 1
                    elif decl_text[i] == "]":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                i += 1
            continue
        m = _WORD_RE.match(decl_text, i)
        if not m:
            return ("unknown", decl_text[i:i + 12])
        word = m.group(0)
        if word in _MODIFIERS:
            i = m.end()
            continue
        if word in KNOWN_KINDS:
            return (word, word)
        return ("unknown", word)
    return ("unknown", "")


_SHELL_RE = re.compile(
    r"^(namespace\s+\S+|section(?:\s+\S+)?|end(?:\s+\S+)?|open\s+.+|"
    r"variable[s]?\s*.+|universe[s]?\s+.+|set_option\s+.+|"
    r"noncomputable\s+section(?:\s+\S+)?)\s*$")


def shell_snapshots(text, decl_start_bytes, idx):
    """BATCH syntactic shell reconstruction (§14.5) — perf fix: the
    per-decl scan recomputed code_mask and re-encoded text prefixes for
    every declaration (O(decls x file), with a quadratic prefix decode
    inside — the 2,433-module core stress run pegged a CPU for over a
    minute; mathlib would be far worse). ONE mask, ONE char-line-start
    pass, ONE frame sweep over the file; the active shell is
    snapshotted at each requested (sorted, deduplicated) decl start
    byte. Scope-correct as before: commands attach to the enclosing
    namespace/section frame and die with its `end`. Returns
    {start_byte: [commands]}."""
    mask = code_mask(text)
    char_starts = []                   # char offset of each line start,
    pos = 0                            # computed once (was per-decl
    for ln in idx.lines:               # encode/decode of the prefix)
        char_starts.append(pos)
        pos += len(ln) + 1
    wants = sorted(set(decl_start_bytes))
    out = {}
    wi = 0
    frames = [[]]                      # frame 0 = file level
    for lineno, line in enumerate(idx.lines):
        start = idx.line_start[lineno]
        # a decl starting at/before this line's start sees the shell as
        # of the lines STRICTLY before it (same boundary the per-decl
        # scan used: lines with start >= decl_start are not processed)
        while wi < len(wants) and start >= wants[wi]:
            out[wants[wi]] = [c for fr in frames for c in fr]
            wi += 1
        raw = line.rstrip()
        if not raw or line[:1].isspace():
            continue
        cpos = char_starts[lineno]
        if cpos < len(mask) and not mask[cpos]:
            continue
        m = _SHELL_RE.match(raw)
        if not m:
            continue
        cmd = m.group(1)
        if cmd.startswith(("namespace ", "section")) \
                or cmd.startswith("noncomputable section"):
            frames.append([cmd])
        elif cmd == "end" or cmd.startswith("end "):
            if len(frames) > 1:
                frames.pop()
        else:
            frames[-1].append(cmd)
    while wi < len(wants):             # decls at/after the last line
        out[wants[wi]] = [c for fr in frames for c in fr]
        wi += 1
    return out


def active_shell(text, decl_start_byte, idx):
    """One-target wrapper over shell_snapshots (kept for tests and
    call-site clarity; identical semantics)."""
    return shell_snapshots(text, [decl_start_byte],
                           idx)[decl_start_byte]


# ---------------------------------------------------------------- corpus

def target_priority(repo, module, decl):
    """§14.19 deterministic seeded target priority — MODULE-QUALIFIED
    (pre-outcome amendment, logged: bare fully-elaborated names are NOT
    unique across a source tree — executables each define `main`; the
    live compiler-graph stress hit exactly LakeMain vs LeanChecker).
    Amended before any pilot sample or committed extraction existed.

    FROZEN ENCODING: SHA256 over the UTF-8 bytes of the canonical JSON
    array  json.dumps([V2A_SEED, repo, module, decl],
    ensure_ascii=False, separators=(",", ":")).  JSON string escaping
    length-delimits every field, so quoted Lean names («...» guillemet
    identifiers may contain any punctuation, including ':') cannot
    collide with a different (repo, module, decl) split — raw colon
    concatenation could not guarantee that."""
    return hashlib.sha256(json.dumps(
        [V2A_SEED, repo, module, decl],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def build_corpus_graph(modules):
    """modules: list of parse_ilean outputs. Returns the
    SOURCE-REFERENCE graph (K4 primary; see the frozen block above)
    under MODULE-QUALIFIED node identity (module, decl) — the
    pre-outcome amendment adopted after the live compiler-graph stress
    hit `main` defined in both LakeMain and LeanChecker: bare
    fully-elaborated names are unique per ENVIRONMENT, not per source
    tree, so every node, edge, fold entry, and preserved reference
    carries its module. Under this identity cross-module name
    collisions are UNREPRESENTABLE (the old corpus-wide duplicate
    check is gone because nothing global is keyed by bare name).
    Edges are quadruples [src_module, src_decl, dst_module, dst_decl],
    deduplicated, self-edges dropped, split same_file/cross_file by
    module equality; the referenced const's DEFINING module comes from
    the RefIdent key itself. parentDecl resolves against the SAME
    module's decls; unresolvable parents are ORPHANS, counted per
    module, never positionally guessed."""
    corpus_modules = set()
    decls_by_module = {}
    gen_by_module = {}
    for m in modules:
        if m["module"] in corpus_modules:
            # fail closed: a silently overwritten module record would
            # drop decls/parents from the graph without a trace
            raise ExtractError(
                f"duplicate module record in corpus: {m['module']}")
        corpus_modules.add(m["module"])
        decls_by_module[m["module"]] = set(m["decls"])
        gen_by_module[m["module"]] = dict(
            m.get("definition_parents") or {})

    def fold(module, name):
        """Chase generating parents WITHIN the defining module (chains
        bounded, cycle-guarded) to a decl WITH a span; None =
        unrenderable residue. Definition sites live in the module that
        defines the const, so the chain never leaves `module`."""
        seen = set()
        cur = name
        gp = gen_by_module.get(module, {})
        spans = decls_by_module.get(module, set())
        for _ in range(8):
            if cur in spans:
                return cur
            if cur in seen or cur not in gp:
                return None
            seen.add(cur)
            cur = gp[cur]
        return None

    edges = set()
    external = {}
    # §14.20 k4x PRESERVATION: exact deduplicated external reference
    # identities as QUADRUPLES [src_module, src_decl, defining_module,
    # const_name] (the bare-parentDecl triple was ambiguous under
    # exactly the duplicate-name collision that motivated this
    # refactor); aggregates alone cannot reconstruct physlib's
    # pinned-mathlib closure later. Still EXCLUDED from same-repo k4.
    ext_edges = set()
    ext_by_target = {}
    ext_by_module = {}
    unrend_refs = set()
    internal_unrenderable = {}
    renderability = {}
    n_folded = 0
    same_file = cross_file = 0
    cov = {}
    for m in modules:
        mod = m["module"]
        local_decls = decls_by_module[mod]
        usable = orphan = 0
        for r in m["references"]:
            pd = r["parentDecl"]
            if pd is None or pd not in local_decls:
                orphan += 1
                continue
            usable += 1
            tgt = r["name"]
            tmod = r["module"]
            if tmod == mod and tgt == pd:
                continue
            if tmod in corpus_modules:
                target_render = renderability.setdefault(mod, {}).setdefault(
                    pd, dict(n_internal_occurrences=0,
                             n_renderable_occurrences=0,
                             n_unrenderable_occurrences=0))
                target_render["n_internal_occurrences"] += 1
                node = tgt if tgt in decls_by_module[tmod] \
                    else fold(tmod, tgt)
                if node is None:
                    target_render["n_unrenderable_occurrences"] += 1
                    internal_unrenderable[tmod] = \
                        internal_unrenderable.get(tmod, 0) + 1
                    # exact identities preserved (independent raw-.ilean
                    # audits re-derive every target's resolved/folded/
                    # unrenderable partition without trusting this
                    # function's internals)
                    unrend_refs.add((mod, pd, tmod, tgt))
                    continue
                target_render["n_renderable_occurrences"] += 1
                if node != tgt:
                    n_folded += 1
                if tmod == mod and node == pd:
                    continue           # folded onto its own parent
                edge = (mod, pd, tmod, node)
                if edge not in edges:
                    edges.add(edge)
                    if tmod == mod:
                        same_file += 1
                    else:
                        cross_file += 1
            else:
                root = tmod.split(".", 1)[0]
                external[root] = external.get(root, 0) + 1
                ext_edges.add((mod, pd, tmod, tgt))
                ext_by_target.setdefault(mod, {})[pd] = \
                    ext_by_target.get(mod, {}).get(pd, 0) + 1
                ext_by_module[tmod] = ext_by_module.get(tmod, 0) + 1
        tot = usable + orphan
        cov[mod] = dict(usable=usable, orphan=orphan,
                        coverage=(usable / tot) if tot else None)
    for targets in renderability.values():
        for counts in targets.values():
            total = counts["n_internal_occurrences"]
            counts["coverage"] = (counts["n_renderable_occurrences"] /
                                  total) if total else None
    return dict(edges=[list(e) for e in sorted(edges)],
                n_same_file=same_file,
                n_cross_file=cross_file, external_by_root=external,
                external_reference_edges=[list(e) for e in
                                          sorted(ext_edges)],
                external_ref_counts_by_target=ext_by_target,
                external_ref_counts_by_module=ext_by_module,
                n_folded_generated=n_folded,
                internal_unrenderable_by_module=internal_unrenderable,
                internal_unrenderable_references=[
                    list(e) for e in sorted(unrend_refs)],
                n_internal_unrenderable=sum(
                    internal_unrenderable.values()),
                internal_renderability_by_target=renderability,
                parent_decl_coverage=cov)


def transitive_closure(edges, root):
    """Transitive closure over MODULE-QUALIFIED quadruple edges
    [src_module, src_decl, dst_module, dst_decl]. `root` is a
    (module, decl) pair; returns a set of (module, decl) pairs."""
    root = tuple(root)
    adj = {}
    for e in edges:
        sm, sd, dm, dd = e
        adj.setdefault((sm, sd), set()).add((dm, dd))
    seen, stack = set(), [root]
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    seen.discard(root)
    return seen


def extract_file(src_path, ilean_path):
    """One (source, .ilean) pair -> module record with byte-exact decl
    spans, header/body partitions, and shell commands. Fail-closed on
    any span/round-trip inconsistency."""
    text = open(src_path, encoding="utf-8").read()
    idx = LineIndex(text)
    parsed = parse_ilean(json.load(open(ilean_path, encoding="utf-8")))
    by = text.encode("utf-8")
    # pass 1: byte spans for every decl (validated against the FILE;
    # containment of the selection is RECORDED, not required — review
    # finding: 24/85,353 core decls are generated macro-rule DeclInfos
    # whose selectionRange is the shared enclosing macro_rules
    # selection — valid files, ineligible decls)
    spans = {}
    for name, d in parsed["decls"].items():
        s, e = idx.range_to_bytes(d["range"])
        ss, se = idx.range_to_bytes(d["selectionRange"])
        if not (s <= e <= idx.total_bytes and ss <= se <= idx.total_bytes):
            raise ExtractError(
                f"{name}: range outside file ({s},{e},{ss},{se})")
        spans[name] = (s, e, ss, se)
    # Length-4 definition locations omit parentDecl. Real-core audit found
    # that most are generated fields/constructors such as Pure.pure whose
    # definition token lies inside the generating structure/inductive span.
    # Recover ONLY a unique smallest enclosing declaration; ties and misses
    # remain explicit residue. No name heuristic is used.
    definition_parents = dict(parsed["definition_parents"])
    definition_parent_provenance = {
        name: "explicit-parent" for name in definition_parents}
    definition_site_diagnostics = dict(
        own_decl=0, explicit_parent=len(definition_parents),
        unique_smallest_enclosing=0, ambiguous_smallest=0,
        no_enclosing_span=0, position_name_prefix_agree=0,
        position_name_prefix_mismatch=0)
    definition_position_name_mismatches = []
    for name, site in parsed["definition_sites"].items():
        if name in spans:
            definition_site_diagnostics["own_decl"] += 1
            continue
        if name in definition_parents:
            continue
        ds, de = idx.range_to_bytes(site["range"])
        candidates = sorted(
            (e - s, decl) for decl, (s, e, _, _) in spans.items()
            if s <= ds <= de <= e)
        if not candidates:
            definition_site_diagnostics["no_enclosing_span"] += 1
            continue
        smallest = candidates[0][0]
        winners = [decl for size, decl in candidates if size == smallest]
        if len(winners) != 1:
            definition_site_diagnostics["ambiguous_smallest"] += 1
            continue
        definition_parents[name] = winners[0]
        definition_parent_provenance[name] = "unique-smallest-enclosing"
        definition_site_diagnostics["unique_smallest_enclosing"] += 1
        if name == winners[0] or name.startswith(winners[0] + "."):
            definition_site_diagnostics["position_name_prefix_agree"] += 1
        else:
            # Diagnostic only: geometry defines the generating source span.
            # Private/macro-generated names need not share its dotted prefix.
            definition_site_diagnostics["position_name_prefix_mismatch"] += 1
            definition_position_name_mismatches.append([name, winners[0]])
    # ONE batched shell sweep for the whole file (perf fix: was a full
    # rescan per decl)
    shell_map = shell_snapshots(text, [v[0] for v in spans.values()],
                                idx)
    decls_out = {}
    for name, (s, e, ss, se) in spans.items():
        selection_contained = s <= ss <= se <= e
        span = by[s:e].decode("utf-8")
        header, body, kind = split_header_body(span)
        if header + body != span:      # partition invariant (§14.9)
            raise ExtractError(f"{name}: header/body partition broke")
        cmd_kind, cmd_token = classify_decl_kind(span)
        decls_out[name] = dict(
            start_byte=s, end_byte=e,
            sel_start_byte=ss, sel_end_byte=se,
            selection_contained=selection_contained,
            kind=cmd_kind, kind_token=cmd_token,
            eligible_kind=cmd_kind in ELIGIBLE_KINDS,
            header_bytes=len(header.encode("utf-8")),
            body_bytes=len(body.encode("utf-8")),
            split_kind=kind,
            shell=shell_map[s])
    return dict(module=parsed["module"], source=src_path,
                source_sha256=hashlib.sha256(by).hexdigest(),
                direct_imports=parsed["direct_imports"],
                decls=decls_out, references=parsed["references"],
                # wiring fix (stress-test finding): without threading
                # this through, real extractions never folded generated
                # constants — only the synthetic unit fixtures did
                definition_parents=definition_parents,
                definition_parent_provenance=definition_parent_provenance,
                definition_site_diagnostics=definition_site_diagnostics,
                definition_position_name_mismatches=(
                    definition_position_name_mismatches))


PAIRS_SCHEMA = "v2a_ilean_pairs_v2"
_PAIR_KEYS = ("module", "match_kind", "source", "ilean", "source_sha256",
              "ilean_sha256")


def load_pairs_manifest(path):
    """STRICT consumption of pair_ilean.py's manifest (schema
    v2a_ilean_pairs_v2; pairs are DICTS — the legacy [src, il] list
    form is REJECTED, no compatibility path). Every pair's source and
    .ilean hashes are RE-COMPUTED here and must match the manifest:
    this side re-verifies the pairing rather than trusting it, so
    inputs that drifted since pairing fail closed before any
    extraction. Returns (pairs, manifest_sha256)."""
    blob = open(path, "rb").read()
    raw = json.loads(blob)
    if not isinstance(raw, dict) or raw.get("schema") != PAIRS_SCHEMA:
        got = raw.get("schema") if isinstance(raw, dict) else \
            type(raw).__name__
        raise ExtractError(f"pairs manifest schema {got!r} != "
                           f"{PAIRS_SCHEMA!r} (legacy list form is not "
                           "accepted)")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ExtractError("pairs manifest has no non-empty pairs list")
    seen = set()
    for i, p in enumerate(pairs):
        if not isinstance(p, dict):
            raise ExtractError(f"pair[{i}] is not a dict")
        missing = [k for k in _PAIR_KEYS if k not in p]
        if missing:
            raise ExtractError(f"pair[{i}] missing {missing}")
        if p["module"] in seen:
            raise ExtractError(f"duplicate module {p['module']} in "
                               "pairs manifest")
        seen.add(p["module"])
        if p["match_kind"] not in ("exact", "srcdir_suffix"):
            raise ExtractError(
                f"pair[{i}] invalid match_kind {p['match_kind']!r}")
        for fk, hk in (("source", "source_sha256"),
                       ("ilean", "ilean_sha256")):
            h = hashlib.sha256(open(p[fk], "rb").read()).hexdigest()
            if h != p[hk]:
                raise ExtractError(
                    f"{p['module']}: {fk} hash {h[:12]} != manifest "
                    f"{str(p[hk])[:12]} — inputs drifted since pairing")
    return pairs, hashlib.sha256(blob).hexdigest()


def extract_from_manifest(pairs_path, repo):
    """Manifest-driven corpus extraction: per pair, the EMBEDDED .ilean
    module must agree with the manifest module, and extract_file's own
    source hash must equal the manifest's (double-entry: pairing and
    extraction independently hash the same bytes). The pairing-manifest
    sha is carried into the output for provenance."""
    pairs, manifest_sha = load_pairs_manifest(pairs_path)
    modules = []
    per_file = []
    for p in pairs:
        rec = extract_file(p["source"], p["ilean"])
        if rec["module"] != p["module"]:
            raise ExtractError(
                f"embedded module {rec['module']!r} != manifest module "
                f"{p['module']!r} for {p['source']}")
        if rec["source_sha256"] != p["source_sha256"]:
            raise ExtractError(
                f"{p['module']}: extraction source hash != manifest")
        # Raw reference occurrences remain in the hash-bound .ilean input and
        # are independently reread by audit_lean_closure.py. Duplicating them
        # into extraction JSON made the 2,433-module core artifact ~0.5 GiB
        # and would inflate mathlib by multiple GiB without adding evidence.
        # Keep exact counts plus the source/rendering records and graph.
        evidence_rec = {k: v for k, v in rec.items() if k != "references"}
        evidence_rec["n_reference_occurrences"] = len(rec["references"])
        evidence_rec["n_definition_parents"] = len(
            rec["definition_parents"])
        per_file.append(evidence_rec)
        modules.append(dict(module=rec["module"], decls=rec["decls"],
                            references=rec["references"],
                            definition_parents=rec["definition_parents"]))
    graph = build_corpus_graph(modules)
    # v2: MODULE-QUALIFIED identity — edges and preserved reference
    # lists are quadruples; consumers must reject v1 (pre-outcome
    # schema bump, no committed extraction predates it)
    return dict(schema="v2a_lean_extract_v2",
                k4_closure_definition=K4_CLOSURE_DEFINITION,
                ilean_version=ILEAN_VERSION, repo=repo,
                pairs_manifest=pairs_path,
                pairs_manifest_sha256=manifest_sha,
                n_files=len(per_file), files=per_file, graph=graph)


def write_new_json(path, value):
    """Publish a complete extraction atomically, never replacing evidence."""
    path = os.path.normpath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        raise ExtractError(f"refusing to overwrite extraction: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".lean-extract-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            os.link(tmp, path)
        except FileExistsError as err:
            raise ExtractError(f"refusing to overwrite extraction: {path}") \
                from err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True,
                    help=f"{PAIRS_SCHEMA} manifest from pair_ilean.py")
    ap.add_argument("--repo", required=True, help="repo tag for §14.19")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = extract_from_manifest(args.pairs, args.repo)
    write_new_json(args.out, out)
    g = out["graph"]
    print(f"[extract_lean] {out['n_files']} files, "
          f"{len(g['edges'])} edges (cross-file {g['n_cross_file']}) "
          f"manifest {out['pairs_manifest_sha256'][:12]} -> {args.out}")


if __name__ == "__main__":
    main()
