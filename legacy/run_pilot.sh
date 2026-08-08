#!/bin/bash
[ "${PHYSLEAN_LEGACY:-0}" = "1" ] || { echo "legacy pilot script: set PHYSLEAN_LEGACY=1 (see legacy/README.md)"; exit 1; }
# physlean pilot: train a tiny byte-LM from scratch on each corpus (matched
# data budgets), then measure held-out per-position NLL = predictability as a
# function of in-context codebase size. See README.md.
set -u
BASE=${PHYSLEAN_BASE:-$(cd "$(dirname "$0")" && pwd)}
BIN=$BASE/llama.cpp/build/bin
MODEL=${MODEL:-$BASE/models/base_small.gguf}
CTX=${CTX:-2048}
EPOCHS=${EPOCHS:-2}
LR=${LR:-0.0003}
THREADS=${THREADS:-$(nproc)}
CORPORA=${CORPORA:-"physlib mathlib qutip sympy"}
mkdir -p $BASE/results $BASE/models/trained
STATUS=$BASE/results/status.txt

log() { echo "[$(date +%H:%M:%S)] $*" >> $STATUS; }

log "pilot start: model=$MODEL ctx=$CTX epochs=$EPOCHS lr=$LR"

# untrained-baseline curve (sanity: flat at ~log2(257) = 8.006 bits/byte)
PPL_FIRST=0 PPL_DUMP=$BASE/results/baseline_physlib.csv \
  $BIN/llama-perplexity -m $MODEL -f $BASE/data/physlib/heldout.txt \
  -c $CTX -b $CTX -t $THREADS > $BASE/results/baseline_physlib.log 2>&1
log "baseline eval done rc=$?"

for c in $CORPORA; do
  log "=== $c: training ==="
  FT_STRIDE=$CTX FT_LR=$LR FT_EPOCHS=$EPOCHS FT_VAL=0 \
  FT_OUT=$BASE/models/trained/$c.gguf \
    $BIN/llama-finetune -m $MODEL \
    -f $BASE/data/$c/train.txt -c $CTX -b $CTX -ub $CTX -t $THREADS \
    > $BASE/results/train_$c.log 2>&1
  rc=$?
  log "$c: training done rc=$rc"
  if [ $rc -ne 0 ] || [ ! -f $BASE/models/trained/$c.gguf ]; then
    log "$c: TRAINING FAILED, skipping eval"; continue
  fi
  log "=== $c: eval ==="
  PPL_FIRST=0 PPL_DUMP=$BASE/results/$c.csv \
    $BIN/llama-perplexity -m $BASE/models/trained/$c.gguf \
    -f $BASE/data/$c/heldout.txt -c $CTX -b $CTX -t $THREADS \
    > $BASE/results/eval_$c.log 2>&1
  log "$c: eval done rc=$? ($(wc -l < $BASE/results/$c.csv 2>/dev/null || echo 0) rows)"
done
log "PILOT COMPLETE"
