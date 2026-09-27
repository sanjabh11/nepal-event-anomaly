#!/bin/bash
# Resilient wrapper for ERA5-Land download.
# Restarts the download if it crashes (macOS multiprocessing semaphore issue).
# The download script has checkpoint logic, so it resumes from where it left off.

cd /Users/sanjayb/nepal-event-anomaly
source .venv/bin/activate

MAX_RESTARTS=20
RESTART_COUNT=0

while [ $RESTART_COUNT -lt $MAX_RESTARTS ]; do
    echo "=== Download attempt $((RESTART_COUNT + 1))/$MAX_RESTARTS ==="
    echo "Start time: $(date)"

    python -u nepal/era5_download.py 2>&1
    EXIT_CODE=$?

    echo "Download exited with code: $EXIT_CODE"
    echo "End time: $(date)"

    # Check if download is complete
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Download completed successfully."
        break
    fi

    # Check if final output exists (merge may have succeeded despite exit code)
    if [ -f "data/era5_land_nepal_jja_2001_2026.nc" ]; then
        echo "Final output exists, download likely complete."
        break
    fi

    RESTART_COUNT=$((RESTART_COUNT + 1))
    echo "Restarting in 60 seconds... (attempt $RESTART_COUNT/$MAX_RESTARTS)"
    sleep 60
done

if [ $RESTART_COUNT -ge $MAX_RESTARTS ]; then
    echo "ERROR: Max restarts ($MAX_RESTARTS) reached. Manual intervention needed."
    exit 1
fi

echo "=== Wrapper complete ==="
