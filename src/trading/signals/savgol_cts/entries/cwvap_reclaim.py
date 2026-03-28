"""Path 4 -- CWVAP Reclaim entry.

cts_slope crosses zero from below while price is above CWVAP and PSZ > threshold.
Captures institutional reclaim setups where trend momentum is inflecting upward.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_cwvap_reclaim(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 4 entry conditions.

    Requires:
    - cts_slope crosses zero from below (prev <= 0, now > 0).
    - close > CWVAP (price above institutional average).
    - PSZ > psz_min (momentum confirmation).
    """
    if not cfg.cwvap_reclaim.enabled:
        return False, 0, {"reason": "CWVAP reclaim disabled"}

    # cts_slope zero-cross
    cs = row.get("cts_slope", np.nan)
    pcs = prev_row.get("cts_slope", np.nan)
    if np.isnan(cs) or np.isnan(pcs):
        return False, 0, {"reason": "Missing cts_slope data"}
    if not (pcs <= 0 and cs > 0):
        return False, 0, {"reason": "No cts_slope zero-cross"}

    # Close > CWVAP
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if np.isnan(close) or np.isnan(cwvap) or cwvap <= 0:
        return False, 0, {"reason": "Missing close/CWVAP data"}
    if close <= cwvap:
        return False, 0, {"reason": f"Close {close:.1f} <= CWVAP {cwvap:.1f}"}

    # PSZ gate
    psz = row.get("price_slope_z", np.nan)
    if np.isnan(psz) or psz <= cfg.cwvap_reclaim.psz_min:
        return False, 0, {"reason": f"PSZ {psz:.3f} <= {cfg.cwvap_reclaim.psz_min}"}

    cwvap_dist = (close - cwvap) / cwvap * 100.0
    cts = row.get("cts", np.nan)
    coh = row.get("coherence", np.nan)
    pdd = row.get("pdd_120", np.nan)
    regime = row.get("regime", "")

    intensity_int, meta = compute_intensity(
        cts, coh, pdd, regime, EntryTag.CWVAP_RECLAIM,
        [f"slope={cs:.5f}", f"psz={psz:.3f}", f"cwvap_dist={cwvap_dist:.1f}%"],
    )
    return True, intensity_int, meta
