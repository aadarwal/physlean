#!/usr/bin/env python3
"""Deterministic analysis of the separate V2-b NLL-only pilot reveal.

This consumer never calls a model.  It accepts only the exact exploratory
reveal schema, replays the frozen B3 masked-delta producer from the reveal's
hash-bound original inputs, verifies the committed blind-governance object,
and reconstructs every named per-target delta.  Inference is repo-specific,
target-equal, and cluster-aware: source modules are the random-effect clusters
under the already-frozen unequal-cluster method-of-moments estimator.

The output remains an exploratory, one-checkpoint NLL pilot.  It is not a
formal V2-b unblinding artifact and contains no behavioral conclusion.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys

from finalize_v2b_a6 import EXPECTED
from provenance import BASE, head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (
    ASSEMBLY_SCHEMA,
    BOUND_SAMPLE_SCHEMA,
    CANDIDATES_SCHEMA,
    MASKED_DELTAS_SCHEMA,
    N_GOVERNANCE_SCHEMA,
    SALT_COMMITMENT_SCHEMA,
    V2BError,
    artifact_binding,
    identity_key,
    load_json,
    sha256_file,
    sha256_sorted_json,
    validate_identity,
    write_new_json,
)
from v2b_n_governance import (
    T_0975_BY_DF,
    analyze as governance_analyze,
    variance_components,
)


ANALYSIS_SCHEMA = "v2b_nll_exploratory_analysis_v1"
# Frozen upstream constants are repeated here so importing this CPU analysis
# module does not transitively import the tokenizer/model evaluation stack.
NLL_EXPLORATORY_REVEAL_SCHEMA = "v2b_nll_exploratory_reveal_v1"
IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_exploratory_reveal_implementation_freeze_v1"
REVEAL_AMENDMENT_SHA256 = \
    "5b9053e00f081ff0e614375bb6eae2c4a93a8330cd2d67257889cf080b6982e2"
REVEAL_AMENDMENT_ADOPTION_COMMIT = \
    "eccd7638f39c34739273a70a82243546968dea14"
REVEAL_AMENDMENT_PATH = \
    "results_v2/v2b/NLL_ONLY_EXPLORATORY_REVEAL_AMENDMENT.md"
REVEAL_IMPLEMENTATION_FREEZE_PATH = (
    "results_v2/v2b/"
    "NLL_ONLY_EXPLORATORY_REVEAL_IMPLEMENTATION_FREEZE.json")
REVEAL_IMPLEMENTATION_FREEZE_SHA256 = \
    "57400b8808c761463c799a371725dc987b05a83239bbef9f0156e7988ee3c967"
REVEAL_IMPLEMENTATION_COMMIT = \
    "e337c0765cc8c82b651ebdaf3df1218eec547c6c"
REVEAL_FREEZE_ADOPTION_COMMIT = \
    "75f85c6c7c5d6acb33b9a22016445282aa6d4918"
REVEAL_SOURCE_TREE_SHA256 = \
    "0a2b29fda01af5c03d2f300e967526f008d098a7a81f797894931aa23bb84b81"
REPLAY_SOURCE_TREE_SHA256 = \
    "85dd90e8529ba9380669ffff6a3d9f396e5c65f5f2ef4d08128c920ecdb498c0"
REVEAL_SOURCE_FILES = {
    "finalize_v2b_nll_exploratory_reveal.py":
    "e7f216389232e95bc355f298818eaa1fa779a63c5af4e1804c603060c0a1cba5",
    "slurm/v2b_nll_exploratory_reveal.sbatch":
    "ad29b9a31348770af1a295dfaed5065a17bc62484303cad61d60bd3dbef4c580",
    "tests/test_finalize_v2b_nll_exploratory_reveal.py":
    "c83cc3737f15980ed1adb0c09a945652646e9c5b32855d28a0a7239b37b53410",
    "tests/test_v2b_nll_exploratory_reveal_job.py":
    "83f3f72d680476e0ed7eeb5d3549d2aeafde5a61e8275eaa52c3b2e64b5f5c52",
}
PAIRED_COMPLETE_SCHEMA = "v2b_paired_nll_complete_v2"
INPUT_LEDGER_SCHEMA = "v2b_nll_exploratory_analysis_input_ledger_v1"
DELTA_METRIC = "bpb"
DELTA_BUDGET_BYTES = 16384
SALT_ALGORITHM = (
    "commitment = SHA256(salt-32-bytes); opaque ids/signs = "
    "HMAC-SHA256(salt, '<domain>:<repo>:<contrast>') with "
    "domains b3mask:v2b:20260808 / b3flip:v2b:20260808")
CONTRASTS = (
    ("E1a", "k1", "k4:16384", ("k4:16384",)),
    ("E1b", "k3:16384", "k4:16384", ("k3:16384", "k4:16384")),
    ("E2", "k5:0:16384", "k4:16384",
     ("k5:0:16384", "k4:16384")),
)
ANALYSIS_AMENDMENT_PATH = (
    "results_v2/v2b/NLL_ONLY_EXPLORATORY_ANALYSIS_AMENDMENT.md")
ANALYSIS_AMENDMENT_SHA256 = (
    "78df62200524ccb86cedbab48cbf7f1a531682a0fc1a5f7f4470dd6d9a55b01c")
ANALYSIS_AMENDMENT_HISTORY = (
    "ea01307d202d4ef15cf129a4e45f71c4eaabec0c",
    "1eb64690f49d3ac7747c64c31f291f47f9cd390f",
    "71ef9e5e9e820b311ccf16dfb9ddb324d30c2a79",
    "518e0c034008bec7aff113636e906bb671bb37d8",
)
ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA = \
    "v2b_nll_exploratory_analysis_implementation_freeze_v1"
ANALYSIS_IMPLEMENTATION_FREEZE_PATH = (
    "results_v2/v2b/"
    "NLL_ONLY_EXPLORATORY_ANALYSIS_IMPLEMENTATION_FREEZE.json")
ANALYSIS_SOURCE_FILES = (
    "analyze_v2b_nll_exploratory.py",
    "tests/test_analyze_v2b_nll_exploratory.py",
)
PILOT_MODEL = "Qwen/Qwen2.5-Coder-1.5B"
PILOT_REVISION = "df3ce67c0e24480f20468b6ef2894622d69eb73b"
NONINFERIORITY_MARGIN_BPB = 0.02
ALPHA = 0.05
CONTRAST_NAMES = tuple(row[0] for row in CONTRASTS)

# Frozen one-sided 95% Student-t quantiles, df 1..19.  The pilot has at most
# 20 targets and therefore cannot need any other degree of freedom.
T_095_BY_DF = {
    1: 6.313751515, 2: 2.919985580, 3: 2.353363435,
    4: 2.131846786, 5: 2.015048373, 6: 1.943180281,
    7: 1.894578605, 8: 1.859548038, 9: 1.833112933,
    10: 1.812461123, 11: 1.795884819, 12: 1.782287556,
    13: 1.770933396, 14: 1.761310136, 15: 1.753050356,
    16: 1.745883676, 17: 1.739606726, 18: 1.734063607,
    19: 1.729132812,
}

REVEAL_STATE = "revealed-post-nll-governance-exploratory"
CLAIM_STATUS = "exploratory-nll-only-one-checkpoint-pilot"
FORMAL_STATUS = \
    "formal-unblinding-artifact-not-produced-joint-pilot-not-completed"
NLL_BLIND_STATUS = "destroyed-by-this-exploratory-reveal"
BEHAVIORAL_STATUS = \
    "not-governed-not-a-co-primary-fresh-confirmatory-sample-required"

REVEAL_KEYS = frozenset((
    "schema", "state", "claim_status", "formal_v2b_status",
    "nll_blind_status", "behavioral_status", "algorithm",
    "salt_commitment", "revealed_salt_hex", "repos",
    "prospective_amendment", "implementation_freeze",
    "replay_source_tree_sha256", "generator",
))
REPO_REVEAL_KEYS = frozenset((
    "repo", "bindings", "governance_verdict", "governance_repo_n",
    "mapping", "reconstructed_equal",
))
MAPPING_KEYS = frozenset((
    "fid", "sign", "n_rows", "removed_mean_bpb", "fsum_correction",
    "total_centering_bpb",
))


def _hex(value, length):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _number(value, label):
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value):
        raise V2BError(f"malformed {label}: {value!r}")
    return float(value)


def _binding_core(binding, schema, label):
    if not isinstance(binding, dict) \
            or set(binding) != {"path", "sha256", "schema"} \
            or not isinstance(binding.get("path"), str) \
            or not binding["path"] or not _hex(binding.get("sha256"), 64) \
            or binding.get("schema") != schema:
        raise V2BError(f"malformed {label} binding")
    return dict(path=os.path.abspath(binding["path"]),
                sha256=binding["sha256"], schema=schema)


def _load_exact_binding(binding, schema, label):
    expected = _binding_core(binding, schema, label)
    observed, value = artifact_binding(expected["path"], schema)
    if observed != expected:
        raise V2BError(f"{label} binding bytes/path drift")
    return value


def _path_history(path):
    proc = subprocess.run(
        ["git", "-C", BASE, "log", "--format=%H", "--", path],
        capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise V2BError(f"cannot inspect committed history for {path}")
    return proc.stdout.splitlines()


def _commit_file_sha256(commit, path):
    proc = subprocess.run(
        ["git", "-C", BASE, "show", f"{commit}:{path}"],
        capture_output=True)
    if proc.returncode != 0:
        raise V2BError(f"cannot read {path} at frozen commit {commit}")
    return hashlib.sha256(proc.stdout).hexdigest()


def _git_is_ancestor(older, newer):
    if not _hex(older, 40) or not _hex(newer, 40):
        return False
    proc = subprocess.run(
        ["git", "-C", BASE, "merge-base", "--is-ancestor", older, newer],
        capture_output=True)
    return proc.returncode == 0


def _require_ancestor(older, newer, label, ancestor_fn=_git_is_ancestor,
                      strict=False):
    if (strict and older == newer) or not ancestor_fn(older, newer):
        raise V2BError(f"commit ancestry failure: {label}")


def _reveal_amendment_binding(
        path=REVEAL_AMENDMENT_PATH,
        require_committed_fn=require_committed,
        history_fn=_path_history):
    absolute = os.path.join(BASE, path)
    committed = require_committed_fn(absolute)
    digest = sha256_file(absolute)
    if digest != REVEAL_AMENDMENT_SHA256 \
            or history_fn(path) != [REVEAL_AMENDMENT_ADOPTION_COMMIT] \
            or not isinstance(committed, dict) \
            or committed.get("sha256") != digest:
        raise V2BError("exploratory reveal amendment bytes/history drift")
    return dict(path=os.path.abspath(absolute), sha256=digest,
                adoption_commit=REVEAL_AMENDMENT_ADOPTION_COMMIT)


def _reveal_implementation_freeze_binding(
        path=REVEAL_IMPLEMENTATION_FREEZE_PATH,
        require_committed_fn=require_committed,
        history_fn=_path_history,
        commit_file_sha_fn=_commit_file_sha256,
        ancestor_fn=_git_is_ancestor):
    absolute = os.path.join(BASE, path)
    committed = require_committed_fn(absolute)
    artifact, digest = load_json(absolute, IMPLEMENTATION_FREEZE_SCHEMA)
    expected = dict(
        schema=IMPLEMENTATION_FREEZE_SCHEMA,
        state="frozen-before-score-inspection",
        implementation_commit=REVEAL_IMPLEMENTATION_COMMIT,
        source_tree_sha256=REVEAL_SOURCE_TREE_SHA256,
        replay_source_tree_sha256=REPLAY_SOURCE_TREE_SHA256,
        amendment=dict(sha256=REVEAL_AMENDMENT_SHA256,
                       adoption_commit=REVEAL_AMENDMENT_ADOPTION_COMMIT),
        files=REVEAL_SOURCE_FILES)
    if digest != REVEAL_IMPLEMENTATION_FREEZE_SHA256 \
            or artifact != expected \
            or history_fn(path) != [REVEAL_FREEZE_ADOPTION_COMMIT] \
            or not isinstance(committed, dict) \
            or committed.get("sha256") != digest:
        raise V2BError("exploratory reveal implementation-freeze drift")
    for source_path, expected_sha in REVEAL_SOURCE_FILES.items():
        history = history_fn(source_path)
        if REVEAL_IMPLEMENTATION_COMMIT not in history \
                or sha256_file(os.path.join(BASE, source_path)) != expected_sha \
                or commit_file_sha_fn(REVEAL_IMPLEMENTATION_COMMIT,
                                      source_path) != expected_sha:
            raise V2BError(f"frozen reveal implementation drift: "
                           f"{source_path}")
    _require_ancestor(REVEAL_AMENDMENT_ADOPTION_COMMIT,
                      REVEAL_IMPLEMENTATION_COMMIT,
                      "reveal amendment -> reveal implementation",
                      ancestor_fn=ancestor_fn, strict=True)
    _require_ancestor(REVEAL_IMPLEMENTATION_COMMIT,
                      REVEAL_FREEZE_ADOPTION_COMMIT,
                      "reveal implementation -> reveal freeze",
                      ancestor_fn=ancestor_fn, strict=True)
    return dict(path=os.path.abspath(absolute), sha256=digest,
                schema=IMPLEMENTATION_FREEZE_SCHEMA,
                adoption_commit=REVEAL_FREEZE_ADOPTION_COMMIT,
                implementation_commit=REVEAL_IMPLEMENTATION_COMMIT,
                source_tree_sha256=REVEAL_SOURCE_TREE_SHA256)


def _analysis_amendment_binding(
        path=ANALYSIS_AMENDMENT_PATH,
        require_committed_fn=require_committed,
        history_fn=_path_history):
    """Bind the exact four-commit prospective analysis amendment."""
    absolute = os.path.join(BASE, path)
    committed = require_committed_fn(absolute)
    digest = sha256_file(absolute)
    history = history_fn(path)
    if digest != ANALYSIS_AMENDMENT_SHA256 \
            or history != list(ANALYSIS_AMENDMENT_HISTORY) \
            or not isinstance(committed, dict) \
            or committed.get("sha256") != digest:
        raise V2BError("exploratory analysis amendment bytes/history drift")
    return dict(path=os.path.abspath(absolute), sha256=digest,
                adoption_commits=list(ANALYSIS_AMENDMENT_HISTORY))


def _analysis_implementation_freeze_binding(
        path=ANALYSIS_IMPLEMENTATION_FREEZE_PATH,
        require_committed_fn=require_committed,
        history_fn=_path_history,
        commit_file_sha_fn=_commit_file_sha256,
        ancestor_fn=_git_is_ancestor):
    """Validate the one-touch freeze adopted after analyzer implementation."""
    absolute = os.path.join(BASE, path)
    committed = require_committed_fn(absolute)
    artifact, digest = load_json(
        absolute, ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA)
    expected_keys = {
        "schema", "state", "implementation_commit", "source_tree_sha256",
        "analysis_amendment", "files",
    }
    amendment = artifact.get("analysis_amendment")
    files = artifact.get("files")
    implementation_commit = artifact.get("implementation_commit")
    if set(artifact) != expected_keys \
            or artifact.get("state") != "frozen-before-nll-reveal" \
            or not _hex(implementation_commit, 40) \
            or artifact.get("source_tree_sha256") != source_tree_hash() \
            or amendment != dict(
                sha256=ANALYSIS_AMENDMENT_SHA256,
                adoption_commits=list(ANALYSIS_AMENDMENT_HISTORY)) \
            or not isinstance(files, dict) \
            or set(files) != set(ANALYSIS_SOURCE_FILES):
        raise V2BError("exploratory analysis implementation freeze malformed")
    for source_path, expected_sha in files.items():
        absolute_source = os.path.join(BASE, source_path)
        if not _hex(expected_sha, 64) \
                or sha256_file(absolute_source) != expected_sha \
                or implementation_commit not in history_fn(source_path) \
                or commit_file_sha_fn(implementation_commit,
                                      source_path) != expected_sha:
            raise V2BError(f"frozen analysis implementation drift: "
                           f"{source_path}")
    freeze_history = history_fn(path)
    if len(freeze_history) != 1 or not _hex(freeze_history[0], 40):
        raise V2BError("analysis implementation freeze must be one-touch")
    _require_ancestor(ANALYSIS_AMENDMENT_HISTORY[0],
                      implementation_commit,
                      "analysis amendment -> analysis implementation",
                      ancestor_fn=ancestor_fn, strict=True)
    _require_ancestor(implementation_commit, freeze_history[0],
                      "analysis implementation -> analysis freeze",
                      ancestor_fn=ancestor_fn, strict=True)
    if not isinstance(committed, dict) \
            or committed.get("sha256") != digest:
        raise V2BError("analysis implementation-freeze HEAD binding drift")
    return dict(path=os.path.abspath(absolute), sha256=digest,
                schema=ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA,
                adoption_commit=freeze_history[0],
                implementation_commit=implementation_commit,
                source_tree_sha256=artifact["source_tree_sha256"])


def _validate_analysis_provenance(amendment, freeze):
    expected_amendment_path = os.path.abspath(os.path.join(
        BASE, ANALYSIS_AMENDMENT_PATH))
    if not isinstance(amendment, dict) \
            or set(amendment) != {"path", "sha256", "adoption_commits"} \
            or amendment.get("path") != expected_amendment_path \
            or amendment.get("sha256") != ANALYSIS_AMENDMENT_SHA256 \
            or amendment.get("adoption_commits") != \
            list(ANALYSIS_AMENDMENT_HISTORY):
        raise V2BError("analysis amendment output binding drift")
    freeze_keys = {
        "path", "sha256", "schema", "adoption_commit",
        "implementation_commit", "source_tree_sha256",
    }
    expected_freeze_path = os.path.abspath(os.path.join(
        BASE, ANALYSIS_IMPLEMENTATION_FREEZE_PATH))
    if not isinstance(freeze, dict) or set(freeze) != freeze_keys \
            or freeze.get("path") != expected_freeze_path \
            or not _hex(freeze.get("sha256"), 64) \
            or freeze.get("schema") != \
            ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA \
            or not _hex(freeze.get("adoption_commit"), 40) \
            or not _hex(freeze.get("implementation_commit"), 40) \
            or not _hex(freeze.get("source_tree_sha256"), 64):
        raise V2BError("analysis implementation-freeze output binding drift")


def _validate_reveal_provenance(amendment, freeze):
    expected_amendment = dict(
        path=os.path.abspath(os.path.join(BASE, REVEAL_AMENDMENT_PATH)),
        sha256=REVEAL_AMENDMENT_SHA256,
        adoption_commit=REVEAL_AMENDMENT_ADOPTION_COMMIT)
    expected_freeze = dict(
        path=os.path.abspath(os.path.join(
            BASE, REVEAL_IMPLEMENTATION_FREEZE_PATH)),
        sha256=REVEAL_IMPLEMENTATION_FREEZE_SHA256,
        schema=IMPLEMENTATION_FREEZE_SCHEMA,
        adoption_commit=REVEAL_FREEZE_ADOPTION_COMMIT,
        implementation_commit=REVEAL_IMPLEMENTATION_COMMIT,
        source_tree_sha256=REVEAL_SOURCE_TREE_SHA256)
    if amendment != expected_amendment:
        raise V2BError("authenticated reveal amendment binding drift")
    if freeze != expected_freeze:
        raise V2BError("authenticated reveal implementation-freeze drift")


def _require_analysis_before_reveal(reveal, analysis_freeze,
                                    ancestor_fn=_git_is_ancestor):
    generator = reveal.get("generator") if isinstance(reveal, dict) else None
    reveal_commit = generator.get("source_commit") \
        if isinstance(generator, dict) else None
    if not _hex(reveal_commit, 40):
        raise V2BError("reveal generator commit is malformed")
    required = (
        (REVEAL_IMPLEMENTATION_COMMIT,
         "reveal implementation -> reveal generator"),
        (REVEAL_FREEZE_ADOPTION_COMMIT,
         "reveal freeze -> reveal generator"),
        (ANALYSIS_AMENDMENT_HISTORY[0],
         "analysis amendment -> reveal generator"),
        (analysis_freeze["implementation_commit"],
         "analysis implementation -> reveal generator"),
        (analysis_freeze["adoption_commit"],
         "analysis freeze -> reveal generator"),
    )
    for older, label in required:
        _require_ancestor(older, reveal_commit, label,
                          ancestor_fn=ancestor_fn)


def _validate_reveal(reveal, reveal_amendment,
                     reveal_implementation_freeze,
                     analysis_implementation_freeze,
                     ancestor_fn=_git_is_ancestor):
    _validate_reveal_provenance(
        reveal_amendment, reveal_implementation_freeze)
    if not isinstance(reveal, dict) or set(reveal) != REVEAL_KEYS:
        raise V2BError("exploratory reveal top-level schema is not exact")
    expected = {
        "schema": NLL_EXPLORATORY_REVEAL_SCHEMA,
        "state": REVEAL_STATE,
        "claim_status": CLAIM_STATUS,
        "formal_v2b_status": FORMAL_STATUS,
        "nll_blind_status": NLL_BLIND_STATUS,
        "behavioral_status": BEHAVIORAL_STATUS,
        "algorithm": SALT_ALGORITHM,
        "replay_source_tree_sha256": REPLAY_SOURCE_TREE_SHA256,
    }
    for key, value in expected.items():
        if reveal.get(key) != value:
            raise V2BError(f"exploratory reveal {key} drift")

    try:
        salt = bytes.fromhex(reveal.get("revealed_salt_hex", ""))
    except ValueError as err:
        raise V2BError("exploratory reveal salt encoding is malformed") from err
    if len(salt) != 32 \
            or reveal["revealed_salt_hex"] != salt.hex():
        raise V2BError("exploratory reveal salt must be canonical 32-byte hex")
    commitment = reveal.get("salt_commitment")
    if not isinstance(commitment, dict) \
            or set(commitment) != {"path", "sha256", "schema",
                                   "salt_sha256"} \
            or not isinstance(commitment.get("path"), str) \
            or not commitment["path"] \
            or not _hex(commitment.get("sha256"), 64) \
            or not _hex(commitment.get("salt_sha256"), 64) \
            or hashlib.sha256(salt).hexdigest() != \
            commitment["salt_sha256"]:
        raise V2BError("exploratory reveal salt commitment drift")

    amendment = reveal.get("prospective_amendment")
    if amendment != reveal_amendment:
        raise V2BError("exploratory reveal amendment binding drift")
    freeze = reveal.get("implementation_freeze")
    if freeze != reveal_implementation_freeze:
        raise V2BError("exploratory reveal implementation-freeze drift")
    generator = reveal.get("generator")
    if not isinstance(generator, dict) \
            or set(generator) != {"source_commit", "source_tree_hash",
                                   "program"} \
            or generator.get("program") != \
            "finalize_v2b_nll_exploratory_reveal.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or generator.get("source_tree_hash") != \
            REVEAL_SOURCE_TREE_SHA256:
        raise V2BError("exploratory reveal generator drift")

    _require_analysis_before_reveal(
        reveal, analysis_implementation_freeze,
        ancestor_fn=ancestor_fn)

    repos = reveal.get("repos")
    if not isinstance(repos, dict) or set(repos) != set(EXPECTED):
        raise V2BError("analysis requires the exact five-corpus reveal")
    for repo, row in repos.items():
        if not isinstance(row, dict) or set(row) != REPO_REVEAL_KEYS \
                or row.get("repo") != repo \
                or row.get("reconstructed_equal") is not True \
                or row.get("governance_verdict") not in \
                ("feasible", "infeasible") \
                or not isinstance(row.get("bindings"), dict) \
                or set(row["bindings"]) != \
                {"masked", "governance", "completion"} \
                or not isinstance(row.get("mapping"), dict) \
                or set(row["mapping"]) != set(CONTRAST_NAMES):
            raise V2BError(f"malformed exploratory reveal repo row: {repo}")
        _binding_core(row["bindings"]["masked"], MASKED_DELTAS_SCHEMA,
                      f"{repo} masked")
        _binding_core(row["bindings"]["governance"], N_GOVERNANCE_SCHEMA,
                      f"{repo} governance")
        completion = row["bindings"]["completion"]
        if not isinstance(completion, dict) \
                or set(completion) != {"path", "sha256", "schema"} \
                or not isinstance(completion.get("path"), str) \
                or not completion["path"] \
                or not _hex(completion.get("sha256"), 64):
            raise V2BError(f"malformed {repo} completion binding")
        repo_n = row.get("governance_repo_n")
        if row["governance_verdict"] == "feasible":
            if not isinstance(repo_n, int) or isinstance(repo_n, bool) \
                    or not 200 <= repo_n <= 400:
                raise V2BError(f"malformed feasible governance N: {repo}")
        elif repo_n is not None:
            raise V2BError(f"infeasible governance has an N: {repo}")
        for name, mapping in row["mapping"].items():
            if not isinstance(mapping, dict) or set(mapping) != MAPPING_KEYS \
                    or not isinstance(mapping.get("fid"), str) \
                    or not mapping["fid"].startswith("fam-") \
                    or len(mapping["fid"]) != 20 \
                    or not isinstance(mapping.get("sign"), int) \
                    or isinstance(mapping["sign"], bool) \
                    or mapping["sign"] not in (-1, 1) \
                    or not isinstance(mapping.get("n_rows"), int) \
                    or isinstance(mapping["n_rows"], bool) \
                    or mapping["n_rows"] < 0:
                raise V2BError(f"malformed reveal mapping: {repo} {name}")
            center_fields = (mapping.get("removed_mean_bpb"),
                             mapping.get("fsum_correction"),
                             mapping.get("total_centering_bpb"))
            if mapping["n_rows"] == 0:
                if center_fields != (None, None, None):
                    raise V2BError(f"empty reveal mapping is centered: "
                                   f"{repo} {name}")
            elif any(not isinstance(value, (int, float))
                     or isinstance(value, bool) or not math.isfinite(value)
                     for value in center_fields):
                raise V2BError(f"nonempty reveal mapping lacks centering: "
                               f"{repo} {name}")
    return salt


def _canonical_target_row(language, key, residual, sign, centering):
    try:
        identity = json.loads(key)
    except (TypeError, ValueError) as err:
        raise V2BError(f"malformed masked target key {key!r}") from err
    identity = list(validate_identity(language, identity))
    if identity_key(language, identity) != key:
        raise V2BError(f"noncanonical masked target key {key!r}")
    value = sign * _number(residual, "masked residual") + centering
    if not math.isfinite(value):
        raise V2BError("reconstructed target delta is non-finite")
    return dict(target_key=key, module=identity[0], delta_bpb=value)


def _reconstruct_family(language, rows, mapping, label):
    if not isinstance(rows, list) or len(rows) != mapping["n_rows"]:
        raise V2BError(f"masked/reveal family row-count drift: {label}")
    if not rows:
        return []
    seen = set()
    output = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 2 \
                or not isinstance(row[0], str) or row[0] in seen:
            raise V2BError(f"malformed/duplicate masked row: {label}[{index}]")
        seen.add(row[0])
        output.append(_canonical_target_row(
            language, row[0], row[1], mapping["sign"],
            float(mapping["total_centering_bpb"])))
    if output != sorted(output, key=lambda row: row["target_key"]):
        raise V2BError(f"masked target rows are not canonical: {label}")
    mean = math.fsum(row["delta_bpb"] for row in output) / len(output)
    recorded = float(mapping["removed_mean_bpb"])
    tolerance = 32 * math.ulp(max(abs(mean), abs(recorded), 1.0))
    if abs(mean - recorded) > tolerance:
        raise V2BError(f"reconstructed family mean drift: {label}")
    return output


def _replay_repo(repo, reveal_row, salt, salt_commitment,
                 build_fn=None,
                 governance_fn=governance_analyze,
                 expected_model=PILOT_MODEL,
                 expected_revision=PILOT_REVISION):
    """Full production replay for one repository."""
    if build_fn is None:
        # Lazy because the frozen producer imports tokenizer/model modules even
        # though this replay path itself performs no model call.
        from prepare_v2b_masked_deltas import build_masked_deltas
        build_fn = build_masked_deltas
    masked_binding = _binding_core(
        reveal_row["bindings"]["masked"], MASKED_DELTAS_SCHEMA,
        f"{repo} masked")
    governance_binding = _binding_core(
        reveal_row["bindings"]["governance"], N_GOVERNANCE_SCHEMA,
        f"{repo} governance")
    masked = _load_exact_binding(masked_binding, MASKED_DELTAS_SCHEMA,
                                 f"{repo} masked")
    governance = _load_exact_binding(
        governance_binding, N_GOVERNANCE_SCHEMA, f"{repo} governance")
    if masked.get("repo") != repo or governance.get("repo") != repo \
            or masked.get("metric") != DELTA_METRIC \
            or masked.get("budget_bytes") != DELTA_BUDGET_BYTES:
        raise V2BError(f"masked/governance identity drift: {repo}")
    bindings = masked.get("bindings")
    if not isinstance(bindings, dict) \
            or bindings.get("salt_commitment") != salt_commitment \
            or bindings.get("completion") != \
            reveal_row["bindings"]["completion"]:
        raise V2BError(f"reveal does not bind the original B3 chain: {repo}")
    needed = ("completion", "assembly", "sample", "candidates")
    if any(not isinstance(bindings.get(name), dict)
           or not isinstance(bindings[name].get("path"), str)
           or not bindings[name]["path"] for name in needed):
        raise V2BError(f"masked artifact lacks a replay path: {repo}")
    run_identity = masked.get("run_identity")
    if not isinstance(run_identity, dict) \
            or run_identity.get("model") != expected_model \
            or run_identity.get("revision") != expected_revision:
        raise V2BError(f"exploratory analysis requires the exact pilot model: "
                       f"{repo}")

    replayed, private = build_fn(
        bindings["completion"]["path"], bindings["assembly"]["path"],
        bindings["sample"]["path"], bindings["candidates"]["path"],
        salt, bindings["salt_commitment"])
    public = {key: value for key, value in masked.items()
              if key != "generator"}
    if replayed != public:
        raise V2BError(f"masked rows do not replay from B3 inputs: {repo}")

    expected_governance = governance_fn(
        masked_binding["path"], bindings["candidates"]["path"],
        bindings["sample"]["path"], bindings["completion"]["path"])
    stripped_governance = {key: value for key, value in governance.items()
                           if key != "generator"}
    if expected_governance != stripped_governance:
        raise V2BError(f"governance does not replay during analysis: {repo}")
    if governance.get("verdict") != reveal_row["governance_verdict"] \
            or governance.get("repo_n") != reveal_row["governance_repo_n"]:
        raise V2BError(f"reveal governance summary drift: {repo}")

    expected_mapping = {}
    for name, private_row in private.items():
        expected_mapping[name] = dict(
            fid=private_row["fid"], sign=private_row["sign"],
            n_rows=private_row["n_rows"],
            removed_mean_bpb=private_row["removed_mean"],
            fsum_correction=private_row["fsum_correction"],
            total_centering_bpb=private_row["total_centering"])
    if reveal_row["mapping"] != expected_mapping:
        raise V2BError(f"reveal mapping differs from B3 replay: {repo}")

    families = masked.get("families")
    language = masked.get("language")
    if language not in ("lean", "python") or not isinstance(families, dict):
        raise V2BError(f"masked language/families malformed: {repo}")
    rows_by_name = {}
    for name in CONTRAST_NAMES:
        mapping = reveal_row["mapping"][name]
        if mapping["fid"] not in families:
            raise V2BError(f"mapped family absent from masked rows: "
                           f"{repo} {name}")
        rows_by_name[name] = _reconstruct_family(
            language, families[mapping["fid"]], mapping, f"{repo} {name}")
    if set(families) != {reveal_row["mapping"][name]["fid"]
                        for name in CONTRAST_NAMES}:
        raise V2BError(f"masked artifact has an unmapped family: {repo}")
    return dict(
        language=language,
        model=run_identity["model"], revision=run_identity["revision"],
        run_identity_sha256=bindings.get("run_identity_sha256"),
        governance_verdict=governance["verdict"],
        governance_repo_n=governance.get("repo_n"),
        bindings=dict(masked=masked_binding, governance=governance_binding,
                      completion=bindings["completion"]),
        families=rows_by_name)


def _regularized_beta(x, a, b):
    """Deterministic regularized incomplete beta for Student-t tails."""
    if not 0.0 <= x <= 1.0 or a <= 0.0 or b <= 0.0:
        raise V2BError("invalid incomplete-beta arguments")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0

    def continued_fraction(aa, bb, xx):
        max_iter, eps, fpmin = 256, 3e-14, 1e-300
        qab, qap, qam = aa + bb, aa + 1.0, aa - 1.0
        c = 1.0
        d = 1.0 - qab * xx / qap
        if abs(d) < fpmin:
            d = fpmin
        d = 1.0 / d
        h = d
        for m in range(1, max_iter + 1):
            m2 = 2 * m
            term = m * (bb - m) * xx / ((qam + m2) * (aa + m2))
            d = 1.0 + term * d
            if abs(d) < fpmin:
                d = fpmin
            c = 1.0 + term / c
            if abs(c) < fpmin:
                c = fpmin
            d = 1.0 / d
            h *= d * c
            term = -(aa + m) * (qab + m) * xx \
                / ((aa + m2) * (qap + m2))
            d = 1.0 + term * d
            if abs(d) < fpmin:
                d = fpmin
            c = 1.0 + term / c
            if abs(c) < fpmin:
                c = fpmin
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1.0) <= eps:
                return h
        raise V2BError("incomplete-beta continued fraction did not converge")

    log_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) \
        + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        value = bt * continued_fraction(a, b, x) / a
    else:
        value = 1.0 - bt * continued_fraction(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, value))


def student_t_cdf(value, df):
    if not isinstance(df, int) or isinstance(df, bool) or df <= 0 \
            or not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value):
        raise V2BError("malformed Student-t query")
    if value == 0:
        return 0.5
    absolute = abs(float(value))
    # Avoid overflowing the square for a finite but enormous t statistic.
    x = 0.0 if absolute > math.sqrt(sys.float_info.max) \
        else df / (df + absolute * absolute)
    tail_twice = _regularized_beta(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * tail_twice if value > 0 else 0.5 * tail_twice


def student_t_sf(value, df):
    # Symmetry is more accurate than 1-CDF in the positive tail.
    return student_t_cdf(-float(value), df)


def _inference(rows):
    if not isinstance(rows, list):
        raise V2BError("inference rows must be a list")
    if not rows:
        return dict(
            n_targets=0, n_modules=0, cluster_sizes=[],
            target_keys=[],
            target_range_bpb=None, degeneracy_floor_bpb=None,
            target_equal_mean_bpb=None, variance_components=None,
            standard_error_bpb=None, degrees_of_freedom=None,
            ci95_two_sided_bpb=None, lower_one_sided_95_bpb=None,
            upper_one_sided_95_bpb=None,
            inference_status="insufficient-clusters")
    deltas_by_module = {}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) \
                or set(row) != {"target_key", "module", "delta_bpb"} \
                or not isinstance(row["target_key"], str) \
                or row["target_key"] in seen \
                or not isinstance(row["module"], str) or not row["module"]:
            raise V2BError("malformed/duplicate target inference row")
        seen.add(row["target_key"])
        value = _number(row["delta_bpb"], "target delta")
        deltas_by_module.setdefault(row["module"], []).append(value)
    values = [value for module_values in deltas_by_module.values()
              for value in module_values]
    mean = math.fsum(values) / len(rows)
    target_range = max(values) - min(values)
    floor = 64.0 * math.ulp(max(1.0, max(abs(value) for value in values)))
    if not math.isfinite(mean) or not math.isfinite(target_range) \
            or not math.isfinite(floor):
        raise V2BError("target summary arithmetic is non-finite")
    components = variance_components(deltas_by_module)
    base = dict(
        n_targets=len(rows), n_modules=components["n_modules"],
        cluster_sizes=components["cluster_sizes"],
        target_keys=sorted(seen),
        target_range_bpb=target_range, degeneracy_floor_bpb=floor,
        target_equal_mean_bpb=mean, variance_components=components,
        standard_error_bpb=None, degrees_of_freedom=None,
        ci95_two_sided_bpb=None, lower_one_sided_95_bpb=None,
        upper_one_sided_95_bpb=None)
    if components["mode"] == "insufficient-clusters":
        return dict(base, inference_status="insufficient-clusters")
    df = components["n_modules"] - 1
    if df not in T_095_BY_DF or df not in T_0975_BY_DF:
        raise V2BError(f"no frozen Student-t quantiles for df={df}")
    n = len(rows)
    variance = components["sigma_b2"] * math.fsum(
        size * size for size in components["cluster_sizes"]) / (n * n) \
        + components["sigma_w2"] / n
    if variance < 0 or not math.isfinite(variance):
        raise V2BError("cluster-aware mean variance is negative/non-finite")
    se = math.sqrt(variance)
    if target_range <= floor or se <= floor:
        return dict(base, standard_error_bpb=se,
                    degrees_of_freedom=df,
                    ci95_two_sided_bpb=None,
                    lower_one_sided_95_bpb=None,
                    upper_one_sided_95_bpb=None,
                    inference_status="degenerate-zero-se")
    q2, q1 = T_0975_BY_DF[df], T_095_BY_DF[df]
    interval = [mean - q2 * se, mean + q2 * se]
    lower, upper = mean - q1 * se, mean + q1 * se
    if any(not math.isfinite(value) for value in (*interval, lower, upper)):
        raise V2BError("cluster-aware interval is non-finite")
    return dict(
        base, standard_error_bpb=se, degrees_of_freedom=df,
        ci95_two_sided_bpb=interval,
        lower_one_sided_95_bpb=lower,
        upper_one_sided_95_bpb=upper,
        inference_status="available")


def _p_greater(summary, null):
    if summary["inference_status"] != "available":
        return 1.0
    statistic = (summary["target_equal_mean_bpb"] - null) \
        / summary["standard_error_bpb"]
    return student_t_sf(statistic, summary["degrees_of_freedom"])


def _p_less(summary, null):
    if summary["inference_status"] != "available":
        return 1.0
    statistic = (summary["target_equal_mean_bpb"] - null) \
        / summary["standard_error_bpb"]
    return student_t_cdf(statistic, summary["degrees_of_freedom"])


def holm_adjust(pvalues):
    if not isinstance(pvalues, dict) or not pvalues:
        raise V2BError("Holm adjustment needs named p-values")
    normalized = {}
    for name, value in pvalues.items():
        value = _number(value, f"p-value {name}")
        if not 0.0 <= value <= 1.0:
            raise V2BError(f"p-value outside [0,1]: {name}")
        normalized[name] = value
    order = sorted(normalized, key=lambda name: (normalized[name], name))
    adjusted = {}
    running = 0.0
    total = len(order)
    for rank, name in enumerate(order):
        running = max(running, min(1.0, (total - rank) * normalized[name]))
        adjusted[name] = running
    return dict(method="model-based Holm-adjusted diagnostics",
                scope="E1a/E1b-IUT/E2 within repository",
                deterministic_tie_break="contrast-name-ascending",
                order=order, raw_pvalues={name: normalized[name]
                                          for name in sorted(normalized)},
                adjusted_pvalues={name: adjusted[name]
                                  for name in sorted(adjusted)})


def _contrast_record(name, rows, summary, raw_p, adjusted_p):
    orientations = {
        "E1a": "k1-k4:16384",
        "E1b": "k3:16384-k4:16384",
        "E2": "k5:0:16384-k4:16384",
    }
    positive = name in ("E1a", "E2")
    controlled = positive \
        and summary["inference_status"] == "available" \
        and summary["lower_one_sided_95_bpb"] > 0.0 \
        and adjusted_p <= ALPHA
    return dict(
        orientation=orientations[name], metric=DELTA_METRIC,
        budget_bytes=DELTA_BUDGET_BYTES,
        favorable_direction=("positive" if positive else
                             "smaller/noninferior"),
        target_rows=rows, inference=summary,
        raw_one_sided_pvalue=raw_p,
        holm_adjusted_pvalue=adjusted_p,
        exploratory_positive_model_based_diagnostic=controlled,
        interpretation_status=(
            "positive-model-based-diagnostic" if controlled
            else "positive-not-established") if positive else None)


def _analyze_repo_rows(repo, replayed):
    families = replayed.get("families")
    if not isinstance(families, dict) or set(families) != set(CONTRAST_NAMES):
        raise V2BError(f"replayed contrast family drift: {repo}")
    summaries = {}
    for name in CONTRAST_NAMES:
        rows = families[name]
        summaries[name] = _inference(rows)
        if rows != sorted(rows, key=lambda row: row["target_key"]):
            raise V2BError(f"replayed rows not canonical: {repo} {name}")
    keys = {name: set(summaries[name]["target_keys"])
            for name in CONTRAST_NAMES}
    if not keys["E1b"].issubset(keys["E1a"]) \
            or not keys["E2"].issubset(keys["E1a"]):
        raise V2BError(f"contrast eligibility nesting violated: {repo}")
    set_records = {
        name: dict(n=len(keys[name]), target_keys=sorted(keys[name]))
        for name in CONTRAST_NAMES}
    pairwise = {}
    for left, right in (("E1a", "E1b"), ("E1a", "E2"),
                        ("E1b", "E2")):
        shared = keys[left] & keys[right]
        pairwise[f"{left}&{right}"] = dict(
            n=len(shared), target_keys=sorted(shared))
    three_way = keys["E1a"] & keys["E1b"] & keys["E2"]
    e1a_intersection_rows = [row for row in families["E1a"]
                             if row["target_key"] in keys["E1b"]]
    if [row["target_key"] for row in e1a_intersection_rows] != \
            [row["target_key"] for row in families["E1b"]]:
        # Both are key-sorted, so equality also proves the exact shared set.
        raise V2BError(f"E1a/E1b assay intersection drift: {repo}")

    assay_summary = _inference(e1a_intersection_rows)
    raw_p = {
        "E1a": _p_greater(summaries["E1a"], 0.0),
        "E2": _p_greater(summaries["E2"], 0.0),
    }
    p_ni = _p_less(summaries["E1b"], NONINFERIORITY_MARGIN_BPB)
    p_active = _p_greater(assay_summary, NONINFERIORITY_MARGIN_BPB)
    raw_p["E1b"] = max(p_ni, p_active)       # intersection-union test
    multiplicity = holm_adjust(raw_p)
    adjusted = multiplicity["adjusted_pvalues"]

    e1b_upper = summaries["E1b"]["upper_one_sided_95_bpb"]
    active_lower = assay_summary["lower_one_sided_95_bpb"]
    available = e1b_upper is not None and active_lower is not None \
        and summaries["E1b"]["inference_status"] == "available" \
        and assay_summary["inference_status"] == "available"
    ni_pointwise = available and e1b_upper <= NONINFERIORITY_MARGIN_BPB
    active_pointwise = available \
        and active_lower >= NONINFERIORITY_MARGIN_BPB
    holm_ok = adjusted["E1b"] <= ALPHA
    compatible = ni_pointwise and active_pointwise and holm_ok
    if not available:
        assay_label = "inference-unavailable"
    elif not ni_pointwise:
        assay_label = "noninferiority-not-established"
    elif not active_pointwise:
        assay_label = "assay-insensitive-inconclusive"
    elif not holm_ok:
        assay_label = "multiplicity-not-established"
    else:
        assay_label = "interface-sufficiency-compatible-exploratory"

    contrasts = {
        name: _contrast_record(name, families[name], summaries[name],
                               raw_p[name], adjusted[name])
        for name in CONTRAST_NAMES}
    # E1b's substantive compatibility is governed by the joint assay, not by
    # the generic positive-direction field used only for E1a/E2.
    contrasts["E1b"]["exploratory_positive_model_based_diagnostic"] = False
    contrasts["E1b"]["interpretation_status"] = assay_label
    if repo == "physlib":
        physlib_status = "uninterpretable-pending-k4x-sensitivity"
        contrasts["E1a"]["exploratory_positive_model_based_diagnostic"] = \
            False
        contrasts["E1a"]["interpretation_status"] = physlib_status
        contrasts["E1b"]["interpretation_status"] = physlib_status
        compatible = False
        assay_label = physlib_status
    return dict(
        repo=repo, language=replayed.get("language"),
        model=replayed.get("model"), revision=replayed.get("revision"),
        run_identity_sha256=replayed.get("run_identity_sha256"),
        governance=dict(verdict=replayed.get("governance_verdict"),
                        repo_n=replayed.get("governance_repo_n")),
        bindings=replayed.get("bindings"),
        eligible_target_sets=set_records,
        eligible_intersections=dict(
            pairwise=pairwise,
            three_way=dict(n=len(three_way),
                           target_keys=sorted(three_way))),
        three_way_n=len(three_way),
        contrasts=contrasts,
        e1b_assay=dict(
            margin_bpb=NONINFERIORITY_MARGIN_BPB,
            intersection="E1a eligible intersect E1b eligible",
            target_rows=e1a_intersection_rows,
            e1a_on_intersection_inference=assay_summary,
            e1b_upper_one_sided_95_bpb=e1b_upper,
            e1a_intersection_lower_one_sided_95_bpb=active_lower,
            noninferiority_pvalue=p_ni,
            active_assay_pvalue=p_active,
            intersection_union_pvalue=raw_p["E1b"],
            holm_adjusted_pvalue=adjusted["E1b"],
            pointwise_noninferiority=bool(ni_pointwise),
            pointwise_active_assay=bool(active_pointwise),
            interface_compatible=bool(compatible), label=assay_label,
            requires_k4x_sensitivity=(repo == "physlib")),
        multiplicity=multiplicity)


def _ledger_entry(label, path, expected_sha256=None, recorded_path=None):
    if not isinstance(label, str) or not label \
            or not isinstance(path, str) or not path:
        raise V2BError("analysis input-ledger label/path malformed")
    absolute = os.path.abspath(path)
    digest = sha256_file(absolute)
    if expected_sha256 is not None \
            and (not _hex(expected_sha256, 64)
                 or digest != expected_sha256):
        raise V2BError(f"analysis input hash drift before replay: {label}")
    row = dict(label=label, path=absolute,
               realpath=os.path.realpath(absolute), sha256=digest)
    if recorded_path is not None:
        row["recorded_path"] = os.path.abspath(recorded_path)
    return row


def _resolve_target_path(recorded_path, complete_path):
    if not isinstance(recorded_path, str) or not recorded_path:
        raise V2BError("completion target ledger path is malformed")
    if os.path.exists(recorded_path):
        return recorded_path
    sibling = os.path.join(os.path.dirname(os.path.abspath(complete_path)),
                           os.path.basename(recorded_path))
    if not os.path.exists(sibling):
        raise V2BError(f"paired target artifact missing: {recorded_path}")
    return sibling


def _capture_input_ledger(reveal_path, reveal, reveal_binding,
                          reveal_amendment, reveal_freeze,
                          analysis_amendment, analysis_freeze):
    """Enumerate and hash every file read by provenance or B3 replay."""
    entries = []
    labels = set()

    def add(label, path, expected_sha=None, recorded_path=None):
        if label in labels:
            raise V2BError(f"duplicate analysis input-ledger label: {label}")
        labels.add(label)
        entries.append(_ledger_entry(label, path, expected_sha,
                                     recorded_path=recorded_path))

    add("reveal", reveal_path, reveal_binding["sha256"])
    add("reveal.amendment", reveal_amendment["path"],
        reveal_amendment["sha256"])
    add("reveal.implementation_freeze", reveal_freeze["path"],
        reveal_freeze["sha256"])
    add("analysis.amendment", analysis_amendment["path"],
        analysis_amendment["sha256"])
    add("analysis.implementation_freeze", analysis_freeze["path"],
        analysis_freeze["sha256"])
    for source_path, digest in sorted(REVEAL_SOURCE_FILES.items()):
        add(f"reveal.source.{source_path}", os.path.join(BASE, source_path),
            digest)
    analysis_freeze_value, analysis_freeze_digest = load_json(
        analysis_freeze["path"], ANALYSIS_IMPLEMENTATION_FREEZE_SCHEMA)
    if analysis_freeze_digest != analysis_freeze["sha256"]:
        raise V2BError("analysis freeze changed during ledger capture")
    analysis_files = analysis_freeze_value.get("files")
    if not isinstance(analysis_files, dict) \
            or set(analysis_files) != set(ANALYSIS_SOURCE_FILES):
        raise V2BError("analysis freeze source-file ledger drift")
    for source_path, digest in sorted(analysis_files.items()):
        add(f"analysis.source.{source_path}",
            os.path.join(BASE, source_path), digest)

    commitment = reveal["salt_commitment"]
    commitment_core = _binding_core(
        {key: commitment[key] for key in ("path", "sha256", "schema")},
        SALT_COMMITMENT_SCHEMA, "salt commitment")
    add("salt_commitment", commitment_core["path"],
        commitment_core["sha256"])
    _, commitment_value = artifact_binding(
        commitment_core["path"], SALT_COMMITMENT_SCHEMA)
    if commitment_value.get("salt_sha256") != commitment["salt_sha256"]:
        raise V2BError("salt commitment content differs from reveal")

    chain_schemas = {
        "completion": PAIRED_COMPLETE_SCHEMA,
        "assembly": ASSEMBLY_SCHEMA,
        "sample": BOUND_SAMPLE_SCHEMA,
        "candidates": CANDIDATES_SCHEMA,
    }
    for repo in sorted(EXPECTED):
        reveal_row = reveal["repos"][repo]
        masked_binding = _binding_core(
            reveal_row["bindings"]["masked"], MASKED_DELTAS_SCHEMA,
            f"{repo} masked")
        governance_binding = _binding_core(
            reveal_row["bindings"]["governance"], N_GOVERNANCE_SCHEMA,
            f"{repo} governance")
        add(f"{repo}.masked", masked_binding["path"],
            masked_binding["sha256"])
        add(f"{repo}.governance", governance_binding["path"],
            governance_binding["sha256"])
        _, masked = artifact_binding(masked_binding["path"],
                                     MASKED_DELTAS_SCHEMA)
        bindings = masked.get("bindings")
        if not isinstance(bindings, dict) \
                or bindings.get("salt_commitment") != commitment:
            raise V2BError(f"masked replay ledger chain drift: {repo}")
        completion_value = None
        completion_path = None
        for name, schema in chain_schemas.items():
            binding = _binding_core(bindings.get(name), schema,
                                    f"{repo} {name}")
            if name == "completion" \
                    and binding != _binding_core(
                        reveal_row["bindings"]["completion"], schema,
                        f"{repo} reveal completion"):
                raise V2BError(f"reveal completion ledger drift: {repo}")
            add(f"{repo}.{name}", binding["path"], binding["sha256"])
            if name == "completion":
                completion_path = binding["path"]
                _, completion_value = artifact_binding(
                    completion_path, PAIRED_COMPLETE_SCHEMA)
        targets = completion_value.get("target_artifacts") \
            if isinstance(completion_value, dict) else None
        if not isinstance(targets, list) or not targets:
            raise V2BError(f"completion has no target ledger: {repo}")
        for index, target in enumerate(targets):
            if not isinstance(target, dict) \
                    or not isinstance(target.get("path"), str) \
                    or not _hex(target.get("sha256"), 64):
                raise V2BError(f"malformed completion target ledger: "
                               f"{repo}[{index}]")
            resolved = _resolve_target_path(target["path"], completion_path)
            add(f"{repo}.target[{index:04d}]", resolved,
                target["sha256"], recorded_path=target["path"])

    entries.sort(key=lambda row: row["label"])
    body = dict(
        schema=INPUT_LEDGER_SCHEMA,
        algorithm="SHA256 exact-file bytes; identical pre/post ledger",
        entries=entries, n_entries=len(entries),
        n_unique_paths=len({row["path"] for row in entries}))
    body["ledger_sha256"] = sha256_sorted_json(body)
    return body


def analyze_value(reveal, reveal_binding, analysis_amendment,
                  analysis_implementation_freeze, reveal_amendment,
                  reveal_implementation_freeze,
                  replay_repo_fn=_replay_repo,
                  ancestor_fn=_git_is_ancestor):
    reveal_binding = _binding_core(
        reveal_binding, NLL_EXPLORATORY_REVEAL_SCHEMA,
        "exploratory reveal")
    _validate_analysis_provenance(
        analysis_amendment, analysis_implementation_freeze)
    salt = _validate_reveal(
        reveal, reveal_amendment, reveal_implementation_freeze,
        analysis_implementation_freeze, ancestor_fn=ancestor_fn)
    repos = {}
    for repo in sorted(EXPECTED):
        replayed = replay_repo_fn(
            repo, reveal["repos"][repo], salt, reveal["salt_commitment"])
        if not isinstance(replayed, dict):
            raise V2BError(f"repo replay returned no object: {repo}")
        repos[repo] = _analyze_repo_rows(repo, replayed)
    return dict(
        schema=ANALYSIS_SCHEMA,
        state="analyzed-exploratory-nll-only-one-checkpoint-pilot",
        claim_status=CLAIM_STATUS, formal_v2b_status=FORMAL_STATUS,
        metric=DELTA_METRIC, budget_bytes=DELTA_BUDGET_BYTES,
        target_weighting="equal-within-repository",
        cluster="source-module-identity-field-0",
        inference_contract=dict(
            model="one-way random-effects MoM on unequal source modules",
            variance=("sigma_b2*sum(n_g^2)/N^2 + sigma_w2/N"),
            two_sided_confidence=0.95, one_sided_confidence=0.95,
            t_0975_by_df=T_0975_BY_DF, t_095_by_df=T_095_BY_DF,
            pvalue_method=("Student-t CDF via deterministic regularized "
                           "incomplete beta"),
            diagnostic_calibration=(
                "model-based pilot summaries; not exact finite-sample or "
                "confirmatory error control"),
            degeneracy_floor=(
                "range or SE <= 64*ulp(max(1,max(abs(delta))))"),
            insufficient_cluster_pvalue=1.0,
            degenerate_zero_se_pvalue=1.0,
            noninferiority_margin_bpb=NONINFERIORITY_MARGIN_BPB,
            multiplicity=(
                "model-based Holm-adjusted diagnostics over "
                "E1a/E1b-IUT/E2 within repository")),
        bindings=dict(
            reveal=reveal_binding,
            prospective_reveal_amendment=reveal_amendment,
            reveal_implementation_freeze=reveal_implementation_freeze,
            prospective_analysis_amendment=analysis_amendment,
            analysis_implementation_freeze=
            analysis_implementation_freeze),
        repos={repo: repos[repo] for repo in sorted(repos)},
        language_pooling=False, model_pooling=False,
        behavioral_claim=False, formal_v2b_completed=False)


def analyze(reveal_path, replay_repo_fn=_replay_repo):
    reveal_binding, reveal = artifact_binding(
        reveal_path, NLL_EXPLORATORY_REVEAL_SCHEMA)
    analysis_amendment = _analysis_amendment_binding()
    analysis_freeze = _analysis_implementation_freeze_binding()
    reveal_amendment = _reveal_amendment_binding()
    reveal_freeze = _reveal_implementation_freeze_binding()
    return analyze_value(
        reveal, reveal_binding, analysis_amendment, analysis_freeze,
        reveal_amendment, reveal_freeze, replay_repo_fn=replay_repo_fn)


def _require_committed_inputs(reveal_path, reveal):
    require_committed(reveal_path)
    commitment = reveal.get("salt_commitment")
    if isinstance(commitment, dict) and isinstance(commitment.get("path"), str):
        require_committed(commitment["path"])
    repos = reveal.get("repos")
    if isinstance(repos, dict):
        for row in repos.values():
            bindings = row.get("bindings") if isinstance(row, dict) else None
            if not isinstance(bindings, dict):
                continue
            for name in ("masked", "governance"):
                binding = bindings.get(name)
                if isinstance(binding, dict) \
                        and isinstance(binding.get("path"), str):
                    require_committed(binding["path"])


def prepare(
        reveal_path, source_clean_fn=source_clean,
        head_commit_fn=head_commit, source_tree_hash_fn=source_tree_hash,
        artifact_binding_fn=artifact_binding,
        require_inputs_fn=_require_committed_inputs,
        analysis_amendment_fn=_analysis_amendment_binding,
        analysis_freeze_fn=_analysis_implementation_freeze_binding,
        reveal_amendment_fn=_reveal_amendment_binding,
        reveal_freeze_fn=_reveal_implementation_freeze_binding,
        capture_ledger_fn=_capture_input_ledger,
        analyze_value_fn=analyze_value,
        ancestor_fn=_git_is_ancestor):
    if not source_clean_fn():
        raise V2BError("analysis source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit_fn(), source_tree_hash_fn()
    reveal_binding, reveal = artifact_binding_fn(
        reveal_path, NLL_EXPLORATORY_REVEAL_SCHEMA)
    require_inputs_fn(reveal_path, reveal)
    analysis_amendment = analysis_amendment_fn()
    analysis_freeze = analysis_freeze_fn()
    reveal_amendment = reveal_amendment_fn()
    reveal_freeze = reveal_freeze_fn()
    _require_ancestor(
        reveal.get("generator", {}).get("source_commit"), commit_start,
        "reveal generator -> analysis execution",
        ancestor_fn=ancestor_fn)
    ledger_before = capture_ledger_fn(
        reveal_path, reveal, reveal_binding, reveal_amendment, reveal_freeze,
        analysis_amendment, analysis_freeze)
    artifact = analyze_value_fn(
        reveal, reveal_binding, analysis_amendment, analysis_freeze,
        reveal_amendment, reveal_freeze)

    # Re-read and re-authenticate every input after replay.  Comparing the
    # complete logical-label/path/realpath/hash ledger detects byte, path,
    # target-resolution, missing/extra-row, and chain-binding drift.
    post_binding, post_reveal = artifact_binding_fn(
        reveal_path, NLL_EXPLORATORY_REVEAL_SCHEMA)
    require_inputs_fn(reveal_path, post_reveal)
    post_analysis_amendment = analysis_amendment_fn()
    post_analysis_freeze = analysis_freeze_fn()
    post_reveal_amendment = reveal_amendment_fn()
    post_reveal_freeze = reveal_freeze_fn()
    ledger_after = capture_ledger_fn(
        reveal_path, post_reveal, post_binding, post_reveal_amendment,
        post_reveal_freeze, post_analysis_amendment,
        post_analysis_freeze)
    if ledger_after != ledger_before:
        raise V2BError("analysis input ledger drifted during replay")
    if not source_clean_fn() or head_commit_fn() != commit_start \
            or source_tree_hash_fn() != tree_start:
        raise V2BError("analysis source drifted during exploratory analysis")
    artifact["bindings"]["input_ledger"] = ledger_before
    artifact["provenance_ordering"] = dict(
        analysis_amendment_commit=ANALYSIS_AMENDMENT_HISTORY[0],
        analysis_implementation_commit=analysis_freeze[
            "implementation_commit"],
        analysis_freeze_adoption_commit=analysis_freeze["adoption_commit"],
        reveal_generator_commit=reveal["generator"]["source_commit"],
        analysis_execution_commit=commit_start,
        required_ancestry_verified=True)
    artifact["generator"] = dict(
        source_commit=commit_start, source_tree_hash=tree_start,
        program="analyze_v2b_nll_exploratory.py")
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reveal", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    artifact = prepare(args.reveal)
    digest = write_new_json(args.out, artifact)
    # No means, signs, per-target deltas, or decisions are printed.
    print(f"[v2b-nll-exploratory-analysis] five repositories -> "
          f"{args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
