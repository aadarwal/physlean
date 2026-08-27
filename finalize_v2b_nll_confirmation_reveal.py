#!/usr/bin/env python3
"""Registered one-shot reveal for the fresh SymPy NLL confirmation.

The private salt is read only after the committed masked artifact and its one
committed fixed-N predecessor validate.  The producer then independently
replays the raw-score mask, publishes the salt, maps every opaque family, and
decrypts all 4 x 5 x 200 fixed-width payloads.  Structural padding is filtered
only from the committed assembly eligibility ledger; a decrypted numerical
zero is never treated as padding.
"""
import argparse
import copy
import os
import subprocess
import sys

from eval_v2b_nll_confirmation import ASSEMBLY_SCHEMA, git_is_ancestor
from freeze_v2b_nll_confirmation import validate_freeze
from finalize_v2b_nll_confirmation_fixed_n import (
    FIXED_N_SCHEMA, validate_fixed_n,
)
from prepare_v2b_nll_confirmation_masked import (
    CONTRASTS, MASKED_SCHEMA, MODEL_ORDER, N_CONTRASTS, N_MASKED_ROWS,
    N_MODELS, N_TARGETS, _binding, _discover_paths, _file_ledger,
)
from prepare_v2b_nll_confirmation_masked import (
    _load_discovered, _load_provenance_inputs, _record,
    ledger_record, publication_sha256, replay_masked,
    validate_input_ledger, validate_masked,
)
from prepare_v2b_nll_confirmation_salt import (
    IMPLEMENTATION_FREEZE_SCHEMA, SALT_COMMITMENT_SCHEMA,
    protocol_record, validate_commitment,
)
from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (
    V2BError, artifact_binding, load_json, sha256_bytes, sha256_file,
    sha256_sorted_json, validate_identity, write_new_json,
)
from v2b_nll_confirmation import (
    PROTOCOL_PATH, PROTOCOL_RAW_SHA256, SCORED_CELLS, load_protocol,
)
from v2b_nll_confirmation_crypto import (
    CONTRAST_IDS, STUDY_ID, decrypt_delta, family_id, load_salt_file,
    salt_commitment, verify_ciphertext,
)


REVEAL_SCHEMA = "v2b_nll_e2_confirmation_reveal_v1"
REVEAL_STATE = "registered-one-shot-reveal-complete"
PROGRAM = os.path.basename(__file__)
ANALYZER_PATH = "analyze_v2b_nll_confirmation.py"

ANALYSIS_REGISTRATION_KEYS = {
    "analyzer_path", "analyzer_sha256", "analyzer_commit",
    "implementation_freeze", "scoring_source_commit",
    "analyzer_commit_is_ancestor_of_scoring",
}
TOP_KEYS = {
    "schema", "state", "study_id", "repo", "language",
    "corpus_git_sha", "protocol", "bindings", "ancestry",
    "analysis_registration", "salt_commitment_sha256",
    "revealed_salt_hex", "cohort", "grid", "models", "models_sha256",
    "input_ledger", "generator",
}
REVEAL_BINDING_SCHEMAS = {
    "implementation_freeze": IMPLEMENTATION_FREEZE_SCHEMA,
    "source_gate": "v2b_nll_e2_confirmation_source_gate_v1",
    "bound_sample": "v2b_nll_e2_confirmation_sample_v1",
    "assembly": ASSEMBLY_SCHEMA,
    "salt_commitment": SALT_COMMITMENT_SCHEMA,
    "study_complete": "v2b_nll_e2_confirmation_study_complete_v1",
    "masked": MASKED_SCHEMA,
    "fixed_n": FIXED_N_SCHEMA,
}
GENERATOR_KEYS = {
    "program", "program_sha256", "source_commit", "source_tree_hash"}


