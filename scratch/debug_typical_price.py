#!/usr/bin/env python3
"""
Check what happens if we use typical price = (H+L+C)/3 instead of close
for the va_high comparisons in cwvap_guard.py.

Compares close vs typical price against va_high for every bar in the trade,
and shows where the decision would differ.
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
from src.trading.signals.savgol_cts.exits.cwvap_guard import _apply_cwvap_guard_raw, apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross


def typical(row):
    h = row.get("high", np.nan)
    l = row.get("low", np.nan)
    c = row.get("close", np.nan)
    if any(np.isnan(x) for x in [h, l, c]):
        return np.nan
    return (h + l + c) / 3.0


def main():
    symbol = "ADANIPORTS"
    entry_signal_date = "2026-04-01"

    print(f"Loading DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    ledger = result.ledger
    ledger['date_str'] = pd.to_datetime(ledger['date']).dt.strftime('%Y-%m-%d')
    records = ledger.to_dict('records')

    entry_idx = next((i for i, r in enumerate(records) if r['date_str'] == entry_signal_date), None)
    trade_open_idx = entry_idx + 1
    trade_open_row = records[trade_open_idx]
    entry_price = trade_open_row['open']

    trade = Trade(
        symbol=symbol,
        entry_date=trade_open_row['date_str'],
        entry_price=entry_price,
        entry_idx=trade_open_idx,
        atr_at_entry=trade_open_row.get('atr_20', 0.0),
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    print(f"\nTrade open: {trade_open_row['date_str']} @ ₹{entry_price:.2f}")
    print(f"\nBar-by-bar comparison: Close vs Typical Price against VA High")
    print("=" * 130)
    hdr = (
        f"{'Date':<12} | {'Close':>8} | {'High':>8} | {'Low':>8} | "
        f"{'Typical':>8} | {'VA High':>8} | {'Close>VAH':>10} | {'Typ>VAH':>8} | "
        f"{'DIFF':>10} | Notes"
    )
    print(hdr)
    print("-" * 130)

    delivery_bad_count = 0
    peak_close = trade_open_row['close']
    cwvap_values = [trade_open_row.get('cwvap', np.nan)]

    exit_bar_close = None   # record actual exit bar data
    first_exit_typical = None
    first_exit_close = None

    cur_idx = trade_open_idx + 1
    while cur_idx < len(records):
        row = records[cur_idx]
        prev_row = records[cur_idx - 1]

        close   = row.get('close', np.nan)
        high    = row.get('high', np.nan)
        low     = row.get('low', np.nan)
        cwvap   = row.get('cwvap', np.nan)
        va_high = row.get('va_high', np.nan)
        tp      = typical(row)

        close_above = not np.isnan(close) and not np.isnan(va_high) and close > va_high
        tp_above    = not np.isnan(tp) and not np.isnan(va_high) and tp > va_high
        diff = tp - va_high if not np.isnan(tp) and not np.isnan(va_high) else np.nan

        # Run the actual exit check (with close) to track state
        if close > peak_close:
            peak_close = close
        cwvap_values.append(cwvap)
        bars_held = cur_idx - trade_open_idx

        exit_reason, new_state = signal.check_exit(
            row=row, prev_row=prev_row, trade=trade,
            peak_close=peak_close, bars_held=bars_held,
            delivery_bad_count=delivery_bad_count,
            cwvap_values=cwvap_values, cfg=cfg,
            records=records, idx=cur_idx
        )
        st = SavgolCTSExitState.from_int(new_state)

        notes = []
        if exit_reason:
            notes.append(f"EXIT={exit_reason}")
        if st.climax_hit_above_va:
            notes.append("climax_above_va=True")
        if close_above != tp_above:
            notes.append("*** DECISION DIFFERS ***")

        # Also: simulate what _apply_cwvap_guard_raw would decide if we patched
        # the VA High check to use typical price in the wrapper
        # Run raw exit
        ucx_exit, ucx_state = exit_universal_cross(
            row=row, prev_row=prev_row, trade=trade,
            peak_close=peak_close, bars_held=bars_held,
            state_val=delivery_bad_count,
            cfg=cfg.universal_cross,
            records=records, idx=cur_idx
        )
        raw_res, raw_state = _apply_cwvap_guard_raw(
            row=row, trade=trade, res=ucx_exit, state_val=ucx_state,
            cfg=cfg, records=records, idx=cur_idx,
            tag=EntryTag.UNIVERSAL_CROSS.value
        )

        # Simulate VA High wrapper with typical price
        if raw_res is not None:
            bypass = [
                ExitReason.CWVAP_LOST, ExitReason.GAP_DOWN_LOSS, ExitReason.HARD_STOP,
                ExitReason.PNL_CAP, ExitReason.STRUCTURAL_CLIMAX,
                ExitReason.CANDLE_REJECTION, ExitReason.INSIDE_BAR_REJECTION,
            ]
            if raw_res not in bypass:
                # With TYPICAL price
                if not np.isnan(tp) and not np.isnan(va_high) and tp > va_high:
                    notes.append("TP: VA-High would suppress")
                else:
                    notes.append("TP: VA-High would NOT suppress")
                # With CLOSE price
                if not np.isnan(close) and not np.isnan(va_high) and close > va_high:
                    notes.append("CL: VA-High would suppress")
                else:
                    notes.append("CL: VA-High would NOT suppress")

        print(
            f"{row['date_str']:<12} | {close:>8.2f} | {high:>8.2f} | {low:>8.2f} | "
            f"{tp:>8.2f} | {va_high:>8.2f} | {str(close_above):>10} | {str(tp_above):>8} | "
            f"{diff:>+10.2f} | {', '.join(notes)}"
        )

        delivery_bad_count = new_state

        if exit_reason:
            if first_exit_close is None:
                first_exit_close = (row['date_str'], exit_reason, close, va_high, tp)
            break

        cur_idx += 1

    print("\n")
    print("=" * 130)
    print("\n[Climax VA-High Trail release: close < va_high vs typical < va_high]")
    print("This is the condition in _apply_cwvap_guard_raw line 155:")
    print("  if st.climax_hit_above_va and close < va_high  => exit STRUCTURAL_CLIMAX")
    print()
    if first_exit_close:
        date, reason, close, va_high, tp = first_exit_close
        print(f"  Exit bar {date}:")
        print(f"    Close:         {close:.2f}")
        print(f"    Typical:       {tp:.2f}")
        print(f"    VA High:       {va_high:.2f}")
        print(f"    close < va_high?   {close < va_high}  (triggers climax trail release)")
        print(f"    typical < va_high? {tp < va_high}  (would trigger with typical price?)")
        if (close < va_high) != (tp < va_high):
            print(f"\n  *** Using typical price CHANGES the climax trail release decision! ***")
        else:
            print(f"\n  Both agree on climax trail release decision.")

    print()
    print("[VA High Breakout Suppression: close > va_high vs typical > va_high]")
    print("This is the condition in apply_cwvap_guard (wrapper) lines 271-277:")
    print("  if close > va_high => suppress exit")
    print()
    if first_exit_close:
        date, reason, close, va_high, tp = first_exit_close
        print(f"  Exit bar {date}:")
        print(f"    Close:         {close:.2f}")
        print(f"    Typical:       {tp:.2f}")
        print(f"    VA High:       {va_high:.2f}")
        print(f"    close > va_high?   {close > va_high}  (current: would suppress ST_CROSS?)")
        print(f"    typical > va_high? {tp > va_high}  (proposed: would suppress ST_CROSS?)")
        if close > va_high != tp > va_high:
            print(f"\n  *** Using typical price CHANGES the VA High suppression decision! ***")
        else:
            print(f"\n  Both agree on VA High suppression decision (no change in outcome).")


if __name__ == "__main__":
    main()
