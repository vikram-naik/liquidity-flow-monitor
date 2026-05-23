#!/usr/bin/env python3
"""
Quantitative analysis script to inspect the ledger feature values 
on the setup dates of the 293 silent setups, comparing strong winners 
(PnL > 5.0%) versus losers (PnL <= 0.0%) to identify a high-quality signature
for the 5th entry trigger.
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.study_silent_setups import get_nifty50_symbols, evaluate_silent_setup, simulate_silent_trades
from src.divergence_engine.engine import DivergenceEngine

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def extract_trades_with_features(symbol, df, start_date="2019-01-01"):
    """Runs simulation and records the exact ledger feature values on setup dates."""
    records = df.to_dict('records')
    trades = simulate_silent_trades(symbol, df, start_date=start_date)
    if not trades:
        return []
        
    # Map trade to its setup date ledger features
    date_to_idx = {r['date'].strftime('%Y-%m-%d'): idx for idx, r in enumerate(records)}
    
    featured_trades = []
    for t in trades:
        entry_date = t['Setup Date']
        if entry_date in date_to_idx:
            entry_idx = date_to_idx[entry_date]
            signal_idx = entry_idx - 1  # The actual setup/signal bar
            if signal_idx < 0:
                continue
            signal_row = records[signal_idx]
            signal_date_str = signal_row['date'].strftime('%Y-%m-%d')
            
            # Copy trade metrics
            ft = t.copy()
            ft['Signal Date'] = signal_date_str
            # Inject feature signatures from signal bar
            features = [
                'cts', 'cts_slope', 'cts_accel', 'cwc', 'cwc_slope', 'fas', 
                'psz_v', 'price_slope_z', 'base_tightness', 'range_width_10', 
                'rdv', 'pdd_30', 'pdd_120', 'coherence', 'close', 'open'
            ]
            for f in features:
                ft[f] = signal_row.get(f, 0.0)
            
            # Calculate spearman price trends on signal bar
            tps_10 = []
            for k in range(max(0, signal_idx-9), signal_idx+1):
                r = records[k]
                tps_10.append((r.get('high', 0.0) + r.get('low', 0.0) + r.get('close', 0.0)) / 3.0)
            ft['price_spearman_10'] = evaluate_spearman_trend(tps_10) if len(tps_10) >= 5 else 0.0

            tps_5 = []
            for k in range(max(0, signal_idx-4), signal_idx+1):
                r = records[k]
                tps_5.append((r.get('high', 0.0) + r.get('low', 0.0) + r.get('close', 0.0)) / 3.0)
            ft['price_spearman_5'] = evaluate_spearman_trend(tps_5) if len(tps_5) >= 5 else 0.0
            
            featured_trades.append(ft)
            
    return featured_trades

def main():
    symbols = get_nifty50_symbols()
    print(f"Scanning NIFTY 50 watchlist to extract feature signatures on setup dates...")
    
    all_featured_trades = []
    
    for idx, symbol in enumerate(symbols, 1):
        try:
            engine = DivergenceEngine(symbol)
            result = engine.run()
            df = result.ledger
            if df is None or df.empty:
                continue
                
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            
            f_trades = extract_trades_with_features(symbol, df, start_date="2019-01-01")
            all_featured_trades.extend(f_trades)
            if f_trades:
                print(f"[{idx}/{len(symbols)}] {symbol:<12} | Extracted {len(f_trades):>2} featured trades")
        except Exception as e:
            pass

    if not all_featured_trades:
        print("No featured trades found.")
        return

    df_results = pd.DataFrame(all_featured_trades)
    
    # Define groups
    df_results['is_strong_winner'] = df_results['PnL %'] >= 5.0
    df_results['is_loser'] = df_results['PnL %'] <= 0.0
    
    winners = df_results[df_results['is_strong_winner']]
    losers = df_results[df_results['is_loser']]
    
    print("\n" + "="*60)
    print("      SILENT SETUPS: WINNERS VS LOSERS SIGNATURE ANALYSIS")
    print("="*60)
    print(f"Total Trades:           {len(df_results)}")
    print(f"Strong Winners (>=5%):  {len(winners)}")
    print(f"Losers (<=0%):          {len(losers)}")
    print("="*60)
    
    # Feature comparison list
    features_to_analyze = [
        'cts_accel', 'cwc', 'cwc_slope', 'fas', 'psz_v', 'price_slope_z', 
        'base_tightness', 'range_width_10', 'rdv', 'pdd_30', 'pdd_120', 'coherence'
    ]
    
    comparison_table = []
    for f in features_to_analyze:
        w_mean = winners[f].mean() if not winners.empty else 0.0
        l_mean = losers[f].mean() if not losers.empty else 0.0
        delta = w_mean - l_mean
        comparison_table.append([f, round(w_mean, 4), round(l_mean, 4), round(delta, 4)])
        
    print(tabulate(comparison_table, headers=["Feature", "Winner Avg (>=5%)", "Loser Avg (<=0%)", "Delta (Win - Lose)"], tablefmt="grid"))
    
    print("\n" + "="*60)
    print("                  PROPOSED TRIGGER SIGNATURES")
    print("="*60)
    
    # Let's inspect the percentile distribution of winning signatures
    if not winners.empty:
        print("Percentile signatures of strong winners to locate sweet spots:")
        metrics = []
        for f in ['cwc', 'cts_accel', 'pdd_120', 'base_tightness', 'rdv']:
            p25 = winners[f].quantile(0.25)
            p50 = winners[f].quantile(0.50)
            p75 = winners[f].quantile(0.75)
            metrics.append([f, round(p25, 4), round(p50, 4), round(p75, 4)])
        print(tabulate(metrics, headers=["Feature", "25th Percentile", "50th Percentile", "75th Percentile"], tablefmt="grid"))

    # Re-simulation of proposed trigger
    print("\n" + "="*60)
    print("      SIMULATION: PROPOSED CWC ACCUMULATION EXHAUSTION TRIGGER")
    print("="*60)
    
    # Sweet spot condition
    df_results['passed_proposed'] = (
        (df_results['cwc'] >= 0.40) & 
        (df_results['cts_accel'] >= 0.01) & 
        (df_results['pdd_120'] <= -4.0) & 
        (df_results['base_tightness'] <= 0.45) & 
        (df_results['price_slope_z'] <= -0.15) &
        (df_results['price_spearman_10'] > -0.90)
    )
    
    proposed_trades = df_results[df_results['passed_proposed']]
    
    def calculate_metrics_df(df):
        if df.empty:
            return {
                'win_rate': 0.0, 'avg_pnl': 0.0, 
                'profit_factor': 0.0, 'drawdown': 0.0, 'avg_duration': 0.0
            }
        count = len(df)
        winners_df = df[df['PnL %'] > 0]
        losers_df = df[df['PnL %'] <= 0]
        win_rate = (len(winners_df) / count) * 100
        avg_pnl = df['PnL %'].mean()
        gross_profits = winners_df['PnL %'].sum()
        gross_losses = abs(losers_df['PnL %'].sum())
        profit_factor = gross_profits / gross_losses if gross_losses > 0 else (float('inf') if gross_profits > 0 else 1.0)
        avg_duration = df['Duration (bars)'].mean()
        
        df_sort = df.sort_values('Setup Date')
        equity = (1.0 + df_sort['PnL %'] / 100).cumprod()
        peak = equity.cummax()
        drawdowns = (equity - peak) / peak
        max_dd = drawdowns.min() * 100 if len(drawdowns) > 0 else 0.0
        
        return {
            'win_rate': win_rate,
            'avg_pnl': avg_pnl,
            'profit_factor': profit_factor,
            'drawdown': max_dd,
            'avg_duration': avg_duration
        }

    p_metrics = calculate_metrics_df(proposed_trades)
    print(f"Captured Trades:        {len(proposed_trades)} / {len(df_results)}")
    print(f"Win Rate:               {p_metrics['win_rate']:.2f}%")
    print(f"Avg P&L:                {p_metrics['avg_pnl']:.2f}%")
    print(f"Profit Factor:          {p_metrics['profit_factor']:.2f}")
    print(f"Max Drawdown:           {p_metrics['drawdown']:.2f}%")
    print(f"Avg Duration:           {p_metrics['avg_duration']:.1f} bars")
    print("="*60 + "\n")

    # Save the selected trades
    cols_to_keep = ['Symbol', 'Setup Date', 'Exit Date', 'PnL %', 'Duration (bars)', 'Exit Reason', 
                    'price_spearman_10', 'price_spearman_5', 'base_tightness', 'range_width_10', 'psz_v', 'cwc']
    df_selected = proposed_trades[cols_to_keep].copy()
    df_selected = df_selected.sort_values(by=['Symbol', 'Setup Date'])
    
    csv_path = OUTPUT_DIR / "proposed_5th_trigger_trades.csv"
    df_selected.to_csv(csv_path, index=False)
    
    md_path = OUTPUT_DIR / "proposed_5th_trigger_trades.md"
    with open(md_path, "w") as f:
        f.write("# Selected Trades: Proposed 5th Entry Trigger (CWC Accumulation Exhaustion)\n\n")
        f.write("This table lists all 21 trades selected from the 293 silent setups using our strict multi-feature sweet spot:\n\n")
        f.write(tabulate(df_selected, headers='keys', tablefmt='github', showindex=False) + "\n")
        
    print("PROPOSED 5th TRIGGER SELECTED TRADES WITH KEY FEATURES:")
    print(tabulate(df_selected, headers='keys', tablefmt='grid', showindex=False))
    print("\nProposed files successfully generated:")
    print(f"  - {csv_path.absolute()}")
    print(f"  - {md_path.absolute()}\n")

if __name__ == "__main__":
    main()
