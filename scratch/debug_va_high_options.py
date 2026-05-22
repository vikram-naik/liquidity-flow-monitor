#!/usr/bin/env python3
"""
Explore 'touch-and-go' VA High scenarios:
For each bar in the ADANIPORTS trade, check how different
guard strategies would behave when close dips below VA High.
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

def typical(row):
    h, l, c = row.get("high", np.nan), row.get("low", np.nan), row.get("close", np.nan)
    return (h + l + c) / 3.0 if not any(np.isnan(x) for x in [h, l, c]) else np.nan

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

    # --- Option parameters ---
    ATR_MULT      = 0.5    # Option B: ATR tolerance band below VA High
    CONSEC_BARS   = 2      # Option C: N consecutive closes below VA High to exit
    PCT_TOLERANCE = 0.5    # Option D: % tolerance below VA High

    print(f"\nTrade open: {trade_open_row['date_str']} @ ₹{entry_price:.2f}")
    print(f"\nBar-by-bar: VA High guard options analysis")
    print(f"  Option A: high > va_high (intraday touch) → suppress release")
    print(f"  Option B: close < va_high - {ATR_MULT}×ATR → require meaningful breakdown")
    print(f"  Option C: require {CONSEC_BARS} consecutive closes < va_high before release")
    print(f"  Option D: close < va_high × (1 - {PCT_TOLERANCE}%) → pct tolerance band")
    print()

    cols = (
        f"{'Date':<12} | {'Close':>7} | {'High':>7} | {'VA High':>7} | {'ATR':>5} | "
        f"{'Typ':>7} | {'cl<vah':>6} | {'A:ok':>5} | {'B:ok':>5} | {'C:ok':>5} | {'D:ok':>5} | "
        f"{'CTS':>6} | {'PSZ':>5} | State"
    )
    print(cols)
    print("-" * 140)

    delivery_bad_count = 0
    peak_close = trade_open_row['close']
    cwvap_values = [trade_open_row.get('cwvap', np.nan)]
    consec_below = 0
    prev_below_va = False

    cur_idx = trade_open_idx + 1
    while cur_idx < len(records):
        row      = records[cur_idx]
        prev_row = records[cur_idx - 1]

        close   = row.get('close',   np.nan)
        high    = row.get('high',    np.nan)
        low     = row.get('low',     np.nan)
        cwvap   = row.get('cwvap',   np.nan)
        va_high = row.get('va_high', np.nan)
        atr     = row.get('atr_20',  np.nan)
        cts     = row.get('cts',     np.nan)
        psz     = row.get('price_slope_z', np.nan)
        tp      = typical(row)

        if close > peak_close:
            peak_close = close
        cwvap_values.append(cwvap)
        bars_held = cur_idx - trade_open_idx

        # Actual exit (current logic)
        exit_reason, new_state = signal.check_exit(
            row=row, prev_row=prev_row, trade=trade,
            peak_close=peak_close, bars_held=bars_held,
            delivery_bad_count=delivery_bad_count,
            cwvap_values=cwvap_values, cfg=cfg,
            records=records, idx=cur_idx
        )
        st = SavgolCTSExitState.from_int(new_state)

        # Is this a "climax trail" bar? (only relevant when climax_hit_above_va active)
        close_below_va = not np.isnan(close) and not np.isnan(va_high) and close < va_high
        if close_below_va:
            consec_below += 1
        else:
            consec_below = 0

        # --- Option A: high > va_high → price touched va_high intraday, don't release ---
        opt_a_suppress = (
            not np.isnan(high) and not np.isnan(va_high) and high > va_high
        )  # True = still safe, would suppress the release

        # --- Option B: ATR tolerance band ---
        if not np.isnan(close) and not np.isnan(va_high) and not np.isnan(atr):
            opt_b_suppress = close >= (va_high - ATR_MULT * atr)
        else:
            opt_b_suppress = False  # can't determine → conservative

        # --- Option C: N consecutive closes < va_high ---
        opt_c_suppress = consec_below < CONSEC_BARS   # True = don't release yet

        # --- Option D: pct tolerance ---
        if not np.isnan(close) and not np.isnan(va_high):
            opt_d_suppress = close >= va_high * (1 - PCT_TOLERANCE / 100.0)
        else:
            opt_d_suppress = False

        pnl = (close / entry_price - 1) * 100.0
        state_notes = []
        if exit_reason:
            state_notes.append(f"EXIT={exit_reason}")
        if st.climax_hit_above_va:
            state_notes.append("climax✓")
        if close_below_va:
            state_notes.append(f"↓VAH({consec_below})")

        print(
            f"{row['date_str']:<12} | {close:>7.2f} | {high:>7.2f} | {va_high:>7.2f} | "
            f"{atr:>5.1f} | {tp:>7.2f} | {str(close_below_va):>6} | "
            f"{str(opt_a_suppress):>5} | {str(opt_b_suppress):>5} | "
            f"{str(opt_c_suppress):>5} | {str(opt_d_suppress):>5} | "
            f"{cts:>6.3f} | {psz:>5.2f} | {', '.join(state_notes)}"
        )

        delivery_bad_count = new_state

        if exit_reason:
            print()
            print("=" * 140)
            print(f"\nEXIT on {row['date_str']} @ ₹{close:.2f}  PnL={pnl:+.2f}%  Reason={exit_reason}")
            print(f"\nOn exit bar — what each option would have done:")
            print(f"  Option A (intraday high > va_high):  suppress={'Yes, hold' if opt_a_suppress else 'No, exit'}")
            print(f"  Option B ({ATR_MULT}×ATR tolerance):          suppress={'Yes, hold' if opt_b_suppress else 'No, exit'}")
            print(f"  Option C ({CONSEC_BARS} consec bars):             suppress={'Yes, hold (only 1 bar below)' if opt_c_suppress else 'No, exit'}")
            print(f"  Option D ({PCT_TOLERANCE}% band):                  suppress={'Yes, hold' if opt_d_suppress else 'No, exit'}")
            print()

            print(f"\nKey metrics on exit bar:")
            print(f"  Open:          ₹{row.get('open',np.nan):.2f}  (opened {'ABOVE' if row.get('open',0) > va_high else 'below'} VA High ₹{va_high:.2f})")
            print(f"  High:          ₹{high:.2f}  (+{high-va_high:+.2f} vs VA High)")
            print(f"  Close:         ₹{close:.2f}  ({close-va_high:+.2f} vs VA High)")
            print(f"  ATR:           {atr:.2f}")
            print(f"  0.5×ATR band:  ₹{va_high - ATR_MULT*atr:.2f}  (close is {close-(va_high - ATR_MULT*atr):+.2f} from band)")
            print(f"  {PCT_TOLERANCE}% band:      ₹{va_high*(1-PCT_TOLERANCE/100):.2f}  (close is {close - va_high*(1-PCT_TOLERANCE/100):+.2f} from band)")
            print(f"  CTS:           {cts:.4f} (st={row.get('cts_sell_threshold',np.nan):.4f})")
            print(f"  PSZ:           {psz:.4f}")
            print(f"  CWC slope:     {row.get('cwc_slope',np.nan):.4f}")
            break

        cur_idx += 1

if __name__ == "__main__":
    main()
