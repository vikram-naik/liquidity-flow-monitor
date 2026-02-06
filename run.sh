#!/bin/bash
echo "Starting Global Liquidity Flow Monitor..."

# Ensure we are in the project root
cd "$(dirname "$0")"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

# Ensure pip is up to date and dependencies are installed
echo "Checking dependencies..."
python -m pip install --upgrade pip > /dev/null
python -m pip install -r requirements.txt > /dev/null

echo "Step 0: Initializing Database (if needed)..."
python src/database.py

echo "Step 1: Syncing Local Data (Flow & CME)..."
./scripts/sync_data_local.sh

echo "Step 3: Launching API (Background Worker)..."
# Start API in background - it now handles the intraday worker thread
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

# Trap SIGINT (CTRL+C) and SIGTERM to kill background processes
cleanup() {
    echo "Terminating processes..."
    kill $API_PID 2>/dev/null
    exit
}
trap cleanup SIGINT SIGTERM EXIT

echo "Step 4: Launching Dashboard..."
streamlit run src/dashboard/app.py --server.port 8501
