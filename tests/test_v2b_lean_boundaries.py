#!/usr/bin/env python3
"""Adversarial synthetic tests for the parser-witnessed Lean boundary
chain: planner dedup/ordering/setup discipline, invocation binding over
live file bytes, delimiter byte-witnessing, effective row semantics,
exact order/membership, replay comparison. No corpus, salt, GPU, or
assembly artifact is touched. Run: python3 tests/test_v2b_lean_boundaries.py"""
import copy
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_lean_boundaries import (BOUNDARIES_SCHEMA, BOUNDARY_MANIFEST_SCHEMA,
                                 BOUNDARY_MARKER, BOUNDARY_RESULT_SCHEMA,
                                 DRIVER_MANIFEST_SCHEMA,
                                 DRIVER_OUTPUT_SCHEMA,
                                 aggregate_driver_runs,
                                 build_boundary_artifact,
                                 build_boundary_manifest,
                                 build_driver_manifests,
                                 canonical_driver_manifest_bytes,
                                 compute_invocation_sha256,
                                 parse_driver_stdout, replay_equal,
                                 span_id_of)
from v2b_common import (V2BError, identity_key, sha256_bytes,
                        sha256_sorted_json)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return _sha(text.encode("utf-8"))


TRICKY = "def f : let n := 1; Nat := 0\n"          # the counterexample
PLAIN = "theorem t : True := trivial\n"
MATCHY = "def g : Nat → Nat\n  | 0 => 1\n  | _ => 2\n"


def _fixture(td):
    """One module file with three spans; the tricky span is shared by two
    identities with an identical (wrong) old lexical split."""
    root = os.path.join(td, "corpus")
    text = TRICKY + PLAIN + MATCHY
    source = os.path.join(root, "A.lean")
    source_sha = _write(source, text)
    setup = os.path.join(td, "setup", "A.setup.json")
    _write(setup, json.dumps(dict(module="M.A", options={})))
    # all offsets are BYTES (MATCHY contains a 3-byte arrow)
    t_start, t_end = 0, len(TRICKY.encode("utf-8"))
    p_start, p_end = t_end, t_end + len(PLAIN.encode("utf-8"))
    m_start, m_end = p_end, p_end + len(MATCHY.encode("utf-8"))
    wrong_h = TRICKY.index(":=")                    # inner type := (ascii)
    tricky_old = dict(header_bytes=wrong_h, body_bytes=t_end - wrong_h,
                      split_kind=":=")
    plain_h = PLAIN.index(":=")                     # ascii: char == byte
    matchy_h = len(MATCHY[:MATCHY.index("|")].encode("utf-8"))

    def decl(start, end, old):
        return dict(start_byte=start, end_byte=end,
                    header_bytes=old["header_bytes"],
                    body_bytes=old["body_bytes"],
                    split_kind=old["split_kind"])

    extraction = dict(
        schema="v2a_lean_extract_v3", repo="mathlib4",
        files=[dict(module="M.A", source=source, source_sha256=source_sha,
                    decls={
                        "M.A.f": decl(t_start, t_end, tricky_old),
                        "M.A.f_alias": decl(t_start, t_end, tricky_old),
                        "M.A.t": decl(p_start, p_end, dict(
                            header_bytes=plain_h,
                            body_bytes=p_end - p_start - plain_h,
                            split_kind=":=")),
                        "M.A.g": decl(m_start, m_end, dict(
                            header_bytes=matchy_h,
                            body_bytes=m_end - m_start - matchy_h,
                            split_kind="|"))})])
    extraction_path = os.path.join(td, "extraction.json")
    json.dump(extraction, open(extraction_path, "w"))
    setup_map = {source: setup}
    return dict(extraction=extraction_path, source=source, setup=setup,
                setup_map=setup_map, text=text,
                spans=dict(tricky=(t_start, t_end),
                           plain=(p_start, p_end),
                           matchy=(m_start, m_end)),
                tricky_true_h=TRICKY.rindex(":="),
                plain_h=p_start + plain_h,
                matchy_h=m_start + matchy_h,
                source_sha=source_sha)


def _manifest(td, fixture):
    manifest = build_boundary_manifest(fixture["extraction"],
                                       fixture["setup_map"])
    path = os.path.join(td, "manifest.json")
    json.dump(manifest, open(path, "w"))
    return manifest, path, _sha(open(path, "rb").read())


def _driver(td):
    path = os.path.join(td, "driver.lean")
    _write(path, "-- V2BParseCommand stand-in\n")
    return path, _sha(open(path, "rb").read())


