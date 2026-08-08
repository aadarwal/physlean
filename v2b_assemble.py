#!/usr/bin/env python3
"""Pure context ordering/rendering primitives for the V2-b assembler.

This file intentionally contains no model code and performs no study-corpus
sampling.  It implements the byte- and graph-level invariants in DESIGN_V2
§15.A4-A5 so they can be property-tested before any pilot artifact exists.
"""
import heapq
import math
from collections import Counter, deque

from v2b_common import (V2BError, canonical_json_bytes, seeded_hash,
                        validate_identity)


def normalize_payload(payload):
    """Return a payload with exactly one terminal LF plus audit counts."""
    if not isinstance(payload, bytes):
        raise V2BError("payload must be bytes")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"payload is not UTF-8: {err}") from err
    trailing = len(payload) - len(payload.rstrip(b"\n"))
    if trailing == 0:
        normalized = payload + b"\n"
        removed, appended = 0, 1
    else:
        normalized = payload[:len(payload) - trailing + 1]
        removed, appended = trailing - 1, 0
    return normalized, dict(n_removed_terminal_lf=removed,
                            n_appended_terminal_lf=appended)


def _comment_leader(language):
    if language == "lean":
        return "--"
    if language == "python":
        return "#"
    if language == "cpp":
        return "//"
    raise V2BError(f"no banner syntax for language {language!r}")


def banner_line(language, relpath):
    if not isinstance(relpath, str) or not relpath or relpath.startswith("/"):
        raise V2BError(f"banner path is not repo-relative: {relpath!r}")
    if "\n" in relpath or "\r" in relpath:
        raise V2BError("banner path contains a newline")
    return f"{_comment_leader(language)} ctx: {relpath}".encode("utf-8")


def make_chunk(language, relpath, payload):
    normalized, normalization = normalize_payload(payload)
    banner = banner_line(language, relpath)
    chunk = banner + b"\n" + normalized
    return chunk, dict(banner_bytes=len(banner) + 1,
                       payload_input_bytes=len(payload),
                       payload_rendered_bytes=len(normalized),
                       **normalization)


def render_chunks(language, units):
    """Render ordered units and assign every byte to one contiguous span.

    Each unit is an object with ``identity``, ``relpath``, and byte ``payload``.
    The extra LF between chunks belongs to the preceding (farther) unit.
    §15.A11's final context-to-query separator belongs to the last unit.
    """
    if not isinstance(units, list):
        raise V2BError("ordered units must be a list")
    rendered = bytearray()
    spans = []
    seen = set()
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise V2BError(f"unit[{index}] is not an object")
        identity = tuple(unit.get("identity", ()))
        if not identity or identity in seen:
            raise V2BError(f"missing/duplicate rendered identity {identity!r}")
        seen.add(identity)
        chunk, audit = make_chunk(language, unit.get("relpath"),
                                  unit.get("payload"))
        start = len(rendered)
        rendered.extend(chunk)
        join_bytes = 0
        separator_bytes = 0
        if index + 1 < len(units):
            rendered.extend(b"\n")
            join_bytes = 1
        else:
            rendered.extend(b"\n")
            separator_bytes = 1
        end = len(rendered)
        spans.append(dict(identity=list(identity), start_byte=start,
                          end_byte=end, chunk_bytes=len(chunk),
                          join_bytes=join_bytes,
                          separator_bytes=separator_bytes, **audit))
    if spans:
        if spans[0]["start_byte"] != 0 \
                or spans[-1]["end_byte"] != len(rendered):
            raise AssertionError("render span boundary mismatch")
        for left, right in zip(spans, spans[1:]):
            if left["end_byte"] != right["start_byte"]:
                raise AssertionError("render spans do not partition bytes")
    return bytes(rendered), spans


