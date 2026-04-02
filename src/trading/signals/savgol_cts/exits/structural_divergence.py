"""Structural Divergence exit logic.

Smart path for counter-trend entries:
- PnL cap for quick mean-reversion profits.
- Hard stop for acceptable loss.
- Time decay if trade stalls.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_structural_divergence(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    st = SavgolCTSExitState.from_int(state_val)
    ecfg = cfg.structural_divergence

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop
    if pnl_pct <= -ecfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap
    if ecfg.pnl_cap_enabled and pnl_pct >= ecfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    # 3. Time Decay (if it goes sideways)
    if bars_held >= ecfg.time_decay_bars and pnl_pct < ecfg.time_decay_min_pnl:
        return ExitReason.TIME_DECAY, st.to_int()
        
    # We can also track the slope cycle as a secondary exit
    cs = row.get("cts_slope", np.nan)
    if not np.isnan(cs):
        if not st.slope_crossed_zero:
            if cs > 0:
                st.slope_crossed_zero = True
        else:
            if cs < 0:
                return ExitReason.SLOPE_CYCLE, st.to_int()

    return None, st.to_int()
