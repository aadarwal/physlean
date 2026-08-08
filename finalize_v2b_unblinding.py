#!/usr/bin/env python3
"""§15.A14 post-governance salt reveal and unblinding boundary.

Opens the pre-score commit-reveal ONLY after blind N governance is
sealed: for all five corpora it verifies the COMMITTED governance
artifact binds its committed masked artifact and the same
sample/candidates/completion chain, verifies the revealed private salt
against the pre-score COMMITTED commitment, and then PROVES the entire
masking by determinism — build_masked_deltas is re-run with the
revealed salt over the hash-bound paired artifacts, and its public
output must EQUAL the committed masked artifact object-exactly (dict
equality, minus the generator stamp; file bytes are separately pinned
by the governance binding). That single equality simultaneously proves
the opaque family ids, the private signs, the removed centering, the
centered residuals, and the raw-delta reconstruction from the target
artifacts; no reconstruction path exists that bypasses the producer's
own frozen code. The GOVERNANCE artifact is proven the same way:
v2b_n_governance.analyze is recomputed from the committed masked chain
and the committed governance object must equal it exactly minus its
generator stamp — a committed-but-fabricated verdict cannot pass.

The write-once unblinding artifact publishes the revealed salt, the
contrast -> opaque-id mapping with signs, removed means, fsum
corrections, and total centering (raw delta = sign * published +
total_centering to FP roundoff; exact by forward replay), and the
per-repo governance verdicts, closing the commit-reveal loop with a
fully auditable record. It changes NO estimand and recomputes NO
governance number.

ORDERING GATE: every governance artifact and every masked artifact must
be an exact committed HEAD blob before any reveal — an uncommitted
governance run must never be unblinded around. Fail-closed on any
binding, salt, or reconstruction drift.

BEHAVIORAL GATE (§14.22 sequencing, adopted on independent behavior
audit): pilot contrasts stay masked while BOTH the target N AND the
behavioral completion n are governed; only the k4 aggregate pass-rate
tier check may be exposed early. NLL governance alone is therefore
INSUFFICIENT for any formal V2-b unblinding: prepare() hard-requires a
committed five-corpus behavioral-governance chain
({schema: "v2b_behavioral_governance_v1"}, each artifact binding its
repo's masked SHA). That producer is NOT yet implemented, so this
boundary is deliberately UNRUNNABLE in production until it exists —
the requirement is the disable.
"""
import argparse
import sys

from finalize_v2b_a6 import EXPECTED
from prepare_v2b_masked_deltas import (SALT_ALGORITHM, _read_salt,
                                       build_masked_deltas, family_id,
                                       family_sign)
from provenance import head_commit, source_clean, source_tree_hash
from v2b_behavioral_governance import (
    BEHAVIOR_GOVERNANCE_SCHEMA as BEHAVIORAL_GOVERNANCE_SCHEMA,
    validate_governance_bindings)
from v2b_a6_blind import require_committed
from v2b_common import (MASKED_DELTAS_SCHEMA, N_GOVERNANCE_SCHEMA,
                        UNBLINDING_SCHEMA, V2BError, artifact_binding,
                        write_new_json)
from v2b_n_governance import analyze as governance_analyze


