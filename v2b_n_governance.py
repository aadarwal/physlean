#!/usr/bin/env python3
"""§14.22/§15.A14 blind N governance: deterministic V2-c sample size.

The V2-c per-repo N in [200, 400] is a function of masked pilot data,
never an analyst choice. Input = one masked-deltas artifact
({schema: "v2b_masked_deltas_v1"}) whose families carry OPAQUE ids and
per-target paired B* deltas; arm names never enter this module. Per
family: one-way module random-effects method-of-moments on unequal
clusters (cluster = identity[0], the source module), the all-singleton
conservative fallback, NO upper ICC clamp (extreme clustering must be
allowed to render every N infeasible), and G < 2 failing closed as
insufficient-clusters. Per integer N in [200, 400], projected module
sizes are the EXACT frozen-plan selection (build_sample_plan
exclude_keys) over the sealed candidate table with the pilot identities
excluded. halfwidth = t(0.975, G_pilot-1) * sqrt(sigma_b^2 * sum m_g^2
/ N^2 + sigma_w^2 / N) using the FROZEN t table below. Family N =
smallest N with halfwidth <= 0.02 b/B; repo N = max over families;
otherwise the repo verdict is infeasible.

BLINDNESS: the output artifact records variance components, cluster
counts, per-N halfwidths, chosen N, and verdicts only — never means,
signs, or per-target deltas.
"""
import argparse
import json
import math
import re
import sys

from finalize_v2b_sample import N_PER_CORPUS
from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (BOUND_SAMPLE_SCHEMA, CANDIDATES_SCHEMA,
                        MASKED_DELTAS_SCHEMA, N_GOVERNANCE_SCHEMA,
                        V2BError, artifact_binding, identity_key,
                        sha256_json, validate_identity, write_new_json)
from v2b_metadata import build_sample_plan

HALFWIDTH_TARGET = 0.02                   # paired-delta bits/byte
DELTA_METRIC = "bpb"                      # §15.A14: bits/byte, nothing else
DELTA_BUDGET_BYTES = 16384                # B* — the only governed budget
N_FAMILIES = 3                            # masked E1a/E1b/E2, ids opaque
FAMILY_ID_RE = re.compile(r"^fam-[0-9a-f]{16}$")
N_MIN, N_MAX = 200, 400
# Frozen two-sided 97.5% Student-t quantiles, df 1..19 (a 20-target pilot
# cannot exceed df 19); no runtime quantile computation, ever.
T_0975_BY_DF = {
    1: 12.706205, 2: 4.302653, 3: 3.182446, 4: 2.776445, 5: 2.570582,
    6: 2.446912, 7: 2.364624, 8: 2.306004, 9: 2.262157, 10: 2.228139,
    11: 2.200985, 12: 2.178813, 13: 2.160369, 14: 2.144787, 15: 2.131450,
    16: 2.119905, 17: 2.109816, 18: 2.100922, 19: 2.093024,
}


def _module_of_key(key):
    """Source-module cluster label from one canonical identity key."""
    try:
        identity = json.loads(key)
    except (TypeError, ValueError) as err:
        raise V2BError(f"malformed identity key {key!r}: {err}") from err
    if not isinstance(identity, list) or len(identity) not in (2, 3) \
            or not isinstance(identity[0], str) or not identity[0]:
        raise V2BError(f"identity key lacks a module: {key!r}")
    return identity[0]


def variance_components(deltas_by_module):
    """§15.A14 one-way module random-effects MoM on unequal clusters."""
    if not isinstance(deltas_by_module, dict) or not deltas_by_module:
        raise V2BError("variance components need a module->deltas mapping")
    sizes = {}
    for module, rows in deltas_by_module.items():
        if not isinstance(rows, list) or not rows \
                or any(not isinstance(v, float) and not isinstance(v, int)
                       or isinstance(v, bool) or not math.isfinite(v)
                       for v in rows):
            raise V2BError(f"malformed delta rows for module {module!r}")
        sizes[module] = len(rows)
    G = len(sizes)
    n = sum(sizes.values())
    base = dict(n_pilot=n, n_modules=G,
                cluster_sizes=sorted(sizes.values(), reverse=True))
    if G < 2 or n < 2:
        return dict(base, mode="insufficient-clusters")
    values = [float(v) for rows in deltas_by_module.values() for v in rows]
    grand = math.fsum(values) / n
    if n == G:                            # all-singleton conservative
        sample_var = math.fsum((v - grand) ** 2 for v in values) / (n - 1)
        return dict(base, mode="all-singleton-conservative",
                    sigma_w2=0.0, sigma_b2=sample_var)
    ssw = ssb = 0.0
    for module, rows in deltas_by_module.items():
        mean = math.fsum(float(v) for v in rows) / len(rows)
        ssw += math.fsum((float(v) - mean) ** 2 for v in rows)
        ssb += len(rows) * (mean - grand) ** 2
    msw = ssw / (n - G)
    msb = ssb / (G - 1)
    n0 = (n - math.fsum(s * s for s in sizes.values()) / n) / (G - 1)
    sigma_b2 = max(0.0, (msb - msw) / n0)
    return dict(base, mode="mom", msw=msw, msb=msb, n0=n0,
                sigma_w2=msw, sigma_b2=sigma_b2)


