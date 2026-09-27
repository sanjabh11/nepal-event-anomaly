#!/usr/bin/env bash
# Arm C retrieval launcher — idempotent resume.
# Usage:  bash scripts/armc_retrieve_launch.sh
# Each worker skips chunks whose payload .nc already exists, so re-running
# after a reboot/sleep continues from where it stopped. Logs land in the
# Arm C evidence root under logs/ (persistent, not /tmp).
set -u
cd "$(dirname "$0")/.."   # integration repo root
EVIDENCE=/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22
mkdir -p "$EVIDENCE/logs"
for v in geopotential specific_humidity temperature vertical_velocity; do
  # skip if already running
  if pgrep -f "armc_retrieve.* $v$" >/dev/null || pgrep -f "armc_retrieve.* $v " >/dev/null; then
    echo "$v: already running"; continue
  fi
  nohup caffeinate -i ./.venv/bin/python scripts/armc_retrieve_driver.py "$v" \
      > "$EVIDENCE/logs/armc_$v.log" 2>&1 &
  echo "$v: launched pid $!"
done
