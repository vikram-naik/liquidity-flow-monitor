from __future__ import annotations
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import AnchorShockPullbackExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def exit_anchor_shock_pullback(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    state_val: int,
    cfg: AnchorShockPullbackExitConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[ExitReason | None, int]:
    """Exit condition for Anchor-Shock-Pullback path.
    
    Exits when delivery volume shock crosses above a climax threshold,
    or via fallback hard stop/time decay boundaries.
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

    # 1. Shock Climax Exit (Institutional footprints peak)
    dv_shock = row.get("dv_shock", 0.0)
    if cfg.shock_exit_enabled and not np.isnan(dv_shock):
        if dv_shock >= cfg.shock_exit_threshold:
            return ExitReason.STRUCTURAL_CLIMAX, st.to_int()

    # 2. Hard Stop Fallback
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 3. Time Decay Fallback
    if cfg.time_decay_enabled and bars_held >= cfg.max_hold_bars:
        return ExitReason.TIME_DECAY, st.to_int()

    return None, st.to_int()
