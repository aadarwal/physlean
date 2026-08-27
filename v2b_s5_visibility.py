#!/usr/bin/env python3
"""Build and revalidate an exact-file visibility projection for S5.

This module is deliberately narrower than a sandbox launcher.  It consumes the
raw JSON emitted by ``V2BS5ExpandSetup``, an independently frozen transitive
import closure, and the broad v2 Lean setup index.  It emits only the files and
safe, content-bound symlinks that a later bubblewrap launcher may expose.

No directory in the broad setup index is copied into the projection.  Source,
setup, closure, index, and helper evidence are bound but explicitly excluded
from the child allowlist.  A live validator rebuilds the complete projection
from those bindings, so shape-valid caller data is not trusted.
"""
import argparse
import hashlib
import os
import stat
import sys

from prepare_v2b_lean_setups import (
    SETUP_INDEX_SCHEMA, validate_setup, validate_setup_index)
from v2b_common import (
    V2BError, load_json, sha256_bytes, sha256_sorted_json, write_new_json)


VISIBILITY_SCHEMA = "v2b_s5_visibility_v1"
IMPORT_CLOSURE_SCHEMA = "v2b_s5_import_closure_v1"

_HEX = frozenset("0123456789abcdef")
_SETUP_ROLES = frozenset((
    "dynamic-library", "import-artifact", "plugin", "symlink-target"))
_RUNTIME_ROLES = frozenset(("runtime", "symlink-target"))
_ALLOWLIST_ROLES = _SETUP_ROLES | _RUNTIME_ROLES
_ROW_KEYS = frozenset((
    "kind", "link_sha256", "link_target", "modules", "path", "resolved_path",
    "roles", "sha256"))
_MANIFEST_KEYS = frozenset((
    "allowlist", "allowlist_sha256", "contract_sha256", "helper",
    "import_closure", "import_modules", "import_modules_sha256", "module",
    "mount_policy", "n_allowlist", "runtime_files", "runtime_files_sha256",
    "producer", "schema", "setup", "setup_files", "setup_files_sha256",
    "setup_index", "source", "toolchain", "workspace"))
_MOUNT_POLICY = dict(
    mode="exact-file-allowlist-v1",
    source_transport="framed-stdin",
    bind_workspace_root=False,
    bind_toolchain_root=False,
    bind_search_roots=False)


def _is_hex(value, length=64):
    return isinstance(value, str) and len(value) == length \
        and all(char in _HEX for char in value)


def _utf8_sha256(value, label):
    try:
        return sha256_bytes(value.encode("utf-8"))
    except (AttributeError, UnicodeError) as err:
        raise V2BError(f"{label} is not UTF-8 text") from err


def _inside(path, root):
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _canonical_root(path, label):
    if not isinstance(path, str) or not path or "\x00" in path:
        raise V2BError(f"{label} is not a path")
    path = os.path.abspath(path)
    if os.path.normpath(path) != path or os.path.realpath(path) != path \
            or os.path.islink(path) or not os.path.isdir(path):
        raise V2BError(f"{label} is not a canonical real directory: {path}")
    return path


def _reject_ancestor_symlinks(path, root, include_final=False):
    """Reject traversal through a link between an already checked root/file."""
    if not _inside(path, root):
        raise V2BError(f"path escapes allowed root {root}: {path}")
    relative = os.path.relpath(path, root)
    parts = [] if relative == "." else relative.split(os.sep)
    if not include_final and parts:
        parts = parts[:-1]
    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except OSError as err:
            raise V2BError(f"cannot inspect path component {current}: {err}") \
                from err
        if stat.S_ISLNK(mode):
            raise V2BError(f"path traverses symlink: {current}")


