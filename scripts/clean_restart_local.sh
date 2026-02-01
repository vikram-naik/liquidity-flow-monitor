#!/bin/bash
# scripts/clean_restart_local.sh

echo "🧹 Cleaning up local database..."
rm -f liquidity_monitor.db

echo "🚀 Re-initializing database with UTC seeds..."
python3 src/database.py

echo "📊 Re-running backfill (UTC-aligned)..."
python3 src/agents/cme_historical_agent.py

echo "📡 Re-running agents..."
python3 src/agents/flow_agent.py
python3 src/agents/global_margin_agent.py

echo "✅ Local reset complete. You can now run: "
echo "python3 src/publisher.py --host <PROD_URL> --user <USER> --pass <PASS> --backfill"
