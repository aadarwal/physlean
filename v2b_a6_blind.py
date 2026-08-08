#!/usr/bin/env python3
"""Blind-presentation and post-commit unblinding for V2-b A6.

The human-facing artifact deliberately contains only an opaque pair id, the
language, and the two exact source spans.  Audit origin, corpus, identities,
similarity bin/statistics, length band, and every token hash stay absent until
the complete blind label file is committed.  The hidden projection is not
stored: it is deterministically reconstructed from the sealed A6 packet.
"""
import os
import subprocess

from finalize_v2b_a6 import build_packet
from provenance import BASE
from v2b_common import (A6_AUDIT_PACKET_SCHEMA, A6_BLIND_SCHEMA,
                        A6_LABELS_SCHEMA, A6_OUTCOME_SCHEMA, V2BError,
                        artifact_binding, canonical_json_bytes, identity_key,
                        load_json, sha256_bytes, sha256_file, sha256_json,
                        validate_identity)
from v2b_neardup import (collision_activation, jaccard_outcome, lex_unit,
                         load_lean_keyword_freeze, normalized_hash,
                         verbatim_hash)


BLIND_RUBRIC = (
    "duplicate = the same implementation/specification modulo ONE systematic "
    "identifier renaming; a shared syntax skeleton alone is not enough, and "
    "differing API calls or referenced constants are not-duplicate"
)
PAIR_ID_LABEL = "a6blindid:v2b:20260808"
ORDER_LABEL = "a6blindorder:v2b:20260808"
SIDE_LABEL = "a6blindside:v2b:20260808"


def _hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(ch in "0123456789abcdef" for ch in value)


def require_committed(path):
    """Require the working bytes to equal the path's exact HEAD blob."""
    root = os.path.realpath(BASE)
    real = os.path.realpath(path)
    try:
        if os.path.commonpath((root, real)) != root:
            raise V2BError(f"committed boundary path is outside repo: {path}")
    except ValueError as err:
        raise V2BError(f"committed boundary path mismatch: {err}") from err
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    tracked = subprocess.run(
        ["git", "-C", root, "ls-files", "--error-unmatch", "--", rel],
        capture_output=True)
    if tracked.returncode != 0:
        raise V2BError(f"blind boundary input is not tracked: {rel}")
    committed = subprocess.run(
        ["git", "-C", root, "show", f"HEAD:{rel}"], capture_output=True)
    if committed.returncode != 0:
        raise V2BError(f"blind boundary input has no HEAD blob: {rel}")
    try:
        working = open(real, "rb").read()
    except OSError as err:
        raise V2BError(f"cannot read committed boundary input {rel}: {err}") \
            from err
    if committed.stdout != working:
        raise V2BError(f"blind boundary input differs from HEAD: {rel}")
    return dict(path=real, sha256=sha256_bytes(working))


def require_single_commit(path):
    """Require a blind-label path to have exactly one touching commit."""
    root = os.path.realpath(BASE)
    real = os.path.realpath(path)
    try:
        if os.path.commonpath((root, real)) != root:
            raise V2BError(f"label history path is outside repo: {path}")
    except ValueError as err:
        raise V2BError(f"label history path mismatch: {err}") from err
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    history = subprocess.run(
        ["git", "-C", root, "log", "--format=%H", "--", rel],
        capture_output=True, text=True)
    commits = history.stdout.splitlines() if history.returncode == 0 else []
    if len(commits) != 1 or not _hex(commits[0], 40):
        raise V2BError(
            f"blind labels must have exactly one touching commit: {rel}")
    return commits[0]


