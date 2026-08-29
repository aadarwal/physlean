#!/bin/bash
# Feed tasks_ladder_b.txt to mit_normal_gpu in chunks under its
# MaxSubmitJobsPerUser=64 cap (arrays count per task). Chunk 40, refill
# when the partition queue is nearly drained. Tasks are idempotent, so a
# rerun of this feeder is safe (already-done tasks exit in ~1 min).
cd /orcd/pool/008/aadarwal/physlean || exit 1
TASKS="$PWD/data/cs2/tasks_ladder_b.txt"
LOG=logs/ladder_b_feeder.log
TOTAL=$(wc -l < "$TASKS")
POS=${CS2_FEED_POS:-0}
CHUNK=40
echo "feeder start total=$TOTAL pos=$POS" >> "$LOG"
while [ "$POS" -lt "$TOTAL" ]; do
  QUEUED=$(squeue -u aadarwal -h -p mit_normal_gpu 2>/dev/null | wc -l)
  if [ "$QUEUED" -le 4 ]; then
    END=$((POS + CHUNK - 1))
    [ "$END" -ge "$TOTAL" ] && END=$((TOTAL - 1))
    sbatch -p mit_normal_gpu --gres=gpu:1 --array=${POS}-${END} \
      slurm/cs2_rungs.sbatch "$TASKS" >> "$LOG" 2>&1
    echo "fed ${POS}-${END}" >> "$LOG"
    POS=$((END + 1))
  fi
  sleep 600
done
echo FEEDER-DONE >> "$LOG"
