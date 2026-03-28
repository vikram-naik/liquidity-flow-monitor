"""BT-Cross exit logic.

Hold until PSZ raw drops below glide threshold after having been above it.
Safety nets: floor hit, BT hit, ceiling-leave.  PSZ stall early-exit
optionally fires at a specific bar when momentum hasn't materialised.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.exits.psz_glide import exit_psz_glide


def exit_bt_cross(
    row: dict, prev_row: dict, trade: Trade,
    peak_close: float, bars_held: int, state_val: int,
    cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Exit logic for BT-cross entries."""
    cts = row.get("cts", np.nan)
    psz_raw = row.get("price_slope_z", np.nan)

    st = SavgolCTSExitState.from_int(state_val)

    if np.isnan(cts):
        return None, st.to_int()

    if cts > -1.0:
        st.cts_rose = True

    # Track if PSZ raw ever rose above glide threshold.
    if not np.isnan(psz_raw) and psz_raw >= cfg.psz_glide_threshold:
        st.psz_was_above = True

    # PSZ stall early exit
    stalled, stall_reason = _is_psz_stalled(records, trade, idx, bars_held, cfg)
    if stalled:
        return stall_reason, st.to_int()

    # Bar-3 PnL stop: exit if trade is underwater at bar N
    bar3_result = _is_bar3_stop(row, trade, bars_held, cfg)
    if bar3_result:
        return bar3_result, st.to_int()

    if not st.cts_rose:
        return None, st.to_int()

    # Safety: CTS hit -1.0
    if cts <= -1.0:
        if bars_held >= cfg.floor_hit_min_bars:
            return ExitReason.FLOOR_HIT, st.to_int()

    # Floor zone protection
    floor_zone = -1.0 + cfg.bt_cross.floor_tolerance
    bt = row.get("cts_buy_threshold", np.nan)
    if cts <= floor_zone and not np.isnan(bt) and bt <= floor_zone:
        return None, st.to_int()

    # Ceiling-leave exit: mean reversion complete
    ceiling = 1.0 - cfg.ceiling_leave_tolerance
    prev_cts = prev_row.get("cts", np.nan)
    if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
        return ExitReason.CEILING_HIT, st.to_int()

    # PSZ glide exit: only check if PSZ was above threshold
    if st.psz_was_above:
        res, _ = exit_psz_glide(row, prev_row, trade, st.to_int(), cfg, records, idx)
        if res:
            return res, st.to_int()

    # Safety: CTS drops back to BT (only if BT above floor zone)
    if not np.isnan(bt) and bt > floor_zone and cts <= bt:
        if bars_held >= cfg.bt_hit_min_bars:
            return ExitReason.BT_HIT, st.to_int()

    return None, st.to_int()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_bar3_stop(
    row: dict, trade: Trade | None,
    bars_held: int, cfg: SavgolCTSExitConfig,
) -> str | None:
    """Exit if trade PnL is below threshold at exactly bar N.

    At bar 3 (configurable), if the trade is down more than the threshold
    the entry has failed to follow through.  81% of stopped trades would
    have ended worse (NIFTY 500 walk-forward validated).
    """
    if not cfg.bt_cross.bar3_stop_enabled or trade is None:
        return None
    if bars_held != cfg.bt_cross.bar3_stop_bar:
        return None

    close = row.get("close", np.nan)
    if np.isnan(close) or trade.entry_price <= 0:
        return None

    pnl = (close / trade.entry_price - 1) * 100
    if pnl < cfg.bt_cross.bar3_stop_threshold:
        return ExitReason.BAR3_STOP
    return None


def _is_psz_stalled(
    records: list[dict] | None, trade: Trade | None,
    idx: int, bars_held: int, cfg: SavgolCTSExitConfig,
) -> tuple[bool, str]:
    """Check whether PSZ momentum has stalled at the onset of a trade.

    At exactly bar N (psz_stall_check_bar) after entry, if psz_v is
    non-positive the move has failed to follow through.  Empirically
    (NIFTY 500), trades with psz_v <= 0 at bar 2 have 42% win rate
    and -2.2% mean PnL — clear early kills.

    Returns (is_stalled, reason_string).
    """
    if (
        not cfg.bt_cross.psz_stall_enabled
        or records is None
        or trade is None
        or bars_held != cfg.bt_cross.psz_stall_check_bar
    ):
        return False, ""

    psz_v = records[idx].get("psz_v", np.nan)
    if np.isnan(psz_v):
        return False, ""

    if psz_v <= 0:
        return True, ExitReason.PSZ_STALL
    return False, None