def _load_packet(path):
    binding, packet = artifact_binding(path, A6_AUDIT_PACKET_SCHEMA)
    generator = packet.get("generator")
    if packet.get("label_state") != "unlabeled" \
            or packet.get("sampling_state") != "not-drawn" \
            or not isinstance(generator, dict) \
            or generator.get("program") != "finalize_v2b_a6.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")):
        raise V2BError("A6 packet boundary/generator is malformed")
    source_rows = packet.get("source_tables")
    freeze_binding = packet.get("keyword_freeze")
    if not isinstance(source_rows, list) or len(source_rows) != 5 \
            or any(not isinstance(row, dict) or
                   not isinstance(row.get("path"), str)
                   for row in source_rows) \
            or not isinstance(freeze_binding, dict) \
            or not isinstance(freeze_binding.get("path"), str):
        raise V2BError("A6 packet lacks reconstructible sealed inputs")
    rebuilt = build_packet([row["path"] for row in source_rows],
                           freeze_binding["path"])
    observed = dict(packet)
    observed.pop("generator")
    if observed != rebuilt:
        raise V2BError("A6 packet is not the exact deterministic rebuild")
    tables = {}
    for row in source_rows:
        table_binding, table = artifact_binding(row["path"])
        if table_binding["sha256"] != row.get("sha256") \
                or table.get("repo") != row.get("repo"):
            raise V2BError("A6 packet source-table binding drift")
        tables[row["repo"]] = table
    lean_keywords, _ = load_lean_keyword_freeze(freeze_binding["path"])
    return binding, packet, tables, lean_keywords


def _canonical_pair(language, left, right):
    a = list(validate_identity(language, left))
    b = list(validate_identity(language, right))
    if a == b:
        raise V2BError("blind A6 pair repeats one identity")
    return tuple(sorted((tuple(a), tuple(b)), key=canonical_json_bytes))


def _selected_pairs(packet, packet_sha):
    pairs = {}

    def add(language, repo, left, right, role):
        first, second = _canonical_pair(language, left, right)
        key = (language, repo, first, second)
        if key not in pairs:
            digest = sha256_json(
                [PAIR_ID_LABEL, packet_sha, language, repo,
                 *first, *second])
            pair_id = "P-" + digest[:24]
            if any(row["pair_id"] == pair_id for row in pairs.values()):
                raise V2BError("opaque blind pair-id collision")
            pairs[key] = dict(pair_id=pair_id, language=language, repo=repo,
                              first=list(first), second=list(second), roles=[])
        if role in pairs[key]["roles"]:
            raise V2BError("duplicate hidden role for one blind pair")
        pairs[key]["roles"].append(role)

    calibration = packet.get("calibration")
    collision = packet.get("collision")
    if not isinstance(calibration, dict) or not isinstance(collision, dict):
        raise V2BError("A6 packet lacks audit sections")
    for language in ("lean", "python"):
        for bin_name, section in calibration[language].items():
            for entry in section["entries"]:
                add(language, entry["repo"], entry["a_identity"],
                    entry["b_identity"],
                    dict(kind="calibration", language=language,
                         bin=bin_name, repo=entry["repo"], a=entry["a"],
                         b=entry["b"]))
        for band, section in collision[language].items():
            for entry in section["entries"]:
                add(language, entry["repo"],
                    entry["pair"]["left"]["identity"],
                    entry["pair"]["right"]["identity"],
                    dict(kind="collision", language=language, band=band,
                         repo=entry["repo"],
                         normalized_sha256=entry["normalized_sha256"]))
    return pairs


def _span_index(table):
    extraction_binding, extraction = artifact_binding(
        table["extraction"]["path"])
    if extraction_binding != table["extraction"] \
            or extraction.get("repo") != table.get("repo"):
        raise V2BError("A6 extraction binding drift during blind rendering")
    language = table["language"]
    spans = {}
    for file_row in extraction.get("files", []):
        if not isinstance(file_row, dict):
            raise V2BError("A6 extraction file row is malformed")
        source = file_row.get("source")
        source_sha = file_row.get("source_sha256")
        if not isinstance(source, str) or not _hex(source_sha):
            raise V2BError("A6 extraction lacks source binding")
        if language == "lean":
            module = file_row.get("module")
            decls = file_row.get("decls")
            if not isinstance(module, str) or not isinstance(decls, dict):
                raise V2BError("Lean extraction source index is malformed")
            rows = (([module, name], decl.get("start_byte"),
                     decl.get("end_byte"))
                    for name, decl in decls.items()
                    if isinstance(decl, dict))
        else:
            targets = file_row.get("targets")
            if not isinstance(targets, list):
                raise V2BError("Python extraction source index is malformed")
            rows = ((target.get("identity"), target.get("start_byte"),
                     target.get("end_byte"))
                    for target in targets if isinstance(target, dict))
        for identity, start, end in rows:
            key = identity_key(language, identity)
            if key in spans:
                raise V2BError(f"duplicate A6 extraction identity {key}")
            spans[key] = dict(source=source, source_sha256=source_sha,
                              start=start, end=end)
    return spans


