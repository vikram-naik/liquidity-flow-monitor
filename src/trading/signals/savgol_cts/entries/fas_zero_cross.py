from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import FasZeroCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.entries.prt_slope_zero_cross import is_flattish_line_adaptive

def check_fas_zero_cross(
    row: dict,
    prev_row: dict,
    cfg: FasZeroCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for FAS Zero Cross path (Option A)."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    lookback_size = cfg.lookback_size
    if idx < lookback_size + 2:
        return False, 0, {"reason": "Warming up"}

    fas = row.get("fas", np.nan)
    prev_fas = prev_row.get("fas", np.nan)
    
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    prev_cts_1 = records[idx-2].get("cts", np.nan)
    
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev_cts_accel_1 = records[idx-2].get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    cwc_slope = row.get("cwc_slope", np.nan)
    
    if any(np.isnan(x) for x in [fas, prev_fas, cts, prev_cts, prev_cts_1, cts_accel, prev_cts_accel, prev_cts_accel_1, cwc_slope]):
        return False, 0, {"reason": "Missing data"}

    # Absolute values for interest gate
    abs_cts = abs(cts)
    prev_abs_cts = abs(prev_cts)
    prev_abs_cts_1 = abs(prev_cts_1)

    # 1. Independent Lookbacks
    accel_lb = np.array([records[idx - n].get("cts_accel", 0.0) for n in range(lookback_size, 0, -1)])
    abs_cts_lb = np.array([abs(records[idx - n].get("cts", 0.0)) for n in range(lookback_size, 0, -1)])

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

    # Gate 1: FAS Zero Cross
    evaluate_gate("FAS Zero Cross", prev_fas < 0 and fas >= 0, f"({prev_fas:.3f} -> {fas:.3f})")

    # Gate 2: Structural Base (Filter Falling Value)
    fas_5_ago = records[idx-5].get("fas", np.nan)
    fas_5_delta = fas - fas_5_ago
    evaluate_gate("Structural Base", fas_5_delta > 0, f"FAS 5-bar delta {fas_5_delta:.3f}")

    # Gate 3 & 4: Engine Dynamics (Active & Strong)
    # Research shows if accel is flat, the setup is safe. If it's not flat, it MUST be > threshold.
    accel_res = is_flattish_line_adaptive(cts_accel, prev_cts_accel, prev_cts_accel_1, accel_lb, sensitivity=cfg.sensitivity)
    accel_flat = accel_res["is_valid"]
    accel_falling = not accel_flat and (cts_accel < prev_cts_accel)
    
    evaluate_gate("Active Engine", not accel_falling, f"cts_accel falling ({prev_cts_accel:.3f} -> {cts_accel:.3f})")
    
    if not accel_flat:
        evaluate_gate("Engine Strength", not np.isnan(cts_accel_threshold) and cts_accel > cts_accel_threshold, f"accel {cts_accel:.3f} > threshold {cts_accel_threshold:.3f}")
    else:
        evaluate_gate("Engine Strength", True, "accel is flat (threshold bypassed)")

    # Gate 5: Minimum Thrust Guard (Filter sputtering engines)
    evaluate_gate("Minimum Thrust", cts_accel > 0.01, f"cts_accel {cts_accel:.4f} <= 0.01")

    # Gate 6: Secular Crash Guard (Filter >20% drawdowns)
    dist_high_252 = row.get("dist_high_252", np.nan)
    if not np.isnan(dist_high_252):
        evaluate_gate("Secular Crash Guard", dist_high_252 >= -20.0, f"dist_high {dist_high_252:.2f}% < -20%")

    # Gate 7: Coherence Gate (High Thrust Only)
    is_high_thrust = fas > 0.3
    if is_high_thrust:
        coherence = row.get("coherence", np.nan)
        if not np.isnan(coherence):
            rounded_coherence = round(coherence, 1)
            evaluate_gate("Coherence Gate", rounded_coherence >= 0.5, f"high-thrust rounded coherence {rounded_coherence:.1f} ({coherence:.3f}) < 0.5")

    if first_fail_reason is not None and not telemetry_enabled:
        return False, 0, {"reason": first_fail_reason}

    # Optional Scoring Bonuses & Penalties
    price_slope_z = row.get("price_slope_z", np.nan)
    if not np.isnan(price_slope_z):
        if price_slope_z >= 0:
            tracker.add("Price Exhaustion (Penalty)", -10.0, f"psz {price_slope_z:.4f} is positive")
        else:
            tracker.add("Price Exhaustion (Bonus)", 2.0, f"psz {price_slope_z:.4f} is negative")

    abs_cts_res = is_flattish_line_adaptive(abs_cts, prev_abs_cts, prev_abs_cts_1, abs_cts_lb, sensitivity=cfg.sensitivity)
    abs_cts_flat = abs_cts_res["is_valid"]
    
    if abs_cts_flat:
        tracker.add("Inst. Interest (Stalled)", -5.0, f"abs_cts stalled ({abs_cts:.3f})")

    if cwc_slope > 0:
        tracker.add("Cash Coherence", 3.0, f"CWC slope positive ({cwc_slope:.4f})")

    if cts_accel > prev_cts_accel and not accel_flat:
        tracker.add("Engine Rising", 5.0, "Acceleration improving")
    
    if abs_cts < prev_abs_cts and not abs_cts_flat:
        tracker.add("Structural Absorption", 3.0, "Institutions returning to neutral")

    # PSZ Velocity (Rubber-band snapback effect)
    psz_v = row.get("psz_v", np.nan)
    if not np.isnan(psz_v):
        if psz_v <= -0.05:
            tracker.add("Deep Snapback", 4.0, f"psz_v deep negative ({psz_v:.3f})")
        elif psz_v > 0.1:
            tracker.add("Momentum Breakout", 2.0, f"psz_v strong positive ({psz_v:.3f})")

    if telemetry_enabled:
        tracker.print_table()

    if first_fail_reason is not None:
        return False, 0, {"reason": first_fail_reason}

    if not tracker.passed_scoring():
        return False, 0, {"reason": f"score {tracker.total} < min {cfg.min_score}"}

    intensity, meta = compute_intensity(row, prev_row, EntryTag.FAS_ZERO_CROSS, override_score=tracker.total)
    meta["path_score"] = tracker.total
    meta["score"] = tracker.total
    return True, intensity, meta
