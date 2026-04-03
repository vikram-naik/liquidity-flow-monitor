"""Institutional Floor exit logic.

Bespoke exit replicating the NIFTY 50 study:
Target: Price crosses above CWVAP (reclaim), then exit when 
PSZ falls below threshold (exhaustion of the recovery move).
Safety: Hard stop to prevent infinite holding.
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
    """Exit when target achieved after CWVAP reclaim, or stops hit."""
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

    # 1. Hard Stop (Safety net, study had none)
    if getattr(ecfg, "hard_stop_enabled", False) and pnl_pct <= -ecfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap
    if ecfg.pnl_cap_enabled and pnl_pct >= ecfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    # 3. Study Exit: CWVAP reclaim -> PSZ peak -> PSZ exhaustion
    psz = row.get("price_slope_z", np.nan)
    
    # price_above_cwvap is tracked by the orchestrator (SavgolCTSSignal.check_exit)
    if st.price_above_cwvap and not np.isnan(psz):
        # Phase 2: Wait for PSZ to climb to a peak
        if not st.psz_was_above:
            if psz >= getattr(ecfg, "psz_peak_threshold", 0.25):
                st.psz_was_above = True
        else:
            # Phase 3: Target achieved if PSZ falls below threshold after peaking
            if psz < ecfg.psz_exit_threshold:
                return ExitReason.PSZ_GLIDE, st.to_int()

    return None, st.to_int()
