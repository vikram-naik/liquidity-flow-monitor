#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals import SignalFactory

def main():
    symbols = ["MAXHEALTH", "HCLTECH", "ADANIENT", "ITC"]
    target_dates = {
        "MAXHEALTH": "2025-12-11",
        "HCLTECH": "2026-03-09",
        "ADANIENT": "2024-10-29",
        "ITC": "2025-11-17"
    }
    
    print("Telemetry check for specific bad setups:")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            target_dt = target_dates[sym]
            matches = ledger[ledger['date_str'] == target_dt]
            if matches.empty:
                print(f"Warning: Could not find date {target_dt} for {sym}")
                continue
            
            row = matches.iloc[0]
            print(f"\n--- {sym} on {target_dt} ---")
            for col in ['close', 'cts_slope', 'cts_accel', 'psz_v', 'fas', 'range_pos_10', 'range_pos_22', 'range_pos_63', 'range_pos_252', 'pdd_30', 'base_tightness']:
                val = row.get(col, np.nan)
                print(f"  {col:<15}: {val}")
        except Exception as e:
            print(f"Error checking {sym}: {e}")

if __name__ == "__main__":
    main()
