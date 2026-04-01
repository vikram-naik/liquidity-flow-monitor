"""Slope Bottom exit logic.

Pure slope-based exit: wait for cts_slope to cross above zero (phase 1),
then exit when it drops back below zero (phase 2). The full slope cycle
captures the reversal and exits when momentum fades.

Uses bit 5 (slope_went_negative) from the state bitfield — repurposed here
to track whether slope has crossed above zero (phase 1 complete).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_slope_bottom(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit when cts_slope completes its zero-cross cycle.

    Phase 1: slope starts deeply negative at entry. Wait for it to cross
             above zero. Track this via slope_went_negative bit (repurposed
             as 'slope_crossed_zero').
    Phase 2: once slope has been above zero, exit when it drops back below.
    """
    st = SavgolCTSExitState.from_int(state_val)
    ecfg = cfg.slope_bottom

    cs = row.get("cts_slope", np.nan)
    if np.isnan(cs):
        return None, st.to_int()

    # --- PnL Cap (Highest Priority for Mean Reversion) ---
    # Take profit when trade PnL% >= cap.
    # Suppressed while price is above VA high (breakout territory — let it run).
    if ecfg.pnl_cap_enabled and trade is not None and trade.entry_price > 0:
        close_now = row.get("close", np.nan)
        va_high = row.get("va_high", np.nan)
        if not np.isnan(close_now):
            above_va = not np.isnan(va_high) and va_high > 0 and close_now > va_high
            if not above_va:
                pnl = (close_now / trade.entry_price - 1) * 100
                if pnl >= ecfg.pnl_cap_pct:
                    return ExitReason.PNL_CAP, st.to_int()

    # --- MFE-based Trailing Stop ---
    # Once running MFE exceeds activation threshold, lock a fraction
    # of peak PnL as the floor. Exit if current PnL drops below floor.
    if ecfg.trail_enabled and trade is not None and trade.entry_price > 0:
        close_now = row.get("close", np.nan)
        if not np.isnan(close_now) and peak_close > 0:
            running_mfe = (peak_close / trade.entry_price - 1) * 100
            if running_mfe >= ecfg.trail_activation_pct:
                trail_floor = running_mfe * ecfg.trail_lock_ratio
                current_pnl = (close_now / trade.entry_price - 1) * 100
                if current_pnl < trail_floor:
                    return ExitReason.TRAIL_STOP, st.to_int()

    # slope_went_negative bit repurposed: True = slope has crossed above zero
    slope_crossed_zero = st.slope_went_negative

    if not slope_crossed_zero:
        # Phase 1: waiting for slope to cross above zero
        if cs > 0:
            st.slope_went_negative = True  # mark phase 1 complete
        return None, st.to_int()
    # else:
        # Phase 1.5: slope has been positive, and price is still below CWVAP exit.
        # close = row.get("close", np.nan)
        # cwvap = row.get("cwvap", np.nan)
        # if np.isnan(close) or np.isnan(cwvap):
        #     return None, st.to_int()
        # if close < cwvap:
        #     return ExitReason.CWVAP_LOST, st.to_int()
        

    # Phase 2: slope has been positive, exit when it goes below threshold
    if cs < 0:
        return ExitReason.SLOPE_CYCLE, st.to_int()


    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    
    if np.isnan(close) or np.isnan(cwvap):
        return None, st.to_int()

    # Exit if CWVAP is lost AFTER it was reclaimed (managed by orchestrator)
    if st.price_above_cwvap and close < cwvap:
        return ExitReason.CWVAP_LOST, st.to_int()


    return None, st.to_int()
