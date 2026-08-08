#!/usr/bin/env python3
"""Measurement-identity regression tests (schema v4, stdlib only):
harness-hash contract, canonical environment fingerprint, fail-closed
lock parsing, and write-once freeze matching.
Run: python3 tests/test_provenance.py"""
import hashlib, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import provenance
from provenance import (MEASUREMENT_HARNESS_FILES, env_canonical,
                        env_fingerprint, env_matches_freeze, harness_hash,
                        read_lock)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_harness_set_is_frozen_and_content_hashed():
    """The dump-content harness is EXACTLY eval_incontext.py + layout.py
    (frozen decision: orchestration/provenance changes cannot alter dump
    content); the hash is over names + bytes, order-stable."""
    assert MEASUREMENT_HARNESS_FILES == ("eval_incontext.py", "layout.py")
    h = hashlib.sha256()
    for name in MEASUREMENT_HARNESS_FILES:
        h.update(name.encode() + b"\x00")
        h.update(open(os.path.join(BASE, name), "rb").read())
        h.update(b"\x00")
    assert harness_hash() == h.hexdigest()
    assert harness_hash() == harness_hash()  # deterministic


def test_env_canonical_shape_and_determinism():
    """Canonical text: python runtime line, resolved interpreter BINARY
    hash line, torch-cuda build line, then sorted name==version lines
    for every installed distribution — tokenizers-class packages are
    covered by construction, hardware never appears."""
    text = env_canonical()
    lines = text.splitlines()
    assert lines[0].startswith("python==")
    assert lines[1].startswith("python-binary==")
    assert lines[2].startswith("torch-cuda==")
    dists = lines[3:]
    assert dists == sorted(dists) and len(dists) == len(set(dists))
    assert all("==" in l for l in dists)
    assert not any(l.lower().startswith(("gpu", "driver", "nvidia-smi"))
                   for l in lines)
    assert text == env_canonical()  # deterministic
    assert env_fingerprint() == hashlib.sha256(text.encode()).hexdigest()


def test_python_binary_hash_tracks_real_interpreter():
    """The python-binary line is the sha256 of the RESOLVED base
    interpreter binary (incident 19900858: two '3.12.13' builds — OS
    without headers, managed with — were indistinguishable by version
    string; the binary hash separates them). Verified independently
    against the file on disk."""
    import sys
    from provenance import python_binary_hash
    h = python_binary_hash()
    base = os.path.realpath(getattr(sys, "_base_executable", None)
                            or sys.executable)
    want = hashlib.sha256(open(base, "rb").read()).hexdigest()
    assert h == want and len(h) == 64
    assert f"python-binary=={h}" in env_canonical().splitlines()[1]


def test_fix_cluster_managed_python_selection_is_nonconflicting():
    """The environment preference already restricts uv to managed
    interpreters. Passing uv's redundant --managed-python selector as well
    is an error on the cluster uv build, so the resolver must rely on
    only-managed and name the exact version directly."""
    script = open(os.path.join(BASE, "fix_cluster.sh"), encoding="utf-8").read()
    assert "UV_PYTHON_PREFERENCE=only-managed" in script
    assert "python install 3.12.13 --no-bin" in script
    assert "python find 3.12.13" in script
    assert "python find --managed-python" not in script


def test_lock_contract_matches_committed_lock():
    """The committed cluster lock carries the python==3.12.13 runtime
    contract and 66 exact pins including the measurement-critical ones
    (tokenizers affects offset mappings -> measurement semantics)."""
    lock = read_lock()
    assert lock["python"] == "3.12.13"
    assert len(lock["pins"]) == 66
    for pkg in ("torch", "tokenizers", "transformers", "huggingface-hub",
                "numpy", "safetensors"):
        assert pkg in lock["pins"], f"{pkg} missing from lock"
    assert lock["pins"]["transformers"] == "5.14.1"  # PREREG pin


