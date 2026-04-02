"""Path 5 -- Slope Bottom entry.

cts_slope rising from deep negative (P5 threshold) during downtrend regime,
with price meaningfully below CWVAP. Captures trend exhaustion reversals
where the slope inflects before price reclaims CWVAP.

Empirically (NIFTY 500, 2024-04 to 2026-03): 218 trades, 48.6% WR,
1.84x payoff, +2.27% expectancy with pure slope zero-cross exit.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_slope_bottom(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 5 entry conditions.

    Requires:
    - cts_slope <= slope_threshold AND rising (inflection from deep negative).
    - regime == "downtrend".
    - slope_delta <= slope_delta_max (reject violent dead-cat bounces).
    - cwvap_dist in [cwvap_dist_min, cwvap_dist_max] (below CWVAP, not too far).
    """
    sbcfg = cfg.slope_bottom
    if not sbcfg.enabled:
        return False, 0, {"reason": "Slope bottom disabled"}

    # cts_slope data
    cs = row.get("cts_slope", np.nan)
    pcs = prev_row.get("cts_slope", np.nan)
    if np.isnan(cs) or np.isnan(pcs):
        return False, 0, {"reason": "Missing cts_slope data"}

    # G1: slope must be at or below threshold
    if cs > sbcfg.slope_threshold:
        return False, 0, {"reason": f"cts_slope {cs:.4f} > threshold {sbcfg.slope_threshold}"}

    # G1.1: slope must not be below the absolute floor (exhaustion limit)
    if cs < sbcfg.slope_exhaustion_min:
        return False, 0, {"reason": f"cts_slope {cs:.4f} < floor {sbcfg.slope_exhaustion_min} (extreme fall)"}

    # G2: slope must be rising
    if cs <= pcs:
        return False, 0, {"reason": f"cts_slope not rising: {pcs:.4f} -> {cs:.4f}"}

    # G3: regime must be downtrend
    regime = row.get("regime", "")
    if regime not in ["downtrend", "notrend"]:
        return False, 0, {"reason": f"Regime {regime} != downtrend or notrend"}

    # G4: slope delta must be between min (conviction) and max (noise-vs-spike)
    slope_delta = cs - pcs
    if slope_delta < sbcfg.slope_delta_min:
        return False, 0, {"reason": f"slope_delta {slope_delta:.4f} < min {sbcfg.slope_delta_min} (minor inflection)"}
    if slope_delta > sbcfg.slope_delta_max:
        return False, 0, {"reason": f"slope_delta {slope_delta:.4f} > max {sbcfg.slope_delta_max} (violent bounce)"}

    # G5 & G6: CWVAP distance must be in range
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    if np.isnan(close) or np.isnan(cwvap) or cwvap <= 0:
        return False, 0, {"reason": "Missing close/CWVAP data"}

    cwvap_dist = (close - cwvap) / cwvap * 100.0

    if cwvap_dist < sbcfg.cwvap_dist_min:
        return False, 0, {"reason": f"cwvap_dist {cwvap_dist:.1f}% < min {sbcfg.cwvap_dist_min}% (too far below)"}

    if cwvap_dist > sbcfg.cwvap_dist_max:
        return False, 0, {"reason": f"cwvap_dist {cwvap_dist:.1f}% > max {sbcfg.cwvap_dist_max}% (too close/above)"}

    # Open > CWVAP Guard
    if getattr(sbcfg, "open_cwvap_guard", False):
        open_px = row.get("open", np.nan)
        if not np.isnan(open_px) and not np.isnan(cwvap):
            if open_px > cwvap:
                return False, 0, {"reason": f"Open {open_px:.2f} > CWVAP {cwvap:.2f} (gap up above)"}


    # G7: cts_accel vs threshold
    cts_accel = row.get("cts_accel", np.nan)
    pcts_accel = prev_row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    if np.isnan(cts_accel) or np.isnan(pcts_accel) or np.isnan(cts_accel_threshold):
        return False, 0, {"reason": "Missing cts_accel data"}
        
    # Phase 3: Reject dropping acceleration
    if getattr(sbcfg, "accel_rising_guard", False):
        if cts_accel <= pcts_accel:
            return False, 0, {"reason": f"cts_accel dropping: {pcts_accel:.4f} -> {cts_accel:.4f}"}

    # Phase 4 & G7: cts_accel must be > threshold
    # Prevents negative acceleration (knife falling faster) from slipping through
    if round(cts_accel, 4) <= round(cts_accel_threshold, 4):
        return False, 0, {"reason": f"cts_accel {cts_accel:.4f} <= threshold {cts_accel_threshold:.4f}"}

    # Phase 5: Deep Exhaustion Filter
    # Only allow trades if the rubber band is fully stretched
    cts = row.get("cts", np.nan)
    if not np.isnan(cts):
        cts_max_val = getattr(sbcfg, "cts_max", -0.85)
        if cts > cts_max_val:
            return False, 0, {"reason": f"cts {cts:.2f} > max {cts_max_val:.2f} (not exhausted)"}

    # Intensity scoring
    pdd = row.get("pdd_120", np.nan)

    # G8: PDD institutional exhaustion guard
    if getattr(sbcfg, "pdd_guard", False):
        if not np.isnan(pdd) and pdd > sbcfg.pdd_max:
            return False, 0, {"reason": f"pdd_120 {pdd:.2f} > max {sbcfg.pdd_max} (institutions still distributing)"}

    # Phase 6: Pure Bearish Day Guard (Falling Knife filter)
    if getattr(sbcfg, "pure_bear_guard", False):
        opn = row.get("open", np.nan)
        close_px = row.get("close", np.nan)
        prev_close = prev_row.get("close", np.nan)
        if not np.isnan(opn) and not np.isnan(close_px) and not np.isnan(prev_close):
            if close_px < opn and close_px < prev_close:
                return False, 0, {"reason": f"Pure Bear Guard: Red candle ({close_px:.1f} < {opn:.1f}) and Lower Close ({close_px:.1f} < {prev_close:.1f})"}

    # Phase 7: Shallow Inflection Guard
    if getattr(sbcfg, "shallow_guard_enabled", False):
        shallow_slope = getattr(sbcfg, "shallow_slope_min", -0.12)
        shallow_dist = getattr(sbcfg, "shallow_dist_max", -1.0)
        if cs > shallow_slope and cwvap_dist > shallow_dist:
            return False, 0, {"reason": f"Shallow Inflection: slope {cs:.4f} > {shallow_slope} AND dist {cwvap_dist:.1f}% > {shallow_dist}%"}

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.SLOPE_BOTTOM,
        [f"slope={cs:.4f}", f"delta={slope_delta:.4f}", f"cwvap_dist={cwvap_dist:.1f}%"],
    )
    return True, intensity_int, meta
