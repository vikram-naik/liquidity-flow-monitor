#!/usr/bin/env python3
"""
Study script: FAS Zero Cross Exit Optimization (Dual-Engine Union).

Compares:
1. Current System (Phase-Shift Anchor/CTS Trail)
2. Proposed Routine A (Recovery Gate -> CTS failure -> FAS switch if FAS > 0, Exit if FAS < -0.1)
3. Proposed Routine B (Proposed A + CTS Tolerance of -0.05 for failure check)
4. Proposed Routine C (The Dual-Engine Union: 
   - Wait for Recovery (either > 0).
   - Once live, stay in as long as AT LEAST ONE engine is healthy.
   - CTS Engine fails if CTS < 0 or CTS < ST.
   - FAS Engine fails if FAS < -0.1.
   - EXIT when both have failed OR if FAS > 1.0 climax).

Usage:
    python scripts/study_fas_exit_optimization.py --symbol <SYM> --date <YYYY-MM-DD>
"""

import argparse
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason, EntryTag

def run_study(symbol, signal_date):
    print(f"\n{'='*135}")
    print(f" STUDY: FAS EXIT OPTIMISATION | {symbol} | Signal: {signal_date} ".center(135, '='))
    print(f"{'='*135}\n")

    # 1. Load Data
    engine = DivergenceEngine(symbol)
    df = engine.run().ledger
    df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
    records = df.to_dict('records')
    
    matches = df[df['date_str'] == signal_date]
    if matches.empty:
        print(f"Error: Signal date {signal_date} not found for {symbol}")
        return
    
    sig_idx = matches.index[0]
    entry_idx = sig_idx + 1
    
    if entry_idx >= len(df):
        print(f"Error: Signal bar is the last bar.")
        return

    entry_price = df.loc[entry_idx, 'close']
    entry_date = df.loc[entry_idx, 'date_str']
    
    print(f"Signal Date: {signal_date}")
    print(f"Entry Date:  {entry_date} (at Close)")
    print(f"Entry Price: {entry_price:.2f}")
    print("-" * 135)

    # 2. Simulations
    current = simulate_current_exit(records, entry_idx, entry_price, symbol)
    prop_a = simulate_routine_a(records, entry_idx, entry_price, symbol)
    prop_b = simulate_routine_b(records, entry_idx, entry_price, symbol)
    prop_c = simulate_routine_c(records, entry_idx, entry_price, symbol)

    # 3. Report
    def fmt_pnl(p): return f"{p:+.2f}%"
    
    report = [
        ["Metric", "Current System", "Proposed A (Tol 0)", "Proposed B (Tol -0.05)", "Proposed C (Union)"],
        ["Exit Date", current['exit_date'], prop_a['exit_date'], prop_b['exit_date'], prop_c['exit_date']],
        ["Exit Reason", current['exit_reason'], prop_a['exit_reason'], prop_b['exit_reason'], prop_c['exit_reason']],
        ["Bars", current['duration'], prop_a['duration'], prop_b['duration'], prop_c['duration']],
        ["Exit Price", f"{current['exit_price']:.2f}", f"{prop_a['exit_price']:.2f}", f"{prop_b['exit_price']:.2f}", f"{prop_c['exit_price']:.2f}"],
        ["PnL %", fmt_pnl(current['pnl_pct']), fmt_pnl(prop_a['pnl_pct']), fmt_pnl(prop_b['pnl_pct']), fmt_pnl(prop_c['pnl_pct'])],
    ]
    
    print(tabulate(report, headers="firstrow", tablefmt="grid"))
    
    pnl_list = [current['pnl_pct'], prop_a['pnl_pct'], prop_b['pnl_pct'], prop_c['pnl_pct']]
    best_idx = np.argmax(pnl_list)
    names = ["CURRENT", "PROPOSED A", "PROPOSED B", "PROPOSED C"]
    
    print(f"\nVerdict: {names[best_idx]} logic is best for this trade.")
    print(f"{'='*135}\n")

def simulate_current_exit(records, entry_idx, entry_price, symbol):
    signal = SavgolCTSSignal()
    exit_cfg = SavgolCTSExitConfig()
    trade = Trade(
        symbol=symbol, entry_date=records[entry_idx]['date_str'], entry_price=entry_price,
        entry_idx=entry_idx, atr_at_entry=records[entry_idx].get('atr_20', 0),
        soft_filters_passed=0, entry_tag=EntryTag.FAS_ZERO_CROSS.value
    )
    peak_close = entry_price
    delivery_bad_count = 0
    cwvap_values = []
    pending_exit_reason = None
    
    for i in range(entry_idx + 1, len(records)):
        row, prev = records[i], records[i-1]
        cwvap_values.append(row.get('cwvap', np.nan))
        if pending_exit_reason:
            exit_p = row.get('open', row['close'])
            return {'exit_date': row['date_str'], 'exit_reason': pending_exit_reason, 'exit_price': exit_p, 'pnl_pct': (exit_p / entry_price - 1) * 100, 'duration': i - entry_idx}
        if row['close'] > peak_close: peak_close = row['close']
        res, updated_state = signal.check_exit(row, prev, trade, peak_close, i - entry_idx, delivery_bad_count, cwvap_values, exit_cfg, records, i)
        if res: pending_exit_reason = res
        delivery_bad_count = updated_state
    
    last = records[-1]
    return {'exit_date': last['date_str'], 'exit_reason': "END_OF_DATA", 'exit_price': last['close'], 'pnl_pct': (last['close'] / entry_price - 1) * 100, 'duration': len(records) - 1 - entry_idx}

