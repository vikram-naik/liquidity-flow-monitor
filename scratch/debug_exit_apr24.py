#!/usr/bin/env python3
"""
Deep-dive debug script: traces exactly why ADANIPORTS exited on 2026-04-24.
Prints all gate values, threshold comparisons, and CWVAP guard decision path.
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.base import Trade
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard, _apply_cwvap_guard_raw
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross

def format_cmp(val, threshold, label_above="ABOVE", label_below="BELOW"):
    if np.isnan(val) or np.isnan(threshold):
        return "N/A"
    if val >= threshold:
        return f"{label_above} ({val:.4f} >= {threshold:.4f})"
    else:
        return f"{label_below} ({val:.4f} < {threshold:.4f})"


def main():
    symbol = "ADANIPORTS"
    entry_signal_date = "2026-04-01"
    exit_date = "2026-04-24"

    print(f"Loading DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    ledger = result.ledger

    ledger['date_str'] = pd.to_datetime(ledger['date']).dt.strftime('%Y-%m-%d')
    records = ledger.to_dict('records')

    # Locate indices
    entry_idx = next((i for i, r in enumerate(records) if r['date_str'] == entry_signal_date), None)
    exit_idx  = next((i for i, r in enumerate(records) if r['date_str'] == exit_date),  None)

    if entry_idx is None or exit_idx is None:
        print("Could not locate one or both dates in ledger.")
        return

    trade_open_idx = entry_idx + 1
    trade_open_row = records[trade_open_idx]
    entry_price = trade_open_row['open']

    print(f"\nEntry signal: {entry_signal_date} (idx={entry_idx})")
    print(f"Trade open:  {trade_open_row['date_str']} (idx={trade_open_idx}), open={entry_price:.2f}")
    print(f"Exit date:   {exit_date} (idx={exit_idx})")

    trade = Trade(
        symbol=symbol,
        entry_date=trade_open_row['date_str'],
        entry_price=entry_price,
        entry_idx=trade_open_idx,
        atr_at_entry=trade_open_row.get('atr_20', 0.0),
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    cfg = SavgolCTSExitConfig()

    # Replay state up to (but not including) exit bar so we have the correct
    # accumulated state (delivery_bad_count / suppression bits)
    delivery_bad_count = 0
    peak_close = trade_open_row['close']
    cwvap_values = [trade_open_row.get('cwvap', np.nan)]

    signal = SignalFactory.get_signal("savgol_cts")
    for cur_idx in range(trade_open_idx + 1, exit_idx):
        row = records[cur_idx]
        prev_row = records[cur_idx - 1]
        close = row.get('close', np.nan)
        cwvap = row.get('cwvap', np.nan)
        if close > peak_close:
            peak_close = close
        cwvap_values.append(cwvap)
        bars_held = cur_idx - trade_open_idx
        _, delivery_bad_count = signal.check_exit(
            row=row, prev_row=prev_row, trade=trade,
            peak_close=peak_close, bars_held=bars_held,
            delivery_bad_count=delivery_bad_count,
            cwvap_values=cwvap_values, cfg=cfg, records=records, idx=cur_idx
        )

    print(f"\nState entering exit bar (sup bits): {SavgolCTSExitState.from_int(delivery_bad_count)}")

    # ---- Now inspect the exit bar itself ----
    row      = records[exit_idx]
    prev_row = records[exit_idx - 1]
    close    = row['close']
    cwvap    = row.get('cwvap', np.nan)
    va_high  = row.get('va_high', np.nan)
    cts      = row.get('cts', np.nan)
    cts_st   = row.get('cts_sell_threshold', np.nan)
    prt      = row.get('prt', np.nan)
    prt_st   = row.get('prt_sell_threshold', np.nan)
    prt_bt   = row.get('prt_buy_threshold', np.nan)
    prt_slope = row.get('prt_slope', np.nan)
    cwc_slope = row.get('cwc_slope', np.nan)
    psz      = row.get('price_slope_z', np.nan)
    prev_cts = prev_row.get('cts', np.nan)
    prev_cts_st = prev_row.get('cts_sell_threshold', np.nan)
    prev_prt = prev_row.get('prt', np.nan)
    prev_prt_st = prev_row.get('prt_sell_threshold', np.nan)
    cwc      = row.get('cwc', np.nan)
    fas      = row.get('fas', np.nan)
    rp63     = row.get('range_pos_63', np.nan)
    rp252    = row.get('range_pos_252', np.nan)

    pnl_pct = (close / entry_price - 1) * 100.0

    print("\n" + "="*70)
    print(f"EXIT BAR ANALYSIS: {exit_date}")
    print("="*70)

    print(f"\n[Price Context]")
    print(f"  Close:         {close:.2f}")
    print(f"  Open:          {row.get('open',np.nan):.2f}")
    print(f"  High:          {row.get('high',np.nan):.2f}")
    print(f"  Low:           {row.get('low',np.nan):.2f}")
    print(f"  CWVAP:         {cwvap:.2f}   -> close > cwvap = {close > cwvap}")
    print(f"  VA High:       {va_high:.2f}  -> close > va_high = {close > va_high}")
    print(f"  PnL:           {pnl_pct:.2f}%")

    print(f"\n[CTS Trail Gate]")
    print(f"  prev_cts:      {prev_cts:.4f}")
    print(f"  prev_cts_st:   {prev_cts_st:.4f}")
    print(f"  cts (now):     {cts:.4f}")
    print(f"  cts_st (now):  {cts_st:.4f}")
    prev_was_above = (not np.isnan(prev_cts) and not np.isnan(prev_cts_st) and prev_cts >= prev_cts_st)
    now_below      = (not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st)
    st_cross_triggered = prev_was_above and now_below
    print(f"  ST_CROSS condition: prev_cts >= prev_cts_st = {prev_was_above}")
    print(f"                      cts < cts_st            = {now_below}")
    print(f"  => ST_CROSS TRIGGERED: {st_cross_triggered}")

    print(f"\n[PRT Trail Gate]")
    print(f"  prev_prt:      {prev_prt:.4f}")
    print(f"  prev_prt_st:   {prev_prt_st:.4f}")
    print(f"  prt (now):     {prt:.4f}")
    print(f"  prt_st (now):  {prt_st:.4f}")
    prev_prt_above = (not np.isnan(prev_prt) and not np.isnan(prev_prt_st) and prev_prt >= prev_prt_st)
    now_prt_below  = (not np.isnan(prt) and not np.isnan(prt_st) and prt < prt_st)
    prt_cross_triggered = prev_prt_above and now_prt_below
    print(f"  PRT_ST_CROSS: {prt_cross_triggered}")

    print(f"\n[Momentum Indicators]")
    print(f"  price_slope_z: {psz:.4f}   (> 0 = strong momentum for suppression)")
    print(f"  cwc_slope:     {cwc_slope:.4f}  (< threshold = CWC early release)")
    print(f"  cwc:           {cwc:.4f}")
    print(f"  fas:           {fas:.4f}")
    print(f"  range_pos_63:  {rp63:.4f}")
    print(f"  range_pos_252: {rp252:.4f}")

    print(f"\n[CWVAP Guard: cwvap_guard config]")
    gc = cfg.cwvap_guard
    print(f"  cwc_slope_early_release_enabled:      {getattr(gc, 'cwc_slope_early_release_enabled', True)}")
    print(f"  cwc_slope_early_release_threshold:    {getattr(gc, 'cwc_slope_early_release_threshold', -0.01)}")
    print(f"  va_high_breakout_suppression_enabled: {getattr(gc, 'va_high_breakout_suppression_enabled', True)}")
    print(f"  climax_guard_enabled:                 {getattr(gc, 'climax_guard_enabled', True)}")
    print(f"  climax_rp_threshold:                  {getattr(gc, 'climax_rp_threshold', 0.95)}")
    print(f"  candle_guard_enabled:                 {getattr(gc, 'candle_guard_enabled', False)}")

    print(f"\n[Running universal_cross exit and cwvap_guard step-by-step]")
    # Step 1: universal_cross exit
    st_before = SavgolCTSExitState.from_int(delivery_bad_count)
    ucx_exit, ucx_state = exit_universal_cross(
        row=row, prev_row=prev_row, trade=trade,
        peak_close=peak_close, bars_held=exit_idx - trade_open_idx,
        state_val=delivery_bad_count,
        cfg=cfg.universal_cross,
        records=records, idx=exit_idx
    )
    print(f"  universal_cross exit reason: {ucx_exit}")
    print(f"  state after universal_cross: {SavgolCTSExitState.from_int(ucx_state)}")

    # Step 2: raw cwvap guard
    raw_res, raw_state = _apply_cwvap_guard_raw(
        row=row, trade=trade,
        res=ucx_exit, state_val=ucx_state,
        cfg=cfg, records=records, idx=exit_idx,
        tag=EntryTag.UNIVERSAL_CROSS.value
    )
    print(f"  _apply_cwvap_guard_raw result: {raw_res}, state: {SavgolCTSExitState.from_int(raw_state)}")

    # Step 3: va_high_breakout_suppression wrapper
    final_res, final_state = apply_cwvap_guard(
        row=row, trade=trade,
        res=ucx_exit, state_val=ucx_state,
        cfg=cfg, records=records, idx=exit_idx,
        tag=EntryTag.UNIVERSAL_CROSS.value
    )
    print(f"  apply_cwvap_guard final:       {final_res}, state: {SavgolCTSExitState.from_int(final_state)}")

    print(f"\n[VA High Breakout Suppression Logic]")
    bypass = [
        ExitReason.CWVAP_LOST, ExitReason.GAP_DOWN_LOSS, ExitReason.HARD_STOP,
        ExitReason.PNL_CAP, ExitReason.STRUCTURAL_CLIMAX,
        ExitReason.CANDLE_REJECTION, ExitReason.INSIDE_BAR_REJECTION,
    ]
    # raw_res is the output from _apply_cwvap_guard_raw, the input to the VA High check
    print(f"  raw_res (from _apply_cwvap_guard_raw): {raw_res}")
    print(f"  Is raw_res in bypass list?  {raw_res in bypass}")
    print(f"  close ({close:.2f}) > va_high ({va_high:.2f})? {not np.isnan(close) and not np.isnan(va_high) and close > va_high}")
    if raw_res is not None and raw_res not in bypass:
        if not np.isnan(close) and not np.isnan(va_high) and close > va_high:
            print(f"  => VA High suppression SHOULD HAVE suppressed the exit but final={final_res}")
        else:
            print(f"  => VA High suppression NOT active (close <= va_high), exit passes through")
    else:
        print(f"  => raw_res is None or in bypass, VA High check skipped")

    print("\n[CONCLUSION]")
    if st_cross_triggered:
        cts_below_st_for_bypass = not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st
        print(f"  ST_CROSS was triggered because:")
        print(f"    prev_cts ({prev_cts:.4f}) >= prev_cts_st ({prev_cts_st:.4f})  -> {prev_was_above}")
        print(f"    cts ({cts:.4f}) < cts_st ({cts_st:.4f})                   -> {now_below}")
        print(f"  CTS < cts_sell_threshold = {cts_below_st_for_bypass}  --> This is the bypass condition in cwvap_guard")
        print(f"  => ST_CROSS bypasses CWVAP suppression when cts < cts_sell_threshold")
        print(f"  => VA High suppression only applies AFTER _apply_cwvap_guard_raw,")
        print(f"     but ST_CROSS exits the raw guard via the bypass list (lines 50-65 of cwvap_guard.py)")
        print(f"  => Final exit: {final_res}")

if __name__ == "__main__":
    main()
