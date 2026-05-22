import pandas as pd
import numpy as np

def main():
    filepath = "/home/vn/python-projects/liquidity-flow-monitor/output/added_trades_details.csv"
    df = pd.read_csv(filepath)
    
    # Let's explore the trades satisfying: cwc >= 0.40 and pdd_30 < -3.5
    cond = (df["cwc"] >= 0.40) & (df["pdd_30"] < -3.5)
    subset = df[cond]
    print("\n--- TRADES ACCEPTED BY: cwc >= 0.40 AND pdd_30 < -3.5 ---")
    cols = ["symbol", "sig_date", "pnl", "rp10", "cwc", "pdd_30", "psz_v", "base_tightness"]
    print(subset[cols].to_string(index=False))
    
    # Let's also explore: cwc >= 0.40 and pdd_30 < -3.0
    cond2 = (df["cwc"] >= 0.40) & (df["pdd_30"] < -3.0)
    subset2 = df[cond2]
    print("\n--- TRADES ACCEPTED BY: cwc >= 0.40 AND pdd_30 < -3.0 ---")
    print(subset2[cols].to_string(index=False))
    
    # What about the ones we rejected? Let's print severe losses (pnl <= -5.0) and see their cwc, pdd_30, psz_v, base_tightness
    severe_losers = df[df["pnl"] <= -5.0]
    print("\n--- SEVERE LOSERS (PnL <= -5.0%) ---")
    print(severe_losers[cols].to_string(index=False))
    
    # What about the absolute best winners (pnl >= 3.0%)? Let's see their features
    winners = df[df["pnl"] >= 3.0]
    print("\n--- BEST WINNERS (PnL >= 3.0%) ---")
    print(winners[cols].to_string(index=False))

if __name__ == "__main__":
    main()
