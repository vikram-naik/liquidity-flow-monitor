from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import PrtSlopeZeroCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_prt_slope_zero_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: PrtSlopeZeroCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[str | None, int]:
    """Check exit conditions for PRT Slope Zero Cross path.

    Uses Two-Phase Glide:
    - Phase 1: Wait for CWVAP reclaim (close > cwvap) or timeout.
    - Phase 2: FAS trailing (exit if fas < exit_min or fas >= exit_max).
    """
    st = SavgolCTSExitState.from_int(state_val)

    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    fas = row.get("fas", np.nan)
    va_high = row.get("va_high", np.nan)
    if any(np.isnan(x) for x in [close_now, cwvap, fas, va_high]):
        return None, st.to_int()

    # 1. Phase 1: Waiting for CWVAP reclaim
    if not st.fas_crossed_zero:
        if fas > 0:
            st.fas_crossed_zero = True
        elif bars_held >= cfg.cwvap_timeout_bars:
            return ExitReason.RECLAIM_TIMEOUT, st.to_int()
        return None, st.to_int()
    
    # 2. Phase 2: FAS trailing
    if (fas < cfg.fas_exit_min):
        return ExitReason.ST_CROSS, st.to_int()

    # 3 Phase 2.1: FAS trailing with state, 
    # if fas touched max, but we are above va_high, trail through and exit when we close below va_high
    if fas >= cfg.fas_exit_max:
        if not(st.prt_exit_suppressed):
            if va_high < close_now:
                st.prt_exit_suppressed = True
            else:
                return ExitReason.ST_CROSS, st.to_int()
        else:
           if va_high > close_now:
               return ExitReason.ST_CROSS, st.to_int()
    if st.prt_exit_suppressed:
        if cwvap > close_now:
            return ExitReason.ST_CROSS, st.to_int()

    return None, st.to_int()
