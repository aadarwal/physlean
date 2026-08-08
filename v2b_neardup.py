#!/usr/bin/env python3
"""V2-b A6: layout-preserving typed lexers, exact-hash and Jaccard
near-duplicate machinery, audit-packet selection, and mechanical gates.

Implements DESIGN_V2 §15.A6/§15.A12 exactly:
  - typed token records [kind, value] with layout preserved (Python
    INDENT/DEDENT/NEWLINE sentinels; Lean LAYOUT sentinels carrying the
    token line's exact leading horizontal whitespace);
  - VERBATIM-token and IDENTIFIER-NORMALIZED SHA256 over the canonical
    compact-JSON record array (never a delimiter join);
  - lexical 5-grams (layout sentinels excluded; 20-lexical-record floor);
  - exact brute-force Jaccard and the t = 0.70 size+prefix candidate
    algorithm, required test-equivalent on <= 2000-unit fixtures;
  - deterministic, seeded, repo-balanced Jaccard-bin and collision-group
    audit packets (O(m log m) seeded member rule inside each group);
  - the frozen mechanical label -> threshold / activation gates.

Every threshold decision uses exact integer cross-multiplication
(t = num/den), never floats. All hash-relevant identity encoding comes
from committed v2b_common. Fail-closed on schema, identity, or source
hash drift. The CLI builds artifacts from SYNTHETIC extractions only;
running study corpora, drawing targets, or creating labels stays behind
the PREREG boundary.

Lean identifier predicates are transcribed VERBATIM from the pinned
toolchain source Init/Meta/Defs.lean lines 101-134 (v4.32.0):
isLetterLike, isNumericSubscript/isSubScriptAlnum, isIdFirst, isIdRest.
"""
import argparse
import io
import keyword
import sys
import tokenize

from collections import Counter

from v2b_common import (LEAN_KEYWORD_FREEZE_SCHEMA, NEARDUP_SCHEMA, V2BError,
                        artifact_binding, canonical_json_bytes, identity_key,
                        seeded_hash, sha256_bytes, sha256_file, sha256_json,
                        validate_identity, write_new_json)

LEAN_EXTRACT_SCHEMA = "v2a_lean_extract_v3"
PYTHON_EXTRACT_SCHEMA = "v2a_python_extract_v3"
LEXER_CITATION = ("lean identifier predicates: Init/Meta/Defs.lean:101-134 "
                  "(leanprover/lean4 v4.32.0)")
LEXICAL_FLOOR = 20
JACCARD_T = (7, 10)                       # frozen candidate threshold 0.70
SENSITIVITY_T = {"0.70": (7, 10), "0.80": (4, 5), "0.90": (9, 10)}
BIN_EDGES = (("B1", (7, 10), (3, 4)), ("B2", (3, 4), (4, 5)),
             ("B3", (4, 5), (17, 20)), ("B4", (17, 20), (9, 10)),
             ("B5", (9, 10), None))
PYTHON_SENTINELS = ("NEWLINE", "INDENT", "DEDENT")
LEAN_SENTINEL = "LAYOUT"
SENTINEL_KINDS = frozenset(PYTHON_SENTINELS) | {LEAN_SENTINEL}
PYTHON_KEYWORDS = frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)


# ------------------------------------------------------------- lexers

def _lean_is_letter_like(cp):
    return ((0x3b1 <= cp <= 0x3c9 and cp != 0x3bb)
            or (0x391 <= cp <= 0x3A9 and cp not in (0x3A0, 0x3A3))
            or 0x3ca <= cp <= 0x3fb
            or 0x1f00 <= cp <= 0x1ffe
            or 0x2100 <= cp <= 0x214f
            or 0x1d49c <= cp <= 0x1d59f
            or (0x00c0 <= cp <= 0x00ff and cp not in (0x00d7, 0x00f7))
            or 0x0100 <= cp <= 0x017f)


def _lean_is_subscript_alnum(cp):
    return (0x2080 <= cp <= 0x2089 or 0x2090 <= cp <= 0x209c
            or 0x1d62 <= cp <= 0x1d6a or cp == 0x2c7c)


def _lean_id_first(ch):
    return ch.isalpha() and ch.isascii() or ch == "_" \
        or _lean_is_letter_like(ord(ch))


def _lean_id_rest(ch):
    return (ch.isascii() and (ch.isalnum() or ch in "_'!?")) \
        or _lean_is_letter_like(ord(ch)) \
        or _lean_is_subscript_alnum(ord(ch))


def lean_identifier_spelling(text):
    """Whether one complete token satisfies the pinned Lean ID predicates."""
    return isinstance(text, str) and bool(text) \
        and _lean_id_first(text[0]) \
        and all(_lean_id_rest(ch) for ch in text[1:])


