"""Floor-path exit logic.

Applies to CTS-Floor-Leave, CTS-BT-Floor, and CTS-Floor-Touch entries.
Safety nets: floor hit (-1.0) and BT hit.  Ceiling-leave fires when CTS
drops below (1.0 - tolerance) after having reached it.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def exit_floor(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit logic for floor-path entries."""
    cts = row.get("cts", np.nan)
    bt = row.get("cts_buy_threshold", np.nan)
    psz_raw = row.get("price_slope_z", np.nan)

    st = SavgolCTSExitState.from_int(state_val)

    if np.isnan(cts):
        return None, st.to_int()

    if cts > -1.0:
        st.cts_rose = True
    if not np.isnan(bt) and cts > bt:
        st.cts_above_bt = True

    # Track if PSZ raw ever rose above glide threshold.
    if not np.isnan(psz_raw) and psz_raw >= cfg.psz_glide_threshold:
        st.psz_was_above = True

    # Safety: CTS hit -1.0 (after having risen)
    if st.cts_rose and cts <= -1.0:
        if bars_held >= cfg.floor_hit_min_bars:
            return ExitReason.FLOOR_HIT, st.to_int()

    # Floor-leave specific: Hit BT (mean reversion complete)
    if st.cts_above_bt and not np.isnan(bt) and cts <= bt:
        if bars_held >= cfg.bt_hit_min_bars:
            return ExitReason.BT_HIT, st.to_int()

    # Ceiling-leave exit: CTS drops below ceiling after having been there
    ceiling = 1.0 - cfg.ceiling_leave_tolerance
    prev_cts = prev_row.get("cts", np.nan)
    if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
        return ExitReason.CEILING_HIT, st.to_int()

    return None, st.to_int()
