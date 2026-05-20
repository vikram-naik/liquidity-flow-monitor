import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from src.divergence_engine.engine import DivergenceEngine

def check_stock(symbol, date_str):
    print(f"\n--- Checking {symbol} near {date_str} ---")
    engine = DivergenceEngine(symbol)
    res = engine.run()
    ledger = res.ledger
    ledger['date_str'] = ledger['date'].astype(str).str[:10]
    
    matches = ledger[ledger['date_str'] == date_str]
    if matches.empty:
        print(f"Date {date_str} not found for {symbol}")
        return
        
    idx = matches.index[0]
    # Get last 6 bars including the signal date
    start_idx = max(0, idx - 5)
    sub = ledger.iloc[start_idx : idx + 1]
    
    cols = ['date_str', 'close', 'psz_v', 'psz_v_slope', 'psz_v_accel', 'price_slope_z']
    available_cols = [c for c in cols if c in ledger.columns]
    print(sub[available_cols].to_string())

check_stock("TRENT", "2025-01-31")
check_stock("WIPRO", "2025-03-24")
check_stock("CIPLA", "2026-02-03")
