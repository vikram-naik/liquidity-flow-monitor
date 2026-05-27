import sys
import numpy as np
import pandas as pd
from pathlib import Path

# Add root folder to path
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scratch.advanced_grid_search import load_enriched_dataset

def main():
    df = load_enriched_dataset()
    if df.empty:
        return
        
    # Apply entry filters (Config A - Surgical)
    passed = df[
        (df["cwc"] >= 0.0) &
        (df["psz"] >= -0.25) &
        (df["range_width_252"] >= 20.0) &
        (df["rp_252"] <= 0.70)
    ].copy()
    
    passed["final_pnl"] = passed["pnl"].apply(lambda p: -15.0 if p <= -15.0 else p)
    
    # Split into TRAIN and TEST based on signal_date
    train_df = passed[passed["signal_date"] <= "2023-12-31"]
    test_df = passed[passed["signal_date"] >= "2024-01-01"]
    
    print("\n=======================================================")
    print("  CONFIG A: TRAIN VS TEST BIFURCATION")
    print("=======================================================")
    
    def print_metrics(label, subset):
        total = len(subset)
        if total == 0:
            print(f"{label}: No trades")
            return
            
        win_trades = subset[subset["final_pnl"] > 0]
        loss_trades = subset[subset["final_pnl"] <= 0]
        duds = subset[subset["is_dud"]]
        high_yield = win_trades[win_trades["final_pnl"] >= 5.0]
        
        wr = len(win_trades) / total * 100
        avg_pnl = subset["final_pnl"].mean()
        
        profits = win_trades["final_pnl"].sum()
        losses = abs(loss_trades["final_pnl"].sum())
        pf = profits / losses if losses > 0 else float('inf')
        
        print(f"--- {label} Period ---")
        print(f"  Total Trades        : {total}")
        print(f"  Winners (PnL > 0%)  : {len(win_trades)}  (Win Rate: {wr:.1f}%)")
        print(f"  High-Yield (>= 5%)  : {len(high_yield)}")
        print(f"  Duds Remaining      : {len(duds)}")
        print(f"  Average P&L%        : {avg_pnl:.2f}%")
        print(f"  Profit Factor       : {pf:.2f}")
        print()
        
    print_metrics("TRAIN (2019-2023)", train_df)
    print_metrics("TEST (2024-2026)", test_df)

if __name__ == "__main__":
    main()