def _exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        observed = sorted(value) if isinstance(value, dict) else type(value)
        raise V2BError(f"{label} key drift: {observed!r}")


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _generator(value):
    _exact_keys(value, GENERATOR_KEYS, "confirmation reveal generator")
    if value["program"] != PROGRAM or not _hex(value["program_sha256"]) \
            or not _hex(value["source_commit"], 40) \
            or not _hex(value["source_tree_hash"]):
        raise V2BError("malformed confirmation reveal generator")
    return value


def _freeze_file_index(value):
    rows = value.get("files")
    if not isinstance(rows, list) or not rows:
        raise V2BError("implementation freeze has no source-file closure")
    paths = []
    out = {}
    for row in rows:
        _exact_keys(row, {"path", "sha256", "role"},
                    "implementation freeze file")
        path = row["path"]
        if not isinstance(path, str) or not path or os.path.isabs(path) \
                or os.path.normpath(path) != path \
                or path == ".." or path.startswith("../") \
                or not _hex(row["sha256"]) \
                or not isinstance(row["role"], str) or not row["role"]:
            raise V2BError("malformed implementation freeze file row")
        paths.append(path)
        out[path] = row
    if paths != sorted(paths) or len(paths) != len(set(paths)) \
            or value.get("files_sha256") != sha256_sorted_json(rows):
        raise V2BError("implementation freeze file closure/hash drift")
    return out


def validate_implementation_freeze(value, binding, protocol):
    """Bind exact raw bytes to the root-owned full closure validator."""
    _binding(binding, IMPLEMENTATION_FREEZE_SCHEMA,
             "reveal implementation freeze")
    validate_freeze(value, protocol)
    if publication_sha256(value) != binding["sha256"]:
        raise V2BError("implementation freeze bytes differ from binding")
    files = _freeze_file_index(value)
    if ANALYZER_PATH not in files or PROGRAM not in files:
        raise V2BError("implementation freeze omits reveal/analyzer source")
    return value


def _git_blob_sha256(commit, relative_path):
    process = subprocess.run(
        ["git", "-C", BASE, "show", f"{commit}:{relative_path}"],
        capture_output=True)
    if process.returncode != 0:
        raise V2BError(f"cannot read registered git blob {relative_path}")
    return sha256_bytes(process.stdout)


def analysis_registration(freeze, freeze_binding, scoring_source_commit,
                          ancestor_fn=git_is_ancestor,
                          current_sha_fn=None, commit_sha_fn=None):
    """Build and verify the exact analyzer-before-score registration row."""
    files = _freeze_file_index(freeze)
    analyzer = files.get(ANALYZER_PATH)
    if analyzer is None:
        raise V2BError("implementation freeze omits registered analyzer")
    commit = freeze["implementation_commit"]
    current_sha = (current_sha_fn or
                   (lambda path: sha256_file(os.path.join(BASE, path))))
    commit_sha = commit_sha_fn or _git_blob_sha256
    if current_sha(ANALYZER_PATH) != analyzer["sha256"] \
            or commit_sha(commit, ANALYZER_PATH) != analyzer["sha256"]:
        raise V2BError("registered analyzer bytes differ from freeze/git")
    if not ancestor_fn(commit, scoring_source_commit):
        raise V2BError("registered analyzer commit is not before scoring")
    value = dict(
        analyzer_path=ANALYZER_PATH,
        analyzer_sha256=analyzer["sha256"], analyzer_commit=commit,
        implementation_freeze=copy.deepcopy(freeze_binding),
        scoring_source_commit=scoring_source_commit,
        analyzer_commit_is_ancestor_of_scoring=True)
    return validate_analysis_registration(
        value, freeze, freeze_binding, scoring_source_commit, ancestor_fn,
        current_sha_fn=current_sha_fn, commit_sha_fn=commit_sha_fn)


