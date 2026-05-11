#!/bin/bash
# LFM Daily Sync Cron Runner
# This script is intended to be run from the HOST crontab.
# It triggers the synchronization process inside the running Docker container.

CONTAINER_NAME="lfm-app-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/cron_sync.log"

# Ensure log directory exists
mkdir -p "$SCRIPT_DIR/logs"

echo "[$(date)] Starting sync for $CONTAINER_NAME" >> "$LOG_FILE"

# Check if container is running
if [ "$(docker ps -q -f name=$CONTAINER_NAME)" ]; then
    docker exec "$CONTAINER_NAME" /app/scripts/daily_sync.sh >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date)] Sync completed successfully. Starting automated walk-forward backtest..." >> "$LOG_FILE"
        docker exec "$CONTAINER_NAME" python /app/scripts/walk_forward.py --watchlist "NIFTY 50" >> "$LOG_FILE" 2>&1
        if [ $? -eq 0 ]; then
            echo "[$(date)] Walk-forward backtest completed successfully." >> "$LOG_FILE"
        else
            echo "[$(date)] Walk-forward backtest failed." >> "$LOG_FILE"
        fi
    else
        echo "[$(date)] Sync failed with exit code $EXIT_CODE." >> "$LOG_FILE"
    fi
else
    echo "[$(date)] Error: Container $CONTAINER_NAME is not running." >> "$LOG_FILE"
fi

echo "----------------------------------------" >> "$LOG_FILE"
