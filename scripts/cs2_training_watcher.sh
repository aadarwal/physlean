#!/bin/bash
# Push a git marker when ALL CS-2 training is genuinely complete.
#
# v2: completion is judged by ARTIFACT COUNTS, not by an empty queue.
# v1 keyed on "no cs2-rungs jobs in squeue", which is also true during
# any gap between feeder chunks — and a silently refused submission
# (see the feeder's QOSMaxSubmitJobPerUserLimit failure) would have made
# that gap permanent, firing a "training complete" marker over 40
# missing runs. Counting results cannot mistake absent work for done.
cd /orcd/pool/008/aadarwal/physlean || exit 1
LADDER_TARGET=${CS2_LADDER_TARGET:-168}
CAP_TARGET=${CS2_CAP_TARGET:-24}
LOG=logs/training_watcher.log
echo "watcher v2 start need ladder=$LADDER_TARGET cap=$CAP_TARGET $(date +%F-%T)" >> "$LOG"
while true; do
  L=$(ls results_cs/runs 2>/dev/null | grep -c -- "-r0\.")
  C=$(ls results_cs/runs 2>/dev/null | grep -c cap30m)
  echo "ladder=$L/$LADDER_TARGET cap=$C/$CAP_TARGET $(date +%T)" >> "$LOG"
  if [ "$L" -ge "$LADDER_TARGET" ] && [ "$C" -ge "$CAP_TARGET" ]; then
    (GIT_TERMINAL_PROMPT=0 git pull -q --rebase origin main
     git -c commit.gpgsign=false commit --allow-empty -q \
       -m "marker: CS-2 training complete (ladder $L/$LADDER_TARGET, capacity $C/$CAP_TARGET)"
     GIT_TERMINAL_PROMPT=0 git push -q origin main) && break
  fi
  sleep 900
done