def validate_analysis_registration(value, freeze, freeze_binding,
                                   scoring_source_commit,
                                   ancestor_fn=git_is_ancestor,
                                   current_sha_fn=None, commit_sha_fn=None):
    _exact_keys(value, ANALYSIS_REGISTRATION_KEYS,
                "analysis registration")
    files = _freeze_file_index(freeze)
    analyzer = files.get(ANALYZER_PATH)
    if analyzer is None \
            or value["analyzer_path"] != ANALYZER_PATH \
            or value["analyzer_sha256"] != analyzer["sha256"] \
            or value["analyzer_commit"] != freeze["implementation_commit"] \
            or value["implementation_freeze"] != freeze_binding \
            or value["scoring_source_commit"] != scoring_source_commit \
            or value["analyzer_commit_is_ancestor_of_scoring"] is not True:
        raise V2BError("analysis registration/freeze binding drift")
    current_sha = (current_sha_fn or
                   (lambda path: sha256_file(os.path.join(BASE, path))))
    commit_sha = commit_sha_fn or _git_blob_sha256
    if current_sha(ANALYZER_PATH) != value["analyzer_sha256"] \
            or commit_sha(value["analyzer_commit"], ANALYZER_PATH) != \
            value["analyzer_sha256"] \
            or not ancestor_fn(value["analyzer_commit"],
                               scoring_source_commit):
        raise V2BError("analysis registration does not replay")
    return value


def _contrast_eligibility(target, contrast_id):
    by_id = {row["cell_id"]: row for row in target["cells"]}
    spec = next((row for row in CONTRASTS if row[0] == contrast_id), None)
    if spec is None or set(by_id) != set(SCORED_CELLS):
        raise V2BError("assembly contrast/cell grid drift at reveal")
    _name, minuend, subtrahend, required = spec
    eligible = by_id[minuend]["eligible"] is True \
        and by_id[subtrahend]["eligible"] is True
    if required and not eligible:
        raise V2BError("required reveal contrast is structurally ineligible")
    return eligible, minuend, subtrahend, required


def _masked_family(masked, salt, model_id, contrast_id):
    model = next((row for row in masked["models"]
                  if row["model_id"] == model_id), None)
    opaque = family_id(salt, model_id, contrast_id)
    if model is None:
        raise V2BError("masked model missing during reveal")
    matches = [row for row in model["families"]
               if row["family_id"] == opaque]
    if len(matches) != 1:
        raise V2BError("opaque family mapping is missing or ambiguous")
    return opaque, matches[0]


def reconstruct_models(masked, assembly, salt):
    """Public deterministic mapping/decryption over the committed grid."""
    keys = masked["cohort"]["ordered_target_keys"]
    targets = assembly.get("targets")
    if not isinstance(targets, list) or len(targets) != N_TARGETS \
            or [row.get("key") for row in targets] != keys:
        raise V2BError("reveal assembly/masked cohort drift")
    public_models = []
    total_observed = total_padding = 0
    seen_opaque = set()
    for model_id in MODEL_ORDER:
        families = []
        for contrast_id in CONTRAST_IDS:
            opaque, masked_family = _masked_family(
                masked, salt, model_id, contrast_id)
            if opaque in seen_opaque:
                raise V2BError("revealed opaque family collision")
            seen_opaque.add(opaque)
            masked_rows = masked_family["rows"]
            rows = []
            n_observed = n_padding = 0
            spec = next(row for row in CONTRASTS if row[0] == contrast_id)
            for index, (target, masked_row) in enumerate(
                    zip(targets, masked_rows)):
                key = target["key"]
                if masked_row["target_key"] != key:
                    raise V2BError("masked/reveal target order drift")
                eligible, minuend, subtrahend, required = \
                    _contrast_eligibility(target, contrast_id)
                cipher = masked_row["ciphertext"]
                value = decrypt_delta(salt, model_id, contrast_id, key,
                                      cipher)
                if eligible:
                    verify_ciphertext(
                        salt, model_id, contrast_id, key, cipher, value)
                    delta = value
                    padding = False
                    n_observed += 1
                else:
                    # Bitwise replay, not numeric equality, proves registered
                    # +0.0 padding.  A real eligible +0.0 remains observed.
                    verify_ciphertext(
                        salt, model_id, contrast_id, key, cipher, None)
                    delta = None
                    padding = True
                    n_padding += 1
                identity = validate_identity("python", target["identity"])
                rows.append(dict(
                    target_index=index, target_key=key, module=identity[0],
                    structurally_eligible=eligible,
                    padding_filtered=padding, delta_bpb=delta))
            if n_observed + n_padding != N_TARGETS \
                    or required and n_observed != N_TARGETS:
                raise V2BError("revealed family observation accounting drift")
            family = dict(
                contrast_id=contrast_id, family_id=opaque, sign=1,
                minuend_cell=spec[1], subtrahend_cell=spec[2],
                required_for_fixed_n=spec[3], n_rows=N_TARGETS,
                n_observed=n_observed, n_padding_filtered=n_padding,
                rows=rows, rows_sha256=sha256_sorted_json(rows))
            families.append(family)
            total_observed += n_observed
            total_padding += n_padding
        public_models.append(dict(
            model_id=model_id, n_families=N_CONTRASTS, families=families,
            families_sha256=sha256_sorted_json(families)))
    if len(seen_opaque) != N_MODELS * N_CONTRASTS:
        raise V2BError("revealed family coverage is incomplete")
    return public_models, total_observed, total_padding


