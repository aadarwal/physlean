#!/usr/bin/env python3
"""§15.A14 B3 masked-delta producer: the mandatory pre-score boundary
between paired NLL artifacts and blind N governance.

Reconstructs every target artifact from one hash-bound paired
complete.json, requires the ENTIRE observed cell metadata grid to equal
the manifest grid (via eval_paired's shared _target_cell_rows — the
required contrast cells alone are insufficient), applies the FROZEN
complete-case eligibility per contrast family at B* using the
manifest-verified eligibility booleans, computes primary-bpb paired
deltas with the frozen orientations (E1a = k1 - k4; E1b = k3 - k4;
E2 = k5:0 - k4), and emits ONE public masked artifact whose three
families carry opaque fam-<16 hex> ids and BLIND rows only:
sign * (delta - family mean), fsum-centered with one final correction
so published family means are zero to within one ulp-scale residue.
The §15.A14 MoM components are invariant to the translation and flip,
so governance is numerically preserved.

MASKING CONSTRUCTION. One 32-byte private salt is generated PRE-SCORE
(write-once, mode 0600, POOL storage, never committed, never printed)
TOGETHER with a write-once PUBLIC commitment artifact
({schema: "v2b_salt_commitment_v1"} with state, algorithm, salt SHA256,
and generator provenance) — a printed hash alone proves nothing.
Masking requires that exact committed artifact and verifies the salt
against it. Opaque ids and signs derive from domain-separated
HMAC-SHA256(salt, contrast), so nothing public recomputes the mapping
until the salt is revealed after governance; no private sidecar is
needed because raw deltas and means reconstruct deterministically from
the hash-bound target artifacts once the salt is opened.

Fail-closed on ANY drift: completion/target/manifest/sample/candidates
hashes and schemas, exact completion-vs-manifest target-key equality,
per-target repo/language/corpus/run-identity/index/cell-count/generator
agreement, full-grid cell metadata equality, non-finite scores.
Pre-score code only — no model execution.
"""
import argparse
import hashlib
import hmac
import math
import os
import sys

from eval_paired import COMPLETE_SCHEMA, TARGET_SCHEMA, _target_cell_rows
from provenance import head_commit, source_clean, source_tree_hash
from v2b_a6_blind import require_committed
from v2b_common import (ASSEMBLY_SCHEMA, BOUND_SAMPLE_SCHEMA,
                        CANDIDATES_SCHEMA, MASKED_DELTAS_SCHEMA,
                        SALT_COMMITMENT_SCHEMA, V2BError, artifact_binding,
                        load_json, sha256_file, sha256_json,
                        write_new_json)

DELTA_METRIC = "bpb"
DELTA_BUDGET_BYTES = 16384                # B*
B3_MASK_DOMAIN = b"b3mask:v2b:20260808"
B3_FLIP_DOMAIN = b"b3flip:v2b:20260808"
SALT_ALGORITHM = ("commitment = SHA256(salt-32-bytes); opaque ids/signs = "
                  "HMAC-SHA256(salt, '<domain>:<repo>:<contrast>') with "
                  "domains b3mask:v2b:20260808 / b3flip:v2b:20260808")
# (name, minuend cell, subtrahend cell, frozen eligibility cells §14.2)
CONTRASTS = (
    ("E1a", "k1", "k4:16384", ("k4:16384",)),
    ("E1b", "k3:16384", "k4:16384", ("k3:16384", "k4:16384")),
    ("E2", "k5:0:16384", "k4:16384", ("k5:0:16384", "k4:16384")),
)


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def init_salt(salt_path, commitment_path):
    """Production entry: source-clean guarded salt + commitment pair."""
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    return _write_salt_pair(salt_path, commitment_path)


