#!/usr/bin/env python3
"""Shared provenance helpers (PREREG §4/§12): source-tree identity,
measurement-harness hash, and the canonical software-environment
fingerprint. ONE implementation consumed by eval, cell_done, the
battery, and preflight — two definitions of "the environment" would
drift (review decision, adopted pre-launch)."""
import hashlib, os, re, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))

# The dump-content measurement harness: EXACTLY the files whose code
# determines measured NLL bytes given identical inputs. Orchestration
# (run_phase1) and identity plumbing (this module) are excluded on
# purpose — their changes cannot alter dump content, cell parametrization
# is per-cell identity-checked, and gates always execute from current
# code. Frozen at the pre-launch boundary; extending the set invalidates
# raw cells and needs a logged amendment.
MEASUREMENT_HARNESS_FILES = ("eval_incontext.py", "layout.py")

LOCK_FILE = os.path.join(BASE, "requirements-cluster.lock")
FREEZE_FILE = os.path.join(BASE, "results_v2", "env", "freeze-cluster.txt")


def harness_hash():
    """sha256 over the measurement-harness file set (name + content)."""
    h = hashlib.sha256()
    for name in MEASUREMENT_HARNESS_FILES:
        h.update(name.encode() + b"\x00")
        h.update(open(os.path.join(BASE, name), "rb").read())
        h.update(b"\x00")
    return h.hexdigest()


def _canon_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()  # PEP 503 normalization


def env_canonical():
    """Canonical software-environment text: python runtime + torch CUDA
    BUILD + every installed distribution as sorted 'name==version'
    lines (tokenizers included by construction). GPU model and driver
    are EXCLUDED by frozen decision: mixed L40S/H200 grids are by
    design and the battery overlap item is the cross-hardware
    instrument — hardware is recorded informationally, never gated."""
    import platform
    try:
        import torch
        cuda_build = getattr(torch.version, "cuda", None)
    except ImportError:
        cuda_build = None
    from importlib import metadata
    dists = sorted({f"{_canon_name(d.metadata['Name'])}=={d.version}"
                    for d in metadata.distributions()
                    if d.metadata["Name"]})
    lines = [f"python=={platform.python_version()}",
             f"torch-cuda=={cuda_build or 'none'}"] + dists
    return "\n".join(lines) + "\n"


def env_fingerprint():
    """sha256 of the canonical environment text."""
    return hashlib.sha256(env_canonical().encode()).hexdigest()


def read_lock(path=None):
    """Parse the committed cluster lock: exact pins + the python runtime
    contract ('# python==X.Y.Z' — a comment so pip/uv ignore it).
    FAIL-CLOSED parser (review fix): a malformed line, non-exact
    specifier, empty version, duplicate pin, MISSING python contract,
    or EMPTY pin set raises — a lock that cannot be read exactly and
    completely must never gate anything."""
    pins, py = {}, None
    for ln, raw in enumerate(open(path or LOCK_FILE), 1):
        line = raw.strip()
        m = re.fullmatch(r"#\s*python==(\S+)", line)
        if m:
            if py is not None:
                raise ValueError(f"lock line {ln}: duplicate python contract")
            py = m.group(1)
            continue
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
        if not m:
            raise ValueError(f"lock line {ln}: not an exact pin: {line!r}")
        name = _canon_name(m.group(1))
        if name in pins:
            raise ValueError(f"lock line {ln}: duplicate pin {name}")
        pins[name] = m.group(2)
    if py is None:
        raise ValueError("lock has no python runtime contract "
                         "('# python==X.Y.Z' line)")
    if not pins:
        raise ValueError("lock has no pins")
    return dict(python=py, pins=pins)


def env_matches_lock(path=None):
    """(ok, problems): every locked pin installed at exactly the locked
    version, and the python runtime equals the lock contract. Extra
    installed distributions are NOT lock failures (the freeze-file
    equality is the exact-set check)."""
    import platform
    lock = read_lock(path)
    probs = []
    if lock["python"] and platform.python_version() != lock["python"]:
        probs.append(f"python {platform.python_version()} != "
                     f"lock {lock['python']}")
    from importlib import metadata
    have = {_canon_name(d.metadata["Name"]): d.version
            for d in metadata.distributions() if d.metadata["Name"]}
    for name, ver in sorted(lock["pins"].items()):
        if have.get(name) != ver:
            probs.append(f"{name}: installed {have.get(name)} != "
                         f"locked {ver}")
    return not probs, probs


def env_matches_freeze(path=None):
    """(ok, detail): the live canonical environment text equals the
    write-once software-only freeze file byte-for-byte."""
    p = path or FREEZE_FILE
    if not os.path.exists(p):
        return False, "freeze file missing"
    frozen = open(p, encoding="utf-8").read()
    live = env_canonical()
    if frozen == live:
        return True, "environment matches freeze"
    fl, ll = set(frozen.splitlines()), set(live.splitlines())
    return False, dict(only_in_freeze=sorted(fl - ll)[:6],
                       only_live=sorted(ll - fl)[:6])


def gpu_info():
    """Informational hardware record (never part of any gate): GPU name
    via torch, driver version via nvidia-smi when available."""
    name = driver = None
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=driver_version",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            driver = p.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return dict(gpu_name=name, gpu_driver=driver)


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