def _reveal_bindings(fixed, fixed_record):
    bindings = copy.deepcopy(fixed["bindings"])
    bindings["fixed_n"] = dict(
        path=fixed_record["path"], schema=FIXED_N_SCHEMA,
        sha256=fixed_record["sha256"])
    if set(bindings) != set(REVEAL_BINDING_SCHEMAS):
        raise V2BError("reveal predecessor binding set drift")
    for label, schema in REVEAL_BINDING_SCHEMAS.items():
        _binding(bindings[label], schema, f"reveal {label}")
    return bindings


def _expected_ledger(fixed, fixed_binding, freeze_binding, registration):
    """Extend only the already-validated public fixed-N ledger projection."""
    rows = fixed.get("input_ledger", {}).get("entries")
    if not isinstance(rows, list):
        raise V2BError("fixed-N public input ledger is unavailable at reveal")
    expected = {}
    for row in rows:
        _exact_keys(row, {"label", "sha256"},
                    "fixed-N public input-ledger row at reveal")
        if row["label"] in expected or not isinstance(row["label"], str) \
                or not row["label"] or not _hex(row["sha256"]):
            raise V2BError("malformed fixed-N public ledger at reveal")
        expected[row["label"]] = row["sha256"]
    if expected.get("implementation_freeze") != freeze_binding["sha256"]:
        raise V2BError("fixed-N ledger binds another implementation freeze")
    expected.update(fixed_n=fixed_binding["sha256"],
                    registered_analyzer=registration["analyzer_sha256"])
    return expected


def _validate_commitment_anchor(commitment_record, binding, salt):
    commitment = _record(
        commitment_record, SALT_COMMITMENT_SCHEMA,
        "reveal salt commitment anchor")
    validate_commitment(commitment)
    expected_binding = dict(
        path=commitment_record["path"], schema=SALT_COMMITMENT_SCHEMA,
        sha256=commitment_record["sha256"])
    if binding != expected_binding \
            or salt_commitment(salt) != commitment["salt_sha256"]:
        raise V2BError("revealed salt does not match salt commitment")
    return commitment