def _write_salt_pair(salt_path, commitment_path):
    """Write-once private salt (0600) + write-once PUBLIC commitment
    artifact with generator provenance."""
    commit_start, tree_start = head_commit(), source_tree_hash()
    salt = os.urandom(32)
    fd = os.open(salt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(salt.hex() + "\n")
    commitment = dict(
        schema=SALT_COMMITMENT_SCHEMA,
        state="committed-pre-score",
        algorithm=SALT_ALGORITHM,
        salt_sha256=hashlib.sha256(salt).hexdigest(),
        generator=dict(source_commit=commit_start,
                       source_tree_hash=tree_start,
                       program="prepare_v2b_masked_deltas.py"))
    write_new_json(commitment_path, commitment)
    return commitment["salt_sha256"]


def _read_salt(salt_path, commitment_path):
    """The salt is valid ONLY against its exact committed artifact."""
    binding, commitment = artifact_binding(commitment_path,
                                           SALT_COMMITMENT_SCHEMA)
    if commitment.get("state") != "committed-pre-score" \
            or commitment.get("algorithm") != SALT_ALGORITHM \
            or not _hex(commitment.get("salt_sha256")):
        raise V2BError("salt commitment artifact is malformed")
    try:
        text = open(salt_path, "r", encoding="ascii").read().strip()
        salt = bytes.fromhex(text)
    except (OSError, ValueError) as err:
        raise V2BError(f"cannot read private salt {salt_path}: {err}") \
            from err
    if len(salt) != 32:
        raise V2BError("private salt must be exactly 32 bytes")
    if hashlib.sha256(salt).hexdigest() != commitment["salt_sha256"]:
        raise V2BError("private salt does not match its public commitment")
    return salt, dict(binding, salt_sha256=commitment["salt_sha256"])


def _production_salt(salt_path, commitment_path):
    """Production boundary: the PUBLIC commitment must be an exact
    committed HEAD blob — an uncommitted commitment proves nothing."""
    require_committed(commitment_path)
    return _read_salt(salt_path, commitment_path)


def _derive(salt, domain, repo, contrast):
    message = domain + b":" + repo.encode("utf-8") + b":" \
        + contrast.encode("utf-8")
    return hmac.new(salt, message, hashlib.sha256).hexdigest()


def family_id(salt, repo, contrast):
    return "fam-" + _derive(salt, B3_MASK_DOMAIN, repo, contrast)[:16]


def family_sign(salt, repo, contrast):
    return 1 if int(_derive(salt, B3_FLIP_DOMAIN, repo, contrast),
                    16) % 2 == 0 else -1


def blind_rows(rows, sign):
    """(published rows, centering): published = sign * (delta - mean -
    fsum_correction), so the published family mean is zero to within one
    ulp-scale residue and the §15.A14 MoM components are invariant up to
    floating-point roundoff. The centering record exposes BOTH removed
    terms plus their sum (total_centering = mean + correction): raw
    deltas reconstruct as sign * published + total_centering to FP
    roundoff, and reconstruct EXACTLY by forward replay of this
    function over the hash-bound raw deltas."""
    if not rows:
        return [], None
    mean = math.fsum(delta for _, delta in rows) / len(rows)
    residuals = [(key, delta - mean) for key, delta in rows]
    correction = math.fsum(residual for _, residual in residuals) \
        / len(residuals)
    published = [[key, sign * (residual - correction)]
                 for key, residual in sorted(residuals)]
    return published, dict(removed_mean=mean, fsum_correction=correction,
                           total_centering=mean + correction)


def _grid_metadata(rows):
    return [{key: value for key, value in row.items() if key != "row"}
            for row in rows]


def _load_target(row, index, complete_dir, complete, manifest_binding,
                 manifest, manifest_row):
    path = row.get("path")
    if not isinstance(path, str) or not path:
        raise V2BError("completion target row lacks a path")
    if not os.path.exists(path):
        sibling = os.path.join(complete_dir, os.path.basename(path))
        if not os.path.exists(sibling):
            raise V2BError(f"paired target artifact missing: {path}")
        path = sibling
    if sha256_file(path) != row.get("sha256"):
        raise V2BError(f"paired target artifact hash drift: {path}")
    target, _ = load_json(path, TARGET_SCHEMA)
    if target.get("run_identity") != complete.get("run_identity") \
            or target.get("run_identity_sha256") != \
            complete.get("run_identity_sha256") \
            or target.get("target_key") != row.get("target_key") \
            or target.get("target_index") != index \
            or target.get("repo") != manifest.get("repo") \
            or target.get("language") != manifest.get("language") \
            or target.get("corpus_git_sha") != \
            manifest.get("corpus_git_sha") \
            or target.get("n_cells") != row.get("n_cells") \
            or target.get("assembly_manifest", {}).get("sha256") != \
            manifest_binding["sha256"]:
        raise V2BError(f"paired target artifact binding drift: {path}")
    if target.get("generator") != complete.get("generator"):
        raise V2BError(f"target generator does not equal the completion "
                       f"generator: {path}")
    if target.get("assembly_target_sha256") != sha256_json(manifest_row) \
            or target.get("target_identity") != \
            manifest_row.get("identity") \
            or target.get("prefix_sha256") != \
            manifest_row.get("prefix_sha256") \
            or target.get("prefix_bytes") != \
            manifest_row.get("prefix_bytes") \
            or target.get("body_sha256") != \
            manifest_row.get("body_sha256") \
            or target.get("body_bytes") != manifest_row.get("body_bytes"):
        raise V2BError(f"paired target does not rebind its manifest row: "
                       f"{target.get('target_key')}")
    cells = target.get("cells")
    if not isinstance(cells, list) or target.get("n_cells") != len(cells):
        raise V2BError(f"paired target cell table malformed: {path}")
    # The ENTIRE observed metadata grid must equal the manifest grid —
    # eval_paired's shared enumeration, so B3 and the evaluator's resume
    # audit can never disagree about what a complete target looks like.
    expected = _grid_metadata(_target_cell_rows(manifest_row))
    metadata_fields = tuple(expected[0]) if expected else ()
    observed = [{key: cell.get(key) for key in metadata_fields}
                if isinstance(cell, dict) else None for cell in cells]
    if not expected or observed != expected:
        raise V2BError(f"target cell grid does not equal the manifest "
                       f"grid: {target.get('target_key')}")
    cells_by_id = {cell["cell_id"]: cell for cell in cells}
    return cells_by_id


def _cell_bpb(cells_by_id, cell_id, target_key):
    """Recompute bpb from nll_nats / ln2 / scored_body_bytes so a
    self-consistently restamped target/completion cannot alter the
    governance scalar; the stored value must agree exactly."""
    cell = cells_by_id[cell_id]
    primary = cell.get("primary", {})
    nll = primary.get("nll_nats") if isinstance(primary, dict) else None
    ledger = cell.get("boundary_ledger")
    scored = ledger.get("scored_body_bytes") \
        if isinstance(ledger, dict) else None
    if not isinstance(nll, (int, float)) or isinstance(nll, bool) \
            or not math.isfinite(nll) or nll < 0 \
            or not isinstance(scored, int) or isinstance(scored, bool) \
            or scored <= 0:
        raise V2BError(f"malformed nll/scored-body-bytes: {target_key} "
                       f"{cell_id}")
    recomputed = nll / math.log(2) / scored
    if primary.get("bpb") != recomputed:
        raise V2BError(f"primary bpb does not recompute from "
                       f"nll/scored bytes: {target_key} {cell_id}")
    return recomputed


def build_masked_deltas(complete_path, manifest_path, sample_path,
                        candidates_path, salt, salt_commitment_binding):
    """Pure construction: (public masked artifact, private raw preview).

    The second element exists ONLY for synthetic tests and never leaves
    this function in production (prepare() discards it)."""
    complete_binding, complete = artifact_binding(complete_path,
                                                  COMPLETE_SCHEMA)
    manifest_binding, manifest = artifact_binding(manifest_path,
                                                  ASSEMBLY_SCHEMA)
    sample_binding, _sample = artifact_binding(sample_path,
                                               BOUND_SAMPLE_SCHEMA)
    cand_binding, _candidates = artifact_binding(candidates_path,
                                                 CANDIDATES_SCHEMA)
    run_identity = complete.get("run_identity")
    run_sha = complete.get("run_identity_sha256")
    if not isinstance(run_identity, dict) \
            or sha256_json(run_identity) != run_sha:
        raise V2BError("completion run identity hash drift")
    if complete.get("assembly_manifest", {}).get("sha256") != \
            manifest_binding["sha256"]:
        raise V2BError("completion is not bound to this assembly manifest")
    repo = complete.get("repo")
    if not isinstance(repo, str) or not repo \
            or manifest.get("repo") != repo:
        raise V2BError("completion/manifest repo mismatch")
    complete_generator = complete.get("generator")
    if complete.get("language") != manifest.get("language") \
            or complete.get("corpus_git_sha") != \
            manifest.get("corpus_git_sha") \
            or complete.get("ast_class_state") != \
            "not-run-separate-required-gate" \
            or not isinstance(complete_generator, dict) \
            or complete_generator.get("program") != "eval_paired.py" \
            or not _hex(complete_generator.get("source_commit"), 40) \
            or not _hex(complete_generator.get("source_tree_hash")):
        raise V2BError("completion language/corpus/AST/generator drift")
    if manifest.get("b_star") != DELTA_BUDGET_BYTES \
            or DELTA_BUDGET_BYTES not in manifest.get("budgets", []):
        raise V2BError(f"manifest does not govern B*={DELTA_BUDGET_BYTES}")
    bindings = manifest.get("bindings", {})
    if bindings.get("sample", {}).get("sha256") != \
            sample_binding["sha256"] \
            or bindings.get("candidates", {}).get("sha256") != \
            cand_binding["sha256"]:
        raise V2BError("manifest is not bound to this exact "
                       "sample/candidates pair")
    rows = complete.get("target_artifacts")
    manifest_targets = manifest.get("targets")
    if not isinstance(rows, list) or not rows \
            or not isinstance(manifest_targets, list) \
            or complete.get("n_targets") != len(rows) \
            or manifest.get("n_targets") != len(manifest_targets):
        raise V2BError("completion target table is malformed")
    completion_keys = [row.get("target_key") for row in rows]
    manifest_keys = [t.get("key") for t in manifest_targets]
    if completion_keys != manifest_keys:
        raise V2BError("completion target keys do not equal the manifest "
                       "target keys exactly")
    n_cells = [row.get("n_cells") for row in rows]
    if any(not isinstance(n, int) or isinstance(n, bool) or n <= 0
           for n in n_cells) or complete.get("n_cells") != sum(n_cells):
        raise V2BError("completion cell counts do not sum")

    raw_by_contrast = {name: [] for name, _, _, _ in CONTRASTS}
    complete_dir = os.path.dirname(os.path.abspath(complete_path))
    for index, (row, manifest_row) in enumerate(zip(rows,
                                                    manifest_targets)):
        cells_by_id = _load_target(row, index, complete_dir, complete,
                                   manifest_binding, manifest,
                                   manifest_row)
        key = row["target_key"]
        for name, minuend, subtrahend, eligibility in CONTRASTS:
            # eligibility booleans were verified EQUAL to the manifest by
            # the full-grid check; complete-case requires exactly True
            if not all(cells_by_id[cell_id].get("eligible") is True
                       for cell_id in eligibility):
                continue
            delta = _cell_bpb(cells_by_id, minuend, key) \
                - _cell_bpb(cells_by_id, subtrahend, key)
            raw_by_contrast[name].append((key, delta))

    families = {}
    private = {}
    for name, minuend, subtrahend, eligibility in CONTRASTS:
        fid = family_id(salt, repo, name)
        if fid in families:
            raise V2BError("opaque family id collision")
        sign = family_sign(salt, repo, name)
        raw_rows = raw_by_contrast[name]
        families[fid], centering = blind_rows(raw_rows, sign)
        private[name] = dict(
            fid=fid, sign=sign, n_rows=len(raw_rows),
            removed_mean=centering["removed_mean"] if centering else None,
            fsum_correction=centering["fsum_correction"]
            if centering else None,
            total_centering=centering["total_centering"]
            if centering else None)
    masked = dict(
        schema=MASKED_DELTAS_SCHEMA, repo=repo,
        language=manifest.get("language"),
        corpus_git_sha=manifest.get("corpus_git_sha"),
        metric=DELTA_METRIC, budget_bytes=DELTA_BUDGET_BYTES,
        run_identity=run_identity,
        bindings=dict(
            sample=sample_binding,
            candidates=cand_binding,
            assembly=manifest_binding,
            completion=complete_binding,
            run_identity_sha256=run_sha,
            salt_commitment=salt_commitment_binding),
        n_rows_by_family={fid: len(rows_) for fid, rows_ in
                          sorted(families.items())},
        families=families)
    return masked, private


def prepare(complete_path, manifest_path, sample_path, candidates_path,
            salt_path, salt_commitment_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    salt, commitment_binding = _production_salt(salt_path,
                                                salt_commitment_path)
    masked, _ = build_masked_deltas(complete_path, manifest_path,
                                    sample_path, candidates_path, salt,
                                    commitment_binding)
    # Scoring must have happened at exactly this committed HEAD/tree —
    # which also proves the committed salt artifact existed at scoring
    # HEAD, closing the commit-after-peek channel.
    complete_value, _ = load_json(complete_path, COMPLETE_SCHEMA)
    scored_generator = complete_value.get("generator", {})
    if scored_generator.get("source_commit") != commit_start \
            or scored_generator.get("source_tree_hash") != tree_start:
        raise V2BError("completion was not scored at the current "
                       "committed HEAD/tree")
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during masking")
    masked["generator"] = dict(source_commit=commit_start,
                               source_tree_hash=tree_start,
                               program="prepare_v2b_masked_deltas.py")
    return masked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-salt", metavar="SALT_PATH",
                    help="generate the write-once 0600 private salt AND "
                         "its write-once public commitment artifact")
    ap.add_argument("--commitment-out",
                    help="public commitment artifact path (with "
                         "--init-salt)")
    ap.add_argument("--complete")
    ap.add_argument("--manifest")
    ap.add_argument("--sample")
    ap.add_argument("--candidates")
    ap.add_argument("--salt")
    ap.add_argument("--salt-commitment")
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.init_salt:
        if not args.commitment_out:
            raise SystemExit("FATAL: --init-salt requires --commitment-out")
        digest = init_salt(args.init_salt, args.commitment_out)
        print(f"[v2b-mask] salt committed: {digest} "
              f"-> {args.commitment_out}")
        sys.exit(0)
    required = (args.complete, args.manifest, args.sample,
                args.candidates, args.salt, args.salt_commitment,
                args.out)
    if not all(required):
        raise SystemExit(
            "FATAL: --complete/--manifest/--sample/--candidates/--salt/"
            "--salt-commitment/--out are all required")
    masked = prepare(args.complete, args.manifest, args.sample,
                     args.candidates, args.salt, args.salt_commitment)
    digest = write_new_json(args.out, masked)
    counts = "/".join(str(n) for n in
                      masked["n_rows_by_family"].values())
    print(f"[v2b-mask] {masked['repo']}: rows {counts} -> {args.out} "
          f"({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
