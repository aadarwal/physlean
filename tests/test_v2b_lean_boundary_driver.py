#!/usr/bin/env python3
"""Pinned-Lean integration tests for the corpus body-boundary driver."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_common import V2BError


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(ROOT, "lean_drivers", "V2BLeanBoundaryAudit.lean")
TOOLCHAIN = "leanprover/lean4:v4.32.0"
TOOLCHAIN_433 = "leanprover/lean4:v4.33.0-rc2"
MANIFEST_SCHEMA = "v2b_lean_boundary_driver_manifest_v1"
OUTPUT_SCHEMA = "v2b_lean_boundary_driver_output_v1"
MARKER = "@@V2B_LEAN_BOUNDARY@@"
MODULE_KEYS = {
    "schema", "record_type", "invocation_binding", "module_name",
    "n_spans", "n_commands_parsed",
    "trusted_original_commands_elaborated", "sentinels_elaborated",
}
SPAN_KEYS = {
    "schema", "record_type", "span_id", "status", "reason",
    "start_byte", "end_byte", "header_end_byte", "delimiter",
    "syntax_kind", "n_candidate_starts_total", "n_tested",
    "n_untested_after_choice", "rejected_starts",
    "sentinels_elaborated",
}


def _lean_runner(toolchain=TOOLCHAIN):
    elan = shutil.which("elan")
    if elan is None:
        return None
    listed = subprocess.run([elan, "toolchain", "list"],
                            capture_output=True, text=True, check=False)
    return elan if toolchain in listed.stdout else None


def _loads_exact(payload):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise V2BError(f"duplicate driver JSON key {key!r}")
            value[key] = item
        return value
    return json.loads(payload, object_pairs_hook=no_duplicates)


def _records(stdout):
    rows = []
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            rows.append(_loads_exact(line[len(MARKER):]))
    return rows


def _command_span(source, start_marker, next_marker=None):
    start = source.index(start_marker)
    end = source.index(next_marker, start) - 1 if next_marker else \
        len(source.rstrip(b"\n"))
    return start, end


def _invoke(elan, source, spans, module="V2BBoundary", toolchain=TOOLCHAIN,
            is_module=False):
    with tempfile.TemporaryDirectory() as td:
        source_path = os.path.join(td, "Original.lean")
        with open(source_path, "wb") as handle:
            handle.write(source)
        setup_path = os.path.join(td, "setup.json")
        with open(setup_path, "w", encoding="utf-8") as handle:
            json.dump(dict(dynlibs=[], importArts={}, isModule=is_module,
                           name=module, options={}, plugins=[]), handle)
        manifest = dict(
            schema=MANIFEST_SCHEMA, invocationBinding="a" * 64,
            originalFile=source_path, moduleSetupFile=setup_path,
            moduleName=module, optionOverrides=[], spans=spans)
        manifest_path = os.path.join(td, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        return subprocess.run(
            [elan, "run", toolchain, "lean", "--run", DRIVER,
             manifest_path], cwd=ROOT, capture_output=True, text=True,
            timeout=180, check=False)


def test_real_driver_selects_earliest_sentinel_valid_body_slot():
    elan = _lean_runner()
    if elan is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    source = (
        "import Lean\n"
        "def tricky : let n := 1; Nat := 0\n"
        "def outer : Nat := let n := 1; n\n"
        "def equations : Nat → Nat\n"
        "  | 0 => 1\n"
        "  | _ => 2\n"
        "axiom noBody : Nat\n"
        "structure S where\n"
        "  x : Nat\n"
        "def structBody : S where\n"
        "  x := 1\n"
    ).encode("utf-8")
    tricky = _command_span(source, b"def tricky", b"def outer")
    outer = _command_span(source, b"def outer", b"def equations")
    equations = _command_span(source, b"def equations", b"axiom noBody")
    no_body = _command_span(source, b"axiom noBody", b"structure S")
    struct_body = _command_span(source, b"def structBody")
    nested_start = source.index(b"let n", tricky[0])
    nested = (nested_start, source.index(b"; Nat", nested_start))
    spans = []
    for name, (start, end) in (
            ("tricky", tricky), ("nested", nested), ("outer", outer),
            ("equations", equations), ("no-body", no_body),
            ("struct-body", struct_body)):
        spans.append(dict(id=name, startByte=start, endByte=end))
    spans.sort(key=lambda row: (row["startByte"], row["endByte"]))
    result = _invoke(elan, source, spans)
    assert result.returncode == 0, (result.stdout, result.stderr)
    records = _records(result.stdout)
    assert len(records) == len(spans) + 1
    module_row, span_rows = records[0], records[1:]
    assert set(module_row) == MODULE_KEYS
    assert module_row["schema"] == OUTPUT_SCHEMA
    assert module_row["record_type"] == "module"
    assert module_row["n_spans"] == len(spans)
    assert module_row["n_commands_parsed"] >= 6
    assert module_row["trusted_original_commands_elaborated"] is True
    assert module_row["sentinels_elaborated"] is False
    assert all(set(row) == SPAN_KEYS for row in span_rows)
    assert [row["span_id"] for row in span_rows] == \
        [row["id"] for row in spans]
    by_id = {row["span_id"]: row for row in span_rows}

    inner = source.index(b":=", tricky[0])
    true_body = source.index(b":=", inner + 2)
    assert by_id["tricky"]["status"] == "resolved"
    assert by_id["tricky"]["header_end_byte"] == true_body
    assert by_id["tricky"]["delimiter"] == ":="
    assert by_id["tricky"]["rejected_starts"] == [inner]
    assert by_id["tricky"]["n_tested"] == 2

    outer_body = source.index(b":=", outer[0])
    assert by_id["outer"]["header_end_byte"] == outer_body
    assert by_id["outer"]["n_tested"] == 1
    assert by_id["outer"]["n_untested_after_choice"] >= 1
    assert by_id["outer"]["rejected_starts"] == []

    assert by_id["equations"]["delimiter"] == "|"
    assert by_id["equations"]["header_end_byte"] == \
        source.index(b"|", equations[0])
    assert by_id["struct-body"]["delimiter"] == "where"
    assert by_id["struct-body"]["header_end_byte"] == \
        source.index(b"where", struct_body[0])

    assert by_id["no-body"]["status"] == "unsplit"
    assert by_id["no-body"]["reason"] == "no-canonical-candidate"
    assert by_id["no-body"]["header_end_byte"] is None
    assert by_id["nested"]["status"] == "unsplit"
    assert by_id["nested"]["reason"] == "not-exact-command-span"
    assert all(row["sentinels_elaborated"] is False for row in span_rows)


def test_real_driver_settles_scoped_async_trusted_state():
    source = (
        "import Lean\n"
        "set_option Elab.async true in\n"
        "theorem prior : True := by trivial\n"
        "theorem target : True := by trivial\n"
    ).encode("utf-8")
    target = _command_span(source, b"theorem target")
    for toolchain in (TOOLCHAIN, TOOLCHAIN_433):
        elan = _lean_runner(toolchain)
        if elan is None:
            print(f"    [skip] pinned {toolchain} is not installed")
            continue
        result = _invoke(elan, source, [dict(
            id="target", startByte=target[0], endByte=target[1])],
            module="V2BBoundaryAsync", toolchain=toolchain)
        assert result.returncode == 0, (
            toolchain, result.stdout, result.stderr)
        records = _records(result.stdout)
        assert len(records) == 2
        assert records[1]["span_id"] == "target"
        assert records[1]["status"] == "resolved"


def test_real_driver_rejects_settled_async_errors():
    elan = _lean_runner()
    if elan is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    source = (
        "import Lean\n"
        "set_option Elab.async true in\n"
        "theorem prior : False := by trivial\n"
        "theorem target : True := by trivial\n"
    ).encode("utf-8")
    target = _command_span(source, b"theorem target")
    result = _invoke(elan, source, [dict(
        id="target", startByte=target[0], endByte=target[1])],
        module="V2BBoundaryAsyncError")
    assert result.returncode != 0
    assert "asynchronous error" in result.stderr
    assert _records(result.stdout) == []


def test_real_driver_preserves_scoped_private_access_in_module_file():
    elan = _lean_runner(TOOLCHAIN_433)
    if elan is None:
        print("    [skip] pinned Lean 4.33 toolchain is not installed")
        return
    source = (
        "module\n"
        "public import Lean\n"
        "@[expose] public section\n"
        "structure Witness where\n"
        "  proof : True\n"
        "set_option backward.privateInPublic true in\n"
        "private theorem hidden : True := by trivial\n"
        "set_option backward.privateInPublic true in\n"
        "set_option backward.privateInPublic.warn false in\n"
        "def pairing : Witness where\n"
        "  proof := hidden\n"
        "theorem target : True := pairing.proof\n"
    ).encode("utf-8")
    target = _command_span(source, b"theorem target")
    result = _invoke(
        elan, source, [dict(
            id="target", startByte=target[0], endByte=target[1])],
        module="V2BBoundaryPrivate", toolchain=TOOLCHAIN_433,
        is_module=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    records = _records(result.stdout)
    assert len(records) == 2
    assert records[1]["status"] == "resolved"

    first = source.index(b"set_option backward.privateInPublic true")
    second = source.index(b"set_option backward.privateInPublic true",
                          first + 1)
    line_end = source.index(b"\n", second) + 1
    without_override = source[:second] + source[line_end:]
    target = _command_span(without_override, b"theorem target")
    rejected = _invoke(
        elan, without_override, [dict(
            id="target", startByte=target[0], endByte=target[1])],
        module="V2BBoundaryPrivateRejected", toolchain=TOOLCHAIN_433,
        is_module=True)
    assert rejected.returncode != 0
    assert "asynchronous error" in rejected.stderr
    assert _records(rejected.stdout) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B LEAN BOUNDARY DRIVER TESTS PASS")