def build_reveal_value(protocol, assembly, assembly_binding, salt_record,
                       study_record, model_inputs, provenance_inputs,
                       masked_record,
                       fixed_record, freeze_record, salt, input_ledger,
                       generator, ancestor_fn=None, current_sha_fn=None,
                       commit_sha_fn=None, replay_fn=replay_masked):
    """Pure constructor; fixed-N validation strictly precedes salt use."""
    if protocol.get("study_id") != STUDY_ID:
        raise V2BError("reveal protocol/study drift")
    ancestor_fn = ancestor_fn or git_is_ancestor
    generator = _generator(copy.deepcopy(generator))
    masked = _record(masked_record, MASKED_SCHEMA, "reveal masked input")
    validate_masked(masked, protocol, assembly)
    masked_binding = dict(
        path=masked_record["path"], schema=MASKED_SCHEMA,
        sha256=masked_record["sha256"])
    fixed = _record(fixed_record, FIXED_N_SCHEMA, "reveal fixed-N input")
    validate_fixed_n(fixed, protocol, assembly, masked, masked_binding)
    bindings = _reveal_bindings(fixed, fixed_record)
    if bindings["masked"] != masked_binding \
            or bindings["assembly"] != assembly_binding:
        raise V2BError("reveal fixed-N/masked/assembly binding drift")
    freeze = _record(
        freeze_record, IMPLEMENTATION_FREEZE_SCHEMA,
        "reveal implementation freeze")
    freeze_binding = bindings["implementation_freeze"]
    if freeze_record["path"] != freeze_binding["path"] \
            or freeze_record["sha256"] != freeze_binding["sha256"]:
        raise V2BError("reveal implementation freeze exact binding drift")
    validate_implementation_freeze(freeze, freeze_binding, protocol)
    frozen_files = _freeze_file_index(freeze)
    if generator["source_tree_hash"] != freeze["source_tree_hash"] \
            or generator["program_sha256"] != frozen_files[PROGRAM]["sha256"]:
        raise V2BError("reveal generator differs from implementation freeze")
    registration = analysis_registration(
        freeze, freeze_binding, masked["ancestry"]["scoring_source_commit"],
        ancestor_fn, current_sha_fn=current_sha_fn,
        commit_sha_fn=commit_sha_fn)

    # Only now may private material or numerical payloads be used.
    commitment = _record(
        salt_record, SALT_COMMITMENT_SCHEMA, "reveal salt commitment")
    _validate_commitment_anchor(
        salt_record, bindings["salt_commitment"], salt)
    replay_fn(
        masked, protocol, assembly, assembly_binding, salt_record,
        study_record, model_inputs, provenance_inputs, salt,
        ancestor_fn=ancestor_fn)
    if not ancestor_fn(fixed["ancestry"]["fixed_n_source_commit"],
                       generator["source_commit"]):
        raise V2BError("fixed-N/reveal source ancestry is not monotone")

    models, n_observed, n_padding = reconstruct_models(
        masked, assembly, salt)
    expected_ledger = _expected_ledger(
        fixed, bindings["fixed_n"], freeze_binding, registration)
    validate_input_ledger(input_ledger, expected_ledger)
    ancestry = dict(
        salt_commitment_adoption_commit=fixed["ancestry"][
            "salt_commitment_adoption_commit"],
        scoring_source_commit=fixed["ancestry"]["scoring_source_commit"],
        masking_source_commit=fixed["ancestry"]["masking_source_commit"],
        fixed_n_source_commit=fixed["ancestry"]["fixed_n_source_commit"],
        reveal_source_commit=generator["source_commit"], verified=True)
    value = dict(
        schema=REVEAL_SCHEMA, state=REVEAL_STATE, study_id=STUDY_ID,
        repo="sympy", language="python",
        corpus_git_sha=protocol["scope"]["corpus_git_sha"],
        protocol=protocol_record(), bindings=bindings, ancestry=ancestry,
        analysis_registration=registration,
        salt_commitment_sha256=commitment["salt_sha256"],
        revealed_salt_hex=salt.hex(),
        cohort=copy.deepcopy(masked["cohort"]),
        grid=dict(
            model_ids=list(MODEL_ORDER), contrast_ids=list(CONTRAST_IDS),
            n_models=N_MODELS, n_targets=N_TARGETS,
            n_contrasts_per_model=N_CONTRASTS,
            n_ciphertexts=N_MASKED_ROWS, n_observed=n_observed,
            n_padding_filtered=n_padding),
        models=models, models_sha256=sha256_sorted_json(models),
        input_ledger=copy.deepcopy(input_ledger),
        generator=copy.deepcopy(generator))
    return validate_reveal(
        value, protocol, assembly, masked, masked_binding, fixed,
        bindings["fixed_n"], freeze, freeze_binding, salt_record, ancestor_fn,
        current_sha_fn=current_sha_fn, commit_sha_fn=commit_sha_fn)


