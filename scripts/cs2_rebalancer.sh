#!/bin/bash
# Keep mit_normal_gpu saturated: once the ladder_b feeder has submitted
# everything and normal_gpu is (nearly) drained, migrate PENDING array
# elements of the ladder_a backlog from mit_preemptable in small chunks.
# Cancel-before-resubmit: an element is scancel'd while PENDING and only
# then resubmitted on normal_gpu, so a task never runs twice at once.
# Capacity probes are never migrated (e64 runs exceed the 6h limit).
cd /orcd/pool/008/aadarwal/physlean || exit 1
LOG=logs/rebalancer.log
CHUNK=6
echo "rebalancer start $(date +%F-%T)" >> "$LOG"
while true; do
  sleep 900
  grep -q FEEDER-DONE logs/ladder_b_feeder.log 2>/dev/null || continue
  NG=$(squeue -u aadarwal -h -p mit_normal_gpu 2>/dev/null | wc -l)
  [ "$NG" -gt 2 ] && continue
  squeue -r -u aadarwal -h -t PD -p mit_preemptable -o "%i %o" 2>/dev/null | \
    grep -F "tasks_ladder_a.txt" | grep -oE "^[0-9]+_[0-9]+" | head -n "$CHUNK" | \
  while read -r EL; do
    IDX=${EL#*_}
    if scancel "$EL" 2>>"$LOG"; then
      sbatch -p mit_normal_gpu --gres=gpu:1 --array=${IDX}-${IDX} \
        slurm/cs2_rungs.sbatch "$PWD/data/cs2/tasks_ladder_a.txt" >> "$LOG" 2>&1
      echo "moved ladder_a[$IDX] $(date +%T)" >> "$LOG"
    fi
  done
done
