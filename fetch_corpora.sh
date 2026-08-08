#!/usr/bin/env bash
# Full-history clones. History (not shallow) is required: contamination splits
# need per-file first-add dates via `git log --diff-filter=A --follow`.
set -euo pipefail  # expected failures are explicitly guarded; anything unguarded aborts
export GIT_TERMINAL_PROMPT=0   # a 404'd fallback URL must fail, not prompt
BASE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$BASE/corpora" "$BASE/logs"
cd "$BASE/corpora"

FETCH_FAILED=0
PIDS=()
collect() { # wait on every recorded pid, propagating failures
  local p
  for p in "${PIDS[@]}"; do wait "$p" || FETCH_FAILED=1; done
  PIDS=()
}

clone() { # name url [fallback-urls...]
  local name="$1"; shift
  # a dir that exists but cannot `git log` is a husk from a killed run
  if [ -d "$name/.git" ]; then
    if git -C "$name" log -1 --oneline >/dev/null 2>&1; then
      echo "[skip] $name exists"; return 0
    fi
    echo "[husk] $name — recloning"; rm -rf "$name"
  fi
  local url
  for url in "$@"; do
    echo "[clone] $name <- $url"
    if git clone --quiet "$url" "$name" 2>>"$BASE/logs/clone_err.log"; then
      echo "[ok] $name"; return 0
    fi
    rm -rf "$name"
  done
  echo "[FAIL] $name" >&2; return 1
}

# Wave 1 — the 2x2 grid (Physlib was renamed twice: HepLean -> PhysLean ->
# Physlib; GitHub redirects renames, so older URLs are fallbacks).
clone physlib  https://github.com/Physlib/Physlib https://github.com/HEPLean/PhysLean https://github.com/HEPLean/HepLean & PIDS+=($!)
clone mathlib4 https://github.com/leanprover-community/mathlib4 & PIDS+=($!)
clone qutip    https://github.com/qutip/qutip & PIDS+=($!)
clone sympy    https://github.com/sympy/sympy & PIDS+=($!)
clone batteries https://github.com/leanprover-community/batteries & PIDS+=($!)
collect

# Wave 2 — G3 extension (C++ physics) + near-term v2 repos (DESIGN_V2 §6).
clone geant4   https://github.com/Geant4/geant4 & PIDS+=($!)
clone astropy  https://github.com/astropy/astropy & PIDS+=($!)
collect

# Phase-2-only pools are DEFERRED to the G6 gate (staging consistency:
# same rationale that staged models before 300GB). FETCH_PHASE2=1 opts in.
if [ "${FETCH_PHASE2:-0}" = "1" ]; then
  clone plasmapy https://github.com/PlasmaPy/PlasmaPy & PIDS+=($!)
  clone yt       https://github.com/yt-project/yt & PIDS+=($!)
  clone lean4    https://github.com/leanprover/lean4 & PIDS+=($!)
  clone scipy    https://github.com/scipy/scipy & PIDS+=($!)
  clone sunpy    https://github.com/sunpy/sunpy & PIDS+=($!)
  clone pymatgen https://github.com/materialsproject/pymatgen & PIDS+=($!)
  clone ase      https://gitlab.com/ase/ase & PIDS+=($!)
  collect
fi

if [ "$FETCH_FAILED" -ne 0 ]; then
  echo "CLONES INCOMPLETE" >&2
  exit 1
fi
echo "ALL CLONES DONE"
du -sh "$BASE"/corpora/* 2>/dev/null
