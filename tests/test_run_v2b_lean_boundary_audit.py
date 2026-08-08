#!/usr/bin/env python3
"""End-to-end fake-runtime test for the requeue-safe boundary runner."""
import json
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prepare_v2b_lean_setups import SETUP_INDEX_SCHEMA
from run_v2b_lean_boundary_audit import run_audit
from v2b_common import (V2BError, artifact_binding, sha256_file,
                        sha256_sorted_json, write_new_json)
from v2b_lean_boundaries import (build_boundary_artifact,
                                 build_boundary_manifest)


FAKE_LEAN = r'''#!/usr/bin/env python3
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("Lean fixture 4.32")
    raise SystemExit(0)
manifest = json.load(open(sys.argv[-1], encoding="utf-8"))
marker = "@@V2B_LEAN_BOUNDARY@@"
module = {
    "schema": "v2b_lean_boundary_driver_output_v1",
    "record_type": "module",
    "invocation_binding": manifest["invocationBinding"],
    "module_name": manifest["moduleName"],
    "n_spans": len(manifest["spans"]),
    "n_commands_parsed": 1,
    "trusted_original_commands_elaborated": True,
    "sentinels_elaborated": False,
}
print(marker + json.dumps(module, separators=(",", ":")))
for span in manifest["spans"]:
    row = {
        "schema": "v2b_lean_boundary_driver_output_v1",
        "record_type": "span", "span_id": span["id"],
        "status": "unsplit", "reason": "no-canonical-candidate",
        "start_byte": span["startByte"], "end_byte": span["endByte"],
        "header_end_byte": None, "delimiter": None,
        "syntax_kind": "Lean.Parser.Command.declaration",
        "n_candidate_starts_total": 0, "n_tested": 0,
        "n_untested_after_choice": 0, "rejected_starts": [],
        "sentinels_elaborated": False,
    }
    print(marker + json.dumps(row, separators=(",", ":")))
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _git(corpus, *args):
    process = subprocess.run(["git", "-C", corpus, *args],
                             capture_output=True, text=True, check=False)
    assert process.returncode == 0, (args, process.stdout, process.stderr)
    return process.stdout.strip()


def _fixture(td):
    corpus = os.path.join(td, "corpus")
    os.makedirs(corpus)
    source = os.path.join(corpus, "A.lean")
    source_text = "axiom a : Nat\n"
    _write(source, source_text)
    toolchain = "leanprover/lean4:v4.32.0"
    toolchain_path = os.path.join(corpus, "lean-toolchain")
    _write(toolchain_path, toolchain + "\n")
    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.email", "fixture@example.test")
    _git(corpus, "config", "user.name", "Fixture")
    _git(corpus, "add", "A.lean", "lean-toolchain")
    _git(corpus, "commit", "-qm", "fixture")
    corpus_sha = _git(corpus, "rev-parse", "HEAD")

    setup = os.path.join(td, "setups", "A.setup.json")
    setup_value = dict(
        dynlibs=[], importArts={"Init": [[source]]}, isModule=True, name="A",
        options={}, plugins=[])
    _write(setup, json.dumps(setup_value))
    extraction = dict(
        schema="v2a_lean_extract_v3", repo="fixture",
        files=[dict(
            module="A", source=source, source_sha256=sha256_file(source),
            decls={"A.a": dict(
                start_byte=0, end_byte=len(source_text.rstrip("\n")),
                header_bytes=len(source_text.rstrip("\n")), body_bytes=0,
                split_kind=None)})])
    extraction_path = os.path.join(td, "extraction.json")
    _write(extraction_path, json.dumps(extraction))
    extraction_binding, _ = artifact_binding(extraction_path)
    manifest = build_boundary_manifest(extraction_path, {source: setup})
    manifest_path = os.path.join(td, "manifest.json")
    _write(manifest_path, json.dumps(manifest))

    lean = os.path.join(td, "bin", "lean")
    _write(lean, FAKE_LEAN)
    os.chmod(lean, os.stat(lean).st_mode | stat.S_IXUSR)
    driver = os.path.join(td, "Driver.lean")
    _write(driver, "-- fixture driver bytes\n")
    setups = {source: setup}
    setup_sha = sha256_file(setup)
    rows = [dict(
        module="A", source=source, source_rel="A.lean",
        source_sha256=sha256_file(source), setup=setup,
        setup_sha256=setup_sha,
        setup_semantics_sha256=sha256_sorted_json(setup_value),
        batch_index=0)]
    batches = [dict(
        batch_index=0, n_modules=1, first_module="A", last_module="A",
        targets_sha256=sha256_sorted_json(["+A:setup"]),
        stdout_sha256="4" * 64, stderr_sha256="5" * 64,
        setup_rows_sha256=sha256_sorted_json([["A", setup_sha]]))]
    artifacts = [dict(
        path=source, sha256=sha256_file(source),
        roles=["import-artifact"])]
    setup_index = dict(
        schema=SETUP_INDEX_SCHEMA, repo="fixture", language="lean",
        corpus_git_sha=corpus_sha,
        extraction=dict(extraction_binding,
                        schema="v2a_lean_extract_v3"),
        corpus_root=corpus, toolchain=toolchain,
        lean_toolchain_sha256=sha256_file(toolchain_path),
        lake=dict(path=lean, sha256=sha256_file(lean),
                  version="Lake fixture"),
        lean=dict(path=lean, sha256=sha256_file(lean),
                  version="Lean fixture 4.32"),
        n_modules=1, n_batches=1, batch_size=1,
        setups=setups, setups_sha256=sha256_sorted_json(setups),
        rows=rows, rows_sha256=sha256_sorted_json(rows),
        batches=batches, batches_sha256=sha256_sorted_json(batches),
        n_artifacts=1, artifacts=artifacts,
        artifacts_sha256=sha256_sorted_json(artifacts),
        generator=dict(
            source_commit="6" * 40, source_tree_hash="7" * 64,
            program="prepare_v2b_lean_setups.py"))
    setup_index_path = os.path.join(td, "setup-index.json")
    write_new_json(setup_index_path, setup_index)
    return dict(manifest=manifest_path, setup_index=setup_index_path,
                driver=driver, corpus=corpus)


def test_runner_publishes_and_strictly_reuses_module_evidence():
    with tempfile.TemporaryDirectory() as td:
        fixture = _fixture(td)
        run_dir = os.path.join(td, "runs")
        first, reused = run_audit(
            fixture["manifest"], fixture["setup_index"],
            fixture["driver"], os.path.join(td, "elan"), run_dir,
            workers=2, timeout=30)
        assert reused == 0
        assert first["n_modules"] == 1
        assert first["n_spans"] == 1
        assert first["results"][0]["status"] == "unsplit"
        assert first["runtime_sha256"] == \
            sha256_sorted_json(first["runtime"])
        published = dict(first)
        published["generator"] = dict(
            source_commit="2" * 40, source_tree_hash="3" * 64,
            program="run_v2b_lean_boundary_audit.py")
        result_path = os.path.join(td, "result.json")
        write_new_json(result_path, published)
        artifact = build_boundary_artifact(
            fixture["manifest"], result_path, fixture["driver"])
        assert artifact["n_unsplit_spans"] == 1

        second, reused = run_audit(
            fixture["manifest"], fixture["setup_index"],
            fixture["driver"], os.path.join(td, "elan"), run_dir,
            workers=1, timeout=30)
        assert reused == 1
        assert second == first

        module_dirs = [os.path.join(run_dir, "modules", name)
                       for name in os.listdir(os.path.join(run_dir, "modules"))]
        assert len(module_dirs) == 1
        evidence_path = os.path.join(module_dirs[0], "evidence.json")
        evidence_blob = open(evidence_path, "rb").read()
        evidence = json.loads(evidence_blob)
        evidence["cwd"] = td
        with open(evidence_path, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=1, sort_keys=True)
            handle.write("\n")
        try:
            run_audit(
                fixture["manifest"], fixture["setup_index"],
                fixture["driver"], os.path.join(td, "elan"), run_dir,
                workers=1, timeout=30)
            assert False, "tampered module invocation evidence was reused"
        except V2BError as err:
            assert "binding drift" in str(err), str(err)
        with open(evidence_path, "wb") as handle:
            handle.write(evidence_blob)

        stdout = os.path.join(module_dirs[0], "stdout.txt")
        with open(stdout, "a", encoding="utf-8") as handle:
            handle.write("tamper\n")
        try:
            run_audit(
                fixture["manifest"], fixture["setup_index"],
                fixture["driver"], os.path.join(td, "elan"), run_dir,
                workers=1, timeout=30)
            assert False, "tampered immutable module evidence was reused"
        except V2BError as err:
            assert "binding drift" in str(err), str(err)


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
    print("V2B LEAN BOUNDARY RUNNER TESTS PASS")
