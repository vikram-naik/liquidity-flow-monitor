from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import PrtSlopeZeroCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def is_flattish_line_adaptive(y1, y2, y3, lookback_window_data, sensitivity=0.05):
    """
    Evaluates if 3 points are a flat line, using the recent market environment
    to automatically calculate the tolerance.
    
    Parameters:
    - y1, y2, y3: The three points to evaluate.
    - lookback_window_data: An array/list of the recent N data points to gauge current volatility.
    - sensitivity: The maximum allowed variance as a percentage of the lookback range (e.g. 0.05 = 5%).
    """
    # 1. Dynamically calculate the tolerance based on local environment
    local_max = np.max(lookback_window_data)
    local_min = np.min(lookback_window_data)
    local_range = local_max - local_min
    
    # Protect against a zero-range denominator (perfectly flat historical window)
    if local_range == 0:
        dynamic_tolerance = 0.0001 # absolute minimum threshold
    else:
        dynamic_tolerance = local_range * sensitivity
        
    # 2. Horizontal Test (Using the dynamically calculated tolerance)
    y_min = min(y1, y2, y3)
    y_max = max(y1, y2, y3)
    range_spread = y_max - y_min
    passes_horizontal = range_spread <= dynamic_tolerance
    
    # 3. Linearity Test (Using the dynamically calculated tolerance)
    expected_y2 = (y1 + y3) / 2.0
    midpoint_deviation = abs(y2 - expected_y2)
    passes_linear = midpoint_deviation <= dynamic_tolerance
    
    return {
        "is_valid": passes_horizontal and passes_linear,
        "dynamic_tolerance_used": round(dynamic_tolerance, 5),
        "range_spread": round(range_spread, 5),
        "midpoint_deviation": round(midpoint_deviation, 5)
    }

# # --- Testing the Adaptive Logic ---

# # Scenario A: The market has been highly volatile recently.
# # The macro range is wide (from -1.0 to 0.5).
# high_volatility_history = [-1.0, -0.8, -0.2, 0.4, 0.5, 0.1, -0.4, -0.9]

# # Scenario B: The market has been dead quiet recently.
# # The macro range is very tight (from -0.55 to -0.40).
# low_volatility_history = [-0.55, -0.52, -0.48, -0.45, -0.42, -0.40, -0.43, -0.47]

# # Our 3 points to test (from your previous prompt)
# p1, p2, p3 = -0.4591, -0.5397, -0.4290

# print("Testing in High Volatility Environment:")
# result_high_vol = is_flattish_line_adaptive(p1, p2, p3, high_volatility_history, sensitivity=0.10)
# print(f"Valid: {result_high_vol['is_valid']}, Tolerance Calculated: {result_high_vol['dynamic_tolerance_used']}")

# print("\nTesting in Low Volatility Environment:")
# result_low_vol = is_flattish_line_adaptive(p1, p2, p3, low_volatility_history, sensitivity=0.10)
# print(f"Valid: {result_low_vol['is_valid']}, Tolerance Calculated: {result_low_vol['dynamic_tolerance_used']}")



