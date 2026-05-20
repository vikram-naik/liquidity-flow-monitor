import sys
import os
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
    watchlist_name = "NIFTY 50"
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols(watchlist_name)
    test_end = today_str()
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    universal_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]

    print(f"Total Universal Cross trades: {len(universal_trades)}")

    bad_examples_keys = {
        ("TRENT", "2025-01-31"),
        ("WIPRO", "2025-03-24"),
        ("CIPLA", "2026-02-03")
    }

    bad_rows = []
    good_rows = []
    other_rows = []

    # Let's map symbols to trades
    by_sym = {}
    for t in universal_trades:
        by_sym.setdefault(t.symbol, []).append(t)

    # We will gather features for signal days
    for sym, sym_trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            records = ledger.to_dict('records')
            
            for t in sym_trades:
                matches = ledger[ledger['date_str'] == t.entry_date]
                if matches.empty:
                    continue
                
                entry_idx = matches.index[0]
                sig_idx = entry_idx - 1
                if sig_idx < 0:
                    continue
                
                sig_row = records[sig_idx]
                t_date = sig_row['date_str']
                
                # Attach PnL and symbol
                row_data = dict(sig_row)
                row_data['trade_symbol'] = t.symbol
                row_data['trade_pnl'] = t.pnl_pct
                row_data['trade_entry_date'] = t.entry_date
                row_data['trade_signal_date'] = t_date
                
                key = (t.symbol, t_date)
                if key in bad_examples_keys:
                    bad_rows.append(row_data)
                elif t.pnl_pct > 3.0:  # Strong positive trades
                    good_rows.append(row_data)
                else:
                    other_rows.append(row_data)
        except Exception as e:
            print(f"Error {sym}: {e}")

    df_bad = pd.DataFrame(bad_rows)
    df_good = pd.DataFrame(good_rows)
    df_other = pd.DataFrame(other_rows)

    print(f"Bad examples count: {len(df_bad)}")
    print(f"Good examples count: {len(df_good)}")
    print(f"Other trades count: {len(df_other)}")

    if df_bad.empty:
        print("Error: bad examples count is 0. Check date matching.")
        return

    # We will look at numeric columns only
    cols_to_compare = []
    for col in df_bad.columns:
        if col in ['date', 'date_str', 'trade_symbol', 'trade_entry_date', 'trade_signal_date'] or not np.issubdtype(df_bad[col].dtype, np.number):
            continue
        cols_to_compare.append(col)

    results = []
    for col in cols_to_compare:
        bad_vals = df_bad[col].dropna()
        good_vals = df_good[col].dropna()
        
        if len(bad_vals) == 0 or len(good_vals) == 0:
            continue
            
        bad_min, bad_max = bad_vals.min(), bad_vals.max()
        good_min, good_max = good_vals.min(), good_vals.max()
        
        # Count how many good trades would be rejected if we use the bad trades' bounding box [bad_min, bad_max] as a filter.
        good_in_bad_range = ((good_vals >= bad_min) & (good_vals <= bad_max)).sum()
        pct_good_rejected = (good_in_bad_range / len(good_vals)) * 100.0
        
        results.append({
            'feature': col,
            'bad_min': bad_min,
            'bad_max': bad_max,
            'good_min': good_min,
            'good_max': good_max,
            'good_rejected_count': good_in_bad_range,
            'pct_good_rejected': pct_good_rejected
        })

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by='pct_good_rejected')
    
    print("\n--- Features where bad examples have distinct ranges (Top 25) ---")
    print(df_res.head(25).to_string(index=False))

    # Let's print out the exact values of the top distinct features for the bad trades
    top_features = df_res.head(10)['feature'].tolist()
    print("\n--- Value comparison for top distinct features ---")
    for f in top_features:
        print(f"\nFeature: {f}")
        for r in bad_rows:
            print(f"  Bad {r['trade_symbol']} ({r['trade_signal_date']}): {r[f]}")
        print(f"  Good range: [{df_good[f].min():.4f}, {df_good[f].max():.4f}]")

if __name__ == "__main__":
    main()
