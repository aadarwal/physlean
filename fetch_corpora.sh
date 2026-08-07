#!/usr/bin/env bash
# Full-history clones. History (not shallow) is required: contamination splits
# need per-file first-add dates via `git log --diff-filter=A --follow`.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$BASE/corpora" "$BASE/logs"
cd "$BASE/corpora"

clone() { # name url [fallback-urls...]
  local name="$1"; shift
  if [ -d "$name/.git" ]; then echo "[skip] $name exists"; return 0; fi
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
clone physlib  https://github.com/Physlib/Physlib https://github.com/HEPLean/PhysLean https://github.com/HEPLean/HepLean &
clone mathlib4 https://github.com/leanprover-community/mathlib4 &
clone qutip    https://github.com/qutip/qutip &
clone sympy    https://github.com/sympy/sympy &
clone batteries https://github.com/leanprover-community/batteries &
wait

# Wave 2 — Phase 1 extensions (C++ physics, Lean core) and Phase 2 pools.
clone geant4   https://github.com/Geant4/geant4 &
clone astropy  https://github.com/astropy/astropy &
clone plasmapy https://github.com/PlasmaPy/PlasmaPy &
clone yt       https://github.com/yt-project/yt &
clone lean4    https://github.com/leanprover/lean4 &
wait

# Wave 3 — python-physics pool wideners (python was the binding budget).
clone scipy    https://github.com/scipy/scipy &
clone sunpy    https://github.com/sunpy/sunpy &
clone pymatgen https://github.com/materialsproject/pymatgen &
clone ase      https://gitlab.com/ase/ase &
wait

echo "ALL CLONES DONE"
du -sh "$BASE"/corpora/* 2>/dev/null
