#!/bin/bash
# scripts/sync_data_local.sh

# --- CONFIGURATION ---
export FRED_API_KEY="5bbee1aad376b64693645ea3a2c8becd"

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

echo "✅ Local Sync Complete!"
