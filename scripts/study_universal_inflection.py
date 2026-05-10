#!/usr/bin/env python3
import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend, is_flattish_line_adaptive
from src.database import get_db_connection

def get_nifty50_symbols():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol FROM watchlist_items 
        WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = 'NIFTY 50')
    """)
    symbols = [row[0] for row in cursor.fetchall()]
    conn.close()
    return symbols

def is_falling_price_guard(row, records, idx):
    if idx < 9: return False
    typical_vals_10 = [(r.get('high', 0) + r.get('low', 0) + r.get('close', 0)) / 3.0 for r in records[idx - 9 : idx + 1]]
    price_spearman_10 = evaluate_spearman_trend(typical_vals_10)
    prt_slope = row.get("prt_slope", 0.0)
    return (price_spearman_10 < -0.85 and prt_slope < -0.02) or (prt_slope < -0.50)

def simulate_trades(symbol, start_date="2024-01-01"):
    try:
        engine = DivergenceEngine(symbol)
        result = engine.run()
        df = result.ledger
        if df is None or df.empty: return []
        
        # Ensure dates are datetime
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        # Compute CWVAP Dist manually since it's missing from ledger
        df['cwvap_dist_val'] = (df['close'] / df['cwvap'] - 1) * 100
        
        records = df.to_dict('records')
        
        trades = []
        in_trade = False
        entry_row = None
        pending_entry = False
        entry_row_psz = 0.0
        entry_row_prt_slope = 0.0
        entry_idx = 0
        
        for i in range(1, len(records)):
            row = records[i]
            prev = records[i-1]
            curr_date = row['date']
            
            # Skip before start date
            if curr_date < pd.to_datetime(start_date):
                continue

            if in_trade:
                # Exit Logic: CTS trailing
                cts = row.get('cts', 0.0)
                prev_cts = prev.get('cts', 0.0)
                cts_st = row.get('cts_sell_threshold', 0.0)
                prev_cts_st = prev.get('cts_sell_threshold', 0.0)
                
                # Condition: CTS crosses CTS_ST from above
                if cts < cts_st and prev_cts >= prev_cts_st:
                    # Exit
                    exit_price = row['close']
                    pnl = (exit_price / entry_row['close']) - 1
                    duration = i - entry_idx
                    
                    # Calculate MFE/MAE
                    trade_slice = df.iloc[entry_idx : i + 1]
                    mfe = (trade_slice['high'].max() / entry_row['close']) - 1
                    mae = (trade_slice['low'].min() / entry_row['close']) - 1
                    
                    trades.append({
                        'symbol': symbol,
                        'entry_date': entry_row['date'],
                        'exit_date': curr_date,
                        'pnl': pnl,
                        'duration': duration,
                        'mfe': mfe,
                        'mae': mae,
                        'psz': entry_row_psz,
                        'prt_slope_sig': entry_row_prt_slope,
                        'prt_slope_st_delta': entry_row.get('prt_slope_sell_threshold', 0.0) - entry_row_prt_slope,
                        'cts_accel': entry_row.get('cts_accel', 0.0),
                        'cts_accel_threshold': entry_row.get('cts_accel_threshold', 0.0),
                        'cwvap_dist': entry_row.get('cwvap_dist_val', 0.0),
                        'cwc_slope': entry_row.get('cwc_slope', 0.0),
                        'pdd_120': entry_row.get('pdd_120', 0.0),
                        'rdv': entry_row.get('rdv', 0.0),
                        'coherence': entry_row.get('coherence', 0.0)
                    })
                    in_trade = False
                    entry_row = None
                continue

            if pending_entry:
                # EOD-Lag: Entry on the day after signal
                entry_row = row
                entry_idx = i
                in_trade = True
                pending_entry = False
                continue

            if i < 5: continue
            prev2 = records[i-2]
            
            # Entry Logic (Bar i)
            prt_slope = row.get('prt_slope', 0.0)
            prev_prt_slope = prev.get('prt_slope', 0.0)
            prt_accel = row.get('prt_accel', 0.0)
            psz_v = row.get('psz_v', 0.0)
            prev_psz_v = prev.get('psz_v', 0.0)
            prev2_psz_v = prev2.get('psz_v', 0.0)
            psz = row.get('price_slope_z', 0.0)
            
            # PSZ Velocity Flatness Check
            psz_v_lookback = [r.get('psz_v', 0.0) for r in records[i-5:i+1]]
            psz_v_flat = is_flattish_line_adaptive(psz_v, prev_psz_v, prev2_psz_v, psz_v_lookback, sensitivity=0.15)['is_valid']
            
            # Check conditions
            cond_cross = prt_slope > 0 and prev_prt_slope <= 0
            cond_accel = prt_accel > 0
            cond_psz_v = psz_v > prev_psz_v
            cond_psz_not_flat = not psz_v_flat
            cond_psz_cap = psz < -0.25
            cond_pdd = row.get('pdd_120', 0.0) < -1.50
            cond_cts_accel = row.get('cts_accel', 0.0) > 0
            
            if cond_cross and cond_accel and cond_psz_v and cond_psz_not_flat and cond_psz_cap and cond_pdd and cond_cts_accel:
                pending_entry = True
                entry_row_psz = psz
                entry_row_prt_slope = prt_slope
                
        return trades
    except Exception as e:
        print(f"Error simulating {symbol}: {e}")
        return []

def main():
    symbols = get_nifty50_symbols()
    print(f"Starting study on {len(symbols)} symbols from 2024-01-01...")
    
    all_trades = []
    for symbol in symbols:
        symbol_trades = simulate_trades(symbol)
        all_trades.extend(symbol_trades)
        if symbol_trades:
            print(f"  {symbol}: {len(symbol_trades)} trades")

    if not all_trades:
        print("No trades found.")
        return

    df_results = pd.DataFrame(all_trades)
    num_trades = len(df_results)
    
    print("\n" + "="*40)
    print("      UNIVERSAL CROSS INFLECTION STUDY")
    print("="*40)
    print(f"Total Trades:      {num_trades}")
    print(f"Win Rate:         {(df_results['pnl'] > 0).mean() * 100:.2f}%")
    print(f"Avg P&L:          {df_results['pnl'].mean() * 100:.2f}%")

    print("\n--- PRT Slope Threshold Delta vs PnL Correlation Analysis ---")
    corr_delta = df_results['prt_slope_st_delta'].corr(df_results['pnl'])
    print(f"Correlation (Delta vs PnL): {corr_delta:.4f}")

    # Use qcut to divide Delta into 5 bins
    df_results['delta_bin'] = pd.qcut(df_results['prt_slope_st_delta'], q=5, precision=3)
    delta_summary = df_results.groupby('delta_bin', observed=False).agg(
        trades=('pnl', 'count'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl', lambda x: x.mean() * 100)
    ).round(2)
    print("\nPerformance by PRT Slope Threshold Delta Bin:")
    print(delta_summary.to_string())

    # PnL Distribution Analysis
    print("\n--- PnL Distribution (Count vs Returns) ---")
    bins = [-np.inf, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 0.20, np.inf]
    labels = ["< -10%", "-10% to -5%", "-5% to -2%", "-2% to 0%", "0% to 2%", "2% to 5%", "5% to 10%", "10% to 20%", "> 20%"]
    df_results['pnl_bin'] = pd.cut(df_results['pnl'], bins=bins, labels=labels)
    pnl_dist = df_results['pnl_bin'].value_counts().sort_index()
    
    for label, count in pnl_dist.items():
        pct = (count / num_trades) * 100
        bar = "█" * int(pct / 2)
        print(f"{label:>12}: {count:>3} ({pct:>5.1f}%) {bar}")

    # Detailed view of all trades for this sweet spot
    print("\n--- ALL Trades in Large Delta Sweet Spot ---")
    df_all_trades = df_results.sort_values('entry_date')
    if not df_all_trades.empty:
        df_print = df_all_trades[['symbol', 'entry_date', 'exit_date', 'pnl', 'psz', 'prt_slope_st_delta', 'duration']].copy()
        df_print['pnl_pct'] = (df_print['pnl'] * 100).round(2).astype(str) + "%"
        df_print['entry_date'] = df_print['entry_date'].dt.strftime('%Y-%m-%d')
        df_print['exit_date'] = df_print['exit_date'].dt.strftime('%Y-%m-%d')
        # Reorder columns
        cols = ['symbol', 'entry_date', 'exit_date', 'pnl_pct', 'psz', 'prt_slope_st_delta', 'duration']
        print(tabulate(df_print[cols], headers='keys', tablefmt='psql', showindex=False))
    else:
        print("No trades found.")

    # Lever Analysis: Comparing Winners vs Losers
    print("\n--- Lever Analysis: Feature Comparison (Winners vs Losers) ---")
    df_results['is_winner'] = df_results['pnl'] > 0.02 # Strong winners
    df_results['is_loser'] = (df_results['pnl'] <= 0.0) & (df_results['pnl'] > -0.10) # Target losers
    
    features = ['cwvap_dist', 'cwc_slope', 'pdd_120', 'rdv', 'coherence', 'cts_accel']
    
    analysis = []
    for f in features:
        win_mean = df_results[df_results['is_winner']][f].mean()
        lose_mean = df_results[df_results['is_loser']][f].mean()
        analysis.append({
            'feature': f,
            'win_avg': win_mean,
            'lose_avg': lose_mean,
            'delta': win_mean - lose_mean
        })
    
    df_lever = pd.DataFrame(analysis).round(4)
    print(tabulate(df_lever, headers='keys', tablefmt='psql', showindex=False))

    # Test potential levers
    print("\n--- Testing Specific Levers ---")
    
    base_wr = (df_results['pnl'] > 0).mean() * 100
    base_pnl = df_results['pnl'].mean() * 100
    
    # 1. Distance from CWVAP lever
    # Filter for entries where price is CLOSE to CWVAP
    l1_val = df_results[df_results['is_winner']]['cwvap_dist'].quantile(0.80)
    
    # 2. Institutional Exhaustion (PDD_120)
    # Deep negative PDD means institutional accumulation/reversion is mature
    l2_val = df_results[df_results['is_winner']]['pdd_120'].mean()

    # 3. CTS Accel Lever
    l3_val = df_results[df_results['is_winner']]['cts_accel'].quantile(0.20)
    
    print(f"Baseline: Trades: {len(df_results)} | WR: {base_wr:.2f}% | Avg P&L: {base_pnl:.2f}%")
    
    df_l1 = df_results[df_results['cwvap_dist'] < l1_val]
    print(f"Lever [CWVAP Dist < {l1_val:.2f}%]: Trades: {len(df_l1)} | WR: {(df_l1['pnl']>0).mean()*100:.2f}% | Avg P&L: {df_l1['pnl'].mean()*100:.2f}%")
    
    df_l2 = df_results[df_results['pdd_120'] < l2_val]
    print(f"Lever [PDD_120 < {l2_val:.2f}]: Trades: {len(df_l2)} | WR: {(df_l2['pnl']>0).mean()*100:.2f}% | Avg P&L: {df_l2['pnl'].mean()*100:.2f}%")

    df_l3 = df_results[df_results['cts_accel'] > l3_val]
    print(f"Lever [CTS Accel > {l3_val:.4f}]: Trades: {len(df_l3)} | WR: {(df_l3['pnl']>0).mean()*100:.2f}% | Avg P&L: {df_l3['pnl'].mean()*100:.2f}%")
    
    # Combined Lever
    df_comb = df_results[(df_results['cwvap_dist'] < l1_val) & (df_results['pdd_120'] < l2_val) & (df_results['cts_accel'] > l3_val)]
    print(f"Combined Lever (All 3): Trades: {len(df_comb)} | WR: {(df_comb['pnl']>0).mean()*100:.2f}% | Avg P&L: {df_comb['pnl'].mean()*100:.2f}%")

if __name__ == "__main__":
    main()
