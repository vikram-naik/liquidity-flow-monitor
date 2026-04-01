"""Path 5 -- CWVAP Cross entry.

Price crosses CWVAP from below while cts_slope is positive.
Captures the moment price reclaims the institutional average with trend confirmation.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_cwvap_cross(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 5 entry conditions.

    Requires:
    - prev_close <= cwvap AND close > cwvap (price crosses CWVAP from below).
    - cts_accel > cts_accel_threshold AND rising (momentum accelerating).
    """
    if not cfg.cwvap_cross.enabled:
        return False, 0, {"reason": "CWVAP cross disabled"}

    close = row.get("close", np.nan)
    opn = row.get("open", np.nan)
    high = row.get("high", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if np.isnan(close) or np.isnan(opn) or np.isnan(high) or np.isnan(cwvap) or cwvap <= 0:
        return False, 0, {"reason": "Missing close/CWVAP data"}

    # Price must cross CWVAP from below
    if not (opn < cwvap and (close > cwvap)):
        return False, 0, {"reason": f"No CWVAP cross: open {opn:.1f}, close {close:.1f}, cwvap {cwvap:.1f}"}


    # cts_slope must be greater than equal to cfg.cwvap_cross.slope_min
    cs = row.get("cts_slope", np.nan)
    if np.isnan(cs) or cs <= cfg.cwvap_cross.slope_min:
        return False, 0, {"reason": f"cts_slope {cs:.5f} <= min {cfg.cwvap_cross.slope_min}"}

    # cts_accel must be above threshold
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    if np.isnan(cts_accel) or np.isnan(cts_accel_threshold):
        return False, 0, {"reason": "Missing cts_accel data"}

    accel_margin = cts_accel - cts_accel_threshold
    if accel_margin <= cfg.cwvap_cross.accel_margin_min:
        return False, 0, {"reason": f"accel_margin {accel_margin:.5f} <= min {cfg.cwvap_cross.accel_margin_min}"}


    # PSZ gate
    psz = row.get("price_slope_z", np.nan)
    if np.isnan(psz) or psz <= cfg.cwvap_cross.psz_min:
        return False, 0, {"reason": f"PSZ {psz:.3f} <= {cfg.cwvap_cross.psz_min}"}

    # CTS range guard
    cts = row.get("cts", np.nan)
    if not np.isnan(cts):
        if cts <= cfg.cwvap_cross.cts_min:
            return False, 0, {"reason": f"CTS {cts:.3f} <= cts_min {cfg.cwvap_cross.cts_min}"}
        if cts > cfg.cwvap_cross.cts_max:
            return False, 0, {"reason": f"CTS {cts:.3f} > cts_max {cfg.cwvap_cross.cts_max}"}

    # ST guard: reject when CTS already at/above sell threshold (upside exhausted)
    if cfg.cwvap_cross.st_guard_enabled:
        st = row.get("cts_sell_threshold", np.nan)
        if not np.isnan(cts) and not np.isnan(st) and cts >= st - cfg.cwvap_cross.st_guard_tolerance:
            return False, 0, {"reason": f"ST guard: CTS {cts:.3f} >= ST {st:.3f} (tol {cfg.cwvap_cross.st_guard_tolerance})"}

    cwvap_dist = (close - cwvap) / cwvap * 100.0

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.CWVAP_CROSS,
        [f"accel={cts_accel:.5f}", f"cwvap_dist={cwvap_dist:.1f}%"],
    )
    return True, intensity_int, meta
