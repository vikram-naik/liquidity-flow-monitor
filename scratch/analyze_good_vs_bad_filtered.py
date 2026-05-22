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
    print("Loading watchlist and running walk-forward test period to gather trade telemetry...")
    symbols = get_watchlist_symbols("NIFTY 50")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    # Run TEST period
    trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    uni_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
    
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
            
            records_list.append({
                'symbol': t.symbol,
                'entry_date': t.entry_date,
                'pnl': t.pnl_pct,
                'mae': getattr(t, 'mae_pct', 0.0),
                'mfe': getattr(t, 'mfe_pct', 0.0),
                'duration': t.duration,
                'rp_22': sig_row.get("range_pos_22", 0.0),
                'rp_63': sig_row.get("range_pos_63", 0.0),
                'rp_252': sig_row.get("range_pos_252", 0.0),
                'psz_v': sig_row.get("psz_v", 0.0),
                'pdd_30': sig_row.get("pdd_30", 0.0),
                'base_tightness': sig_row.get("base_tightness", 1.0),
                'fas': sig_row.get("fas", 0.0),
                'cts_slope': sig_row.get("cts_slope", 0.0),
            })
        except Exception as e:
            pass
            
    df = pd.DataFrame(records_list)
    
    # Apply filter condition: rp_22 > 0.50 or rp_63 > 0.60 or rp_252 > 0.55
    filtered_out_mask = (df['rp_22'] > 0.50) | (df['rp_63'] > 0.60) | (df['rp_252'] > 0.55)
    df_filtered_out = df[filtered_out_mask]
    
    print(f"\nTotal trades in TEST period before filter: {len(df)}")
    print(f"Total trades filtered out: {len(df_filtered_out)}")
    
    winners_filtered = df_filtered_out[df_filtered_out['pnl'] > 0]
    losers_filtered = df_filtered_out[df_filtered_out['pnl'] <= 0]
    
    print(f"  - Winning trades filtered out (Good Trades): {len(winners_filtered)}")
    print(f"  - Losing trades filtered out (Bad Trades): {len(losers_filtered)}")
    
    print("\n--- DETAILED LIST OF FILTERED OUT GOOD TRADES (WINNERS) ---")
    if winners_filtered.empty:
        print("None")
    else:
        print(winners_filtered[['symbol', 'entry_date', 'pnl', 'rp_22', 'rp_63', 'rp_252']].to_string(index=False))
        
    print("\n--- DETAILED LIST OF FILTERED OUT BAD TRADES (LOSERS) ---")
    if losers_filtered.empty:
        print("None")
    else:
        print(losers_filtered[['symbol', 'entry_date', 'pnl', 'rp_22', 'rp_63', 'rp_252']].to_string(index=False))

if __name__ == "__main__":
    main()
