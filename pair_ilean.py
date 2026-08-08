#!/usr/bin/env python3
"""Deterministically pair root-package Lean sources with Lake `.ilean`s.

Lake mirrors module names beneath `.lake/build/lib/lean`, but packages may
set a `srcDir`, so a raw path substitution is not always correct.  The module
name embedded in each v5 `.ilean` is authoritative: match it to an exact
repository-relative source path first, then to one unique suffix (the srcDir
case).  Ambiguity and duplicate artifact modules fail closed.

The output is a hashed, new-file-only manifest consumed by V2-a extraction;
unbuilt sources and unmatched artifacts remain explicit diagnostics.
"""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile


SCHEMA = "v2a_ilean_pairs_v2"
ILEAN_VERSION = 5


class PairError(RuntimeError):
    """A source/artifact identity cannot be established unambiguously."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _walk(root, suffix, prune=()):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in prune)
        for name in sorted(fns):
            if name.endswith(suffix):
                out.append(os.path.normpath(os.path.join(dp, name)))
    return out


def _ilean_identity(path):
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise PairError(f"cannot read {path}: {err}") from err
    if not isinstance(raw, dict) or raw.get("version") != ILEAN_VERSION:
        got = raw.get("version") if isinstance(raw, dict) else type(raw).__name__
        raise PairError(f"{path}: .ilean version/root drift ({got!r})")
    module = raw.get("module")
    if not isinstance(module, str) or not module:
        raise PairError(f"{path}: invalid module {module!r}")
    return module


def _repo_sha(repo_root):
    """Record the checked-out revision when the source root is in Git.

    Toolchain source trees used by local stress tests are intentionally not
    repositories, so absence is explicit rather than fatal. Cluster corpus
    manifests must carry the non-null value into their gate evidence.
    """
    proc = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        encoding="utf-8")
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    if sha and (len(sha) != 40 or any(c not in "0123456789abcdef"
                                     for c in sha.lower())):
        raise PairError(f"unexpected git revision for {repo_root}: {sha!r}")
    return sha or None


def discover_pairs(repo_root, artifact_root, expected_repo_sha=None):
    # Absolute paths make the manifest independent of the later consumer's
    # working directory. Hashes still bind the exact bytes at consumption.
    repo_root = os.path.abspath(os.path.normpath(repo_root))
    artifact_root = os.path.abspath(os.path.normpath(artifact_root))
    if not os.path.isdir(repo_root):
        raise PairError(f"missing repository root: {repo_root}")
    if not os.path.isdir(artifact_root):
        raise PairError(f"missing artifact root: {artifact_root}")

    repo_sha = _repo_sha(repo_root)
    if expected_repo_sha is not None:
        expected_repo_sha = expected_repo_sha.lower()
        if repo_sha != expected_repo_sha:
            raise PairError(
                f"repository revision mismatch for {repo_root}: "
                f"expected {expected_repo_sha}, got {repo_sha}")

    sources = _walk(repo_root, ".lean", prune=(".git", ".lake"))
    source_by_rel = {os.path.relpath(p, repo_root): p for p in sources}
    artifacts = _walk(artifact_root, ".ilean")
    artifact_by_module = {}
    for path in artifacts:
        module = _ilean_identity(path)
        if module in artifact_by_module:
            raise PairError(
                f"duplicate .ilean module {module}: "
                f"{artifact_by_module[module]} and {path}")
        artifact_by_module[module] = path

    pairs = []
    unmatched_artifacts = []
    used_sources = set()
    for module, ilean in sorted(artifact_by_module.items()):
        module_rel = os.path.join(*module.split(".")) + ".lean"
        if module_rel in source_by_rel:
            candidates = [source_by_rel[module_rel]]
            match_kind = "exact"
        else:
            suffix = os.sep + module_rel
            candidates = sorted(
                path for rel, path in source_by_rel.items()
                if (os.sep + rel).endswith(suffix))
            match_kind = "srcdir_suffix"
        if not candidates:
            unmatched_artifacts.append(
                {"module": module, "ilean": ilean})
            continue
        if len(candidates) != 1:
            raise PairError(
                f"ambiguous source for module {module}: {candidates}")
        source = candidates[0]
        if source in used_sources:
            raise PairError(f"source paired twice: {source}")
        used_sources.add(source)
        pairs.append(dict(
            module=module,
            match_kind=match_kind,
            source=source,
            ilean=ilean,
            source_sha256=_sha256(source),
            ilean_sha256=_sha256(ilean),
        ))

    unmatched_sources = sorted(
        os.path.relpath(path, repo_root)
        for path in sources if path not in used_sources)
    if not pairs:
        raise PairError(
            f"no source/.ilean pairs under {repo_root} and {artifact_root}")
    return dict(
        schema=SCHEMA,
        ilean_version=ILEAN_VERSION,
        repo_root=repo_root,
        artifact_root=artifact_root,
        repo_git_sha=repo_sha,
        expected_repo_git_sha=expected_repo_sha,
        n_sources=len(sources),
        n_artifacts=len(artifacts),
        n_pairs=len(pairs),
        n_unmatched_sources=len(unmatched_sources),
        n_unmatched_artifacts=len(unmatched_artifacts),
        pairs=pairs,
        unmatched_sources=unmatched_sources,
        unmatched_artifacts=unmatched_artifacts,
    )


def write_new_json(path, value):
    """Atomically create evidence; never replace an earlier manifest."""
    path = os.path.normpath(path)
    if os.path.exists(path):
        raise PairError(f"refusing to overwrite existing manifest: {path}")
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ilean-pairs-", suffix=".json",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, sort_keys=True)
            fh.write("\n")
        try:
            # Atomic publication without replacement, including a race with
            # another writer after the existence pre-check.
            os.link(tmp, path)
        except FileExistsError as err:
            raise PairError(
                f"refusing to overwrite existing manifest: {path}") \
                from err
    except BaseException:
        raise
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--artifact-root",
                    help="default: REPO/.lake/build/lib/lean")
    ap.add_argument("--expected-repo-sha",
                    help="fail unless git HEAD exactly matches this SHA")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    artifact_root = args.artifact_root or os.path.join(
        args.repo_root, ".lake", "build", "lib", "lean")
    manifest = discover_pairs(args.repo_root, artifact_root,
                              args.expected_repo_sha)
    write_new_json(args.out, manifest)
    print(f"[pair_ilean] {manifest['n_pairs']} pairs; "
          f"{manifest['n_unmatched_sources']} unbuilt sources; "
          f"{manifest['n_unmatched_artifacts']} unmatched artifacts -> "
          f"{args.out}")


if __name__ == "__main__":
    main()
