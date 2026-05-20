import sys
import os
import argparse
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 100")
    args = parser.parse_args()
    
    watchlist_name = args.watchlist
    print(f"Running simulate_filter on {watchlist_name}...")
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols(watchlist_name)
    test_end = today_str()
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    universal_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]

    bad_examples_keys = {
        ("TRENT", "2025-01-31"),
        ("WIPRO", "2025-03-24"),
        ("CIPLA", "2026-02-03")
    }

    all_rows = []

    # Map symbols to trades
    for sym in set(t.symbol for t in universal_trades):
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            records = ledger.to_dict('records')
            
            sym_trades = [t for t in universal_trades if t.symbol == sym]
            for t in sym_trades:
                matches = ledger[ledger['date_str'] == t.entry_date]
                if matches.empty:
                    continue
                
                entry_idx = matches.index[0]
                sig_idx = entry_idx - 1
                if sig_idx < 0:
                    continue
                
                sig_row = records[sig_idx]
                row_data = dict(sig_row)
                row_data['trade_symbol'] = t.symbol
                row_data['trade_pnl'] = t.pnl_pct
                row_data['trade_entry_price'] = t.entry_price
                row_data['trade_entry_date'] = t.entry_date
                row_data['trade_signal_date'] = sig_row['date_str']
                row_data['trade_duration'] = t.duration
                
                key = (t.symbol, sig_row['date_str'])
                row_data['is_bad_example'] = key in bad_examples_keys
                all_rows.append(row_data)
        except Exception as e:
            print(f"Error {sym}: {e}")

    df_all = pd.DataFrame(all_rows)
    if df_all.empty:
        print("No Universal trades found.")
        return
    
    # We will test two filters:
    # Filter 1: (pdd_30 <= -5.5) AND (base_tightness >= 0.40)
    # Filter 2: (pdd_30 <= -5.5) AND (0.40 <= base_tightness <= 0.46)
    
    for name, condition in [
        ("Filter 1: pdd_30 <= -5.5 and base_tightness >= 0.40", 
         lambda df: (df['pdd_30'] <= -5.5) & (df['base_tightness'] >= 0.40)),
        ("Filter 2: pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46", 
         lambda df: (df['pdd_30'] <= -5.5) & (df['base_tightness'] >= 0.40) & (df['base_tightness'] <= 0.46))
    ]:
        print(f"\n==========================================")
        print(f"Evaluating: {name}")
        print(f"==========================================")
        
        # Original Trades
        orig_count = len(df_all)
        orig_winrate = (df_all['trade_pnl'] > 0).mean() * 100.0
        orig_avg_pnl = df_all['trade_pnl'].mean()
        orig_med_pnl = df_all['trade_pnl'].median()
        orig_win_pnl = df_all[df_all['trade_pnl'] > 0]['trade_pnl'].mean()
        orig_loss_pnl = df_all[df_all['trade_pnl'] <= 0]['trade_pnl'].mean()
        orig_pf = sum(df_all[df_all['trade_pnl'] > 0]['trade_pnl']) / abs(sum(df_all[df_all['trade_pnl'] <= 0]['trade_pnl'])) if any(df_all['trade_pnl'] <= 0) else float('inf')
        
        # Rejected Trades
        rej_df = df_all[condition(df_all)]
        rej_count = len(rej_df)
        rej_bad_count = rej_df['is_bad_example'].sum()
        
        # Filtered Trades (Remaining)
        rem_df = df_all[~condition(df_all)]
        rem_count = len(rem_df)
        rem_winrate = (rem_df['trade_pnl'] > 0).mean() * 100.0
        rem_avg_pnl = rem_df['trade_pnl'].mean()
        rem_med_pnl = rem_df['trade_pnl'].median()
        rem_win_pnl = rem_df[rem_df['trade_pnl'] > 0]['trade_pnl'].mean()
        rem_loss_pnl = rem_df[rem_df['trade_pnl'] <= 0]['trade_pnl'].mean()
        rem_pf = sum(rem_df[rem_df['trade_pnl'] > 0]['trade_pnl']) / abs(sum(rem_df[rem_df['trade_pnl'] <= 0]['trade_pnl'])) if any(rem_df['trade_pnl'] <= 0) else float('inf')
        
        print(f"Original Trades Count  : {orig_count}")
        print(f"Original Win Rate     : {orig_winrate:.2f}%")
        print(f"Original Avg P&L      : {orig_avg_pnl:+.2f}%")
        print(f"Original Median P&L   : {orig_med_pnl:+.2f}%")
        print(f"Original Profit Factor: {orig_pf:.2f}x")
        print(f"Original Avg Win/Loss : {orig_win_pnl:+.2f}% / {orig_loss_pnl:+.2f}%")
        print()
        print(f"REJECTED Trades Count : {rej_count} (Avoided {rej_bad_count} of 3 target bad examples)")
        if rej_count > 0:
            # Sort by pnl
            rej_df_sorted = rej_df.sort_values(by='trade_pnl')
            for idx, r in rej_df_sorted.iterrows():
                bad_label = "[TARGET BAD EXAMPLE]" if r['is_bad_example'] else ""
                print(f"  - Rejected {r['trade_symbol']} ({r['trade_signal_date']}): P&L {r['trade_pnl']:+.2f}% | pdd_30: {r['pdd_30']:.2f} | tightness: {r['base_tightness']:.3f} {bad_label}")
        print()
        print(f"Remaining Trades Count : {rem_count}")
        print(f"Remaining Win Rate     : {rem_winrate:.2f}%")
        print(f"Remaining Avg P&L      : {rem_avg_pnl:+.2f}% (Change: {rem_avg_pnl - orig_avg_pnl:+.2f}%)")
        print(f"Remaining Median P&L   : {rem_med_pnl:+.2f}% (Change: {rem_med_pnl - orig_med_pnl:+.2f}%)")
        print(f"Remaining Profit Factor: {rem_pf:.2f}x (Change: {rem_pf - orig_pf:+.2f}x)")
        print(f"Remaining Avg Win/Loss : {rem_win_pnl:+.2f}% / {rem_loss_pnl:+.2f}%")

if __name__ == "__main__":
    main()
