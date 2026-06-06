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
    
    trough_features = []
    
    print("Gathering contextual features for all missed troughs...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
            
            # Construct contextual features
            df["price_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
            df["cts_min_5b"] = df["cts"].rolling(5).min()
            df["cts_surge_from_min"] = df["cts"] - df["cts_min_5b"]
            df["pdd_30_compression_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
            df["range_pos_22_low_count"] = (df["range_pos_22"] < 0.30).rolling(5).sum()
            df["close_change_5b"] = df["close"] / df["close"].shift(5) - 1.0
            df["close_change_5to10b"] = df["close"].shift(5) / df["close"].shift(10) - 1.0
            df["price_momentum_change"] = df["close_change_5b"] - df["close_change_5to10b"]
            if "dv_shock" in df.columns:
                df["max_dv_shock_3b"] = df["dv_shock"].rolling(3).max()
            else:
                df["max_dv_shock_3b"] = np.nan
        except Exception as e:
            continue
            
        trough_indices = df[(df['date_dt'] >= start_date_limit) & (df['oracle_trough'] == 1.0)].index.tolist()
        
        for t_idx in trough_indices:
            # Check if it was missed
            start_check = max(0, t_idx - 5)
            end_check = min(len(df) - 1, t_idx + 5)
            fired = df.loc[start_check:end_check, 'entry_signal'].sum() > 0
            
            if not fired:
                row_t0 = df.loc[t_idx]
                trough_features.append({
                    "symbol": sym,
                    "date": row_t0['date_dt'].strftime('%Y-%m-%d'),
                    "price_decel_3b": row_t0.get("price_decel_3b", np.nan),
                    "cts_surge_from_min": row_t0.get("cts_surge_from_min", np.nan),
                    "pdd_30_compression_3b": row_t0.get("pdd_30_compression_3b", np.nan),
                    "range_pos_22_low_count": row_t0.get("range_pos_22_low_count", np.nan),
                    "price_momentum_change": row_t0.get("price_momentum_change", np.nan),
                    "max_dv_shock_3b": row_t0.get("max_dv_shock_3b", np.nan),
                    "cts_accel": row_t0.get("cts_accel", np.nan),
                    "psz_v": row_t0.get("psz_v", np.nan),
                    "pdd_30": row_t0.get("pdd_30", np.nan),
                    "range_pos_22": row_t0.get("range_pos_22", np.nan)
                })

    df_t0 = pd.DataFrame(trough_features)
    print(f"Total Missed Troughs Analyzed: {len(df_t0)}")
    
    features = [
        "price_decel_3b", "cts_surge_from_min", "pdd_30_compression_3b",
        "range_pos_22_low_count", "price_momentum_change", "max_dv_shock_3b",
        "cts_accel", "psz_v", "pdd_30", "range_pos_22"
    ]
    
    summary_rows = []
    for feat in features:
        summary_rows.append({
            "Feature": feat,
            "Mean": f"{df_t0[feat].mean():.4f}",
            "Median": f"{df_t0[feat].median():.4f}",
            "Std": f"{df_t0[feat].std():.4f}",
            "Min": f"{df_t0[feat].min():.4f}",
            "Max": f"{df_t0[feat].max():.4f}",
        })
        
    print("\n--- CONTEXTUAL FEATURE SIGNATURE OF MISSED TROUGHS ---")
    print(tabulate(summary_rows, headers='keys', tablefmt='grid'))

if __name__ == "__main__":
    main()
