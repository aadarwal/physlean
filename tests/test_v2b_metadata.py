#!/usr/bin/env python3
"""V2-b A1-A3 tests: frozen terciles, Hamilton quotas, conservative
first-add provenance on synthetic temp git repos, strict extraction
normalization for both languages, and deterministic sample plans.
No study artifact is read and no real target is drawn.
Run: python3 tests/test_v2b_metadata.py"""
import json
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extract_lean import target_priority
from v2b_common import (V2BError, identity_key, seeded_hash,
                        sha256_sorted_json)
from v2b_metadata import (SAMPLING_SEED, allocate_quotas,
                          build_candidate_table, build_sample_plan,
                          cohort_of, corpus_git_identity, first_add_record,
                          tercile, tercile_cutpoints)
from test_prepare_v2b_assembly import _boundaries


def _git(repo, *args, author_date=None, commit_date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    if author_date:
        env["GIT_AUTHOR_DATE"] = author_date
    if commit_date:
        env["GIT_COMMITTER_DATE"] = commit_date
    p = subprocess.run(["git", "-C", repo, *args], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def _repo(td, name="corpus"):
    root = os.path.join(td, name)
    os.mkdir(root)
    _git(root, "init", "-q")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    return root


def _commit(root, rel, text, msg, adate, cdate=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    open(path, "w").write(text)
    _git(root, "add", rel)
    _git(root, "commit", "-q", "-m", msg,
         author_date=adate, commit_date=cdate or adate)
    return _git(root, "rev-parse", "HEAD").strip()


def test_tercile_rule_frozen():
    q1, q2 = tercile_cutpoints([10, 20, 30, 40, 50, 60, 70])
    assert (q1, q2) == (30, 50)               # floor((6)/3)=2, floor(12/3)=4
    assert [tercile(v, q1, q2) for v in (10, 30, 31, 50, 51)] == \
        [1, 1, 2, 2, 3]
    q1, q2 = tercile_cutpoints([5, 5, 5, 9])  # ties share a tercile
    assert tercile(5, q1, q2) == 1 and tercile(9, q1, q2) == 3


def test_hamilton_quotas_deterministic():
    pops = {"L1-D1-Cpre": 6, "L1-D1-Cpost": 6, "L2-D2-Cpre": 8}
    q = allocate_quotas(pops, 10)
    assert sum(q.values()) == 10
    assert q["L2-D2-Cpre"] == 4 and q["L1-D1-Cpre"] == 3 \
        and q["L1-D1-Cpost"] == 3
    # remainder tie -> ascending cell label wins
    q2 = allocate_quotas({"L1-D1-Cpre": 1, "L1-D1-Cpost": 1}, 1)
    assert q2["L1-D1-Cpost"] == 1 and q2["L1-D1-Cpre"] == 0
    try:
        allocate_quotas({"bogus": 3}, 5)
        assert False
    except V2BError:
        pass
    # Exact integer remainders: float rounding must never choose a seat.
    huge = {"L1-D1-Cpre": 10**18 + 1,
            "L1-D1-Cpost": 10**18,
            "L2-D2-Cpre": 1}
    q3 = allocate_quotas(huge, 20)
    assert sum(q3.values()) == 20
    assert q3["L1-D1-Cpre"] >= q3["L1-D1-Cpost"]


def test_first_add_min_over_all_records_and_signals():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        _commit(root, "base.txt", "b", "root", "2020-01-01T00:00:00 +0000")
        ident = corpus_git_identity(
            root, expected_sha=_git(root, "rev-parse", "HEAD").strip())
        cache = {}
        # author earlier than committer -> author wins
        _commit(root, "a.py", "x", "add a",
                "2023-05-01T00:00:00 +0000", "2024-06-01T00:00:00 +0000")
        rec = first_add_record(root, "a.py", ident["first_commit"], cache)
        assert rec["provenance_mode"] == "exact-add"
        assert rec["exact_add_unresolved"] is False
        assert rec["timestamp_utc"].startswith("2023-05-01")
        assert rec["timestamp_source"] == "author"
        assert rec["n_add_records"] == 1
        assert rec["per_commit_signals"][0]["author_date"].startswith(
            "2023-05-01")
        assert rec["per_commit_signals"][0]["committer_date"].startswith(
            "2024-06-01")
        assert not rec["vendor_flagged"]
        assert cohort_of(rec) == "pre"
        # rename tracked via --follow
        _git(root, "mv", "a.py", "b.py")
        _git(root, "commit", "-q", "-m", "mv",
             author_date="2025-01-01T00:00:00 +0000",
             commit_date="2025-01-01T00:00:00 +0000")
        rec2 = first_add_record(root, "b.py", ident["first_commit"], cache)
        assert rec2["timestamp_utc"].startswith("2023-05-01")
        # delete/re-add -> two records; identical dates tie -> smallest hash
        _git(root, "rm", "-q", "b.py")
        _git(root, "commit", "-q", "-m", "rm",
             author_date="2025-02-01T00:00:00 +0000",
             commit_date="2025-02-01T00:00:00 +0000")
        h2 = _commit(root, "b.py", "x2", "re-add",
                     "2023-05-01T00:00:00 +0000")
        rec3 = first_add_record(root, "b.py", ident["first_commit"], cache)
        assert rec3["n_add_records"] == 2
        assert rec3["timestamp_utc"].startswith("2023-05-01")
        assert rec3["n_tied_commits"] >= 1
        assert rec3["commit"] == min(
            {s["commit"] for s in rec3["per_commit_signals"]
             if s["commit"] in (rec3["commit"], h2)} | {rec3["commit"]})
        # post-cutoff strictly-later rule
        _commit(root, "new.py", "n", "add new",
                "2024-11-13T00:00:00 +0000")
        recn = first_add_record(root, "new.py", ident["first_commit"], cache)
        assert cohort_of(recn) == "post"
        _commit(root, "edge.py", "e", "add edge",
                "2024-11-12T23:59:59 +0000")
        rece = first_add_record(root, "edge.py", ident["first_commit"],
                                cache)
        assert cohort_of(rece) == "pre"     # boundary instant is PRE


def _merge_add_without_nonmerge_add(root, rel, stamp, tag):
    """Add rel only while recording a merge, then modify it normally.

    Git's ordinary single-path history suppresses the merge diff, reproducing
    the no-A topology observed in SymPy while leaving a dated existence
    witness in the later modification.
    """
    main = _git(root, "branch", "--show-current").strip()
    branch = f"side-{tag}"
    _git(root, "checkout", "-q", "-b", branch)
    _commit(root, f"side-{tag}.txt", "s", f"side {tag}", stamp)
    _git(root, "checkout", "-q", main)
    _commit(root, f"main-{tag}.txt", "m", f"main {tag}", stamp)
    _git(root, "merge", "--no-ff", "--no-commit", branch)
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    open(path, "w").write("merge-created")
    _git(root, "add", rel)
    _git(root, "commit", "-q", "-m", f"merge {tag}",
         author_date=stamp, commit_date=stamp)
    return _commit(root, rel, "later modification", f"modify {tag}", stamp)


def test_no_add_merge_topology_uses_only_pre_witness():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        _commit(root, "base.txt", "b", "root", "2020-01-01T00:00:00 +0000")
        _merge_add_without_nonmerge_add(
            root, "old.py", "2021-06-07T00:00:00 +0000", "old")
        # Pin the triggering condition: ordinary --follow sees no A record.
        assert not _git(root, "log", "--follow", "--find-renames=50%",
                        "--diff-filter=A", "--format=%H|%aI|%cI", "--",
                        "old.py").strip()
        head = _git(root, "rev-parse", "HEAD").strip()
        ident = corpus_git_identity(root, expected_sha=head)
        record = first_add_record(root, "old.py", ident["first_commit"], {})
        assert record["provenance_mode"] == "no-add-pre-witness"
        assert record["exact_add_unresolved"] is True
        assert record["n_add_records"] == 0
        assert record["n_history_records"] >= 1
        assert record["history_witness"]["commit"] == record["commit"]
        assert record["vendor_flagged"] and record["vendor_unknown"]
        assert cohort_of(record) == "pre"

        _merge_add_without_nonmerge_add(
            root, "unknown.py", "2025-06-07T00:00:00 +0000", "new")
        head = _git(root, "rev-parse", "HEAD").strip()
        ident = corpus_git_identity(root, expected_sha=head)
        try:
            first_add_record(root, "unknown.py", ident["first_commit"], {})
            assert False, "post-only no-add history was accepted"
        except V2BError as err:
            assert "after cohort cutoff" in str(err)


def test_anomaly_recorded_not_acted_on_and_vendor_signals():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        _commit(root, "base.txt", "b", "root", "2020-01-01T00:00:00 +0000")
        ident = corpus_git_identity(
            root, expected_sha=_git(root, "rev-parse", "HEAD").strip())
        cache = {}
        _commit(root, "weird.py", "w", "normal subject",
                "1999-01-01T00:00:00 +0000", "2025-01-01T00:00:00 +0000")
        rec = first_add_record(root, "weird.py", ident["first_commit"],
                               cache)
        assert rec["author_date_anomalous"] is True
        assert rec["timestamp_utc"].startswith("1999-01-01")  # record-only
        # subject-based vendor flag (prep_streams classifier reused)
        _commit(root, "v.py", "v", "vendored from upstream project",
                "2025-03-01T00:00:00 +0000")
        assert first_add_record(root, "v.py", ident["first_commit"],
                                cache)["vendor_flagged"]
        # path-segment flag
        _commit(root, "vendor/x.py", "x", "plain",
                "2025-03-01T00:00:00 +0000")
        assert first_add_record(root, "vendor/x.py", ident["first_commit"],
                                cache)["path_vendor"]
        # bulk-import flag: one commit adding 100 files
        for i in range(100):
            path = os.path.join(root, "bulk", f"f{i}.py")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").write(str(i))
        _git(root, "add", "bulk")
        _git(root, "commit", "-q", "-m", "big drop",
             author_date="2025-04-01T00:00:00 +0000",
             commit_date="2025-04-01T00:00:00 +0000")
        recb = first_add_record(root, "bulk/f3.py", ident["first_commit"],
                                cache)
        assert any(s["bulk_import"] for s in recb["per_commit_signals"])
        assert recb["vendor_flagged"]


def test_shallow_and_missing_history_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        _commit(root, "a.txt", "a", "one", "2024-01-01T00:00:00 +0000")
        shallow = os.path.join(td, "shallow")
        p = subprocess.run(["git", "clone", "-q", "--depth", "1",
                            "file://" + root, shallow],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        try:
            corpus_git_identity(
                shallow,
                expected_sha=_git(shallow, "rev-parse", "HEAD").strip())
            assert False, "shallow clone accepted"
        except V2BError as err:
            assert "shallow" in str(err)
        head = _git(root, "rev-parse", "HEAD").strip()
        ident = corpus_git_identity(root, expected_sha=head)
        try:
            first_add_record(root, "never-committed.txt",
                             ident["first_commit"], {})
            assert False
        except V2BError as err:
            assert "no add record" in str(err)
        try:
            corpus_git_identity(root, expected_sha="f" * 40)
            assert False
        except V2BError as err:
            assert "revision drift" in str(err)


def _lean_extraction(root, rels):
    files, mods = [], {}
    for rel, (module, imports, decls) in rels.items():
        files.append(dict(
            module=module, source=os.path.join(root, rel),
            source_sha256=hashlib.sha256(
                open(os.path.join(root, rel), "rb").read()).hexdigest(),
            direct_imports=[dict(module=m, isPrivate=False, isAll=False,
                                 isMeta=False) for m in imports],
            decls=decls))
        mods[module] = rel
    render = {f["module"]: {n: dict(n_internal_occurrences=4,
                                    n_renderable_occurrences=3,
                                    n_unrenderable_occurrences=1)
                            for n in f["decls"]} for f in files}
    return dict(schema="v2a_lean_extract_v3", repo="mathlib4",
                files=files,
                graph=dict(edges=[["M.B", "M.B.u", "M.A", "M.A.t"],
                                  ["M.C", "M.C.v", "M.A", "M.A.t"],
                                  ["M.B", "M.B.u", "M.A", "M.A.t"]],
                           internal_renderability_by_target=render))


def _lean_source(module, specs):
    """Return byte-exact synthetic commands and extraction rows."""
    chunks = []
    decls = {}
    offset = 0
    for fqname, body_bytes, eligible, kind in specs:
        bare = fqname.rsplit(".", 1)[-1]
        header = f"{kind} {bare} : True "
        body = ":=" + " " * (body_bytes - 3) + "\n"
        text = header + body
        decls[fqname] = dict(
            eligible_kind=eligible, selection_contained=True,
            split_kind=":=", kind=kind, start_byte=offset,
            end_byte=offset + len(text), header_bytes=len(header),
            body_bytes=len(body), shell=[])
        chunks.append(text)
        offset += len(text)
    return "".join(chunks), decls


def test_lean_candidate_table_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        ma_text, ma_decls = _lean_source("M.A", [
            ("M.A.t", 100, True, "theorem"),
            ("M.A.s", 200, True, "theorem"),
            ("M.A.gen", 9, False, "instance")])
        mb_text, mb_decls = _lean_source("M.B", [
            ("M.B.u", 300, True, "theorem")])
        mc_text, mc_decls = _lean_source("M.C", [
            ("M.C.v", 400, True, "theorem")])
        source_rows = (("MA.lean", ma_text), ("MB.lean", mb_text),
                       ("MC.lean", mc_text))
        for i, (rel, text) in enumerate(source_rows):
            _commit(root, rel, text, f"add {rel}",
                    f"202{3 + (i % 2)}-0{1 + i}-01T00:00:00 +0000")
        ex = _lean_extraction(root, {
            "MA.lean": ("M.A", [], ma_decls),
            "MB.lean": ("M.B", ["M.A"], mb_decls),
            "MC.lean": ("M.C", ["M.A", "M.B", "External.Mod"],
                        mc_decls),
        })
        ex_path = os.path.join(td, "extract.json")
        json.dump(ex, open(ex_path, "w"))
        boundaries_path, _binding, _rows = _boundaries(
            td, ex, ex_path, name="metadata_boundaries.json")
        head = _git(root, "rev-parse", "HEAD").strip()
        table = build_candidate_table(ex_path, root, "mathlib4",
                                      expected_corpus_sha=head,
                                      lean_boundaries_path=boundaries_path)
        assert table["first_add_provenance_file_counts"] == {
            "exact-add": 3, "no-add-pre-witness": 0}
        assert table["no_add_pre_witness_files"] == []
        assert table["n_candidates"] == 4
        by = {tuple(t["identity"]): t for t in table["targets"]}
        # module centrality: M.A imported by M.B and M.C -> 2; M.B by M.C
        assert by[("M.A", "M.A.t")]["module_in_degree"] == 2
        assert by[("M.B", "M.B.u")]["module_in_degree"] == 1
        assert by[("M.C", "M.C.v")]["module_in_degree"] == 0
        # decl in-degree deduplicates edge multiplicity: 2 distinct sources
        assert by[("M.A", "M.A.t")]["decl_in_degree"] == 2
        assert by[("M.A", "M.A.s")]["decl_in_degree"] == 0
        # priority matches the frozen §14.19 lean key exactly
        for t in table["targets"]:
            assert t["priority"] == target_priority(
                "mathlib4", t["identity"][0], t["identity"][1])
            assert t["priority"] == seeded_hash(
                SAMPLING_SEED, "mathlib4", *t["identity"])
            assert t["cell"].startswith(
                f"L{t['strata']['length_tercile']}-"
                f"D{t['strata']['centrality_tercile']}-C")
        assert by[("M.A", "M.A.t")]["renderability_coverage"] == 0.75
        # v3 lexical null is diagnostic only: a parser-resolved declaration
        # re-enters the population with parser-derived body bytes/span id.
        promoted_ex = json.loads(json.dumps(ex))
        promoted_decl = promoted_ex["files"][0]["decls"]["M.A.s"]
        parser_header = promoted_decl["header_bytes"]
        parser_body = promoted_decl["body_bytes"]
        promoted_decl["header_bytes"] += promoted_decl["body_bytes"]
        promoted_decl["body_bytes"] = 0
        promoted_decl["split_kind"] = None
        promoted_path = os.path.join(td, "promoted_extract.json")
        json.dump(promoted_ex, open(promoted_path, "w"))
        promoted_boundaries, _binding, _rows = _boundaries(
            td, promoted_ex, promoted_path,
            name="promoted_boundaries.json")
        boundary_value = json.load(open(promoted_boundaries))
        promoted_key = identity_key("lean", ["M.A", "M.A.s"])
        boundary_row = boundary_value["boundaries"][promoted_key]
        boundary_row.update(
            header_bytes=parser_header, body_bytes=parser_body,
            split_kind=":=", status="resolved", reason=None,
            n_candidate_starts_total=1, n_tested=1,
            n_untested_after_choice=0, rejected_starts=[],
            changed_vs_v3=True)
        boundary_value["n_resolved_spans"] += 1
        boundary_value["n_unsplit_spans"] -= 1
        boundary_value["n_changed_spans_vs_v3"] += 1
        boundary_value["n_unchanged_spans_vs_v3"] -= 1
        boundary_value["boundaries_sha256"] = sha256_sorted_json(
            boundary_value["boundaries"])
        json.dump(boundary_value, open(promoted_boundaries, "w"),
                  sort_keys=True)
        promoted_table = build_candidate_table(
            promoted_path, root, "mathlib4", expected_corpus_sha=head,
            lean_boundaries_path=promoted_boundaries)
        promoted_by = {tuple(row["identity"]): row
                       for row in promoted_table["targets"]}
        assert promoted_by[("M.A", "M.A.s")]["body_bytes"] == parser_body
        assert promoted_by[("M.A", "M.A.s")]["span_id"] == \
            boundary_row["span_id"]
        # strict failures: wrong schema, wrong repo, revision drift
        try:
            build_candidate_table(ex_path, root, "physlib",
                                  lean_boundaries_path=boundaries_path)
            assert False
        except V2BError:
            pass
        bad = dict(ex, schema="v2a_lean_extract_v2")
        bad_path = os.path.join(td, "old.json")
        json.dump(bad, open(bad_path, "w"))
        try:
            build_candidate_table(bad_path, root, "mathlib4")
            assert False
        except V2BError:
            pass
        try:
            build_candidate_table(ex_path, root, "mathlib4",
                                  expected_corpus_sha="e" * 40,
                                  lean_boundaries_path=boundaries_path)
            assert False
        except V2BError:
            pass
        try:
            build_candidate_table(ex_path, root, "mathlib4",
                                  lean_boundaries_path=boundaries_path)
            assert False, "accepted unpinned candidate build"
        except V2BError as err:
            assert "revision" in str(err)

        drifted = json.loads(json.dumps(ex))
        drifted["files"][0]["source_sha256"] = "0" * 64
        drifted_path = os.path.join(td, "drifted.json")
        json.dump(drifted, open(drifted_path, "w"))
        drifted_boundaries, _binding, _rows = _boundaries(
            td, drifted, drifted_path, name="drifted_boundaries.json")
        try:
            build_candidate_table(drifted_path, root, "mathlib4",
                                  expected_corpus_sha=head,
                                  lean_boundaries_path=drifted_boundaries)
            assert False, "accepted live/extraction source hash drift"
        except V2BError as err:
            assert "source hash drift" in str(err)

        open(os.path.join(root, "MA.lean"), "a").write("\n-- dirty")
        try:
            build_candidate_table(ex_path, root, "mathlib4",
                                  expected_corpus_sha=head,
                                  lean_boundaries_path=boundaries_path)
            assert False, "accepted dirty tracked corpus"
        except V2BError:
            pass


def test_python_candidate_table_and_duplicates():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td)
        _commit(root, "pkg/a.py", "def f(): pass", "add a",
                "2025-01-01T00:00:00 +0000")
        _commit(root, "pkg/b.py", "import pkg.a", "add b",
                "2023-01-01T00:00:00 +0000")
        ex = dict(
            schema="v2a_python_extract_v3", repo="sympy",
            files=[
                dict(module="pkg.a", source=os.path.join(root, "pkg/a.py"),
                     source_sha256=hashlib.sha256(
                         open(os.path.join(root, "pkg/a.py"), "rb").read()
                     ).hexdigest(),
                     imports={},
                     targets=[dict(identity=["pkg.a", "f", 0], name="f",
                                   start_byte=0, kind="FunctionDef",
                                   body_bytes=40, docstring_bytes=7,
                                   binding_count=2),
                              dict(identity=["pkg.a", "f", 60], name="f",
                                   start_byte=60, kind="FunctionDef",
                                   body_bytes=44, docstring_bytes=0,
                                   binding_count=2)]),
                dict(module="pkg.b", source=os.path.join(root, "pkg/b.py"),
                     source_sha256=hashlib.sha256(
                         open(os.path.join(root, "pkg/b.py"), "rb").read()
                     ).hexdigest(),
                     imports={"pkg.a": "pkg.a", "np": "numpy"},
                     targets=[dict(identity=["pkg.b", "g", 0], name="g",
                                   start_byte=0, kind="FunctionDef",
                                   body_bytes=10, docstring_bytes=0,
                                   binding_count=1)])],
            graph=dict(edges=[["pkg.b", "g", 0, "pkg.a", "f", 60]]))
        ex_path = os.path.join(td, "px.json")
        json.dump(ex, open(ex_path, "w"))
        head = _git(root, "rev-parse", "HEAD").strip()
        table = build_candidate_table(ex_path, root, "sympy",
                                      expected_corpus_sha=head)
        by = {tuple(t["identity"]): t for t in table["targets"]}
        assert by[("pkg.a", "f", 0)]["duplicate_stratum"] is True
        assert by[("pkg.b", "g", 0)]["duplicate_stratum"] is False
        assert by[("pkg.a", "f", 0)]["module_in_degree"] == 1  # from pkg.b
        assert by[("pkg.a", "f", 60)]["decl_in_degree"] == 1
        assert by[("pkg.a", "f", 0)]["decl_in_degree"] == 0
        assert by[("pkg.a", "f", 0)]["docstring_bytes"] == 7
        assert by[("pkg.b", "g", 0)]["cohort"] == "pre"
        assert by[("pkg.a", "f", 0)]["cohort"] == "post"
        # sample plan: deterministic, quota-respecting, shortfall recorded
        plan = build_sample_plan(table, 3)
        assert plan["n_selected"] == 3
        assert sum(plan["quota_table"].values()) == 3
        assert plan["targets"] == build_sample_plan(table, 3)["targets"]
        big = build_sample_plan(table, 20)
        assert big["n_selected"] == 3         # population-limited
        assert sum(big["shortfalls"].values()) == 17
        bad_priority = json.loads(json.dumps(table))
        bad_priority["targets"][0]["priority"] = "0" * 64
        try:
            build_sample_plan(bad_priority, 3)
            assert False, "accepted priority drift"
        except V2BError:
            pass
        bad_cell = json.loads(json.dumps(table))
        original_cell = bad_cell["targets"][0]["cell"]
        bad_cell["targets"][0]["cell"] = next(
            cell for cell in ("L1-D1-Cpre", "L3-D3-Cpost")
            if cell != original_cell)
        try:
            build_sample_plan(bad_cell, 3)
            assert False, "accepted recorded cell drift"
        except V2BError:
            pass
        bad_cutpoint = json.loads(json.dumps(table))
        bad_cutpoint["tercile_cutpoints"]["body_bytes"] = [0, 0]
        try:
            build_sample_plan(bad_cutpoint, 3)
            assert False, "accepted cutpoints from a different population"
        except V2BError:
            pass
        duplicate = json.loads(json.dumps(table))
        duplicate["targets"].append(duplicate["targets"][0])
        duplicate["n_candidates"] += 1
        try:
            build_sample_plan(duplicate, 3)
            assert False, "accepted duplicate candidate identity"
        except V2BError:
            pass
        bad_provenance_summary = json.loads(json.dumps(table))
        bad_provenance_summary["first_add_provenance_file_counts"][
            "exact-add"] -= 1
        try:
            build_sample_plan(bad_provenance_summary, 3)
            assert False, "accepted first-add provenance summary drift"
        except V2BError as err:
            assert "provenance summary drift" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B METADATA TESTS PASS")
