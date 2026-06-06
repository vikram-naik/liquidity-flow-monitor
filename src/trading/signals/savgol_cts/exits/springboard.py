from __future__ import annotations
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SpringBoardExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def exit_springboard(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: SpringBoardExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[ExitReason | None, int]:
    """Dedicated exit condition for SpringBoard path.
    
    Exits purely via fallback hard stop/time decay boundaries.
    """
    st = SavgolCTSExitState.from_int(state_val)
    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1.0) * 100.0

    # 1. Hard Stop Capping
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. Time Decay Limit
    if cfg.time_decay_enabled and bars_held >= cfg.max_hold_bars:
        return ExitReason.TIME_DECAY, st.to_int()

    return None, st.to_int()