def verify_repo_unblinding(masked_path, governance_path, complete_path,
                           manifest_path, sample_path, candidates_path,
                           salt, commitment_binding,
                           analyze_fn=governance_analyze):
    """One corpus: governance->masked binding, salt agreement, and the
    determinism proof. Pure; committed-ness is gated by prepare()."""
    masked_binding, masked = artifact_binding(masked_path,
                                              MASKED_DELTAS_SCHEMA)
    gov_binding, governance = artifact_binding(governance_path,
                                               N_GOVERNANCE_SCHEMA)
    repo = masked.get("repo")
    if not isinstance(repo, str) or not repo \
            or governance.get("repo") != repo:
        raise V2BError("masked/governance repo mismatch")
    masked_bindings = masked.get("bindings")
    if not isinstance(masked_bindings, dict):
        raise V2BError("masked artifact lacks bindings")
    gov_bindings = governance.get("bindings")
    if not isinstance(gov_bindings, dict) \
            or gov_bindings.get("masked_deltas", {}).get("sha256") != \
            masked_binding["sha256"] \
            or gov_bindings.get("sample", {}).get("sha256") != \
            masked_bindings.get("sample", {}).get("sha256") \
            or gov_bindings.get("candidates", {}).get("sha256") != \
            masked_bindings.get("candidates", {}).get("sha256") \
            or gov_bindings.get("completion", {}).get("sha256") != \
            masked_bindings.get("completion", {}).get("sha256"):
        raise V2BError(f"governance does not bind this exact masked "
                       f"chain: {repo}")
    verdict = governance.get("verdict")
    if verdict not in ("feasible", "infeasible"):
        raise V2BError(f"governance verdict is malformed: {verdict!r}")
    recorded_salt = masked_bindings.get("salt_commitment", {})
    if recorded_salt.get("sha256") != commitment_binding["sha256"] \
            or recorded_salt.get("salt_sha256") != \
            commitment_binding["salt_sha256"]:
        raise V2BError(f"masked salt commitment is not the revealed "
                       f"commitment: {repo}")
    # THE determinism proof: the committed public artifact must be the
    # exact output of the frozen producer under the revealed salt.
    rebuilt, private = build_masked_deltas(
        complete_path, manifest_path, sample_path, candidates_path, salt,
        recorded_salt)
    public = {key: value for key, value in masked.items()
              if key != "generator"}
    if rebuilt != public:
        raise V2BError(f"masked artifact does not reconstruct from the "
                       f"revealed salt and hash-bound paired artifacts: "
                       f"{repo}")
    # the governance object itself must recompute from the committed
    # masked chain: a committed-but-fabricated verdict cannot pass
    expected_governance = analyze_fn(masked_path, candidates_path,
                                     sample_path, complete_path)
    stripped = {key: value for key, value in governance.items()
                if key != "generator"}
    if stripped != expected_governance:
        raise V2BError(f"governance artifact does not recompute from "
                       f"the committed masked chain: {repo}")
    mapping = {}
    for name, row in sorted(private.items()):
        if row["fid"] != family_id(salt, repo, name) \
                or row["sign"] != family_sign(salt, repo, name) \
                or row["fid"] not in masked.get("families", {}):
            raise V2BError(f"opaque id/sign derivation drift: {repo} "
                           f"{name}")
        mapping[name] = dict(fid=row["fid"], sign=row["sign"],
                             n_rows=row["n_rows"],
                             removed_mean_bpb=row["removed_mean"],
                             fsum_correction=row["fsum_correction"],
                             total_centering_bpb=row["total_centering"])
    return dict(repo=repo,
                bindings=dict(masked=masked_binding,
                              governance=gov_binding,
                              completion=masked_bindings["completion"]),
                governance_verdict=verdict,
                governance_repo_n=governance.get("repo_n"),
                mapping=mapping,
                reconstructed_equal=True)


def build_unblinding(entries, salt_path, commitment_path,
                     analyze_fn=governance_analyze):
    """Pure five-corpus reveal; committed-ness gated by prepare()."""
    salt, commitment_binding = _read_salt(salt_path, commitment_path)
    rows = {}
    for entry in entries:
        row = verify_repo_unblinding(salt=salt,
                                     commitment_binding=commitment_binding,
                                     analyze_fn=analyze_fn, **entry)
        if row["repo"] in rows:
            raise V2BError(f"duplicate unblinding corpus {row['repo']}")
        rows[row["repo"]] = row
    if set(rows) != set(EXPECTED):
        raise V2BError("unblinding requires the exact five-corpus set")
    return dict(
        schema=UNBLINDING_SCHEMA,
        state="revealed-post-governance",
        algorithm=SALT_ALGORITHM,
        salt_commitment=commitment_binding,
        revealed_salt_hex=salt.hex(),
        repos={repo: rows[repo] for repo in sorted(rows)})


# §14.22: NLL governance alone is INSUFFICIENT — behavioral completion
# n is governed under the same masking. This forward contract is the
# production disable until the behavioral-governance producer exists.
# This module intentionally lands before the behavioral producer so the
# commit-reveal reconstruction itself can be reviewed prospectively.  A
# schema-shaped JSON is not evidence that the still-missing producer and its
# reliability estimator ran.  Keep the production entry point mechanically
# disabled until that implementation, its exact committed masked-outcomes
# FILE binding, its deterministic governance recomputation check, and its
# tests are committed.  The test suite asserts this constant is False; a flip
# must therefore deliberately amend both code and tests while replacing the
# forward schema check below with object-exact replay.  Merely fabricating
# five committed JSON files cannot open the salt.
PRODUCTION_UNBLINDING_ENABLED = False


