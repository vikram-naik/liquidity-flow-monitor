from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import UniversalCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def exit_universal_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: UniversalCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[ExitReason | None, int]:
    """Exit condition for Universal Cross path.
    
    Exit by trailing cts, such that cts crosses cts_sell_threshold from above.
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

    # Check if this bar qualifies for panic exit suppression (high volume with physical gap-down)
    is_panic_bar = False
    if cfg.panic_exit_suppression_enabled and prev_row:
        high = row.get("high", np.nan)
        prev_low = prev_row.get("low", np.nan)
        rdv = row.get("rdv", 0.0)
        if not np.isnan(high) and not np.isnan(prev_low):
            if high < prev_low and rdv >= cfg.panic_exit_rdv_threshold:
                is_panic_bar = True

    # 0. pnl cap
    if cfg.pnl_cap_enabled and pnl_pct >= cfg.pnl_cap_threshold:
        return ExitReason.PNL_CAP, st.to_int()


    # 1. Hard Stop
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. Gap Down (Loss Prevention)
    if cfg.gap_down_enabled and pnl_pct < 0:
        high = row.get("high", np.nan)
        prev_low = prev_row.get("low", np.nan)
        atr = row.get("atr_20", np.nan)
        if not any(np.isnan(x) for x in [high, prev_low, atr]):
            if high < prev_low:
                gap_size = prev_low - high
                if gap_size > (atr * cfg.gap_down_atr_mult):
                    return ExitReason.GAP_DOWN_LOSS, st.to_int()

    # 3. Negative PnL Timeout (Loss Prevention)
    if cfg.negative_pnl_timeout_enabled and bars_held >= cfg.negative_pnl_timeout_days and pnl_pct < 0:
        return ExitReason.NEGATIVE_PNL_TIMEOUT, st.to_int()

    prt = row.get("prt", np.nan)
    prev_prt = prev_row.get("prt", np.nan)
    prt_st = row.get("prt_sell_threshold", np.nan)
    prt_bt = row.get("prt_buy_threshold", np.nan)
    prev_prt_st = prev_row.get("prt_sell_threshold", np.nan)

    prt_slope = row.get("prt_slope", np.nan)

    # 2. PRT Trail (Exit on cross down through ST)
    if getattr(cfg, "prt_st_cross_enabled", True):
        if not any(np.isnan(x) for x in [prt, prev_prt, prt_st, prev_prt_st]):
            if prev_prt >= prev_prt_st and prt < prt_st:
                if is_panic_bar:
                    return None, st.to_int()
                return ExitReason.PRT_ST_CROSS, st.to_int()

    # 3. CTS Trail (Exit on cross down through ST)
    if getattr(cfg, "cts_st_cross_enabled", True):
        cts = row.get("cts", np.nan)
        prev_cts = prev_row.get("cts", np.nan)
        cts_st = row.get("cts_sell_threshold", np.nan)
        prev_cts_st = prev_row.get("cts_sell_threshold", np.nan)
        if not any(np.isnan(x) for x in [cts, prev_cts, cts_st, prev_cts_st]):
            if prev_cts >= prev_cts_st and cts < cts_st:
                if is_panic_bar:
                    return None, st.to_int()
                return ExitReason.ST_CROSS, st.to_int()

    # 4. CWC Slope Negative Exit
    if getattr(cfg, "cwc_slope_neg_exit_enabled", False):
        cwc_slope = row.get("cwc_slope", np.nan)
        if not np.isnan(cwc_slope) and cwc_slope < 0:
            if is_panic_bar:
                return None, st.to_int()
            return ExitReason.CWVAP_EXHAUSTION, st.to_int()

    # 5. CWC Negative Exit
    if getattr(cfg, "cwc_neg_exit_enabled", False):
        cwc = row.get("cwc", np.nan)
        if not np.isnan(cwc) and cwc < 0:
            if is_panic_bar:
                return None, st.to_int()
            return ExitReason.FAS_FLOOR, st.to_int()
            
    return None, st.to_int()