def _scan_lean_number(text, start):
    """End index for Lean 4 decimal/scientific and 0b/0o/0x forms."""
    n = len(text)

    def take(index, predicate):
        while index < n and (predicate(text[index]) or text[index] == "_"):
            index += 1
        return index

    ascii_digit = lambda ch: ch.isascii() and ch.isdigit()
    if text[start] == "0" and start + 1 < n:
        prefix = text[start + 1]
        predicates = {
            "b": lambda ch: ch in "01", "B": lambda ch: ch in "01",
            "o": lambda ch: ch in "01234567",
            "O": lambda ch: ch in "01234567",
            "x": lambda ch: ch.isascii()
            and ch in "0123456789abcdefABCDEF",
            "X": lambda ch: ch.isascii()
            and ch in "0123456789abcdefABCDEF",
        }
        if prefix in predicates:
            return take(start + 2, predicates[prefix])
    end = take(start, ascii_digit)
    if end < n and text[end] == "." and not text.startswith("..", end):
        end = take(end + 1, ascii_digit)
    if end < n and text[end] in "eE":
        exponent = end + 1
        if exponent < n and text[exponent] in "+-":
            exponent += 1
        end = take(exponent, ascii_digit)
    return end


def _scan_lean_char(text, start):
    """End index of one valid Lean char literal, or raise fail-closed."""
    n = len(text)
    index = start + 1
    if index >= n:
        raise V2BError("unterminated lean char literal")
    if text[index] != "\\":
        index += 1                              # one Unicode codepoint
    else:
        if index + 1 >= n:
            raise V2BError("unterminated lean char escape")
        escape = text[index + 1]
        if escape in "\\\"'rnt":
            index += 2
        elif escape == "x":
            digits = text[index + 2:index + 4]
            if len(digits) != 2 or any(
                    ch not in "0123456789abcdefABCDEF" for ch in digits):
                raise V2BError("malformed Lean \\x char escape")
            index += 4
        elif escape == "u":
            digits = text[index + 2:index + 6]
            if len(digits) != 4 or any(
                    ch not in "0123456789abcdefABCDEF" for ch in digits):
                raise V2BError("malformed Lean \\u char escape")
            index += 6
        else:
            raise V2BError("invalid Lean char escape")
    if index >= n or text[index] != "'":
        raise V2BError("malformed lean char literal")
    return index + 1


def lex_python(text):
    """Layout-preserving typed records for one top-level Python unit."""
    if not isinstance(text, str) or not text:
        raise V2BError("python unit text must be a non-empty string")
    if "\r" in text:
        raise V2BError("python unit contains CR — LF-only corpora expected")
    records = []
    fstring_kinds = {getattr(tokenize, name, None): name
                     for name in ("FSTRING_START", "FSTRING_MIDDLE",
                                  "FSTRING_END")}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL,
                            tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            if tok.type == tokenize.NEWLINE:
                records.append(("NEWLINE", ""))
            elif tok.type == tokenize.INDENT:
                records.append(("INDENT", tok.string))
            elif tok.type == tokenize.DEDENT:
                records.append(("DEDENT", ""))
            elif tok.type == tokenize.NAME:
                records.append(("NAME", tok.string))
            elif tok.type == tokenize.OP:
                records.append(("OP", tok.string))
            elif tok.type == tokenize.NUMBER:
                records.append(("NUMBER", tok.string))
            elif tok.type == tokenize.STRING:
                records.append(("STRING", tok.string))
            elif tok.type in fstring_kinds and fstring_kinds[tok.type]:
                records.append((fstring_kinds[tok.type], tok.string))
            else:
                raise V2BError(
                    f"unexpected python token {tokenize.tok_name[tok.type]}")
    except tokenize.TokenizeError as err:
        raise V2BError(f"python unit does not tokenize: {err}") from err
    except (IndentationError, SyntaxError) as err:
        raise V2BError(f"python unit does not tokenize: {err}") from err
    if not records:
        raise V2BError("python unit produced no tokens")
    return records


def _line_table(text):
    line_of = []
    leading = []
    start = 0
    lines = text.split("\n")
    for line in lines:
        n = 0
        while n < len(line) and line[n] in " \t":
            n += 1
        leading.append(line[:n])
    line_no = 0
    for ch in text:
        line_of.append(line_no)
        if ch == "\n":
            line_no += 1
    return line_of, leading