def _verify_behavioral_chain(behavioral_paths, entries):
    """Five committed behavioral-governance artifacts, one per corpus,
    each binding its repo's exact masked SHA."""
    from v2b_common import sha256_file
    if not isinstance(behavioral_paths, (list, tuple)) \
            or len(behavioral_paths) != len(entries):
        raise V2BError(
            "formal V2-b unblinding requires one committed behavioral-"
            "governance artifact per corpus (§14.22): NLL governance "
            "alone is insufficient")
    seen = set()
    for path, entry in zip(behavioral_paths, entries):
        _, behavioral = artifact_binding(path,
                                         BEHAVIORAL_GOVERNANCE_SCHEMA)
        repo = behavioral.get("repo")
        _, masked = artifact_binding(entry["masked_path"],
                                     MASKED_DELTAS_SCHEMA)
        masked_sha = sha256_file(entry["masked_path"])
        try:
            validate_governance_bindings(
                behavioral.get("bindings"),
                expected_nll_sha256=masked_sha)
        except V2BError as err:
            raise V2BError(f"behavioral governance binding drift: "
                           f"{path}: {err}") from err
        if not isinstance(repo, str) or not repo or repo in seen \
                or repo != masked.get("repo"):
            raise V2BError(f"behavioral governance does not bind this "
                           f"masked chain: {path}")
        seen.add(repo)


def _require_committed_chain(commitment_path, masked_paths,
                             governance_paths, behavioral_paths):
    """Ordering gate: reveal ONLY around committed governance — every
    masked, NLL-governance, and behavioral-governance artifact plus the
    commitment must be exact committed HEAD blobs."""
    require_committed(commitment_path)
    for path in list(masked_paths) + list(governance_paths) \
            + list(behavioral_paths):
        require_committed(path)


def prepare(entries, salt_path, commitment_path, behavioral_paths):
    if not PRODUCTION_UNBLINDING_ENABLED:
        raise V2BError(
            "formal V2-b unblinding is disabled until the frozen "
            "behavioral-governance producer and deterministic "
            "recomputation gate are implemented (§14.22)")
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    _require_committed_chain(
        commitment_path,
        [entry["masked_path"] for entry in entries],
        [entry["governance_path"] for entry in entries],
        behavioral_paths)
    _verify_behavioral_chain(behavioral_paths, entries)
    artifact = build_unblinding(entries, salt_path, commitment_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during unblinding")
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="finalize_v2b_unblinding.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    for flag in ("--masked", "--governance", "--complete", "--manifest",
                 "--candidates", "--behavioral-governance"):
        ap.add_argument(flag, action="append", required=True,
                        help=f"{flag} artifact; repeat five times in the "
                             f"same corpus order")
    ap.add_argument("--sample", required=True)
    ap.add_argument("--salt", required=True)
    ap.add_argument("--salt-commitment", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    groups = (args.masked, args.governance, args.complete, args.manifest,
              args.candidates, args.behavioral_governance)
    if any(len(group) != 5 for group in groups):
        raise SystemExit("FATAL: --masked/--governance/--complete/"
                         "--manifest/--candidates/--behavioral-governance "
                         "must each appear exactly five times")
    entries = [dict(masked_path=masked, governance_path=governance,
                    complete_path=complete, manifest_path=manifest,
                    sample_path=args.sample, candidates_path=candidates)
               for masked, governance, complete, manifest, candidates, _
               in zip(*groups)]
    artifact = prepare(entries, args.salt, args.salt_commitment,
                       args.behavioral_governance)
    digest = write_new_json(args.out, artifact)
    # NEVER print the salt; the artifact is its only disclosure surface
    verdicts = "/".join(f"{repo}:{row['governance_verdict']}"
                        for repo, row in sorted(artifact["repos"].items()))
    print(f"[v2b-unblind] {verdicts} -> {args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
