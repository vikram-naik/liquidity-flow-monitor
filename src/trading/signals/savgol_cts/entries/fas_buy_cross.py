from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import FasBuyCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive, evaluate_spearman_trend


def check_fas_buy_cross(
    row: dict,
    prev_row: dict,
    cfg: FasBuyCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for FAS Buy Cross path."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    if idx < 10 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    fas = row.get("fas", np.nan)
    prev_fas = prev_row.get("fas", np.nan)
    fas_bt = row.get("fas_buy_threshold", np.nan)
    prev_fas_bt = prev_row.get("fas_buy_threshold", np.nan)
    
    cts = row.get("cts", np.nan)
    cts_bt = row.get("cts_buy_threshold", np.nan)
    
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev2_cts_accel = records[idx-2].get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    
    low_px = row.get("low", np.nan)
    prev_high = prev_row.get("high", np.nan)
    
    if any(np.isnan(x) for x in [fas, prev_fas, fas_bt, prev_fas_bt, cts, cts_bt, cts_accel, prev_cts_accel, prev2_cts_accel, cts_accel_threshold, low_px, prev_high]):
        return False, 0, {"reason": "Missing data for FAS Buy Cross"}

    # 1. fas crosses fas_bt
    if not (prev_fas <= prev_fas_bt and fas > fas_bt):
        return False, 0, {"reason": f"fas did not cross fas_bt ({prev_fas:.3f} -> {fas:.3f})"}

    # 2. cts <= cfg.cts_max (BT check moved to scoring)
    if not (cts <= cfg.cts_max):
        return False, 0, {"reason": f"cts ({cts:.3f}) above max ({cfg.cts_max:.3f})"}

    # 3. cts_accel should be rising and not flat (MOVED TO SCORING)
    # if not (cts_accel > prev_cts_accel):
    #     return False, 0, {"reason": f"cts_accel not rising ({prev_cts_accel:.4f} -> {cts_accel:.4f})"}
        
    lookback_size = 10
    cts_accel_lookback = np.array([
        records[idx - n].get("cts_accel", np.nan) 
        for n in range(lookback_size, 0, -1)
    ])
    
    flat_check = is_flattish_line_adaptive(
        cts_accel, prev_cts_accel, prev2_cts_accel, 
        cts_accel_lookback, sensitivity=0.05
    )
    if flat_check["is_valid"]:
        return False, 0, {"reason": "cts_accel is flat"}

    # 4. cts_accel > cts_accel_threshold (MOVED TO SCORING)
    # if cts_accel <= cts_accel_threshold:
    #     return False, 0, {"reason": f"cts_accel ({cts_accel:.3f}) <= threshold ({cts_accel_threshold:.3f})"}

    # 5. no gap ups between current and prev candle (True Gap: Low > Prev High)
    if low_px > prev_high * 1.001: # Added 0.1% tolerance for tiny gaps
        return False, 0, {"reason": f"True Gap up detected (low {low_px:.2f} > prev_high {prev_high:.2f})"}

    # 6. Safety gates based on price slope and volume velocity
    price_slope_z = row.get("price_slope_z", np.nan)
    if not np.isnan(price_slope_z):
        if price_slope_z > -0.15:
            return False, 0, {"reason": f"price_slope_z ({price_slope_z:.3f}) > -0.15"}
        if price_slope_z < -0.34:
            return False, 0, {"reason": f"price_slope_z ({price_slope_z:.3f}) < -0.34"}

    rsz_v = row.get("rsz_v", np.nan)
    if not np.isnan(rsz_v) and rsz_v < -0.13:
        return False, 0, {"reason": f"rsz_v ({rsz_v:.3f}) < -0.13"}

    # 7. GUARD: Shallow Pullback (Empirical Data shows prt_slope > 0.10 performs poorly)
    prt_slope = row.get("prt_slope", np.nan)
    if not np.isnan(prt_slope):
        prt_max = getattr(cfg, "prt_slope_max", 0.10)
        if prt_slope >= prt_max:
            return False, 0, {"reason": f"Shallow Pullback Guard: PRT Slope ({prt_slope:.3f}) >= {prt_max:.2f}"}
        
    # 8. GUARD: close > va_high and rp_10 > 0.20 (Empirical data shows entries failing when price is above value area high with strong recent relative price strength)
    close_px = row.get("close", np.nan)
    va_high = row.get("va_high", np.nan)
    rp_10 = row.get("range_pos_10", np.nan)
    if not np.isnan(close_px) and not np.isnan(va_high) and not np.isnan(rp_10):
        if close_px > va_high and rp_10 > 0.20:
            return False, 0, {"reason": f"Value Area Guard: close ({close_px:.2f}) > va_high ({va_high:.2f}) with strong rp_10 ({rp_10:.3f})"}


    # -----------------------------------------------------------------------
    # Multi-Factor Scoring (Strength & Weakness)
    # -----------------------------------------------------------------------
    base_score = 15.0
    
    # 1. Institutional Acceleration Quality (Adaptive)
    accel_delta = cts_accel - prev_cts_accel
    dynamic_tol_accel = flat_check.get("dynamic_tolerance_used", 0.005)
    
    if cts_accel > prev_cts_accel:
        if accel_delta > dynamic_tol_accel:
            base_score += 10.0 # Strong Thrust
        else:
            base_score -= 10.0 # Anemic Rise (Frizzling)
    else:
        base_score -= 10.0 # Falling Acceleration

    # 2. Institutional Cash Alignment
    cwc_slope = row.get("cwc_slope", np.nan)
    if not np.isnan(cwc_slope):
        if cwc_slope > 0.05:
            base_score += 10.0 # Strong cash alignment
        elif cwc_slope < 0.01:
            base_score -= 10.0 # No cash backing (anemic)

    # 3. Acceleration Threshold alignment
    delta_at = cts_accel - cts_accel_threshold
    if delta_at >= 0:
        base_score += 5.0
    else:
        # Scale penalty based on how far below threshold
        if delta_at < -0.05:
            base_score -= 21.0
        elif delta_at < -0.01:
            base_score -= 11.0
        else:
            base_score -= 6.0

    # 4. Momentum Quality (Adaptive psz_v)
    psz_v = row.get("psz_v", np.nan)
    prev_psz_v = prev_row.get("psz_v", np.nan)
    prev2_psz_v = records[idx-2].get("psz_v", np.nan)
    prev3_psz_v = records[idx-3].get("psz_v", np.nan)

    if not any(np.isnan(x) for x in [psz_v, prev_psz_v, prev2_psz_v, prev3_psz_v]):
        psz_v_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(lookback_size, 0, -1)
        ])
        
        flat_check_psz_v = is_flattish_line_adaptive(
            psz_v, prev_psz_v, prev2_psz_v, 
            psz_v_lookback, sensitivity=0.15
        )
        if flat_check_psz_v["is_valid"]:
            return False, 0, {"reason": "psz_v is flat"}
            
        # Check if it spiked from a dead/flat base
        psz_v_prev_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(lookback_size + 1, 1, -1)
        ])
        
        velocity_was_flat_results = is_flattish_line_adaptive(
            y1=prev_psz_v, y2=prev2_psz_v, y3=prev3_psz_v, 
            lookback_window_data=psz_v_prev_lookback, 
            sensitivity=0.15
        )
        if velocity_was_flat_results["is_valid"]:
            return False, 0, {"reason": "psz_v spiked from flat base"}
        
        # Magnitude-based reward for rising momentum
        psz_v_delta = psz_v - prev_psz_v
        dynamic_tol_psz = flat_check_psz_v.get("dynamic_tolerance_used", 0.01)
        
        if psz_v > prev_psz_v:
            if psz_v_delta > dynamic_tol_psz:
                base_score += 5.0  # meaningful momentum turn
            else:
                base_score -= 10.0 # anemic momentum turn
        # No bonus or penalty for falling psz_v, just zeroing out the old negative bonus

    # 5. Structural Free-Fall & Distribution Trap Guards
    prt = row.get("prt", np.nan)
    prt_slope = row.get("prt_slope", np.nan)
    if not np.isnan(prt) and not np.isnan(prt_slope):
        prt_structural_min = getattr(cfg, "prt_structural_min", -0.45) # slightly tightened from -0.50 to catch traps
        prt_slope_min = getattr(cfg, "prt_slope_min", -0.02)
        if prt < prt_structural_min and prt_slope < prt_slope_min:
            base_score -= 11.0 # Heavy penalty for catching a deeply broken falling knife

    dist_high_10 = row.get("dist_high_10", np.nan)
    if not np.isnan(dist_high_10) and not np.isnan(cwc_slope):
        # Heavy penalty for massive short-term drop without institutional cash alignment
        if dist_high_10 < -8.0 and cwc_slope <= 0.0:
            base_score -= 11.0

    min_score = getattr(cfg, "min_score", 20.0)
    if base_score < min_score:
        return False, 0, {"reason": f"Score {base_score} < min {min_score} (delta_at={delta_at:.4f})"}

    intensity, meta = compute_intensity(row, prev_row, EntryTag.FAS_BUY_CROSS, override_score=base_score)
    meta["score"] = float(intensity)

    return True, intensity, meta
