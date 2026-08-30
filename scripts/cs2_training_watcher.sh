#!/bin/bash
# Push a git marker when ALL CS-2 training is genuinely complete, and
# resubmit anything that silently went missing in the meantime.
#
# v3: completion is decided by scripts/cs2_missing.py, which derives the
# expected artifact path for every task line. v2 counted result files
# with `grep -c -- "-r0\."`, which silently excludes the frac=1.000000
# rung (tag "-r1.000000-…"): it undercounted the ladder by 24 runs and
# could never have reached its own 168 target, so the marker would never
# have fired. Deriving from the task file cannot drift from the design.
#
# The sweep also re-submits tasks lost to preemption, cancellation, or a
# refused submit — the failure modes that cost this run ~40 tasks twice.
cd /orcd/pool/008/aadarwal/physlean || exit 1
LOG=logs/training_watcher.log
CAP_TARGET=${CS2_CAP_TARGET:-24}
echo "watcher v3 start $(date +%F-%T)" >> "$LOG"
while true; do
  MISS=$(.venv/bin/python scripts/cs2_missing.py --write 2>>"$LOG")
  CAP=$(ls results_cs/runs 2>/dev/null | grep -c cap30m)
  N=$(echo "$MISS" | sed -E 's/.*missing=([0-9]+).*/\1/')
  echo "$MISS cap=$CAP/$CAP_TARGET $(date +%T)" >> "$LOG"
  if [ "$N" = "0" ] && [ "$CAP" -ge "$CAP_TARGET" ]; then
    (GIT_TERMINAL_PROMPT=0 git pull -q --rebase origin main
     git -c commit.gpgsign=false commit --allow-empty -q \
       -m "marker: CS-2 training complete (ladder 168/168, capacity $CAP/$CAP_TARGET)"
     GIT_TERMINAL_PROMPT=0 git push -q origin main) && break
  fi
  # resubmit missing work whenever the queue has drained
  Q=$(squeue -u aadarwal -h 2>/dev/null | wc -l)
  if [ "$Q" -le 1 ] && [ "$N" != "0" ]; then
    NB=$(wc -l < data/cs2/tasks_missing_big.txt)
    NS=$(wc -l < data/cs2/tasks_missing_small.txt)
    [ "$NB" -gt 0 ] && { sbatch -p mit_preemptable --gres=gpu:1 --requeue \
      -t 24:00:00 --array=0-$((NB - 1)) slurm/cs2_rungs.sbatch \
      "$PWD/data/cs2/tasks_missing_big.txt" >> "$LOG" 2>&1 \
      && echo "swept big=$NB $(date +%T)" >> "$LOG" \
      || echo "SWEEP BIG REFUSED $(date +%T)" >> "$LOG"; }
    [ "$NS" -gt 0 ] && { sbatch -p mit_normal_gpu --gres=gpu:1 \
      --array=0-$((NS - 1)) slurm/cs2_rungs.sbatch \
      "$PWD/data/cs2/tasks_missing_small.txt" >> "$LOG" 2>&1 \
      && echo "swept small=$NS $(date +%T)" >> "$LOG" \
      || echo "SWEEP SMALL REFUSED $(date +%T)" >> "$LOG"; }
  fi
  sleep 900
done
