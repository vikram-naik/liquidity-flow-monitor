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

    bad_rows = []
    good_rows = []
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
                if key in bad_examples_keys:
                    bad_rows.append(row_data)
                elif t.pnl_pct > 0.0:  # Positive trades
                    good_rows.append(row_data)
                
                all_rows.append(row_data)
        except Exception as e:
            print(f"Error {sym}: {e}")

    df_bad = pd.DataFrame(bad_rows)
    df_good = pd.DataFrame(good_rows)
    df_all = pd.DataFrame(all_rows)

    print(f"\nBad trades count: {len(df_bad)}")
    print(f"Good trades count (pnl > 0): {len(df_good)}")
    print(f"Total trades: {len(df_all)}")

    # Let's define candidates for rules
    # We want a rule like:
    # if feature1 >= min1 and feature1 <= max1: reject
    # OR combination
    
    # We want to find a simple rule that:
    # 1. Rejects ALL 3 bad trades (TRENT, WIPRO, CIPLA)
    # 2. Rejects 0 or minimal good trades.
    
    features = [
        'psz_v', 'psz_smooth', 'price_slope_z', 'pdd_30', 'price_distance_30', 
        'base_tightness', 'prt_accel', 'dist_high_22', 'range_pos_10', 'ars_10'
    ]
    
    # Let's search for single-feature rules: bounding box of bad trades
    print("\n--- Single Feature Rules ---")
    for f in features:
        if f not in df_bad.columns:
            continue
        bad_vals = df_bad[f]
        good_vals = df_good[f]
        
        b_min, b_max = bad_vals.min(), bad_vals.max()
        
        # Test rule: if b_min <= val <= b_max: reject
        bad_rejected = ((bad_vals >= b_min) & (bad_vals <= b_max)).sum()
        good_rejected = ((good_vals >= b_min) & (good_vals <= b_max)).sum()
        
        print(f"Feature: {f:<20} | Range: [{b_min:+.4f}, {b_max:+.4f}] | Rejects Bad: {bad_rejected}/3 | Rejects Good: {good_rejected}/{len(df_good)}")

    # Let's search for combinations of two features!
    print("\n--- Two-Feature Bounding Box Combinations ---")
    combo_results = []
    for i in range(len(features)):
        for j in range(i+1, len(features)):
            f1, f2 = features[i], features[j]
            if f1 not in df_bad.columns or f2 not in df_bad.columns:
                continue
                
            b1_min, b1_max = df_bad[f1].min(), df_bad[f1].max()
            b2_min, b2_max = df_bad[f2].min(), df_bad[f2].max()
            
            # Rule: reject if (f1 is in bad_range) AND (f2 is in bad_range)
            bad_rej = ((df_bad[f1] >= b1_min) & (df_bad[f1] <= b1_max) & 
                       (df_bad[f2] >= b2_min) & (df_bad[f2] <= b2_max)).sum()
                       
            good_rej = ((df_good[f1] >= b1_min) & (df_good[f1] <= b1_max) & 
                        (df_good[f2] >= b2_min) & (df_good[f2] <= b2_max)).sum()
            
            if bad_rej == 3:  # Only interested in rules that catch ALL 3 bad examples!
                combo_results.append({
                    'f1': f1, 'f2': f2,
                    'f1_range': f"[{b1_min:+.4f}, {b1_max:+.4f}]",
                    'f2_range': f"[{b2_min:+.4f}, {b2_max:+.4f}]",
                    'good_rejected': good_rej
                })
                
    df_combos = pd.DataFrame(combo_results)
    if not df_combos.empty:
        df_combos = df_combos.sort_values(by='good_rejected')
        print(df_combos.to_string(index=False))
    else:
        print("No two-feature combinations rejected all 3 bad trades.")

    # Let's look at another type of rule: absolute thresholds (not bounding box).
    # For example, what if we check:
    # 1. pdd_30 is extremely negative: e.g. pdd_30 <= -5.5
    # 2. price_slope_z is between -0.25 and -0.15
    # 3. base_tightness is between 0.40 and 0.46
    
    print("\n--- Testing Specific Threshold Rules ---")
    # Rule 1: pdd_30 <= -5.5
    bad_rej_1 = (df_bad['pdd_30'] <= -5.5).sum()
    good_rej_1 = (df_good['pdd_30'] <= -5.5).sum()
    print(f"Rule: pdd_30 <= -5.5 | Rejects Bad: {bad_rej_1}/3 | Rejects Good: {good_rej_1}/{len(df_good)}")

    # Rule 2: price_slope_z between -0.26 and -0.18
    bad_rej_2 = ((df_bad['price_slope_z'] >= -0.26) & (df_bad['price_slope_z'] <= -0.18)).sum()
    good_rej_2 = ((df_good['price_slope_z'] >= -0.26) & (df_good['price_slope_z'] <= -0.18)).sum()
    print(f"Rule: price_slope_z in [-0.26, -0.18] | Rejects Bad: {bad_rej_2}/3 | Rejects Good: {good_rej_2}/{len(df_good)}")

    # Rule 3: base_tightness between 0.40 and 0.46
    bad_rej_3 = ((df_bad['base_tightness'] >= 0.40) & (df_bad['base_tightness'] <= 0.46)).sum()
    good_rej_3 = ((df_good['base_tightness'] >= 0.40) & (df_good['base_tightness'] <= 0.46)).sum()
    print(f"Rule: base_tightness in [0.40, 0.46] | Rejects Bad: {bad_rej_3}/3 | Rejects Good: {good_rej_3}/{len(df_good)}")

    # Combined Rule: (pdd_30 <= -5.5) AND (price_slope_z in [-0.26, -0.18])
    bad_rej_c = ((df_bad['pdd_30'] <= -5.5) & (df_bad['price_slope_z'] >= -0.26) & (df_bad['price_slope_z'] <= -0.18)).sum()
    good_rej_c = ((df_good['pdd_30'] <= -5.5) & (df_good['price_slope_z'] >= -0.26) & (df_good['price_slope_z'] <= -0.18)).sum()
    print(f"Rule: (pdd_30 <= -5.5) AND (price_slope_z in [-0.26, -0.18]) | Rejects Bad: {bad_rej_c}/3 | Rejects Good: {good_rej_c}/{len(df_good)}")

    # Combined Rule: (pdd_30 <= -5.5) AND (base_tightness in [0.40, 0.46])
    bad_rej_c2 = ((df_bad['pdd_30'] <= -5.5) & (df_bad['base_tightness'] >= 0.40) & (df_bad['base_tightness'] <= 0.46)).sum()
    good_rej_c2 = ((df_good['pdd_30'] <= -5.5) & (df_good['base_tightness'] >= 0.40) & (df_good['base_tightness'] <= 0.46)).sum()
    print(f"Rule: (pdd_30 <= -5.5) AND (base_tightness in [0.40, 0.46]) | Rejects Bad: {bad_rej_c2}/3 | Rejects Good: {good_rej_c2}/{len(df_good)}")

if __name__ == "__main__":
    main()
