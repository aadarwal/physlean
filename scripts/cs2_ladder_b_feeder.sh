#!/bin/bash
# Feed tasks_ladder_b.txt to mit_normal_gpu in chunks under its
# MaxSubmitJobsPerUser=64 cap (array elements count individually).
#
# v2: CHECK sbatch's exit status. v1 advanced its position even when
# submission was refused (QOSMaxSubmitJobPerUserLimit), silently
# skipping 40 tasks — the failure was visible only in this log. Now a
# refused chunk is retried on the next tick and the position never
# advances past unsubmitted work.
cd /orcd/pool/008/aadarwal/physlean || exit 1
TASKS="$PWD/data/cs2/tasks_ladder_b.txt"
LOG=logs/ladder_b_feeder.log
TOTAL=$(wc -l < "$TASKS")
POS=${CS2_FEED_POS:-0}
CHUNK=${CS2_FEED_CHUNK:-20}
echo "feeder v2 start total=$TOTAL pos=$POS chunk=$CHUNK $(date +%F-%T)" >> "$LOG"
while [ "$POS" -lt "$TOTAL" ]; do
  QUEUED=$(squeue -u aadarwal -h -p mit_normal_gpu 2>/dev/null | wc -l)
  if [ "$QUEUED" -le 4 ]; then
    END=$((POS + CHUNK - 1))
    [ "$END" -ge "$TOTAL" ] && END=$((TOTAL - 1))
    if sbatch -p mit_normal_gpu --gres=gpu:1 --array=${POS}-${END} \
         slurm/cs2_rungs.sbatch "$TASKS" >> "$LOG" 2>&1; then
      echo "fed ${POS}-${END} $(date +%T)" >> "$LOG"
      POS=$((END + 1))
    else
      echo "SUBMIT REFUSED ${POS}-${END} — retrying next tick" >> "$LOG"
    fi
  fi
  sleep 300
done
echo FEEDER-DONE >> "$LOG"