def _render_unit(table, units, spans, identity, lean_keywords, cache):
    language = table["language"]
    key = identity_key(language, identity)
    if key not in units or key not in spans:
        raise V2BError(f"selected A6 identity lacks unit/span: {key}")
    row = spans[key]
    source = row["source"]
    if source not in cache:
        try:
            blob = open(source, "rb").read()
        except OSError as err:
            raise V2BError(f"cannot read selected A6 source {source}: {err}") \
                from err
        if sha256_bytes(blob) != row["source_sha256"]:
            raise V2BError(f"selected A6 source hash drift: {source}")
        cache[source] = (blob, sha256_bytes(blob))
    blob, live_sha = cache[source]
    if live_sha != row["source_sha256"]:
        raise V2BError(f"selected A6 source binding disagrees: {source}")
    start, end = row["start"], row["end"]
    if not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) \
            or not 0 <= start < end <= len(blob):
        raise V2BError(f"selected A6 source span is invalid: {key}")
    try:
        text = blob[start:end].decode("utf-8")
    except UnicodeDecodeError as err:
        raise V2BError(f"selected A6 source span splits UTF-8: {key}") \
            from err
    records = lex_unit(language, text)
    unit = units[key]
    normalized = normalized_hash(
        records, language,
        lean_keywords=lean_keywords if language == "lean" else None)
    if verbatim_hash(records) != unit["verbatim_sha256"] \
            or normalized != unit["normalized_sha256"] \
            or len(records) != unit["n_records"]:
        raise V2BError(f"selected A6 source no longer matches token unit: {key}")
    return text


def build_blind_core(packet_path):
    packet_binding, packet, tables, lean_keywords = _load_packet(packet_path)
    hidden = _selected_pairs(packet, packet_binding["sha256"])
    span_indexes = {repo: _span_index(table) for repo, table in tables.items()}
    unit_indexes = {
        repo: {unit["key"]: unit for unit in table["units"]}
        for repo, table in tables.items()}
    source_cache = {}
    visible = []
    mapping = {}
    for row in hidden.values():
        table = tables[row["repo"]]
        spans = span_indexes[row["repo"]]
        units = unit_indexes[row["repo"]]
        left = _render_unit(table, units, spans, row["first"], lean_keywords,
                            source_cache)
        right = _render_unit(table, units, spans, row["second"], lean_keywords,
                             source_cache)
        side_hash = sha256_json(
            [SIDE_LABEL, packet_binding["sha256"], row["pair_id"]])
        if int(side_hash[-1], 16) % 2:
            left, right = right, left
        visible.append(dict(pair_id=row["pair_id"], language=row["language"],
                            left=left, right=right))
        mapping[row["pair_id"]] = list(row["roles"])
    visible.sort(key=lambda row: sha256_json(
        [ORDER_LABEL, packet_binding["sha256"], row["pair_id"]]))
    core = dict(schema=A6_BLIND_SCHEMA, label_state="awaiting-human",
                rubric=BLIND_RUBRIC, n_pairs=len(visible), pairs=visible)
    return core, mapping, packet_binding, packet