def splice_local_prefix(source, target_start_byte, excluded_spans):
    """Build §15.A11's raw, banner-free k2 core plus final separator.

    Exclusions must be wholly earlier than the target. Overlapping or
    adjacent exclusions are merged once; retained source intervals are
    concatenated without normalization or invented splice markers.
    """
    if not isinstance(source, bytes):
        raise V2BError("k2 source must be bytes")
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"k2 source is not UTF-8: {err}") from err
    if not isinstance(target_start_byte, int) \
            or isinstance(target_start_byte, bool) \
            or not 0 <= target_start_byte <= len(source):
        raise V2BError(f"invalid k2 target start {target_start_byte!r}")
    try:
        source[:target_start_byte].decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"k2 target start splits UTF-8: {err}") from err
    intervals = []
    for index, row in enumerate(excluded_spans):
        if not isinstance(row, dict):
            raise V2BError(f"k2 exclusion[{index}] is not an object")
        start, end = row.get("start_byte"), row.get("end_byte")
        if not isinstance(start, int) or isinstance(start, bool) \
                or not isinstance(end, int) or isinstance(end, bool) \
                or not 0 <= start < end <= target_start_byte:
            raise V2BError(f"k2 exclusion outside prefix: {row!r}")
        try:
            source[:start].decode("utf-8")
            source[:end].decode("utf-8")
        except UnicodeDecodeError as err:
            raise V2BError(f"k2 exclusion splits UTF-8: {err}") from err
        intervals.append((start, end, row.get("identity")))
    intervals.sort(key=lambda row: (row[0], row[1], repr(row[2])))
    merged = []
    for start, end, identity in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end, [identity]])
        else:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2].append(identity)

    pieces = []
    retained = []
    cursor = output_pos = 0
    for start, end, identities in merged:
        if cursor < start:
            piece = source[cursor:start]
            pieces.append(piece)
            retained.append(dict(source_start_byte=cursor,
                                 source_end_byte=start,
                                 output_start_byte=output_pos,
                                 output_end_byte=output_pos + len(piece)))
            output_pos += len(piece)
        cursor = end
    if cursor < target_start_byte:
        piece = source[cursor:target_start_byte]
        pieces.append(piece)
        retained.append(dict(source_start_byte=cursor,
                             source_end_byte=target_start_byte,
                             output_start_byte=output_pos,
                             output_end_byte=output_pos + len(piece)))
        output_pos += len(piece)
    core = b"".join(pieces)
    rendering = core + (b"\n" if core else b"")
    spans = ([dict(identity=["k2-local-prefix"], start_byte=0,
                   end_byte=len(rendering), chunk_bytes=len(core),
                   join_bytes=0, separator_bytes=1)] if core else [])
    return dict(core=core, rendering=rendering, spans=spans,
                separator_bytes=1 if core else 0,
                merged_exclusions=[
                    dict(start_byte=start, end_byte=end,
                         identities=identities)
                    for start, end, identities in merged],
                retained_intervals=retained)


def utf8_budget_suffix(rendering, spans, budget):
    """Derive the largest UTF-8-valid byte suffix no larger than ``budget``."""
    if not isinstance(rendering, bytes):
        raise V2BError("rendering must be bytes")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        raise V2BError(f"invalid positive byte budget {budget!r}")
    try:
        rendering.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"rendering is not UTF-8: {err}") from err
    if spans:
        if spans[0].get("start_byte") != 0 \
                or spans[-1].get("end_byte") != len(rendering):
            raise V2BError("spans do not cover rendering")
        for left, right in zip(spans, spans[1:]):
            if left.get("end_byte") != right.get("start_byte"):
                raise V2BError("spans are not contiguous")
    elif rendering:
        raise V2BError("non-empty rendering has no unit spans")

    start = max(0, len(rendering) - budget)
    # At most three increments for valid UTF-8, but fail explicitly rather
    # than rely on that fact if the input invariant is ever weakened.
    while start < len(rendering):
        try:
            context = rendering[start:].decode("utf-8").encode("utf-8")
            break
        except UnicodeDecodeError:
            start += 1
    else:
        context = b""
    if context != rendering[start:] or len(context) > budget:
        raise AssertionError("UTF-8 suffix construction failed")

    selected = []
    partial = []
    for span in spans:
        s, e = span["start_byte"], span["end_byte"]
        if e <= start:
            continue
        overlap_start = max(start, s)
        row = dict(identity=span["identity"],
                   rendered_start_byte=s, rendered_end_byte=e,
                   included_start_byte=overlap_start,
                   included_bytes=e - overlap_start,
                   wholly_contained=overlap_start == s)
        selected.append(row)
        if overlap_start != s:
            partial.append(row)
    if len(partial) > 1:
        raise AssertionError("a suffix partially overlaps multiple units")
    return dict(context=context, context_bytes=len(context),
                rendering_bytes=len(rendering), rendering_start_byte=start,
                budget_bytes=budget, eligible=len(rendering) >= budget,
                utf8_shortfall_bytes=budget - len(context)
                if len(rendering) >= budget else None,
                selected_units=selected,
                partial_unit=partial[0] if partial else None)


