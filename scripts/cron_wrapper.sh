#!/bin/bash

# Log file
LOG_FILE="/home/vn/python-projects/liquidity-flow-monitor/cron_debug.log"

# Function to log
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "Starting Cron Wrapper..."

# Set environment
# Ensure basic paths are set
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/vn

# Go to project dir
PROJECT_DIR="/home/vn/python-projects/liquidity-flow-monitor"
if [ ! -d "$PROJECT_DIR" ]; then
    log "Error: Project directory $PROJECT_DIR not found."
    exit 1
fi
cd "$PROJECT_DIR"

# Run Sync
log "Running sync_data_local.sh..."
./scripts/sync_data_local.sh >> "$LOG_FILE" 2>&1
SYNC_EXIT=$?

if [ $SYNC_EXIT -eq 0 ]; then
    log "Sync successful. Running publish..."
    # Using the password from the original crontab
    ./scripts/publish_lfm.sh "lfm_api_user@15" >> "$LOG_FILE" 2>&1
    PUB_EXIT=$?
    if [ $PUB_EXIT -eq 0 ]; then
        log "Publish successful."
    else
        log "Publish failed with exit code $PUB_EXIT"
    fi
else
    log "Sync failed with exit code $SYNC_EXIT"
fi

log "Done."
