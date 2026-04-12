from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import AccelZeroCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_accel_zero_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: AccelZeroCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[str | None, int]:
    """Check exit conditions for Accel Zero Cross path.

    Uses Two-Phase Glide:
    - Phase 1 (PSZ trail): Wait for PSZ to cross above zero (become positive),
      then when PSZ drops back below zero, switch to Phase 2.
    - Phase 2 (CTS trail): Hold until CTS crosses sell threshold → ST_CROSS.
    - Safety Nets: Hard stop, PnL Cap, and MFE Trailing Stop.
    
    The CWVAP Guard then runs post-exit, acting as a trend-following
    trailing stop if the price breaks out above CWVAP.
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

    # 1. Hard Stop (safety net)
    if getattr(cfg, "hard_stop_enabled", False) and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap (active in both phases)
    if getattr(cfg, "pnl_cap_enabled", False) and pnl_pct >= cfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    # 3. MFE-based Trailing Stop
    if getattr(cfg, "trail_enabled", False) and peak_close > 0:
        running_mfe = (peak_close / trade.entry_price - 1) * 100.0
        if running_mfe >= cfg.trail_activation_pct:
            trail_floor = running_mfe * cfg.trail_lock_ratio
            if pnl_pct < trail_floor:
                return ExitReason.TRAIL_STOP, st.to_int()

    psz = row.get("price_slope_z", np.nan)
    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    prev_cts = prev_row.get("cts", np.nan)

    # 4. Early Bailout: If CTS crosses below zero (trend collapse)
    # This prevents riding obvious losers to the hard stop if they never launch.
    if not np.isnan(cts) and not np.isnan(prev_cts):
        if prev_cts > 0 and cts <= 0:
            return ExitReason.CTS_ZERO_DOWN, st.to_int()

    # 5. Phase 2: CTS trail (psz_was_above repurposed as trailing_cts flag)
    if st.psz_was_above:
        if not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st:
            return ExitReason.ST_CROSS, st.to_int()
        return None, st.to_int()

    # 6. Phase 1: PSZ zero-cross cycle
    if not np.isnan(psz):
        # price_above_cwvap repurposed as psz_was_positive flag
        if not st.price_above_cwvap:
            if psz > 0:
                st.price_above_cwvap = True
        else:
            if psz < 0:
                # PSZ cycle done — check CTS before exiting
                if not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st:
                    # CTS still below sell threshold — switch to Phase 2
                    st.psz_was_above = True
                else:
                    return ExitReason.PSZ_GLIDE, st.to_int()

    return None, st.to_int()
