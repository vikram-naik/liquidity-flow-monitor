#!/bin/bash

# Determine the absolute path to the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || { echo "Failed to navigate to project root"; exit 1; }

# Activate the virtual environment if it's not already set
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f "venv/bin/activate" ]; then
        echo "Activating virtual environment at $PROJECT_ROOT/venv"
        source venv/bin/activate
    else
        echo "Error: Virtual environment not found at $PROJECT_ROOT/venv. Please set it up."
        exit 1
    fi
fi

# Ensure Python can find the 'src' package
export PYTHONPATH="$PROJECT_ROOT"

echo "========================================"
echo "Starting Daily EOD Data Sync"
echo "Date: $(date)"
echo "========================================"

echo ""
echo "[1/4] Downloading Corporate Actions from NSE..."
python scripts/sync_nse_ca.py --all --yes

echo ""
echo "[2/4] Flushing Redis Cache..."
python scripts/flush_cache.py --all

echo ""
echo "[3/4] Syncing NSE Equities Delivery Data..."
python src/agents/nse_agent.py --sync

echo ""
echo "[4/4] Syncing NSE Indices Data..."
python src/agents/nse_indices_agent.py --sync

echo ""
echo "[5/6] Syncing Index Watchlists..."
python scripts/sync_index_watchlists.py

echo ""
echo "[6/6] Validating Data Integrity & Reconciliation..."
python scripts/validate_data_integrity.py --watchlist "NIFTY 50" --auto-fix --auto-patch

echo ""
echo "========================================"
echo "Daily EOD Data Sync Complete"
echo "========================================"
