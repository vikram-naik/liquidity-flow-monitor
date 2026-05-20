import argparse
import pandas as pd
from pathlib import Path
from tabulate import tabulate

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--dataset", default="output/ml/dataset_dense_nifty_50_20260516.csv")
    args = parser.parse_args()

    df = pd.read_csv(args.dataset)
    df["date"] = pd.to_datetime(df["date"])
    
    target_date = pd.to_datetime(args.date)
    row = df[(df["symbol"] == args.symbol) & (df["date"] == target_date)]
    
    if row.empty:
        print(f"No row found for {args.symbol} on {args.date}")
        return

    # Expanded feature list focusing on divergences and geometry
    key_cols = [
        "symbol", "date", "pnl_pct",
        "prt", "fas", "cts", 
        "accum_div", "distrib_div", "coherence", "coherence_raw",
        "mcs_composite", "mcs_composite_slope", 
        "price_slope_z", "rdv_slope_z", "cwc", "cwc_slope"
    ]
    
    present_cols = [c for c in key_cols if c in df.columns]
    print(tabulate(row[present_cols].T, headers=["Feature", "Value"], tablefmt="psql"))

if __name__ == "__main__":
    main()
