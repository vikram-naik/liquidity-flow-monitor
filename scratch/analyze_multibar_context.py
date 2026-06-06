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

def get_watchlist_symbols(name="NIFTY 50"):
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?) ORDER BY display_order",
        (name,)
    ).fetchall()]
    conn.close()
    return symbols

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    print(f"Loaded {len(symbols)} symbols from NIFTY 50.")
    
    start_date = pd.to_datetime("2019-03-01")
    end_date = pd.to_datetime("2026-04-30")
    
    all_data = []
    
    print("Running engine and constructing multi-bar contextual features...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger.copy()
            df["symbol"] = sym
            df["date_dt"] = pd.to_datetime(df["date"])
            
            # --- Construct Multi-Bar Contextual Features ---
            # 1. Price Deceleration: current price slope z-score is higher than 3 bars ago (turning up)
            df["price_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
            
            # 2. CTS Bottoming & Surge: CTS has risen from its 5-bar low
            df["cts_min_5b"] = df["cts"].rolling(5).min()
            df["cts_surge_from_min"] = df["cts"] - df["cts_min_5b"]
            
            # 3. PDD Compression: price divergence distance is narrowing (reverting)
            df["pdd_30_compression_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
            
            # 4. Range Position Persistence: how many of the last 5 bars were in the lower 30% of the 22-day range
            df["range_pos_22_low_count"] = (df["range_pos_22"] < 0.30).rolling(5).sum()
            
            # 5. Price Trend deceleration: price close compared to 5 bars ago versus 10 bars ago
            # measures if the fall is slowing down
            df["close_change_5b"] = df["close"] / df["close"].shift(5) - 1.0
            df["close_change_5to10b"] = df["close"].shift(5) / df["close"].shift(10) - 1.0
            df["price_momentum_change"] = df["close_change_5b"] - df["close_change_5to10b"]
            
            # 6. Delivery Climax: maximum delivery volume shock over the last 3 bars
            df["max_dv_shock_3b"] = df["dv_shock"].rolling(3).max()
            
            # Filter range
            mask = (df["date_dt"] >= start_date) & (df["date_dt"] <= end_date)
            df_filtered = df.loc[mask].copy()
            if df_filtered.empty:
                continue
                
            all_data.append(df_filtered)
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    if not all_data:
        print("No data loaded.")
        return
        
    # Process each symbol to find the EOD-lag trade details
    processed_dfs = []
    for df in all_data:
        sym_df = df.sort_values("date_dt").copy().reset_index(drop=True)
        sym_df["class_label"] = 0  # 0 = background, 1 = successful trough
        sym_df["setup_pnl"] = np.nan
        sym_df["peak_idx"] = -1
        
        trough_indices = sym_df[sym_df["oracle_trough"] == 1.0].index.tolist()
        peak_indices = sym_df[sym_df["oracle_peak"] == 1.0].index.tolist()
        
        for t_idx in trough_indices:
            next_peaks = [p for p in peak_indices if p > t_idx]
            if not next_peaks:
                continue
            p_idx = next_peaks[0]
            
            if t_idx + 1 >= len(sym_df) or p_idx + 1 >= len(sym_df):
                continue
                
            close_entry = sym_df.loc[t_idx + 1, "close"]
            close_exit = sym_df.loc[p_idx + 1, "close"]
            pnl_pct = (close_exit / close_entry - 1.0) * 100.0
            
            sym_df.loc[t_idx, "setup_pnl"] = pnl_pct
            sym_df.loc[t_idx, "peak_idx"] = p_idx
            
            if pnl_pct > 5.0:
                sym_df.loc[t_idx, "class_label"] = 1
                
        processed_dfs.append(sym_df)
        
    df_all = pd.concat(processed_dfs, ignore_index=True)
    
    # Train / Test split
    train_mask = df_all["date_dt"] < pd.to_datetime("2025-01-01")
    df_train = df_all.loc[train_mask].copy()
    df_test = df_all.loc[~train_mask].copy()
    
    # Analyze contextual features Z-score difference from background
    contextual_feats = [
        "price_decel_3b", "cts_surge_from_min", "pdd_30_compression_3b",
        "range_pos_22_low_count", "price_momentum_change", "max_dv_shock_3b",
        "range_pos_22", "range_pos_63", "pdd_30", "cts", "psz_v", "cts_accel"
    ]
    
    stats_rows = []
    for feat in contextual_feats:
        feat_data = df_train[[feat, "class_label"]].dropna()
        success = feat_data[feat_data["class_label"] == 1][feat]
        background = feat_data[feat_data["class_label"] == 0][feat]
        
        if len(success) == 0:
            continue
            
        mean_s, std_s = success.mean(), success.std()
        mean_b, std_b = background.mean(), background.std()
        
        n_s = len(success)
        n_b = len(background)
        denom = np.sqrt((std_s**2)/n_s + (std_b**2)/n_b)
        z_score = (mean_s - mean_b) / denom if denom > 0 else 0
        
        stats_rows.append({
            "Feature": feat,
            "Success Mean": mean_s,
            "Success Std": std_s,
            "Background Mean": mean_b,
            "Background Std": std_b,
            "Z-Score Diff": z_score,
        })
        
    df_stats = pd.DataFrame(stats_rows).sort_values("Z-Score Diff", key=abs, ascending=False)
    print("\nFeature statistics (sorted by Z-score difference from background):")
    print(tabulate(df_stats, headers="keys", tablefmt="grid", floatfmt=".4f"))

if __name__ == "__main__":
    main()