def _graph_nodes(language, nodes, edges):
    checked = {validate_identity(language, node) for node in nodes}
    if len(checked) != len(nodes):
        raise V2BError("duplicate graph nodes")
    normalized_edges = set()
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise V2BError(f"edge is not [dependent, dependency]: {edge!r}")
        src = validate_identity(language, edge[0])
        dst = validate_identity(language, edge[1])
        if src not in checked or dst not in checked:
            raise V2BError(f"edge endpoint absent from unit universe: {edge!r}")
        if src != dst:
            normalized_edges.add((src, dst))
    return checked, normalized_edges


def _components(nodes, edges):
    """Deterministic iterative Kosaraju; safe for mathlib-scale graphs."""
    adj = {node: [] for node in nodes}
    rev = {node: [] for node in nodes}
    for src, dst in edges:
        adj[src].append(dst)
        rev[dst].append(src)
    for table in (adj, rev):
        for node in table:
            table[node].sort()

    seen = set()
    finish = []
    for root in sorted(nodes):
        if root in seen:
            continue
        seen.add(root)
        # An explicit DFS frame index is required here.  The tempting
        # (node, expanded) stack is wrong on DAGs with cross-edges if nodes
        # are marked when scheduled: a pending descendant can sit below a
        # parent's expanded marker and corrupt finishing order, merging
        # unrelated nodes into one apparent SCC.
        stack = [(root, 0)]
        while stack:
            node, next_index = stack[-1]
            if next_index >= len(adj[node]):
                stack.pop()
                finish.append(node)
                continue
            nxt = adj[node][next_index]
            stack[-1] = (node, next_index + 1)
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, 0))

    raw_components = []
    assigned = set()
    for root in reversed(finish):
        if root in assigned:
            continue
        assigned.add(root)
        members = []
        stack = [root]
        while stack:
            node = stack.pop()
            members.append(node)
            for nxt in reversed(rev[node]):
                if nxt not in assigned:
                    assigned.add(nxt)
                    stack.append(nxt)
        raw_components.append(tuple(sorted(members)))
    raw_components.sort(key=lambda members: members[0])
    component_of = {}
    for cid, members in enumerate(raw_components):
        for node in members:
            component_of[node] = cid
    return raw_components, component_of


