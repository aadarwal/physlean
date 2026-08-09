#!/usr/bin/env python3
"""Adversarial tests for the oracle-safe two-process S5 architecture.

Every case runs real Lean under a pinned toolchain.  Bodies are written so
that SUCCESS means the attack worked: an honest body verifies through both
processes, and each attack must end in a recorded verification failure.  Where
an attack could fail for a boring reason, a positive control runs the same
mechanism in a position where it is expected to succeed.

Run: python3 tests/test_v2b_oracle_safe_probe.py
"""
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_common import V2BError
from v2b_lean_frames import (SUFFIX_FRAME_ROLES, TARGET_FRAME_ROLES,
                             blank_region, build_views, channel_payload)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(ROOT, "lean_drivers", "V2BOracleSafeProbe.lean")
MANIFEST_SCHEMA = "v2b_lean_oracle_probe_manifest_v2"
MARKER_PREFIX = "@@V2B_ORACLE_PROBE:"
MARKER_SUFFIX = "@@"
TOOLCHAIN_432 = "leanprover/lean4:v4.32.0"
TOOLCHAIN_433 = "leanprover/lean4:v4.33.0-rc2"
NONCE = "5c" * 32
LOGICAL_FILE = "/v2b-logical-repo/Probe/Target.lean"

# A trusted prefix metaprogram that persists whatever its FileMap exposes.
# `elabCommand` inside `run_cmd` does not persist in these toolchains, but
# `addAndCompile` does, so this is a real persistence channel.
STASH_COMMAND = (
    "open Lean Elab Command in\n"
    "run_cmd do\n"
    "  let context ← readThe Lean.Elab.Command.Context\n"
    "  let n := context.fileMap.source.utf8ByteSize\n"
    "  Lean.Elab.Command.liftCoreM <| Lean.addAndCompile (.defnDecl {\n"
    "    name := `stashedLen\n"
    "    levelParams := []\n"
    "    type := .const ``Nat []\n"
    "    value := Lean.mkNatLit n\n"
    "    hints := .abbrev\n"
    "    safety := .safe })\n"
)
# A candidate that installs global simp state naming a PRE-EXISTING lemma, so
# it adds no new constant of its own and the bundle stays clean.
SIMP_INJECT_BODY = (
    "by run_tac do\n"
    "  Lean.Meta.addSimpTheorem (Lean.Meta.simpExtension) `bigValEq "
    "true false .global 1000\n"
    "  Lean.Elab.Tactic.evalTactic (<- `(tactic| exact 41))"
)
# A candidate that creates a constant outside the committed target namespace.
FOREIGN_CONST_BODY = (
    "by run_tac do\n"
    "  Lean.addDecl (.defnDecl {\n"
    "    name := `injected\n"
    "    levelParams := []\n"
    "    type := .const ``Nat []\n"
    "    value := Lean.mkNatLit 1\n"
    "    hints := .abbrev\n"
    "    safety := .safe })\n"
    "  Lean.Elab.Tactic.evalTactic (<- `(tactic| exact 41))"
)


def _toolchain(name):
    elan = shutil.which("elan")
    if elan is None:
        return None
    listed = subprocess.run([elan, "toolchain", "list"], capture_output=True,
                            text=True, check=False)
    return elan if name in listed.stdout else None


def _records(stdout):
    marker = f"{MARKER_PREFIX}{NONCE}{MARKER_SUFFIX}"
    return [json.loads(line[len(marker):])
            for line in stdout.splitlines() if line.startswith(marker)]


