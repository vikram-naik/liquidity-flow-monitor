#!/usr/bin/env python3
import sys
import os
import pandas as pd
import numpy as np

# Add project root to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.base import Trade

def main():
    symbol = "ADANIPORTS"
    print(f"Loading DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    ledger = result.ledger
    
    if ledger is None or ledger.empty:
        print("Error: Empty ledger.")
        return
        
    ledger['date_str'] = pd.to_datetime(ledger['date']).dt.strftime('%Y-%m-%d')
    records = ledger.to_dict('records')
    
    # Let's find index for 2026-04-01 entry signal (meaning trade open on 2026-04-02 or so)
    entry_signal_date = "2026-04-01"
    
    entry_idx = None
    for idx, r in enumerate(records):
        if r['date_str'] == entry_signal_date:
            entry_idx = idx
            break
            
    if entry_idx is None:
        print(f"Error: Entry signal date {entry_signal_date} not found.")
        # Let's list dates around that time
        dates = [r['date_str'] for r in records if "2026-03-25" <= r['date_str'] <= "2026-04-05"]
        print(f"Available dates around that period: {dates}")
        return
        
    print(f"Found entry signal on index {entry_idx} ({records[entry_idx]['date_str']})")
    
    # Set up signal instance
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    
    # Print the entry evaluation to confirm it passed
    passed, intensity, entry_meta = signal.check_entry(
        row=records[entry_idx],
        prev_row=records[entry_idx - 1],
        cfg=entry_cfg,
        records=records,
        idx=entry_idx
    )
    print(f"check_entry on {entry_signal_date} result: passed={passed}, intensity={intensity}, meta={entry_meta}")
    
    # The rule says:
    # "Signal fires on bar i.
    # Trade opens on bar i+1.
    # Exit checks begin on bar i+2."
    trade_open_idx = entry_idx + 1
    trade_open_row = records[trade_open_idx]
    entry_price = trade_open_row['open'] # or standard execution price
    
    print(f"Trade opens on {trade_open_row['date_str']} at open price: {entry_price}")
    
    trade = Trade(
        symbol=symbol,
        entry_date=trade_open_row['date_str'],
        entry_price=entry_price,
        entry_idx=trade_open_idx,
        atr_at_entry=trade_open_row.get('atr_20', 0.0),
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )
    
    # Initialize variables for trailing
    peak_close = trade_open_row['close']
    delivery_bad_count = 0
    cwvap_values = [trade_open_row['cwvap']]
    
    print("\nSimulating trade lifecycle bar-by-bar:")
    print("=" * 120)
    fmt = "{:<12} | {:<8} | {:<8} | {:<8} | {:<6} | {:<6} | {:<8} | {:<8} | {:<8} | {:<15} | {:<12}"
    print(fmt.format("Date", "Close", "CWVAP", "VA High", "CTS", "PRT", "CWC Slp", "Z-Slope", "PnL %", "Exit Check", "State"))
    print("-" * 120)
    
    # Exit checks begin on bar i+2 (which is entry_idx + 2)
    current_idx = trade_open_idx + 1 # wait, the rule says "Exit checks begin on bar i+2" where i is the entry signal bar, so yes, entry_idx + 2.
    
    while current_idx < len(records):
        row = records[current_idx]
        prev_row = records[current_idx - 1]
        
        close = row['close']
        cwvap = row['cwvap']
        va_high = row.get('va_high', np.nan)
        cts = row.get('cts', np.nan)
        prt = row.get('prt', np.nan)
        cwc_slope = row.get('cwc_slope', np.nan)
        psz = row.get('price_slope_z', np.nan)
        
        pnl_pct = (close / entry_price - 1) * 100.0
        
        # update peak close
        if close > peak_close:
            peak_close = close
            
        cwvap_values.append(cwvap)
        bars_held = current_idx - trade_open_idx
        
        # Perform check_exit
        exit_reason, new_state = signal.check_exit(
            row=row,
            prev_row=prev_row,
            trade=trade,
            peak_close=peak_close,
            bars_held=bars_held,
            delivery_bad_count=delivery_bad_count,
            cwvap_values=cwvap_values,
            cfg=exit_cfg,
            records=records,
            idx=current_idx
        )
        
        # Parse state bits for print
        from src.trading.signals.savgol_cts.state import SavgolCTSExitState
        st = SavgolCTSExitState.from_int(new_state)
        state_str = f"sup={int(st.exit_suppressed)},bar={int(st.suppressed_this_bar)}"
        
        print(fmt.format(
            row['date_str'],
            f"{close:.2f}",
            f"{cwvap:.2f}",
            f"{va_high:.2f}" if not np.isnan(va_high) else "N/A",
            f"{cts:.2f}",
            f"{prt:.2f}",
            f"{cwc_slope:.4f}",
            f"{psz:.2f}",
            f"{pnl_pct:.2f}%",
            str(exit_reason) if exit_reason else "None",
            state_str
        ))
        
        delivery_bad_count = new_state
        
        if exit_reason:
            print("-" * 120)
            print(f"EXIT TRIGGERED on {row['date_str']}! Reason: {exit_reason}")
            break
            
        current_idx += 1

if __name__ == "__main__":
    main()
