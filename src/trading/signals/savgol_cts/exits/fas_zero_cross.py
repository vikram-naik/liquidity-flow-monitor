from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import FasZeroCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_fas_zero_cross(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: FasZeroCrossExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[str | None, int]:
    """Check exit conditions for FAS Zero Cross path.

    Uses Phase-Shift Trail:
    - Phase 1 (Bars 0-anchor_bars): Anchor Stop (Exit if FAS < anchor_fas_floor).
    - Phase 2 (Bars > anchor_bars): Institutional Trail (Exit if CTS < 0 or CTS < Sell Threshold).
    """
    st = SavgolCTSExitState.from_int(state_val)

    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    fas = row.get("fas", np.nan)
    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)
    
    if any(np.isnan(x) for x in [fas, cts]):
        return None, st.to_int()

    # Phase-Shift Logic
    if bars_held <= cfg.anchor_bars:
        # Phase 1: Structural Anchor
        if fas < cfg.anchor_fas_floor:
            return ExitReason.FAS_FLOOR, st.to_int()
    else:
        # Phase 2: Institutional Trail
        # 1. Structural failure: Institutional flow turns net-negative
        if cts < 0:
            return ExitReason.CTS_ZERO_DOWN, st.to_int()
        
        # 2. Momentum Exhaustion: Institutional flow crosses below sell threshold
        if not np.isnan(cts_st) and cts_st > 0 and cts < cts_st:
            return ExitReason.ST_CROSS, st.to_int()

    return None, st.to_int()
