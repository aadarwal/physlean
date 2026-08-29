#!/bin/bash
# Push a git marker when ALL CS-2 training is drained: the ladder_b
# feeder has submitted everything (FEEDER-DONE) and no cs2-rungs jobs
# remain in any partition for 3 consecutive checks (preempted/requeued
# jobs still show in squeue, so this cannot false-fire mid-requeue).
cd /orcd/pool/008/aadarwal/physlean || exit 1
while true; do
  if grep -q FEEDER-DONE logs/ladder_b_feeder.log 2>/dev/null; then
    n=0
    for _ in 1 2 3; do
      c=$(squeue -u aadarwal -h -o "%j" 2>/dev/null | grep -c cs2-rungs)
      n=$((n + c))
      sleep 120
    done
    if [ "$n" -eq 0 ]; then
      (GIT_TERMINAL_PROMPT=0 git pull -q --rebase origin main
       git -c commit.gpgsign=false commit --allow-empty -q \
         -m "marker: CS-2 training complete (capacity + ladder drained)"
       GIT_TERMINAL_PROMPT=0 git push -q origin main) && break
    fi
  fi
  sleep 600
done
