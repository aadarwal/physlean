#!/usr/bin/env python3
"""Property-oriented tests for V2-b graph ordering and byte rendering."""
import os
import math
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_assemble import (_components, canonical_dependency_order,
                          bm25_scores, interface_payload,
                          k5_unit_order, k6_unit_order, make_chunk,
                          normalize_payload, render_chunks,
                          splice_local_prefix,
                          utf8_budget_suffix)
from v2b_common import V2BError


def test_payload_normalization_is_exact_and_idempotent():
    cases = [(b"x", b"x\n", 0, 1),
             (b"x\n", b"x\n", 0, 0),
             (b"x\n\n\n", b"x\n", 2, 0),
             (b"", b"\n", 0, 1)]
    for raw, expected, removed, appended in cases:
        got, audit = normalize_payload(raw)
        assert got == expected
        assert audit == dict(n_removed_terminal_lf=removed,
                             n_appended_terminal_lf=appended)
        assert normalize_payload(got)[0] == got


def test_chunk_join_bytes_belong_to_preceding_unit():
    units = [dict(identity=["M", "a"], relpath="M.lean", payload=b"def a"),
             dict(identity=["N", "b"], relpath="N.lean", payload=b"def b\n\n")]
    rendered, spans = render_chunks("lean", units)
    c0, _ = make_chunk("lean", "M.lean", b"def a")
    c1, _ = make_chunk("lean", "N.lean", b"def b\n\n")
    assert rendered == c0 + b"\n" + c1 + b"\n"
    assert spans[0]["join_bytes"] == 1
    assert spans[0]["end_byte"] == len(c0) + 1
    assert spans[1]["start_byte"] == len(c0) + 1
    assert spans[1]["n_removed_terminal_lf"] == 1
    assert spans[1]["join_bytes"] == 0
    assert spans[1]["separator_bytes"] == 1
    assert spans[1]["end_byte"] == len(rendered)


def test_utf8_suffixes_are_nested_and_have_at_most_one_partial_unit():
    units = [dict(identity=["M", "α"], relpath="M.lean",
                  payload="def α := 1\n".encode()),
             dict(identity=["N", "β"], relpath="N.lean",
                  payload="def β := α\n".encode())]
    rendered, spans = render_chunks("lean", units)
    small = utf8_budget_suffix(rendered, spans, 17)
    large = utf8_budget_suffix(rendered, spans, 31)
    assert len(small["context"]) <= 17
    assert len(large["context"]) <= 31
    assert large["context"].endswith(small["context"])
    assert small["context"].decode("utf-8")
    assert large["context"].decode("utf-8")
    assert sum(not row["wholly_contained"]
               for row in large["selected_units"]) <= 1
    under = utf8_budget_suffix(rendered, spans, len(rendered) + 10)
    assert under["eligible"] is False
    assert under["context"] == rendered


def test_k2_splices_once_without_banner_or_normalization():
    source = "αA DROP βB DROP2 γTARGET".encode()
    first = "αA ".encode()
    drop = "DROP".encode()
    middle = " βB ".encode()
    drop2 = "DROP2".encode()
    target_start = len("αA DROP βB DROP2 γ".encode())
    s1 = len(first)
    s2 = len(first + drop + middle)
    report = splice_local_prefix(source, target_start, [
        dict(identity=["M", "d1"], start_byte=s1,
             end_byte=s1 + len(drop)),
        # overlapping duplicate evidence merges rather than double-splicing
        dict(identity=["M", "d1b"], start_byte=s1 + 1,
             end_byte=s1 + len(drop)),
        dict(identity=["M", "d2"], start_byte=s2,
             end_byte=s2 + len(drop2)),
    ])
    expected_core = first + middle + " γ".encode()
    assert report["core"] == expected_core
    assert report["rendering"] == expected_core + b"\n"
    assert b"ctx:" not in report["rendering"]
    assert len(report["merged_exclusions"]) == 2
    assert report["spans"][0]["separator_bytes"] == 1


def test_topology_precedes_distance_in_same_shell_counterexample():
    # t->a, t->b, a->c, c->b.  A global farthest-first sort could place c
    # before b, but the dependency topology requires b, c, a.
    nodes = [["M", name] for name in ("t", "a", "b", "c")]
    edges = [(["M", "t"], ["M", "a"]),
             (["M", "t"], ["M", "b"]),
             (["M", "a"], ["M", "c"]),
             (["M", "c"], ["M", "b"])]
    report = canonical_dependency_order("lean", "repo", ["M", "t"],
                                        nodes, edges)
    assert report["unit_order"] == [["M", "b"], ["M", "c"],
                                    ["M", "a"]]


def test_target_scc_cycle_mates_are_excluded():
    nodes = [["M", name] for name in ("t", "mate", "dep")]
    edges = [(["M", "t"], ["M", "mate"]),
             (["M", "mate"], ["M", "t"]),
             (["M", "t"], ["M", "dep"])]
    report = canonical_dependency_order("lean", "repo", ["M", "t"],
                                        nodes, edges)
    assert report["target_scc"] == [["M", "mate"], ["M", "t"]]
    assert report["unit_order"] == [["M", "dep"]]


