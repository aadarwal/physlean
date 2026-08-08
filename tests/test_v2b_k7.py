#!/usr/bin/env python3
"""Tests for exact reuse and audit of the G3-style k7 file order."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2b_k7 import build_k7_order
from v2b_common import V2BError


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    p = subprocess.run(["git", "-C", repo, *args], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _write(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)


def test_collector_bytes_cycles_edges_and_sccs_are_bound():
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "corpus")
        os.mkdir(root)
        _git(root, "init", "-q")
        _git(root, "config", "commit.gpgsign", "false")
        pad = b"\n-- " + b"p" * 80
        _write(os.path.join(root, "Pkg/A.lean"), b"import Pkg.B" + pad)
        _write(os.path.join(root, "Pkg/B.lean"), b"import Pkg.A" + pad + b"\n")
        _write(os.path.join(root, "Pkg/C.lean"), b"import Pkg.A" + pad)
        _write(os.path.join(root, "Pkg/D.lean"), b"def d := 1" + pad)
        _write(os.path.join(root, "Pkg/short.lean"), b"def s := 1\n")
        _write(os.path.join(root, "Pkg/bad.lean"), b"\xff" * 80)
        _write(os.path.join(root, "Pkg/Skip/X.lean"), b"def x := 1" + pad)
        _write(os.path.join(root, "Pkg/note.txt"), b"not source")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "fixture")
        head = _git(root, "rev-parse", "HEAD")
        cfg = dict(repo="corpus", dirs=["Pkg"], exts=[".lean"],
                   lang="lean", exclude=["Skip"])
        artifact = build_k7_order(root, "fixture", head, cfg=cfg)
        assert artifact["schema"] == "v2b_k7_order_v1"
        assert artifact["n_edges"] == 3
        assert artifact["n_cycle_nodes"] == 3  # A/B cycle blocks C
        assert artifact["n_cycle_sccs"] == 1
        assert artifact["collector"]["n_admitted"] == 4
        assert artifact["collector"]["n_terminal_lf_appended"] == 3
        assert artifact["collector"]["skipped"] == {
            "read_error": {"count": 0, "stat_bytes": 0,
                           "stat_errors": 0},
            "non_utf8": {"count": 1, "raw_bytes": 80},
            "under_64_bytes": {"count": 1, "raw_bytes": 11}}
        assert artifact["collector"]["nonmatching_extension"] == {
            "count": 1, "stat_bytes": 10, "stat_errors": 0}
        assert artifact["collector"]["n_admitted_raw_bytes"] + 3 == \
            artifact["collector"]["n_admitted_emitted_bytes"]
        ordered = [row[0] for row in artifact["files"]]
        assert ordered[0] == "Pkg/D.lean"
        assert ordered[1:] == ["Pkg/A.lean", "Pkg/B.lean", "Pkg/C.lean"]
        diag = {row["relpath"]: row for row in artifact["file_diagnostics"]}
        assert diag["Pkg/A.lean"]["file_scc_id"] == "Pkg/A.lean"
        assert diag["Pkg/B.lean"]["file_scc_id"] == "Pkg/A.lean"
        assert diag["Pkg/C.lean"]["file_scc_id"] == "Pkg/C.lean"
        for row in artifact["files"]:
            assert row[1] == diag[row[0]]["emitted_bytes"]
            assert row[2] == diag[row[0]]["source_sha256"]
            if diag[row[0]]["collector_appended_terminal_lf"]:
                assert diag[row[0]]["emitted_sha256"] != row[2]
        _write(os.path.join(root, "Pkg/untracked.lean"),
               b"def untracked := true" + pad)
        try:
            build_k7_order(root, "fixture", head, cfg=cfg)
            assert False, "untracked source-looking file entered k7"
        except V2BError as err:
            assert "dirty" in str(err) or "locked HEAD" in str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("V2B K7 TESTS PASS")