def validate_reveal(value, protocol, assembly, masked, masked_binding,
                    fixed, fixed_binding, freeze, freeze_binding,
                    commitment_record, ancestor_fn=git_is_ancestor,
                    current_sha_fn=None,
                    commit_sha_fn=None):
    """Strict public validator; the published salt replays all payloads."""
    _exact_keys(value, TOP_KEYS, "confirmation reveal")
    validate_masked(masked, protocol, assembly)
    validate_fixed_n(fixed, protocol, assembly, masked, masked_binding)
    validate_implementation_freeze(freeze, freeze_binding, protocol)
    if value["schema"] != REVEAL_SCHEMA or value["state"] != REVEAL_STATE \
            or value["study_id"] != STUDY_ID or value["repo"] != "sympy" \
            or value["language"] != "python" \
            or value["corpus_git_sha"] != \
            protocol["scope"]["corpus_git_sha"] \
            or value["protocol"] != protocol_record():
        raise V2BError("confirmation reveal identity drift")
    expected_bindings = copy.deepcopy(fixed["bindings"])
    expected_bindings["fixed_n"] = copy.deepcopy(fixed_binding)
    if value["bindings"] != expected_bindings \
            or value["bindings"].get("implementation_freeze") != \
            freeze_binding:
        raise V2BError("confirmation reveal predecessor binding drift")
    for label, schema in REVEAL_BINDING_SCHEMAS.items():
        _binding(value["bindings"][label], schema, f"reveal {label}")
    ancestry = value["ancestry"]
    _exact_keys(ancestry, {
        "salt_commitment_adoption_commit", "scoring_source_commit",
        "masking_source_commit", "fixed_n_source_commit",
        "reveal_source_commit", "verified"}, "reveal ancestry")
    if any(not _hex(ancestry[name], 40) for name in (
            "salt_commitment_adoption_commit", "scoring_source_commit",
            "masking_source_commit", "fixed_n_source_commit",
            "reveal_source_commit")) or ancestry["verified"] is not True \
            or any(ancestry[name] != fixed["ancestry"][name]
                   for name in ("salt_commitment_adoption_commit",
                                "scoring_source_commit",
                                "masking_source_commit",
                                "fixed_n_source_commit")):
        raise V2BError("reveal/fixed-N ancestry drift")
    _generator(value["generator"])
    frozen_files = _freeze_file_index(freeze)
    if value["generator"]["source_commit"] != \
            ancestry["reveal_source_commit"] \
            or value["generator"]["source_tree_hash"] != \
            freeze["source_tree_hash"] \
            or value["generator"]["program_sha256"] != \
            frozen_files[PROGRAM]["sha256"]:
        raise V2BError("reveal generator/freeze drift")
    committed_blob_sha = commit_sha_fn or _git_blob_sha256
    if committed_blob_sha(
            ancestry["reveal_source_commit"], PROGRAM) != \
            frozen_files[PROGRAM]["sha256"]:
        raise V2BError("reveal execution-commit source differs from freeze")
    ancestry_chain = (
        (freeze["implementation_commit"],
         ancestry["salt_commitment_adoption_commit"]),
        (ancestry["salt_commitment_adoption_commit"],
         ancestry["scoring_source_commit"]),
        (ancestry["scoring_source_commit"],
         ancestry["masking_source_commit"]),
        (ancestry["masking_source_commit"],
         ancestry["fixed_n_source_commit"]),
        (ancestry["fixed_n_source_commit"],
         ancestry["reveal_source_commit"]),
    )
    if any(not ancestor_fn(older, newer)
           for older, newer in ancestry_chain):
        raise V2BError("reveal committed ancestry chain does not replay")
    validate_analysis_registration(
        value["analysis_registration"], freeze, freeze_binding,
        ancestry["scoring_source_commit"], ancestor_fn,
        current_sha_fn=current_sha_fn, commit_sha_fn=commit_sha_fn)
    salt_hex = value["revealed_salt_hex"]
    if not _hex(salt_hex, 64):
        raise V2BError("revealed salt is not exact 32-byte lowercase hex")
    salt = bytes.fromhex(salt_hex)
    commitment = _validate_commitment_anchor(
        commitment_record, value["bindings"]["salt_commitment"], salt)
    if value["salt_commitment_sha256"] != commitment["salt_sha256"]:
        raise V2BError("revealed salt/public commitment mismatch")
    expected_models, n_observed, n_padding = reconstruct_models(
        masked, assembly, salt)
    if value["cohort"] != masked["cohort"] \
            or value["models"] != expected_models \
            or value["models_sha256"] != sha256_sorted_json(expected_models):
        raise V2BError("revealed cohort/model reconstruction drift")
    expected_grid = dict(
        model_ids=list(MODEL_ORDER), contrast_ids=list(CONTRAST_IDS),
        n_models=N_MODELS, n_targets=N_TARGETS,
        n_contrasts_per_model=N_CONTRASTS,
        n_ciphertexts=N_MASKED_ROWS, n_observed=n_observed,
        n_padding_filtered=n_padding)
    if value["grid"] != expected_grid:
        raise V2BError("revealed grid/observation accounting drift")
    validate_input_ledger(
        value["input_ledger"], _expected_ledger(
            fixed, fixed_binding, freeze_binding,
            value["analysis_registration"]))
    return value


