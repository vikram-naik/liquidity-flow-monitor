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
                row_data['trade_entry_date'] = t.entry_date
                row_data['trade_signal_date'] = sig_row['date_str']
                
                key = (t.symbol, sig_row['date_str'])
                row_data['is_bad_example'] = key in bad_examples_keys
                all_rows.append(row_data)
        except Exception as e:
            print(f"Error {sym}: {e}")

    df_all = pd.DataFrame(all_rows)
    
    # We want to test different combinations of:
    # pdd_30 <= pdd_thresh
    # base_tightness >= bt_thresh
    # and see the count of bad examples rejected (out of 3) and good trades rejected (out of ones with pnl > 0).
    
    print("\n--- Testing Broad 'Distribution Trap' Rule: (pdd_30 <= PDD_Thresh) AND (base_tightness >= BT_Thresh) ---")
    
    good_trades = df_all[(df_all['trade_pnl'] > 0) & (~df_all['is_bad_example'])]
    bad_trades = df_all[df_all['is_bad_example']]
    
    pdd_thresholds = [-5.0, -5.3, -5.5, -5.7, -6.0]
    bt_thresholds = [0.35, 0.38, 0.40, 0.42]
    
    results = []
    for pdd_t in pdd_thresholds:
        for bt_t in bt_thresholds:
            # Rule: reject if pdd_30 <= pdd_t AND base_tightness >= bt_t
            bad_rej = ((bad_trades['pdd_30'] <= pdd_t) & (bad_trades['base_tightness'] >= bt_t)).sum()
            good_rej = ((good_trades['pdd_30'] <= pdd_t) & (good_trades['base_tightness'] >= bt_t)).sum()
            
            results.append({
                'pdd_threshold': pdd_t,
                'bt_threshold': bt_t,
                'bad_rejected': f"{bad_rej}/3",
                'good_rejected': f"{good_rej}/{len(good_trades)}"
            })
            
    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))

    # Also let's test a rule:
    # Reject if pdd_30 <= -5.5 AND base_tightness >= 0.40 AND base_tightness <= 0.46
    print("\n--- Testing Bounded Tightness Rule: (pdd_30 <= -5.5) AND (0.40 <= base_tightness <= 0.46) ---")
    bad_rej = ((bad_trades['pdd_30'] <= -5.5) & (bad_trades['base_tightness'] >= 0.40) & (bad_trades['base_tightness'] <= 0.46)).sum()
    good_rej = ((good_trades['pdd_30'] <= -5.5) & (good_trades['base_tightness'] >= 0.40) & (good_trades['base_tightness'] <= 0.46)).sum()
    print(f"Rejects Bad: {bad_rej}/3 | Rejects Good: {good_rej}/{len(good_trades)}")

if __name__ == "__main__":
    main()
