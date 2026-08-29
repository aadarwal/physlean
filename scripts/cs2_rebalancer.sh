#!/bin/bash
# Keep mit_normal_gpu saturated while the preemptable backlog drains.
#
# v2: key migration on the ladder_a ARRAY JOB ID, not on squeue's %o —
# %o reports only the sbatch script path, never the task-file argument,
# so the v1 filter matched nothing and the partition sat idle.
#
# Whenever normal_gpu is (nearly) drained, take the next chunk of still
# PENDING elements of the ladder_a array on preemptable, scancel them,
# and resubmit those exact indices on normal_gpu. Cancel-before-resubmit
# on PENDING-only elements: a task can never run twice concurrently, and
# no started work is discarded. Capacity probes are never migrated
# (their 32/64-epoch runs can exceed normal_gpu's 6h limit).
cd /orcd/pool/008/aadarwal/physlean || exit 1
LOG=logs/rebalancer.log
JOB=${CS2_LADDER_A_JOB:-$(cat data/cs2/ladder_a_jobid.txt 2>/dev/null)}
CHUNK=${CS2_REBAL_CHUNK:-30}
TASKS="$PWD/data/cs2/tasks_ladder_a.txt"
[ -n "$JOB" ] || { echo "no ladder_a job id; exiting" >> "$LOG"; exit 1; }
echo "rebalancer v2 start job=$JOB chunk=$CHUNK $(date +%F-%T)" >> "$LOG"
while true; do
  sleep 600
  NG=$(squeue -u aadarwal -h -p mit_normal_gpu 2>/dev/null | wc -l)
  [ "$NG" -gt 4 ] && continue
  IDS=$(squeue -r -u aadarwal -h -t PD -o %i 2>/dev/null | grep "^${JOB}_" | head -"$CHUNK")
  [ -n "$IDS" ] || { echo "backlog empty $(date +%T)" >> "$LOG"; continue; }
  IDX=
  for i in $IDS; do IDX=$IDX,${i#*_}; done
  IDX=${IDX#,}
  scancel $IDS 2>>"$LOG"
  sleep 3
  sbatch -p mit_normal_gpu --gres=gpu:1 --array="$IDX" \
    slurm/cs2_rungs.sbatch "$TASKS" >> "$LOG" 2>&1
  echo "migrated $IDX $(date +%T)" >> "$LOG"
done
