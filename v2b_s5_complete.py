#!/usr/bin/env python3
"""Complete-artifact producer for the S5 behavioral evidence table.

This is the file-based producer that §15.A17's execution boundary requires:
it duplicate-key-rejects and RE-MATERIALIZES the exact per-target/arm/draw
outcome table from hash-bound four-phase run evidence, never from a caller-
supplied summary.  For every expected cell it rebuilds the candidate module
bytes from the hash-bound generation-table body file, rebuilds the plan, and
re-runs the four-phase envelope's own byte-level revalidation
(``run_v2b_s5_four_phase.validate_summary``) before a single outcome enters
the artifact.

EVIDENCE-INVALID ACCOUNTING (carried forward verbatim from commit 6109346:
"evidence-invalid candidate accounting must be prespecified — count-as-zero
or reported exclusion category — before any behavioral score").  This
producer implements the conservative fail-closed position: it REFUSES to
finalize while any expected cell is missing, harness-invalid, or
baseline-inconsistent, and reports every unresolved cell in the refusal.  It
never counts an evidence failure as a model zero and never silently excludes
one.  Choosing count-as-zero or a reported exclusion category instead is a
PREREG amendment (RESUMPTION_S5.md §7 P3), not a code-path flag.

Outcome mapping (frozen here, matching the four-phase summary vocabulary):
``verified-pass`` -> 1; ``verification-failure`` / ``candidate-timeout`` /
``candidate-output-limit`` / ``candidate-terminated`` /
``candidate-type-drift`` -> 0; ``baseline-ineligible`` -> arm-independent
target ineligibility (all outcomes null; §14.23 baseline-pass rule);
``harness-invalid`` -> refusal, never an outcome.

The artifact is execution-mode labeled.  ``dry-run-stub-not-evidence`` marks
tables produced through the ``none-test-only`` backend seams of
``v2b_s5_dryrun.py``; production consumers must refuse that mode.
``production-bubblewrap`` additionally requires a clean tracked source tree.
"""
import os

from provenance import head_commit, source_clean, source_tree_hash
from run_v2b_s5_four_phase import (FOUR_PHASE_CONTRACT_SHA256,
                                   SANDBOX_CONTRACT_SHA256, LEAN_DRIVER,
                                   build_plan, validate_summary)
from v2b_behavioral_governance import (BEHAVIOR_ELIGIBILITY_FIELDS,
                                       BEHAVIOR_EVIDENCE_SCHEMA)
from v2b_common import (V2BError, sha256_bytes, sha256_file,
                        sha256_sorted_json, write_new_json)


EXECUTION_MODES = ("production-bubblewrap", "dry-run-stub-not-evidence")
CANDIDATE_ZERO_CLASSIFICATIONS = frozenset((
    "verification-failure", "candidate-timeout", "candidate-output-limit",
    "candidate-terminated", "candidate-type-drift"))
OUTCOME_CLASS_BY_KIND = {
    "def": "lean-def-typecheck",
    "theorem": "lean-theorem-proof",
    "lemma": "lean-theorem-proof",
}

ACCOUNTING_CONTRACT = dict(
    schema="v2b_s5_complete_accounting_contract_v1",
    verified_pass=1,
    ordinary_zero=sorted(CANDIDATE_ZERO_CLASSIFICATIONS),
    baseline_ineligible=("arm-independent target ineligibility; outcomes "
                         "null; at least one witness cell required; mixing "
                         "baseline-ineligible with any other classification "
                         "for one target fails closed"),
    spec_ineligible=("targets whose launch-spec eligibility flags are false "
                     "carry no cells and null outcomes"),
    harness_invalid=("refusal with a complete unresolved-cell report; never "
                     "a zero, never a silent exclusion; the count-as-zero "
                     "vs reported-exclusion decision is a pending PREREG "
                     "amendment (RESUMPTION_S5.md §7 P3)"),
    missing_cell="refusal",
    baseline_consistency=("every cell of one target must bind the same "
                          "baseline bundle and certificate hashes"),
    re_materialization=("candidate bytes rebuilt from the hash-bound "
                        "generation-table body file; plans rebuilt from the "
                        "launch spec; summaries revalidated byte-for-byte "
                        "by run_v2b_s5_four_phase.validate_summary"),
)
ACCOUNTING_CONTRACT_SHA256 = sha256_sorted_json(ACCOUNTING_CONTRACT)

