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

    # 1. Hard Stop
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    prev_cts_st = prev_row.get("cts_sell_threshold", np.nan)

    if any(np.isnan(x) for x in [cts, prev_cts, cts_st, prev_cts_st]):
        return None, st.to_int()

    # 2. CTS Trail (Exit on cross down through ST)
    if prev_cts >= prev_cts_st and cts < cts_st:
        return ExitReason.ST_CROSS, st.to_int()

    return None, st.to_int()
