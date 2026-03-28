"""CWVAP Reclaim exit logic.

Two exit conditions, whichever fires first:
1. Slope cycle: cts_slope goes negative post-entry, then turns positive again.
2. CWVAP lost: close drops below CWVAP.

Empirically (NIFTY 500 walk-forward):
- Slope fires first: 75.5% WR, +5.45% avg (winners ride the full cycle).
- CWVAP fires first: 5.8% WR, -4.55% avg (losers cut early).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_cwvap_reclaim(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit on slope cycle completion or CWVAP lost, whichever first."""
    st = SavgolCTSExitState.from_int(state_val)

    cs = row.get("cts_slope", np.nan)
    pcs = prev_row.get("cts_slope", np.nan)

    # Track slope going non-positive post-entry
    if not np.isnan(cs) and cs <= 0:
        st.slope_went_negative = True

    # Exit 1: slope cycle — slope turns positive again after having been negative
    if (st.slope_went_negative
            and not np.isnan(cs) and not np.isnan(pcs)
            and pcs <= 0 and cs > 0):
        return ExitReason.SLOPE_CYCLE, st.to_int()

    # Exit 2: CWVAP lost — close < CWVAP
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if not np.isnan(close) and not np.isnan(cwvap) and cwvap > 0 and close < cwvap:
        return ExitReason.CWVAP_LOST, st.to_int()

    return None, st.to_int()