def test_lock_parser_fails_closed():
    """A lock that cannot be read EXACTLY must never gate anything:
    malformed lines, non-exact specifiers, empty versions, duplicate
    pins, and duplicate python contracts all raise."""
    def parse(content):
        with tempfile.NamedTemporaryFile("w", suffix=".lock",
                                         delete=False) as f:
            f.write(content)
        try:
            return read_lock(f.name)
        finally:
            os.unlink(f.name)
    ok = parse("# python==3.12.13\na==1.0\nb==2.0\n")
    assert ok["python"] == "3.12.13" and ok["pins"] == {"a": "1.0",
                                                        "b": "2.0"}
    CONTRACT = "# python==3.12.13\n"
    for bad in (CONTRACT + "a>=1.0\n",           # non-exact specifier
                CONTRACT + "a\n",                # no version at all
                CONTRACT + "a==\n",              # empty version
                CONTRACT + "a==1.0\nA==1.1\n",   # dup after normalization
                CONTRACT + "# python==3.12.14\na==1.0\n",  # dup contract
                CONTRACT + "a==1.0 b==2.0\n",    # malformed line
                CONTRACT,                        # EMPTY pin set (review
                CONTRACT + "# just comments\n",  # blocker: must raise)
                "a==1.0\nb==2.0\n",              # MISSING python contract
                ""):                             # empty lock entirely
        try:
            parse(bad)
            assert False, f"lock parser accepted {bad!r}"
        except ValueError:
            pass


def test_freeze_matching_is_exact():
    """env_matches_freeze: byte-exact equality with the live canonical
    text; missing file and any drifted line fail with the diff
    surfaced."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "freeze.txt")
        ok, detail = env_matches_freeze(p)
        assert ok is False and detail == "freeze file missing"
        open(p, "w").write(env_canonical())
        ok, detail = env_matches_freeze(p)
        assert ok is True
        open(p, "w").write(env_canonical().replace(
            "python==", "python==9.", 1))
        ok, detail = env_matches_freeze(p)
        assert ok is False and detail["only_in_freeze"]


def test_item_E_designation_frozen():
    """Item E's designated corpus, floor, and parser are FROZEN (PREREG
    §7/§13): mathlib lite-E with floor == its own sample size (8) —
    empty/thin E fails, never passes vacuously — and the import parser
    resolves `import all` while never matching indented/commented
    lines."""
    from validity_battery import (E_CORPUS, E_IMPORT_RE, E_MIN_ELIGIBLE,
                                  E_REPO_DIR, E_SAMPLE, E_SCAN_DIR)
    assert (E_CORPUS, E_REPO_DIR, E_SCAN_DIR) == \
        ("mathlib", "mathlib4", "Mathlib")
    assert E_SAMPLE == 8 and E_MIN_ELIGIBLE == E_SAMPLE
    assert E_IMPORT_RE.findall("import all Foo.Bar\nimport Baz\n") \
        == ["Foo.Bar", "Baz"]
    assert E_IMPORT_RE.findall("  import X\n-- import Y\n") == []


def test_battery_identity_drift_detection():
    """Battery completion re-check (pure helper): any mid-run change to
    source cleanliness/tree hash, harness, or environment is named;
    unchanged identities return empty (evidence may publish)."""
    from validity_battery import identity_drift
    start = dict(source_clean=True, source_tree_hash="s",
                 harness_hash="h", env_fingerprint="e")
    assert identity_drift(start, dict(start)) == []
    assert identity_drift(start, dict(start, source_clean=False)) \
        == ["source_clean"]
    assert identity_drift(start, dict(start, source_tree_hash="X")) \
        == ["source_tree_hash"]
    assert identity_drift(start, dict(start, harness_hash="X")) \
        == ["harness_hash"]
    assert identity_drift(start, dict(start, env_fingerprint="X")) \
        == ["env_fingerprint"]
    assert identity_drift(start, dict(source_clean=False,
                                      source_tree_hash="X",
                                      harness_hash="Y",
                                      env_fingerprint="Z")) \
        == ["source_clean", "source_tree_hash", "harness_hash",
            "env_fingerprint"]
    # a MISSING field on either side counts as drift, never as a match
    assert identity_drift(start, {}) == list(start)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("PROVENANCE TESTS PASS")
