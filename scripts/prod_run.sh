#!/bin/bash

# Project root directory
PROJECT_ROOT="$HOME/mfm/scripts"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/lfm-price-download-prod_run.log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

echo "--------------------------------------------------" >> "$LOG_FILE"
echo "[$(date)] Starting Production Run" >> "$LOG_FILE"

cd "$PROJECT_ROOT" || { echo "Failed to cd to $PROJECT_ROOT" >> "$LOG_FILE"; exit 1; }

# 1. Run NSE Data Sync
echo "[$(date)] Step 1: Syncing NSE Data..." >> "$LOG_FILE"
docker exec scripts-lfm-api-1 python3 src/agents/nse_agent.py --sync >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date)] NSE Sync successful." >> "$LOG_FILE"
else
    echo "[$(date)] NSE Sync FAILED. Stopping execution to prevent running screener on old data." >> "$LOG_FILE"
    echo "--------------------------------------------------" >> "$LOG_FILE"
    exit 1
fi

# 2. Run Screener
echo "[$(date)] Step 2: Running Screener..." >> "$LOG_FILE"
docker exec scripts-lfm-api-1 python3 scripts/run_screener.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date)] Screener successful." >> "$LOG_FILE"
else
    echo "[$(date)] Screener FAILED. Check logs above." >> "$LOG_FILE"
fi

echo "[$(date)] Production Run Complete" >> "$LOG_FILE"
echo "--------------------------------------------------" >> "$LOG_FILE"
