#!/usr/bin/env bash
# Repair a half-broken cluster bootstrap (empty husk clones from a killed
# first run; venv missing huggingface_hub; streams built from 0-byte
# corpora). Idempotent; safe to re-run. Ends with FIX-ALL-DONE — submission
# stays MANUAL: verify data/streams_stats.json + model cache before
# bash submit_all.sh.
set -euo pipefail  # expected failures are explicitly guarded; anything unguarded aborts
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"
# HOME is at ~98% of its FILE quota: every cache goes to POOL
POOL_BASE="/orcd/pool/008/$USER"
export HF_HOME="${HF_HOME:-$POOL_BASE/hf}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$POOL_BASE/uv-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$POOL_BASE/xdg-cache}"
export TORCH_HOME="${TORCH_HOME:-$POOL_BASE/torch-home}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$POOL_BASE/mpl}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$POOL_BASE/triton-cache}"
mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" "$TRITON_CACHE_DIR"
export CUDA_CACHE_PATH="$POOL_BASE/cuda-cache"
# Lean v2 tooling (G3.5) must never touch HOME: predeclared here so any
# elan/Lake install lands on POOL (XDG_CACHE_HOME already covers ~/.cache)
export ELAN_HOME="$POOL_BASE/elan"
mkdir -p "$CUDA_CACHE_PATH" "$ELAN_HOME"
# uv-MANAGED interpreter only (incident 19900858: a venv on the OS
# /usr/bin/python3.12 had no Python.h on ORCD — Triton JIT failed
# closed): only-managed makes a system interpreter structurally
# impossible to select, and the install dir lives on POOL.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$POOL_BASE/uv-python}"
export UV_PYTHON_PREFERENCE=only-managed
# hf-xet (in the lock) is the current transfer backend; the deprecated
# hf_transfer flag is gone (review fix)
export HF_XET_HIGH_PERFORMANCE=1
export GIT_TERMINAL_PROMPT=0

