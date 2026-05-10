"""
Extracts feature vectors at each Oracle-labeled trough and calculates the PnL to the next Oracle-labeled peak
using Universal Cross inflection as the entry point.
Produces a CSV dataset for training the ML Guard model.
"""

import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols
from datetime import datetime, timedelta

# Columns to exclude from the ML features (leakage or non-numeric metadata)
EXCLUDE_COLS = [
    "date", "symbol", "regime", "gradient_shape", 
    "oracle_trough", "oracle_peak", "oracle_smooth",
    "entry_signal", "entry_reason", "exit_signal", "exit_reason", "cooldown"
]

def main():
    watchlist = "NIFTY 100"
    print(f"Fetching symbols for {watchlist}...")
    symbols = get_watchlist_symbols(watchlist)
    
    cutoff_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    print(f"Filtering out events after {cutoff_date} to avoid edge effects...")

    all_features = []
    
    for i, sym in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] Processing {sym}...")
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            df = result.ledger
            
            if df.empty or 'oracle_trough' not in df.columns:
                continue
                
            # Filter ledger to before cutoff_date
            df_filtered = df[df["date"].dt.strftime("%Y-%m-%d") <= cutoff_date]
            
            # Find indices of all troughs and peaks
            trough_indices = df_filtered[df_filtered['oracle_trough'] == 1.0].index.tolist()
            peak_indices = df_filtered[df_filtered['oracle_peak'] == 1.0].index.tolist()
            
            # Find all Universal Cross inflection events (PRT component)
            prev_prt_slope = df_filtered['prt_slope'].shift(1)
            cross_mask = (df_filtered['prt_slope'] > 0) & (prev_prt_slope <= 0)
            cross_indices = df_filtered[cross_mask].index.tolist()
            
            for t_idx in trough_indices:
                # Find the next peak
                future_peaks = [p for p in peak_indices if p > t_idx]
                if not future_peaks:
                    continue
                    
                p_idx = future_peaks[0]
                
                # Find the FIRST cross event AFTER the trough and BEFORE the peak
                valid_crosses = [c for c in cross_indices if t_idx < c < p_idx]
                if not valid_crosses:
                    continue
                
                c_idx = valid_crosses[0]
                
                # Calculate PnL from the CROSS to the PEAK
                entry_price = df.loc[c_idx, 'close']
                exit_price = df.loc[p_idx, 'close']
                
                if pd.isna(entry_price) or pd.isna(exit_price) or entry_price == 0:
                    continue
                    
                pnl_pct = ((exit_price / entry_price) - 1.0) * 100.0
                
                # Label: 1 if PnL >= 4%, 0 otherwise
                label = 1 if pnl_pct >= 4.0 else 0
                
                # Extract features for the CROSS bar
                row_data = df.loc[c_idx].copy()
                
                # Create a feature dict
                feature_row = {
                    "symbol": sym,
                    "date": str(row_data["date"])[:10],
                    "pnl_to_peak": round(pnl_pct, 2),
                    "label": label,
                    "peak_date": str(df.loc[p_idx, "date"])[:10],
                }
                
                # Add all numeric columns as features
                for col in df.columns:
                    if col not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(df[col]):
                        feature_row[col] = row_data[col]
                        
                all_features.append(feature_row)
                
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    # Compile dataset
    if all_features:
        out_df = pd.DataFrame(all_features)
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "oracle_ml_dataset.csv"
        out_df.to_csv(out_path, index=False)
        
        print(f"\nExtraction complete! Dataset saved to {out_path}")
        print(f"Total labeled events: {len(out_df)}")
        print(f"Class Balance - Good Setups (1): {out_df['label'].sum()}, Bad Setups (0): {len(out_df) - out_df['label'].sum()}")
    else:
        print("\nNo labeled events found. Make sure Oracle Labeller has run successfully.")

if __name__ == "__main__":
    main()
