"""PSZ glide exit — shared helper used by floor, psz, and bt_cross exits.

Exit fires when PSZ drops below ``psz_glide_threshold`` after having been
above it.  Uses a 5-bar mean to smooth noise; falls back to 1-bar if
insufficient history.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig


def exit_psz_glide(
    row: dict, prev_row: dict, trade: Trade,
    state: int, cfg: SavgolCTSExitConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[str | None, int]:
    """Check PSZ glide exit condition (5-bar mean crossover)."""
    psz_raw = row.get("price_slope_z", np.nan)

    if records is not None and idx >= 4:
        # 5-bar mean of PSZ
        lookback_pszs = [
            records[j].get("price_slope_z", np.nan) for j in range(idx - 4, idx + 1)
        ]
        if not any(np.isnan(lookback_pszs)):
            mean_psz = sum(lookback_pszs) / 5.0
            if mean_psz >= cfg.psz_glide_threshold and psz_raw < cfg.psz_glide_threshold:
                return ExitReason.PSZ_GLIDE, state
    else:
        # Fallback to 1-bar if records not provided or insufficient history
        prev_psz_raw = prev_row.get("price_slope_z", np.nan)
        if not np.isnan(psz_raw) and not np.isnan(prev_psz_raw):
            if psz_raw < cfg.psz_glide_threshold and prev_psz_raw >= cfg.psz_glide_threshold:
                return ExitReason.PSZ_GLIDE, state

    return None, state
