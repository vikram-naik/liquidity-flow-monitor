"""Path 13: CTS Accel Cross — High-conviction crossover with robust structural guards.

Refined from NIFTY 50 study (May 2026).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import CtsAccelCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend, is_flattish_line_adaptive
from src.trading.signals.savgol_cts.telemetry import ScoreTracker
from src.trading.signals.savgol_cts.ml_guard import MLGuard


def check_cts_accel_cross(
    row: dict,
    prev_row: dict,
    cfg: CtsAccelCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for Path 13 (CTS Accel Cross)."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    if idx < 20 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    # Core Indicators
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_bt = row.get("cts_buy_threshold", np.nan)
    prev_cts_bt = prev_row.get("cts_buy_threshold", np.nan)
    
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    cts_at = row.get("cts_accel_threshold", np.nan)
    
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    prt_slope = row.get("prt_slope", np.nan)
    
    rp10 = row.get("range_pos_10", np.nan)
    rp252 = row.get("range_pos_252", np.nan)

    if any(np.isnan(x) for x in [cts, prev_cts, cts_bt, prev_cts_bt, cts_accel, cts_at, close, cwvap, prt_slope, rp10, rp252]):
        return False, 0, {"reason": "Missing core data"}

    tracker = ScoreTracker(base_score=15.0, min_score=cfg.min_score, enabled=cfg.telemetry_enabled)
    first_fail_reason = None

    # 1. GATE: Adaptive CTS Cross
    is_cts_cross = (prev_cts <= prev_cts_bt) and (cts > cts_bt)
    tracker.add_gate("CTS Crossover", is_cts_cross, f"{prev_cts:.3f} -> {cts:.3f} | BT: {cts_bt:.3f}")
    if not is_cts_cross:
        if not cfg.telemetry_enabled:
            return False, 0, {"reason": "CTS no crossover"}
        if first_fail_reason is None:
            first_fail_reason = "CTS no crossover"

    # 2. GATE: Accel Conviction
    is_accel_conviction = (cts_accel > cts_at)
    tracker.add_gate("Accel Conviction", is_accel_conviction, f"Accel {cts_accel:.4f} vs Thr {cts_at:.4f}")
    if not is_accel_conviction:
        if not cfg.telemetry_enabled:
            return False, 0, {"reason": f"Accel {cts_accel:.4f} <= Threshold {cts_at:.4f}"}
        if first_fail_reason is None:
            first_fail_reason = f"Accel {cts_accel:.4f} <= Threshold {cts_at:.4f}"

    # 3. GUARD: Falling Price (10-bar Typical Price Spearman + PRT Slope)
    typical_vals_10 = [(r.get('high', 0) + r.get('low', 0) + r.get('close', 0)) / 3.0 for r in records[idx - 9 : idx + 1]]
    price_spearman_10 = evaluate_spearman_trend(typical_vals_10)
    is_falling_price = (price_spearman_10 < cfg.price_spearman_max and prt_slope < cfg.prt_slope_min) or (prt_slope < cfg.prt_structural_min)
    tracker.add_gate("Falling Price Guard", not is_falling_price, f"Sp10: {price_spearman_10:.2f}, PRT: {prt_slope:.2f}")
    if is_falling_price and first_fail_reason is None:
        first_fail_reason = "Falling Price Guard"

    # 4. GUARD: Deep Reversion (Multi-Factor Range)
    is_deep_rev_fail = rp10 > cfg.rp10_deep_thr and rp252 > cfg.rp252_deep_thr
    tracker.add_gate("Deep Reversion", not is_deep_rev_fail, f"RP10: {rp10:.2f}, RP252: {rp252:.2f}")
    if is_deep_rev_fail and first_fail_reason is None:
        first_fail_reason = f"Range too high (RP10: {rp10:.2f}, RP252: {rp252:.2f})"
    
    # 5. GUARD: Strict Range (Upper Bound)
    is_rp252_safe = rp252 < cfg.rp252_max
    tracker.add_gate("Annual Range Cap", is_rp252_safe, f"RP252 {rp252:.2f} < {cfg.rp252_max}")
    if not is_rp252_safe and first_fail_reason is None:
        first_fail_reason = f"RP252 {rp252:.2f} >= {cfg.rp252_max}"

    # 5b. GUARD: Strict RP10
    is_rp10_safe = rp10 <= cfg.rp10_max
    tracker.add_gate("Strict RP10", is_rp10_safe, f"RP10 {rp10:.2f} <= {cfg.rp10_max}")
    if not is_rp10_safe and first_fail_reason is None:
        first_fail_reason = f"RP10 {rp10:.2f} > {cfg.rp10_max}"

    # 5c. GUARD: Negative Momentum Gap (Distribution Zone Guard)
    dist_high_10 = row.get("dist_high_10", 0)
    is_momentum_trapped = False
    if dist_high_10 < -6.0:
        recent_dist = [records[idx - n].get("accum_div", 0) for n in range(1, 11)]
        if any(d > 0.05 for d in recent_dist):
            is_momentum_trapped = True
    tracker.add_gate("Momentum Gap Guard", not is_momentum_trapped, f"DistHigh10: {dist_high_10:.2f}")
    if is_momentum_trapped and first_fail_reason is None:
        first_fail_reason = "Negative Momentum Gap: Trapped after heavy distribution"

    # 6. GUARD: Institutional Dislocation (CTS Value)
    is_dislocated = cts <= cfg.cts_max
    tracker.add_gate("Inst. Dislocation", is_dislocated, f"CTS {cts:.3f} <= {cfg.cts_max}")
    if not is_dislocated and first_fail_reason is None:
        first_fail_reason = f"CTS {cts:.3f} > {cfg.cts_max}"

    # 7. GUARD: Prior Exhaustion (CTS Reset)
    prior_reset_window = records[idx - cfg.prior_reset_lookback : idx]
    is_exhausted = any(r.get("cts", 0) >= r.get("cts_sell_threshold", 0.5) for r in prior_reset_window)
    tracker.add_gate("Prior Exhaustion", not is_exhausted, "No sell-threshold hits in lookback")
    if is_exhausted and first_fail_reason is None:
        first_fail_reason = "Prior Exhaustion detected in last 10 bars"

    # 8. GUARD: Strict Accel (Momentum of Accel)
    is_accel_rising = cts_accel > prev_cts_accel
    tracker.add_gate("Accel Rising", is_accel_rising, f"{prev_cts_accel:.4f} -> {cts_accel:.4f}")
    if not is_accel_rising and first_fail_reason is None:
        first_fail_reason = f"Accel not strictly increasing ({prev_cts_accel:.4f} -> {cts_accel:.4f})"

    # 9. GUARD: Minimum Accel Thrust (Spread)
    accel_lb = [records[idx - n].get("cts_accel", 0) for n in range(cfg.accel_lookback - 1, -1, -1)]
    accel_spread = max(accel_lb) - min(accel_lb)
    cts_a_spearman = evaluate_spearman_trend(accel_lb)
    is_elite_clean_thrust = (cts_a_spearman >= 0.90) and (cts_accel > 2 * cts_at)
    
    # PRT Reversal Bypass Logic
    prt_slope = row.get("prt_slope", np.nan)
    prev_prt_slope = prev_row.get("prt_slope", np.nan)
    is_prt_reversal_bypass = False
    if not np.isnan(prt_slope) and not np.isnan(prev_prt_slope):
        prt_delta = prt_slope - prev_prt_slope
        if prev_prt_slope < 0 and prt_slope > 0 and prt_delta >= cfg.accel_spread_prt_bypass_delta:
            is_prt_reversal_bypass = True

    is_accel_thrust = (accel_spread >= cfg.accel_spread_min) or is_elite_clean_thrust or is_prt_reversal_bypass
    tracker.add_gate("Accel Thrust", is_accel_thrust, f"Spread: {accel_spread:.4f}, Elite: {is_elite_clean_thrust}, PRT Bypass: {is_prt_reversal_bypass}")
    if not is_accel_thrust and first_fail_reason is None:
        first_fail_reason = f"Accel spread {accel_spread:.4f} < {cfg.accel_spread_min} (Non-elite, no PRT bypass)"

    # 9b. GUARD: Peak Momentum (ONGC Guard)
    accel_peak = max(accel_lb)
    is_at_peak = True
    if cfg.accel_peak_guard_type == "strict":
        if cts_accel < accel_peak:
            is_at_peak = False
    elif cfg.accel_peak_guard_type == "proximity":
        proximity_threshold = accel_peak - (accel_spread * cfg.accel_peak_proximity_limit)
        if cts_accel < proximity_threshold:
            is_at_peak = False
    tracker.add_gate("Accel Peak Guard", is_at_peak, f"Type: {cfg.accel_peak_guard_type}, Accel: {cts_accel:.4f}, Peak: {accel_peak:.4f}")
    if not is_at_peak and first_fail_reason is None:
        first_fail_reason = "Accel Peak Guard"

    # 9c. GUARD: Accumulation Divergence (Distribution Zone Guard)
    accum_div = row.get("accum_div", 0)
    is_not_distribution = accum_div <= cfg.accum_div_max
    tracker.add_gate("Distribution Guard", is_not_distribution, f"AccumDiv: {accum_div:.4f} <= {cfg.accum_div_max}")
    if not is_not_distribution and first_fail_reason is None:
        first_fail_reason = f"Distribution Zone: Accum Div {accum_div:.4f} > {cfg.accum_div_max}"

    # 10. GUARD: Flatness Check
    psz_v_lb_data = [records[idx - n].get("psz_v", 0) for n in range(cfg.psz_v_lookback - 1, -1, -1)]
    psz_v_peak = max(psz_v_lb_data)
    psz_v_spread = psz_v_peak - min(psz_v_lb_data)
    is_psz_v_ok = True
    if cfg.psz_v_peak_guard_type == "proximity":
        psz_v_prox_threshold = psz_v_peak - (psz_v_spread * cfg.psz_v_peak_proximity_limit)
        psz_v_now = row.get("psz_v", 0)
        if psz_v_now < psz_v_prox_threshold:
            is_psz_v_ok = False
    tracker.add_gate("Velocity Peak Guard", is_psz_v_ok, f"PSZv: {row.get('psz_v', 0):.4f}, Peak: {psz_v_peak:.4f}")
    if not is_psz_v_ok and first_fail_reason is None:
        first_fail_reason = "Velocity Fizzle"

    flat_psz_v = is_flattish_line_adaptive(
        row.get("psz_v", 0), prev_row.get("psz_v", 0), records[idx-2].get("psz_v", 0),
        psz_v_lb_data, sensitivity=0.15
    )
    flat_cts_accel = is_flattish_line_adaptive(
        cts_accel, prev_cts_accel, records[idx-2].get("cts_accel", 0),
        accel_lb, sensitivity=0.15
    )
    is_not_flat = not (flat_psz_v["is_valid"] or flat_cts_accel["is_valid"])
    tracker.add_gate("Flatness Check", is_not_flat, f"PSZv Flat: {flat_psz_v['is_valid']}, Accel Flat: {flat_cts_accel['is_valid']}")
    if not is_not_flat and first_fail_reason is None:
        first_fail_reason = "Engine or Momentum is flat"

    # --- SCORING (Soft Guards) ---
    psz_v_vals = psz_v_lb_data # N bars (cfg.psz_v_lookback)
    psz_v_spearman = evaluate_spearman_trend(psz_v_vals)
    
    # Soft Rejection (Momentum Decay)
    if psz_v_spearman < cfg.spearman_threshold:
        tracker.add("Momentum Quality (Penalty)", -20.0, f"psz_v spearman {psz_v_spearman:.2f} < {cfg.spearman_threshold}")
    else:
        # Linear interpolation points mapped to 0-45
        # Interp -0.3 to 0.9 -> 0 to 42.5
        ratio = (psz_v_spearman - (-0.3)) / (0.9 - (-0.3))
        ratio = max(0.0, min(1.0, ratio))
        tracker.add("Momentum Quality", ratio * 42.5, f"psz_v spearman {psz_v_spearman:.2f}")

    if cts_a_spearman < cfg.spearman_threshold:
        tracker.add("Accel Quality (Penalty)", -20.0, f"cts_accel spearman {cts_a_spearman:.2f} < {cfg.spearman_threshold}")
    else:
        ratio = (cts_a_spearman - (-0.3)) / (0.9 - (-0.3))
        ratio = max(0.0, min(1.0, ratio))
        tracker.add("Accel Quality", ratio * 42.5, f"cts_accel spearman {cts_a_spearman:.2f}")

    # Correction Depth Penalty (Soft Guard for Shallow Setups)
    if dist_high_10 > -1.0:
        tracker.add("Correction Depth (Heavy Penalty)", -20.0, f"Very shallow dist_high_10 {dist_high_10:.2f}%")
    elif dist_high_10 > cfg.dist_high_10_max:
        tracker.add("Correction Depth (Penalty)", -10.0, f"Shallow dist_high_10 {dist_high_10:.2f}%")

    # Red Bar Penalty (Soft Guard for Intraday Divergence)
    if close < row.get('open', close):
        tracker.add("Price Reaction (Penalty)", -15.0, "Red Candle signal day")

    # Final Telemetry Dump
    if cfg.telemetry_enabled:
        tracker.print_table()

    # Hard Rejection Exit
    if first_fail_reason is not None:
        return False, 0, {"reason": first_fail_reason, "tracker": tracker}

    # CWVAP Guard
    if close > cwvap:
        return False, 0, {"reason": "Price above CWVAP", "tracker": tracker}

    if not tracker.passed_scoring():
        return False, 0, {"reason": f"Score {tracker.total:.1f} < min {cfg.min_score}", "tracker": tracker}

    # ML Guard gate
    if getattr(cfg, "ml_guard_enabled", False):
        prob = MLGuard.get_instance().score_setup(row)
        if prob is None:
            return False, 0, {"reason": "ML Guard model not available", "tracker": tracker}
        
        prob_pct = prob * 100.0
        if prob_pct < cfg.min_ml_score:
            return False, 0, {"reason": f"ML Guard Failed: Score {prob_pct:.1f}% < {cfg.min_ml_score}%", "tracker": tracker}
        
        intensity_int, meta = compute_intensity(
            row, prev_row, EntryTag.CTS_ACCEL_CROSS,
            extra_parts=[f"ml={prob_pct:.1f}%", f"path_score={tracker.total:.1f}"],
            override_score=prob_pct
        )
        meta["ml_score"] = prob_pct
        meta["score"] = float(intensity_int)
        meta["tracker"] = tracker
        return True, intensity_int, meta

    # Intensity Mapping (90-99)
    # Map score (15 to 100) to intensity (90 to 99)
    intensity_pts = 90.0 + (tracker.total - 15.0) / (100.0 - 15.0) * 9.0
    
    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.CTS_ACCEL_CROSS,
        extra_parts=[f"score={tracker.total:.1f}", f"sp_p={price_spearman_10:.2f}"],
        override_score=float(intensity_pts)
    )
    meta["score"] = int(round(tracker.total))
    meta["tracker"] = tracker
    
    return True, intensity_int, meta
