"""
Script to measure the temporal offset between an Oracle Trough and a Universal Cross trigger (PRT inflection).
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    
    offsets = []
    prt_slope_at_trough = []
    prt_slope_at_cross = []

    print("Scanning NIFTY 50 to measure temporal offset...", flush=True)

    for i, sym in enumerate(symbols):
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger
            
            if df.empty or 'oracle_trough' not in df.columns or 'prt_slope' not in df.columns:
                continue
                
            trough_indices = df[df['oracle_trough'] == 1.0].index.tolist()
            
            # Find Universal Cross inflection events (PRT component)
            prev_prt_slope = df['prt_slope'].shift(1)
            cross_mask = (df['prt_slope'] > 0) & (prev_prt_slope <= 0)
            cross_indices = df[cross_mask].index.tolist()

            for t_idx in trough_indices:
                # Find the FIRST cross event AFTER the trough
                future_crosses = [c for c in cross_indices if c > t_idx]
                if not future_crosses:
                    continue
                    
                c_idx = future_crosses[0]
                
                # Only count it if the cross happens before the next peak
                peak_indices = df[df['oracle_peak'] == 1.0].index.tolist()
                future_peaks = [p for p in peak_indices if p > t_idx]
                
                if future_peaks and c_idx > future_peaks[0]:
                    continue # Cross happened after the move was over
                    
                offset_days = c_idx - t_idx
                offsets.append(offset_days)
                
                prt_slope_at_trough.append(df.loc[t_idx, 'prt_slope'])
                prt_slope_at_cross.append(df.loc[c_idx, 'prt_slope'])

        except Exception as e:
            pass

    if offsets:
        print(f"\nTotal Matching Events (Trough -> Cross): {len(offsets)}")
        print(f"Average Delay: {np.mean(offsets):.2f} trading days")
        print(f"Median Delay: {np.median(offsets):.0f} trading days")
        print(f"Min Delay: {np.min(offsets)} days")
        print(f"Max Delay: {np.max(offsets)} days")
        
        print(f"\nAverage PRT_Slope at Oracle Trough (Training State): {np.mean(prt_slope_at_trough):.4f}")
        print(f"Average PRT_Slope at Universal Cross inflection (Inference State): {np.mean(prt_slope_at_cross):.4f}")
    else:
        print("No events found.")

if __name__ == "__main__":
    main()
