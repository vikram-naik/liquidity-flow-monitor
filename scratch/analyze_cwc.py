#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag
from src.divergence_engine.engine import DivergenceEngine

def main():
    print("Loading watchlist and running walk-forward test period...")
    symbols = get_watchlist_symbols("NIFTY 50")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    uni_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
    
    records_list = []
    for t in uni_trades:
        try:
            engine = DivergenceEngine(t.symbol)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            matches = ledger[ledger['date_str'] == t.entry_date]
            if matches.empty:
                continue
            
            entry_idx = matches.index[0]
            sig_idx = entry_idx - 1
            if sig_idx < 0:
                continue
                
            sig_row = ledger.iloc[sig_idx]
            
            records_list.append({
                'symbol': t.symbol,
                'entry_date': t.entry_date,
                'pnl': t.pnl_pct,
                'rp_22': sig_row.get("range_pos_22", 0.0),
                'rp_63': sig_row.get("range_pos_63", 0.0),
                'rp_252': sig_row.get("range_pos_252", 0.0),
                'cwc': sig_row.get("cwc", 0.0),
                'cwc_slope': sig_row.get("cwc_slope", 0.0),
                'cwc_delta': sig_row.get("cwc_delta", 0.0),
                'psz_v': sig_row.get("psz_v", 0.0),
                'fas': sig_row.get("fas", 0.0),
                'pdd_30': sig_row.get("pdd_30", 0.0),
                'base_tightness': sig_row.get("base_tightness", 1.0),
                'raw_trade': t
            })
        except Exception as e:
            pass
            
    df = pd.DataFrame(records_list)
    
    # 1. Good trades that would be filtered out by range constraint:
    range_mask = (df['rp_22'] > 0.50) | (df['rp_63'] > 0.60) | (df['rp_252'] > 0.55)
    df_good_filtered = df[range_mask & (df['pnl'] > 0)]
    df_bad_filtered = df[range_mask & (df['pnl'] <= 0)]
    
    print("\n============================================================")
    print("GOOD TRADES FILTERED BY STATIC RANGE (N = {})".format(len(df_good_filtered)))
    print("============================================================")
    cols = ['symbol', 'entry_date', 'pnl', 'rp_22', 'rp_63', 'rp_252', 'cwc', 'cwc_slope', 'cwc_delta']
    print(df_good_filtered[cols].to_string(index=False))
    
    print("\n============================================================")
    print("BAD TRADES FILTERED BY STATIC RANGE (N = {})".format(len(df_bad_filtered)))
    print("============================================================")
    print(df_bad_filtered[cols].to_string(index=False))

    # Let's inspect differences in CWC metrics between these two groups
    print("\n--- Summary Statistics of CWC Metrics ---")
    print("Good Filtered:")
    print("  cwc mean: {:.4f}, std: {:.4f}, min: {:.4f}, max: {:.4f}".format(
        df_good_filtered['cwc'].mean(), df_good_filtered['cwc'].std(), df_good_filtered['cwc'].min(), df_good_filtered['cwc'].max()))
    print("  cwc_slope mean: {:.4f}, std: {:.4f}, min: {:.4f}, max: {:.4f}".format(
        df_good_filtered['cwc_slope'].mean(), df_good_filtered['cwc_slope'].std(), df_good_filtered['cwc_slope'].min(), df_good_filtered['cwc_slope'].max()))
    
    print("Bad Filtered:")
    print("  cwc mean: {:.4f}, std: {:.4f}, min: {:.4f}, max: {:.4f}".format(
        df_bad_filtered['cwc'].mean(), df_bad_filtered['cwc'].std(), df_bad_filtered['cwc'].min(), df_bad_filtered['cwc'].max()))
    print("  cwc_slope mean: {:.4f}, std: {:.4f}, min: {:.4f}, max: {:.4f}".format(
        df_bad_filtered['cwc_slope'].mean(), df_bad_filtered['cwc_slope'].std(), df_bad_filtered['cwc_slope'].min(), df_bad_filtered['cwc_slope'].max()))

    # Let's see if we can find a bypass rule that preserves the good ones and rejects the bad ones
    # For example, if cwc_slope > some_val or cwc > some_val, we bypass the range check
    print("\n--- Testing Potential Bypass Rules ---")
    
    # Let's sweep potential cwc_slope and cwc thresholds to see what they do
    for cwc_thresh in [0.0, 0.20, 0.30, 0.40, 0.50]:
        for slope_thresh in [-0.02, -0.01, 0.0, 0.01, 0.02]:
            # Rule: if cwc > cwc_thresh and cwc_slope > slope_thresh, bypass range position check!
            # Otherwise, enforce range position check.
            def test_rule(row):
                in_high_range = (row['rp_22'] > 0.50) | (row['rp_63'] > 0.60) | (row['rp_252'] > 0.55)
                # Strong trend coherence bypass:
                strong_trend = (row['cwc'] > cwc_thresh) and (row['cwc_slope'] > slope_thresh)
                if in_high_range and not strong_trend:
                    return False # Rejected by range gate
                return True # Passed
                
            passed_mask = df.apply(test_rule, axis=1)
            df_passed = df[passed_mask]
            
            # Calculate stats
            winners = df_passed[df_passed['pnl'] > 0]
            win_rate = (len(winners) / len(df_passed)) * 100.0 if not df_passed.empty else 0.0
            avg_pnl = df_passed['pnl'].mean() if not df_passed.empty else 0.0
            
            # Count how many target bad trades avoided (of the 10)
            bad_targets = [
                ("POWERGRID", "2025-05-26"),
                ("COALINDIA", "2025-06-06"),
                ("KOTAKBANK", "2024-01-16"),
                ("COALINDIA", "2025-02-07"),
                ("MAXHEALTH", "2025-12-12"),
                ("HCLTECH", "2026-03-10"),
                ("NTPC", "2024-11-25"),
                ("TATASTEEL", "2024-10-22"),
                ("ADANIENT", "2024-10-30"),
                ("ITC", "2025-11-18")
            ]
            avoided_count = 0
            for sym, dt in bad_targets:
                match = df[(df['symbol'] == sym) & (df['entry_date'].str.startswith(dt[:10]))]
                if not match.empty:
                    idx = match.index[0]
                    if not passed_mask.loc[idx]:
                        avoided_count += 1
                        
            # Check if ADANIPORTS (2026-04-02), APOLLOHOSP, CIPLA are kept
            adanip_kept = passed_mask.loc[df[(df['symbol'] == 'ADANIPORTS') & (df['entry_date'] == '2026-04-02')].index[0]] if not df[(df['symbol'] == 'ADANIPORTS') & (df['entry_date'] == '2026-04-02')].empty else False
            
            print("Bypass (cwc > {:.2f} & cwc_slope > {:.3f}): WR={:.1f}%, Avg P&L={:.2f}%, Avoided={}/10, ADANIPORTS Kept={}".format(
                cwc_thresh, slope_thresh, win_rate, avg_pnl, avoided_count, "yes" if adanip_kept else "no"
            ))

if __name__ == "__main__":
    main()
