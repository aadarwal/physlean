#!/usr/bin/env python3
"""Shared provenance helpers (PREREG §4/§12)."""
import hashlib, os, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))


def source_tree_hash():
    """Deterministic hash of the TRACKED SOURCE state (blob SHAs + paths,
    excluding generated evidence under results_v2). Evidence-only commits
    do not change it, so battery/preflight can prove 'no source diff since
    measurement' without freezing HEAD (review: harness_commit == HEAD
    would be broken by committing the evidence itself)."""
    p = subprocess.run(["git", "-C", BASE, "ls-files", "-s", "--",
                        ".", ":(exclude)results_v2"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {p.stderr[:200]}")
    return hashlib.sha256(p.stdout.encode()).hexdigest()


def head_commit():
    p = subprocess.run(["git", "-C", BASE, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return p.stdout.strip() or None


def source_clean():
    """True iff no modified/untracked SOURCE exists outside results_v2 —
    the precondition for source_tree_hash (an index hash) to describe the
    code actually executing. Measurement entry points refuse when False."""
    p = subprocess.run(["git", "-C", BASE, "status", "--porcelain", "--",
                        ".", ":(exclude)results_v2"],
                       capture_output=True, text=True)
    return p.returncode == 0 and not p.stdout.strip()