def canonical_dependency_order(language, repo, target, nodes, edges):
    """Return §15.A4's dependency-before-dependent closure order."""
    target = validate_identity(language, target)
    checked, normalized_edges = _graph_nodes(language, nodes, edges)
    if target not in checked:
        raise V2BError(f"target absent from graph unit universe: {target!r}")
    components, component_of = _components(checked, normalized_edges)
    raw = {cid: set() for cid in range(len(components))}
    for src, dst in normalized_edges:
        a, b = component_of[src], component_of[dst]
        if a != b:
            raw[a].add(b)

    target_cid = component_of[target]
    distances = {target_cid: 0}
    queue = deque([target_cid])
    while queue:
        cid = queue.popleft()
        for dep in sorted(raw[cid]):
            if dep not in distances:
                distances[dep] = distances[cid] + 1
                queue.append(dep)
    closure = set(distances) - {target_cid}

    # Reverse dependent->dependency into dependency->dependent and Kahn-sort.
    dependents = {cid: set() for cid in closure}
    indegree = {cid: 0 for cid in closure}
    for dependent in closure:
        for dependency in raw[dependent]:
            if dependency in closure:
                if dependent not in dependents[dependency]:
                    dependents[dependency].add(dependent)
                    indegree[dependent] += 1

    def ready_key(cid):
        unit_identity = components[cid][0]
        tie = seeded_hash("k4sel:v2b:20260808", repo, *target,
                          *unit_identity)
        return (-distances[cid], tie, cid)

    ready = [ready_key(cid) for cid in closure if indegree[cid] == 0]
    heapq.heapify(ready)
    component_order = []
    while ready:
        _, _, cid = heapq.heappop(ready)
        component_order.append(cid)
        for dependent in sorted(dependents[cid]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, ready_key(dependent))
    if len(component_order) != len(closure):
        raise AssertionError("condensed dependency graph is cyclic")
    unit_order = [member for cid in component_order
                  for member in components[cid]]
    return dict(unit_order=[list(node) for node in unit_order],
                component_order=[list(map(list, components[cid]))
                                 for cid in component_order],
                target_scc=[list(node) for node in components[target_cid]],
                distance_by_component={
                    str(cid): distances[cid] for cid in component_order})


def k5_unit_order(language, repo, target, units, seed):
    """Frozen random-nondependency order: lowest priority nearest query."""
    target = validate_identity(language, target)
    if not isinstance(seed, int) or isinstance(seed, bool) \
            or seed not in (0, 1, 2):
        raise V2BError(f"invalid k5 seed {seed!r}")
    checked = {validate_identity(language, unit) for unit in units}
    if len(checked) != len(units):
        raise V2BError("duplicate k5 universe units")
    rows = []
    for unit in checked:
        priority = seeded_hash(f"k5:{seed}", repo, *target, *unit)
        rows.append((priority, unit))
    # Top-to-bottom descending: the minimum hash is the final, nearest unit.
    rows.sort(reverse=True)
    return [dict(identity=list(unit), priority_sha256=priority)
            for priority, unit in rows]


def k6_unit_order(language, repo, target, scored_units):
    """Frozen BM25 post-score order; this does not choose the BM25 formula."""
    target = validate_identity(language, target)
    rows = []
    seen = set()
    for row in scored_units:
        if not isinstance(row, dict):
            raise V2BError("k6 scored unit is not an object")
        unit = validate_identity(language, row.get("identity"))
        if unit in seen:
            raise V2BError(f"duplicate k6 unit {unit!r}")
        seen.add(unit)
        score = row.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) \
                or not math.isfinite(score):
            raise V2BError(f"invalid k6 score for {unit!r}: {score!r}")
        tie = seeded_hash("k6tie:v2b:20260808", repo, *target, *unit)
        rows.append((float(score), tie, unit))
    # Top-to-bottom: score ascending, tie hash descending.  Thus the largest
    # score and, within ties, lowest hash are nearest the query at the end.
    rows.sort(key=lambda row: (row[0], -int(row[1], 16), row[2]))
    return [dict(identity=list(unit), score=score, tie_sha256=tie)
            for score, tie, unit in rows]


def _typed_term(term):
    if not isinstance(term, (list, tuple)) or len(term) != 2 \
            or not isinstance(term[0], str) or not term[0] \
            or not isinstance(term[1], (str, int, float)) \
            or isinstance(term[1], bool) \
            or isinstance(term[1], float) and not math.isfinite(term[1]):
        raise V2BError(f"invalid typed lexical term {term!r}")
    return tuple(term)


