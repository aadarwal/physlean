#!/usr/bin/env python3
"""NON-EVIDENCE dry-run seams for the S5 four-phase launcher.

This module owns everything the ``--dry-run`` launcher path substitutes for
real production dependencies, so the substitution surface is one explicit,
reviewable file:

* a protocol-faithful STUB Lean driver — a ``/bin/sh`` + Python pair installed
  as ``bin/lean`` in a stub toolchain — that speaks the exact framed-stdin /
  nonce / GO / marker protocol of ``lean_drivers/V2BOracleSafeProbe.lean``
  and derives verified/failure records deterministically from frame bytes;
* a toy Lean workspace + broad-setup-index fixture builder, so
  ``v2b_s5_visibility.produce_visibility`` and the full corpus-integration
  join run unmodified against synthetic files; and
* a deterministic stub generation table ("the model call"), so the launcher's
  orchestration is exercised without a GPU.

Nothing produced through these seams is oracle-isolation or behavioral
evidence.  The stub toolchain is consumed through the four-phase runner's
existing ``none-test-only`` backend, which is opt-in at the Python API; every
artifact the launcher derives from these seams is labeled
``dry-run-stub-not-evidence`` and refused by production consumers.  The real
cluster run replaces exactly: the stub toolchain (pinned elan toolchains +
bubblewrap), the stub generation table (S2 GPU generation + S4 extraction),
and the synthetic corpus fixtures (frozen corpus artifacts).  See
RESUMPTION_S5.md §4/§6.
"""
import json
import os
import stat
import sys

from prepare_v2b_lean_setups import SETUP_INDEX_SCHEMA
from v2b_common import V2BError, sha256_bytes, sha256_file, sha256_sorted_json
from v2b_s5_visibility import IMPORT_CLOSURE_SCHEMA


STUB_TOOLCHAIN_PIN = "stub/lean4:v0-dry-run-not-evidence"
STUB_MODEL_BINDING = dict(
    name="stub/dry-run-generator", revision="0" * 40)
STUB_GENERATOR_NOTE = "stub-not-a-model; deterministic dry-run bodies"

# Frame-byte steering markers.  The stub TARGET phase reads them from the
# body region [headerEnd, retainedEnd) of its target view; the stub SUFFIX
# phase never sees body bytes (they arrive masked, exactly as in production),
# so suffix failure is steered through the bundle value literal instead —
# mirroring how the real driver's suffix outcome depends only on the
# transported kernel bundle and the trusted suffix.
STUB_TARGET_FAIL = "STUB_S5_TARGET_FAIL"
STUB_SUFFIX_FAIL = "STUB_S5_SUFFIX_FAIL"
STUB_TYPE_DRIFT = "STUB_S5_TYPE_DRIFT"
_SUFFIX_FAIL_LITERAL = 666

_SYNTH_HEX40 = "1" * 40
_SYNTH_HEX64 = "2" * 64