def load_reveal(path, protocol, assembly, masked, masked_binding, fixed,
                fixed_binding, freeze, freeze_binding, commitment_record,
                ancestor_fn=None):
    value, digest = load_json(path, REVEAL_SCHEMA)
    return validate_reveal(
        value, protocol, assembly, masked, masked_binding, fixed,
        fixed_binding, freeze, freeze_binding, commitment_record,
        ancestor_fn or git_is_ancestor), digest


def _load_record(path, schema):
    value, digest = load_json(path, schema)
    return dict(path=os.path.abspath(path), sha256=digest, value=value)


def prepare(private_salt_path, salt_commitment_path, assembly_path,
            study_complete_path, masked_path, fixed_n_path,
            implementation_freeze_path, protocol_path=PROTOCOL_PATH):
    """Production one-shot reveal with fixed-N-before-salt sequencing."""
    if not source_clean():
        raise V2BError("source tree dirty before confirmation reveal")
    if os.path.realpath(protocol_path) != os.path.realpath(PROTOCOL_PATH):
        raise V2BError("confirmation reveal requires canonical protocol")
    commit, tree = head_commit(), source_tree_hash()
    protocol, digest = load_protocol(protocol_path)
    if digest != PROTOCOL_RAW_SHA256:
        raise V2BError("confirmation reveal protocol raw digest drift")
    assembly_binding_raw, assembly = artifact_binding(
        assembly_path, ASSEMBLY_SCHEMA)
    assembly_binding = dict(
        path=assembly_binding_raw["path"], schema=ASSEMBLY_SCHEMA,
        sha256=assembly_binding_raw["sha256"])
    masked_record = _load_record(masked_path, MASKED_SCHEMA)
    masked_binding = dict(path=masked_record["path"], schema=MASKED_SCHEMA,
                          sha256=masked_record["sha256"])
    fixed_record = _load_record(fixed_n_path, FIXED_N_SCHEMA)
    freeze_record = _load_record(
        implementation_freeze_path, IMPLEMENTATION_FREEZE_SCHEMA)

    # Validate the full blind predecessor before discovering private values.
    validate_masked(masked_record["value"], protocol, assembly)
    validate_fixed_n(
        fixed_record["value"], protocol, assembly, masked_record["value"],
        masked_binding)
    if fixed_record["value"]["bindings"]["implementation_freeze"] != dict(
            path=freeze_record["path"], schema=IMPLEMENTATION_FREEZE_SCHEMA,
            sha256=freeze_record["sha256"]):
        raise V2BError("fixed-N binds another implementation freeze")
    validate_implementation_freeze(
        freeze_record["value"],
        fixed_record["value"]["bindings"]["implementation_freeze"],
        protocol)

    model_paths, target_paths, score_paths = _discover_paths(
        protocol_path, assembly_path, salt_commitment_path,
        study_complete_path)
    paths = list(score_paths) + [
        ("masked", masked_path), ("fixed_n", fixed_n_path),
        ("registered_analyzer", os.path.join(BASE, ANALYZER_PATH)),
    ]
    for _label, path in paths:
        require_committed(path)
    pre = _file_ledger(paths)
    salt_record, study_record, model_inputs = _load_discovered(
        model_paths, target_paths, study_complete_path,
        salt_commitment_path)
    provenance_inputs = _load_provenance_inputs(
        study_record, salt_commitment_path, live_freeze=True)
    discovered_freeze = provenance_inputs["implementation_freeze"]
    if discovered_freeze["path"] != freeze_record["path"] \
            or discovered_freeze["sha256"] != freeze_record["sha256"]:
        raise V2BError("reveal explicit/discovered implementation freeze drift")
    salt_before = load_salt_file(private_salt_path)
    generator = dict(
        program=PROGRAM, program_sha256=sha256_file(__file__),
        source_commit=commit, source_tree_hash=tree)
    value = build_reveal_value(
        protocol, assembly, assembly_binding, salt_record, study_record,
        model_inputs, provenance_inputs, masked_record, fixed_record,
        freeze_record, salt_before, ledger_record(pre, pre), generator,
        ancestor_fn=git_is_ancestor)
    salt_after = load_salt_file(private_salt_path)
    post = _file_ledger(paths)
    if salt_before != salt_after or pre != post or not source_clean() \
            or head_commit() != commit or source_tree_hash() != tree:
        raise V2BError("reveal inputs/source/private salt changed")
    value["input_ledger"] = ledger_record(pre, post)
    return validate_reveal(
        value, protocol, assembly, masked_record["value"], masked_binding,
        fixed_record["value"], value["bindings"]["fixed_n"],
        freeze_record["value"], value["bindings"]["implementation_freeze"],
        salt_record, git_is_ancestor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-salt", required=True)
    parser.add_argument("--salt-commitment", required=True)
    parser.add_argument("--assembly", required=True)
    parser.add_argument("--study-complete", required=True)
    parser.add_argument("--masked", required=True)
    parser.add_argument("--fixed-n", required=True)
    parser.add_argument("--implementation-freeze", required=True)
    parser.add_argument("--protocol", default=PROTOCOL_PATH)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise V2BError("refusing to overwrite registered confirmation reveal")
    value = prepare(
        args.private_salt, args.salt_commitment, args.assembly,
        args.study_complete, args.masked, args.fixed_n,
        args.implementation_freeze, args.protocol)
    digest = write_new_json(args.out, value)
    print(f"[v2b-confirmation-reveal] exact payloads={N_MASKED_ROWS} "
          f"observed={value['grid']['n_observed']} -> "
          f"{args.out} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, V2BError) as error:
        print(f"FATAL: {error}", file=sys.stderr)
        raise SystemExit(2)
