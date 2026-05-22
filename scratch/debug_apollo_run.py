import sys
import os
import pandas as pd
from pathlib import Path
import importlib

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scratch.study_state_based_bypass import modify_entry_file, backup_file, restore_file

backup_file()
try:
    modify_entry_file(0.02)
    print("\n--- Running step-by-step simulate_trades debug for APOLLOHOSP with module reload ---")
    
    # Reload modules to bypass cache
    import src.trading.signals.savgol_cts.entries.universal_cross as uc
    import src.trading.signals.savgol_cts.signal as ucsig
    import src.trading.signals.savgol_cts as uc_pkg
    import src.trading.signals.factory as ucfact
    import src.trading.signals as uc_signals
    
    importlib.reload(uc)
    importlib.reload(ucsig)
    importlib.reload(uc_pkg)
    importlib.reload(ucfact)
    importlib.reload(uc_signals)
    
    # Get a fresh signal instance
    signal = uc_signals.SignalFactory.get_signal("savgol_cts")
    
    engine = DivergenceEngine("APOLLOHOSP")
    result = engine.run()
    df = result.ledger
    df['date_str'] = df['date'].astype(str).str[:10]
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    
    records = df.to_dict("records")
    n = len(records)
    
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values = []
    pending_signal = None
    pending_exit_reason = None
    
    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        date_str = str(row.get("date", ""))[:10]
        close = row.get("close", 0)
        
        cw = row.get("cwvap", 0)
        cwvap_values.append(cw)
        
        show = ('2026-01-20' <= date_str <= '2026-02-05')
        
        if show:
            print(f"\n[Date: {date_str}] in_trade={in_trade}, pending_signal={pending_signal is not None}")
            
        if pending_exit_reason is not None:
            if show:
                print(f"  Pending exit processing: {pending_exit_reason}")
            in_trade = False
            pending_exit_reason = None
            continue
            
        if in_trade:
            if close > peak_close:
                peak_close = close
            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, i - trade.entry_idx,
                delivery_bad_count, cwvap_values, exit_cfg, records, i
            )
            if show:
                print(f"  check_exit returned: {reason}")
            if reason:
                pending_exit_reason = reason
        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if show:
                print(f"  Entering trade today! ATR: {atr}, Close: {close}")
            trade = Trade(
                symbol='APOLLOHOSP',
                entry_date=date_str,
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=sig.get("details", {}).get("score", 0),
                regime_at_entry=sig.get("details", {}).get("regime", "-"),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=row.get("price_slope_z", 0.0),
                psz_peak=row.get("price_slope_z", 0.0)
            )
            in_trade = True
            peak_close = close
            delivery_bad_count = 0
        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if show:
                print(f"  check_entry qualifies={qualifies}, reason={fdetails.get('reason')}")
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

finally:
    restore_file()
