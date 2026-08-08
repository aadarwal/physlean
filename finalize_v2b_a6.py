#!/usr/bin/env python3
"""Seal deterministic, still-unlabeled V2-b A6 audit packets.

This is deliberately a pre-label boundary.  It validates the exact five
source-locked near-duplicate tables and the parser-token freeze, then applies
only the already-frozen seeded selection rules.  It cannot accept labels,
draw targets, assemble prompts, or inspect model outcomes.
"""
import argparse

from provenance import head_commit, source_clean, source_tree_hash
from v2b_common import (A6_AUDIT_PACKET_SCHEMA, LEAN_KEYWORD_FREEZE_SCHEMA,
                        NEARDUP_SCHEMA, V2BError, artifact_binding,
                        identity_key, sha256_json, validate_identity,
                        write_new_json)
from v2b_neardup import (build_calibration_pack, build_collision_pack,
                         JACCARD_T, LEXER_CITATION, LEXICAL_FLOOR, meets,
                         load_lean_keyword_freeze,
                         python_keyword_evidence,
                         _validate_calibration_pack,
                         _validate_collision_pack)


EXPECTED = {
    "mathlib4": ("lean", "87adeaebd370a3b6a41ac4f044fddd4bf81803ad"),
    "batteries": ("lean", "76e1c118b0700b4ceafe99532e887d6431625e1a"),
    "physlib": ("lean", "e882411d1b6bcbdfdd336d4c509c6cc72e96842d"),
    "sympy": ("python", "c0a595d78fb2a2c4b0dfa7f2ee720fde84918c6c"),
    "astropy": ("python", "440fe546589c4e496235d712bc29783ecf5a5fec"),
}


def _hex(value, length):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def _validate_units(table, repo, language):
    units = table.get("units")
    if not isinstance(units, list) or not units:
        raise V2BError(f"A6 table has no units for {repo}")
    expected_keys = {"identity", "key", "verbatim_sha256",
                     "normalized_sha256", "n_records",
                     "n_lexical_records", "under_floor"}
    by_key = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or set(unit) != expected_keys:
            raise V2BError(f"malformed A6 unit[{index}] for {repo}")
        identity = list(validate_identity(language, unit.get("identity")))
        key = identity_key(language, identity)
        n_records = unit.get("n_records")
        n_lexical = unit.get("n_lexical_records")
        if unit.get("key") != key or key in by_key \
                or not _hex(unit.get("verbatim_sha256"), 64) \
                or not _hex(unit.get("normalized_sha256"), 64) \
                or not isinstance(n_records, int) \
                or isinstance(n_records, bool) or n_records <= 0 \
                or not isinstance(n_lexical, int) \
                or isinstance(n_lexical, bool) \
                or not 0 <= n_lexical <= n_records \
                or not isinstance(unit.get("under_floor"), bool) \
                or unit["under_floor"] is not \
                (n_lexical < LEXICAL_FLOOR):
            raise V2BError(f"binding-drifted A6 unit {key!r} for {repo}")
        by_key[key] = unit
    keys = [unit["key"] for unit in units]
    if keys != sorted(keys) or table.get("n_units") != len(units) \
            or table.get("n_under_floor") != sum(
                unit["under_floor"] for unit in units):
        raise V2BError(f"A6 unit ordering/count drift for {repo}")
    return by_key


def _validate_pairs(table, repo, language, by_key):
    pairs = table.get("jaccard_pairs")
    if not isinstance(pairs, list):
        raise V2BError(f"A6 Jaccard pairs are malformed for {repo}")
    expected_keys = {"a", "b", "a_identity", "b_identity",
                     "intersection", "union"}
    seen = set()
    order = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or set(pair) != expected_keys:
            raise V2BError(f"malformed A6 pair[{index}] for {repo}")
        a, b = pair.get("a"), pair.get("b")
        key = (a, b)
        if not isinstance(a, str) or not isinstance(b, str) or a >= b \
                or key in seen or a not in by_key or b not in by_key \
                or pair.get("a_identity") != by_key[a]["identity"] \
                or pair.get("b_identity") != by_key[b]["identity"] \
                or by_key[a]["n_lexical_records"] < LEXICAL_FLOOR \
                or by_key[b]["n_lexical_records"] < LEXICAL_FLOOR:
            raise V2BError(f"A6 pair/unit binding drift for {repo}: {key!r}")
        inter, union = pair.get("intersection"), pair.get("union")
        max_a = max(0, by_key[a]["n_lexical_records"] - 4)
        max_b = max(0, by_key[b]["n_lexical_records"] - 4)
        if not isinstance(inter, int) or isinstance(inter, bool) \
                or not isinstance(union, int) or isinstance(union, bool) \
                or not 0 <= inter <= union or union <= 0 \
                or inter > min(max_a, max_b) \
                or union > max_a + max_b \
                or not meets(inter, union, JACCARD_T):
            raise V2BError(f"invalid A6 pair statistics for {repo}: {key!r}")
        seen.add(key)
        order.append(key)
    if order != sorted(order):
        raise V2BError(f"A6 pair ordering drift for {repo}")