def bm25_scores(language, query_terms, documents, k1=1.2, b=0.75):
    """Compute §15.A11's fully frozen typed-record BM25 scores."""
    if k1 != 1.2 or b != 0.75:
        raise V2BError("V2-b BM25 constants are frozen at k1=1.2, b=0.75")
    query = Counter(_typed_term(term) for term in query_terms)
    rows = []
    seen = set()
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise V2BError(f"BM25 document[{index}] is not an object")
        identity = validate_identity(language, document.get("identity"))
        if identity in seen:
            raise V2BError(f"duplicate BM25 document {identity!r}")
        seen.add(identity)
        terms = [_typed_term(term) for term in document.get("terms", ())]
        rows.append(dict(identity=identity, terms=terms,
                         tf=Counter(terms), length=len(terms)))
    if not rows:
        raise V2BError("empty BM25 document universe")
    total_length = sum(row["length"] for row in rows)
    if total_length == 0:
        raise V2BError("BM25 document universe has zero lexical length")
    n_docs = len(rows)
    avgdl = total_length / n_docs
    df = Counter()
    for row in rows:
        df.update(row["tf"].keys())
    # Canonical term order fixes floating summation order across construction
    # paths; raw query frequency still enters linearly.
    ordered_query = sorted(query, key=lambda term: canonical_json_bytes(
        list(term)))
    query_statistics = []
    for term in ordered_query:
        term_df = df.get(term, 0)
        idf = math.log(1.0 + (n_docs - term_df + 0.5)
                       / (term_df + 0.5))
        query_statistics.append(dict(term=list(term), qtf=query[term],
                                     df=term_df, idf=idf))
    output = []
    for row in rows:
        norm = k1 * (1.0 - b + b * row["length"] / avgdl)
        score = 0.0
        for stats in query_statistics:
            term = tuple(stats["term"])
            tf = row["tf"].get(term, 0)
            if not tf:
                continue
            score += (stats["qtf"] * stats["idf"] * tf * (k1 + 1.0)
                      / (tf + norm))
        output.append(dict(identity=list(row["identity"]), score=score,
                           document_length=row["length"]))
    return dict(k1=k1, b=b, n_documents=n_docs, avg_document_length=avgdl,
                n_query_terms=sum(query.values()),
                n_distinct_query_terms=len(query),
                query_statistics=query_statistics, scores=output)


def _leading_horizontal(line):
    n = 0
    while n < len(line) and line[n:n + 1] in (b" ", b"\t"):
        n += 1
    return line[:n]


def interface_payload(language, declaration, header_bytes):
    """Render §15.A5's exact interface marker for a splittable unit."""
    if not isinstance(declaration, bytes):
        raise V2BError("declaration must be bytes")
    try:
        declaration.decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"declaration is not UTF-8: {err}") from err
    if not isinstance(header_bytes, int) or isinstance(header_bytes, bool) \
            or not 0 < header_bytes < len(declaration):
        raise V2BError(f"invalid header byte boundary {header_bytes!r}")
    header = declaration[:header_bytes]
    body = declaration[header_bytes:]
    if language == "python":
        if not header.rstrip(b" \t").endswith(b":"):
            raise V2BError("Python interface header does not end at colon")
        if body.startswith(b"\n"):
            indent = _leading_horizontal(body[1:].split(b"\n", 1)[0])
        else:
            first_header_line = header.split(b"\n", 1)[0]
            indent = _leading_horizontal(first_header_line) + b"    "
        return header + b"\n" + indent + b"...  # ctx: body omitted\n"
    if language == "lean":
        line_start = header.rfind(b"\n") + 1
        before_delimiter = header[line_start:]
        if before_delimiter.strip(b" \t") == b"":
            indent = before_delimiter
        else:
            first_decl_line = declaration.split(b"\n", 1)[0]
            indent = _leading_horizontal(first_decl_line) + b"  "
        return header + b"\n" + indent + b"-- ctx: body omitted\n"
    raise V2BError(f"no interface marker for language {language!r}")