def simulate_routine_a(records, entry_idx, entry_price, symbol):
    mode = "TRAILING_CTS"
    pending_exit_reason = None
    recovery_gate_passed = False
    
    for i in range(entry_idx + 1, len(records)):
        row = records[i]
        if pending_exit_reason:
            exit_p = row.get('open', row['close'])
            return {'exit_date': row['date_str'], 'exit_reason': pending_exit_reason, 'exit_price': exit_p, 'pnl_pct': (exit_p / entry_price - 1) * 100, 'duration': i - entry_idx}
            
        cts, cts_st, fas = row.get('cts', np.nan), row.get('cts_sell_threshold', np.nan), row.get('fas', np.nan)
        
        if not recovery_gate_passed:
            if (not np.isnan(cts) and cts > 0) or (not np.isnan(fas) and fas > 0):
                recovery_gate_passed = True
            else:
                continue

        if mode == "TRAILING_CTS":
            is_cts_fail = (not np.isnan(cts) and cts < 0) or (not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st)
            if is_cts_fail:
                if not np.isnan(fas) and fas > 0: mode = "TRAILING_FAS"
                else: pending_exit_reason = "CTS FAIL (Mode A)"
                    
        if mode == "TRAILING_FAS":
            if not np.isnan(fas) and fas < -0.1: pending_exit_reason = "FAS FAIL (Mode A)"
                
    last = records[-1]
    return {'exit_date': last['date_str'], 'exit_reason': "END_OF_DATA", 'exit_price': last['close'], 'pnl_pct': (last['close'] / entry_price - 1) * 100, 'duration': len(records) - 1 - entry_idx}

def simulate_routine_b(records, entry_idx, entry_price, symbol):
    mode = "TRAILING_CTS"
    pending_exit_reason = None
    cts_tol = -0.05
    recovery_gate_passed = False
    
    for i in range(entry_idx + 1, len(records)):
        row = records[i]
        if pending_exit_reason:
            exit_p = row.get('open', row['close'])
            return {'exit_date': row['date_str'], 'exit_reason': pending_exit_reason, 'exit_price': exit_p, 'pnl_pct': (exit_p / entry_price - 1) * 100, 'duration': i - entry_idx}
            
        cts, cts_st, fas = row.get('cts', np.nan), row.get('cts_sell_threshold', np.nan), row.get('fas', np.nan)

        if not recovery_gate_passed:
            if (not np.isnan(cts) and cts > 0) or (not np.isnan(fas) and fas > 0):
                recovery_gate_passed = True
            else:
                continue
        
        if mode == "TRAILING_CTS":
            is_cts_fail = (not np.isnan(cts) and cts < cts_tol) or (not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st)
            if is_cts_fail:
                if not np.isnan(fas) and fas > 0: mode = "TRAILING_FAS"
                else: pending_exit_reason = f"CTS FAIL (Tol {cts_tol})"
                    
        if mode == "TRAILING_FAS":
            if not np.isnan(fas) and fas < -0.1: pending_exit_reason = "FAS FAIL (Mode B)"
                
    last = records[-1]
    return {'exit_date': last['date_str'], 'exit_reason': "END_OF_DATA", 'exit_price': last['close'], 'pnl_pct': (last['close'] / entry_price - 1) * 100, 'duration': len(records) - 1 - entry_idx}

def simulate_routine_c(records, entry_idx, entry_price, symbol):
    """Proposed C: Dual-Engine Union Logic."""
    pending_exit_reason = None
    recovery_gate_passed = False
    
    for i in range(entry_idx + 1, len(records)):
        row = records[i]
        if pending_exit_reason:
            exit_p = row.get('open', row['close'])
            return {'exit_date': row['date_str'], 'exit_reason': pending_exit_reason, 'exit_price': exit_p, 'pnl_pct': (exit_p / entry_price - 1) * 100, 'duration': i - entry_idx}
            
        cts, cts_st, fas = row.get('cts', np.nan), row.get('cts_sell_threshold', np.nan), row.get('fas', np.nan)
        
        # 0. Recovery Gate
        if not recovery_gate_passed:
            if (not np.isnan(cts) and cts > 0) or (not np.isnan(fas) and fas > 0):
                recovery_gate_passed = True
            else:
                continue

        # 1. Individual Engine Status
        # CTS Engine running if above zero AND above sell threshold
        cts_running = not (np.isnan(cts) or cts < 0 or (not np.isnan(cts_st) and cts < cts_st))
        # FAS Engine running if above structural floor
        fas_running = not (np.isnan(fas) or fas < -0.1)

        # 2. Union Exit Trigger
        # Exit if BOTH engines have failed
        if not cts_running and not fas_running:
            pending_exit_reason = "DUAL ENGINE FAILURE"
            
        # 3. Climax Override
        # If FAS shoots into extreme climax, we take profit regardless of CTS
        if not np.isnan(fas) and fas > 1.0:
            pending_exit_reason = "FAS CLIMAX (> 1.0)"

    last = records[-1]
    return {'exit_date': last['date_str'], 'exit_reason': "END_OF_DATA", 'exit_price': last['close'], 'pnl_pct': (last['close'] / entry_price - 1) * 100, 'duration': len(records) - 1 - entry_idx}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--date", type=str, required=True)
    args = parser.parse_args()
    run_study(args.symbol.upper(), args.date)