def _validate_groups(table, repo, language, by_key):
    groups = table.get("collision_groups")
    if not isinstance(groups, list):
        raise V2BError(f"A6 collision groups are malformed for {repo}")
    units_by_hash = {}
    for unit in by_key.values():
        units_by_hash.setdefault(unit["normalized_sha256"], []).append(unit)
    expected_hashes = {
        digest for digest, units in units_by_hash.items()
        if len({unit["verbatim_sha256"] for unit in units}) >= 2}
    expected_group_keys = {"normalized_sha256", "repo", "band",
                           "n_records", "n_members",
                           "n_distinct_verbatim", "members"}
    seen = set()
    order = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or set(group) != expected_group_keys:
            raise V2BError(f"malformed A6 collision group[{index}] for {repo}")
        digest = group.get("normalized_sha256")
        members = group.get("members")
        if not _hex(digest, 64) or digest in seen \
                or digest not in expected_hashes \
                or group.get("repo") != repo \
                or not isinstance(members, list) or len(members) < 2:
            raise V2BError(f"A6 collision-group binding drift for {repo}")
        bucket = sorted(units_by_hash[digest], key=lambda unit: unit["key"])
        counts = {unit["n_records"] for unit in bucket}
        if len(counts) != 1:
            raise V2BError(f"A6 collision group count drift for {repo}")
        n_records = counts.pop()
        band = "under20" if n_records < LEXICAL_FLOOR else "geq20"
        member_keys = []
        for member in members:
            if not isinstance(member, dict) \
                    or set(member) != {"identity", "verbatim_sha256"}:
                raise V2BError(f"malformed A6 collision member for {repo}")
            member_key = identity_key(
                language, validate_identity(language, member["identity"]))
            if member_key not in by_key \
                    or by_key[member_key]["normalized_sha256"] != digest \
                    or member.get("verbatim_sha256") != \
                    by_key[member_key]["verbatim_sha256"]:
                raise V2BError(f"A6 collision member/unit drift for {repo}")
            member_keys.append(member_key)
        distinct = {unit["verbatim_sha256"] for unit in bucket}
        if member_keys != [unit["key"] for unit in bucket] \
                or group.get("n_records") != n_records \
                or group.get("band") != band \
                or group.get("n_members") != len(bucket) \
                or group.get("n_distinct_verbatim") != len(distinct):
            raise V2BError(f"A6 collision group is nonmaximal/drifted for {repo}")
        seen.add(digest)
        order.append(digest)
    if seen != expected_hashes or order != sorted(order):
        raise V2BError(f"A6 collision-group coverage/order drift for {repo}")


def _validate_table(path, freeze_binding):
    binding, table = artifact_binding(path, NEARDUP_SCHEMA)
    repo = table.get("repo")
    if repo not in EXPECTED:
        raise V2BError(f"unexpected A6 corpus {repo!r}")
    language, corpus_sha = EXPECTED[repo]
    generator = table.get("generator")
    extraction = table.get("extraction")
    if table.get("language") != language \
            or table.get("corpus_git_sha") != corpus_sha \
            or table.get("lexer_citation") != LEXER_CITATION \
            or table.get("lexical_floor") != LEXICAL_FLOOR \
            or table.get("jaccard_threshold") != "7/10" \
            or not isinstance(extraction, dict) \
            or not _hex(extraction.get("sha256"), 64) \
            or not isinstance(generator, dict) \
            or generator.get("program") != "prepare_v2b_neardup.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash"), 64) \
            or not _hex(generator.get("environment_fingerprint"), 64):
        raise V2BError(f"malformed/binding-drifted A6 table for {repo}")
    by_key = _validate_units(table, repo, language)
    _validate_pairs(table, repo, language, by_key)
    _validate_groups(table, repo, language, by_key)
    keyword_evidence = table.get("keyword_evidence")
    if language == "lean":
        if keyword_evidence != freeze_binding:
            raise V2BError(f"Lean keyword freeze binding drift for {repo}")
    elif keyword_evidence != python_keyword_evidence():
        raise V2BError(f"Python keyword evidence drift for {repo}")
    provenance = {key: generator[key] for key in
                  ("source_commit", "source_tree_hash",
                   "environment_fingerprint")}
    return repo, language, binding, table, provenance


