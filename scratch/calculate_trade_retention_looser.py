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
        
    duds = df[df["is_dud"]]
    non_duds = df[~df["is_dud"]]
    
    orig_total = len(df)
    orig_duds = len(duds)
    orig_winners = len(non_duds[non_duds["pnl"] > 0])
    orig_high_yield = len(non_duds[non_duds["pnl"] >= 5.0])
    orig_non_duds = len(non_duds)
    
    # -------------------------------------------------------------
    # Config A: Surgical (rp_252_max = 0.70, rw_252_min = 20.0%)
    # -------------------------------------------------------------
    passed_a = df[
        (df["cwc"] >= 0.0) &
        (df["psz"] >= -0.25) &
        (df["range_width_252"] >= 20.0) &
        (df["rp_252"] <= 0.70)
    ].copy()
    passed_a["final_pnl"] = passed_a["pnl"].apply(lambda p: -15.0 if p <= -15.0 else p)
    
    passed_a_duds = passed_a[passed_a["is_dud"]]
    passed_a_non_duds = passed_a[~passed_a["is_dud"]]
    win_a = passed_a_non_duds[passed_a_non_duds["final_pnl"] > 0]
    hy_a = passed_a_non_duds[passed_a_non_duds["final_pnl"] >= 5.0]
    
    avg_pnl_a = passed_a["final_pnl"].mean()
    wr_a = (passed_a["final_pnl"] > 0).mean() * 100
    profits_a = passed_a[passed_a["final_pnl"] > 0]["final_pnl"].sum()
    losses_a = abs(passed_a[passed_a["final_pnl"] <= 0]["final_pnl"].sum())
    pf_a = profits_a / losses_a if losses_a > 0 else float('inf')
    
    # -------------------------------------------------------------
    # Config B: Balanced (rp_252_max = 0.75, rw_252_min = 0.0%)
    # -------------------------------------------------------------
    passed_b = df[
        (df["cwc"] >= 0.0) &
        (df["psz"] >= -0.25) &
        (df["range_width_252"] >= 0.0) &
        (df["rp_252"] <= 0.75)
    ].copy()
    passed_b["final_pnl"] = passed_b["pnl"].apply(lambda p: -15.0 if p <= -15.0 else p)
    
    passed_b_duds = passed_b[passed_b["is_dud"]]
    passed_b_non_duds = passed_b[~passed_b["is_dud"]]
    win_b = passed_b_non_duds[passed_b_non_duds["final_pnl"] > 0]
    hy_b = passed_b_non_duds[passed_b_non_duds["final_pnl"] >= 5.0]
    
    avg_pnl_b = passed_b["final_pnl"].mean()
    wr_b = (passed_b["final_pnl"] > 0).mean() * 100
    profits_b = passed_b[passed_b["final_pnl"] > 0]["final_pnl"].sum()
    losses_b = abs(passed_b[passed_b["final_pnl"] <= 0]["final_pnl"].sum())
    pf_b = profits_b / losses_b if losses_b > 0 else float('inf')
    
    print("\n=======================================================")
    print("  STRATEGY CONFIGURATION COMPARISON STUDY")
    print("=======================================================")
    print(f"{'Metric':<30} | {'Baseline':<12} | {'A: Surgical':<12} | {'B: Balanced'}")
    print("-" * 75)
    print(f"{'Total Trades Count':<30} | {orig_total:<12} | {len(passed_a):<12} | {len(passed_b)}")
    print(f"{'Average P&L per Trade':<30} | {df['pnl'].mean():>11.2f}% | {avg_pnl_a:>11.2f}% | {avg_pnl_b:>10.2f}%")
    print(f"{'Win Rate':<30} | {orig_winners/orig_total*100:>11.2f}% | {wr_a:>11.2f}% | {wr_b:>10.2f}%")
    print(f"{'Profit Factor':<30} | {orig_winners/(orig_total-orig_winners):>12.2f} | {pf_a:>12.2f} | {pf_b:>11.2f}")
    
    print("\n=======================================================")
    print("  TRADE RETENTION BREAKDOWN")
    print("=======================================================")
    print(f"{'Category':<30} | {'Original':<8} | {'Retained A':<10} | {'Retained B':<10}")
    print("-" * 70)
    print(f"{'Dud Setups (PnL <= -7.24%)':<30} | {orig_duds:<8} | {len(passed_a_duds):<10} | {len(passed_b_duds):<10}")
    print(f"{'Winning Trades (PnL > 0%)':<30} | {orig_winners:<8} | {len(win_a):<10} | {len(win_b):<10}")
    print(f"{'High-Yield Trades (PnL >= 5%)':<30} | {orig_high_yield:<8} | {len(hy_a):<10} | {len(hy_b):<10}")
    print(f"{'Total Non-Dud Trades':<30} | {orig_non_duds:<8} | {len(passed_a_non_duds):<10} | {len(passed_b_non_duds):<10}")
    
if __name__ == "__main__":
    main()
