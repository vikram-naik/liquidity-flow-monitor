"""Position Swing exit logic.

Implements ATR-based wide stops and trailing stops to survive volatility,
plus a time failure exit if the trade doesn't perform.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_position_swing(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Position Swing ATR trailing and time decay exit."""
    st = SavgolCTSExitState.from_int(state_val)
    ps_cfg = cfg.position_swing

    if not ps_cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    atr = row.get("atr_20", np.nan)
    
    if np.isnan(close_now) or np.isnan(atr):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0
    peak_pnl_pct = (peak_close / trade.entry_price - 1) * 100.0

    # 1. Initial Stop
    initial_stop = trade.entry_price - (ps_cfg.stop_atr * trade.atr_at_entry)
    
    # 2. Trailing Stop
    # Activate if peak PnL exceeds the start threshold
    if peak_pnl_pct > ps_cfg.trail_start_pnl:
        trail_stop = peak_close - (ps_cfg.trail_atr * atr)
        active_stop = max(initial_stop, trail_stop)
    else:
        active_stop = initial_stop

    # Check stops
    if close_now < active_stop:
        if active_stop == initial_stop:
            return ExitReason.INITIAL_STOP, st.to_int()
        else:
            return ExitReason.TRAIL_STOP, st.to_int()

    # 3. Time Failure
    if bars_held >= ps_cfg.time_fail_bars and pnl_pct < ps_cfg.time_fail_pnl:
        return ExitReason.TIME_FAIL, st.to_int()

    return None, st.to_int()
