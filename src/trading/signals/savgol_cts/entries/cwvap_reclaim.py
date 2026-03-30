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
    pcs_cs_diff_threshold = 0.005
    if not (pcs <= 0 and cs > 0 and cs - pcs > pcs_cs_diff_threshold):
        return False, 0, {"reason": f"No cts_slope zero-cross {pcs:.3f} -> {cs:.3f} and diff {cs - pcs:.3f}, thrs: {pcs_cs_diff_threshold}"}

    # cts_accel vs threshold
    cts_accel = row.get("cts_accel", np.nan)
    pcts_accel = prev_row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    if np.isnan(cts_accel) or np.isnan(pcts_accel) or np.isnan(cts_accel_threshold):
        return False, 0, {"reason": "Missing cts_accel data"}

    accel_margin = cts_accel - cts_accel_threshold
    if accel_margin <= cfg.cwvap_reclaim.accel_margin_min:
        return False, 0, {"reason": f"accel_margin {accel_margin:.5f} <= min {cfg.cwvap_reclaim.accel_margin_min}"}

    # if (pcts_accel > cts_accel):
    #     return False, 0, {"reason": f"cts accel not accelerating {pcts_accel:.3f} > {cts_accel:.3f}"}

    # CTS range guard
    cts = row.get("cts", np.nan)
    if not np.isnan(cts):
        if cts <= cfg.cwvap_reclaim.cts_min:
            return False, 0, {"reason": f"CTS {cts:.3f} <= cts_min {cfg.cwvap_reclaim.cts_min}"}
        if cts > cfg.cwvap_reclaim.cts_max:
            return False, 0, {"reason": f"CTS {cts:.3f} > cts_max {cfg.cwvap_reclaim.cts_max}"}

    # Close > CWVAP
    close = row.get("close", np.nan)
    low = row.get("low", np.nan)
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

    # CWVAP distance cap: reject when price already extended above CWVAP
    if cwvap_dist > cfg.cwvap_reclaim.cwvap_dist_max:
        return False, 0, {"reason": f"cwvap_dist {cwvap_dist:.1f}% > max {cfg.cwvap_reclaim.cwvap_dist_max}%"}

    # VA high guard: reject when close is above the value area high
    va_high = row.get("va_high", np.nan)
    if not np.isnan(va_high) and va_high > 0 and close > va_high:
        return False, 0, {"reason": f"Close {close:.1f} > VA high {va_high:.1f}"}

    # ST guard: reject when CTS already at/above sell threshold (upside exhausted)
    if cfg.cwvap_reclaim.st_guard_enabled:
        st = row.get("cts_sell_threshold", np.nan)
        if not np.isnan(cts) and not np.isnan(st) and cts >= st - cfg.cwvap_reclaim.st_guard_tolerance:
            return False, 0, {"reason": f"ST guard: CTS {cts:.3f} >= ST {st:.3f} (tol {cfg.cwvap_reclaim.st_guard_tolerance})"}

    coh = row.get("coherence", np.nan)
    pdd = row.get("pdd_120", np.nan)
    regime = row.get("regime", "")

    intensity_int, meta = compute_intensity(
        cts, coh, pdd, regime, EntryTag.CWVAP_RECLAIM,
        [f"slope={cs:.5f}", f"psz={psz:.3f}", f"cwvap_dist={cwvap_dist:.1f}%"],
    )
    return True, intensity_int, meta