def check_prt_slope_zero_cross(
    row: dict,
    prev_row: dict,
    cfg: PrtSlopeZeroCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for PRT Slope Zero Cross path."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    # Extract required fields
    prt_slope = row.get("prt_slope", np.nan)
    prev_prt_slope = prev_row.get("prt_slope", np.nan)
    prt_accel = row.get("prt_accel", np.nan)
    prt = row.get("prt",np.nan)
    prev_prt = prev_row.get("prt",np.nan)
    prev_prt_1 = records[idx-2].get("prt",np.nan)
    lookback_size = 10
    # Generate the window (Chronological order: idx-10, idx-9 ... idx-1)
    lookback_window_data = [
        records[idx - n].get("prt", np.nan) 
        for n in range(lookback_size, 0, -1)
    ] 

    # Convert to a numpy array for efficient math operations later
    lookback_array = np.array(lookback_window_data)
    
    fas = row.get("fas", np.nan)
    
    psz = row.get("price_slope_z", np.nan)
    psz_v = row.get("psz_v", np.nan)
    psz_v_1 = prev_row.get("psz_v", np.nan)
    psz_v_2 = records[idx-2].get("psz_v", np.nan)
    psz_v_3 = records[idx-3].get("psz_v", np.nan)
    
    psz_v_lookback_data = [
        records[idx - n].get("psz_v", np.nan) 
        for n in range(lookback_size + 1, 1, -1)
    ]
    psz_v_lookback_array = np.array(psz_v_lookback_data)
    
    cts = row.get("cts", np.nan)
    cts_slope = row.get("cts_slope", np.nan)
    prev_cts_slope = prev_row.get("cts_slope", np.nan)
    prev_cts_slope_1 = records[idx-2].get("cts_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    
    cts_slope_lookback_data = [
        records[idx - n].get("cts_slope", np.nan) 
        for n in range(lookback_size, 0, -1)
    ]
    cts_slope_lookback_array = np.array(cts_slope_lookback_data)
    
    rdv_slope_z = row.get("rdv_slope_z", np.nan)

    if any(np.isnan(x) for x in [prt, prev_prt, prev_prt_1, prt_slope, prev_prt_slope, prt_accel, fas, psz, psz_v, psz_v_1, psz_v_2, psz_v_3, cts, cts_slope, prev_cts_slope, prev_cts_slope_1, cts_accel, cts_accel_threshold, rdv_slope_z]):
        return False, 0, {"reason": "Missing data for PRT Slope Zero Cross"}

    from src.trading.signals.savgol_cts.telemetry import ScoreTracker
    telemetry_enabled = getattr(cfg, "telemetry_enabled", False)
    tracker = ScoreTracker(base_score=10.0, min_score=cfg.min_score, enabled=telemetry_enabled)
    
    first_fail_reason = None
    
    def evaluate_gate(name: str, passed: bool, reason: str):
        nonlocal first_fail_reason
        tracker.add_gate(name, passed, reason)
        if not passed and first_fail_reason is None:
            first_fail_reason = reason
        return passed

    # 1. PRT Slope crosses above zero
    g1 = evaluate_gate("PRT Cross Zero", prev_prt_slope < 0 and prt_slope > 0, f"({prev_prt_slope:.3f} -> {prt_slope:.3f})")
    g2 = evaluate_gate("PRT Min Slope", prt_slope >= cfg.prt_slope_min, f"prt_slope {prt_slope:.3f} >= {cfg.prt_slope_min:.3f}")
    
    results = is_flattish_line_adaptive(y1=prt, y2=prev_prt, y3=prev_prt_1, lookback_window_data=lookback_array , sensitivity=0.05)
    g3 = evaluate_gate("PRT Not Flat", not results["is_valid"], f"[{prt:.4f},{prev_prt:.4f},{prev_prt_1:.4f}]")
    
    g4 = evaluate_gate("FAS Alignment", cfg.fas_min <= fas < cfg.fas_max, f"fas {fas:.3f} in [{cfg.fas_min:.3f}, {cfg.fas_max:.3f})")
    g5 = evaluate_gate("PSZ Threshold", psz < cfg.psz_threshold, f"psz {psz:.3f} < {cfg.psz_threshold:.3f}")
    g6 = evaluate_gate("CTS Alignment", cts < 0, f"cts {cts:.3f} < 0")
    g7 = evaluate_gate("PRT Accel Guard", prt_accel <= cfg.prt_accel_max, f"prt_accel {prt_accel:.3f} <= {cfg.prt_accel_max:.3f}")
    
    if first_fail_reason is not None and not telemetry_enabled:
        return False, 0, {"reason": first_fail_reason}

    # Calculate flatness unconditionally so we can use it to guard against dead-cat bounces
    cts_flat_results = is_flattish_line_adaptive(
        y1=cts_slope, y2=prev_cts_slope, y3=prev_cts_slope_1, 
        lookback_window_data=cts_slope_lookback_array, 
        sensitivity=0.05
    )
    is_cts_falling = not cts_flat_results["is_valid"] and (cts_slope < prev_cts_slope)

    # A. CTS Slope Confluence (Max 1)
    if cts_slope < -0.05:
        tracker.add("CTS Confluence", 1.0, f"CTS slope ({cts_slope:.3f}) < -0.05")
        
        if cts_flat_results["is_valid"]:
            tracker.add("CTS Confluence (Flat)", -2.0, f"Flat slope ({prev_cts_slope:.3f} -> {cts_slope:.3f})")
        elif is_cts_falling:
            tracker.add("CTS Confluence (Falling)", -2.0, f"Accelerating selling ({prev_cts_slope:.3f} -> {cts_slope:.3f})")
        else:
            tracker.add("CTS Confluence (Rising)", 1.0, f"Decelerating selling ({prev_cts_slope:.3f} -> {cts_slope:.3f})")
    else:
        tracker.add("CTS Confluence", -4.0, f"CTS slope ({cts_slope:.3f}) >= -0.05 (Run up)")
    
    # B. CTS Acceleration State (Max 2)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev2_cts_accel = np.nan
    if records and idx >= 2:
        prev2_cts_accel = records[idx - 2].get("cts_accel", np.nan)

    if not np.isnan(cts_accel) and not np.isnan(cts_accel_threshold) and not np.isnan(prev_cts_accel):
        if cts_accel >= 0:
            if cts_accel <= cts_accel_threshold:
                tracker.add("CTS Acceleration", -4.0, f"Acceleration ({cts_accel:.3f}) <= threshold ({cts_accel_threshold:.3f})")
            else:
                tracker.add("CTS Acceleration", 1.0, f"Acceleration ({cts_accel:.3f}) > threshold ({cts_accel_threshold:.3f})")
                if (cts_accel - cts_accel_threshold) >= 0.01 and (cts_accel - prev_cts_accel) >= 0.01:
                    tracker.add("CTS Acceleration (Strong Break)", 2.0, "Strong break above threshold and prev")
                else:
                    tracker.add("CTS Acceleration (Weak Break)", -2.0, "Weak break above threshold/prev")

                if not np.isnan(prev2_cts_accel):
                    if cts_accel > prev_cts_accel:
                        tracker.add("CTS Acceleration (Rising)", 1.0, f"Rising acceleration ({prev_cts_accel:.3f} -> {cts_accel:.3f})")
                        # C. CTS Acceleration Momentum Bonus (Max 1)
                        if prev_cts_accel > prev2_cts_accel:
                            tracker.add("CTS Acceleration (Sustained)", 1.0, f"Sustained rising ({prev2_cts_accel:.3f} -> {prev_cts_accel:.3f})")
                        else:
                            tracker.add("CTS Acceleration (3-bar Falling)", -2.0, f"3-bar Acceleration falling ({prev2_cts_accel:.3f} -> {prev_cts_accel:.3f})")
                    else:
                        tracker.add("CTS Acceleration (Falling)", -3.0, f"Falling acceleration ({prev_cts_accel:.3f} -> {cts_accel:.3f})")
        else:
            tracker.add("CTS Acceleration", -4.0, f"Negative acceleration ({cts_accel:.3f})")

    if fas < -0.5:
        tracker.add("FAS Alignment", 1.0, f"Deep FAS ({fas:.3f}) < -0.5")
        if fas <= -0.8:
            tracker.add("FAS Alignment (Extreme)", 2.0, f"Extreme depth FAS ({fas:.3f}) <= -0.8")
    else:
        tracker.add("FAS Alignment", -1.0, f"FAS ({fas:.3f}) >= -0.5 (Not deep enough)")

    # Price momentum, must be positive
    if psz_v > cfg.psz_v_min:
        if is_cts_falling:
            tracker.add("PSZ Velocity Guard", 0.0, f"psz_v positive ({psz_v:.3f}) but CTS falling (Ignored dead-cat)")
        else:
            tracker.add("PSZ Velocity Guard", 2.0, f"psz_v ({psz_v:.3f}) > {cfg.psz_v_min:.2f} (Positive momentum)")
    else:
        tracker.add("PSZ Velocity Guard", -2.0, f"psz_v ({psz_v:.3f}) <= {cfg.psz_v_min:.2f} (Negative/flat momentum)")

    # E. PSZ Velocity Rising Bonus/Penalty (+1 / -1)
    if psz_v > psz_v_1:
        if is_cts_falling:
            tracker.add("PSZ Acceleration", 0.0, f"psz_v rising ({psz_v_1:.3f} -> {psz_v:.3f}) but CTS falling (Ignored dead-cat)")
        else:
            velocity_was_flat_results = is_flattish_line_adaptive(
                y1=psz_v_1, y2=psz_v_2, y3=psz_v_3, 
                lookback_window_data=psz_v_lookback_array, 
                sensitivity=0.15
            )

            if velocity_was_flat_results["is_valid"]:
                tracker.add("PSZ Acceleration", -2.0, "Velocity spiked from a dead/flat base (Ignored)")
            else:
                tracker.add("PSZ Acceleration", 1.0, f"psz_v rising ({psz_v_1:.3f} -> {psz_v:.3f})")
                if abs(psz_v - psz_v_1) > 0.01:
                    tracker.add("PSZ Acceleration (Strong)", 1.0, "Strong psz_v delta > 0.01")
                else:
                    tracker.add("PSZ Acceleration (Weak)", -2.0, "Weak psz_v delta <= 0.01")

                sum_total = abs(psz_v + psz_v_1 + psz_v_2)
                if (sum_total/3) > 0.01:
                    tracker.add("PSZ Acceleration (Sustained)", 1.0, "3-bar avg > 0.01")
                else:
                    tracker.add("PSZ Acceleration (Not Sustained)", -2.0, "3-bar avg <= 0.01")            
    else:
        tracker.add("PSZ Acceleration", -2.0, f"psz_v falling ({psz_v_1:.3f} -> {psz_v:.3f})")

    # if prt_slope crosses with momentum, the trade fizzles out early, so penalize.
    if prt_slope > 0.1:
        tracker.add("PRT Momentum Guard", -2.0, f"prt_slope ({prt_slope:.3f}) > 0.1 (Fizzle risk)")

    tracker.print_table()

    if not tracker.passed_scoring():
        return False, 0, {"reason": f"path_score ({tracker.total}) <= min ({cfg.min_score})"}

    base_score = 0.0
    override_score = base_score + tracker.total

    intensity, meta = compute_intensity(row, prev_row, EntryTag.PRT_SLOPE_ZERO_CROSS, override_score=override_score)
    meta["path_score"] = tracker.total
    meta["score"] = override_score

    return True, intensity, meta
