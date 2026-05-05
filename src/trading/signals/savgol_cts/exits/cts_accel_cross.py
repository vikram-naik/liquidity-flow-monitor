"""Path 13: CTS Accel Cross exit logic — Standard + Bare-Touch.

Refined from NIFTY 50 study (May 2026).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import CtsAccelCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_cts_accel_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: CtsAccelCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[ExitReason | None, int]:
    """Exit condition for Path 13 (CTS Accel Cross)."""
    st = SavgolCTSExitState.from_int(state_val)

    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap
    if cfg.pnl_cap_enabled and pnl_pct >= cfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    prev_cts_st = prev_row.get("cts_sell_threshold", np.nan)

    if any(np.isnan(x) for x in [cts, prev_cts, cts_st, prev_cts_st]):
        return None, st.to_int()

    # --- EXIT CONDITIONS ---

    # 1. CWVAP Rejection Guard (Cumulative)
    # Exits if total rejections (pierce but close below) reach the limit.
    # Reset ONLY if we successfully reclaim CWVAP on a closing basis.
    high = row.get("high", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if not np.isnan(high) and not np.isnan(cwvap) and cwvap > 0:
        if high > cwvap and close_now < cwvap:
            st.cwf_count += 1
            if st.cwf_count >= cfg.cwvap_rejection_limit:
                return ExitReason.CWVAP_REJECTION, st.to_int()
        elif close_now >= cwvap:
            st.cwf_count = 0

    # Condition A: Standard CTS cross below ST from above
    # We apply the tolerance here to prevent "pixel-perfect" exits.
    # A true cross is now defined as dropping significantly below the ceiling.
    is_standard_cross = prev_cts >= prev_cts_st and cts < (cts_st - cfg.bare_touch_tolerance)
    if is_standard_cross:
        return ExitReason.ST_CROSS, st.to_int()

    # Condition B: Bare-Touch Persistence (HCLTECH/NESTLEIND case)
    # Latch the 'near miss' state if we are hugging the ceiling (within tolerance).
    is_near_miss = (cts_st - cfg.bare_touch_tolerance) <= cts < cts_st
    if is_near_miss:
        st.cts_near_miss = True
    
    # If we are in 'near-miss' mode, exit if institutional commitment starts fading significantly.
    if st.cts_near_miss:
        # If we finally cross WELL ABOVE ST, clear the near-miss latch
        if cts >= (cts_st + cfg.bare_touch_tolerance):
            st.cts_near_miss = False
        # Otherwise, if we are falling away from the ceiling by more than the tolerance, trigger near-miss exit
        elif cts < (prev_cts - cfg.bare_touch_tolerance):
            return ExitReason.CTS_BARE_TOUCH, st.to_int()

    return None, st.to_int()