def build_packet(neardup_paths, keyword_freeze_path):
    if not isinstance(neardup_paths, (list, tuple)) \
            or len(neardup_paths) != 5:
        raise V2BError("A6 packet requires exactly five near-duplicate tables")
    _, freeze_binding = load_lean_keyword_freeze(keyword_freeze_path)
    _, freeze = artifact_binding(keyword_freeze_path,
                                 LEAN_KEYWORD_FREEZE_SCHEMA)
    freeze_generator = freeze.get("generator")
    if not isinstance(freeze_generator, dict) \
            or freeze_generator.get("program") != \
            "finalize_v2b_lean_keywords.py" \
            or not _hex(freeze_generator.get("source_commit"), 40) \
            or not _hex(freeze_generator.get("source_tree_hash"), 64):
        raise V2BError("Lean keyword freeze generator binding is malformed")
    rows = {}
    tables = {}
    provenances = {}
    for path in neardup_paths:
        repo, language, binding, table, provenance = _validate_table(
            path, freeze_binding)
        if repo in rows:
            raise V2BError(f"duplicate A6 table for {repo}")
        rows[repo] = dict(binding, repo=repo, language=language,
                          corpus_git_sha=EXPECTED[repo][1],
                          extraction_sha256=table["extraction"]["sha256"],
                          n_units=table["n_units"],
                          n_jaccard_pairs=len(table["jaccard_pairs"]),
                          n_collision_groups=len(table["collision_groups"]))
        tables[repo] = table
        provenances[repo] = provenance
    if set(rows) != set(EXPECTED):
        raise V2BError("A6 tables do not cover the exact corpus set")
    commits = {provenance["source_commit"]
               for provenance in provenances.values()}
    trees = {provenance["source_tree_hash"]
             for provenance in provenances.values()}
    environments = {provenance["environment_fingerprint"]
                    for provenance in provenances.values()}
    if len(commits) != 1 or len(trees) != 1 or len(environments) != 1:
        raise V2BError("A6 tables came from mixed source/environment cohorts")
    source_commit, = commits
    source_tree_hash, = trees
    environment_fingerprint, = environments
    if freeze_generator["source_commit"] != source_commit \
            or freeze_generator["source_tree_hash"] != source_tree_hash:
        raise V2BError("A6 tables and Lean keyword freeze use different source")

    calibration, collision = {}, {}
    for language in ("lean", "python"):
        repos = sorted(repo for repo, (lang, _) in EXPECTED.items()
                       if lang == language)
        calibration[language] = build_calibration_pack(
            {repo: tables[repo]["jaccard_pairs"] for repo in repos},
            language)
        collision[language] = build_collision_pack(
            {repo: tables[repo]["collision_groups"] for repo in repos},
            language)
        packet_language, _ = _validate_calibration_pack(
            calibration[language])
        collision_language, _ = _validate_collision_pack(
            collision[language])
        if packet_language != language or collision_language != language:
            raise AssertionError("A6 packet language validation drift")
    return dict(
        schema=A6_AUDIT_PACKET_SCHEMA,
        label_state="unlabeled",
        sampling_state="not-drawn",
        keyword_freeze=freeze_binding,
        input_generator=dict(source_commit=source_commit,
                             source_tree_hash=source_tree_hash,
                             environment_fingerprint=environment_fingerprint),
        source_tables=[rows[repo] for repo in sorted(rows)],
        calibration=calibration,
        collision=collision,
        packet_sha256=sha256_json([calibration, collision]))


def prepare(neardup_paths, keyword_freeze_path):
    if not source_clean():
        raise V2BError("measurement source tree is dirty outside results_v2")
    commit_start, tree_start = head_commit(), source_tree_hash()
    packet = build_packet(neardup_paths, keyword_freeze_path)
    if not source_clean() or head_commit() != commit_start \
            or source_tree_hash() != tree_start:
        raise V2BError("measurement source drifted during A6 packet seal")
    packet["generator"] = dict(source_commit=commit_start,
                               source_tree_hash=tree_start,
                               program="finalize_v2b_a6.py")
    return packet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neardup", action="append", required=True)
    ap.add_argument("--keyword-freeze", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    packet = prepare(args.neardup, args.keyword_freeze)
    digest = write_new_json(args.out, packet)
    n_cal = sum(section["n_selected"]
                for language in packet["calibration"].values()
                for section in language.values())
    n_col = sum(section["n_selected"]
                for language in packet["collision"].values()
                for section in language.values())
    print(f"[v2b-a6-packet] {n_cal} calibration + {n_col} collision "
          f"pairs -> {args.out} ({digest[:12]})")


if __name__ == "__main__":
    main()
