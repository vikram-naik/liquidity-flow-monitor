import sys
import os
import sqlite3
import pandas as pd
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from scratch.analyze_trends import parse_trends, clean_date_str

def main():
    watchlist = "NIFTY 50"
    start_date_limit = pd.to_datetime("2025-12-01")
    
    trends = parse_trends()
    trends_filtered = []
    for sym, dt_str in trends:
        dt = clean_date_str(dt_str)
        if dt >= start_date_limit:
            sym_clean = "ASIANPAINT" if sym == "ASIANPAINTS" else sym
            trends_filtered.append((sym_clean, dt))
            
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    
    print("Diagnosing missed trends from trends.txt...")
    results = []
    
    for sym, dt in trends_filtered:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            continue
            
        # Find closest index
        match_df = df[df['date_dt'] <= dt]
        if match_df.empty:
            continue
        t_idx = match_df.index[-1]
        
        # Check reasons in a window around the target date
        # Let's inspect the target date t_idx first, then maybe t_idx - 1 or t_idx + 1 if needed
        # We want to see what is failing on the target date t_idx
        row = df.loc[t_idx].to_dict()
        prev_row = df.loc[t_idx - 1].to_dict() if t_idx > 0 else row
        
        passed, intensity, meta = signal.check_entry(row, prev_row, entry_cfg, df.to_dict('records'), t_idx)
        
        results.append({
            "symbol": sym,
            "target_date": dt.strftime('%Y-%m-%d'),
            "actual_date": df.loc[t_idx, 'date_dt'].strftime('%Y-%m-%d'),
            "passed": passed,
            "reason": meta.get("reason", "")
        })
        
    df_res = pd.DataFrame(results)
    print("\n--- REJECTION REASONS FOR MISSED TRENDS ---")
    for idx, row in df_res.iterrows():
        print(f"\n{row['symbol']} on {row['target_date']} (Actual: {row['actual_date']}): passed={row['passed']}")
        reasons = row['reason'].split(" | ")
        for r in reasons:
            if "disabled" not in r:
                print(f"  - {r}")

if __name__ == "__main__":
    main()
