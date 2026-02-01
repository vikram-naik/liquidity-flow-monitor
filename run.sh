
#!/bin/bash
echo "Starting Global Liquidity Flow Monitor..."
source venv/bin/activate

echo "Step 0: Initializing Database (if needed)..."
python src/database.py

echo "Step 1: Syncing Local Data (Flow & CME)..."
./scripts/sync_data_local.sh

echo "Step 3: Launching Dashboard..."
streamlit run src/dashboard/app.py
