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
from scripts.walk_forward import get_watchlist_symbols

def main():
    watchlist = "NIFTY 50"
    start_date_limit = pd.to_datetime("2025-12-01")
    
    symbols = get_watchlist_symbols(watchlist)
    signal = SignalFactory.get_signal("savgol_cts")
    
    trough_features_t0 = []
    trough_features_t1 = []
    
    print("Gathering telemetry for all missed troughs...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
        except Exception as e:
            continue
            
        trough_indices = df[(df['date_dt'] >= start_date_limit) & (df['oracle_trough'] == 1.0)].index.tolist()
        
        for t_idx in trough_indices:
            # Check if it was missed
            start_check = max(0, t_idx - 5)
            end_check = min(len(df) - 1, t_idx + 5)
            fired = df.loc[start_check:end_check, 'entry_signal'].sum() > 0
            
            if not fired:
                # Add T+0 features
                row_t0 = df.loc[t_idx]
                trough_features_t0.append({
                    "symbol": sym,
                    "date": row_t0['date_dt'].strftime('%Y-%m-%d'),
                    "cts": row_t0.get("cts", np.nan),
                    "cts_slope": row_t0.get("cts_slope", np.nan),
                    "cts_accel": row_t0.get("cts_accel", np.nan),
                    "fas": row_t0.get("fas", np.nan),
                    "psz_v": row_t0.get("psz_v", np.nan),
                    "price_slope_z": row_t0.get("price_slope_z", np.nan),
                    "cwc": row_t0.get("cwc", np.nan),
                    "cwc_slope": row_t0.get("cwc_slope", np.nan),
                    "pdd_30": row_t0.get("pdd_30", np.nan),
                    "pdd_120": row_t0.get("pdd_120", np.nan),
                    "range_pos_10": row_t0.get("range_pos_10", np.nan),
                    "range_pos_22": row_t0.get("range_pos_22", np.nan),
                    "range_pos_63": row_t0.get("range_pos_63", np.nan),
                    "base_tightness": row_t0.get("base_tightness", np.nan)
                })
                
                # Add T+1 features
                if t_idx + 1 < len(df):
                    row_t1 = df.loc[t_idx + 1]
                    trough_features_t1.append({
                        "symbol": sym,
                        "date": row_t1['date_dt'].strftime('%Y-%m-%d'),
                        "cts": row_t1.get("cts", np.nan),
                        "cts_slope": row_t1.get("cts_slope", np.nan),
                        "cts_accel": row_t1.get("cts_accel", np.nan),
                        "fas": row_t1.get("fas", np.nan),
                        "psz_v": row_t1.get("psz_v", np.nan),
                        "price_slope_z": row_t1.get("price_slope_z", np.nan),
                        "cwc": row_t1.get("cwc", np.nan),
                        "cwc_slope": row_t1.get("cwc_slope", np.nan),
                        "pdd_30": row_t1.get("pdd_30", np.nan),
                        "pdd_120": row_t1.get("pdd_120", np.nan),
                        "range_pos_10": row_t1.get("range_pos_10", np.nan),
                        "range_pos_22": row_t1.get("range_pos_22", np.nan),
                        "range_pos_63": row_t1.get("range_pos_63", np.nan),
                        "base_tightness": row_t1.get("base_tightness", np.nan)
                    })

    df_t0 = pd.DataFrame(trough_features_t0)
    df_t1 = pd.DataFrame(trough_features_t1)
    
    print(f"Total Missed Troughs Analyzed: {len(df_t0)}")
    
    features = ["cts", "cts_slope", "cts_accel", "fas", "psz_v", "price_slope_z", "cwc", "cwc_slope", "pdd_30", "pdd_120", "range_pos_10", "range_pos_22", "range_pos_63", "base_tightness"]
    
    summary_rows = []
    for feat in features:
        summary_rows.append({
            "Feature": feat,
            "T0 Mean": f"{df_t0[feat].mean():.4f}",
            "T0 Median": f"{df_t0[feat].median():.4f}",
            "T0 Std": f"{df_t0[feat].std():.4f}",
            "T1 Mean": f"{df_t1[feat].mean():.4f}",
            "T1 Median": f"{df_t1[feat].median():.4f}",
            "T1 Std": f"{df_t1[feat].std():.4f}",
        })
        
    print("\n--- STATISTICAL FEATURE SIGNATURE OF MISSED TROUGHS ---")
    print(tabulate(summary_rows, headers='keys', tablefmt='grid'))

if __name__ == "__main__":
    main()
