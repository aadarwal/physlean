#!/usr/bin/env python3
"""S5 launcher: corpus-integration join + four-phase execution + complete artifact.

This is the orchestration layer named by the 6109346 TODO "corpus integration
+ S5 launcher".  It consumes:

* one launch spec (``v2b_s5_launch_spec_v1``): the repo/model/arm/draw grid
  plus, per target, the committed identity, byte offsets, S4/boundary
  bindings, eligibility flags, and the paths of the corpus-derived artifacts
  (expanded ModuleSetup, import closure, broad setup index) that
  ``v2b_s5_visibility.produce_visibility`` joins; and
* one generation table (``v2b_s5_generation_table_v1``): hash-bound candidate
  body files per (target, arm, draw) — the output of the upstream S2/S4
  generation-and-extraction chain, which this launcher deliberately does not
  implement.

Per target it produces the exact-file visibility projection, then runs one
four-phase invocation per (arm, draw) through ``run_v2b_s5_four_phase`` into
a frozen run-directory layout, short-circuiting a baseline-ineligible target
after one witness cell, and finally re-materializes the complete outcome
table via ``v2b_s5_complete.produce_complete``.

PRODUCTION mode requires the canonical bubblewrap backend and a clean
tracked source tree, and exists for the cluster (RESUMPTION_S5.md §6;
cluster submission is conductor-gated).  ``--dry-run`` substitutes exactly
the seams in ``v2b_s5_dryrun.py`` (stub toolchain in place of Lean, stub
table in place of the model) through the runner's opt-in ``none-test-only``
backend; everything it emits is labeled ``dry-run-stub-not-evidence``.
"""
import argparse
import json
import os
import sys

from provenance import source_clean
from run_v2b_s5_four_phase import LEAN_DRIVER, build_plan, run_four_phase
from v2b_common import V2BError, sha256_bytes, sha256_file, write_new_json
from v2b_s5_complete import cell_run_dir, produce_complete, validate_complete
from v2b_s5_visibility import produce_visibility


LAUNCH_SPEC_SCHEMA = "v2b_s5_launch_spec_v1"
GENERATION_TABLE_SCHEMA = "v2b_s5_generation_table_v1"
ARMS = ("k1", "k3", "k4", "k5", "k6")  # §14.24 generation-arm freeze
MAX_DRAWS = 32                          # §15.A17 pilot draw ceiling
CANONICAL_BWRAP = "/usr/bin/bwrap"

_SPEC_KEYS = frozenset((
    "schema", "repo", "language", "corpus", "model_binding", "arms",
    "n_draws", "helper_path", "targets"))
_CORPUS_KEYS = frozenset((
    "workspace_root", "toolchain_root", "corpus_git_sha"))
_MODEL_KEYS = frozenset(("name", "revision"))
_TARGET_KEYS = frozenset((
    "target_key", "identity", "module", "source_path", "original_sha256",
    "target_name", "target_kind", "target_start_byte", "header_end_byte",
    "target_end_byte", "boundary_artifact_sha256", "span_id",
    "setup_path", "import_closure_path", "setup_index_path",
    "runtime_paths", "reference_body_le_448_tokens",
    "class_verifier_feasible"))
_TABLE_KEYS = frozenset((
    "schema", "repo", "model_binding", "generator_note", "n_rows", "rows"))
_TABLE_ROW_KEYS = frozenset((
    "target_key", "arm", "draw_index", "body_path", "body_sha256"))


