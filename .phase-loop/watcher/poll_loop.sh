#!/usr/bin/env bash
# 10-minute watcher polling loop
set -uo pipefail
REPO="/Users/sanjayb/nepal-event-anomaly"
CYCLE=1
MAX_CYCLES=72  # 12 hours max
while [ $CYCLE -le $MAX_CYCLES ]; do
  bash "$REPO/.phase-loop/watcher/watcher_check.sh" $CYCLE
  echo ""
  echo "--- Sleeping 600s until cycle $((CYCLE+1)) ---"
  sleep 600
  CYCLE=$((CYCLE+1))
done
echo "=== WATCHER LOOP COMPLETE after $MAX_CYCLES cycles ==="
