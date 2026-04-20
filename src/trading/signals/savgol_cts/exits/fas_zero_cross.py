from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import FasZeroCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def exit_fas_zero_cross(
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
    FAS-Zero-Cross Persistent Dual-Exhaustion Logic.
    
    1. Recovery Gate: Wait for turn (CTS > 0 or FAS > 0).
    2. Exhaustion Latches: Lock in exhaustion states when crossovers occur.
    3. Climax Override: Exit if FAS >= 1.0.
    4. Alpha-Release Guard: If PnL > cfg.alpha_release_threshold_pct AND one engine fails, exit on CWVAP loss.
    5. Union Exit: Final trigger when both engines are exhausted.
    """
    st = SavgolCTSExitState.from_int(state_val)
    
    fas = row.get("fas", np.nan)
    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    
    prev_fas = prev_row.get("fas", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    prev_cts_st = prev_row.get("cts_sell_threshold", np.nan)

    # 1. Recovery Gate
    if not st.recovery_passed:
        if (not np.isnan(cts) and cts > 0) or (not np.isnan(fas) and fas > 0):
            st.recovery_passed = True
        else:
            return None, st.to_int()

    # 2. Exhaustion Definitions (Strict Crossovers)
    # CTS Exhaustion
    if not st.cts_exhausted:
        zero_cross = not np.isnan(prev_cts) and not np.isnan(cts) and prev_cts > 0 and cts <= 0
        threshold_cross = (
            not np.isnan(prev_cts) and not np.isnan(prev_cts_st) and 
            not np.isnan(cts) and not np.isnan(cts_st) and 
            prev_cts > prev_cts_st and cts <= (cts_st + cfg.cts_st_tolerance)
        )
        if zero_cross or threshold_cross:
            st.cts_exhausted = True

    # FAS Exhaustion
    if not st.fas_exhausted:
        floor_breach = not np.isnan(prev_fas) and not np.isnan(fas) and prev_fas >= -0.1 and fas < -0.1
        climax_latch = not np.isnan(fas) and fas >= (cfg.fas_climax_threshold - cfg.fas_climax_tolerance)
        if floor_breach or climax_latch:
            st.fas_exhausted = True

    # 3. The Climax Kill-Switch (Override)
    if not np.isnan(fas) and fas >= (cfg.fas_climax_threshold - cfg.fas_climax_tolerance):
        st.fas_exhausted = True
        return ExitReason.FAS_CLIMAX, st.to_int()

    # 4. Smart Alpha-Release (Contextual Guard)
    current_pnl = 0.0
    if trade and trade.entry_price > 0 and not np.isnan(close):
        current_pnl = (close / trade.entry_price - 1) * 100.0

    if current_pnl > 2.5 and (st.cts_exhausted or st.fas_exhausted):
        if not np.isnan(close) and not np.isnan(cwvap) and close < cwvap:
            return ExitReason.ALPHA_RELEASE_EXIT, st.to_int()

    # 5. The Union Exit
    if st.cts_exhausted and st.fas_exhausted:
        return ExitReason.DUAL_ENGINE_FAILURE, st.to_int()

    return None, st.to_int()