def _validate_presentation(path, packet_path):
    binding, presentation = artifact_binding(path, A6_BLIND_SCHEMA)
    if set(presentation) != {"schema", "label_state", "rubric", "n_pairs",
                            "pairs", "generator"}:
        raise V2BError("blind presentation root leaks/omits fields")
    generator = presentation.get("generator")
    if not isinstance(generator, dict) \
            or set(generator) != {"source_commit", "source_tree_hash",
                                  "program"} \
            or generator.get("program") != "prepare_v2b_a6_blind.py" \
            or not _hex(generator.get("source_commit"), 40) \
            or not _hex(generator.get("source_tree_hash")):
        raise V2BError("blind presentation generator is malformed")
    core, mapping, packet_binding, packet = build_blind_core(packet_path)
    observed = dict(presentation)
    observed.pop("generator")
    if observed != core:
        raise V2BError("blind presentation differs from deterministic rebuild")
    for row in presentation["pairs"]:
        if not isinstance(row, dict) \
                or set(row) != {"pair_id", "language", "left", "right"}:
            raise V2BError("blind presentation pair leaks/omits fields")
    return binding, presentation, mapping, packet_binding, packet


def _load_labels(path, presentation_binding, pair_ids):
    binding, labels = artifact_binding(path, A6_LABELS_SCHEMA)
    if set(labels) != {"schema", "label_state", "rubric", "labeler",
                      "presentation_sha256", "labels"} \
            or labels.get("label_state") != "blind-complete" \
            or labels.get("rubric") != BLIND_RUBRIC \
            or labels.get("presentation_sha256") != \
            presentation_binding["sha256"] \
            or not isinstance(labels.get("labeler"), str) \
            or not labels["labeler"].strip() \
            or not isinstance(labels.get("labels"), list):
        raise V2BError("blind A6 label artifact is malformed/drifted")
    label_of = {}
    for row in labels["labels"]:
        if not isinstance(row, dict) \
                or set(row) != {"pair_id", "label", "note"} \
                or row.get("label") not in ("duplicate", "not-duplicate") \
                or not isinstance(row.get("note"), str):
            raise V2BError("blind A6 label row is malformed")
        pair_id = row.get("pair_id")
        if pair_id in label_of:
            raise V2BError(f"duplicate blind A6 label {pair_id!r}")
        label_of[pair_id] = row["label"]
    if set(label_of) != set(pair_ids):
        raise V2BError("blind A6 labels do not cover the exact presentation")
    return binding, labels, label_of


def build_outcome(packet_path, presentation_path, labels_path):
    presentation_binding, presentation, mapping, packet_binding, packet = \
        _validate_presentation(presentation_path, packet_path)
    labels_binding, labels, label_of = _load_labels(
        labels_path, presentation_binding, mapping)
    calibration = {language: [] for language in ("lean", "python")}
    collision = {language: [] for language in ("lean", "python")}
    n_roles = 0
    for pair_id, roles in mapping.items():
        label = label_of[pair_id]
        for role in roles:
            n_roles += 1
            if role["kind"] == "calibration":
                calibration[role["language"]].append(dict(
                    repo=role["repo"], a=role["a"], b=role["b"],
                    label=label))
            elif role["kind"] == "collision":
                collision[role["language"]].append(dict(
                    band=role["band"], repo=role["repo"],
                    normalized_sha256=role["normalized_sha256"],
                    label="clone" if label == "duplicate" else "not-clone"))
            else:
                raise AssertionError("unknown hidden A6 role")
    jaccard = {language: jaccard_outcome(
        packet["calibration"][language], calibration[language])
        for language in ("lean", "python")}
    activation = {language: collision_activation(
        packet["collision"][language], collision[language])
        for language in ("lean", "python")}
    outcomes = dict(jaccard=jaccard, collision_activation=activation)
    return dict(
        schema=A6_OUTCOME_SCHEMA,
        label_state="unblinded-from-committed-labels",
        sampling_state="not-drawn",
        packet=packet_binding,
        presentation=presentation_binding,
        labels=labels_binding,
        labeler=labels["labeler"],
        n_blind_pairs=len(mapping),
        n_projected_roles=n_roles,
        outcomes=outcomes,
        outcomes_sha256=sha256_json(outcomes))
