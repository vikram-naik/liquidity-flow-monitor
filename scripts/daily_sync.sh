#!/bin/bash

# Determine the absolute path to the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || { echo "Failed to navigate to project root"; exit 1; }

# Activate the virtual environment if it's not already set
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f "venv/bin/activate" ]; then
        if [[ "$*" != *"--quiet"* ]] && [[ "$*" != *"-q"* ]]; then
            echo "Activating virtual environment at $PROJECT_ROOT/venv"
        fi
        source venv/bin/activate
    elif [ -f "/.dockerenv" ] || { [ -f "/proc/1/cgroup" ] && grep -q "docker" /proc/1/cgroup; }; then
        echo "Detected Docker/Container environment, skipping venv activation."
    else
        echo "Error: Virtual environment not found at $PROJECT_ROOT/venv. Please set it up."
        exit 1
    fi
fi

# Ensure Python can find the 'src' package
export PYTHONPATH="$PROJECT_ROOT"

# --- Parse CLI Options ---
QUIET=0
QUIET_FLAG=""
for arg in "$@"; do
    if [[ "$arg" == "--quiet" ]] || [[ "$arg" == "-q" ]]; then
        QUIET=1
        QUIET_FLAG="--quiet"
    fi
done

if [[ $QUIET -eq 0 ]]; then
    echo "========================================"
    echo "Starting Daily EOD Data Sync"
    echo "Date: $(date)"
    echo "========================================"
fi

if [[ $QUIET -eq 0 ]]; then echo "[1/8] Downloading Corporate Actions from NSE..."; fi
python scripts/sync_nse_ca.py --all --yes $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[2/8] Flushing Redis Cache..."; fi
python scripts/flush_cache.py --all $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[3/8] Syncing NSE Equities Delivery Data..."; fi
python src/agents/nse_agent.py --sync $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[4/8] Syncing NSE Indices Data..."; fi
python src/agents/nse_indices_agent.py --sync $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[5/8] Syncing Index Watchlists..."; fi
python scripts/sync_index_watchlists.py $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[6/8] Validating Data Integrity & Reconciliation..."; fi
python scripts/validate_data_integrity.py --watchlist "NIFTY 500" --auto-fix --auto-patch $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[7/8] Warming Engine Cache (NIFTY 500)..."; fi
python scripts/warm_cache.py --watchlist "NIFTY 500" $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then echo "[8/8] Running Global Market Screener..."; fi
python scripts/daily_screener.py $QUIET_FLAG

if [[ $QUIET -eq 0 ]]; then
    echo "========================================"
    echo "Daily EOD Data Sync Complete"
    echo "========================================"
fi
