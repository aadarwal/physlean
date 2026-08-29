#!/bin/bash
# Keep mit_normal_gpu saturated while the preemptable backlog drains.
#
# v3 fixes two silent-loss hazards found by auditing v2 against the
# feeder's QOSMaxSubmitJobPerUserLimit failure:
#   1. v2 scancel'd pending elements and THEN submitted. A refused
#      submit would have destroyed those tasks outright. v3 checks the
#      submit-cap headroom first, verifies sbatch's exit status, and on
#      failure immediately restores the indices to preemptable.
#   2. v2 dropped v1's FEEDER-DONE gate, so rebalancer and feeder raced
#      for the same 64-slot submit budget, each causing the other's
#      refusals. v3 waits for the feeder to finish before migrating.
# Capacity probes are never migrated (32/64-epoch runs can exceed
# normal_gpu's 6h limit).
cd /orcd/pool/008/aadarwal/physlean || exit 1
LOG=logs/rebalancer.log
JOB=${CS2_LADDER_A_JOB:-$(cat data/cs2/ladder_a_jobid.txt 2>/dev/null)}
CHUNK=${CS2_REBAL_CHUNK:-20}
CAP=${CS2_SUBMIT_CAP:-64}
TASKS="$PWD/data/cs2/tasks_ladder_a.txt"
[ -n "$JOB" ] || { echo "no ladder_a job id; exiting" >> "$LOG"; exit 1; }
echo "rebalancer v3 start job=$JOB chunk=$CHUNK $(date +%F-%T)" >> "$LOG"
while true; do
  sleep 600
  grep -q FEEDER-DONE logs/ladder_b_feeder.log 2>/dev/null || continue
  NG=$(squeue -u aadarwal -h -p mit_normal_gpu 2>/dev/null | wc -l)
  [ "$NG" -gt 4 ] && continue
  HEADROOM=$((CAP - NG))
  [ "$HEADROOM" -lt 1 ] && continue
  N=$CHUNK
  [ "$HEADROOM" -lt "$N" ] && N=$HEADROOM
  IDS=$(squeue -r -u aadarwal -h -t PD -o %i 2>/dev/null | grep "^${JOB}_" | head -"$N")
  [ -n "$IDS" ] || { echo "backlog empty $(date +%T)" >> "$LOG"; continue; }
  IDX=
  for i in $IDS; do IDX=$IDX,${i#*_}; done
  IDX=${IDX#,}
  scancel $IDS 2>>"$LOG"
  sleep 3
  if sbatch -p mit_normal_gpu --gres=gpu:1 --array="$IDX" \
       slurm/cs2_rungs.sbatch "$TASKS" >> "$LOG" 2>&1; then
    echo "migrated $IDX $(date +%T)" >> "$LOG"
  else
    echo "MIGRATE FAILED $IDX — restoring to preemptable $(date +%T)" >> "$LOG"
    sbatch -p mit_preemptable --gres=gpu:1 --requeue --array="$IDX" \
      slurm/cs2_rungs.sbatch "$TASKS" >> "$LOG" 2>&1 \
      || echo "RESTORE ALSO FAILED $IDX — MANUAL RESUBMIT NEEDED" >> "$LOG"
  fi
done
