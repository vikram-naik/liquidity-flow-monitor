"""CWVAP Reclaim exit logic.

Exit conditions in priority order:
1. Bar-3 PnL stop — cut early losers.
2. Bar-5 breakeven gate — cut flat drifters.
3. PnL cap — take profit.
4. LH+LL trend break — exit on confirmed price structure deterioration.
5. CWVAP lost — close drops below CWVAP.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_cwvap_reclaim(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit on bar stops, PnL cap, LH+LL break, or CWVAP lost."""
    st = SavgolCTSExitState.from_int(state_val)

    # Bar-3 PnL stop: exit early if trade is underwater at bar N
    ecfg = cfg.cwvap_reclaim
    if ecfg.bar3_stop_enabled and trade is not None and bars_held == ecfg.bar3_stop_bar:
        close_now = row.get("close", np.nan)
        if not np.isnan(close_now) and trade.entry_price > 0:
            pnl = (close_now / trade.entry_price - 1) * 100
            if pnl < ecfg.bar3_stop_threshold:
                return ExitReason.BAR3_STOP, st.to_int()

    # Bar-5 breakeven gate: exit if trade still negative at bar N
    if ecfg.bar5_stop_enabled and trade is not None and bars_held == ecfg.bar5_stop_bar:
        close_now = row.get("close", np.nan)
        if not np.isnan(close_now) and trade.entry_price > 0:
            pnl = (close_now / trade.entry_price - 1) * 100
            if pnl < ecfg.bar5_stop_threshold:
                return ExitReason.BAR5_STOP, st.to_int()

    # PnL cap: take profit when trade PnL% >= cap
    # Suppressed while price is above VA high (breakout territory — let it run)
    if ecfg.pnl_cap_enabled and trade is not None and trade.entry_price > 0:
        close_now = row.get("close", np.nan)
        va_high = row.get("va_high", np.nan)
        if not np.isnan(close_now):
            above_va = not np.isnan(va_high) and va_high > 0 and close_now > va_high
            if not above_va:
                pnl = (close_now / trade.entry_price - 1) * 100
                if pnl >= ecfg.pnl_cap_pct:
                    return ExitReason.PNL_CAP, st.to_int()

    # LH+LL trend break: confirmed lower-high + lower-low after price peak
    if ecfg.lh_ll_enabled and records is not None and trade is not None:
        lh_ll = _check_lh_ll(records, trade.entry_idx, idx, ecfg.lh_ll_pivot_lookback)
        if lh_ll:
            return ExitReason.LH_LL_BREAK, st.to_int()

    # CWVAP lost — close < CWVAP minus ATR-based tolerance
    close = row.get("close", np.nan)
    high = row.get("high", np.nan)
    cwvap = row.get("cwvap", np.nan)
    atr = row.get("atr_20", np.nan)
    if not np.isnan(close) and not np.isnan(cwvap) and cwvap > 0:
        tol = atr * cfg.cwvap_reclaim.cwvap_lost_atr_mult if not np.isnan(atr) else 0.0
        if close < cwvap - tol and high < cwvap:
            return ExitReason.CWVAP_LOST, st.to_int()

    return None, st.to_int()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_lh_ll(
    records: list[dict], entry_idx: int, current_idx: int, lookback: int,
) -> bool:
    """Detect confirmed LH+LL pattern in the trade's price bars.

    A swing high at bar i is confirmed when ``lookback`` subsequent bars
    all have lower highs.  Same logic (inverted) for swing lows.

    Returns True when:
    - At least two confirmed swing highs exist and the latest < previous (LH).
    - At least two confirmed swing lows exist and the latest < previous (LL).
    - Both the LH and LL occurred after the trade's highest close.
    """
    # Need enough bars: entry + at least 2*lookback + 2 for two pivots
    start = entry_idx + 1  # first bar of trade
    end = current_idx       # current bar (inclusive for reads, exclusive for pivot confirmation)

    if end - start < 2 * lookback + 3:
        return False

    # Collect highs/lows from trade bars
    highs = []
    lows = []
    closes = []
    for j in range(start, end + 1):
        r = records[j]
        highs.append(r.get("high", r.get("close", np.nan)))
        lows.append(r.get("low", r.get("close", np.nan)))
        closes.append(r.get("close", np.nan))

    n = len(highs)

    # Find peak close bar (relative to trade start)
    peak_bar = 0
    peak_val = closes[0]
    for i in range(1, n):
        if not np.isnan(closes[i]) and closes[i] > peak_val:
            peak_val = closes[i]
            peak_bar = i

    # Find confirmed swing highs (bar i is confirmed if i + lookback <= last bar)
    # A pivot at bar i requires lookback bars before AND after with lower values
    swing_highs = []  # (bar_index, value)
    for i in range(lookback, n - lookback):
        is_pivot = True
        for j in range(1, lookback + 1):
            if highs[i] < highs[i - j] or highs[i] < highs[i + j]:
                is_pivot = False
                break
        if is_pivot:
            swing_highs.append((i, highs[i]))

    # Find confirmed swing lows
    swing_lows = []
    for i in range(lookback, n - lookback):
        is_pivot = True
        for j in range(1, lookback + 1):
            if lows[i] > lows[i - j] or lows[i] > lows[i + j]:
                is_pivot = False
                break
        if is_pivot:
            swing_lows.append((i, lows[i]))

    # Detect first lower-high after peak
    has_lh = False
    for i in range(1, len(swing_highs)):
        bar_i, val_i = swing_highs[i]
        _, val_prev = swing_highs[i - 1]
        if bar_i > peak_bar and val_i < val_prev:
            has_lh = True
            break

    if not has_lh:
        return False

    # Detect first lower-low after peak
    for i in range(1, len(swing_lows)):
        bar_i, val_i = swing_lows[i]
        _, val_prev = swing_lows[i - 1]
        if bar_i > peak_bar and val_i < val_prev:
            return True  # LH + LL confirmed

    return False
