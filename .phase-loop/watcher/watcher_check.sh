#!/usr/bin/env bash
# Watcher cycle check — runs all 7 isolation/integrity checks
# Usage: bash watcher_check.sh <cycle_number>
set -uo pipefail

REPO="/Users/sanjayb/nepal-event-anomaly"
CYCLE="${1:-0}"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOG="$REPO/.phase-loop/watcher/cycle_${CYCLE}.log"

mkdir -p "$REPO/.phase-loop/watcher"
exec > >(tee "$LOG") 2>&1

echo "=== WATCHER CYCLE $CYCLE — $TS ==="
echo ""

# --- Check 1: Frozen artifact hash integrity ---
echo "--- CHECK 1: Frozen Artifact Hash Integrity ---"
declare -A EXPECTED=(
  ["preregistration.md"]="0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c"
  ["data/gmm_false_positive_results.json"]="fb711617194d41ed4f9b109f72c07558c22d247b6266b4dbb59f211a76cfd2bc"
  ["data/locked_jja_events.json"]="536125c44033cd6852e82c4718cca2a9138ca0f369e2cecf84de4c215af427eb"
  ["data/features_nepal_jja_2001_2026.csv"]="444f2ac0904757d67e68a0c8cbec8544a355a0a16c42ed7ffa86c2556a338041"
)
FROZEN_OK=true
for f in "${!EXPECTED[@]}"; do
  actual=$(shasum -a 256 "$REPO/$f" 2>/dev/null | awk '{print $1}')
  if [ "$actual" = "${EXPECTED[$f]}" ]; then
    echo "  OK   $f"
  else
    echo "  FAIL $f — expected ${EXPECTED[$f]:0:16}... got ${actual:0:16}..."
    FROZEN_OK=false
  fi
done
$FROZEN_OK && echo "  RESULT: ALL FROZEN ARTIFACTS INTACT" || echo "  RESULT: FROZEN ARTIFACT VIOLATION DETECTED"
echo ""

# --- Check 2: Codex lane — no writes to GLM2 data scope ---
echo "--- CHECK 2: Codex Lane Isolation (no writes to data/framework_inputs_v1/) ---"
CODEX_VIOLATIONS=$(find "$REPO/data/framework_inputs_v1/" -newermt "-10 minutes" -type f 2>/dev/null | grep -v __pycache__ | head -10)
if [ -z "$CODEX_VIOLATIONS" ]; then
  echo "  OK   No recent Codex writes to GLM2 data scope"
else
  echo "  WARN Recent modifications in data/framework_inputs_v1/ (may be GLM2 lane):"
  echo "$CODEX_VIOLATIONS" | sed 's/^/    /'
fi
echo ""

# --- Check 3: GLM2 lane — no writes to Codex code scope ---
echo "--- CHECK 3: GLM2 Lane Isolation (no writes to nepal/ or tests/) ---"
GLM2_VIOLATIONS=$(find "$REPO/nepal/framework_v1/" "$REPO/tests/" -newermt "-10 minutes" -type f 2>/dev/null | grep -v __pycache__ | head -10)
if [ -z "$GLM2_VIOLATIONS" ]; then
  echo "  OK   No recent GLM2 writes to Codex code scope"
else
  echo "  WARN Recent modifications in nepal/ or tests/ (may be Codex lane):"
  echo "$GLM2_VIOLATIONS" | sed 's/^/    /'
fi
echo ""

# --- Check 4: Disk space ---
echo "--- CHECK 4: Disk Space ---"
DISK_AVAIL=$(df -h "$REPO" | tail -1 | awk '{print $4}')
DISK_PCT=$(df -h "$REPO" | tail -1 | awk '{print $5}')
DATA_SIZE=$(du -sh "$REPO/data/" 2>/dev/null | awk '{print $1}')
FW_SIZE=$(du -sh "$REPO/data/framework_inputs_v1/" 2>/dev/null | awk '{print $1}')
echo "  Disk available: $DISK_AVAIL ($DISK_PCT used)"
echo "  data/ size: $DATA_SIZE"
echo "  framework_inputs_v1/ size: $FW_SIZE"
if [ "$DISK_PCT" \> "95%" ]; then
  echo "  WARN Disk usage above 95%"
fi
echo ""

# --- Check 5: Test suite status ---
echo "--- CHECK 5: Test Suite (framework_v1) ---"
TEST_RESULT=$("$REPO/.venv/bin/python" -m pytest tests/test_framework_v1_catalog.py tests/test_framework_v1_contract.py tests/test_framework_v1_extensions.py tests/test_framework_v1_provenance.py tests/test_framework_v1_screen.py tests/test_framework_v1_validation.py -q --tb=no 2>&1 | tail -1)
echo "  $TEST_RESULT"
echo ""

# --- Check 6: Manifest artifact count ---
echo "--- CHECK 6: Manifest Status ---"
"$REPO/.venv/bin/python" -c "
import json
m = json.load(open('$REPO/data/framework_inputs_v1/manifest.json'))
arts = m.get('artifacts', [])
from collections import Counter
c = Counter(a['status'] for a in arts)
print(f'  Artifacts: {len(arts)}')
for s, n in sorted(c.items()):
    print(f'    {s}: {n}')
print(f'  Manifest hash: {m.get(\"manifest_sha256\",\"MISSING\")[:32]}')
" 2>&1
echo ""

# --- Check 7: Recent file modifications (last 10 min) ---
echo "--- CHECK 7: Recent File Modifications (last 10 min) ---"
RECENT=$(find "$REPO" -newermt "-10 minutes" -type f \
  -not -path "*/.git/*" \
  -not -path "*/__pycache__/*" \
  -not -path "*/.phase-loop/watcher/*" \
  2>/dev/null | head -20)
if [ -z "$RECENT" ]; then
  echo "  No recent modifications"
else
  echo "$RECENT" | sed 's/^/  /'
fi
echo ""

echo "=== CYCLE $CYCLE COMPLETE ==="
