import sys
import os
import sqlite3
import pandas as pd
import numpy as np
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
    
    # We will pick a few major missed setups to inspect in detail:
    # 1. SBIN on 2025-12-04
    # 2. TRENT on 2026-03-19
    # 3. AXISBANK on 2026-03-19
    # 4. DRREDDY on 2026-01-19
    target_setups = [
        ("SBIN", pd.to_datetime("2025-12-04")),
        ("TRENT", pd.to_datetime("2026-03-19")),
        ("AXISBANK", pd.to_datetime("2026-03-19")),
        ("DRREDDY", pd.to_datetime("2026-01-19"))
    ]
    
    for sym, dt in target_setups:
        print(f"\n======================================================================")
        print(f"TELEMETRY FOR {sym} AROUND {dt.strftime('%Y-%m-%d')}")
        print(f"======================================================================")
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
        
        # Display window [-2, +6] around t_idx
        start_idx = max(0, t_idx - 2)
        end_idx = min(len(df) - 1, t_idx + 6)
        
        telemetry_rows = []
        for idx in range(start_idx, end_idx + 1):
            row = df.loc[idx].to_dict()
            prev_row = df.loc[idx - 1].to_dict() if idx > 0 else row
            
            passed, intensity, meta = signal.check_entry(row, prev_row, entry_cfg, df.to_dict('records'), idx)
            
            # Extract main features
            telemetry_rows.append({
                "T+k": idx - t_idx,
                "Date": row['date_dt'].strftime('%Y-%m-%d'),
                "Close": f"{row.get('close', 0):.2f}",
                "CTS": f"{row.get('cts', 0):.4f}",
                "CTS_Slope": f"{row.get('cts_slope', 0):.4f}",
                "CTS_Accel": f"{row.get('cts_accel', 0):.4f}",
                "FAS": f"{row.get('fas', 0):.4f}",
                "PSZ_V": f"{row.get('psz_v', 0):.4f}",
                "CWC": f"{row.get('cwc', 0):.4f}",
                "PDD_30": f"{row.get('pdd_30', 0):.4f}",
                "RP_63": f"{row.get('range_pos_63', 0):.4f}",
                "BT": f"{row.get('base_tightness', 0):.4f}",
                "Signal": "BUY" if passed else "NONE",
                "Rejection Reason": meta.get("reason", "")[:60] + "..." if not passed else ""
            })
            
        print(tabulate(telemetry_rows, headers='keys', tablefmt='grid'))

if __name__ == "__main__":
    main()