def _invoke(manifest, sources, roles, toolchain, payload=None):
    elan = _toolchain(toolchain)
    if elan is None:
        return None, []
    with tempfile.TemporaryDirectory() as td:
        manifest_path = os.path.join(td, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        if payload is None:
            payload = channel_payload(
                NONCE, {role: sources[role].encode("utf-8")
                        for role in roles}, roles)
        assert not os.path.exists(LOGICAL_FILE), LOGICAL_FILE
        result = subprocess.run(
            [elan, "run", toolchain, "lean", "--run", DRIVER, manifest_path],
            input=payload, capture_output=True, cwd=ROOT, timeout=900,
            check=False)
    return result, _records(result.stdout.decode("utf-8", errors="replace"))


def _row(records, record_type):
    rows = [row for row in records if row.get("record_type") == record_type]
    assert len(rows) == 1, (record_type, records)
    return rows[0]


class Case:
    """One module split into the exact byte offsets the architecture needs."""

    def __init__(self, prefix, body, suffix, target_name="target",
                 target_kind="def", header="def target : Nat := "):
        self.text = prefix + header + body + suffix
        blob = self.text.encode("utf-8")
        self.target_start = len(prefix.encode("utf-8"))
        self.header_end = len((prefix + header).encode("utf-8"))
        self.retained_end = len((prefix + header + body).encode("utf-8"))
        self.total = len(blob)
        self.target_name = target_name
        self.target_kind = target_kind
        self.views = build_views(self.text, self.target_start, self.header_end,
                                 self.retained_end)


def verify(case, toolchain=TOOLCHAIN_432):
    """Run the full two-process flow; returns (target_row, suffix_row)."""
    base = dict(schema=MANIFEST_SCHEMA, logicalFile=LOGICAL_FILE,
                targetName=case.target_name, targetKind=case.target_kind,
                targetStartByte=case.target_start,
                headerEndByte=case.header_end,
                retainedEndByte=case.retained_end)
    result, records = _invoke(dict(base, mode="target"), case.views,
                              TARGET_FRAME_ROLES, toolchain)
    if result is None:
        return None, None
    assert result.returncode == 0, result.stderr.decode()[-2500:]
    target_row = _row(records, "target")
    if target_row["status"] != "verified":
        return target_row, None
    sources = dict(case.views,
                   bundle=json.dumps(target_row["bundle"], sort_keys=True))
    result, records = _invoke(dict(base, mode="suffix"), sources,
                              SUFFIX_FRAME_ROLES, toolchain)
    assert result.returncode == 0, result.stderr.decode()[-2500:]
    return target_row, _row(records, "suffix")


# --------------------------------------------------------------- pure views

def test_views_and_blanking_preserve_offsets_and_hide_bytes():
    text = "import Lean\n-- π ∀ 🎯\ndef target : Nat := 41\n-- TAIL\n"
    start = text.index("def target")
    start = len(text[:start].encode("utf-8"))
    retained = len(text[:text.index("\n-- TAIL")].encode("utf-8"))
    header_end = len(text[:text.index("41")].encode("utf-8"))
    views = build_views(text, start, header_end, retained)
    assert views["prefix"] == text[:text.index("def target")]
    assert views["target"].endswith("41")
    assert "TAIL" in views["suffix"] and "41" not in views["suffix"]
    # Byte offsets and line structure survive blanking exactly.
    assert len(views["suffix"].encode("utf-8")) == len(text.encode("utf-8"))
    assert views["suffix"].count("\n") == text.count("\n")
    masked = blank_region("aπ∀🎯\t\r\n")
    assert len(masked.encode("utf-8")) == len("aπ∀🎯\t\r\n".encode("utf-8"))
    assert len(masked) == len("aπ∀🎯\t\r\n")
    assert masked.endswith("\t\r\n")
    for offset in (start + 1,):
        pass
    try:                                     # split multibyte char refused
        build_views("π\ndef x := 1\n", 1, 4, 9)
        assert False, "offset splitting a UTF-8 character accepted"
    except V2BError as err:
        assert "UTF-8" in str(err) or "range" in str(err)


# ------------------------------------------------------------ happy path

def test_honest_candidate_verifies_through_both_processes():
    case = Case("import Lean\ndef helper : Nat := 7\n", "41",
                "\ntheorem after : target = 41 := rfl\n")
    target_row, suffix_row = verify(case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert target_row["n_bundled_constants"] >= 1
    assert target_row["bundle"][1] == "target"
    assert suffix_row["status"] == "verified", suffix_row
    assert suffix_row["n_replayed_constants"] >= 1
    assert suffix_row["n_suffix_commands"] == 1


def test_wrong_body_still_fails_at_the_trusted_suffix():
    case = Case("import Lean\ndef helper : Nat := 7\n", "42",
                "\ntheorem after : target = 41 := rfl\n")
    target_row, suffix_row = verify(case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verification-failure", suffix_row
    assert suffix_row["reason"] == "suffix-elaboration-error", suffix_row


# -------------------------------------------------- prefix persistence

def test_prefix_metaprogram_cannot_persist_future_bytes():
    """The stash sees the prefix view only, never the body or suffix."""
    prefix = "import Lean\n" + STASH_COMMAND
    case = Case(prefix, "stashedLen",
                "\ntheorem after : target = 0 := rfl\n")
    prefix_bytes = case.target_start
    # Pin the observed value two ways: it must equal the prefix length and
    # must NOT equal the whole-module length.
    exact = Case(prefix, "stashedLen",
                 f"\ntheorem after : target = {prefix_bytes} := rfl\n")
    leaked = Case(prefix, "stashedLen",
                  f"\ntheorem after : target = {case.total} := rfl\n")
    exact_target, exact_suffix = verify(exact)
    if exact_target is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert exact_target["status"] == "verified", exact_target
    assert exact_suffix["status"] == "verified", exact_suffix
    leaked_target, leaked_suffix = verify(leaked)
    assert leaked_target["status"] == "verified", leaked_target
    assert leaked_suffix["status"] == "verification-failure", leaked_suffix


# ---------------------------------------------- candidate state isolation

def test_candidate_simp_state_does_not_reach_the_suffix():
    prefix = ("import Lean\naxiom bigVal : Nat\naxiom bigValEq : "
              "bigVal = 7\n")
    suffix = "\ntheorem needsSimp : bigVal = 7 := by simp\n"
    case = Case(prefix, SIMP_INJECT_BODY, suffix)
    target_row, suffix_row = verify(case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verification-failure", suffix_row
    assert suffix_row["reason"] == "suffix-elaboration-error", suffix_row

    # Positive control: the SAME suffix succeeds when the simp lemma is
    # trusted prefix state, proving the failure above is the process
    # boundary and not an unprovable goal.
    control_prefix = ("import Lean\naxiom bigVal : Nat\n@[simp] axiom "
                      "bigValEq : bigVal = 7\n")
    control = Case(control_prefix, "41", suffix)
    control_target, control_suffix = verify(control)
    assert control_target["status"] == "verified", control_target
    assert control_suffix["status"] == "verified", control_suffix


def test_suffix_cannot_execute_replayed_candidate_runtime():
    """The continuation transports kernel declarations, never candidate IR."""
    io_case = Case(
        "import Lean\n", "pure 41", "\n#eval target\n",
        header="def target : IO Nat := ")
    target_row, suffix_row = verify(io_case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verification-failure", suffix_row
    assert suffix_row["reason"] == "suffix-elaboration-error", suffix_row

    core_case = Case(
        "import Lean\n", "pure 41",
        ("\nrun_cmd do\n"
         "  let n ← target\n"
         "  unless n == 41 do throwError \"bad runtime value\"\n"),
        header="def target : Lean.CoreM Nat := ")
    target_row, suffix_row = verify(core_case)
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verification-failure", suffix_row
    assert suffix_row["reason"] == "suffix-elaboration-error", suffix_row


def test_candidate_filemap_and_logical_path_hide_the_suffix():
    canary = "V2B_UNREADABLE_SUFFIX_5E71"
    body = (
        "by\n"
        "  run_tac\n"
        "    let source := (← Lean.getFileMap).source\n"
        "    let canary := \"V2B_UNREADABLE_\" ++ \"SUFFIX_5E71\"\n"
        "    if source.contains canary then\n"
        "      throwError \"suffix leaked through FileMap\"\n"
        "    let visible ← Lean.Core.liftIOCore <| "
        f"System.FilePath.pathExists {json.dumps(LOGICAL_FILE)}\n"
        "    if visible then throwError \"logical source path is visible\"\n"
        "  exact 41")
    case = Case("import Lean\n", body,
                "\ntheorem after : target = 41 := rfl\n"
                f"-- {canary}\n")
    target_row, suffix_row = verify(case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row


def test_safe_auxiliary_constant_is_normalized_and_replayed():
    case = Case("import Lean\ndef helper : Nat := 7\n", FOREIGN_CONST_BODY,
                "\ntheorem after : target = 41 := rfl\n"
                "theorem auxiliarySurvives : injected = 1 := rfl\n")
    target_row, suffix_row = verify(case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert target_row["n_bundled_constants"] >= 2, target_row
    assert suffix_row["status"] == "verified", suffix_row


# ------------------------------------------------------- codec coverage

def test_where_universe_and_utf8_bundles_replay():
    where_case = Case(
        "import Lean\n", "aux where aux : Nat := 41",
        "\ntheorem after : target = 41 := rfl\n")
    target_row, suffix_row = verify(where_case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    # `where` creates a namespaced auxiliary that must cross in the bundle.
    assert target_row["n_bundled_constants"] >= 2, target_row
    assert suffix_row["status"] == "verified", suffix_row

    universe_case = Case(
        "import Lean\nuniverse u\n", "a",
        "\ndef afterUse : Nat -> Nat := fun x => target x\n",
        header="def target {α : Sort u} (a : α) : α := ")
    target_row, suffix_row = verify(universe_case)
    assert target_row["status"] == "verified", target_row
    levels = target_row["bundle"][2][0][2]
    assert levels and levels != [[]], target_row["bundle"][2][0]
    assert suffix_row["status"] == "verified", suffix_row

    utf8_case = Case(
        "import Lean\n-- π ∀ 🎯 prefix\ndef helper : Nat := 7\n",
        "41 -- λ ∑ 🎯 body",
        "\ntheorem after : target = 41 := rfl\n-- ∎ π\n")
    target_row, suffix_row = verify(utf8_case)
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row

    struct_case = Case(
        "import Lean\nstructure Wrap where val : Nat\n",
        "match (Wrap.mk 41) with | ⟨v⟩ => v",
        "\ntheorem after : target = 41 := rfl\n")
    target_row, suffix_row = verify(struct_case)
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row


def test_codec_preserves_named_binders_and_dependent_types():
    named_case = Case(
        "import Lean\n", "named",
        "\ndef afterNamed : Nat := target (named := 41)\n",
        header="def target (named : Nat) : Nat := ")
    target_row, suffix_row = verify(named_case)
    if target_row is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row

    projection_case = Case(
        "import Lean\nstructure Pack where\n  α : Type\n  x : α\n",
        "p.x", "\ndef afterProj (p : Pack) : p.α := target p\n",
        header="def target (p : Pack) : p.α := ")
    target_row, suffix_row = verify(projection_case)
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row
    encoded = json.dumps(target_row["bundle"], ensure_ascii=False)
    assert "Pack" in encoded and "α" in encoded, target_row["bundle"]


# ------------------------------------------------------------- channel

def test_channel_and_bundle_decoding_fail_closed():
    case = Case("import Lean\n", "41",
                "\ntheorem after : target = 41 := rfl\n")
    base = dict(schema=MANIFEST_SCHEMA, logicalFile=LOGICAL_FILE,
                targetName="target", targetKind="def",
                targetStartByte=case.target_start,
                headerEndByte=case.header_end,
                retainedEndByte=case.retained_end)
    sources = {role: case.views[role].encode("utf-8")
               for role in TARGET_FRAME_ROLES}
    unauthorized = channel_payload(NONCE, sources, TARGET_FRAME_ROLES,
                                   authorize=False)
    result, records = _invoke(dict(base, mode="target"), case.views,
                              TARGET_FRAME_ROLES, TOOLCHAIN_432,
                              payload=unauthorized)
    if result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    assert result.returncode != 0
    assert not [r for r in records if r.get("record_type") == "target"]

    # The suffix process refuses a bundle that no longer carries the committed
    # target even if the target process were bypassed entirely.
    target_row, _ = verify(case)
    forged = json.loads(json.dumps(target_row["bundle"]))
    forged[2][0][1] = [["s", "injected"]]
    forged_sources = dict(case.views,
                          bundle=json.dumps(forged, sort_keys=True))
    result, _ = _invoke(dict(base, mode="suffix"), forged_sources,
                        SUFFIX_FRAME_ROLES, TOOLCHAIN_432)
    assert result.returncode != 0
    assert "committed target" in result.stderr.decode()


def test_target_phase_waits_for_observed_go_before_generated_work():
    elan = _toolchain(TOOLCHAIN_432)
    if elan is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    with tempfile.TemporaryDirectory() as td:
        sentinel = os.path.join(td, "target-ran")
        body = (
            "by\n"
            "  run_tac\n"
            "    Lean.Core.liftIOCore <| IO.FS.writeFile "
            f"{json.dumps(sentinel)} \"ran\"\n"
            "  exact 41")
        case = Case("import Lean\n", body,
                    "\ntheorem after : target = 41 := rfl\n")
        manifest = dict(schema=MANIFEST_SCHEMA, mode="target",
                        logicalFile=LOGICAL_FILE, targetName="target",
                        targetKind="def", targetStartByte=case.target_start,
                        headerEndByte=case.header_end,
                        retainedEndByte=case.retained_end)
        manifest_path = os.path.join(td, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        sources = {role: case.views[role].encode("utf-8")
                   for role in TARGET_FRAME_ROLES}
        before_go = channel_payload(NONCE, sources, TARGET_FRAME_ROLES,
                                    authorize=False)
        process = subprocess.Popen(
            [elan, "run", TOOLCHAIN_432, "lean", "--run", DRIVER,
             manifest_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=ROOT, start_new_session=True)
        prefix = b""
        try:
            process.stdin.write(before_go)
            process.stdin.flush()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                ready, _, _ = select.select([process.stdout], [], [], 0.25)
                if ready:
                    prefix += os.read(process.stdout.fileno(), 65536)
                    complete = prefix[:prefix.rfind(b"\n") + 1]
                    records = _records(complete.decode("utf-8", errors="replace"))
                    if any(row.get("record_type") == "phase-start"
                           for row in records):
                        break
                assert process.poll() is None, process.stderr.read().decode()
            else:
                raise AssertionError("driver produced no flushed phase-start")
            assert not os.path.exists(sentinel)
            assert process.poll() is None
            process.stdin.write(f"GO:{NONCE}\n".encode("ascii"))
            process.stdin.close()
            process.stdin = None
            tail_out, stderr = process.communicate(timeout=60)
            stdout = prefix + tail_out
            assert process.returncode == 0, stderr.decode()
            assert os.path.exists(sentinel)
            records = _records(stdout.decode("utf-8", errors="replace"))
            assert [row["record_type"] for row in records] == [
                "prevalidation", "phase-start", "phase-go-accepted", "target"]
            assert records[-1]["status"] == "verified"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def test_bundle_codec_rejects_noncanonical_and_untrusted_rows():
    case = Case("import Lean\n", "41",
                "\ntheorem after : target = 41 := rfl\n")
    base = dict(schema=MANIFEST_SCHEMA, logicalFile=LOGICAL_FILE,
                targetName="target", targetKind="def",
                targetStartByte=case.target_start,
                headerEndByte=case.header_end,
                retainedEndByte=case.retained_end)
    target_result, target_records = _invoke(
        dict(base, mode="target"), case.views, TARGET_FRAME_ROLES,
        TOOLCHAIN_432)
    if target_result is None:
        print("    [skip] pinned Lean 4.32 toolchain is not installed")
        return
    bundle = _row(target_records, "target")["bundle"]

    corruptions = {}
    extra_top = deepcopy(bundle)
    extra_top.append("extra")
    corruptions["exact tagged-array"] = extra_top

    extra_constant_field = deepcopy(bundle)
    extra_constant_field[2][0].append("extra")
    corruptions["malformed or forbidden"] = extra_constant_field

    duplicate = deepcopy(bundle)
    duplicate[2].append(deepcopy(duplicate[2][0]))
    corruptions["duplicate constant"] = duplicate

    preexisting = deepcopy(bundle)
    cloned = deepcopy(preexisting[2][0])
    cloned[1] = [["s", "Nat"]]
    preexisting[2].append(cloned)
    corruptions["not fresh"] = preexisting

    loose = deepcopy(bundle)
    loose[2][0][4] = ["bvar", 9]
    corruptions["loose-bound"] = loose

    undeclared_level = deepcopy(bundle)
    undeclared_level[2][0][3] = ["sort", ["param", [["s", "evil"]]]]
    corruptions["undeclared universe"] = undeclared_level

    oversized_hint = deepcopy(bundle)
    oversized_hint[2][0][5] = ["regular", 2**32]
    corruptions["exceeds UInt32"] = oversized_hint

    for expected, forged in corruptions.items():
        sources = dict(case.views, bundle=json.dumps(forged, sort_keys=True))
        result, records = _invoke(dict(base, mode="suffix"), sources,
                                  SUFFIX_FRAME_ROLES, TOOLCHAIN_432)
        assert result.returncode != 0, (expected, records)
        assert expected in result.stderr.decode(), (expected,
                                                     result.stderr.decode())
        assert not [row for row in records
                    if row.get("record_type") == "suffix"]


def test_second_pinned_toolchain_agrees():
    honest = Case("import Lean\ndef helper : Nat := 7\n", "41",
                  "\ntheorem after : target = 41 := rfl\n")
    target_row, suffix_row = verify(honest, toolchain=TOOLCHAIN_433)
    if target_row is None:
        print("    [skip] pinned Lean 4.33.0-rc2 toolchain is not installed")
        return
    assert target_row["status"] == "verified", target_row
    assert suffix_row["status"] == "verified", suffix_row

    prefix = "import Lean\n" + STASH_COMMAND
    probe = Case(prefix, "stashedLen", "\ntheorem after : target = 0 := rfl\n")
    exact = Case(prefix, "stashedLen",
                 f"\ntheorem after : target = {probe.target_start} := rfl\n")
    exact_target, exact_suffix = verify(exact, toolchain=TOOLCHAIN_433)
    assert exact_target["status"] == "verified", exact_target
    assert exact_suffix["status"] == "verified", exact_suffix


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"[ok] {name}")
    print("V2B ORACLE SAFE PROBE TESTS PASS")
