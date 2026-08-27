#!/usr/bin/env python3
"""V2-c formal reveal (ONE-SHOT; human-gated; DESIGN_V2 §10).

Opens the V2-c blind: reads the private salt against its committed
pre-score commitment, re-runs the UNCHANGED B3 producer with the real
salt per repo and requires the freshly built masked artifact to equal
the committed one exactly (proving the sealed artifacts derive from
these completions and this salt — nothing can be swapped after the
fact), inverts the masking through the reveal-frozen
`_reconstruct_family` identity, and applies the UNCHANGED pilot
inference (`_analyze_repo_rows`: unequal-cluster MoM, frozen t-tables,
Holm over E1a/E1b-IUT/E2, the +0.02 non-inferiority margin). The
artifact carries the adopted amendment's claim label and hash, its
required disclosures (anchor-sensitivity provenance, capacity
attrition, structurally-ineligible repos), and the revealed salt so
the entire chain is externally verifiable. Running this program IS the
unblinding: it must be invoked only on the campaign owner's explicit
go, passes `--confirm-reveal` with the amendment sha verbatim, and the
output is write-once at a fixed committed path."""
import argparse
import hashlib
import sys

from analyze_v2b_nll_exploratory import (
    CONTRAST_NAMES, _analyze_repo_rows, _reconstruct_family)
from prepare_v2b_masked_deltas import _read_salt, build_masked_deltas
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import V2BError, artifact_binding, load_json, \
    sha256_file, write_new_json
from v2b_v2c_governance import V2C_CLAIM_LABEL, V2C_PLAN_SCHEMA

V2C_REVEAL_SCHEMA = "v2c_confirmatory_reveal_v1"
REVEAL_OUT_PATH = "results_v2/v2c/V2C_REVEAL.json"
AMENDMENT_PATH = "results_v2/v2b/V2C_FEASIBILITY_AMENDMENT_DRAFT.md"
AMENDMENT_SHA256 = \
    "49ff6d8f9650921eeb02d0e0e404fa7d991f277a020fe783a10d4b1bced7bc37"
CAPACITY_AMENDMENT_PATH = \
    "results_v2/v2b/V2C_CAPACITY_ELIGIBILITY_AMENDMENT.md"
REPOS = ("mathlib4", "sympy")


def _require(condition, message):
    if not condition:
        raise V2BError(message)


