import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

def process_file(csv_path, output_csv, date_col):
    df = pd.read_csv(csv_path)
    # Filter for missed setups
    missed = df[df['is_missed'] == True].copy()
    
    # Filter for causal PnL > 5%
    high_pnl = missed[missed['pnl_causal'] > 5.0].copy()
    
    # Format and save
    high_pnl.to_csv(output_csv, index=False)
    
    return missed, high_pnl

def main():
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    
    trends_csv = "scratch/trends_analysis.csv"
    troughs_csv = "scratch/troughs_analysis.csv"
    
    out_trends = "output/missed_trends_extract.csv"
    out_troughs = "output/missed_troughs_extract.csv"
    
    missed_tr, high_tr = process_file(trends_csv, out_trends, "target_date")
    missed_trg, high_trg = process_file(troughs_csv, out_troughs, "trough_date")
    
    print(f"--- TRENDS.TXT ANALYSIS ---")
    print(f"Total Missed: {len(missed_tr)}")
    print(f"Missed with PnL > 5%: {len(high_tr)}")
    print(f"Saved extract to {out_trends}")
    
    avg_pnl_causal_tr = high_tr['pnl_causal'].mean()
    avg_pnl_oracle_tr = high_tr['pnl_oracle'].mean()
    avg_dur_tr = high_tr['duration_causal'].mean()
    print(f"Causal Avg PnL: {avg_pnl_causal_tr:.2f}%")
    print(f"Oracle Avg PnL: {avg_pnl_oracle_tr:.2f}%")
    print(f"Causal Avg Duration: {avg_dur_tr:.1f} bars")
    print(tabulate(high_tr[["symbol", "target_date", "pnl_causal", "exit_reason_causal", "pnl_oracle"]].sort_values(by="pnl_causal", ascending=False), headers='keys', tablefmt='psql', showindex=False))
    
    print(f"\n--- ORACLE TROUGHS ANALYSIS ---")
    print(f"Total Missed: {len(missed_trg)}")
    print(f"Missed with PnL > 5%: {len(high_trg)}")
    print(f"Saved extract to {out_troughs}")
    
    avg_pnl_causal_trg = high_trg['pnl_causal'].mean()
    avg_pnl_oracle_trg = high_trg['pnl_oracle'].mean()
    avg_dur_trg = high_trg['duration_causal'].mean()
    print(f"Causal Avg PnL: {avg_pnl_causal_trg:.2f}%")
    print(f"Oracle Avg PnL: {avg_pnl_oracle_trg:.2f}%")
    print(f"Causal Avg Duration: {avg_dur_trg:.1f} bars")
    print(tabulate(high_trg[["symbol", "trough_date", "pnl_causal", "exit_reason_causal", "pnl_oracle"]].sort_values(by="pnl_causal", ascending=False).head(30), headers='keys', tablefmt='psql', showindex=False))

if __name__ == "__main__":
    main()
