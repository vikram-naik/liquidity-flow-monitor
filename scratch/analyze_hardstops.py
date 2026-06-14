#!/usr/bin/env python3
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason, EntryTag
from src.divergence_engine.engine import DivergenceEngine

def main():
    watchlist = "NSE F&O"
    symbols = get_watchlist_symbols(watchlist)
    test_end = today_str()
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    print(f"Simulating trades for {len(symbols)} symbols in {watchlist} from 2024-01-01 to {test_end}...")
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    
    hard_stops = []
    for t in trades:
        reason_str = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        if "hard_stop" in reason_str.lower() or "hard stop" in reason_str.lower():
            hard_stops.append(t)
            
    print(f"Found {len(hard_stops)} hard stop trades.")
    if not hard_stops:
        print("No hard stops found. Exiting.")
        return
        
    # Analyze exit date clustering for systemic shock classification
    exit_dates = [t.exit_date for t in hard_stops]
    date_counts = Counter(exit_dates)
    
    # We will classify each hard stop trade
    records_analysis = []
    
    for idx, t in enumerate(hard_stops, 1):
        sym = t.symbol
        entry_dt = t.entry_date
        exit_dt = t.exit_date
        
        # Load ledger to inspect trade context
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger
            df['date_str'] = df['date'].astype(str).str[:10]
            
            # Find the entry and exit index in ledger
            entry_matches = df[df['date_str'] == entry_dt]
            exit_matches = df[df['date_str'] == exit_dt]
            
            if entry_matches.empty or exit_matches.empty:
                print(f"Skipping {sym} ({entry_dt} to {exit_dt}) - date match not found in ledger")
                continue
                
            entry_idx = entry_matches.index[0]
            exit_idx = exit_matches.index[0]
            
            # Extract rows during the trade (from entry_idx to exit_idx - 1, or exit_idx)
            trade_df = df.iloc[entry_idx : exit_idx + 1]
            
            # Key statistics
            mfe = t.mfe_pct
            duration = t.duration
            pnl = t.pnl_pct
            
            # Volumes during trade
            avg_rdv = trade_df['rdv'].mean() if 'rdv' in trade_df else 1.0
            max_rdv = trade_df['rdv'].max() if 'rdv' in trade_df else 1.0
            
            # Relies on CWVAP
            # How many bars had close < cwvap during trade?
            close_below_cwvap = 0
            if 'cwvap' in trade_df:
                close_below_cwvap = (trade_df['close'] < trade_df['cwvap']).sum()
            pct_below_cwvap = (close_below_cwvap / len(trade_df)) * 100 if len(trade_df) > 0 else 0.0
            
            # Classification
            category = "Other"
            reason_details = ""
            
            # 1. Market Event / Systemic Shock
            # If multiple stops happened on the same date (e.g. >= 2 hard stops on the same day) or known macro dates
            if date_counts[exit_dt] >= 3:
                category = "Market Event / Systemic Shock"
                reason_details = f"Clustered exit date: {date_counts[exit_dt]} hard stops on {exit_dt}"
            # 2. Distribution Trap (Bull Trap)
            # High MFE initially, but reversed on high volume, or price crossed below CWVAP
            elif mfe >= 3.0:
                category = "Distribution Trap (Bull Trap)"
                reason_details = f"MFE reached {mfe:.2f}%, then reversed. Max RDV: {max_rdv:.2f}"
            # 3. Falling Price (Falling Knife)
            # Price stayed strictly below CWVAP or trend was strongly down, stopped out quickly
            elif pct_below_cwvap >= 80.0 and mfe < 1.5:
                category = "Falling Price (Falling Knife)"
                reason_details = f"Spent {pct_below_cwvap:.1f}% of trade below CWVAP, low MFE ({mfe:.2f}%)"
            # 4. Low Volume Selling / Drift
            # Price drifted down to stop loss on low volume, over several days
            elif avg_rdv < 1.3 and max_rdv < 2.0 and duration >= 5:
                category = "Low Volume Selling / Drift"
                reason_details = f"Slow drift (duration={duration} bars) on low volume (Avg RDV={avg_rdv:.2f}, Max RDV={max_rdv:.2f})"
            else:
                # Fallback classifications based on refined checks
                if pct_below_cwvap >= 50.0 and mfe < 2.0:
                    category = "Falling Price (Falling Knife)"
                    reason_details = f"Spent {pct_below_cwvap:.1f}% below CWVAP, MFE {mfe:.2f}%"
                elif max_rdv >= 2.0:
                    category = "Distribution Trap (Bull Trap)"
                    reason_details = f"High volume selloff (Max RDV={max_rdv:.2f}, MFE={mfe:.2f}%)"
                else:
                    category = "Low Volume Selling / Drift"
                    reason_details = f"Duration={duration} bars, Avg RDV={avg_rdv:.2f}"
                    
            records_analysis.append({
                "symbol": sym,
                "entry_date": entry_dt,
                "exit_date": exit_dt,
                "pnl": pnl,
                "mfe": mfe,
                "duration": duration,
                "avg_rdv": avg_rdv,
                "max_rdv": max_rdv,
                "pct_below_cwvap": pct_below_cwvap,
                "category": category,
                "reason_details": reason_details
            })
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    # Print Summary Report
    df_analysis = pd.DataFrame(records_analysis)
    print("\n" + "="*80)
    print("                      HARD STOP CLASSIFICATION SUMMARY")
    print("="*80)
    cat_counts = df_analysis['category'].value_counts()
    for cat, cnt in cat_counts.items():
        pct = (cnt / len(df_analysis)) * 100
        print(f"- {cat:<35}: {cnt:>2} ({pct:.1f}%)")
        
    print("\n" + "="*80)
    print("                      DETAILED HARD STOP LOGS")
    print("="*80)
    pd.set_option('display.max_rows', None)
    pd.set_option('display.width', 1000)
    print(df_analysis[['symbol', 'entry_date', 'exit_date', 'pnl', 'mfe', 'duration', 'category', 'reason_details']].to_string(index=False))
    
    # Save output to a text file for documentation
    output_path = Path(__file__).resolve().parent.parent / "output" / "hard_stops_classification.txt"
    with open(output_path, "w") as f:
        f.write("HARD STOP CLASSIFICATION SUMMARY\n")
        f.write("="*80 + "\n")
        for cat, cnt in cat_counts.items():
            pct = (cnt / len(df_analysis)) * 100
            f.write(f"- {cat:<35}: {cnt:>2} ({pct:.1f}%)\n")
        f.write("\n" + "="*80 + "\n")
        f.write("DETAILED HARD STOP LOGS\n")
        f.write("="*80 + "\n")
        f.write(df_analysis[['symbol', 'entry_date', 'exit_date', 'pnl', 'mfe', 'duration', 'category', 'reason_details']].to_string(index=False))
        f.write("\n")
    print(f"\nSaved detailed analysis to {output_path}")

if __name__ == "__main__":
    main()
