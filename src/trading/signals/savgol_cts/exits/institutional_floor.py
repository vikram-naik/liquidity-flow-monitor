"""Institutional Floor exit logic — CTS Trail Cap (two-phase).

Phase 1 (PSZ trail): Wait for PSZ to cross above zero (become positive),
    then when PSZ drops back below zero, switch to Phase 2.
Phase 2 (CTS trail): Hold until CTS crosses sell threshold → ST_CROSS.
PnL cap and hard stop active throughout both phases.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_institutional_floor(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Two-phase exit: PSZ zero-cross cycle -> CTS max-trail to sell threshold."""
    st = SavgolCTSExitState.from_int(state_val)
    ecfg = cfg.institutional_floor

    if not ecfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop (safety net)
    if getattr(ecfg, "hard_stop_enabled", False) and pnl_pct <= -ecfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap (active in both phases)
    if ecfg.pnl_cap_enabled and pnl_pct >= ecfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    psz = row.get("price_slope_z", np.nan)
    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)

    # Phase 2: CTS max-trail (psz_was_above repurposed as trailing_cts flag)
    if st.psz_was_above:
        if not np.isnan(cts) and not np.isnan(cts_st):
            # Track if we have already reached/exceeded the sell threshold
            if not st.cts_above_bt:  # repurposed: True if CTS >= ST
                if cts >= cts_st:
                    st.cts_above_bt = True
            else:
                # Once we are above ST, exit when we cross back BELOW it (max trail)
                if cts < cts_st:
                    return ExitReason.ST_CROSS, st.to_int()
        return None, st.to_int()

    # Phase 1: PSZ zero-cross cycle
    if not np.isnan(psz):
        # price_above_cwvap repurposed as psz_was_positive flag
        if not st.price_above_cwvap:
            if psz > 0:
                st.price_above_cwvap = True
        else:
            if psz < 0:
                # PSZ cycle done — transition to Phase 2 for CTS trailing
                st.psz_was_above = True
                # Pre-check if CTS is already above threshold
                if not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st:
                    st.cts_above_bt = True
                return None, st.to_int()

    return None, st.to_int()
