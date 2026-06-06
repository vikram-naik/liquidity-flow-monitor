import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    
    target_setups = [
        ("SBIN", pd.to_datetime("2025-12-04")),
        ("TRENT", pd.to_datetime("2026-03-19")),
        ("AXISBANK", pd.to_datetime("2026-03-19")),
        ("DRREDDY", pd.to_datetime("2026-01-19"))
    ]
    
    for sym, dt in target_setups:
        print(f"\n==============================================================")
        print(f"DIAGNOSTIC FOR {sym} AROUND {dt.strftime('%Y-%m-%d')}")
        print(f"==============================================================")
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            continue
            
        match_df = df[df['date_dt'] <= dt]
        if match_df.empty:
            continue
        t_idx = match_df.index[-1]
        
        # Look at 0 to +4 bars after the trough
        for k in range(0, 5):
            idx = t_idx + k
            if idx >= len(df):
                continue
            row = df.loc[idx].to_dict()
            prev_row = df.loc[idx - 1].to_dict() if idx > 0 else row
            
            passed, intensity, meta = signal.check_entry(row, prev_row, entry_cfg, df.to_dict('records'), idx)
            date_str = row['date_dt'].strftime('%Y-%m-%d')
            close = row.get('close', 0.0)
            print(f"\nDay T+{k} ({date_str}) | Close: {close:.2f} | passed: {passed}")
            
            # Print features of interest
            print(f"  Features: CTS={row.get('cts', 0):.4f}, CTS_Slope={row.get('cts_slope', 0):.4f}, CTS_Accel={row.get('cts_accel', 0):.4f}, FAS={row.get('fas', 0):.4f}, PSZ_V={row.get('psz_v', 0):.4f}, CWC={row.get('cwc', 0):.4f}, PDD_30={row.get('pdd_30', 0):.4f}, RP_63={row.get('range_pos_63', 0):.4f}")
            
            # Print path reasons
            reasons = meta.get("reason", "").split(" | ")
            for r in reasons:
                if "disabled" not in r:
                    print(f"    {r}")

if __name__ == "__main__":
    main()
