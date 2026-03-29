"""CWVAP Reclaim exit logic.

Two exit conditions, whichever fires first:
1. Slope cycle: cts_slope goes negative post-entry, then turns positive again.
2. CWVAP lost: close drops below CWVAP.

Empirically (NIFTY 500 walk-forward):
- Slope fires first: 75.5% WR, +5.45% avg (winners ride the full cycle).
- CWVAP fires first: 5.8% WR, -4.55% avg (losers cut early).
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
    """Exit on slope cycle completion or CWVAP lost, whichever first."""
    st = SavgolCTSExitState.from_int(state_val)

    # Bar-3 PnL stop: exit early if trade is underwater at bar N
    ecfg = cfg.cwvap_reclaim
    if ecfg.bar3_stop_enabled and trade is not None and bars_held == ecfg.bar3_stop_bar:
        close_now = row.get("close", np.nan)
        if not np.isnan(close_now) and trade.entry_price > 0:
            pnl = (close_now / trade.entry_price - 1) * 100
            if pnl < ecfg.bar3_stop_threshold:
                return ExitReason.BAR3_STOP, st.to_int()

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

    # Exit 1: CWVAP lost — close < CWVAP minus ATR-based tolerance
    close = row.get("close", np.nan)
    high = row.get("high", np.nan)
    cwvap = row.get("cwvap", np.nan)
    atr = row.get("atr_20", np.nan)
    if not np.isnan(close) and not np.isnan(cwvap) and cwvap > 0:
        tol = atr * cfg.cwvap_reclaim.cwvap_lost_atr_mult if not np.isnan(atr) else 0.0
        if close < cwvap - tol and high < cwvap:
            return ExitReason.CWVAP_LOST, st.to_int()

    return None, st.to_int()