def test_dependency_order_property_on_seeded_dags():
    rng = random.Random(20260808)
    for trial in range(20):
        # Index increases toward dependencies, ensuring an acyclic raw graph.
        names = ["t"] + [f"n{i}" for i in range(16)]
        nodes = [["M", name] for name in names]
        edges = []
        for i in range(len(names) - 1):
            # Keep every node reachable from t with a spine, then add chords.
            edges.append((["M", names[i]], ["M", names[i + 1]]))
            for j in range(i + 2, len(names)):
                if rng.random() < 0.10:
                    edges.append((["M", names[i]], ["M", names[j]]))
        report = canonical_dependency_order("lean", f"repo{trial}",
                                            ["M", "t"], nodes, edges)
        order = {tuple(unit): i for i, unit in
                 enumerate(report["unit_order"])}
        for dependent, dependency in edges:
            dep, req = tuple(dependent), tuple(dependency)
            if dep in order and req in order:
                assert order[req] < order[dep]
        assert len(order) == len(nodes) - 1


def test_iterative_sccs_match_mutual_reachability():
    rng = random.Random(8128)
    for _ in range(20):
        nodes = {("M", f"n{i}") for i in range(12)}
        edges = {(src, dst) for src in nodes for dst in nodes
                 if src != dst and rng.random() < 0.12}
        components, component_of = _components(nodes, edges)
        assert sum(len(c) for c in components) == len(nodes)
        adj = {node: set() for node in nodes}
        for src, dst in edges:
            adj[src].add(dst)

        def reachable(src, dst):
            seen, stack = {src}, [src]
            while stack:
                node = stack.pop()
                for nxt in adj[node]:
                    if nxt == dst:
                        return True
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return src == dst

        for left in nodes:
            for right in nodes:
                same = component_of[left] == component_of[right]
                assert same == (reachable(left, right)
                                and reachable(right, left))


def test_k5_and_k6_nearest_end_directions_are_frozen():
    target = ["M", "t"]
    units = [["M", name] for name in ("a", "b", "c")]
    k5 = k5_unit_order("lean", "repo", target, units, seed=0)
    priorities = [row["priority_sha256"] for row in k5]
    assert priorities == sorted(priorities, reverse=True)
    assert priorities[-1] == min(priorities)
    try:
        k5_unit_order("lean", "repo", target, units, seed=3)
        assert False, "accepted undeclared k5 seed"
    except V2BError as err:
        assert "seed" in str(err)

    tied = [dict(identity=unit, score=1.0) for unit in units[:2]]
    scored = tied + [dict(identity=units[2], score=2.0)]
    k6 = k6_unit_order("lean", "repo", target, scored)
    assert k6[-1]["identity"] == units[2]  # highest score is nearest
    tie_rows = k6[:2]
    assert tie_rows[-1]["tie_sha256"] == min(
        row["tie_sha256"] for row in tie_rows)


def test_typed_bm25_formula_and_linear_query_frequency():
    docs = [dict(identity=["M", "a"],
                 terms=[["IDENT", "x"], ["IDENT", "x"],
                        ["STRING", "x"]]),
            dict(identity=["M", "b"],
                 terms=[["IDENT", "x"], ["IDENT", "y"]])]
    once = bm25_scores("lean", [["IDENT", "x"]], docs)
    twice = bm25_scores("lean", [["IDENT", "x"], ["IDENT", "x"]],
                        docs)
    assert once["k1"] == 1.2 and once["b"] == 0.75
    assert once["avg_document_length"] == 2.5
    by_once = {tuple(row["identity"]): row["score"]
               for row in once["scores"]}
    by_twice = {tuple(row["identity"]): row["score"]
                for row in twice["scores"]}
    for identity in by_once:
        assert math.isclose(by_twice[identity], 2 * by_once[identity],
                            rel_tol=1e-15)
    # Typed records keep a string literal "x" distinct from identifier x.
    string = bm25_scores("lean", [["STRING", "x"]], docs)
    assert string["scores"][0]["score"] > 0
    assert string["scores"][1]["score"] == 0


def test_interface_markers_use_relative_layout_indentation():
    py_multi = b"def f(x):\n  return x"
    assert interface_payload("python", py_multi, len(b"def f(x):")) == \
        b"def f(x):\n  ...  # ctx: body omitted\n"
    py_one = b"  def f(x): return x"
    assert interface_payload("python", py_one, len(b"  def f(x):")) == \
        b"  def f(x):\n      ...  # ctx: body omitted\n"
    lean_line = b"  theorem t : True\n  := by trivial"
    boundary = len(b"  theorem t : True\n  ")
    assert interface_payload("lean", lean_line, boundary) == \
        b"  theorem t : True\n  \n  -- ctx: body omitted\n"
    lean_inline = b"  theorem t : True := by trivial"
    boundary = len(b"  theorem t : True ")
    assert interface_payload("lean", lean_inline, boundary).endswith(
        b"\n    -- ctx: body omitted\n")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B ASSEMBLER TESTS PASS")
