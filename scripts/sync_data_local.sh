#!/bin/bash
# scripts/sync_data_local.sh

# --- CONFIGURATION ---
export FRED_API_KEY="5bbee1aad376b64693645ea3a2c8becd"
SYNC_LOG_FILE=".last_sync.txt"

echo "🔄 Starting Daily Local Data Sync..."

# Ensure we are in the project root
cd "$(dirname "$0")/.."

# Check for venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Step 1: Running Flow Agent (FRED Yields & FX)..."
python3 src/agents/flow_agent.py

echo "Step 2: Running CME Margin Agent..."
python3 src/agents/global_margin_agent.py

echo "Step 3: Running US Treasury Agent..."
python3 src/agents/treasury_agent.py

echo "Step 4: Running NSE Delivery Agent (Smart Sync)..."
python3 src/agents/nse_agent.py --sync

# Record sync timestamp
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" > "$SYNC_LOG_FILE"

echo "✅ Local Sync Complete!"