_STUB_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""Stub V2BOracleSafeProbe: dry-run protocol driver.  NON-EVIDENCE."""
import json
import sys

MARKER_PREFIX = "@@V2B_ORACLE_PROBE:"
MARKER_SUFFIX = "@@"
OUTPUT_SCHEMA = "v2b_lean_oracle_probe_result_v2"
MANIFEST_SCHEMA = "v2b_lean_oracle_probe_manifest_v2"
BUNDLE_SCHEMA = "v2b_lean_constant_bundle_v1"
TARGET_ROLES = ("prefix", "header", "target")
SUFFIX_ROLES = ("prefix", "header", "suffix", "bundle")
TARGET_FAIL = "STUB_S5_TARGET_FAIL"
SUFFIX_FAIL = "STUB_S5_SUFFIX_FAIL"
TYPE_DRIFT = "STUB_S5_TYPE_DRIFT"
SUFFIX_FAIL_LITERAL = 666


def hard(message):
    sys.stderr.write("stub probe trusted-input error: %s\n" % message)
    sys.stderr.flush()
    raise SystemExit(2)


def read_line(stream):
    line = stream.readline()
    if not line.endswith(b"\n"):
        hard("stdin line is not newline-terminated")
    return line


def read_exact(stream, n):
    payload = b""
    while len(payload) < n:
        chunk = stream.read(n - len(payload))
        if not chunk:
            hard("channel closed inside a source frame")
        payload += chunk
    return payload


def read_frames(stream, allowed):
    frames = {}
    while True:
        line = read_line(stream).decode("utf-8", "strict").rstrip("\n")
        if line == "ENDFRAMES":
            return frames
        parts = line.split(" ")
        if len(parts) != 3 or parts[0] != "FRAME":
            hard("malformed source frame header: %r" % line)
        role, length_text = parts[1], parts[2]
        if role not in allowed or role in frames:
            hard("frame role %r not permitted or duplicated" % role)
        payload = read_exact(stream, int(length_text))
        if stream.read(1) != b"\n":
            hard("source frame is not newline-terminated")
        frames[role] = payload


def emit(nonce, value):
    sys.stdout.write(MARKER_PREFIX + nonce + MARKER_SUFFIX
                     + json.dumps(value, ensure_ascii=False,
                                  separators=(",", ":")) + "\n")
    sys.stdout.flush()


def await_authorization(stream, nonce):
    line = read_line(stream)
    if line != ("GO:%s\n" % nonce).encode("ascii"):
        hard("channel start authorization is missing or malformed")
    if stream.read(1) != b"":
        hard("channel stdin must end immediately after start authorization")


def name_array(dotted):
    return [["s", part] for part in dotted.split(".")]


def main():
    if len(sys.argv) < 2:
        hard("usage: stub lean [--run driver.lean] manifest.json")
    manifest_path = sys.argv[-1]
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        hard("manifest schema drift")
    mode = manifest["mode"]
    stream = sys.stdin.buffer
    nonce = read_line(stream).decode("ascii").rstrip("\n")
    if len(nonce) != 64:
        hard("channel nonce is not 64 hex characters")
    if mode == "target":
        frames = read_frames(stream, TARGET_ROLES)
        prefix, header = frames["prefix"], frames["header"]
        target = frames["target"]
        if len(prefix) != manifest["targetStartByte"] \
                or len(header) != manifest["headerEndByte"] \
                or len(target) != manifest["retainedEndByte"] \
                or header[:len(prefix)] != prefix \
                or target[:len(header)] != header:
            hard("frame/manifest byte disagreement")
        body = target[manifest["headerEndByte"]:].decode("utf-8", "strict")
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "prevalidation", "mode": "target",
                     "n_prior_commands": 1,
                     "prefix_view_bytes": len(prefix),
                     "header_view_bytes": len(header),
                     "target_view_bytes": len(target)})
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "phase-start", "mode": "target"})
        await_authorization(stream, nonce)
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "phase-go-accepted", "mode": "target"})
        if TARGET_FAIL in body:
            emit(nonce, {"schema": OUTPUT_SCHEMA, "record_type": "target",
                         "status": "verification-failure",
                         "reason": "elaboration-error"})
            return
        type_name = "Int" if TYPE_DRIFT in body else "Nat"
        value = SUFFIX_FAIL_LITERAL if SUFFIX_FAIL in body \
            else len(body.encode("utf-8")) % 97
        kind = "defn" if manifest["targetKind"] == "def" else "thm"
        row = [kind, name_array(manifest["targetName"]), [],
               ["const", name_array(type_name), []], ["lit", "nat", value]]
        if kind == "defn":
            row.append(["regular", 0])
        bundle = [BUNDLE_SCHEMA, manifest["targetName"], [row]]
        emit(nonce, {"schema": OUTPUT_SCHEMA, "record_type": "target",
                     "status": "verified", "n_bundled_constants": 1,
                     "bundle": bundle})
    elif mode == "suffix":
        frames = read_frames(stream, SUFFIX_ROLES)
        bundle = json.loads(frames["bundle"].decode("utf-8", "strict"))
        if not isinstance(bundle, list) or len(bundle) != 3 \
                or bundle[0] != BUNDLE_SCHEMA \
                or bundle[1] != manifest["targetName"]:
            hard("suffix bundle schema/target drift")
        rows = bundle[2]
        target_key = name_array(manifest["targetName"])
        target_rows = [row for row in rows if row[1] == target_key]
        if len(target_rows) != 1:
            hard("suffix bundle lacks one exact committed target")
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "prevalidation", "mode": "suffix",
                     "n_prior_commands": 1, "n_decoded_constants": len(rows)})
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "phase-start", "mode": "suffix"})
        await_authorization(stream, nonce)
        emit(nonce, {"schema": OUTPUT_SCHEMA,
                     "record_type": "phase-go-accepted", "mode": "suffix"})
        value = target_rows[0][4]
        if value == ["lit", "nat", SUFFIX_FAIL_LITERAL]:
            emit(nonce, {"schema": OUTPUT_SCHEMA, "record_type": "suffix",
                         "status": "verification-failure",
                         "reason": "suffix-elaboration-error"})
            return
        emit(nonce, {"schema": OUTPUT_SCHEMA, "record_type": "suffix",
                     "status": "verified", "n_replayed_constants": len(rows),
                     "n_suffix_commands": 1})
    else:
        hard("unknown probe mode %r" % mode)


if __name__ == "__main__":
    main()
'''


def _write(path, value, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(value, bytes) else "w"
    kwargs = {} if isinstance(value, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as handle:
        handle.write(value)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True)
        handle.write("\n")


def build_stub_toolchain(root):
    """Install the stub toolchain (bin/lean speaking the probe protocol)."""
    root = os.path.realpath(root)
    toolchain = os.path.join(root, "stub-toolchain")
    driver = os.path.join(toolchain, "bin", "v2b_s5_stub_probe.py")
    lean = os.path.join(toolchain, "bin", "lean")
    lake = os.path.join(toolchain, "bin", "lake")
    runtime = os.path.join(toolchain, "lib", "libLeanStubShared.so")
    python = os.path.realpath(sys.executable)
    _write(driver, _STUB_DRIVER_SOURCE)
    _write(lean, "#!/bin/sh\nexec \"%s\" \"%s\" \"$@\"\n"
           % (python, driver), executable=True)
    _write(lake, "#!/bin/sh\necho stub lake: not a build tool >&2\nexit 2\n",
           executable=True)
    _write(runtime, b"stub-runtime-library-not-evidence")
    return dict(toolchain=toolchain, lean=lean, lake=lake, runtime=runtime,
                driver=driver)


def build_toy_workspace(root, toolchain_paths, *, module="Probe.Target",
                        source_rel="Probe/Target.lean",
                        prefix="import Probe.Direct\n\n",
                        header="def target : Nat := ",
                        original_body="41",
                        suffix="\n-- toy immutable suffix\n#check target\n",
                        target_name="target", target_kind="def",
                        corpus_git_sha=_SYNTH_HEX40):
    """One toy corpus module plus the synthetic setup/closure/index chain.

    Returns every path plus the byte offsets the launch spec needs.  The
    fixture shapes mirror tests/test_v2b_s5_visibility.py so the unmodified
    production ``produce_visibility`` join accepts them.
    """
    root = os.path.realpath(root)
    workspace = os.path.join(root, "workspace")
    if os.path.exists(workspace):
        raise V2BError(f"toy workspace already exists: {workspace}")
    source = os.path.join(workspace, *source_rel.split("/"))
    pin = os.path.join(workspace, "lean-toolchain")
    direct = os.path.join(workspace, ".lake", "build", "lib", "lean",
                          "Probe", "Direct.olean")
    transitive = os.path.join(workspace, ".lake", "build", "lib", "lean",
                              "Probe", "Transitive.olean")
    helper = os.path.join(root, "v2bS5ExpandSetupStub")
    setup_path = os.path.join(root, "expanded-setup.json")
    closure_path = os.path.join(root, "closure.json")
    index_path = os.path.join(root, "broad-index.json")
    extraction = os.path.join(root, "extract-placeholder.json")
    original_text = prefix + header + original_body + suffix
    _write(source, original_text)
    _write(pin, STUB_TOOLCHAIN_PIN + "\n")
    _write(direct, b"toy-direct-olean")
    _write(transitive, b"toy-transitive-olean")
    _write(helper, b"stub-setup-helper-not-evidence", executable=True)
    _write(extraction, "{}\n")
    runtime = toolchain_paths["runtime"]
    lean = toolchain_paths["lean"]
    lake = toolchain_paths["lake"]
    setup = dict(
        dynlibs=[runtime],
        importArts={"Probe.Direct": [[direct]],
                    "Probe.Transitive": [[transitive]]},
        isModule=True, name=module, options={"autoImplicit": False},
        package="Toy",
        plugins=[dict(path=runtime, initFn="initialize_toy")],
        imports=[dict(module="Probe.Direct", importAll=False,
                      isExported=False, isMeta=False)])
    _write_json(setup_path, setup)
    modules = ["Probe.Direct", "Probe.Transitive"]
    _write_json(closure_path, dict(
        schema=IMPORT_CLOSURE_SCHEMA, module=module,
        source_sha256=sha256_file(source), modules=modules,
        modules_sha256=sha256_sorted_json(modules)))
    artifacts = sorted((
        dict(path=direct, sha256=sha256_file(direct),
             roles=["lean-search-artifact"]),
        dict(path=transitive, sha256=sha256_file(transitive),
             roles=["lean-search-artifact"]),
        dict(path=runtime, sha256=sha256_file(runtime),
             roles=["dynamic-search-artifact"]),
    ), key=lambda row: row["path"])
    setup_row = dict(
        module=module, source=source, source_rel=source_rel,
        source_sha256=sha256_file(source), setup=setup_path,
        setup_sha256=sha256_file(setup_path),
        setup_semantics_sha256=sha256_sorted_json(setup), batch_index=0)
    lake_environment = dict(
        LEAN_PATH=os.path.dirname(direct),
        LEAN_SRC_PATH=os.path.dirname(source),
        LD_LIBRARY_PATH=os.path.dirname(runtime),
        DYLD_LIBRARY_PATH=None,
        PATH=os.path.dirname(lean))
    roots = [dict(path=os.path.dirname(direct),
                  roles=["lean-search-root"], state="directory")]
    batches = [dict(batch_index=0, n_modules=1, first_module=module,
                    last_module=module, targets_sha256=_SYNTH_HEX64,
                    stdout_sha256=_SYNTH_HEX64, stderr_sha256=_SYNTH_HEX64,
                    setup_rows_sha256=_SYNTH_HEX64)]
    index = dict(
        schema=SETUP_INDEX_SCHEMA, repo="toy-dry-run", language="lean",
        corpus_git_sha=corpus_git_sha,
        extraction=dict(path=extraction, sha256=_SYNTH_HEX64,
                        schema="v2a_lean_extract_v3"),
        corpus_root=workspace, toolchain=STUB_TOOLCHAIN_PIN,
        lean_toolchain_sha256=sha256_file(pin),
        lake=dict(path=lake, sha256=sha256_file(lake),
                  version="stub lake"),
        lean=dict(path=lean, sha256=sha256_file(lean),
                  version="stub lean"),
        environment_probe=dict(path="/usr/bin/env", sha256=_SYNTH_HEX64),
        lake_environment=lake_environment,
        lake_environment_sha256=sha256_sorted_json(lake_environment),
        n_search_roots=len(roots), search_roots=roots,
        search_roots_sha256=sha256_sorted_json(roots),
        n_search_directories=0, search_directories=[],
        search_directories_sha256=sha256_sorted_json([]),
        n_search_symlinks=0, search_symlinks=[],
        search_symlinks_sha256=sha256_sorted_json([]),
        n_modules=1, n_batches=1, batch_size=1,
        setups={source: setup_path},
        setups_sha256=sha256_sorted_json({source: setup_path}),
        rows=[setup_row], rows_sha256=sha256_sorted_json([setup_row]),
        batches=batches, batches_sha256=sha256_sorted_json(batches),
        n_artifacts=len(artifacts), artifacts=artifacts,
        artifacts_sha256=sha256_sorted_json(artifacts),
        generator=dict(source_commit=_SYNTH_HEX40,
                       source_tree_hash=_SYNTH_HEX64,
                       program="prepare_v2b_lean_setups.py"))
    _write_json(index_path, index)
    blob = original_text.encode("utf-8")
    target_start = len(prefix.encode("utf-8"))
    header_end = target_start + len(header.encode("utf-8"))
    target_end = header_end + len(original_body.encode("utf-8"))
    return dict(
        workspace=workspace, module=module, source=source,
        source_sha256=sha256_bytes(blob), original_text=original_text,
        pin=pin, setup=setup_path, closure=closure_path, index=index_path,
        helper=helper, corpus_git_sha=corpus_git_sha,
        target_name=target_name, target_kind=target_kind,
        target_start_byte=target_start, header_end_byte=header_end,
        target_end_byte=target_end,
        runtime_paths=[lean, runtime])


def stub_body(arm, draw_index, *, fail_target=False, fail_suffix=False,
              type_drift=False):
    """One deterministic dry-run candidate body ('the model call')."""
    markers = []
    if fail_target:
        markers.append(STUB_TARGET_FAIL)
    if fail_suffix:
        markers.append(STUB_SUFFIX_FAIL)
    if type_drift:
        markers.append(STUB_TYPE_DRIFT)
    note = (" -- " + " ".join(markers)) if markers else ""
    return f"41 -- stub {arm} draw {draw_index}{note}"


def default_stub_outcomes(arm, draw_index):
    """The demo's frozen deterministic mix: mostly passes, three zeros."""
    return dict(
        fail_target=(arm == "k1" and draw_index == 1),
        fail_suffix=(arm == "k3" and draw_index == 1),
        type_drift=(arm == "k6" and draw_index == 1))


