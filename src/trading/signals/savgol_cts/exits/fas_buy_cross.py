from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import FasBuyCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_fas_buy_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: FasBuyCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[str | None, int]:
    """Exit condition for FAS Buy Cross path.
    
    Trail cts or fas, whichever crosses st from above first.
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
    
    fas = row.get("fas", np.nan)
    prev_fas = prev_row.get("fas", np.nan)
    fas_st = row.get("fas_sell_threshold", np.nan)

    # Check CTS crossing ST from above
    if not any(np.isnan(x) for x in [cts, prev_cts, cts_st]):
        if prev_cts >= cts_st and cts < cts_st:
            return ExitReason.ST_CROSS, st.to_int()

    # Check FAS crossing ST from above
    if not any(np.isnan(x) for x in [fas, prev_fas, fas_st]):
        if prev_fas >= fas_st and fas < fas_st:
            return ExitReason.ST_CROSS, st.to_int()

    return None, st.to_int()
