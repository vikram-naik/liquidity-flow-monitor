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
    prev_prt = prev_row.get("prt", np.nan) if prev_row else np.nan
    prt_st = row.get("prt_sell_threshold", np.nan)
    prt_bt = row.get("prt_buy_threshold", np.nan)
    prev_prt_st = prev_row.get("prt_sell_threshold", np.nan) if prev_row else np.nan

    prt_slope = row.get("prt_slope", np.nan)

    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)

    # CTS Near-Miss Detection
    if getattr(cfg, "cts_near_miss_exit_enabled", True):
        if not any(np.isnan(x) for x in [cts, cts_st]):
            if cts >= cts_st:
                st.cts_reached_st = True
                st.cts_near_miss = False
            else:
                prev_cts = prev_row.get("cts", np.nan) if prev_row else np.nan
                prev_cts_st = prev_row.get("cts_sell_threshold", np.nan) if prev_row else np.nan
                if not any(np.isnan(x) for x in [prev_cts, prev_cts_st]) and prev_cts >= prev_cts_st:
                    st.cts_reached_st = True
            
                # If CTS has ever reached ST, it cannot be a near-miss setup
                if st.cts_reached_st:
                    st.cts_near_miss = False
                else:
                    gap = getattr(cfg, "cts_near_miss_gap", 0.10)
                    if 0 < (cts_st - cts) <= gap:
                        st.cts_near_miss = True

    exit_reason = None

    # 2. PRT Trail (Exit on cross down through ST)
    if getattr(cfg, "prt_st_cross_enabled", True):
        if not any(np.isnan(x) for x in [prt, prev_prt, prt_st, prev_prt_st]):
            if prev_prt >= prev_prt_st and prt < prt_st:
                cts_above_st = not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st
                if not cts_above_st and not is_panic_bar:
                    exit_reason = ExitReason.PRT_ST_CROSS

    # 3. CTS Trail (Exit on cross down through ST)
    if not exit_reason and getattr(cfg, "cts_st_cross_enabled", True):
        prev_cts = prev_row.get("cts", np.nan) if prev_row else np.nan
        prev_cts_st = prev_row.get("cts_sell_threshold", np.nan) if prev_row else np.nan
        if not any(np.isnan(x) for x in [cts, prev_cts, cts_st, prev_cts_st]):
            if prev_cts >= prev_cts_st and cts < cts_st:
                if not is_panic_bar:
                    # Determine if this is a momentum trade setup
                    is_momentum = False
                    if trade.entry_tag in [
                        "SavgolCTS Flow-Momentum",
                        "SavgolCTS Coherent-Pullback",
                        "SavgolCTS CDVL-CTS"
                    ]:
                        is_momentum = True
                    elif trade.entry_tag == "SavgolCTS Custom-Bayesian":
                        if trade.regime_at_entry not in ["downtrend", "notrend"]:
                            is_momentum = True

                    if is_momentum and bars_held <= 1:
                        # Suppress first ST cross over exit for momentum setups as we enter very near ST
                        pass
                    else:
                        exit_reason = ExitReason.ST_CROSS

    # 4. CWC Slope Negative Exit
    if not exit_reason and getattr(cfg, "cwc_slope_neg_exit_enabled", False):
        cwc_slope = row.get("cwc_slope", np.nan)
        if not np.isnan(cwc_slope) and cwc_slope < 0:
            if not is_panic_bar:
                exit_reason = ExitReason.CWVAP_EXHAUSTION

    # 6. CTS Near-Miss Rollover Check
    if not exit_reason and getattr(cfg, "cts_near_miss_exit_enabled", True) and st.cts_near_miss:
        rollover_level = getattr(cfg, "cts_near_miss_rollover_level", 0.50)
        prev_cts = prev_row.get("cts", np.nan) if prev_row else np.nan
        if not any(np.isnan(x) for x in [cts, prev_cts]) and cts < prev_cts and cts < rollover_level:
            if not is_panic_bar:
                exit_reason = ExitReason.CTS_NEAR_MISS_ROLLOVER

    if exit_reason:
        return exit_reason, st.to_int()

    return None, st.to_int()
