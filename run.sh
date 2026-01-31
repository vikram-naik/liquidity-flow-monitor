
#!/bin/bash
echo "Starting Global Liquidity Flow Monitor..."
source venv/bin/activate

echo "Step 0: Initializing Database (if needed)..."
python src/database.py

echo "Step 1: Running Flow Agent..."
python src/agents/flow_agent.py

echo "Step 2: Running CME Agent..."
python src/agents/global_margin_agent.py

echo "Step 3: Launching Dashboard..."
streamlit run src/dashboard/app.py