def projected_halfwidth(sigma_b2, sigma_w2, module_sizes, df):
    """t(0.975, df) * sqrt(sigma_b^2*sum m_g^2/N^2 + sigma_w^2/N)."""
    if df not in T_0975_BY_DF:
        raise V2BError(f"no frozen t quantile for df {df!r}")
    if not module_sizes or any(
            not isinstance(m, int) or isinstance(m, bool) or m <= 0
            for m in module_sizes):
        raise V2BError("projection needs positive module sizes")
    total = sum(module_sizes)
    variance = sigma_b2 * math.fsum(m * m for m in module_sizes) \
        / (total * total) + sigma_w2 / total
    if variance < 0 or not math.isfinite(variance):
        raise V2BError("projected variance is negative/non-finite")
    return T_0975_BY_DF[df] * math.sqrt(variance)


def family_governance(rows, module_sizes_by_n):
    """One masked family -> components, per-N halfwidths, chosen N."""
    if not isinstance(rows, list) or not rows:
        raise V2BError("masked family has no delta rows")
    deltas_by_module = {}
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 2:
            raise V2BError(f"malformed masked delta row[{index}]")
        key, delta = row
        if key in seen:
            raise V2BError(f"duplicate masked delta target {key!r}")
        seen.add(key)
        deltas_by_module.setdefault(_module_of_key(key), []).append(delta)
    components = variance_components(deltas_by_module)
    if components["mode"] == "insufficient-clusters":
        return dict(components, verdict="insufficient-clusters",
                    chosen_n=None, halfwidths_by_n=None)
    df = components["n_modules"] - 1
    halfwidths = {}
    chosen = None
    for n_candidate in range(N_MIN, N_MAX + 1):
        sizes = module_sizes_by_n[n_candidate]
        if sizes is None:
            # the pilot-excluded pool cannot FILL this N: the projection
            # at N is undefined, never "requested N over a smaller
            # realized denominator"
            halfwidths[str(n_candidate)] = None
            continue
        halfwidth = projected_halfwidth(components["sigma_b2"],
                                        components["sigma_w2"], sizes, df)
        halfwidths[str(n_candidate)] = halfwidth
        if chosen is None and halfwidth <= HALFWIDTH_TARGET:
            chosen = n_candidate
    return dict(components, df=df, halfwidths_by_n=halfwidths,
                chosen_n=chosen,
                verdict="feasible" if chosen is not None else "infeasible")


def _pilot_keys(sample, repo):
    plans = sample.get("plans")
    if sample.get("sampling_state") != "drawn" \
            or not isinstance(plans, dict) or repo not in plans:
        raise V2BError("bound sample lacks a drawn plan for this corpus")
    rows = plans[repo].get("targets")
    if not isinstance(rows, list) or not rows:
        raise V2BError("bound sample plan has no pilot targets")
    first = rows[0].get("identity") if isinstance(rows[0], dict) else None
    if not isinstance(first, list) or len(first) not in (2, 3):
        raise V2BError("bound sample pilot identity shape is malformed")
    language = "lean" if len(first) == 2 else "python"
    keys = [identity_key(language,
                         validate_identity(language, row.get("identity")))
            for row in rows]
    if len(set(keys)) != len(keys):
        raise V2BError("bound sample pilot identities are duplicated")
    return frozenset(keys)


