"""Slope Bottom exit logic.

Pure slope-based exit: wait for cts_slope to cross above zero (phase 1),
then exit when it drops back below zero (phase 2). The full slope cycle
captures the reversal and exits when momentum fades.

Uses bit 5 (slope_went_negative) from the state bitfield — repurposed here
to track whether slope has crossed above zero (phase 1 complete).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_slope_bottom(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit when cts_slope completes its zero-cross cycle.

    Phase 1: slope starts deeply negative at entry. Wait for it to cross
             above zero. Track this via slope_went_negative bit (repurposed
             as 'slope_crossed_zero').
    Phase 2: once slope has been above zero, exit when it drops back below.
    """
    st = SavgolCTSExitState.from_int(state_val)

    cs = row.get("cts_slope", np.nan)
    if np.isnan(cs):
        return None, st.to_int()

    # slope_went_negative bit repurposed: True = slope has crossed above zero
    slope_crossed_zero = st.slope_went_negative

    if not slope_crossed_zero:
        # Phase 1: waiting for slope to cross above zero
        if cs > 0:
            st.slope_went_negative = True  # mark phase 1 complete
        return None, st.to_int()

    # Phase 2: slope has been positive, exit when it goes negative again
    if cs < 0:
        return ExitReason.SLOPE_CYCLE, st.to_int()

    return None, st.to_int()
