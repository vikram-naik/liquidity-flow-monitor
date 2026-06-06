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
from scripts.walk_forward import get_watchlist_symbols

def main():
    watchlist = "NIFTY 50"
    start_date_limit = pd.to_datetime("2019-01-01")  # Run across all history
    
    symbols = get_watchlist_symbols(watchlist)
    
    trough_features = []
    background_features = []
    
    print("Gathering new candidate features for NIFTY 50 across ALL history (since 2019-01-01)...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
            
            # --- Define New Candidate Features ---
            high_low_spread = df["high"] - df["low"]
            df["lwr"] = np.where(high_low_spread > 0, (df["close"] - df["low"]) / high_low_spread, 0.5)
            df["iac"] = df["dv_shock"] * df["lwr"]
            df["rdi"] = df["rdv"] * (2.0 * df["lwr"] - 1.0)
            df["pddm_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
            df["das"] = np.where(df["atr_20"] > 0, (df["close"] - df["cwvap"]) / df["atr_20"], 0.0)
            df["psz_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
            
            # Filter range
            df_filtered = df[df['date_dt'] >= start_date_limit].copy()
            if df_filtered.empty:
                continue
                
            # Separate troughs and background
            troughs = df_filtered[df_filtered['oracle_trough'] == 1.0]
            
            # Check if troughs were missed
            for idx, row in troughs.iterrows():
                start_check = max(0, idx - 5)
                end_check = min(len(df) - 1, idx + 5)
                fired = df.loc[start_check:end_check, 'entry_signal'].sum() > 0
                
                feat_dict = {
                    "symbol": sym,
                    "date": row['date_dt'].strftime('%Y-%m-%d'),
                    "lwr": row["lwr"],
                    "iac": row["iac"],
                    "rdi": row["rdi"],
                    "pddm_3b": row["pddm_3b"],
                    "das": row["das"],
                    "psz_decel_3b": row["psz_decel_3b"],
                    "range_pos_22": row["range_pos_22"],
                    "pdd_30": row["pdd_30"],
                    "dv_shock": row["dv_shock"]
                }
                
                if not fired:
                    trough_features.append(feat_dict)
                    
            # Background
            bg = df_filtered[df_filtered['oracle_trough'] != 1.0]
            for idx, row in bg.iterrows():
                background_features.append({
                    "lwr": row["lwr"],
                    "iac": row["iac"],
                    "rdi": row["rdi"],
                    "pddm_3b": row["pddm_3b"],
                    "das": row["das"],
                    "psz_decel_3b": row["psz_decel_3b"],
                    "range_pos_22": row["range_pos_22"],
                    "pdd_30": row["pdd_30"],
                    "dv_shock": row["dv_shock"]
                })
        except Exception as e:
            continue

    df_t = pd.DataFrame(trough_features)
    df_b = pd.DataFrame(background_features)
    
    print(f"\nTotal Missed Troughs: {len(df_t)}")
    print(f"Total Background Days: {len(df_b)}")
    
    new_feats = ["lwr", "iac", "rdi", "pddm_3b", "das", "psz_decel_3b", "range_pos_22", "pdd_30", "dv_shock"]
    
    summary_rows = []
    for feat in new_feats:
        mean_t = df_t[feat].mean()
        std_t = df_t[feat].std()
        median_t = df_t[feat].median()
        
        mean_b = df_b[feat].mean()
        std_b = df_b[feat].std()
        
        norm_diff = (mean_t - mean_b) / std_b if std_b > 0 else 0.0
        
        summary_rows.append({
            "Feature": feat,
            "Troughs Mean": f"{mean_t:.4f}",
            "Troughs Median": f"{median_t:.4f}",
            "Bg Mean": f"{mean_b:.4f}",
            "Bg Std": f"{std_b:.4f}",
            "Normalized Diff": f"{norm_diff:+.4f}"
        })
        
    print("\n--- STATISTICAL SIGNIFICANCE OF NEW CANDIDATE FEATURES (SINCE 2019) ---")
    print(tabulate(summary_rows, headers='keys', tablefmt='grid'))

if __name__ == "__main__":
    main()
