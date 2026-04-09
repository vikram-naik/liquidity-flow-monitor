"""Range Reversion exit logic — PSZ zero-cross cycle.

Wait phase: after entry, wait up to patience bars for PSZ > 0.
   If PSZ doesn't cross zero within patience window → dead-trade abort (TIME_DECAY).
Trail phase: once PSZ > 0, hold. Exit when PSZ drops back <= 0 (PSZ_GLIDE).
Hard stop: -8% (always active).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_range_reversion(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """PSZ zero-cross cycle with patience window."""
    st = SavgolCTSExitState.from_int(state_val)
    rr_cfg = cfg.range_reversion

    if not rr_cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop (always active)
    if pnl_pct <= -rr_cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    psz = row.get("price_slope_z", 0)

    # st.psz_was_above tracks the Phase: False=wait, True=trail
    if st.psz_was_above:
        # Trail phase: PSZ was above zero, exit when it drops back
        if psz <= 0:
            return ExitReason.PSZ_GLIDE, st.to_int()
    else:
        # Wait phase: check if PSZ crossed above zero
        if psz > 0:
            st.psz_was_above = True
        elif bars_held >= rr_cfg.patience_bars:
            # Dead trade: PSZ never crossed zero within patience window
            return ExitReason.TIME_DECAY, st.to_int()

    return None, st.to_int()
