"""
Study script to test the Live Inference capabilities of the ML Guard model.
Scans the NIFTY 100 universe over the last 60 days.
Finds PRT Slope zero-cross entries, scores them with ML, applies CTS trailing exit,
calculates EOD-lag PnL, and reports PnL distribution by ML confidence.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import joblib
from tabulate import tabulate

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols

MODEL_PATH = PROJECT_ROOT / "output" / "ml_guard_model.joblib"

def main():
    if not MODEL_PATH.exists():
        print(f"Model not found at {MODEL_PATH}. Please run train_ml_guard.py first.")
        sys.exit(1)

    print("Loading ML Guard model...")
    model_data = joblib.load(MODEL_PATH)
    clf = model_data['model']
    feature_cols = model_data['feature_cols']

    watchlist = "NIFTY 100"
    print(f"Fetching symbols for {watchlist}...")
    symbols = get_watchlist_symbols(watchlist)
    
    # We test on the 60-day period that was explicitly excluded from the training set
    end_date_dt = datetime.now()
    start_date_dt = end_date_dt - timedelta(days=60)
    start_date = start_date_dt.strftime("%Y-%m-%d")

    print(f"Scanning for PRT Slope Zero-Crosses from {start_date} to today (Out-of-Sample)...")

    results = []

    for i, sym in enumerate(symbols):
        if i % 10 == 0:
            print(f"  Processed {i}/{len(symbols)} symbols...")
            
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            df = result.ledger
            
            if df.empty or 'prt_slope' not in df.columns or 'cts' not in df.columns or 'cts_sell_threshold' not in df.columns:
                continue
                
            # We filter the mask based on dates, but we need the full dataframe context to do EOD lag
            df_test = df[df["date"].dt.strftime("%Y-%m-%d") >= start_date].copy()
            if df_test.empty:
                continue
                
            # Identify PRT Slope Zero Cross (-ve to +ve)
            prev_prt_slope = df['prt_slope'].shift(1)
            cross_mask = (df['prt_slope'] > 0) & (prev_prt_slope <= 0)
            
            # Apply mask to our test window
            cross_events = df_test[cross_mask.loc[df_test.index]]
            
            for idx, row in cross_events.iterrows():
                # INFERENCE: Ask the model
                x_dict = {col: row.get(col, 0) for col in feature_cols}
                x_df = pd.DataFrame([x_dict]).fillna(0)
                
                probs = clf.predict_proba(x_df)
                prob_good = probs[0][1] * 100.0
                
                signal_date = str(row['date'])[:10]
                signal_idx = df.index.get_loc(idx)
                
                # EOD-Lag Entry: Open of signal_idx + 1
                if signal_idx + 1 >= len(df):
                    continue # Signal on the last day, no entry yet
                    
                entry_row = df.iloc[signal_idx + 1]
                entry_price = entry_row['open']
                if pd.isna(entry_price) or entry_price == 0:
                    entry_price = entry_row['close']
                entry_date = str(entry_row['date'])[:10]
                
                # CTS Trailing Exit Check
                exit_price = None
                exit_date = None
                exit_reason = None
                
                for j in range(signal_idx + 1, len(df) - 1):
                    curr_row = df.iloc[j]
                    prev_row = df.iloc[j - 1]
                    
                    curr_cts = curr_row.get('cts', 0)
                    curr_cts_st = curr_row.get('cts_sell_threshold', 0)
                    prev_cts = prev_row.get('cts', 0)
                    prev_cts_st = prev_row.get('cts_sell_threshold', 0)
                    
                    # Exit Signal: CTS crosses below CTS Sell Threshold
                    if prev_cts >= prev_cts_st and curr_cts < curr_cts_st:
                        # EOD-Lag Exit: Open of j + 1
                        exit_row = df.iloc[j + 1]
                        exit_price = exit_row['open']
                        if pd.isna(exit_price) or exit_price == 0:
                            exit_price = exit_row['close']
                        exit_date = str(exit_row['date'])[:10]
                        exit_reason = "CTS Trailing Exit"
                        break
                
                # If no exit triggered, mark as unrealized
                if exit_price is None:
                    last_row = df.iloc[-1]
                    exit_price = last_row['close']
                    exit_date = str(last_row['date'])[:10]
                    exit_reason = "Unrealized"
                
                pnl_pct = ((exit_price / entry_price) - 1.0) * 100.0
                
                results.append({
                    "Symbol": sym,
                    "Signal Date": signal_date,
                    "Entry Date": entry_date,
                    "Exit Date": exit_date,
                    "ML Score (%)": prob_good,
                    "PnL (%)": pnl_pct,
                    "Status": exit_reason
                })

        except Exception as e:
            pass

    if not results:
        print("No trades triggered in the test period.")
        return

    # Convert results to DataFrame for distribution analysis
    res_df = pd.DataFrame(results)
    
    # Calculate aggregate metrics by ML Score
    dist_stats = []
    
    # Group by the distinct probability scores the decision tree produced
    for score, group in res_df.groupby("ML Score (%)"):
        trade_count = len(group)
        win_count = len(group[group["PnL (%)"] > 0])
        win_rate = (win_count / trade_count) * 100.0
        avg_pnl = group["PnL (%)"].mean()
        total_pnl = group["PnL (%)"].sum()
        
        dist_stats.append({
            "ML Score (%)": round(score, 1),
            "Trades": trade_count,
            "Win Rate": f"{win_rate:.1f}%",
            "Avg PnL": f"{avg_pnl:.2f}%",
            "Total PnL": f"{total_pnl:.2f}%"
        })
        
    dist_stats.sort(key=lambda x: x["ML Score (%)"], reverse=True)

    print("\n" + "="*80)
    print("   PNL DISTRIBUTION BY ML CONFIDENCE SCORE (OUT-OF-SAMPLE) ")
    print("="*80)
    print(tabulate(dist_stats, headers="keys", tablefmt="grid"))
    
    print(f"\nTotal Trades Simulated: {len(res_df)}")
    print(f"Overall Avg PnL: {res_df['PnL (%)'].mean():.2f}%")

if __name__ == "__main__":
    main()