echo "=== [fix 1/6] drop broken clones ==="
for d in corpora/*/; do
  n=$(basename "$d")
  [ "$n" = arxiv ] && continue
  git -C "$d" log -1 --oneline >/dev/null 2>&1 \
    || { echo "[broken] $n — removing"; rm -rf "$d"; }
done

echo "=== [fix 2/6] env (lock-synced; install must succeed) ==="
# EVERY wheel comes from the committed lock — no unpinned installs
# (review fix: unpinned torch made reruns silently change the
# measurement environment). The '# python==' contract line is a
# comment, so uv/pip consume the same file provenance verifies.
# fail-closed venv identity: an EXISTING venv must sit on the managed
# interpreter WITH headers; a wrong-base venv (the incident state)
# refuses and requires the explicit migration flag REBUILD_VENV=1
# (quarantines the old venv) — never a silent rebuild. Idempotent:
# a healthy managed venv passes untouched on every rerun.
# idempotent managed-interpreter install; --no-bin is REQUIRED — without
# it uv symlinks ~/.local/bin/python3.12 into HOME, which sits at ~98%
# FILE quota (observed on the probe install). The exact executable is
# then resolved and passed to uv venv — never a bare version string.
"$HOME/.local/bin/uv" python install 3.12.13 --no-bin \
  --install-dir "$UV_PYTHON_INSTALL_DIR" \
  || { echo "MANAGED-PYTHON-INSTALL-FAILED"; exit 1; }
PYBIN="$("$HOME/.local/bin/uv" python find --managed-python 3.12.13)" \
  || { echo "MANAGED-PYTHON-FIND-FAILED"; exit 1; }
echo "[venv] managed interpreter: $PYBIN"
VENV_ID='import os, sys, sysconfig
base = os.path.realpath(getattr(sys, "_base_executable", None)
                        or sys.executable)
inc = sysconfig.get_config_var("INCLUDEPY") or ""
hdr = os.path.exists(os.path.join(inc, "Python.h"))
mdir = os.environ.get("UV_PYTHON_INSTALL_DIR")
try:  # realpath+commonpath containment (audit fix: string startswith
    # is spoofable by sibling paths like .../uv-python-evil)
    mng = bool(mdir) and os.path.commonpath(
        [os.path.realpath(mdir), base]) == os.path.realpath(mdir)
except ValueError:
    mng = False
print(f"[venv-id] base={base}")
print(f"[venv-id] headers={hdr} ({inc})")
print(f"[venv-id] managed={mng}")
sys.exit(0 if (hdr and mng) else 1)'
if [ -d .venv ]; then
  if ! .venv/bin/python -c "$VENV_ID"; then
    if [ "${REBUILD_VENV:-0}" = "1" ]; then
      TS="$(date +%Y%m%d-%H%M%S)-$$"   # pid-suffixed: same-second safe
      mv .venv ".venv.quarantine-$TS"
      echo "[REBUILD_VENV] old venv -> .venv.quarantine-$TS"
    else
      echo "[VENV-BASE-INVALID] existing venv is not on the managed"
      echo "interpreter with headers; rerun with REBUILD_VENV=1 (the"
      echo "fingerprint change also needs REFREEZE=1) to migrate"
      exit 1
    fi
  fi
fi
[ -d .venv ] || "$HOME/.local/bin/uv" venv --python "$PYBIN" .venv \
  || { echo "VENV-CREATE-FAILED"; exit 1; }
# post-create identity assert: headers + managed base, BEFORE any sync
.venv/bin/python -c "$VENV_ID" || { echo "VENV-IDENTITY-FAILED"; exit 1; }
# --strict: the venv must equal the lock EXACTLY (extras removed).
# torch's CUDA build is fingerprint-gated (torch-cuda line in the
# canonical text), so a wrong-backend wheel fails closed downstream; if
# the default index ever stops resolving cu130, force it explicitly:
#   UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu130
"$HOME/.local/bin/uv" pip sync --strict -p .venv requirements-cluster.lock \
  || { echo "ENV-INSTALL-FAILED"; exit 1; }
.venv/bin/python - <<'PYEOF' || { echo "ENV-LOCK-MISMATCH"; exit 1; }
import sys, os
sys.path.insert(0, os.getcwd())
from provenance import env_matches_lock
ok, probs = env_matches_lock()
assert ok, probs[:8]
print("environment matches requirements-cluster.lock (66 pins + python)")
PYEOF
# WRITE-ONCE software-only freeze (PREREG §4): the canonical SOFTWARE
# identity (python runtime + resolved interpreter BINARY hash + torch
# CUDA build + every distribution; no hardware) is frozen at first
# success; any later mismatch REFUSES (the freeze is evidence, not a
# scratchpad). REFREEZE=1 quarantines the old freeze and writes anew —
# an explicit, logged act.
mkdir -p results_v2/env
FREEZE=results_v2/env/freeze-cluster.txt
.venv/bin/python -c "import sys, os; sys.path.insert(0, os.getcwd());
from provenance import env_canonical
sys.stdout.write(env_canonical())" > "$FREEZE.candidate"
if [ -f "$FREEZE" ]; then
  if cmp -s "$FREEZE" "$FREEZE.candidate"; then
    rm -f "$FREEZE.candidate"
    echo "freeze unchanged (environment identical)"
  elif [ "${REFREEZE:-0}" = "1" ]; then
    TS=$(date +%Y%m%d-%H%M%S)
    mv "$FREEZE" "$FREEZE.quarantine-$TS"
    mv "$FREEZE.candidate" "$FREEZE"
    echo "[REFREEZE] old freeze -> $FREEZE.quarantine-$TS; new freeze written"
  else
    echo "[ENV-FREEZE-MISMATCH] live environment differs from the frozen"
    echo "record; refusing (rerun with REFREEZE=1 to adopt, old freeze is"
    echo "quarantined). Diff:"
    diff "$FREEZE" "$FREEZE.candidate" | head -20 || true
    rm -f "$FREEZE.candidate"
    exit 1
  fi
else
  mv "$FREEZE.candidate" "$FREEZE"
  echo "freeze written (first run)"
fi
# informational RUNTIME NOTES (hardware/kernel; never gated, freely
# rewritten — hardware is characterized by the battery overlap item)
{ echo "generated: $(date -u +%FT%TZ) on $(hostname)"; \
  .venv/bin/python -c "import torch; print('torch', torch.__version__, 'cuda-build', torch.version.cuda)"; \
  nvidia-smi -L 2>/dev/null || echo "no GPU on login node (GPU recorded per job)"; \
  nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true; \
  uname -a; } > results_v2/env/runtime-notes.txt

FAILED=0

echo "=== [fix 3/6] reclone missing corpora ==="
./fetch_corpora.sh || FAILED=1
for d in corpora/*/; do
  n=$(basename "$d")
  [ "$n" = arxiv ] && continue
  git -C "$d" log -1 --oneline >/dev/null 2>&1 \
    || { echo "[STILL-BROKEN] $n"; FAILED=1; }
