"""
FAS Zero Cross Exit — Pure CTS Trailing Variant.
Strictly follows the Path 13 (CTS Accel Cross) logic:
1. Hard Stop (8%)
2. CWVAP Rejection Guard (Limit 5)
3. Standard CTS Cross-down
4. Bare-Touch Persistence (Tolerance 0.01)
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import FasZeroCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_fas_cts_trailing(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: FasZeroCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[ExitReason | None, int]:
    """
    Pure CTS Trailing Exit for FAS-Zero-Cross.
    Removes all FAS-based exit triggers.
    """
    st = SavgolCTSExitState.from_int(state_val)

    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop (8% default)
    if pnl_pct <= -8.0:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. CWVAP Rejection Guard (Limit 5)
    high = row.get("high", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if not np.isnan(high) and not np.isnan(cwvap) and cwvap > 0:
        if high > cwvap and close_now < cwvap:
            st.cwf_count += 1
            if st.cwf_count >= 5: 
                return ExitReason.CWVAP_REJECTION, st.to_int()
        elif close_now >= cwvap:
            st.cwf_count = 0

    # 3. CTS Trailing Logic
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    prev_cts_st = prev_row.get("cts_sell_threshold", np.nan)

    if any(np.isnan(x) for x in [cts, prev_cts, cts_st, prev_cts_st]):
        return None, st.to_int()

    # Condition A: Standard CTS cross below ST from above
    is_standard_cross = prev_cts >= prev_cts_st and cts < cts_st
    if is_standard_cross:
        return ExitReason.ST_CROSS, st.to_int()

    # Condition B: Bare-Touch Persistence (Tolerance 0.01)
    bare_touch_tolerance = 0.01
    
    if not st.cts_near_miss:
        # Was yesterday a near-miss?
        is_near_miss = (prev_cts_st - bare_touch_tolerance) <= prev_cts < prev_cts_st
        if is_near_miss:
            st.cts_near_miss = True
    
    if st.cts_near_miss:
        if cts >= cts_st:
            st.cts_near_miss = False
        elif cts < prev_cts:
            return ExitReason.CTS_BARE_TOUCH, st.to_int()

    return None, st.to_int()
