#!/usr/bin/env python3
"""Committed, replayable corpus lock (PREREG §2).

  write     corpora/*/ HEAD SHAs + remote URLs + arXiv checksum identity
            -> corpora_lock.json (commit it at the acquisition boundary)
  checkout  detach every corpus at its locked SHA (full history retained —
            contamination dating needs it); fetches the SHA if absent

streams_stats corpus_shas are evidence of what WAS measured; this lock is
the replay instruction for reproducing it elsewhere.
"""
import hashlib, json, os, subprocess, sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "corpora")
LOCK = os.path.join(BASE, "corpora_lock.json")


def run(args, cwd=None, ok_fail=False):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0 and not ok_fail:
        raise RuntimeError(f"{' '.join(args)}: {p.stderr[:200]}")
    return p.stdout.strip()


def worktree_ok(d, name, bad):
    """HEAD alone is insufficient provenance: refuse dirty or shallow
    worktrees. Git command FAILURES fail the check (review fix: ok_fail
    returned empty stdout, which read as clean)."""
    try:
        if run(["git", "-C", d, "status", "--porcelain"]):
            bad.append(f"{name}: dirty worktree")
            return False
        if run(["git", "-C", d, "rev-parse",
                "--is-shallow-repository"]) == "true":
            bad.append(f"{name}: shallow clone")
            return False
    except RuntimeError as e:
        bad.append(f"{name}: git failed ({e})")
        return False
    return True


def write():
    lock = {"repos": {}, "arxiv": None}
    bad = []
    for name in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, name)
        if not os.path.isdir(os.path.join(d, ".git")):
            continue
        if not worktree_ok(d, name, bad):
            continue
        lock["repos"][name] = dict(
            url=run(["git", "-C", d, "remote", "get-url", "origin"]),
            sha=run(["git", "-C", d, "rev-parse", "HEAD"]))
    if bad:
        print("[lock] REFUSING to write lock:", bad)
        sys.exit(1)
    sys.path.insert(0, BASE)
    from arxiv_fetch import material_present  # ONE recursive definition
    cj = os.path.join(ROOT, "arxiv", "checksums.json")
    if not material_present(os.path.join(ROOT, "arxiv")):
        # tri-state (amendment): optional corpus ABSENT -> lock is valid
        # without the arXiv identity, recorded explicitly
        lock["arxiv"] = None
        lock["arxiv_absent"] = True
        with open(LOCK, "w") as f:
            json.dump(lock, f, indent=1)
        print(f"[lock] {len(lock['repos'])} repos; optional arXiv absent "
              "(recorded) -> corpora_lock.json")
        return
    if not os.path.exists(cj):
        # PRESENT but unvalidated: refusing (present-must-validate)
        print("[lock] REFUSING: arXiv material present but "
              "checksums.json absent — run arxiv_fetch --from-manifest")
        sys.exit(1)
    lock["arxiv"] = dict(
        checksums_sha256=hashlib.sha256(
            open(cj, "rb").read()).hexdigest(),
        manifest_sha256=hashlib.sha256(
            open(os.path.join(BASE, "arxiv_manifest.json"),
                 "rb").read()).hexdigest())
    with open(LOCK, "w") as f:
        json.dump(lock, f, indent=1)
    print(f"[lock] {len(lock['repos'])} repos + arxiv identity -> "
          f"corpora_lock.json")


def have_object(d, sha):
    return subprocess.run(["git", "-C", d, "cat-file", "-e", sha],
                          capture_output=True).returncode == 0


def checkout():
    """Replay a committed lock. NOTE: the FIRST G1 acquisition runs with no
    lock (this is a no-op); `write` then emits an UNTRACKED lock that must
    be reviewed and committed before the G3 source-clean gate can pass —
    that is the forcing function for lock review."""
    if not os.path.exists(LOCK):
        print("[lock] no corpora_lock.json — nothing to replay")
        return
    lock = json.load(open(LOCK))
    bad = []
    for name, ent in lock["repos"].items():
        d = os.path.join(ROOT, name)
        if not os.path.isdir(os.path.join(d, ".git")):
            bad.append(f"{name}: missing clone")
            continue
        url = run(["git", "-C", d, "remote", "get-url", "origin"],
                  ok_fail=True)
        if url != ent["url"]:
            bad.append(f"{name}: remote {url!r} != locked {ent['url']!r}")
            continue
        if not worktree_ok(d, name, bad):
            continue
        if run(["git", "-C", d, "rev-parse", "HEAD"]) == ent["sha"]:
            continue
        if not have_object(d, ent["sha"]):
            run(["git", "-C", d, "fetch", "origin", ent["sha"]],
                ok_fail=True)
        try:
            run(["git", "-C", d, "checkout", "--detach", ent["sha"]])
            print(f"[lock] {name} -> {ent['sha'][:12]}")
        except RuntimeError as e:
            bad.append(f"{name}: {e}")
    # arXiv identity (tri-state, frozen rule): the CURRENT on-disk state
    # governs. Current ABSENT -> pass regardless of any prior locked
    # identity (reported — the optional artifact may legitimately be
    # gone on this machine). Current PRESENT -> checksums + manifest
    # must exist, and a locked identity, if any, must match exactly.
    sys.path.insert(0, BASE)
    from arxiv_fetch import material_present
    present = material_present(os.path.join(ROOT, "arxiv"))
    if not present:
        if lock.get("arxiv"):
            print("[lock] optional arXiv corpus ABSENT here; lock records "
                  f"a prior identity (non-blocking, reported): "
                  f"{lock['arxiv']}")
    else:
        cj = os.path.join(ROOT, "arxiv", "checksums.json")
        mf = os.path.join(BASE, "arxiv_manifest.json")
        for path, key in ((cj, "checksums_sha256"), (mf, "manifest_sha256")):
            if not os.path.exists(path):
                bad.append(f"arxiv: {os.path.basename(path)} missing "
                           "(run arxiv_fetch --from-manifest first)")
                continue
            if lock.get("arxiv"):
                h = hashlib.sha256(open(path, "rb").read()).hexdigest()
                if h != lock["arxiv"][key]:
                    bad.append(f"arxiv: {key} mismatch ({h[:12]} != "
                               f"{lock['arxiv'][key][:12]})")
    if bad:
        print("[lock] FAILED:", bad)
        sys.exit(1)
    print("[lock] all corpora at locked SHAs"
          + ("; arXiv identity verified" if present and lock.get("arxiv")
             else "; optional arXiv " + ("present (identity not in lock — "
             "rewrite the lock to adopt it)" if present else "absent")))


if __name__ == "__main__":
    {"write": write, "checkout": checkout}[sys.argv[1]]()