def _hex(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


def _nat(value, label):
    if type(value) is not int or value < 0:
        raise V2BError(f"{label} must be a nonnegative integer")
    return value


def validate_launch_spec(spec):
    if not isinstance(spec, dict) or set(spec) != _SPEC_KEYS \
            or spec.get("schema") != LAUNCH_SPEC_SCHEMA:
        raise V2BError("S5 launch spec schema/key drift")
    if spec["language"] != "lean":
        raise V2BError("S5 launcher currently covers Lean only "
                       "(Python S5 is the separate §15.A7 chain)")
    if not isinstance(spec["repo"], str) or not spec["repo"]:
        raise V2BError("S5 launch spec repo is empty")
    corpus = spec["corpus"]
    if not isinstance(corpus, dict) or set(corpus) != _CORPUS_KEYS \
            or not _hex(corpus.get("corpus_git_sha"), 40):
        raise V2BError("S5 launch spec corpus binding drift")
    model = spec["model_binding"]
    if not isinstance(model, dict) or set(model) != _MODEL_KEYS \
            or not isinstance(model.get("name"), str) or not model["name"] \
            or not _hex(model.get("revision"), 40):
        raise V2BError("S5 launch spec model binding drift")
    if tuple(spec["arms"]) != ARMS:
        raise V2BError(f"S5 launch spec arms must be exactly {list(ARMS)} "
                       f"(§14.24)")
    n_draws = spec["n_draws"]
    if type(n_draws) is not int or not 1 <= n_draws <= MAX_DRAWS:
        raise V2BError(f"S5 launch spec n_draws outside 1..{MAX_DRAWS}")
    if not isinstance(spec.get("helper_path"), str) or not spec["helper_path"]:
        raise V2BError("S5 launch spec helper path is empty")
    targets = spec["targets"]
    if not isinstance(targets, list) or not targets:
        raise V2BError("S5 launch spec has no targets")
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != _TARGET_KEYS:
            raise V2BError("S5 launch spec target key drift")
        if not _hex(target["target_key"]) or target["target_key"] in seen:
            raise V2BError("S5 launch spec duplicate/malformed target key")
        seen.add(target["target_key"])
        if not isinstance(target["identity"], list) or not target["identity"]:
            raise V2BError("S5 launch spec target identity is empty")
        for field in ("module", "source_path", "target_name", "span_id"):
            if not isinstance(target[field], str) or not target[field]:
                raise V2BError(f"S5 launch spec target {field} is empty")
        if not _hex(target["original_sha256"]) \
                or not _hex(target["boundary_artifact_sha256"]):
            raise V2BError("S5 launch spec target hash binding drift")
        if target["target_kind"] not in ("def", "theorem", "lemma"):
            raise V2BError("S5 launch spec target kind is unsupported")
        start = _nat(target["target_start_byte"], "target_start_byte")
        header = _nat(target["header_end_byte"], "header_end_byte")
        end = _nat(target["target_end_byte"], "target_end_byte")
        if not start < header < end:
            raise V2BError("S5 launch spec target offsets are not ordered")
        for field in ("setup_path", "import_closure_path",
                      "setup_index_path"):
            if not isinstance(target[field], str) or not target[field]:
                raise V2BError(f"S5 launch spec target {field} is empty")
        runtime = target["runtime_paths"]
        if not isinstance(runtime, list) or not runtime \
                or any(not isinstance(path, str) or not path
                       for path in runtime):
            raise V2BError("S5 launch spec runtime allowlist drift")
        for flag in ("reference_body_le_448_tokens",
                     "class_verifier_feasible"):
            if type(target[flag]) is not bool:
                raise V2BError(f"S5 launch spec {flag} must be boolean")
    return spec


def validate_generation_table(table, spec):
    if not isinstance(table, dict) or set(table) != _TABLE_KEYS \
            or table.get("schema") != GENERATION_TABLE_SCHEMA:
        raise V2BError("S5 generation table schema/key drift")
    if table["repo"] != spec["repo"] \
            or table["model_binding"] != spec["model_binding"]:
        raise V2BError("S5 generation table repo/model binding drift")
    if not isinstance(table.get("generator_note"), str) \
            or not table["generator_note"]:
        raise V2BError("S5 generation table generator note is empty")
    rows = table["rows"]
    if not isinstance(rows, list) or table.get("n_rows") != len(rows):
        raise V2BError("S5 generation table row count drift")
    eligible_keys = {
        target["target_key"] for target in spec["targets"]
        if target["reference_body_le_448_tokens"]
        and target["class_verifier_feasible"]}
    expected = {(key, arm, draw) for key in eligible_keys
                for arm in spec["arms"] for draw in range(spec["n_draws"])}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _TABLE_ROW_KEYS:
            raise V2BError("S5 generation table row key drift")
        key = (row["target_key"], row["arm"], row["draw_index"])
        if key in seen:
            raise V2BError(f"S5 generation table duplicate cell {key!r}")
        seen.add(key)
        if key not in expected:
            raise V2BError(f"S5 generation table cell outside the eligible "
                           f"launch grid: {key!r}")
        if not isinstance(row["body_path"], str) or not row["body_path"] \
                or not _hex(row["body_sha256"]):
            raise V2BError("S5 generation table body binding drift")
    missing = expected - seen
    if missing:
        raise V2BError(f"S5 generation table lacks {len(missing)} eligible "
                       f"cell(s), e.g. {sorted(missing)[:3]}")
    return table


def _target_visibility(spec, target):
    return produce_visibility(
        target["module"], target["source_path"],
        spec["corpus"]["workspace_root"], spec["corpus"]["toolchain_root"],
        spec["helper_path"], target["setup_path"],
        target["import_closure_path"], target["setup_index_path"],
        list(target["runtime_paths"]))


def _read_original(target):
    try:
        blob = open(target["source_path"], "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read original module "
                       f"{target['source_path']}: {err}") from err
    if sha256_bytes(blob) != target["original_sha256"]:
        raise V2BError(f"original module hash drift for "
                       f"{target['target_key']}")
    return blob


def _read_body(row):
    try:
        blob = open(row["body_path"], "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read generation body "
                       f"{row['body_path']}: {err}") from err
    if sha256_bytes(blob) != row["body_sha256"]:
        raise V2BError(f"generation body hash drift: {row['body_path']}")
    if not blob:
        raise V2BError("generation body is empty")
    return blob


def _run_cell(spec, target, visibility, original, body_row, run_root, *,
              dry_run):
    body = _read_body(body_row)
    header_end = target["header_end_byte"]
    candidate = (original[:header_end] + body
                 + original[target["target_end_byte"]:])
    plan = build_plan(
        original, candidate, logical_file=target["source_path"],
        target_name=target["target_name"],
        target_kind=target["target_kind"],
        target_start=target["target_start_byte"], header_end=header_end,
        baseline_retained_end=target["target_end_byte"],
        candidate_retained_end=header_end + len(body),
        visibility=visibility, driver_sha256=sha256_file(LEAN_DRIVER),
        allow_unisolated_test=dry_run)
    directory = cell_run_dir(run_root, target["target_key"],
                             body_row["arm"], body_row["draw_index"])
    result = run_four_phase(plan, visibility, original, candidate,
                            directory, allow_unisolated_test=dry_run)
    return result["summary"]["classification"]


def run_launch(spec, table, run_root, *, dry_run=False):
    """Execute the launch grid and return the complete evidence artifact."""
    validate_launch_spec(spec)
    validate_generation_table(table, spec)
    if not dry_run:
        if not os.path.isfile(CANONICAL_BWRAP):
            raise V2BError(
                "production S5 launch requires canonical bubblewrap at "
                f"{CANONICAL_BWRAP}; this host has none.  Cluster "
                "submission is conductor-gated — see RESUMPTION_S5.md §6")
        if not source_clean():
            raise V2BError("production S5 launch requires a clean tracked "
                           "source tree")
    run_root = os.path.abspath(run_root)
    os.makedirs(run_root, mode=0o700, exist_ok=True)
    bodies = {(row["target_key"], row["arm"], row["draw_index"]): row
              for row in table["rows"]}
    visibilities = {}
    for target in spec["targets"]:
        target_key = target["target_key"]
        if not (target["reference_body_le_448_tokens"]
                and target["class_verifier_feasible"]):
            continue  # spec-ineligible: no runs, null row in the artifact
        visibility = _target_visibility(spec, target)
        visibilities[target_key] = visibility
        original = _read_original(target)
        witness_key = (target_key, spec["arms"][0], 0)
        classification = _run_cell(
            spec, target, visibility, original, bodies[witness_key],
            run_root, dry_run=dry_run)
        if classification == "baseline-ineligible":
            continue  # arm-independent ineligibility; one witness cell
        if classification == "harness-invalid":
            raise V2BError(
                f"S5 witness cell for {target_key} is harness-invalid; "
                f"fix the infrastructure and requeue under a fresh run "
                f"root — evidence is immutable and is never edited in "
                f"place (RESUMPTION_S5.md §6)")
        for arm in spec["arms"]:
            for draw in range(spec["n_draws"]):
                if (arm, draw) == (spec["arms"][0], 0):
                    continue
                _run_cell(spec, target, visibility, original,
                          bodies[(target_key, arm, draw)], run_root,
                          dry_run=dry_run)
    for target in spec["targets"]:
        if target["target_key"] not in visibilities \
                and (target["reference_body_le_448_tokens"]
                     and target["class_verifier_feasible"]):
            raise AssertionError("internal S5 launcher visibility gap")
        visibilities.setdefault(target["target_key"],
                                _spec_ineligible_visibility(spec, target))
    execution_mode = ("dry-run-stub-not-evidence" if dry_run
                      else "production-bubblewrap")
    return produce_complete(
        spec, table, run_root, visibilities,
        execution_mode=execution_mode, allow_unisolated_test=dry_run)


def _spec_ineligible_visibility(spec, target):
    # Spec-ineligible targets never run, but the producer still requires a
    # visibility join so their exclusion is corpus-bound, not asserted.
    return _target_visibility(spec, target)


def _load_json_object(path, schema, label):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        raise V2BError(f"cannot read {label} {path}: {err}") from err
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise V2BError(f"{label} {path} schema drift")
    return value


def dry_run_demo(root):
    """Build every dry-run seam and run the full chain on one toy target."""
    from v2b_s5_dryrun import (STUB_MODEL_BINDING, build_stub_toolchain,
                               build_toy_workspace,
                               write_stub_generation_table)
    root = os.path.abspath(root)
    os.makedirs(root, mode=0o700, exist_ok=True)
    toolchain = build_stub_toolchain(root)
    workspace = build_toy_workspace(root, toolchain)
    target_key = sha256_bytes(b"toy-dry-run-demo-target")
    spec = dict(
        schema=LAUNCH_SPEC_SCHEMA, repo="toy-dry-run", language="lean",
        corpus=dict(workspace_root=workspace["workspace"],
                    toolchain_root=toolchain["toolchain"],
                    corpus_git_sha=workspace["corpus_git_sha"]),
        model_binding=dict(STUB_MODEL_BINDING),
        arms=list(ARMS), n_draws=2, helper_path=workspace["helper"],
        targets=[dict(
            target_key=target_key,
            identity=[workspace["module"], workspace["target_name"]],
            module=workspace["module"], source_path=workspace["source"],
            original_sha256=workspace["source_sha256"],
            target_name=workspace["target_name"],
            target_kind=workspace["target_kind"],
            target_start_byte=workspace["target_start_byte"],
            header_end_byte=workspace["header_end_byte"],
            target_end_byte=workspace["target_end_byte"],
            boundary_artifact_sha256="0" * 64,
            span_id="toy-span-0",
            setup_path=workspace["setup"],
            import_closure_path=workspace["closure"],
            setup_index_path=workspace["index"],
            runtime_paths=workspace["runtime_paths"],
            reference_body_le_448_tokens=True,
            class_verifier_feasible=True)])
    _, table = write_stub_generation_table(
        root, target_key, spec["arms"], spec["n_draws"])
    run_root = os.path.join(root, "runs")
    artifact = run_launch(spec, table, run_root, dry_run=True)
    out_path = os.path.join(root, "complete-dry-run.json")
    write_new_json(out_path, artifact)
    return artifact, out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-spec")
    parser.add_argument("--generation-table")
    parser.add_argument("--run-root")
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true",
                        help="use the v2b_s5_dryrun.py stub seams; output "
                             "is labeled dry-run-stub-not-evidence")
    parser.add_argument("--dry-run-demo", metavar="DIR",
                        help="build the toy corpus + stub toolchain + stub "
                             "generation table under DIR and run the full "
                             "chain locally (no GPU, no Lean toolchain)")
    args = parser.parse_args(argv)
    try:
        if args.dry_run_demo:
            artifact, out_path = dry_run_demo(args.dry_run_demo)
        else:
            required = ("launch_spec", "generation_table", "run_root", "out")
            if any(getattr(args, name) is None for name in required):
                parser.error("--launch-spec, --generation-table, "
                             "--run-root, and --out are required "
                             "(or use --dry-run-demo DIR)")
            spec = _load_json_object(args.launch_spec, LAUNCH_SPEC_SCHEMA,
                                     "launch spec")
            table = _load_json_object(args.generation_table,
                                      GENERATION_TABLE_SCHEMA,
                                      "generation table")
            artifact = run_launch(spec, table, args.run_root,
                                  dry_run=args.dry_run)
            write_new_json(args.out, artifact)
            out_path = args.out
        validate_complete(artifact)
    except (OSError, V2BError) as err:
        raise SystemExit(f"FATAL: {err}") from err
    eligible = sum(1 for row in artifact["rows"] if row["eligible"])
    passes = sum(sum(outcomes) for row in artifact["rows"]
                 if row["eligible"]
                 for outcomes in row["outcomes"].values())
    cells = sum(len(outcomes) for row in artifact["rows"]
                if row["eligible"]
                for outcomes in row["outcomes"].values())
    print(f"[v2b-s5-launcher] {artifact['execution_mode']} "
          f"targets={artifact['n_targets']} eligible={eligible} "
          f"cells={cells} passes={passes} -> {out_path}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARMS", "GENERATION_TABLE_SCHEMA", "LAUNCH_SPEC_SCHEMA", "MAX_DRAWS",
    "dry_run_demo", "run_launch", "validate_generation_table",
    "validate_launch_spec",
]