def analyze(masked_path, candidates_path, sample_path):
    """Pure §15.A14 governance construction from three sealed inputs.

    The masked-deltas GENERATOR (B3 side) is solely responsible for
    delta computation, per-family eligibility filtering, and the sealed
    arm-to-opaque-id mapping — those need arm identities this module
    must never see. Everything checkable without unmasking is enforced
    here, fail-closed."""
    masked_binding, masked = artifact_binding(masked_path,
                                              MASKED_DELTAS_SCHEMA)
    cand_binding, candidates = artifact_binding(candidates_path,
                                                CANDIDATES_SCHEMA)
    sample_binding, sample = artifact_binding(sample_path,
                                              BOUND_SAMPLE_SCHEMA)
    repo = masked.get("repo")
    if not isinstance(repo, str) or not repo \
            or candidates.get("repo") != repo:
        raise V2BError("masked deltas/candidates repo mismatch")
    if masked.get("metric") != DELTA_METRIC \
            or masked.get("budget_bytes") != DELTA_BUDGET_BYTES:
        raise V2BError(f"masked deltas must declare metric="
                       f"{DELTA_METRIC!r} at budget_bytes="
                       f"{DELTA_BUDGET_BYTES}")
    declared = masked.get("bindings")
    if not isinstance(declared, dict) \
            or not isinstance(declared.get("sample"), dict) \
            or declared["sample"].get("sha256") != \
            sample_binding["sha256"] \
            or not isinstance(declared.get("candidates"), dict) \
            or declared["candidates"].get("sha256") != \
            cand_binding["sha256"]:
        raise V2BError("masked deltas are not bound to this exact "
                       "sample/candidates pair")
    # The pilot exclusions bind to the FROZEN deterministic draw, not to
    # any self-consistent 20-row JSON: recompute the exact plan the
    # sampler would produce (finalize_v2b_sample construction, including
    # its candidates_sha256 stamp) and require equality.
    expected_plan = build_sample_plan(candidates, N_PER_CORPUS)
    expected_plan["candidates_sha256"] = cand_binding["sha256"]
    plan_row = sample.get("plans", {}).get(repo)
    if plan_row != expected_plan:
        raise V2BError("bound sample plan is not the frozen deterministic "
                       "pilot draw from this candidate table")
    families = masked.get("families")
    if not isinstance(families, dict) or len(families) != N_FAMILIES \
            or any(not isinstance(fid, str) or not FAMILY_ID_RE.match(fid)
                   for fid in families):
        raise V2BError(f"masked deltas must carry exactly {N_FAMILIES} "
                       f"canonical opaque families (fam-<16 hex>)")
    pilot = _pilot_keys(sample, repo)
    if len(pilot) != N_PER_CORPUS:
        raise V2BError(f"pilot draw must contain exactly {N_PER_CORPUS} "
                       f"identities; found {len(pilot)}")
    language = candidates.get("language")
    identity_arity = {"lean": 2, "python": 3}.get(language)
    if identity_arity is None or any(
            len(json.loads(key)) != identity_arity for key in pilot):
        raise V2BError("candidate language does not match pilot identity "
                       "shape")
    for fid, rows in families.items():
        if not isinstance(rows, list):
            raise V2BError(f"malformed masked family {fid!r}")
        foreign = {row[0] for row in rows
                   if isinstance(row, list) and len(row) == 2} - pilot
        if foreign:
            raise V2BError(f"masked family {fid!r} carries non-pilot "
                           f"targets: {sorted(foreign)[:2]}")
    module_sizes_by_n = {}
    for n_candidate in range(N_MIN, N_MAX + 1):
        plan = build_sample_plan(candidates, n_candidate,
                                 exclude_keys=pilot)
        if plan["n_selected"] != n_candidate:
            module_sizes_by_n[n_candidate] = None       # underfilled N
            continue
        counts = {}
        for row in plan["targets"]:
            module = row["identity"][0]
            counts[module] = counts.get(module, 0) + 1
        module_sizes_by_n[n_candidate] = sorted(counts.values(),
                                                reverse=True)
    rows_out = {}
    chosen = []
    verdicts = set()
    for fid in sorted(families):
        row = family_governance(families[fid], module_sizes_by_n)
        rows_out[fid] = row
        verdicts.add(row["verdict"])
        if row["chosen_n"] is not None:
            chosen.append(row["chosen_n"])
    feasible = verdicts == {"feasible"}
    return dict(
        schema=N_GOVERNANCE_SCHEMA, repo=repo,
        halfwidth_target=HALFWIDTH_TARGET,
        n_range=[N_MIN, N_MAX],
        pilot_exclusion=dict(n_excluded=len(pilot),
                             keys_sha256=sha256_json(sorted(pilot))),
        bindings=dict(masked_deltas=masked_binding,
                      candidates=cand_binding, sample=sample_binding),
        n_families=len(rows_out), families=rows_out,
        repo_n=max(chosen) if feasible else None,
        verdict="feasible" if feasible else "infeasible")


def prepare(masked_path, candidates_path, sample_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    artifact = analyze(masked_path, candidates_path, sample_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during N governance")
    artifact["generator"] = dict(source_commit=commit_start,
                                 source_tree_hash=tree_start,
                                 program="v2b_n_governance.py")
    return artifact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--masked-deltas", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact = prepare(args.masked_deltas, args.candidates, args.sample)
    digest = write_new_json(args.out, artifact)
    print(f"[v2b-n-gov] {artifact['repo']}: verdict {artifact['verdict']}"
          f" repo_n={artifact['repo_n']} -> {args.out} ({digest[:12]})")
    sys.exit(0)


if __name__ == "__main__":
    main()
