#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str, compute_profit_factor
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag
from src.divergence_engine.engine import DivergenceEngine

def main():
    print("Loading watchlist and running walk-forward test period to gather trade telemetry...")
    symbols = get_watchlist_symbols("NIFTY 50")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    # Run TEST period
    trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    uni_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
    
    # Enrich trades
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
                'mae': getattr(t, 'mae_pct', 0.0),
                'rp_22': sig_row.get("range_pos_22", 0.0),
                'rp_63': sig_row.get("range_pos_63", 0.0),
                'rp_252': sig_row.get("range_pos_252", 0.0),
                'cwc': sig_row.get("cwc", 0.0),
                'cwc_slope': sig_row.get("cwc_slope", 0.0),
                'psz_v': sig_row.get("psz_v", 0.0),
                'pdd_30': sig_row.get("pdd_30", 0.0),
                'base_tightness': sig_row.get("base_tightness", 1.0),
                'raw_trade': t
            })
        except Exception as e:
            pass
            
    df = pd.DataFrame(records_list)
    print(f"Enriched {len(df)} trades successfully.")
    
    # 10 target bad trades to track
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
    
    # Find matching indices
    target_indices = []
    for sym, entry_dt in bad_targets:
        match = df[(df['symbol'] == sym) & (df['entry_date'].str.startswith(entry_dt[:10]))]
        if not match.empty:
            target_indices.append(match.index[0])
            
    # Sweep dynamic trend bypasses
    # We want to keep ADANIPORTS (2026-04-02), APOLLOHOSP, and CIPLA
    results = []
    
    # Define a set of bypasses
    # Format: (name, function)
    bypasses = [
        ("No Filter", lambda r: True),
        ("Static Range Filter (No Bypass)", lambda r: not ((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55))),
        
        # CWC & cwc_slope bypasses
        ("Bypass: cwc > 0.5 & cwc_slope > -0.01", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.50 and r['cwc_slope'] > -0.01))),
         
        ("Bypass: cwc > 0.4 & cwc_slope > -0.01", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.40 and r['cwc_slope'] > -0.01))),
         
        ("Bypass: cwc > 0.35 & cwc_slope > -0.015", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.35 and r['cwc_slope'] > -0.015))),

        ("Bypass: cwc > 0.3 & cwc_slope > -0.02", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.30 and r['cwc_slope'] > -0.02))),
         
        # What if we only bypass when CWC is high AND trend direction is positive?
        ("Bypass: cwc > 0.5", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.50))),
         
        ("Bypass: cwc > 0.4", 
         lambda r: not (((r['rp_22'] > 0.50) | (r['rp_63'] > 0.60) | (r['rp_252'] > 0.55)) and not (r['cwc'] > 0.40))),
    ]
    
    for name, rule in bypasses:
        passed_mask = df.apply(rule, axis=1)
        df_passed = df[passed_mask]
        
        # Calculate stats
        raw_trades_passed = [r['raw_trade'] for idx, r in df_passed.iterrows()]
        pf = compute_profit_factor(raw_trades_passed)
        winners = df_passed[df_passed['pnl'] > 0]
        win_rate = (len(winners) / len(df_passed)) * 100.0 if not df_passed.empty else 0.0
        avg_pnl = df_passed['pnl'].mean() if not df_passed.empty else 0.0
        avg_mae = df_passed['mae'].mean() if not df_passed.empty else 0.0
        
        # Check target bad trades avoided
        avoided_count = 0
        for ti in target_indices:
            if not passed_mask.loc[ti]:
                avoided_count += 1
                
        # Check if ADANIPORTS is kept
        adanip_match = df[(df['symbol'] == 'ADANIPORTS') & (df['entry_date'] == '2026-04-02')]
        adanip_kept = "no"
        if not adanip_match.empty and passed_mask.loc[adanip_match.index[0]]:
            adanip_kept = "yes"
            
        # Check how many of the 12 filtered winners are kept (restored)
        good_filtered_indices = df[(df['rp_22'] > 0.50) | (df['rp_63'] > 0.60) | (df['rp_252'] > 0.55) & (df['pnl'] > 0)].index
        restored_good_count = 0
        for idx in good_filtered_indices:
            if passed_mask.loc[idx] and df.loc[idx]['pnl'] > 0:
                restored_good_count += 1
                
        # Check how many of the 7 filtered losers are kept (restored)
        bad_filtered_indices = df[(df['rp_22'] > 0.50) | (df['rp_63'] > 0.60) | (df['rp_252'] > 0.55) & (df['pnl'] <= 0)].index
        restored_bad_count = 0
        for idx in bad_filtered_indices:
            if passed_mask.loc[idx] and df.loc[idx]['pnl'] <= 0:
                restored_bad_count += 1
                
        results.append({
            'Strategy': name,
            'Trades': len(df_passed),
            'Win Rate': f"{win_rate:.1f}%",
            'Avg P&L': f"{avg_pnl:.2f}%",
            'Profit Factor': f"{pf:.2f}",
            'Avg MAE': f"{avg_mae:.2f}%",
            'Targets Avoided': f"{avoided_count}/10",
            'ADANIPORTS Kept': adanip_kept,
            'Good Restored': f"{restored_good_count}/12",
            'Bad Restored (Leak)': f"{restored_bad_count}/7"
        })
        
    df_res = pd.DataFrame(results)
    print("\n=== SYSTEMATIC TREND BYPASS SWEEP RESULTS ===")
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    main()