def lex_lean(text):
    """Single-pass sequential Lean lexer per §15.A6/§15.A12.

    Comments (nested block, line) are skipped by THIS lexer — never by a
    mask that also hides string contents (the earlier code_mask-based
    form silently dropped every string literal, merging units that
    differ only in literal text). Ordinary strings, raw strings
    (r#*"..."#*), and char literals are each ONE typed record carrying
    the verbatim source text, so comment markers inside literals survive
    and multiline literal contents can never manufacture interior LAYOUT
    sentinels. FROZEN LAYOUT READING: a sentinel is emitted when a token
    STARTS on a later physical line than the PREVIOUS token's START
    line, carrying the new line's exact leading horizontal whitespace.
    Unterminated comments/literals and CR bytes fail closed."""
    if not isinstance(text, str) or not text:
        raise V2BError("lean unit text must be a non-empty string")
    if "\r" in text:
        raise V2BError("lean unit contains CR — LF-only corpora expected")
    line_of, leading = _line_table(text)
    records = []
    state = dict(previous_line=None)
    n = len(text)

    def emit(kind, value, start):
        token_line = line_of[start]
        if state["previous_line"] is not None \
                and token_line > state["previous_line"]:
            records.append((LEAN_SENTINEL, leading[token_line]))
        state["previous_line"] = token_line
        records.append((kind, value))

    i = 0
    while i < n:
        ch = text[i]
        if ch in " \t\n":
            i += 1
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if text.startswith("/-", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/-", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("-/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise V2BError("unterminated lean block comment")
            i = j
            continue
        start = i
        if ch == "«":
            j = text.find("»", i + 1)
            if j < 0:
                raise V2BError("unterminated « quoted identifier")
            emit("IDENT", text[i:j + 1], start)
            i = j + 1
            continue
        if ch == "r":
            j = i + 1
            hashes = 0
            while j < n and text[j] == "#":
                hashes, j = hashes + 1, j + 1
            if j < n and text[j] == '"':
                closer = '"' + "#" * hashes
                k = text.find(closer, j + 1)
                if k < 0:
                    raise V2BError("unterminated lean raw string literal")
                end = k + len(closer)
                emit("STR", text[i:end], start)
                i = end
                continue
            # not a raw string: fall through to identifier scanning
        if _lean_id_first(ch):
            j = i + 1
            while j < n and _lean_id_rest(text[j]):
                j += 1
            emit("IDENT", text[i:j], start)
            i = j
            continue
        if ch.isdigit() and ch.isascii():
            j = _scan_lean_number(text, i)
            emit("NUM", text[i:j], start)
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if j >= n:
                raise V2BError("unterminated lean string literal")
            emit("STR", text[i:j + 1], start)
            i = j + 1
            continue
        if ch == "'":
            # Lean invokes the char parser only if the next character is not
            # another apostrophe; otherwise apostrophe is ordinary syntax.
            if i + 1 < n and text[i + 1] == "'":
                j = i
                while j < n and text[j] == "'":
                    emit("OP", "'", j)
                    j += 1
                i = j
                continue
            j = _scan_lean_char(text, i)
            emit("CHAR", text[i:j], start)
            i = j
            continue
        emit("OP", ch, start)
        i += 1
    if not records:
        raise V2BError("lean unit produced no tokens")
    return records


def lex_unit(language, text):
    if language == "python":
        return lex_python(text)
    if language == "lean":
        return lex_lean(text)
    raise V2BError(f"no A6 lexer for language {language!r}")


# ------------------------------------------------------------- hashes

def lean_keyword_provenance_hash(provenance):
    """Order-stable hash projection for nested keyword provenance dicts."""
    return sha256_json([
        [row["token"], [[source["repo"],
                         source["reserved_token_table"],
                         source["parser_dispatch"]]
                        for source in row["sources"]]]
        for row in provenance])


def load_lean_keyword_freeze(path):
    """Load and revalidate the write-once parser-token union used by A6."""
    binding, freeze = artifact_binding(path, LEAN_KEYWORD_FREEZE_SCHEMA)
    tokens = freeze.get("tokens")
    sources = freeze.get("source_tables")
    provenance = freeze.get("token_provenance")
    if not isinstance(tokens, list) or not tokens \
            or tokens != sorted(tokens) or len(tokens) != len(set(tokens)) \
            or freeze.get("n_tokens") != len(tokens) \
            or freeze.get("tokens_sha256") != sha256_json(tokens) \
            or not all(lean_identifier_spelling(token) for token in tokens):
        raise V2BError("Lean keyword freeze token list/hash is malformed")
    if not isinstance(sources, list) or len(sources) != 3 \
            or any(not isinstance(source, dict) for source in sources) \
            or {source.get("repo") for source in sources} != \
            {"mathlib4", "batteries", "physlib"}:
        raise V2BError("Lean keyword freeze lacks exact source-table evidence")
    if [source.get("repo") for source in sources] != \
            sorted(source.get("repo") for source in sources) \
            or any(not isinstance(source.get("n_excluded_dispatch_keys"), int)
                   or isinstance(source.get("n_excluded_dispatch_keys"), bool)
                   or source["n_excluded_dispatch_keys"] <= 0
                   for source in sources) \
            or freeze.get("n_excluded_dispatch_keys_total") != sum(
                source["n_excluded_dispatch_keys"] for source in sources):
        raise V2BError("Lean keyword freeze dispatch evidence is malformed")
    if not isinstance(provenance, list) or len(provenance) != len(tokens) \
            or [row.get("token") if isinstance(row, dict) else None
                for row in provenance] != tokens:
        raise V2BError("Lean keyword freeze token provenance is malformed")
    allowed_repos = {"mathlib4", "batteries", "physlib"}
    for row in provenance:
        evidence = row.get("sources")
        if set(row) != {"token", "sources"} \
                or not isinstance(evidence, list) or not evidence \
                or any(not isinstance(source, dict)
                       or not isinstance(source.get("repo"), str)
                       for source in evidence):
            raise V2BError("Lean keyword token provenance row is malformed")
        repos = [source["repo"] for source in evidence]
        if repos != sorted(repos) or len(repos) != len(set(repos)):
            raise V2BError("Lean keyword token source order is malformed")
        seen = set()
        for source in evidence:
            repo = source.get("repo")
            if set(source) != {"repo", "reserved_token_table",
                               "parser_dispatch"} \
                    or repo not in allowed_repos or repo in seen \
                    or not isinstance(source.get("reserved_token_table"), bool) \
                    or not isinstance(source.get("parser_dispatch"), bool) \
                    or not (source["reserved_token_table"] \
                            or source["parser_dispatch"]):
                raise V2BError("Lean keyword token source is malformed")
            seen.add(repo)
    if freeze.get("token_provenance_sha256") != \
            lean_keyword_provenance_hash(provenance):
        raise V2BError("Lean keyword freeze provenance hash is malformed")
    binding.update(n_tokens=len(tokens),
                   tokens_sha256=freeze["tokens_sha256"],
                   source_repos=sorted(source["repo"] for source in sources))
    return frozenset(tokens), binding


def _validated_lean_keywords(lean_keywords):
    if not isinstance(lean_keywords, (set, frozenset, list, tuple)) \
            or not lean_keywords:
        raise V2BError("Lean normalization requires a nonempty keyword freeze")
    keywords = frozenset(lean_keywords)
    if len(keywords) != len(lean_keywords) \
            or not all(lean_identifier_spelling(token) for token in keywords):
        raise V2BError("Lean normalization keyword freeze is malformed")
    return keywords


def python_keyword_evidence():
    tokens = sorted(PYTHON_KEYWORDS)
    return dict(schema="python_stdlib_keywords_v1",
                n_tokens=len(tokens), tokens_sha256=sha256_json(tokens),
                includes_soft_keywords=True)

def _validated_records(records):
    if not isinstance(records, (list, tuple)) or not records:
        raise V2BError("token records must be a non-empty sequence")
    out = []
    for index, record in enumerate(records):
        if not isinstance(record, (list, tuple)) or len(record) != 2 \
                or not isinstance(record[0], str) or not record[0] \
                or not isinstance(record[1], (str, int)) \
                or isinstance(record[1], bool):
            raise V2BError(f"invalid typed token record[{index}] {record!r}")
        out.append((record[0], record[1]))
    return out


def lexical_records(records):
    return [record for record in _validated_records(records)
            if record[0] not in SENTINEL_KINDS]


def verbatim_hash(records):
    rows = [[kind, value] for kind, value in _validated_records(records)]
    return sha256_bytes(canonical_json_bytes(rows))


def normalized_hash(records, language, lean_keywords=None):
    if language == "python":
        if lean_keywords is not None:
            raise V2BError("Python normalization received Lean keywords")
        id_kind, keywords = "NAME", PYTHON_KEYWORDS
    elif language == "lean":
        id_kind, keywords = "IDENT", _validated_lean_keywords(lean_keywords)
    else:
        raise V2BError(f"no normalization rules for language {language!r}")
    ranks = {}
    rows = []
    for kind, value in _validated_records(records):
        if kind == id_kind and value not in keywords:
            if value not in ranks:
                ranks[value] = len(ranks)
            rows.append(["IDRANK", ranks[value]])
        else:
            rows.append([kind, value])
    return sha256_bytes(canonical_json_bytes(rows))


def five_grams(records):
    lexical = lexical_records(records)
    return frozenset(tuple(lexical[i:i + 5])
                     for i in range(len(lexical) - 4))


# ------------------------------------------------- exact Jaccard pairs

def meets(intersection, union, threshold):
    num, den = threshold
    return den * intersection >= num * union


def jaccard_bin(intersection, union):
    for name, low, high in BIN_EDGES:
        if meets(intersection, union, low) \
                and (high is None
                     or not meets(intersection, union, high)):
            return name
    return None


def _pair_stats(a_grams, b_grams):
    intersection = len(a_grams & b_grams)
    union = len(a_grams | b_grams)
    return intersection, union


def _eligible_units(units):
    seen = set()
    rows = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise V2BError(f"jaccard unit[{index}] is not an object")
        key = unit.get("key")
        grams = unit.get("grams")
        count = unit.get("n_lexical_records")
        if not isinstance(key, str) or not key or key in seen:
            raise V2BError(f"missing/duplicate jaccard unit key {key!r}")
        seen.add(key)
        if not isinstance(grams, frozenset):
            raise V2BError(f"unit {key}: grams must be a frozenset")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise V2BError(f"unit {key}: invalid lexical record count")
        if count >= LEXICAL_FLOOR and grams:
            rows.append((key, grams))
    return rows


def brute_force_pairs(units, threshold=JACCARD_T):
    rows = _eligible_units(units)
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            inter, union = _pair_stats(rows[i][1], rows[j][1])
            if union and meets(inter, union, threshold):
                a, b = sorted((rows[i][0], rows[j][0]))
                out.append(dict(a=a, b=b, intersection=inter, union=union))
    out.sort(key=lambda row: (row["a"], row["b"]))
    return out


def candidate_pairs(units, threshold=JACCARD_T):
    """Exact size+prefix filtered pairs — REQUIRED equal to brute force."""
    rows = _eligible_units(units)
    num, den = threshold
    df = Counter()
    for _, grams in rows:
        df.update(grams)
    gram_sha = {}

    def gram_key(gram):
        digest = gram_sha.get(gram)
        if digest is None:
            digest = sha256_bytes(canonical_json_bytes(
                [list(record) for record in gram]))
            gram_sha[gram] = digest
        return (df[gram], digest)

    sizes = {key: len(grams) for key, grams in rows}
    index_by_gram = {}
    for key, grams in rows:
        s = sizes[key]
        ceil_ts = (num * s + den - 1) // den
        prefix_len = s - ceil_ts + 1
        prefix = sorted(grams, key=gram_key)[:prefix_len]
        for gram in prefix:
            index_by_gram.setdefault(gram, []).append(key)

    grams_of = dict(rows)
    candidates = set()
    for gram, keys in index_by_gram.items():
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = sorted((keys[i], keys[j]))
                small, large = sorted((sizes[a], sizes[b]))
                if den * small >= num * large:
                    candidates.add((a, b))
    out = []
    for a, b in candidates:
        inter, union = _pair_stats(grams_of[a], grams_of[b])
        if union and meets(inter, union, threshold):
            out.append(dict(a=a, b=b, intersection=inter, union=union))
    out.sort(key=lambda row: (row["a"], row["b"]))
    return out


# --------------------------------------------- collision groups + packs

def collision_groups(units, language, repo):
    """§15.A6 collision groups within one corpus, band by the group's
    normalized token count (the FROZEN literal: full normalized record
    count, layout sentinels included)."""
    by_normalized = {}
    for unit in units:
        by_normalized.setdefault(unit["normalized_sha256"], []).append(unit)
    groups = []
    for normalized, members in sorted(by_normalized.items()):
        verbatims = {m["verbatim_sha256"] for m in members}
        if len(verbatims) < 2:
            continue
        counts = {m["n_records"] for m in members}
        if len(counts) != 1:
            raise V2BError(f"collision group {normalized[:12]} members "
                           "disagree on normalized record count")
        count = counts.pop()
        members = sorted(members, key=lambda m: identity_key(
            language, m["identity"]))
        groups.append(dict(
            normalized_sha256=normalized, repo=repo,
            band="under20" if count < LEXICAL_FLOOR else "geq20",
            n_records=count, n_members=len(members),
            n_distinct_verbatim=len(verbatims),
            members=[dict(identity=list(m["identity"]),
                          verbatim_sha256=m["verbatim_sha256"])
                     for m in members]))
    return groups


def seeded_member_pair(repo, group):
    """§15.A11 O(m log m) member rule: rank members by the frozen seeded
    hash; pair = first member + first later member whose verbatim-token
    hash differs (exists by group definition)."""
    ranked = sorted(
        group["members"],
        key=lambda m: seeded_hash("a6hashmember:v2b:20260808", repo,
                                  group["normalized_sha256"],
                                  *m["identity"]))
    first = ranked[0]
    for rank, member in enumerate(ranked[1:], 1):
        if member["verbatim_sha256"] != first["verbatim_sha256"]:
            return dict(left=dict(rank=0, **first),
                        right=dict(rank=rank, **member))
    raise V2BError("collision group has no distinct-verbatim member pair")


def _round_robin(items_by_repo, cap):
    repos = sorted(items_by_repo)
    taken = []
    cursor = {repo: 0 for repo in repos}
    while len(taken) < cap:
        progressed = False
        for repo in repos:
            if len(taken) >= cap:
                break
            rows = items_by_repo[repo]
            if cursor[repo] < len(rows):
                taken.append(rows[cursor[repo]])
                cursor[repo] += 1
                progressed = True
        if not progressed:
            break
    return taken


def build_collision_pack(groups_by_repo, language, cap=8):
    """Deterministic per-(language, band) blind audit packet."""
    pack = {}
    for band in ("under20", "geq20"):
        per_repo = {}
        for repo, groups in groups_by_repo.items():
            rows = [g for g in groups if g["band"] == band]
            rows.sort(key=lambda g: seeded_hash(
                "a6hashgrp:v2b:20260808", repo, g["normalized_sha256"]))
            per_repo[repo] = rows
        chosen = _round_robin(per_repo, cap)
        entries = []
        for group in chosen:
            pair = seeded_member_pair(group["repo"], group)
            entries.append(dict(repo=group["repo"],
                                normalized_sha256=group["normalized_sha256"],
                                band=band, pair=pair))
        entries.sort(key=lambda e: seeded_hash(
            "a6hashshow:v2b:20260808", e["repo"], e["normalized_sha256"]))
        pack[band] = dict(language=language, band=band, cap=cap,
                          n_available=sum(len(r) for r in per_repo.values()),
                          n_selected=len(entries),
                          underfilled=len(entries) < cap, entries=entries)
    return pack


def _sorted_pair_identities(pair, language):
    """§15 global convention: identities spliced FLAT into seed keys,
    the pair ordered by canonical-JSON encoding — never key strings."""
    a = list(validate_identity(language, pair.get("a_identity")))
    b = list(validate_identity(language, pair.get("b_identity")))
    if pair.get("a") != identity_key(language, a) \
            or pair.get("b") != identity_key(language, b) \
            or pair["a"] >= pair["b"]:
        raise V2BError(f"calibration pair key/identity drift: {pair!r}")
    first, second = sorted((a, b),
                           key=canonical_json_bytes)
    return [*first, *second]


def build_calibration_pack(pairs_by_repo, language, cap=8):
    """Deterministic per-(language, bin) Jaccard calibration packet."""
    pack = {}
    for name, low, high in BIN_EDGES:
        per_repo = {}
        for repo, pairs in pairs_by_repo.items():
            rows = []
            for pair in pairs:
                bin_name = jaccard_bin(pair["intersection"], pair["union"])
                if bin_name == name:
                    rows.append(dict(pair, bin=name, repo=repo))
            rows.sort(key=lambda p: seeded_hash(
                "a6cal:v2b:20260808", repo,
                *_sorted_pair_identities(p, language)))
            per_repo[repo] = rows
        chosen = _round_robin(per_repo, cap)
        chosen.sort(key=lambda p: seeded_hash(
            "a6calshow:v2b:20260808", p["repo"],
            *_sorted_pair_identities(p, language)))
        pack[name] = dict(language=language, bin=name, cap=cap,
                          n_available=sum(len(r) for r in per_repo.values()),
                          n_selected=len(chosen),
                          underfilled=len(chosen) < cap, entries=chosen)
    return pack


# ------------------------------------------------- mechanical outcomes

def _require_label(row, allowed):
    label = row.get("label")
    if label not in allowed:
        raise V2BError(f"invalid audit label {label!r}")
    return label


def _packet_section(section, section_name, language, entry_count):
    if not isinstance(section, dict) or section.get("language") != language \
            or section.get("cap") != 8 \
            or section.get("n_selected") != entry_count:
        raise V2BError(f"malformed audit packet section {section_name}")
    available = section.get("n_available")
    if not isinstance(available, int) or isinstance(available, bool) \
            or available < entry_count \
            or entry_count != min(available, 8) \
            or section.get("underfilled") is not (available < 8):
        raise V2BError(f"audit packet counts drift in {section_name}")


def _validate_calibration_pack(calibration_pack):
    expected = {name for name, _, _ in BIN_EDGES}
    if not isinstance(calibration_pack, dict) \
            or set(calibration_pack) != expected:
        raise V2BError("calibration packet must contain exactly B1-B5")
    language = calibration_pack["B1"].get("language") \
        if isinstance(calibration_pack["B1"], dict) else None
    if language not in ("lean", "python"):
        raise V2BError("calibration packet lacks one supported language")
    entries = {}
    for name, _, _ in BIN_EDGES:
        section = calibration_pack[name]
        rows_in_bin = section.get("entries") \
            if isinstance(section, dict) else None
        if not isinstance(rows_in_bin, list) or len(rows_in_bin) > 8 \
                or section.get("bin") != name:
            raise V2BError(f"malformed calibration bin {name}")
        _packet_section(section, name, language, len(rows_in_bin))
        for entry in rows_in_bin:
            if not isinstance(entry, dict) or entry.get("bin") != name:
                raise V2BError(f"malformed calibration entry in {name}")
            repo = entry.get("repo")
            if not isinstance(repo, str) or not repo:
                raise V2BError("calibration entry lacks repo")
            _sorted_pair_identities(entry, language)
            inter, union = entry.get("intersection"), entry.get("union")
            if not isinstance(inter, int) or not isinstance(union, int) \
                    or isinstance(inter, bool) or isinstance(union, bool) \
                    or not 0 <= inter <= union or union <= 0 \
                    or not meets(inter, union, JACCARD_T) \
                    or jaccard_bin(inter, union) != name:
                raise V2BError(f"invalid calibration pair stats {entry!r}")
            key = (repo, entry["a"], entry["b"])
            if key in entries:
                raise V2BError(f"duplicate calibration entry {key!r}")
            entries[key] = entry
    return language, entries


def _validate_collision_pack(collision_pack):
    if not isinstance(collision_pack, dict) \
            or set(collision_pack) != {"under20", "geq20"}:
        raise V2BError("collision packet must contain exactly two bands")
    language = collision_pack["under20"].get("language") \
        if isinstance(collision_pack["under20"], dict) else None
    if language not in ("lean", "python"):
        raise V2BError("collision packet lacks one supported language")
    entries = {}
    for band in ("under20", "geq20"):
        section = collision_pack[band]
        rows_in_band = section.get("entries") \
            if isinstance(section, dict) else None
        if not isinstance(rows_in_band, list) or len(rows_in_band) > 8 \
                or section.get("band") != band:
            raise V2BError(f"malformed collision band {band}")
        _packet_section(section, band, language, len(rows_in_band))
        for entry in rows_in_band:
            if not isinstance(entry, dict) or entry.get("band") != band:
                raise V2BError(f"malformed collision entry in {band}")
            repo, normalized = (entry.get("repo"),
                                entry.get("normalized_sha256"))
            if not isinstance(repo, str) or not repo \
                    or not isinstance(normalized, str) \
                    or len(normalized) != 64 \
                    or any(ch not in "0123456789abcdef" for ch in normalized):
                raise V2BError("collision entry identity is malformed")
            pair = entry.get("pair")
            if not isinstance(pair, dict):
                raise V2BError("collision entry lacks member pair")
            members = []
            for side in ("left", "right"):
                member = pair.get(side)
                if not isinstance(member, dict):
                    raise V2BError("collision member is malformed")
                validate_identity(language, member.get("identity"))
                digest, rank = (member.get("verbatim_sha256"),
                                member.get("rank"))
                if not isinstance(digest, str) or len(digest) != 64 \
                        or any(ch not in "0123456789abcdef" for ch in digest) \
                        or not isinstance(rank, int) or isinstance(rank, bool) \
                        or rank < 0:
                    raise V2BError("collision member provenance is malformed")
                members.append(member)
            if members[0]["rank"] != 0 or members[1]["rank"] < 1 \
                    or members[0]["verbatim_sha256"] == \
                    members[1]["verbatim_sha256"]:
                raise V2BError("collision seeded member pair is invalid")
            key = (band, repo, normalized)
            if key in entries:
                raise V2BError(f"duplicate collision entry {key!r}")
            entries[key] = entry
    return language, entries


def jaccard_outcome(calibration_pack, labels):
    """§15.A6 frozen label -> threshold mapping for ONE language.

    PACKET-BOUND (hardening): every label must correspond 1:1 to a
    packet entry — labels for pairs never in the packet, duplicate
    labels, missing labels, or a bin exceeding the frozen cap all fail
    closed, so over-supplied or forged label sets can never reach the
    mapping. Pair statistics come from the PACKET, never the label row."""
    _, entries = _validate_calibration_pack(calibration_pack)
    if not isinstance(labels, list):
        raise V2BError("calibration labels must be a list")
    label_of = {}
    for row in labels:
        key = (row.get("repo"), row.get("a"), row.get("b"))
        if key not in entries:
            raise V2BError(f"label for a pair outside the packet: {key!r}")
        if key in label_of:
            raise V2BError(f"duplicate label for packet entry {key!r}")
        label_of[key] = _require_label(row, ("duplicate", "not-duplicate"))
    missing = sorted(set(entries) - set(label_of))
    if missing:
        raise V2BError(f"unlabeled packet entries remain: {missing[:3]!r}")
    rows = []
    for key, entry in entries.items():
        inter, union = entry["intersection"], entry["union"]
        rows.append(dict(intersection=inter, union=union,
                         label=label_of[key],
                         bin=jaccard_bin(inter, union)))

    def dup_frac(subset):
        total = len(subset)
        dups = sum(1 for r in subset if r["label"] == "duplicate")
        return dups, total

    def majority_dup(subset):
        dups, total = dup_frac(subset)
        return 2 * dups >= total if total else None    # None = vacuous

    def precision_at(threshold):
        subset = [r for r in rows
                  if meets(r["intersection"], r["union"], threshold)]
        dups, total = dup_frac(subset)
        return dups, total

    b1 = [r for r in rows if r["bin"] == "B1"]
    mid = [r for r in rows if r["bin"] in ("B2", "B3", "B4")]
    d1 = majority_dup(b1)
    dmid = majority_dup(mid)
    vacuous = dict(B1=d1 is None, B2uB3uB4=dmid is None)
    dups80, n80 = precision_at(SENSITIVITY_T["0.80"])
    dups90, n90 = precision_at(SENSITIVITY_T["0.90"])
    dups70, n70 = precision_at(SENSITIVITY_T["0.70"])
    detail = dict(n_labeled=len(rows), vacuous_bins=vacuous,
                  n_at_080=n80, n_at_090=n90, n_at_070=n70)
    if n80 < 8:
        return dict(outcome="lexical-inconclusive",
                    reason="insufficient labeled pairs at J>=0.80", **detail)
    b1_nonmajority = (d1 is None) or (not d1)
    if 10 * dups80 >= 9 * n80 and b1_nonmajority:
        return dict(outcome="0.80", reason="rule-1", **detail)
    mid_nonmajority = (dmid is None) or (not dmid)
    if n90 and 10 * dups90 >= 9 * n90 and b1_nonmajority \
            and mid_nonmajority:
        return dict(outcome="0.90", reason="rule-2", **detail)
    if d1 is True and 10 * dups70 >= 9 * n70:
        return dict(outcome="0.70", reason="rule-3", **detail)
    return dict(outcome="lexical-inconclusive", reason="rule-4", **detail)


def collision_activation(collision_pack, labels):
    """§15.A12 8/8 gate per (language x band).

    PACKET-BOUND (hardening): labels must correspond 1:1 to packet
    entries. More labels than the frozen 8-cap packet can hold — the
    former 9/9 acceptance — is a protocol violation and fails closed.
    active iff EXACTLY 8 labeled entries and ALL 8 are clones; any
    underfill or one false positive keeps the band sensitivity-only."""
    _, entries = _validate_collision_pack(collision_pack)
    if not isinstance(labels, list):
        raise V2BError("collision labels must be a list")
    label_of = {}
    for row in labels:
        key = (row.get("band"), row.get("repo"),
               row.get("normalized_sha256"))
        if key not in entries:
            raise V2BError(f"label for a group outside the packet: {key!r}")
        if key in label_of:
            raise V2BError(f"duplicate label for packet entry {key!r}")
        label_of[key] = _require_label(row, ("clone", "not-clone"))
    missing = sorted(set(entries) - set(label_of))
    if missing:
        raise V2BError(f"unlabeled packet entries remain: {missing[:3]!r}")
    out = {}
    for band in ("under20", "geq20"):
        band_labels = [label for (b, _, _), label in label_of.items()
                       if b == band]
        n = len(band_labels)
        n_clones = sum(1 for label in band_labels if label == "clone")
        out[band] = dict(n_labeled=n, n_clones=n_clones,
                         active=(n == 8 and n_clones == 8))
    return out


# ------------------------------------------------------------ artifact

def build_neardup_artifact(extraction_path, repo, lean_keywords=None,
                           lean_keyword_evidence=None):
    """Per-corpus near-dup table over the FULL spanned-unit universe."""
    binding, extraction = artifact_binding(extraction_path)
    schema = extraction.get("schema")
    if schema == LEAN_EXTRACT_SCHEMA:
        language = "lean"
    elif schema == PYTHON_EXTRACT_SCHEMA:
        language = "python"
    else:
        raise V2BError(f"unsupported extraction schema {schema!r}")
    if extraction.get("repo") != repo:
        raise V2BError(f"extraction repo {extraction.get('repo')!r} != "
                       f"{repo!r}")
    if language == "lean":
        lean_keywords = _validated_lean_keywords(lean_keywords)
        if not isinstance(lean_keyword_evidence, dict) \
                or lean_keyword_evidence.get("schema") != \
                LEAN_KEYWORD_FREEZE_SCHEMA \
                or lean_keyword_evidence.get("n_tokens") != \
                len(lean_keywords) \
                or lean_keyword_evidence.get("tokens_sha256") != \
                sha256_json(sorted(lean_keywords)):
            raise V2BError("Lean keyword freeze evidence is missing or drifted")
        keyword_evidence = dict(lean_keyword_evidence)
    else:
        if lean_keywords is not None or lean_keyword_evidence is not None:
            raise V2BError("Python A6 received a Lean keyword freeze")
        keyword_evidence = python_keyword_evidence()
    units = []
    for f in extraction.get("files", []):
        source = f.get("source")
        recorded = f.get("source_sha256")
        if sha256_file(source) != recorded:
            raise V2BError(f"{f.get('module')}: live source hash drift")
        blob = open(source, "rb").read()
        if language == "lean":
            spans = [((f["module"], name), d["start_byte"], d["end_byte"])
                     for name, d in f.get("decls", {}).items()]
        else:
            spans = [(tuple(t["identity"]), t["start_byte"], t["end_byte"])
                     for t in f.get("targets", [])]
        for identity, start, end in spans:
            identity = validate_identity(language, identity)
            if not 0 <= start < end <= len(blob):
                raise V2BError(f"unit span outside source: {identity!r}")
            try:
                text = blob[start:end].decode("utf-8")
            except UnicodeDecodeError as err:
                raise V2BError(
                    f"unit span splits UTF-8: {identity!r}: {err}") from err
            records = lex_unit(language, text)
            lexical = lexical_records(records)
            units.append(dict(
                identity=list(identity),
                key=identity_key(language, identity),
                verbatim_sha256=verbatim_hash(records),
                normalized_sha256=normalized_hash(
                    records, language, lean_keywords=lean_keywords),
                n_records=len(records),
                n_lexical_records=len(lexical),
                under_floor=len(lexical) < LEXICAL_FLOOR,
                grams=five_grams(records)))
    if not units:
        raise V2BError("extraction has no spanned units")
    units.sort(key=lambda u: u["key"])
    pairs = candidate_pairs(units)
    identity_of = {u["key"]: u["identity"] for u in units}
    for pair in pairs:
        pair["a_identity"] = identity_of[pair["a"]]
        pair["b_identity"] = identity_of[pair["b"]]
    groups = collision_groups(units, language, repo)
    unit_rows = [dict((k, v) for k, v in unit.items() if k != "grams")
                 for unit in units]
    return dict(schema=NEARDUP_SCHEMA, repo=repo, language=language,
                extraction=binding, lexer_citation=LEXER_CITATION,
                keyword_evidence=keyword_evidence,
                lexical_floor=LEXICAL_FLOOR,
                jaccard_threshold="7/10",
                n_units=len(unit_rows),
                n_under_floor=sum(u["under_floor"] for u in unit_rows),
                units=unit_rows, jaccard_pairs=pairs,
                collision_groups=groups)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--lean-keyword-freeze")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lean_keywords = keyword_evidence = None
    if args.lean_keyword_freeze:
        lean_keywords, keyword_evidence = load_lean_keyword_freeze(
            args.lean_keyword_freeze)
    artifact = build_neardup_artifact(
        args.extraction, args.repo, lean_keywords, keyword_evidence)
    digest = write_new_json(args.out, artifact)
    print(f"[v2b-neardup] {args.repo}: {artifact['n_units']} units, "
          f"{len(artifact['jaccard_pairs'])} pairs, "
          f"{len(artifact['collision_groups'])} collision groups -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