def _hash_regular_file(path):
    """Hash one regular file without following a final symlink."""
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as err:
        raise V2BError(f"cannot open exact file {path}: {err}") from err
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise V2BError(f"allowlisted path is not a regular file: {path}")
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size,
                           before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after:
            raise V2BError(f"file drifted while hashing: {path}")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _host_file_binding(path, label):
    """Bind a host-only provenance file and reject every symlink component."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise V2BError(f"{label} is not a path")
    path = os.path.abspath(path)
    if os.path.normpath(path) != path or os.path.realpath(path) != path:
        raise V2BError(f"{label} is not a canonical non-symlink path: {path}")
    _reject_ancestor_symlinks(path, os.path.sep, include_final=True)
    return dict(path=path, sha256=_hash_regular_file(path))


def _lexical_allowed_path(path, roots, label):
    if not isinstance(path, str) or not path or "\x00" in path \
            or not os.path.isabs(path) or os.path.normpath(path) != path:
        raise V2BError(f"{label} is not an absolute canonical path: {path!r}")
    containing = [root for root in roots if _inside(path, root)]
    if not containing:
        raise V2BError(f"{label} path escapes the exact artifact roots: {path}")
    root = max(containing, key=len)
    _reject_ancestor_symlinks(path, root, include_final=False)
    return root


def _inspect_allowlisted_path(path, roots, label):
    """Return a content-bound file/link row and an optional exact link target."""
    root = _lexical_allowed_path(path, roots, label)
    try:
        mode = os.lstat(path).st_mode
    except OSError as err:
        raise V2BError(f"missing {label} artifact {path}: {err}") from err
    if stat.S_ISREG(mode):
        return dict(
            kind="file", link_sha256=None, link_target=None,
            path=path, resolved_path=path, sha256=_hash_regular_file(path)), None
    if not stat.S_ISLNK(mode):
        raise V2BError(f"{label} artifact is not a file/symlink: {path}")
    try:
        target = os.readlink(path)
    except OSError as err:
        raise V2BError(f"cannot read allowlisted symlink {path}: {err}") \
            from err
    if not target or "\x00" in target or ".." in target.split(os.sep):
        raise V2BError(f"unsafe symlink target for {path}: {target!r}")
    lexical_target = target if os.path.isabs(target) else \
        os.path.normpath(os.path.join(os.path.dirname(path), target))
    if not os.path.isabs(lexical_target) or not _inside(lexical_target, root):
        raise V2BError(f"symlink target escapes allowed root: {path} -> {target}")
    _reject_ancestor_symlinks(lexical_target, root, include_final=False)
    try:
        target_mode = os.lstat(lexical_target).st_mode
    except OSError as err:
        raise V2BError(f"missing symlink target {path} -> {target}: {err}") \
            from err
    if not stat.S_ISREG(target_mode):
        raise V2BError(f"symlink target is not one regular file: {path} -> {target}")
    resolved = os.path.realpath(path)
    if resolved != lexical_target:
        raise V2BError(f"symlink resolution drift: {path} -> {resolved}")
    digest = _hash_regular_file(resolved)
    row = dict(
        kind="symlink", link_sha256=_utf8_sha256(
            target, f"symlink target for {path}"),
        link_target=target, path=path, resolved_path=resolved, sha256=digest)
    target_row = dict(
        kind="file", link_sha256=None, link_target=None, path=resolved,
        resolved_path=resolved, sha256=digest)
    return row, target_row


def _valid_module(module):
    if not isinstance(module, str) or not module:
        return False
    parts = module.split(".")
    return all(part and part not in (".", "..") and "/" not in part
               and "\\" not in part and "\x00" not in part for part in parts)


def _target_artifact(path, module):
    """Conservatively recognize artifacts for the current target module."""
    module_path = module.replace(".", "/")
    normalized = path.replace(os.sep, "/")
    marker = "/" + module_path
    start = normalized.rfind(marker)
    if start < 0:
        return False
    remainder = normalized[start + len(marker):]
    return remainder == "" or remainder.startswith((
        ".olean", ".ilean", ".ir", ".bc", ".c", ".o"))


def _module_olean(path, module):
    normalized = path.replace(os.sep, "/")
    suffix = "/" + module.replace(".", "/") + ".olean"
    start = normalized.rfind(suffix)
    return start >= 0 and normalized[start + len(suffix):] in (
        "", ".private", ".server")


def _load_import_closure(path, module, source_sha256):
    binding = _host_file_binding(path, "S5 transitive import closure")
    value, digest = load_json(binding["path"], IMPORT_CLOSURE_SCHEMA)
    if digest != binding["sha256"]:
        raise V2BError("S5 transitive import closure changed while loading")
    keys = {"schema", "module", "source_sha256", "modules",
            "modules_sha256"}
    modules = value.get("modules")
    if set(value) != keys or value.get("module") != module \
            or value.get("source_sha256") != source_sha256 \
            or not isinstance(modules, list) or modules != sorted(modules) \
            or len(modules) != len(set(modules)) \
            or any(not _valid_module(item) or item == module
                   for item in modules) \
            or value.get("modules_sha256") != sha256_sorted_json(modules):
        raise V2BError("S5 transitive import closure identity/hash drift")
    return value, dict(
        path=binding["path"], sha256=digest,
        schema=IMPORT_CLOSURE_SCHEMA,
        modules_sha256=value["modules_sha256"])


def _flatten_setup(setup, module, expected_modules):
    """Strictly flatten the Lake 4.32 flat and 4.33 grouped encodings."""
    validate_setup(setup, module, "oracle-safe S5 expanded setup")
    import_arts = setup["importArts"]
    actual_modules = sorted(import_arts)
    if actual_modules != expected_modules:
        raise V2BError(
            "expanded ModuleSetup is nontransitive or closure membership "
            f"drifted: {actual_modules} != {expected_modules}")
    projected = []
    for imported_module in actual_modules:
        groups = import_arts[imported_module]
        if not groups:
            raise V2BError(f"importArts[{imported_module}] has no artifacts")
        if all(isinstance(path, str) for path in groups):
            paths = list(groups)             # Lean/Lake 4.32
        elif all(isinstance(group, list) for group in groups):
            if any(not group for group in groups):
                raise V2BError(
                    f"importArts[{imported_module}] has an empty group")
            paths = [path for group in groups for path in group]  # 4.33
        else:
            raise V2BError(
                f"importArts[{imported_module}] mixes flat/grouped shapes")
        if any(not isinstance(path, str) or not path for path in paths) \
                or len(paths) != len(set(paths)):
            raise V2BError(
                f"importArts[{imported_module}] has empty/duplicate paths")
        if not any(_module_olean(path, imported_module) for path in paths):
            raise V2BError(
                f"importArts[{imported_module}] lacks its matching .olean "
                "artifact")
        for path in paths:
            projected.append((path, "import-artifact", imported_module))
    if "imports" in setup:
        direct = [row["module"] for row in setup["imports"]]
        if len(direct) != len(set(direct)) or any(
                imported not in expected_modules for imported in direct):
            raise V2BError("direct ModuleSetup imports are absent from the "
                           "frozen transitive closure")
    for path in setup["dynlibs"]:
        projected.append((path, "dynamic-library", None))
    for plugin in setup["plugins"]:
        path = plugin if isinstance(plugin, str) else plugin["path"]
        projected.append((path, "plugin", None))
    return projected


def normalize_expanded_setup(setup, module, expected_modules):
    """Public pure projection used by the real two-pin helper integration."""
    if not _valid_module(module) or not isinstance(expected_modules, list) \
            or expected_modules != sorted(expected_modules) \
            or len(expected_modules) != len(set(expected_modules)):
        raise V2BError("invalid normalized-setup module closure")
    return _flatten_setup(setup, module, expected_modules)


def _merge_rows(rows):
    merged = {}
    for row in rows:
        path = row["path"]
        current = merged.get(path)
        if current is None:
            current = dict(row)
            current["roles"] = set(row["roles"])
            current["modules"] = set(row["modules"])
            merged[path] = current
            continue
        for key in ("kind", "link_sha256", "link_target", "resolved_path",
                    "sha256"):
            if current[key] != row[key]:
                raise V2BError(f"conflicting allowlist identity for {path}")
        current["roles"].update(row["roles"])
        current["modules"].update(row["modules"])
    result = []
    for path in sorted(merged):
        row = merged[path]
        row["roles"] = sorted(row["roles"])
        row["modules"] = sorted(row["modules"])
        result.append(row)
    return result


def _indexed_inventory(index):
    inventory = {row["path"]: row for row in index["artifacts"]}
    for label in ("lean", "lake"):
        row = index[label]
        existing = inventory.get(row["path"])
        if existing is not None and existing["sha256"] != row["sha256"]:
            raise V2BError(f"setup index {label} binding conflicts with inventory")
        inventory[row["path"]] = dict(
            path=row["path"], sha256=row["sha256"], roles=[label])
    symlinks = {row["path"]: row["target"]
                for row in index["search_symlinks"]}
    return inventory, symlinks


def _materialize_rows(projected, roots, inventory, indexed_symlinks,
                      target_module, target_source, label):
    rows = []
    seen_tuples = set()
    for path, role, imported_module in projected:
        key = (path, role, imported_module)
        if key in seen_tuples:
            raise V2BError(f"duplicate {label} path/role row: {key}")
        seen_tuples.add(key)
        if path == target_source or path.endswith(".lean"):
            raise V2BError(f"target/source text leaked into S5 visibility: {path}")
        if _target_artifact(path, target_module):
            raise V2BError(f"current target module artifact leaked: {path}")
        inspected, target = _inspect_allowlisted_path(path, roots, label)
        indexed = inventory.get(path)
        if indexed is None or indexed["sha256"] != inspected["sha256"]:
            raise V2BError(f"{label} artifact does not join the frozen broad "
                           f"setup index: {path}")
        if inspected["kind"] == "symlink" \
                and indexed_symlinks.get(path) != inspected["link_target"]:
            raise V2BError(f"unbound/drifted setup-index symlink: {path}")
        inspected.update(
            roles=[role], modules=[] if imported_module is None
            else [imported_module])
        rows.append(inspected)
        if target is not None:
            target_indexed = inventory.get(target["path"])
            if target_indexed is None \
                    or target_indexed["sha256"] != target["sha256"]:
                raise V2BError(f"symlink target is absent/drifted in frozen "
                               f"setup index: {target['path']}")
            target.update(roles=["symlink-target"], modules=[])
            rows.append(target)
    return _merge_rows(rows)


def _validate_binding(value, label, extra_keys=()):
    keys = {"path", "sha256", *extra_keys}
    if not isinstance(value, dict) or set(value) != keys \
            or not isinstance(value.get("path"), str) or not value["path"] \
            or not os.path.isabs(value["path"]) \
            or os.path.normpath(value["path"]) != value["path"] \
            or not _is_hex(value.get("sha256")):
        raise V2BError(f"{label} binding drift")


def _validate_rows(rows, allowed_roles, label):
    if not isinstance(rows, list):
        raise V2BError(f"{label} is not a list")
    paths = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _ROW_KEYS \
                or row.get("kind") not in ("file", "symlink") \
                or not isinstance(row.get("path"), str) or not row["path"] \
                or not os.path.isabs(row["path"]) \
                or os.path.normpath(row["path"]) != row["path"] \
                or not isinstance(row.get("resolved_path"), str) \
                or not os.path.isabs(row["resolved_path"]) \
                or os.path.normpath(row["resolved_path"]) != \
                row["resolved_path"] \
                or not _is_hex(row.get("sha256")) \
                or not isinstance(row.get("roles"), list) or not row["roles"] \
                or row["roles"] != sorted(row["roles"]) \
                or len(row["roles"]) != len(set(row["roles"])) \
                or any(role not in allowed_roles for role in row["roles"]) \
                or not isinstance(row.get("modules"), list) \
                or row["modules"] != sorted(row["modules"]) \
                or len(row["modules"]) != len(set(row["modules"])) \
                or any(not _valid_module(module) for module in row["modules"]):
            raise V2BError(f"{label}[{index}] schema/order drift")
        if row["kind"] == "file":
            if row["resolved_path"] != row["path"] \
                    or row["link_target"] is not None \
                    or row["link_sha256"] is not None:
                raise V2BError(f"{label}[{index}] file identity drift")
        elif not isinstance(row.get("link_target"), str) \
                or not row["link_target"] \
                or not _is_hex(row.get("link_sha256")) \
                or row["link_sha256"] != _utf8_sha256(
                    row["link_target"], f"{label}[{index}] link target") \
                or row["resolved_path"] == row["path"]:
            raise V2BError(f"{label}[{index}] symlink identity drift")
        paths.append(row["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise V2BError(f"{label} path order/membership drift")


def _load_setup_index(path, workspace_root, module, source_path,
                      source_sha256, toolchain_root):
    binding = _host_file_binding(path, "S5 broad setup index")
    index, digest = load_json(binding["path"], SETUP_INDEX_SCHEMA)
    if digest != binding["sha256"]:
        raise V2BError("S5 broad setup index changed while loading")
    rows = validate_setup_index(index, live_files=False,
                                require_generator=True)
    if index["corpus_root"] != workspace_root:
        raise V2BError("setup index corpus root disagrees with S5 workspace")
    matches = [row for row in rows if row["module"] == module]
    if len(matches) != 1 or matches[0]["source"] != source_path \
            or matches[0]["source_sha256"] != source_sha256:
        raise V2BError("setup index lacks the exact target module/source row")
    lean_path = index["lean"]["path"]
    detected_root = os.path.dirname(os.path.dirname(lean_path))
    if detected_root != toolchain_root:
        raise V2BError("setup index Lean executable does not identify the "
                       "exact bound toolchain root")
    return index, dict(
        path=binding["path"], sha256=digest, schema=SETUP_INDEX_SCHEMA,
        artifacts_sha256=index["artifacts_sha256"])


def _contract_projection(manifest):
    return {key: manifest[key] for key in sorted(_MANIFEST_KEYS - {
        "contract_sha256"})}


def _build_visibility(module, source_path, workspace_root, toolchain_root,
                      helper_path, setup_path, import_closure_path,
                      setup_index_path, runtime_paths):
    if not _valid_module(module):
        raise V2BError(f"invalid S5 target module {module!r}")
    workspace_root = _canonical_root(workspace_root, "S5 workspace root")
    toolchain_root = _canonical_root(toolchain_root, "S5 toolchain root")
    if _inside(toolchain_root, workspace_root) or \
            _inside(workspace_root, toolchain_root):
        raise V2BError("workspace and toolchain roots must be disjoint")
    source = _host_file_binding(source_path, "S5 target source")
    if not _inside(source["path"], workspace_root) \
            or not source["path"].endswith(".lean"):
        raise V2BError("S5 target source is not one .lean file in workspace")
    helper = _host_file_binding(helper_path, "S5 setup helper")
    if not os.access(helper["path"], os.X_OK):
        raise V2BError("S5 setup helper is not executable")
    producer = _host_file_binding(
        os.path.abspath(__file__), "S5 visibility producer")
    setup_binding = _host_file_binding(setup_path, "expanded ModuleSetup")
    setup, setup_digest = load_json(setup_binding["path"])
    if setup_digest != setup_binding["sha256"]:
        raise V2BError("expanded ModuleSetup changed while loading")
    closure, closure_binding = _load_import_closure(
        import_closure_path, module, source["sha256"])
    expected_modules = closure["modules"]
    projected = _flatten_setup(setup, module, expected_modules)

    index, index_binding = _load_setup_index(
        setup_index_path, workspace_root, module, source["path"],
        source["sha256"], toolchain_root)
    pin_path = os.path.join(workspace_root, "lean-toolchain")
    pin = _host_file_binding(pin_path, "workspace lean-toolchain")
    if pin["sha256"] != index["lean_toolchain_sha256"]:
        raise V2BError("lean-toolchain bytes disagree with broad setup index")
    try:
        with open(pin_path, "rb") as handle:
            pin_bytes = handle.read()
        if sha256_bytes(pin_bytes) != pin["sha256"]:
            raise V2BError("lean-toolchain changed while decoding")
        toolchain_name = pin_bytes.decode("utf-8").strip()
    except (OSError, UnicodeError) as err:
        raise V2BError(f"cannot decode lean-toolchain: {err}") from err
    if not toolchain_name or "\n" in toolchain_name or "\r" in toolchain_name \
            or toolchain_name != index["toolchain"]:
        raise V2BError("lean-toolchain identity disagrees with setup index")

    workspace_lake = _canonical_root(
        os.path.join(workspace_root, ".lake"), "S5 workspace .lake root")
    roots = (workspace_lake, toolchain_root)
    inventory, indexed_symlinks = _indexed_inventory(index)
    setup_files = _materialize_rows(
        projected, roots, inventory, indexed_symlinks, module, source["path"],
        "expanded setup")

    if not isinstance(runtime_paths, (list, tuple)) or not runtime_paths:
        raise V2BError("S5 runtime allowlist is empty")
    canonical_runtime = []
    for path in runtime_paths:
        if not isinstance(path, str) or not path or not os.path.isabs(path) \
                or os.path.normpath(path) != path:
            raise V2BError("S5 runtime path is malformed")
        canonical_runtime.append(path)
    if len(canonical_runtime) != len(set(canonical_runtime)):
        raise V2BError("S5 runtime allowlist contains duplicate paths")
    if index["lean"]["path"] not in canonical_runtime:
        raise V2BError("S5 runtime allowlist omits the pinned Lean executable")
    if not os.access(index["lean"]["path"], os.X_OK):
        raise V2BError("pinned Lean runtime is not executable")
    runtime_projected = [(path, "runtime", None)
                         for path in sorted(canonical_runtime)]
    runtime_files = _materialize_rows(
        runtime_projected, (toolchain_root,), inventory, indexed_symlinks,
        module, source["path"], "runtime")
    allowlist = _merge_rows(setup_files + runtime_files)
    forbidden = {
        source["path"], helper["path"], setup_binding["path"],
        closure_binding["path"], index_binding["path"], pin["path"]}
    forbidden.add(producer["path"])
    if any(row["path"] in forbidden for row in allowlist):
        raise V2BError("host-only evidence leaked into child allowlist")
    if any(row["path"] in (workspace_root, toolchain_root, workspace_lake)
           or os.path.isdir(row["path"]) and not os.path.islink(row["path"])
           for row in allowlist):
        raise V2BError("broad directory leaked into exact-file allowlist")

    setup_record = dict(
        path=setup_binding["path"], sha256=setup_binding["sha256"],
        semantics_sha256=sha256_sorted_json(setup))
    runtime_digest = sha256_sorted_json(runtime_files)
    toolchain = dict(
        name=toolchain_name, root=toolchain_root, pin=pin,
        lean=index["lean"], runtime_sha256=runtime_digest)
    toolchain["contract_sha256"] = sha256_sorted_json(toolchain)
    manifest = dict(
        schema=VISIBILITY_SCHEMA, module=module, source=source,
        workspace=dict(root=workspace_root,
                       corpus_git_sha=index["corpus_git_sha"]),
        setup=setup_record, import_closure=closure_binding,
        setup_index=index_binding, helper=helper, producer=producer,
        toolchain=toolchain,
        import_modules=expected_modules,
        import_modules_sha256=sha256_sorted_json(expected_modules),
        setup_files=setup_files,
        setup_files_sha256=sha256_sorted_json(setup_files),
        runtime_files=runtime_files,
        runtime_files_sha256=runtime_digest,
        n_allowlist=len(allowlist), allowlist=allowlist,
        allowlist_sha256=sha256_sorted_json(allowlist),
        mount_policy=dict(_MOUNT_POLICY))
    manifest["contract_sha256"] = sha256_sorted_json(
        _contract_projection(manifest))
    return manifest


def produce_visibility(module, source_path, workspace_root, toolchain_root,
                       helper_path, setup_path, import_closure_path,
                       setup_index_path, runtime_paths):
    """Produce one deterministic S5 exact-file visibility manifest."""
    manifest = _build_visibility(
        module, source_path, workspace_root, toolchain_root, helper_path,
        setup_path, import_closure_path, setup_index_path, runtime_paths)
    validate_visibility(manifest, live_files=False)
    return manifest


def validate_visibility(value, live_files=True):
    """Fail closed on schema drift, tampering, or live provenance drift."""
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS \
            or value.get("schema") != VISIBILITY_SCHEMA \
            or not _valid_module(value.get("module")):
        raise V2BError("S5 visibility schema/key/module drift")
    _validate_binding(value.get("source"), "S5 visibility source")
    _validate_binding(value.get("helper"), "S5 visibility helper")
    _validate_binding(value.get("producer"), "S5 visibility producer")
    _validate_binding(value.get("setup"), "S5 visibility setup",
                      ("semantics_sha256",))
    if not _is_hex(value["setup"].get("semantics_sha256")):
        raise V2BError("S5 visibility setup semantics hash drift")
    _validate_binding(value.get("import_closure"), "S5 import closure",
                      ("schema", "modules_sha256"))
    if value["import_closure"].get("schema") != IMPORT_CLOSURE_SCHEMA \
            or not _is_hex(value["import_closure"].get("modules_sha256")):
        raise V2BError("S5 import closure binding drift")
    _validate_binding(value.get("setup_index"), "S5 setup index",
                      ("schema", "artifacts_sha256"))
    if value["setup_index"].get("schema") != SETUP_INDEX_SCHEMA \
            or not _is_hex(value["setup_index"].get("artifacts_sha256")):
        raise V2BError("S5 setup index binding drift")
    workspace = value.get("workspace")
    if not isinstance(workspace, dict) \
            or set(workspace) != {"root", "corpus_git_sha"} \
            or not isinstance(workspace.get("root"), str) \
            or not _is_hex(workspace.get("corpus_git_sha"), 40):
        raise V2BError("S5 visibility workspace binding drift")
    modules = value.get("import_modules")
    if not isinstance(modules, list) or modules != sorted(modules) \
            or len(modules) != len(set(modules)) \
            or any(not _valid_module(module) for module in modules) \
            or value.get("import_modules_sha256") != \
            sha256_sorted_json(modules):
        raise V2BError("S5 visibility import-module closure drift")
    toolchain = value.get("toolchain")
    if not isinstance(toolchain, dict) or set(toolchain) != {
            "contract_sha256", "lean", "name", "pin", "root",
            "runtime_sha256"} \
            or not isinstance(toolchain.get("name"), str) \
            or not toolchain["name"] \
            or not isinstance(toolchain.get("root"), str) \
            or not _is_hex(toolchain.get("runtime_sha256")) \
            or not _is_hex(toolchain.get("contract_sha256")):
        raise V2BError("S5 visibility toolchain binding drift")
    _validate_binding(toolchain.get("pin"), "S5 toolchain pin")
    lean = toolchain.get("lean")
    if not isinstance(lean, dict) or set(lean) != {"path", "sha256", "version"} \
            or not isinstance(lean.get("path"), str) or not lean["path"] \
            or not _is_hex(lean.get("sha256")) \
            or not isinstance(lean.get("version"), str) or not lean["version"]:
        raise V2BError("S5 Lean runtime binding drift")
    expected_toolchain_hash = toolchain["contract_sha256"]
    projected_toolchain = dict(toolchain)
    del projected_toolchain["contract_sha256"]
    if expected_toolchain_hash != sha256_sorted_json(projected_toolchain):
        raise V2BError("S5 toolchain contract hash drift")
    for label, roles in (("setup_files", _SETUP_ROLES),
                         ("runtime_files", _RUNTIME_ROLES),
                         ("allowlist", _ALLOWLIST_ROLES)):
        _validate_rows(value.get(label), roles, label)
        digest_key = label + "_sha256"
        if value.get(digest_key) != sha256_sorted_json(value[label]):
            raise V2BError(f"S5 {label} digest drift")
    if any("runtime" not in row["roles"]
           for row in value["runtime_files"]
           if "symlink-target" not in row["roles"]):
        raise V2BError("S5 runtime projection role drift")
    if value.get("n_allowlist") != len(value["allowlist"]) \
            or type(value.get("n_allowlist")) is not int \
            or value.get("mount_policy") != _MOUNT_POLICY:
        raise V2BError("S5 allowlist count/mount-policy drift")
    merged = _merge_rows(value["setup_files"] + value["runtime_files"])
    if merged != value["allowlist"]:
        raise V2BError("S5 child allowlist is not the exact file projection")
    if value.get("contract_sha256") != sha256_sorted_json(
            _contract_projection(value)):
        raise V2BError("S5 visibility contract hash drift")
    if live_files:
        rebuilt = _build_visibility(
            value["module"], value["source"]["path"], workspace["root"],
            toolchain["root"], value["helper"]["path"],
            value["setup"]["path"], value["import_closure"]["path"],
            value["setup_index"]["path"],
            [row["path"] for row in value["runtime_files"]
             if "runtime" in row["roles"]])
        if rebuilt != value:
            raise V2BError("S5 visibility manifest/live projection drift")
    return value


def _main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    produce = sub.add_parser("produce")
    produce.add_argument("--module", required=True)
    produce.add_argument("--source", required=True)
    produce.add_argument("--workspace-root", required=True)
    produce.add_argument("--toolchain-root", required=True)
    produce.add_argument("--helper", required=True)
    produce.add_argument("--setup", required=True)
    produce.add_argument("--import-closure", required=True)
    produce.add_argument("--setup-index", required=True)
    produce.add_argument("--runtime-file", action="append", required=True)
    produce.add_argument("--out", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "produce":
            value = produce_visibility(
                args.module, args.source, args.workspace_root,
                args.toolchain_root, args.helper, args.setup,
                args.import_closure, args.setup_index, args.runtime_file)
            digest = write_new_json(args.out, value)
            print(f"[v2b-s5-visibility] {args.module}: "
                  f"{value['n_allowlist']} exact paths -> {args.out} "
                  f"({digest[:12]})")
        else:
            value, _ = load_json(args.manifest, VISIBILITY_SCHEMA)
            validate_visibility(value, live_files=True)
            print(f"[v2b-s5-visibility] valid: {args.manifest}")
    except V2BError as err:
        raise SystemExit(f"FATAL: {err}") from err
    return 0


if __name__ == "__main__":
    sys.exit(_main())