def _runtime(td, driver_path, driver_sha, toolchain):
    environment = {name: None for name in (
        "ELAN_HOME", "ELAN_TOOLCHAIN", "LANG", "LC_ALL",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBRARY_PATH", "LEAN_CC",
        "LEAN_NUM_THREADS", "LEAN_PATH", "LEAN_SRC_PATH", "PATH", "TMPDIR",
        "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
    environment["ELAN_HOME"] = os.path.join(td, "elan")
    environment["ELAN_TOOLCHAIN"] = toolchain
    return dict(
        setup_index=dict(path=os.path.join(td, "setup-index.json"),
                         sha256="e" * 64,
                         schema="v2b_lean_setup_index_v2"),
        corpus_root=td, corpus_git_sha="f" * 40, toolchain=toolchain,
        lean=dict(path=os.path.join(td, "lean"), sha256="1" * 64,
                  version="Lean fixture"),
        driver=dict(path=driver_path, sha256=driver_sha), cwd=td,
        argv_template=[os.path.join(td, "lean"), "--run", driver_path,
                       "<module-manifest.json>"],
        environment=environment,
        environment_sha256=sha256_sorted_json(environment))


def _generator():
    return dict(source_commit="2" * 40, source_tree_hash="3" * 64,
                program="run_v2b_lean_boundary_audit.py")


def _driver_records(fixture, driver_manifest):
    module = dict(
        schema=DRIVER_OUTPUT_SCHEMA, record_type="module",
        invocation_binding=driver_manifest["invocationBinding"],
        module_name=driver_manifest["moduleName"],
        n_spans=len(driver_manifest["spans"]), n_commands_parsed=3,
        trusted_original_commands_elaborated=True,
        sentinels_elaborated=False)
    rows = []
    for span in driver_manifest["spans"]:
        start, end = span["startByte"], span["endByte"]
        base = dict(
            schema=DRIVER_OUTPUT_SCHEMA, record_type="span",
            span_id=span["id"], status="resolved", reason=None,
            start_byte=start, end_byte=end, delimiter=":=",
            syntax_kind="Lean.Parser.Command.declaration",
            sentinels_elaborated=False)
        if (start, end) == fixture["spans"]["tricky"]:
            base.update(
                header_end_byte=fixture["tricky_true_h"],
                n_candidate_starts_total=2, n_tested=2,
                n_untested_after_choice=0,
                rejected_starts=[TRICKY.index(":=")])
        elif (start, end) == fixture["spans"]["plain"]:
            base.update(
                header_end_byte=fixture["plain_h"],
                n_candidate_starts_total=1, n_tested=1,
                n_untested_after_choice=0, rejected_starts=[])
        else:
            base.update(
                header_end_byte=fixture["matchy_h"], delimiter="|",
                n_candidate_starts_total=2, n_tested=1,
                n_untested_after_choice=1, rejected_starts=[])
        rows.append(base)
    return module, rows


def _driver_stdout(module, rows):
    records = [module, *rows]
    return "unmarked trusted output is ignored\n" + "\n".join(
        BOUNDARY_MARKER + json.dumps(record, separators=(",", ":"))
        for record in records) + "\n"


def _result(td, fixture, manifest, manifest_sha, driver_sha,
            tricky_row=None):
    def resolved(span_id, delimiter, h_byte, total, rejected):
        tested = len(rejected) + 1
        return dict(
            span_id=span_id, status="resolved", delimiter=delimiter,
            h_byte=h_byte, reason=None,
            syntax_kind="Lean.Parser.Command.declaration",
            n_candidate_starts_total=total, n_tested=tested,
            n_untested_after_choice=total - tested,
            rejected_starts=list(rejected))

    by_span = {}
    for span in manifest["spans"]:
        start, end = span["start_byte"], span["end_byte"]
        if (start, end) == fixture["spans"]["tricky"]:
            by_span[span["span_id"]] = tricky_row or resolved(
                span["span_id"], ":=", fixture["tricky_true_h"], 2,
                [TRICKY.index(":=")])
        elif (start, end) == fixture["spans"]["plain"]:
            by_span[span["span_id"]] = resolved(
                span["span_id"], ":=", fixture["plain_h"], 1, [])
        else:
            by_span[span["span_id"]] = resolved(
                span["span_id"], "|", fixture["matchy_h"], 2, [])
    rows = [by_span[span["span_id"]] for span in manifest["spans"]]
    toolchain = "leanprover/lean4:v4.33.0-rc2"
    driver_path = os.path.join(td, "driver.lean")
    driver_manifest = build_driver_manifests(
        manifest, driver_path, toolchain)["M.A"]
    module_rows = [by_span[span["id"]]
                   for span in driver_manifest["spans"]]
    module_runs = [dict(
        module_name="M.A",
        invocation_binding=driver_manifest["invocationBinding"],
        n_spans=len(rows), manifest_sha256=sha256_bytes(
            canonical_driver_manifest_bytes(driver_manifest)),
        stdout_sha256="c" * 64, stderr_sha256="d" * 64,
        exit_code=0, rows_sha256=sha256_sorted_json(module_rows),
        evidence_sha256="4" * 64)]
    runtime = _runtime(td, driver_path, driver_sha, toolchain)
    result = dict(
        schema=BOUNDARY_RESULT_SCHEMA, marker=BOUNDARY_MARKER,
        manifest_sha256=manifest_sha, driver_sha256=driver_sha,
        toolchain=toolchain,
        invocation_sha256=compute_invocation_sha256(
            manifest_sha, driver_sha, manifest),
        n_modules=1, n_spans=len(rows), module_runs=module_runs,
        module_runs_sha256=sha256_sorted_json(module_runs), results=rows,
        runtime=runtime, runtime_sha256=sha256_sorted_json(runtime),
        generator=_generator())
    path = os.path.join(td, "result.json")
    json.dump(result, open(path, "w"))
    return result, path


def test_planner_dedupes_sorts_and_requires_explicit_setup():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        manifest, _, _ = _manifest(td, fixture)
        assert manifest["schema"] == BOUNDARY_MANIFEST_SCHEMA
        assert manifest["marker"] == BOUNDARY_MARKER
        assert manifest["n_identities"] == 4
        assert manifest["n_spans"] == 3                  # shared span deduped
        sids = [span["span_id"] for span in manifest["spans"]]
        assert sids == sorted(sids)
        shared = [span for span in manifest["spans"]
                  if len(span["members"]) == 2]
        assert len(shared) == 1
        assert shared[0]["members"] == sorted(shared[0]["members"])
        assert shared[0]["old_split"]["split_kind"] == ":="
        expected_sid = span_id_of("M.A", fixture["source_sha"],
                                  *fixture["spans"]["tricky"])
        assert shared[0]["span_id"] == expected_sid
        assert shared[0]["setup"] == fixture["setup"]
        # missing setup mapping refuses, never guesses
        try:
            build_boundary_manifest(fixture["extraction"], {})
            assert False, "silent setup guess"
        except V2BError as err:
            assert "explicit module setup" in str(err)
        # a shared span with a mismatched old split tuple refuses
        value = json.load(open(fixture["extraction"]))
        value["files"][0]["decls"]["M.A.f_alias"]["header_bytes"] += 1
        value["files"][0]["decls"]["M.A.f_alias"]["body_bytes"] -= 1
        json.dump(value, open(fixture["extraction"], "w"))
        try:
            build_boundary_manifest(fixture["extraction"],
                                    fixture["setup_map"])
            assert False, "old-split disagreement accepted"
        except V2BError as err:
            assert "disagree" in str(err)


def test_consumer_witnesses_delimiters_and_builds_effective_rows():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        manifest, manifest_path, manifest_sha = _manifest(td, fixture)
        driver_path, driver_sha = _driver(td)
        _result(td, fixture, manifest, manifest_sha, driver_sha)
        artifact = build_boundary_artifact(
            manifest_path, os.path.join(td, "result.json"), driver_path)
        assert artifact["schema"] == BOUNDARIES_SCHEMA
        assert artifact["n_identities"] == 4
        assert artifact["n_resolved_spans"] == 3
        assert artifact["n_unsplit_spans"] == 0
        # the tricky span CHANGED: parser boundary is the second :=
        f_key = identity_key("lean", ["M.A", "M.A.f"])
        row = artifact["boundaries"][f_key]
        assert row["changed_vs_v3"] is True
        assert row["split_kind"] == ":="
        assert row["header_bytes"] == fixture["tricky_true_h"]
        assert row["body_bytes"] == \
            fixture["spans"]["tricky"][1] - fixture["tricky_true_h"]
        assert row["old_split"]["header_bytes"] == TRICKY.index(":=")
        # both members of the shared span carry the same effective row
        alias = artifact["boundaries"][
            identity_key("lean", ["M.A", "M.A.f_alias"])]
        assert alias["span_id"] == row["span_id"]
        assert alias["header_bytes"] == row["header_bytes"]
        # the plain span is unchanged
        t_row = artifact["boundaries"][
            identity_key("lean", ["M.A", "M.A.t"])]
        assert t_row["changed_vs_v3"] is False
        assert artifact["n_changed_spans_vs_v3"] == 1
        assert artifact["boundaries_sha256"] == \
            sha256_sorted_json(artifact["boundaries"])
        # deterministic replay comparison
        replay = build_boundary_artifact(
            manifest_path, os.path.join(td, "result.json"), driver_path)
        assert replay_equal(artifact, replay)
        assert not replay_equal(artifact, dict(artifact, repo="other"))


def test_unsplit_rows_are_conservative_and_recorded():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        manifest, manifest_path, manifest_sha = _manifest(td, fixture)
        driver_path, driver_sha = _driver(td)
        tricky_sid = span_id_of("M.A", fixture["source_sha"],
                                *fixture["spans"]["tricky"])
        _result(td, fixture, manifest, manifest_sha, driver_sha,
                tricky_row=dict(span_id=tricky_sid, status="unsplit",
                                delimiter=None, h_byte=None,
                                reason="no-sentinel-valid-candidate",
                                syntax_kind=(
                                    "Lean.Parser.Command.declaration"),
                                n_candidate_starts_total=2,
                                n_tested=2,
                                n_untested_after_choice=0,
                                rejected_starts=[
                                    TRICKY.index(":="),
                                    fixture["tricky_true_h"]]))
        artifact = build_boundary_artifact(
            manifest_path, os.path.join(td, "result.json"), driver_path)
        f_row = artifact["boundaries"][
            identity_key("lean", ["M.A", "M.A.f"])]
        start, end = fixture["spans"]["tricky"]
        assert f_row["split_kind"] is None
        assert f_row["header_bytes"] == end - start
        assert f_row["body_bytes"] == 0
        assert f_row["status"] == "unsplit"
        assert f_row["reason"] == "no-sentinel-valid-candidate"
        assert f_row["changed_vs_v3"] is True
        assert artifact["n_unsplit_spans"] == 1


def test_consumer_fails_closed_on_binding_and_witness_drift():
    def broken(mutate, expect):
        with tempfile.TemporaryDirectory() as td:
            fixture = _fixture(td)
            manifest, manifest_path, manifest_sha = _manifest(td, fixture)
            driver_path, driver_sha = _driver(td)
            result, result_path = _result(td, fixture, manifest,
                                          manifest_sha, driver_sha)
            mutate(td, fixture, result)
            json.dump(result, open(result_path, "w"))
            try:
                build_boundary_artifact(manifest_path, result_path,
                                        driver_path)
                assert False, expect
            except V2BError as err:
                assert expect in str(err), str(err)

    # a resolved h that points at the WRONG := (byte-witness catches it)
    def wrong_h(td, fixture, result):
        for row in result["results"]:
            if row["h_byte"] == fixture["tricky_true_h"]:
                row["h_byte"] = fixture["tricky_true_h"] - 1
    broken(wrong_h, "exact ':=' token")

    # invocation binding must recompute from live bytes
    def bad_invocation(td, fixture, result):
        result["invocation_sha256"] = "0" * 64
    broken(bad_invocation, "invocation binding")

    # order/membership drift
    def swapped(td, fixture, result):
        result["results"][0], result["results"][1] = \
            result["results"][1], result["results"][0]
    broken(swapped, "order/membership")

    # missing row
    def short(td, fixture, result):
        result["results"] = result["results"][:-1]
        result["n_spans"] -= 1
    broken(short, "span count drift")

    # foreign key in a row
    def extra_key(td, fixture, result):
        result["results"][0]["note"] = "leak"
    broken(extra_key, "key drift")

    # a resolved row with a null h is malformed
    def h_start(td, fixture, result):
        row = result["results"][0]
        row["h_byte"] = None
        row["status"] = "resolved"
        row["delimiter"] = ":="
    broken(h_start, "resolved truth-table violation")

    # source drift after result creation breaks the invocation recompute
    def drifted_source(td, fixture, result):
        with open(fixture["source"], "a", encoding="utf-8") as fh:
            fh.write("-- drift\n")
    broken(drifted_source, "live-byte drift")


def test_module_transcript_is_exact_and_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        manifest, _, _ = _manifest(td, fixture)
        driver_path, driver_sha = _driver(td)
        toolchain = "leanprover/lean4:v4.33.0-rc2"
        manifests = build_driver_manifests(
            manifest, driver_path, toolchain)
        assert set(manifests) == {"M.A"}
        driver_manifest = manifests["M.A"]
        assert driver_manifest["schema"] == DRIVER_MANIFEST_SCHEMA
        assert [row["startByte"] for row in driver_manifest["spans"]] == \
            sorted(row["startByte"] for row in driver_manifest["spans"])
        module, rows = _driver_records(fixture, driver_manifest)
        stdout = _driver_stdout(module, rows)
        parsed = parse_driver_stdout(
            stdout, driver_manifest, driver_sha, toolchain)
        assert parsed["module"] == module
        assert len(parsed["rows"]) == 3
        tricky = next(row for row in parsed["rows"]
                      if row["h_byte"] == fixture["tricky_true_h"])
        assert tricky["rejected_starts"] == [TRICKY.index(":=")]

        def refuses(text, fragment):
            try:
                parse_driver_stdout(
                    text, driver_manifest, driver_sha, toolchain)
                assert False, fragment
            except V2BError as err:
                assert fragment in str(err), str(err)

        refuses(BOUNDARY_MARKER + '{"schema":1,"schema":2}\n',
                "duplicate boundary driver JSON key")
        missing = copy.deepcopy(rows)
        missing[0].pop("syntax_kind")
        refuses(_driver_stdout(module, missing), "span record drift")
        extra = copy.deepcopy(rows)
        extra[0]["named_arm"] = "k4"
        refuses(_driver_stdout(module, extra), "span record drift")
        swapped = copy.deepcopy(rows)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        refuses(_driver_stdout(module, swapped), "span record drift")
        impossible = copy.deepcopy(rows)
        impossible[0]["n_tested"] = 0
        refuses(_driver_stdout(module, impossible),
                "truth-table violation")
        wrong_binding = copy.deepcopy(module)
        wrong_binding["invocation_binding"] = "0" * 64
        refuses(_driver_stdout(wrong_binding, rows), "module record drift")
        float_count = copy.deepcopy(module)
        float_count["n_spans"] = float(module["n_spans"])
        refuses(_driver_stdout(float_count, rows), "module record drift")
        boolean_start = copy.deepcopy(rows)
        boolean_start[0]["start_byte"] = \
            boolean_start[0]["start_byte"] == 1
        refuses(_driver_stdout(module, boolean_start), "span record drift")


def test_module_runs_aggregate_in_global_manifest_order():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        manifest, manifest_path, manifest_sha = _manifest(td, fixture)
        driver_path, driver_sha = _driver(td)
        toolchain = "leanprover/lean4:v4.33.0-rc2"
        driver_manifest = build_driver_manifests(
            manifest, driver_path, toolchain)["M.A"]
        module, span_records = _driver_records(fixture, driver_manifest)
        stdout = _driver_stdout(module, span_records)
        runs = {"M.A": dict(
            manifest=driver_manifest,
            manifest_sha256=sha256_bytes(
                canonical_driver_manifest_bytes(driver_manifest)),
            stdout=stdout,
            stderr="", exit_code=0, evidence_sha256="4" * 64)}
        result = aggregate_driver_runs(
            manifest_path, driver_path, toolchain, runs)
        assert result["schema"] == BOUNDARY_RESULT_SCHEMA
        assert result["manifest_sha256"] == manifest_sha
        assert result["driver_sha256"] == driver_sha
        assert result["n_modules"] == 1
        assert result["n_spans"] == manifest["n_spans"]
        assert [row["span_id"] for row in result["results"]] == [
            row["span_id"] for row in manifest["spans"]]
        assert result["module_runs_sha256"] == \
            sha256_sorted_json(result["module_runs"])
        runtime = _runtime(td, driver_path, driver_sha, toolchain)
        result["runtime"] = runtime
        result["runtime_sha256"] = sha256_sorted_json(runtime)
        result["generator"] = _generator()
        result_path = os.path.join(td, "aggregate-result.json")
        json.dump(result, open(result_path, "w"))
        artifact = build_boundary_artifact(
            manifest_path, result_path, driver_path)
        assert artifact["n_resolved_spans"] == 3

        try:
            aggregate_driver_runs(
                manifest_path, driver_path, toolchain, {})
            assert False, "missing module accepted"
        except V2BError as err:
            assert "membership drift" in str(err)
        failed = copy.deepcopy(runs)
        failed["M.A"]["exit_code"] = 1
        try:
            aggregate_driver_runs(
                manifest_path, driver_path, toolchain, failed)
            assert False, "failed driver accepted"
        except V2BError as err:
            assert "process failed" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN BOUNDARY TESTS PASS")
