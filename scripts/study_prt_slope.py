import pandas as pd
import numpy as np
import re
from datetime import datetime

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def parse_dump(file_path):
    trades = []
    with open(file_path, "r") as f:
        for line in f:
            if "|" in line and not line.lstrip().startswith("#") and not line.startswith("---"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    try:
                        symbol = parts[1]
                        sig_date = parts[2]
                        pnl = float(parts[3])
                        trades.append({"symbol": symbol, "sig_date": sig_date, "pnl": pnl})
                    except ValueError:
                        pass
    return trades

def main():
    dump_file = "output/fas_buy_cross_dump.txt"
    trades = parse_dump(dump_file)
    print(f"Parsed {len(trades)} trades from {dump_file}")

    results = []
    for trade in trades:
        symbol = trade["symbol"]
        sig_date = trade["sig_date"]
        pnl = trade["pnl"]

        try:
            # Initialize engine and run to get ledger
            engine = DivergenceEngine(symbol)
            ledger = engine.run()
            
            # Find the row for the signal date
            df_ledger = ledger.ledger
            row_idx_matches = df_ledger.index[df_ledger['date'].dt.strftime('%Y-%m-%d') == sig_date]
            if len(row_idx_matches) == 0:
                print(f"Skipping {symbol} on {sig_date}: date not found in ledger")
                continue
                
            idx = row_idx_matches[0]
            row = df_ledger.iloc[idx].to_dict()
            
            prt_slope = row.get("prt_slope", np.nan)
            
            results.append({
                "symbol": symbol,
                "sig_date": sig_date,
                "pnl": pnl,
                "prt_slope": prt_slope
            })
        except Exception as e:
            print(f"Error processing {symbol} on {sig_date}: {e}")

    df = pd.DataFrame(results)
    df = df.dropna(subset=['prt_slope'])
    
    print("\n--- Empirical Study: PRT Slope vs PnL (FAS Buy Cross) ---")
    print(f"Total valid trades analyzed: {len(df)}")
    
    # Use qcut to divide the data into 5 quantiles based on the actual distribution
    df['prt_bin'] = pd.qcut(df['prt_slope'], q=5, precision=3)
    
    summary = df.groupby('prt_bin', observed=False).agg(
        trades=('pnl', 'count'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100 if len(x) > 0 else 0),
        avg_pnl=('pnl', 'mean')
    ).round(2)
    
    print("\nDistribution by PRT Slope Bin:")
    print(summary.to_string())

if __name__ == "__main__":
    main()