def reveal_repo(repo, masked_path, complete_path, manifest_path,
                sample_path, candidates_path, salt,
                commitment_binding):
    masked_binding, committed_masked = artifact_binding(masked_path)
    # The committed artifact carries the seal-time generator block the
    # bare builder never returns (review blocker: comparing with it in
    # place refuses unconditionally). Its provenance fields encode the
    # anti-peek proof, so they are required — then stripped for the
    # value-equality gate.
    committed_generator = committed_masked.get("generator")
    _require(isinstance(committed_generator, dict)
             and committed_generator.get("program")
             == "prepare_v2b_masked_deltas.py"
             and bool(committed_generator.get("source_commit"))
             and bool(committed_generator.get("source_tree_hash")),
             f"committed masked artifact lacks its seal-time "
             f"provenance: {repo}")
    committed_value = {key: value
                       for key, value in committed_masked.items()
                       if key != "generator"}

    def _paths_stripped(value):
        # The frozen materialize precedent: bindings compare
        # path-insensitively — the sealed producer ran in a detached
        # worktree, so recorded absolute paths differ while every
        # sha256 is identity. Only keys literally named "path" inside
        # the bindings block are dropped, on both sides.
        if isinstance(value, dict):
            return {key: _paths_stripped(item)
                    for key, item in value.items() if key != "path"}
        if isinstance(value, list):
            return [_paths_stripped(item) for item in value]
        return value

    if isinstance(committed_value.get("bindings"), dict):
        committed_value = dict(
            committed_value,
            bindings=_paths_stripped(committed_value["bindings"]))
    rebuilt, private = build_masked_deltas(
        complete_path, manifest_path, sample_path, candidates_path,
        salt, commitment_binding)
    if isinstance(rebuilt.get("bindings"), dict):
        rebuilt = dict(rebuilt,
                       bindings=_paths_stripped(rebuilt["bindings"]))
    _require(rebuilt == committed_value,
             f"freshly rebuilt masked artifact differs from the "
             f"committed one: {repo} — the sealed chain does not "
             f"reproduce; refusing to reveal")
    run_identity = committed_masked.get("run_identity") or {}
    families = {}
    mappings = {}
    capacity = []
    for name in CONTRAST_NAMES:
        row = private.get(name)
        _require(isinstance(row, dict),
                 f"producer private mapping missing: {repo} {name}")
        mapping = dict(
            fid=row["fid"], sign=row["sign"], n_rows=row["n_rows"],
            removed_mean_bpb=row["removed_mean"],
            fsum_correction=row["fsum_correction"],
            total_centering_bpb=row["total_centering"])
        _require(mapping["fid"] in committed_masked.get("families", {}),
                 f"mapped family absent from sealed rows: {repo} {name}")
        families[name] = _reconstruct_family(
            committed_masked.get("language"),
            committed_masked["families"][mapping["fid"]],
            mapping, f"{repo} {name}")
        mappings[name] = mapping
    complete_value, _ = load_json(complete_path)
    for row in complete_value.get("target_artifacts") or ():
        target, _ = load_json(row["path"])
        for cell in target.get("cells") or ():
            if cell.get("capacity_excluded"):
                capacity.append(dict(
                    target_key=target.get("target_key"),
                    cell_id=cell.get("cell_id"),
                    prompt_tokens=cell.get("capacity_prompt_tokens"),
                    model_max=cell.get("capacity_model_max")))
    replayed = dict(
        language=committed_masked.get("language"),
        model=run_identity.get("model"),
        revision=run_identity.get("revision"),
        run_identity_sha256=(committed_masked.get("bindings") or {})
        .get("run_identity_sha256"),
        governance_verdict="v2c-amended-governance-planned",
        governance_repo_n=None,
        bindings=dict(
            assembly=(committed_masked.get("bindings") or {})
            .get("assembly"),
            completion=(committed_masked.get("bindings") or {})
            .get("completion"),
            battery=dict(sha256=run_identity.get(
                "pilot_battery_sha256"))),
        families=families)
    block = _analyze_repo_rows(repo, replayed)
    return dict(block,
                masked_sha256=masked_binding["sha256"],
                centering_by_contrast=mappings,
                capacity_exclusions=capacity)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-reveal", required=True,
                    help="the adopted amendment sha256, verbatim — the "
                         "campaign owner's explicit unblinding go")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--salt", required=True)
    ap.add_argument("--salt-commitment", required=True)
    ap.add_argument("--repo", action="append", required=True,
                    metavar="REPO=MASKED,COMPLETE,MANIFEST,SAMPLE,CAND")
    args = ap.parse_args()
    _require(args.confirm_reveal == AMENDMENT_SHA256,
             "the reveal go-token must be the adopted amendment sha256 "
             "verbatim; refusing")
    if not source_clean():
        raise V2BError("source tree is dirty outside results_v2")
    for path in (args.plan, args.salt_commitment, AMENDMENT_PATH,
                 CAPACITY_AMENDMENT_PATH):
        require_committed(path)
    plan_binding, plan = artifact_binding(args.plan, V2C_PLAN_SCHEMA)
    _require((plan.get("amendment") or {}).get("sha256")
             == AMENDMENT_SHA256, "plan does not bind the amendment")
    salt, commitment_binding = _read_salt(args.salt,
                                          args.salt_commitment)
    repo_specs = {}
    for spec in args.repo:
        repo, sep, rest = spec.partition("=")
        parts = rest.split(",")
        _require(bool(sep) and len(parts) == 5
                 and repo not in repo_specs,
                 f"malformed --repo spec: {spec!r}")
        repo_specs[repo] = parts
    _require(sorted(repo_specs) == sorted(REPOS),
             f"the reveal covers exactly {sorted(REPOS)} or nothing")
    for repo, parts in repo_specs.items():
        require_committed(parts[0])  # masked
        require_committed(parts[3])  # sample

    repos_out = {}
    for repo in sorted(repo_specs):
        repos_out[repo] = reveal_repo(repo, *repo_specs[repo], salt,
                                      commitment_binding)
    anchor_note = None
    anchor_sensitivity = {}
    threshold_sensitivity = {}
    for repo in REPOS:
        row = (plan.get("repos") or {}).get(repo, {})
        power = row.get("power") or {}
        if anchor_note is None:
            anchor_note = power.get("anchor_provenance")
        anchor_sensitivity[repo] = power.get("anchor_sensitivity")
        threshold_sensitivity[repo] = (row.get("primary_budget") or {}) \
            .get("threshold_sensitivity")
    _require(anchor_note is not None,
             "plan lacks the anchor-provenance disclosure")
    artifact = dict(
        schema=V2C_REVEAL_SCHEMA,
        claim_status=V2C_CLAIM_LABEL,
        amendment=dict(path=AMENDMENT_PATH, sha256=AMENDMENT_SHA256,
                       provenance="governance amended post-reveal"),
        capacity_amendment=dict(
            path=CAPACITY_AMENDMENT_PATH,
            sha256=sha256_file(CAPACITY_AMENDMENT_PATH)),
        disclosures=dict(
            anchor_provenance=anchor_note,
            anchor_sensitivity=anchor_sensitivity,
            threshold_sensitivity=threshold_sensitivity,
            structurally_ineligible_repos=sorted(
                r for r, row in (plan.get("repos") or {}).items()
                if row.get("verdict") == "structurally-ineligible"),
            n_by_repo={r: (plan.get("repos") or {}).get(r, {})
                       .get("repo_n") for r in REPOS},
            capacity_exclusions_total=sum(
                len(row["capacity_exclusions"])
                for row in repos_out.values())),
        revealed_salt_hex=salt.hex(),
        salt_commitment=dict(sha256=commitment_binding["sha256"]),
        plan=dict(sha256=plan_binding["sha256"]),
        repos=repos_out,
        generator=dict(source_commit=head_commit(),
                       source_tree_hash=source_tree_hash(),
                       program="finalize_v2c_reveal.py"))
    digest = write_new_json(REVEAL_OUT_PATH, artifact)
    print(f"V2C-REVEALED {sorted(repos_out)} {REVEAL_OUT_PATH} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
