"""Path 6 -- PDD Divergence entry (Shallow Bottom).

Captures shallow CTS bottoms coupled with deep institutional exhaustion (PDD_120).
Useful for prolonged basing periods where the standard V-bottom entry fails.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_pdd_divergence(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 6 entry conditions.

    Requires:
    - Deep PDD exhaustion (pdd_120 < pdd_threshold).
    - Shallow CTS slope bottom (between slope_threshold_min and max).
    - Moderate CTS exhaustion.
    - Accel and slope delta guards to filter noise.
    - Safe distance below CWVAP.
    """
    pdcfg = cfg.pdd_divergence
    if not pdcfg.enabled:
        return False, 0, {"reason": "PDD Divergence disabled"}

    pdd = row.get("pdd_120", np.nan)
    if np.isnan(pdd) or pdd > pdcfg.pdd_threshold:
        return False, 0, {"reason": f"PDD not exhausted: {pdd:.2f} > {pdcfg.pdd_threshold}"}

    cs = row.get("cts_slope", np.nan)
    pcs = prev_row.get("cts_slope", np.nan)
    if np.isnan(cs) or np.isnan(pcs):
        return False, 0, {"reason": "Missing cts_slope data"}

    if cs > pdcfg.slope_threshold_max or cs < pdcfg.slope_threshold_min:
        return False, 0, {"reason": f"cts_slope {cs:.4f} not in shallow range"}

    if cs <= pcs:
        return False, 0, {"reason": f"cts_slope not rising: {pcs:.4f} -> {cs:.4f}"}

    cts = row.get("cts", np.nan)
    if np.isnan(cts) or cts > pdcfg.cts_max:
        return False, 0, {"reason": f"CTS not exhausted: {cts:.2f} > {pdcfg.cts_max}"}

    regime = row.get("regime", "")
    if regime not in ["downtrend", "notrend"]:
        return False, 0, {"reason": f"Regime {regime} != downtrend or notrend"}

    slope_delta = cs - pcs
    if slope_delta < pdcfg.slope_delta_min:
        return False, 0, {"reason": f"slope_delta {slope_delta:.4f} < min {pdcfg.slope_delta_min}"}
    if slope_delta > pdcfg.slope_delta_max:
        return False, 0, {"reason": f"slope_delta {slope_delta:.4f} > max {pdcfg.slope_delta_max}"}

    cts_accel = row.get("cts_accel", np.nan)
    pcts_accel = prev_row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    if np.isnan(cts_accel) or np.isnan(pcts_accel) or np.isnan(cts_accel_threshold):
        return False, 0, {"reason": "Missing cts_accel data"}

    if cts_accel <= pcts_accel:
        return False, 0, {"reason": f"cts_accel dropping: {pcts_accel:.4f} -> {cts_accel:.4f}"}

    if round(cts_accel, 4) <= round(cts_accel_threshold, 4):
        return False, 0, {"reason": f"cts_accel {cts_accel:.4f} <= threshold {cts_accel_threshold:.4f}"}

    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if np.isnan(close) or np.isnan(cwvap) or cwvap <= 0:
        return False, 0, {"reason": "Missing close/CWVAP data"}

    cwvap_dist = (close - cwvap) / cwvap * 100.0

    if cwvap_dist < pdcfg.cwvap_dist_min:
        return False, 0, {"reason": f"cwvap_dist {cwvap_dist:.1f}% < min {pdcfg.cwvap_dist_min}%"}
    if cwvap_dist > pdcfg.cwvap_dist_max:
        return False, 0, {"reason": f"cwvap_dist {cwvap_dist:.1f}% > max {pdcfg.cwvap_dist_max}%"}

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.PDD_DIVERGENCE,
        [f"pdd={pdd:.1f}", f"slope={cs:.4f}", f"cwvap_dist={cwvap_dist:.1f}%"],
    )
    return True, intensity_int, meta