_ROW_KEYS = frozenset((
    "target_key", "identity", "outcome_class", "eligibility", "eligible",
    "n_cells", "baseline", "outcomes", "evidence"))
_ARTIFACT_KEYS = frozenset((
    "schema", "execution_mode", "repo", "language", "corpus_git_sha",
    "model_binding", "arms", "n_draws_per_target_arm", "n_targets",
    "launch_spec_sha256", "generation_table_sha256",
    "four_phase_contract_sha256", "sandbox_contract_sha256",
    "accounting", "accounting_sha256", "rows", "generator", "binding"))


def _hex(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


def cell_run_dir(run_root, target_key, arm, draw_index):
    """The frozen run-directory layout shared with the launcher."""
    return os.path.join(run_root, target_key, arm, f"d{draw_index:02d}")


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


def _rebuild_plan(target, body, original, visibility, *,
                  allow_unisolated_test):
    header_end = target["header_end_byte"]
    candidate = (original[:header_end] + body
                 + original[target["target_end_byte"]:])
    plan = build_plan(
        original, candidate, logical_file=target["source_path"],
        target_name=target["target_name"],
        target_kind=target["target_kind"],
        target_start=target["target_start_byte"],
        header_end=header_end,
        baseline_retained_end=target["target_end_byte"],
        candidate_retained_end=header_end + len(body),
        visibility=visibility, driver_sha256=sha256_file(LEAN_DRIVER),
        allow_unisolated_test=allow_unisolated_test)
    return plan, candidate


def _revalidate_cell(run_root, target, arm, draw_index, body_row, original,
                     visibility, *, allow_unisolated_test):
    directory = cell_run_dir(run_root, target["target_key"], arm, draw_index)
    if not os.path.isdir(directory):
        return None
    body = _read_body(body_row)
    plan, candidate = _rebuild_plan(
        target, body, original, visibility,
        allow_unisolated_test=allow_unisolated_test)
    validated = validate_summary(
        directory, plan, visibility, original, candidate,
        allow_unisolated_test=allow_unisolated_test)
    summary = validated["summary"]
    return dict(
        directory=directory, summary=summary,
        summary_sha256=validated["summarySha256"],
        plan_sha256=summary["planSha256"],
        classification=summary["classification"],
        outcome=summary["pass"],
        baseline_bundle_sha256=summary["baselineBundleSha256"],
        baseline_certificate_sha256=summary[
            "baselineTargetCertificateSha256"])


def _target_row(spec, table_rows, run_root, target, visibility, *,
                allow_unisolated_test, unresolved):
    target_key = target["target_key"]
    arms = spec["arms"]
    n_draws = spec["n_draws"]
    flags = dict(
        reference_body_le_448_tokens=target[
            "reference_body_le_448_tokens"],
        class_verifier_feasible=target["class_verifier_feasible"])
    spec_eligible = all(flags.values())
    try:
        original = open(target["source_path"], "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read original module for "
                       f"{target_key}: {err}") from err
    if sha256_bytes(original) != target["original_sha256"]:
        raise V2BError(f"original module hash drift for {target_key}")

    cells = {}
    for arm in arms:
        for draw in range(n_draws):
            key = (arm, draw)
            body_row = table_rows.get((target_key, arm, draw))
            if spec_eligible and body_row is None:
                raise V2BError(f"generation table lacks "
                               f"{target_key}/{arm}/d{draw:02d}")
            if body_row is None:
                continue
            cells[key] = _revalidate_cell(
                run_root, target, arm, draw, body_row, original,
                visibility, allow_unisolated_test=allow_unisolated_test)

    present = {key: cell for key, cell in cells.items() if cell is not None}
    if not spec_eligible:
        if present:
            raise V2BError(
                f"spec-ineligible target {target_key} has run evidence")
        baseline = dict(state="not-run", bundle_sha256=None,
                        certificate_sha256=None)
        outcomes = {arm: None for arm in arms}
        evidence = {arm: None for arm in arms}
        return _finish_row(target, flags, False, 0, baseline, outcomes,
                           evidence)

    for key, cell in sorted(present.items()):
        if cell["classification"] == "harness-invalid":
            unresolved.append((target_key, key[0], key[1],
                               "harness-invalid"))

    ineligible = {key for key, cell in present.items()
                  if cell["classification"] == "baseline-ineligible"}
    if ineligible:
        if len(ineligible) != len(present):
            raise V2BError(
                f"target {target_key} mixes baseline-ineligible with other "
                f"classifications; baseline evidence is inconsistent")
        witness = present[sorted(present)[0]]
        baseline = dict(state="baseline-ineligible",
                        bundle_sha256=witness["baseline_bundle_sha256"],
                        certificate_sha256=witness[
                            "baseline_certificate_sha256"])
        outcomes = {arm: None for arm in arms}
        evidence = {arm: [dict(draw_index=key[1],
                               summary_sha256=cell["summary_sha256"],
                               classification=cell["classification"])
                          for key, cell in sorted(present.items())
                          if key[0] == arm] or None
                    for arm in arms}
        return _finish_row(target, flags, False, len(present), baseline,
                           outcomes, evidence)

    # Harness-invalid cells carry no trustworthy baseline evidence; they are
    # already reported as unresolved above and must not poison the
    # cross-cell baseline-consistency gate on the resolved cells.
    resolved = {key: cell for key, cell in present.items()
                if cell["classification"] != "harness-invalid"}
    baseline_bundles = {cell["baseline_bundle_sha256"]
                        for cell in resolved.values()}
    baseline_certificates = {cell["baseline_certificate_sha256"]
                             for cell in resolved.values()}
    if resolved and (len(baseline_bundles) != 1
                     or len(baseline_certificates) != 1
                     or None in baseline_bundles):
        raise V2BError(f"target {target_key} baseline bundle/certificate "
                       f"disagrees across cells")

    outcomes = {}
    evidence = {}
    for arm in arms:
        arm_outcomes = []
        arm_evidence = []
        for draw in range(n_draws):
            cell = present.get((arm, draw))
            if cell is None:
                unresolved.append((target_key, arm, draw, "missing-run"))
                continue
            classification = cell["classification"]
            if classification == "verified-pass":
                outcome = 1
            elif classification in CANDIDATE_ZERO_CLASSIFICATIONS:
                outcome = 0
            elif classification == "harness-invalid":
                continue  # already reported as unresolved
            else:
                raise V2BError(f"unmapped summary classification "
                               f"{classification!r} for {target_key}")
            if cell["outcome"] != outcome:
                raise V2BError(f"summary pass field disagrees with the "
                               f"frozen outcome map for {target_key}")
            arm_outcomes.append(outcome)
            arm_evidence.append(dict(
                draw_index=draw, summary_sha256=cell["summary_sha256"],
                classification=classification))
        outcomes[arm] = arm_outcomes
        evidence[arm] = arm_evidence
    baseline = dict(
        state="baseline-pass" if resolved else "unresolved",
        bundle_sha256=sorted(baseline_bundles)[0] if resolved else None,
        certificate_sha256=(sorted(baseline_certificates)[0]
                            if resolved else None))
    return _finish_row(target, flags, True, len(present), baseline,
                       outcomes, evidence, baseline_pass=True)


def _finish_row(target, flags, eligible, n_cells, baseline, outcomes,
                evidence, *, baseline_pass=None):
    if baseline_pass is None:
        baseline_pass = baseline.get("state") == "baseline-pass"
    eligibility = dict(flags)
    eligibility["baseline_pass"] = baseline_pass
    if set(eligibility) != set(BEHAVIOR_ELIGIBILITY_FIELDS):
        raise AssertionError("internal S5 eligibility field drift")
    row = dict(
        target_key=target["target_key"],
        identity=list(target["identity"]),
        outcome_class=OUTCOME_CLASS_BY_KIND[target["target_kind"]],
        eligibility=eligibility,
        eligible=bool(eligible and all(eligibility.values())),
        n_cells=n_cells, baseline=baseline, outcomes=outcomes,
        evidence=evidence)
    if set(row) != _ROW_KEYS:
        raise AssertionError("internal S5 complete row key drift")
    return row


def produce_complete(spec, table, run_root, visibilities, *,
                     execution_mode, allow_unisolated_test=False):
    """Re-materialize the complete outcome table from run evidence.

    ``visibilities`` maps target_key -> the exact visibility artifact the
    launcher ran with (revalidated inside ``validate_summary`` against the
    staged copy in every run directory).
    """
    if execution_mode not in EXECUTION_MODES:
        raise V2BError(f"unknown S5 execution mode {execution_mode!r}")
    if (execution_mode == "dry-run-stub-not-evidence") \
            != bool(allow_unisolated_test):
        raise V2BError("S5 execution mode / backend seam disagreement")
    if execution_mode == "production-bubblewrap" and not source_clean():
        raise V2BError("production S5 complete artifact requires a clean "
                       "tracked source tree")
    run_root = os.path.abspath(run_root)
    table_rows = {}
    for row in table["rows"]:
        key = (row["target_key"], row["arm"], row["draw_index"])
        if key in table_rows:
            raise V2BError(f"duplicate generation-table cell {key!r}")
        table_rows[key] = row

    unresolved = []
    rows = []
    seen_targets = set()
    for target in spec["targets"]:
        target_key = target["target_key"]
        if target_key in seen_targets:
            raise V2BError(f"duplicate launch-spec target {target_key}")
        seen_targets.add(target_key)
        visibility = visibilities.get(target_key)
        if visibility is None:
            raise V2BError(f"no visibility artifact for {target_key}")
        rows.append(_target_row(
            spec, table_rows, run_root, target, visibility,
            allow_unisolated_test=allow_unisolated_test,
            unresolved=unresolved))
    foreign = set(table_rows) - {
        (target["target_key"], arm, draw)
        for target in spec["targets"] for arm in spec["arms"]
        for draw in range(spec["n_draws"])}
    if foreign:
        raise V2BError(f"generation table carries cells outside the launch "
                       f"spec: {sorted(foreign)[:3]}")
    if unresolved:
        listing = "; ".join(
            f"{target}/{arm}/d{draw:02d}:{why}"
            for target, arm, draw, why in unresolved[:10])
        raise V2BError(
            f"S5 complete artifact refused: {len(unresolved)} unresolved "
            f"cell(s) [{listing}] — evidence-invalid cells are never "
            f"counted as zeros or silently excluded (accounting contract "
            f"{ACCOUNTING_CONTRACT_SHA256[:12]}; RESUMPTION_S5.md §7 P3)")

    artifact = dict(
        schema=BEHAVIOR_EVIDENCE_SCHEMA,
        execution_mode=execution_mode,
        repo=spec["repo"], language=spec["language"],
        corpus_git_sha=spec["corpus"]["corpus_git_sha"],
        model_binding=dict(spec["model_binding"]),
        arms=list(spec["arms"]),
        n_draws_per_target_arm=spec["n_draws"],
        n_targets=len(rows),
        launch_spec_sha256=sha256_sorted_json(spec),
        generation_table_sha256=sha256_sorted_json(table),
        four_phase_contract_sha256=FOUR_PHASE_CONTRACT_SHA256,
        sandbox_contract_sha256=SANDBOX_CONTRACT_SHA256,
        accounting=dict(ACCOUNTING_CONTRACT),
        accounting_sha256=ACCOUNTING_CONTRACT_SHA256,
        rows=rows,
        generator=dict(program="v2b_s5_complete.py",
                       source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       source_clean=source_clean()))
    artifact["binding"] = _binding(artifact)
    validate_complete(artifact)
    return artifact


def _binding(artifact):
    payload = {key: value for key, value in artifact.items()
               if key != "binding"}
    return sha256_sorted_json(payload)


def validate_complete(artifact):
    """Shape + internal-binding validation (no live filesystem reads)."""
    if not isinstance(artifact, dict) or set(artifact) != _ARTIFACT_KEYS \
            or artifact.get("schema") != BEHAVIOR_EVIDENCE_SCHEMA:
        raise V2BError("S5 complete artifact schema/key drift")
    if artifact["execution_mode"] not in EXECUTION_MODES:
        raise V2BError("S5 complete artifact execution mode drift")
    if artifact["binding"] != _binding(artifact):
        raise V2BError("S5 complete artifact binding drift")
    if artifact["accounting"] != ACCOUNTING_CONTRACT \
            or artifact["accounting_sha256"] != ACCOUNTING_CONTRACT_SHA256:
        raise V2BError("S5 complete accounting contract drift")
    if artifact["four_phase_contract_sha256"] != FOUR_PHASE_CONTRACT_SHA256:
        raise V2BError("S5 complete four-phase contract drift")
    arms = artifact["arms"]
    n_draws = artifact["n_draws_per_target_arm"]
    if not isinstance(arms, list) or not arms \
            or type(n_draws) is not int or n_draws < 1 \
            or artifact["n_targets"] != len(artifact["rows"]):
        raise V2BError("S5 complete artifact arm/draw/target count drift")
    for hex_field in ("launch_spec_sha256", "generation_table_sha256",
                      "sandbox_contract_sha256"):
        if not _hex(artifact[hex_field]):
            raise V2BError(f"S5 complete {hex_field} is malformed")
    seen = set()
    for row in artifact["rows"]:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise V2BError("S5 complete row key drift")
        if not _hex(row["target_key"]) or row["target_key"] in seen:
            raise V2BError("S5 complete duplicate/malformed target key")
        seen.add(row["target_key"])
        eligibility = row["eligibility"]
        if set(eligibility) != set(BEHAVIOR_ELIGIBILITY_FIELDS) \
                or any(type(value) is not bool
                       for value in eligibility.values()):
            raise V2BError("S5 complete eligibility projection drift")
        if row["eligible"] is not all(eligibility.values()):
            raise V2BError("S5 complete eligibility conjunction drift")
        if set(row["outcomes"]) != set(arms) \
                or set(row["evidence"]) != set(arms):
            raise V2BError("S5 complete arm membership drift")
        for arm in arms:
            outcomes = row["outcomes"][arm]
            if row["eligible"]:
                if not isinstance(outcomes, list) \
                        or len(outcomes) != n_draws \
                        or any(outcome not in (0, 1)
                               or type(outcome) is not int
                               for outcome in outcomes):
                    raise V2BError("S5 complete eligible outcome row drift")
                evidence = row["evidence"][arm]
                if not isinstance(evidence, list) \
                        or len(evidence) != n_draws \
                        or [cell["draw_index"] for cell in evidence] != \
                        list(range(n_draws)) \
                        or any(not _hex(cell["summary_sha256"])
                               for cell in evidence):
                    raise V2BError("S5 complete eligible evidence drift")
            elif outcomes is not None:
                raise V2BError("S5 complete excluded row carries outcomes")
    return artifact


def write_complete(artifact, out_path):
    validate_complete(artifact)
    return write_new_json(out_path, artifact)


__all__ = [
    "ACCOUNTING_CONTRACT", "ACCOUNTING_CONTRACT_SHA256",
    "CANDIDATE_ZERO_CLASSIFICATIONS", "EXECUTION_MODES",
    "OUTCOME_CLASS_BY_KIND", "cell_run_dir", "produce_complete",
    "validate_complete", "write_complete",
]
