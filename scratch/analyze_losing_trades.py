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
    
    print(f"Total Universal Cross trades: {len(uni_trades)}")
    
    # Enrich trades with signal-day ledger data
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
            
            # Enrich
            records_list.append({
                'symbol': t.symbol,
                'entry_date': t.entry_date,
                'pnl': t.pnl_pct,
                'mae': getattr(t, 'mae_pct', 0.0),
                'mfe': getattr(t, 'mfe_pct', 0.0),
                'duration': t.duration,
                'rp_10': sig_row.get("range_pos_10", 0.0),
                'rp_22': sig_row.get("range_pos_22", 0.0),
                'rp_63': sig_row.get("range_pos_63", 0.0),
                'rp_252': sig_row.get("range_pos_252", 0.0),
                'psz_v': sig_row.get("psz_v", 0.0),
                'pdd_30': sig_row.get("pdd_30", 0.0),
                'base_tightness': sig_row.get("base_tightness", 1.0),
                'fas': sig_row.get("fas", 0.0),
                'cts_slope': sig_row.get("cts_slope", 0.0),
                'raw_trade': t
            })
        except Exception as e:
            print(f"Error enriching {t.symbol} {t.entry_date}: {e}")
            
    df = pd.DataFrame(records_list)
    print(f"Enriched {len(df)} trades successfully.")
    
    # 10 target bad trades to track
    bad_targets = [
        ("POWERGRID", "2025-05-26"), # Entry date is Monday (signal was Friday 23rd)
        ("COALINDIA", "2025-06-06"),  # Entry date 6th (signal 5th)
        ("KOTAKBANK", "2024-01-16"),  # Entry date 16th (signal 15th)
        ("COALINDIA", "2025-02-07"),  # Entry date 7th (signal 6th)
        ("MAXHEALTH", "2025-12-12"),  # Entry date 12th (signal 11th)
        ("HCLTECH", "2026-03-10"),    # Entry date 10th (signal 9th)
        ("NTPC", "2024-11-25"),       # Entry date 25th (signal 22nd)
        ("TATASTEEL", "2024-10-22"),  # Entry date 22nd (signal 21st)
        ("ADANIENT", "2024-10-30"),   # Entry date 30th (signal 29th)
        ("ITC", "2025-11-18")         # Entry date 18th (signal 17th)
    ]
    
    # Let's map target dates to entries in our DataFrame
    target_indices = []
    for sym, entry_dt in bad_targets:
        match = df[(df['symbol'] == sym) & (df['entry_date'].str.startswith(entry_dt[:10]))]
        if not match.empty:
            target_indices.append(match.index[0])
            print(f"Matched bad trade target: {sym} on {entry_dt} (Index {match.index[0]})")
        else:
            # Try matching signal date or nearby
            match_sig = df[(df['symbol'] == sym) & (df['pnl'] < -3.0)]
            if len(match_sig) == 1:
                target_indices.append(match_sig.index[0])
                print(f"Matched bad trade target by fallback: {sym} on {match_sig.iloc[0]['entry_date']}")
            else:
                print(f"Warning: Could not match bad trade target for {sym} {entry_dt}")
                
    # Sweep filter heuristics
    filters_to_test = [
        ("No Filter (Current)", lambda r: True),
        ("rp_63 <= 0.60 and rp_252 <= 0.55", lambda r: r['rp_63'] <= 0.60 and r['rp_252'] <= 0.55),
        ("rp_22 <= 0.50 and rp_63 <= 0.60 and rp_252 <= 0.55", lambda r: r['rp_22'] <= 0.50 and r['rp_63'] <= 0.60 and r['rp_252'] <= 0.55),
        ("Distribution Trap (pdd_30 <= -5.5 and base_tightness <= 0.46)", lambda r: not (r['pdd_30'] <= -5.5 and r['base_tightness'] <= 0.46)),
        ("Range + Distribution Trap combined", lambda r: r['rp_22'] <= 0.50 and r['rp_63'] <= 0.60 and r['rp_252'] <= 0.55 and not (r['pdd_30'] <= -5.5 and r['base_tightness'] <= 0.46)),
    ]
    
    results = []
    for name, filt in filters_to_test:
        passed_mask = df.apply(filt, axis=1)
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
                
        # Check target good trades kept (APOLLOHOSP 2026-02-02, CIPLA 2026-04-06)
        apollo_kept = "yes"
        cipla_kept = "yes"
        
        apollo_match = df[(df['symbol'] == 'APOLLOHOSP') & (df['entry_date'] == '2026-02-02')]
        if not apollo_match.empty and not passed_mask.loc[apollo_match.index[0]]:
            apollo_kept = "no"
            
        cipla_match = df[(df['symbol'] == 'CIPLA') & (df['entry_date'] == '2026-04-06')]
        if not cipla_match.empty and not passed_mask.loc[cipla_match.index[0]]:
            cipla_kept = "no"
            
        results.append({
            'Filter': name,
            'Trades': len(df_passed),
            'Win Rate': f"{win_rate:.1f}%",
            'Avg P&L': f"{avg_pnl:.2f}%",
            'Profit Factor': f"{pf:.2f}",
            'Avg MAE': f"{avg_mae:.2f}%",
            'Targets Avoided': f"{avoided_count}/{len(target_indices)}",
            'Apollo Kept': apollo_kept,
            'Cipla Kept': cipla_kept
        })
        
    df_res = pd.DataFrame(results)
    print("\n=== HEURISTIC FILTERS SWEEP COMPARISON ===")
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    main()
