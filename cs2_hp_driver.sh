#!/usr/bin/env bash
# ARM_CS CS-2 HP-walk driver (login-node, nohup): drives the frozen HP
# state machine of ARM_CS §4 — wait for each rung's array to drain, pick
# the incumbent, generate + submit the next rung's 6-run walk. Stops after
# the final pick (the LADDER stage is gated on the beta_corr registration
# and is never submitted here). Ends by committing+pushing
# results_cs/hp_incumbents.json as a completion marker.
set -uo pipefail
cd "$(dirname "$0")"
FRACS=(0.015625 0.031250 0.062500 0.125000 0.250000 0.500000 1.000000)
PY=.venv/bin/python

wait_drain() {
  while squeue -h -u "$USER" -n cs2-rungs | grep -q .; do sleep 60; done
}

echo "[driver] waiting for rung-1 HP grid to drain"
wait_drain
$PY cs2_launch.py --stage pick --frac "${FRACS[0]}" --expect-set grid \
  || { echo "[driver] pick failed (fail-closed); abort"; exit 1; }
for i in 1 2 3 4 5 6; do
  f="${FRACS[$i]}"
  echo "[driver] walk rung $((i + 1)) (frac $f)"
  $PY cs2_launch.py --stage walk --frac "$f"
  T="data/cs2/tasks_walk_${f}.txt"
  [ -s "$T" ] || { echo "[driver] no tasks for $f; abort"; exit 1; }
  n=$(wc -l < "$T")
  # wide preemptable pool: CS-2 tasks are minutes-long and idempotent
  # (skip-if-done + request/hash verification), so requeue is safe
  sbatch -p mit_preemptable --gres=gpu:1 --requeue \
    --array=0-$((n - 1))%32 slurm/cs2_rungs.sbatch "$PWD/$T"
  sleep 30
  wait_drain
  $PY cs2_launch.py --stage pick --frac "$f" --expect-set walk \
    || { echo "[driver] pick failed (fail-closed); abort"; exit 1; }
done
echo HP-WALK-COMPLETE
git add results_cs/hp_incumbents.json
git -c commit.gpgsign=false commit -m "CS-2 HP incumbents (walk complete, cluster driver)" \
  && git push origin main || echo "[driver] marker push failed (non-fatal)"