def write_stub_generation_table(root, target_key, arms, n_draws, *,
                                repo="toy-dry-run", body_for=None):
    """Write the dry-run generation table + hash-bound body files."""
    root = os.path.realpath(root)
    bodies_dir = os.path.join(root, "stub-bodies")
    rows = []
    for arm in arms:
        for draw in range(n_draws):
            if body_for is not None:
                body = body_for(arm, draw)
            else:
                body = stub_body(arm, draw, **default_stub_outcomes(
                    arm, draw))
            path = os.path.join(bodies_dir, target_key,
                                f"{arm}-d{draw:02d}.txt")
            _write(path, body)
            rows.append(dict(target_key=target_key, arm=arm,
                             draw_index=draw, body_path=path,
                             body_sha256=sha256_file(path)))
    table = dict(schema="v2b_s5_generation_table_v1", repo=repo,
                 model_binding=dict(STUB_MODEL_BINDING),
                 generator_note=STUB_GENERATOR_NOTE,
                 n_rows=len(rows), rows=rows)
    path = os.path.join(root, "stub-generation-table.json")
    _write_json(path, table)
    return path, table


__all__ = [
    "STUB_GENERATOR_NOTE", "STUB_MODEL_BINDING", "STUB_SUFFIX_FAIL",
    "STUB_TARGET_FAIL", "STUB_TOOLCHAIN_PIN", "STUB_TYPE_DRIFT",
    "build_stub_toolchain", "build_toy_workspace", "default_stub_outcomes",
    "stub_body", "write_stub_generation_table",
]