done

echo "=== [fix 3b/6] arXiv (OPTIONAL corpus, tri-state) ==="
# amendment (PREREG §2): fetch/validation runs ONLY when source material
# is present on disk (shared recursive definition — any .tex anywhere).
# ABSENT -> explicit non-blocking report, no network dependency for G1.
# PRESENT -> repair/validate against the adopted manifest; the script
# validates the EXACT expected set (missing/mismatch -> nonzero; loose
# shell counts were fail-open — review fix) and failure blocks G1.
if .venv/bin/python -c 'import sys; sys.path.insert(0, ".");
from arxiv_fetch import material_present
sys.exit(0 if material_present("corpora/arxiv") else 3)'; then
  .venv/bin/python arxiv_fetch.py --from-manifest || { echo "[ARXIV-FAILED]"; FAILED=1; }
  echo "arxiv: old=$(ls corpora/arxiv/old 2>/dev/null | wc -l) new=$(ls corpora/arxiv/new 2>/dev/null | wc -l) (informational)"
else
  rc=$?
  [ "$rc" -eq 3 ] || { echo "[ARXIV-PRESENCE-CHECK-FAILED] rc=$rc"; FAILED=1; }
  [ "$rc" -eq 3 ] && echo "[ARXIV-ABSENT] optional corpus has no source material — fetch/validation skipped (non-blocking)"
fi
# replay pinned corpus SHAs + verify arXiv identity when a committed lock
# exists (runs AFTER arxiv fetch so checksums.json is present); on the
# first acquisition there is no lock — `corpus_lock.py write` at the G1
# boundary emits it for review + commit
.venv/bin/python corpus_lock.py checkout || FAILED=1

echo "=== [fix 4/6] models ==="
# pipefail propagates models_download's own exit (incl. METADATA-FAILED,
# which produces no [FAIL] line) through the tee|tail pipe
bash models_download.sh 2>&1 | tee /tmp/models_dl.$$ | tail -40 || FAILED=1
if grep -q "\[FAIL\]" /tmp/models_dl.$$; then
  echo "[MODEL-FAILURES] see above"; FAILED=1
fi
rm -f /tmp/models_dl.$$

echo "=== [fix 5/6] streams ==="
.venv/bin/python prep_streams.py || FAILED=1

echo "=== [fix 6/6] pools: DEFERRED to G6 (Phase 2 hard-blocked) ==="

# fail-closed: the done marker only appears when every step verified
if [ "$FAILED" -eq 0 ]; then
  echo "FIX-ALL-DONE"
else
  echo "FIX-INCOMPLETE (see failures above)"
  exit 1
fi
