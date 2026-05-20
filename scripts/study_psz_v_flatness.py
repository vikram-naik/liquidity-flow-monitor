#!/usr/bin/env python3
"""
Study script to analyze psz_v flatness for Universal Cross entry signals during the test period.
Saves a markdown report to output/psz_v_flatness_study.md.
"""

import sys
import os
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive

def calculate_spread(series):
    if len(series) == 0:
        return 0.0
    return float(np.max(series) - np.min(series))

def is_adaptive_flat(y1, y2, y3, lookback, sensitivity=0.08):
    if len(lookback) < 5:
        return False, 0.0
    res = is_flattish_line_adaptive(y1, y2, y3, lookback, sensitivity=sensitivity)
    return res["is_valid"], res["dynamic_tolerance_used"]

def main():
    parser = argparse.ArgumentParser(description="Study psz_v flatness for Universal Cross")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist to analyze (default: NIFTY 50)")
    args = parser.parse_args()

    watchlist_name = args.watchlist
    print(f"Starting psz_v Flatness Study for Universal Cross on {watchlist_name}...")
    
    # 1. Simulate trades
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    try:
        symbols = get_watchlist_symbols(watchlist_name)
    except SystemExit:
        print(f"Error: Watchlist '{watchlist_name}' not found. Please check spelling.")
        return

    test_end = today_str()
    print(f"Running TEST period backtest (2024-01-01 to {test_end})...")
    
    # Simulate trades using walk_forward module
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    
    # Filter for Universal Cross trades only
    universal_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
    print(f"Found {len(universal_trades)} Universal Cross trades in TEST period.")
    
    if not universal_trades:
        print("No Universal Cross trades found. Exiting.")
        return

    # 2. Extract ledgers and compute psz_v features
    print("\nExtracting psz_v history and checking flatness for each trade...")
    
    study_records = []
    
    # Group trades by symbol to load DivergenceEngine only once per symbol
    by_sym = {}
    for t in universal_trades:
        by_sym.setdefault(t.symbol, []).append(t)
        
    for sym, sym_trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                print(f"  Warning: Empty ledger for {sym}")
                continue
                
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            records = ledger.to_dict('records')
            
            for t in sym_trades:
                matches = ledger[ledger['date_str'] == t.entry_date]
                if matches.empty:
                    continue
                
                entry_idx = matches.index[0]
                sig_idx = entry_idx - 1
                if sig_idx < 5:
                    continue
                
                # Signal Row (T) and prior rows
                sig_row = records[sig_idx]
                t_date = sig_row['date_str']
                
                # psz_v at T, T-1, T-2, T-3, T-4, T-5
                psz_v_vals = {
                    't': sig_row.get('psz_v', 0.0),
                    't_1': records[sig_idx - 1].get('psz_v', 0.0),
                    't_2': records[sig_idx - 2].get('psz_v', 0.0),
                    't_3': records[sig_idx - 3].get('psz_v', 0.0),
                    't_4': records[sig_idx - 4].get('psz_v', 0.0),
                    't_5': records[sig_idx - 5].get('psz_v', 0.0)
                }
                
                # Windows
                w3_t1 = [psz_v_vals['t_3'], psz_v_vals['t_2'], psz_v_vals['t_1']]
                w4_t1 = [psz_v_vals['t_4'], psz_v_vals['t_3'], psz_v_vals['t_2'], psz_v_vals['t_1']]
                w5_t1 = [psz_v_vals['t_5'], psz_v_vals['t_4'], psz_v_vals['t_3'], psz_v_vals['t_2'], psz_v_vals['t_1']]
                
                w3_t = [psz_v_vals['t_2'], psz_v_vals['t_1'], psz_v_vals['t']]
                w4_t = [psz_v_vals['t_3'], psz_v_vals['t_2'], psz_v_vals['t_1'], psz_v_vals['t']]
                w5_t = [psz_v_vals['t_4'], psz_v_vals['t_3'], psz_v_vals['t_2'], psz_v_vals['t_1'], psz_v_vals['t']]
                
                # Spreads (Max - Min)
                spread_3_t1 = calculate_spread(w3_t1)
                spread_4_t1 = calculate_spread(w4_t1)
                spread_5_t1 = calculate_spread(w5_t1)
                
                spread_3_t = calculate_spread(w3_t)
                spread_4_t = calculate_spread(w4_t)
                spread_5_t = calculate_spread(w5_t)
                
                # Lookback for adaptive (30 bars before T-1)
                lookback_data = [records[i].get("psz_v", 0.0) for i in range(max(0, sig_idx - 30), sig_idx)]
                
                # Adaptive Flatness Check on t-3, t-2, t-1
                adaptive_flat_3_t1, tol_used_3_t1 = is_adaptive_flat(w3_t1[0], w3_t1[1], w3_t1[2], lookback_data, sensitivity=0.08)
                adaptive_flat_3_t, tol_used_3_t = is_adaptive_flat(w3_t[0], w3_t[1], w3_t[2], lookback_data + [psz_v_vals['t_1']], sensitivity=0.08)
                
                # Define Flatness criteria
                is_flat_c1 = (spread_3_t1 <= 0.025)  # Criterion 1: 3-bar spread from T-1 <= 0.025
                is_flat_c2 = (spread_4_t1 <= 0.025)  # Criterion 2: 4-bar spread from T-1 <= 0.025
                is_flat_c3 = (spread_5_t1 <= 0.030)  # Criterion 3: 5-bar spread from T-1 <= 0.030
                is_flat_c4 = adaptive_flat_3_t1      # Criterion 4: Adaptive 3-bar flat from T-1 (sens=0.08)
                is_flat_c5 = (spread_3_t <= 0.025)   # Criterion 5: 3-bar spread from T <= 0.025
                
                pnl = t.pnl_pct
                is_win = pnl > 0
                
                # Find MFE/MAE
                mfe = getattr(t, "mfe_pct", 0.0)
                mae = getattr(t, "mae_pct", 0.0)
                
                study_records.append({
                    'symbol': t.symbol,
                    'signal_date': t_date,
                    'entry_date': t.entry_date,
                    'pnl': pnl,
                    'is_win': is_win,
                    'mfe': mfe,
                    'mae': mae,
                    'duration': t.duration,
                    'exit_reason': t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
                    'psz_v_t': psz_v_vals['t'],
                    'psz_v_t1': psz_v_vals['t_1'],
                    'psz_v_t2': psz_v_vals['t_2'],
                    'psz_v_t3': psz_v_vals['t_3'],
                    'psz_v_t4': psz_v_vals['t_4'],
                    'psz_v_t5': psz_v_vals['t_5'],
                    'spread_3_t1': spread_3_t1,
                    'spread_4_t1': spread_4_t1,
                    'spread_5_t1': spread_5_t1,
                    'spread_3_t': spread_3_t,
                    'adaptive_flat_3_t1': adaptive_flat_3_t1,
                    'flat_c1': is_flat_c1,
                    'flat_c2': is_flat_c2,
                    'flat_c3': is_flat_c3,
                    'flat_c4': is_flat_c4,
                    'flat_c5': is_flat_c5
                })
        except Exception as e:
            print(f"  Error processing {sym}: {e}")
            
    df_study = pd.DataFrame(study_records)
    if df_study.empty:
        print("No study records generated. Exiting.")
        return

    # Sort by signal date
    df_study = df_study.sort_values(by='signal_date').reset_index(drop=True)

    # 3. Analyze Criteria
    criteria = {
        'C1_3Bar_T1': ('flat_c1', '3-bar spread from T-1 <= 0.025'),
        'C2_4Bar_T1': ('flat_c2', '4-bar spread from T-1 <= 0.025'),
        'C3_5Bar_T1': ('flat_c3', '5-bar spread from T-1 <= 0.030'),
        'C4_Adaptive_T1': ('flat_c4', 'Adaptive 3-bar flat from T-1 (sens=0.08)'),
        'C5_3Bar_T': ('flat_c5', '3-bar spread from T <= 0.025')
    }

    summary_stats = []
    
    for key, (col, desc) in criteria.items():
        flat_subset = df_study[df_study[col] == True]
        nonflat_subset = df_study[df_study[col] == False]
        
        flat_count = len(flat_subset)
        nonflat_count = len(nonflat_subset)
        total_count = len(df_study)
        
        flat_winrate = (flat_subset['is_win'].mean() * 100.0) if flat_count > 0 else 0.0
        nonflat_winrate = (nonflat_subset['is_win'].mean() * 100.0) if nonflat_count > 0 else 0.0
        
        flat_pnl = flat_subset['pnl'].mean() if flat_count > 0 else 0.0
        nonflat_pnl = nonflat_subset['pnl'].mean() if nonflat_count > 0 else 0.0
        
        flat_med_pnl = flat_subset['pnl'].median() if flat_count > 0 else 0.0
        nonflat_med_pnl = nonflat_subset['pnl'].median() if nonflat_count > 0 else 0.0
        
        flat_mfe = flat_subset['mfe'].mean() if flat_count > 0 else 0.0
        nonflat_mfe = nonflat_subset['mfe'].mean() if nonflat_count > 0 else 0.0
        
        flat_mae = flat_subset['mae'].mean() if flat_count > 0 else 0.0
        nonflat_mae = nonflat_subset['mae'].mean() if nonflat_count > 0 else 0.0
        
        flat_dur = flat_subset['duration'].mean() if flat_count > 0 else 0.0
        nonflat_dur = nonflat_subset['duration'].mean() if nonflat_count > 0 else 0.0
        
        summary_stats.append({
            'Criterion': key,
            'Description': desc,
            'Flat Count': flat_count,
            'Flat WinRate': f"{flat_winrate:.1f}%",
            'Flat AvgPnL': f"{flat_pnl:+.2f}%",
            'Flat MedPnL': f"{flat_med_pnl:+.2f}%",
            'Flat AvgMFE': f"{flat_mfe:.2f}%",
            'Flat AvgMAE': f"{flat_mae:.2f}%",
            'Flat AvgDur': f"{flat_dur:.1f}",
            'NonFlat Count': nonflat_count,
            'NonFlat WinRate': f"{nonflat_winrate:.1f}%",
            'NonFlat AvgPnL': f"{nonflat_pnl:+.2f}%",
            'NonFlat MedPnL': f"{nonflat_med_pnl:+.2f}%",
            'NonFlat AvgMFE': f"{nonflat_mfe:.2f}%",
            'NonFlat AvgMAE': f"{nonflat_mae:.2f}%",
            'NonFlat AvgDur': f"{nonflat_dur:.1f}"
        })
        
    df_summary = pd.DataFrame(summary_stats)

    # 4. Generate Markdown Report
    output_dir = Path(__file__).resolve().parent.parent / "output"
    os.makedirs(str(output_dir), exist_ok=True)
    report_path = output_dir / "psz_v_flatness_study.md"
    
    with open(report_path, "w") as f:
        f.write(f"# PSZ Velocity Flatness Study: SavgolCTS Universal Cross\n\n")
        f.write(f"- **Watchlist**: `{watchlist_name}`\n")
        f.write(f"- **Period**: `TEST` (2024-01-01 to {test_end})\n")
        f.write(f"- **Total Universal Cross Trades**: {len(df_study)}\n")
        f.write(f"- **Analysis Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write(f"## Executive Summary\n\n")
        f.write(f"This study inspects the flatness of the `psz_v` indicator (PSZ Velocity) over the last 3-5 bars preceding the signal (or up to the signal date) for the `UniversalCross` entry setup. ")
        f.write(f"The objective is to analyze whether a flat/stable PSZ velocity before the signal acts as a positive filter (avoiding whip-saws or indicating stable basing) or if it restricts profitable trades.\n\n")
        
        # User's examples check
        f.write(f"### Specific Examples Verification\n")
        f.write(f"Verification of user-provided examples (expecting flat `psz_v` setups):\n\n")
        
        examples_found = []
        for ex in [("TRENT", "2025-01-31"), ("WIPRO", "2025-03-24"), ("CIPLA", "2026-02-03")]:
            sym_match = df_study[(df_study['symbol'] == ex[0]) & (df_study['signal_date'] == ex[1])]
            if not sym_match.empty:
                row = sym_match.iloc[0]
                examples_found.append(row)
                
        if examples_found:
            f.write("| Symbol | Sig Date | PnL% | psz_v (T) | psz_v (T-1) | psz_v (T-2) | psz_v (T-3) | 3-Bar Spread (T-1) | 4-Bar Spread (T-1) | C1 (3-Bar Flat) | C4 (Adaptive Flat) |\n")
            f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
            for r in examples_found:
                c1_str = "🟢 FLAT" if r['flat_c1'] else "🔴 NOT FLAT"
                c4_str = "🟢 FLAT" if r['flat_c4'] else "🔴 NOT FLAT"
                f.write(f"| {r['symbol']} | {r['signal_date']} | {r['pnl']:+.2f}% | {r['psz_v_t']:.4f} | {r['psz_v_t1']:.4f} | {r['psz_v_t2']:.4f} | {r['psz_v_t3']:.4f} | {r['spread_3_t1']:.4f} | {r['spread_4_t1']:.4f} | {c1_str} | {c4_str} |\n")
        else:
            f.write("No matching exact examples found in the study results.\n")
        f.write("\n")
        
        f.write(f"## Comparative Performance Metrics\n\n")
        f.write(f"Below is a comparison of trades satisfying each flatness criterion (Flat) against those that do not (Non-Flat).\n\n")
        
        for idx, row in df_summary.iterrows():
            f.write(f"### Criterion {row['Criterion']}: {row['Description']}\n\n")
            
            f.write("| Metric | Flat Setup | Non-Flat Setup |\n")
            f.write("| --- | --- | --- |\n")
            f.write(f"| **Count** | {row['Flat Count']} | {row['NonFlat Count']} |\n")
            f.write(f"| **Win Rate** | **{row['Flat WinRate']}** | {row['NonFlat WinRate']} |\n")
            f.write(f"| **Avg PnL** | **{row['Flat AvgPnL']}** | {row['NonFlat AvgPnL']} |\n")
            f.write(f"| **Median PnL** | **{row['Flat MedPnL']}** | {row['NonFlat MedPnL']} |\n")
            f.write(f"| **Avg MFE** | {row['Flat AvgMFE']} | {row['NonFlat AvgMFE']} |\n")
            f.write(f"| **Avg MAE** | {row['Flat AvgMAE']} | {row['NonFlat AvgMAE']} |\n")
            f.write(f"| **Avg Duration** | {row['Flat AvgDur']} bars | {row['NonFlat AvgDur']} bars |\n\n")
            
        f.write(f"## Detailed Trades Log\n\n")
        f.write(f"Full breakdown of all {len(df_study)} trades with their psz_v values and flatness classifications:\n\n")
        f.write("| # | Symbol | Sig Date | PnL% | psz_v (T) | psz_v (T-1) | psz_v (T-2) | psz_v (T-3) | 3-Bar Sprd (T-1) | C1 (3-Bar Flat) | C4 (Adaptive) | Exit |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for i, r in enumerate(df_study.to_dict('records'), 1):
            c1_str = "🟢 Flat" if r['flat_c1'] else "❌"
            c4_str = "🟢 Adaptive" if r['flat_c4'] else "❌"
            f.write(f"| {i} | {r['symbol']} | {r['signal_date']} | {r['pnl']:+.2f}% | {r['psz_v_t']:.4f} | {r['psz_v_t1']:.4f} | {r['psz_v_t2']:.4f} | {r['psz_v_t3']:.4f} | {r['spread_3_t1']:.4f} | {c1_str} | {c4_str} | {r['exit_reason']} |\n")
            
    print(f"\nSuccessfully generated study report at: {report_path}")
    print("\n--- Summary of Results (Criterion C1: 3-bar spread from T-1 <= 0.025) ---")
    c1_row = df_summary[df_summary['Criterion'] == 'C1_3Bar_T1'].iloc[0]
    print(f"Flat Trades: {c1_row['Flat Count']} | Win Rate: {c1_row['Flat WinRate']} | Avg PnL: {c1_row['Flat AvgPnL']}")
    print(f"Non-Flat Trades: {c1_row['NonFlat Count']} | Win Rate: {c1_row['NonFlat WinRate']} | Avg PnL: {c1_row['NonFlat AvgPnL']}")

if __name__ == "__main__":
    main()
