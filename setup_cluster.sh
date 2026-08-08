#!/usr/bin/env bash
# DEPRECATED as a distinct bootstrap: the original fail-open path produced
# a false SETUP-ALL-DONE (empty husk clones + missing hub CLI, 2026-08-07).
# There is exactly ONE reviewed acquisition path now: fix_cluster.sh
# (idempotent, fail-closed, staged model acquisition). This wrapper only
# delegates so old instructions keep working.
set -euo pipefail
cd "$(dirname "$0")"
echo "[setup_cluster] delegating to the reviewed fail-closed path: fix_cluster.sh"
exec bash fix_cluster.sh
