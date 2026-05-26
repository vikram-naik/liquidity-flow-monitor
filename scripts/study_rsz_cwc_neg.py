#!/usr/bin/env python3
"""
Study script to analyze occurrences across NIFTY 50 stocks where
RSZ Velocity (rsz_v) turns negative and Cash Coherence Slope (cwc_slope) is negative.
For each occurrence, records the range positions (range_pos_10, range_pos_22, range_pos_63, range_pos_252).
Saves a detailed report to output/nifty50_rsz_v_cwc_slope_study.md
and raw results to output/nifty50_rsz_v_cwc_slope_study.csv.
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.walk_forward import get_watchlist_symbols
from src.divergence_engine.engine import DivergenceEngine

def main():
    parser = argparse.ArgumentParser(description="Study RSZ_v turning negative and cwc_slope negative")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist to analyze (default: NIFTY 50)")
    parser.add_argument("--start-date", default=None, help="Start date in YYYY-MM-DD format (default: None, full history)")
    args = parser.parse_args()

    watchlist_name = args.watchlist
    print(f"Starting RSZ_v & CWC_slope Negativity Study for {watchlist_name}...")
    
    try:
        symbols = get_watchlist_symbols(watchlist_name)
    except Exception as e:
        print(f"Error: Watchlist '{watchlist_name}' could not be loaded. Details: {e}")
        sys.exit(1)

    print(f"Loaded {len(symbols)} symbols from watchlist '{watchlist_name}'.")
    
    study_records = []
    
    for idx, sym in enumerate(symbols, 1):
        print(f"[{idx}/{len(symbols)}] Processing {sym}...", flush=True)
        try:
            # Initialize DivergenceEngine (NEVER pass start_date or end_date during init, per GEMINI.md)
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger
            
            if df is None or df.empty:
                print(f"  Warning: Empty ledger for {sym}")
                continue
            
            # Ensure required columns are present
            req_cols = ["date", "close", "rsz_v", "cwc_slope", "range_pos_10", "range_pos_22", "range_pos_63", "range_pos_252"]
            missing = [col for col in req_cols if col not in df.columns]
            if missing:
                print(f"  Warning: {sym} ledger is missing required columns: {missing}")
                continue
            
            # Format dates to string
            df['date_str'] = df['date'].astype(str).str[:10]
            
            # Shift rsz_v to detect transition (turns negative)
            df['rsz_v_prev'] = df['rsz_v'].shift(1)
            
            # Define mask
            # turns negative: rsz_v is negative (< 0) and previously non-negative (>= 0)
            # cwc_slope is negative (< 0)
            mask = (
                (df['rsz_v'] < 0.0) &
                (df['rsz_v_prev'] >= 0.0) &
                (df['cwc_slope'] < 0.0)
            )
            
            # Optional start date filter
            if args.start_date:
                mask = mask & (df['date_str'] >= args.start_date)
                
            matches = df[mask]
            
            if not matches.empty:
                print(f"  Found {len(matches)} matching occurrences for {sym}")
                for _, row in matches.iterrows():
                    study_records.append({
                        "symbol": sym,
                        "date": row["date_str"],
                        "close": float(row["close"]),
                        "rsz_v": float(row["rsz_v"]),
                        "rsz_v_prev": float(row["rsz_v_prev"]),
                        "cwc_slope": float(row["cwc_slope"]),
                        "range_pos_10": float(row["range_pos_10"]),
                        "range_pos_22": float(row["range_pos_22"]),
                        "range_pos_63": float(row["range_pos_63"]),
                        "range_pos_252": float(row["range_pos_252"]),
                    })
        except Exception as e:
            print(f"  Error processing {sym}: {e}")
            
    if not study_records:
        print("No matching occurrences found across the watchlist.")
        return
        
    df_results = pd.DataFrame(study_records)
    # Sort chronologically, then by symbol
    df_results = df_results.sort_values(by=["date", "symbol"]).reset_index(drop=True)
    
    # Save CSV
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "nifty50_rsz_v_cwc_slope_study.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nSaved raw results to: {csv_path}")
    
    # Generate statistics
    total_signals = len(df_results)
    distinct_symbols = df_results["symbol"].nunique()
    
    stats = {}
    for n in [10, 22, 63, 252]:
        col = f"range_pos_{n}"
        vals = df_results[col]
        stats[n] = {
            "mean": vals.mean(),
            "median": vals.median(),
            "min": vals.min(),
            "max": vals.max(),
            "std": vals.std(),
            "lower_half_pct": (vals <= 0.5).sum() / len(vals) * 100.0
        }
        
    # Generate markdown report
    md_path = output_dir / "nifty50_rsz_v_cwc_slope_study.md"
    with open(md_path, "w") as f:
        f.write(f"# RSZ Velocity & CWC Slope Negativity Study\n\n")
        f.write(f"## Metadata\n")
        f.write(f"- **Watchlist**: `{watchlist_name}` ({len(symbols)} symbols)\n")
        f.write(f"- **Start Date Filter**: `{args.start_date or 'Full History'}`\n")
        f.write(f"- **Total Signal Occurrences**: `{total_signals}`\n")
        f.write(f"- **Unique Symbols with Signals**: `{distinct_symbols}`\n")
        f.write(f"- **Generated At**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n")
        
        f.write(f"## Executive Summary\n")
        f.write(f"This study investigates market state conditions when **RSZ Velocity (`rsz_v`)** turns negative (crossing from $\\ge 0$ to $< 0$) while the **Cash Coherence Slope (`cwc_slope`)** is also negative.\n\n")
        f.write(f"These conditions typically indicate a shift towards downside momentum (`rsz_v < 0`) in an environment where cash flow alignment is deteriorating (`cwc_slope < 0`). ")
        f.write(f"Specifically, we record and analyze the price location within multiple historical ranges: `range_pos_10`, `range_pos_22`, `range_pos_63`, and `range_pos_252`. ")
        f.write(f"This analysis helps determine whether these negative turns tend to occur at local peaks (high range positions, e.g. close to 1.0) or during established downtrends/bases (low range positions, e.g. close to 0.0).\n\n")
        
        f.write(f"> [!IMPORTANT]\n")
        f.write(f"> A `range_pos` near **1.0** indicates the price is near its historical high for that lookback window, while a `range_pos` near **0.0** indicates the price is near its historical low. A value of **0.5** is the exact midpoint.\n\n")
        
        f.write(f"## Range Position Statistical Summary\n\n")
        f.write(f"The table below summarizes the distribution of price range positions across the `{total_signals}` occurrences:\n\n")
        
        f.write("| Lookback | Mean | Median | Std Dev | Min | Max | % in Lower Half (<= 0.5) |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        for n in [10, 22, 63, 252]:
            s = stats[n]
            f.write(f"| **{n} Days** | {s['mean']:.4f} | {s['median']:.4f} | {s['std']:.4f} | {s['min']:.4f} | {s['max']:.4f} | {s['lower_half_pct']:.1f}% |\n")
        f.write("\n")
        
        f.write(f"## Key Takeaways\n\n")
        
        # Formulate some smart dynamic takeaways based on the actual stats
        rp10_lower = stats[10]['lower_half_pct']
        rp252_lower = stats[252]['lower_half_pct']
        
        f.write(f"1. **Short-Term Range Position (10-day)**: ")
        if rp10_lower > 50:
            f.write(f"With **{rp10_lower:.1f}%** of occurrences having a `range_pos_10 <= 0.5`, these negative inflections predominantly occur when the price is already trading in the lower half of its recent 10-day range. This suggests the transition represents a continuation of weakness rather than an early exit signal from local highs.\n")
        else:
            f.write(f"With **{(100 - rp10_lower):.1f}%** of occurrences having a `range_pos_10 > 0.5`, these signals often trigger when the price is in the upper half of its 10-day range, representing potential early warnings of short-term rollover from local peaks.\n")
            
        f.write(f"2. **Long-Term Range Position (252-day)**: ")
        if rp252_lower > 50:
            f.write(f"With **{rp252_lower:.1f}%** of occurrences having a `range_pos_252 <= 0.5`, the majority of signals occur when the stock is in the lower half of its annual range. This implies that the momentum and flow deterioration are taking place during broader bearish phases or long-term basing processes.\n")
        else:
            f.write(f"With **{(100 - rp252_lower):.1f}%** of occurrences having a `range_pos_252 > 0.5`, many signals fire when the stock is in the upper half of its yearly range, which may signify critical macro reversals or distribution phases of strong uptrends.\n")
            
        f.write(f"3. **Coherence & Momentum Alignment**: ")
        f.write(f"The confluence of `rsz_v` turning negative and `cwc_slope < 0` is a strong filter. It ensures we only capture momentum breakdown (`rsz_v < 0`) when cash flow coherence is also actively declining (`cwc_slope < 0`), avoiding false signals in healthy uptrends.\n\n")
        
        f.write(f"## Detailed Occurrences Log\n\n")
        f.write(f"Showing all `{total_signals}` occurrences, sorted chronologically:\n\n")
        
        f.write("| Date | Symbol | Close | rsz_v (T-1) | rsz_v (T) | cwc_slope | RP 10 | RP 22 | RP 63 | RP 252 |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        
        for _, row in df_results.iterrows():
            f.write(f"| {row['date']} | **{row['symbol']}** | {row['close']:.2f} | {row['rsz_v_prev']:.4f} | {row['rsz_v']:.4f} | {row['cwc_slope']:.4f} | {row['range_pos_10']:.4f} | {row['range_pos_22']:.4f} | {row['range_pos_63']:.4f} | {row['range_pos_252']:.4f} |\n")
            
    print(f"Saved premium research report to: {md_path}")

if __name__ == "__main__":
    main()
