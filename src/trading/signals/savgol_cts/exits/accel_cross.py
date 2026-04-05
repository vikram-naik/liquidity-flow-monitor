"""Path 2: Accel Cross exit logic — Two-Phase PSZ/CTS Glide.

Mirroring the Institutional Floor exit (2026-04-05).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import AccelCrossExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def check_exit_accel_cross(
    records: list[dict],
    idx: int,
    trade: "Trade",
    state_val: int,
    cfg: AccelCrossExitConfig,
    cwvap_cfg: "CwvapGuardConfig",
    exit_cfg: "SavgolCTSExitConfig",
) -> tuple[ExitReason | None, int]:
    """Evaluate Path 2 (Accel Cross) exit: Two-phase glide → CTS trail."""
    st = SavgolCTSExitState.from_int(state_val)

    if not cfg.enabled:
        return None, st.to_int()

    if trade is None or trade.entry_price <= 0:
        return None, st.to_int()

    row = records[idx]
    close_now = row.get("close", np.nan)
    if np.isnan(close_now):
        return None, st.to_int()

    pnl_pct = (close_now / trade.entry_price - 1) * 100.0

    # 1. Hard Stop (safety net)
    if cfg.hard_stop_enabled and pnl_pct <= -cfg.hard_stop_pct:
        return ExitReason.HARD_STOP, st.to_int()

    # 2. PnL Cap (active throughout)
    if cfg.pnl_cap_enabled and pnl_pct >= cfg.pnl_cap_pct:
        return ExitReason.PNL_CAP, st.to_int()

    psz = row.get("price_slope_z", np.nan)
    cts = row.get("cts", np.nan)
    cts_st = row.get("cts_sell_threshold", np.nan)

    # Phase 2: CTS trail (psz_was_above bit repurposed as Phase 2 flag)
    if st.psz_was_above:
        if not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st:
            return ExitReason.ST_CROSS, st.to_int()
        return None, st.to_int()

    # Phase 1: PSZ zero-cross cycle
    if not np.isnan(psz):
        # price_above_cwvap repurposed as psz_was_positive flag
        if not st.price_above_cwvap:
            if psz > cfg.psz_exit_threshold:
                st.price_above_cwvap = True
        else:
            if psz <= cfg.psz_exit_threshold:
                # Momentum cycle done — check CTS before exiting
                if not np.isnan(cts) and not np.isnan(cts_st) and cts < cts_st:
                    # Institutional floor still holds — transition to Phase 2 CTS trail
                    st.psz_was_above = True
                else:
                    return ExitReason.PSZ_GLIDE, st.to_int()

    return None, st.to_int()
