"""Path 2: Accel Cross — Triple-trend momentum cross with institutional alignment.

Ported from the NIFTY 50 study (2026-04-05).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import AccelCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.ml_guard import MLGuard


def check_entry_accel_cross(
    row: dict,
    prev: dict,
    cfg: AccelCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Evaluate Path 2 (Accel Cross) entry conditions.

    Returns:
        (passed, intensity, meta)
    """
    if not cfg.enabled:
        return False, 0, {"reason": "Accel Cross disabled"}
    if idx < 10 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    # Core Data
    cts_now = row.get("cts", np.nan)
    s_now, s_prev = row.get("cts_slope", np.nan), prev.get("cts_slope", np.nan)
    a_now, a_prev = row.get("cts_accel", np.nan), prev.get("cts_accel", np.nan)
    at = row.get("cts_accel_threshold", np.nan)
    psz_now = row.get("price_slope_z", np.nan)
    v_now = row.get("psz_v", np.nan)
    cwc_slope = row.get("cwc_slope", np.nan)
    
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    cwvap_dist = (close - cwvap) / cwvap * 100.0 if not np.isnan(cwvap) and cwvap > 0 else np.nan

    if any(np.isnan(v) for v in [cts_now, s_now, s_prev, a_now, a_prev, at, psz_now, v_now, cwc_slope, close, cwvap, cwvap_dist]):
        return False, 0, {"reason": "Missing data for Accel Cross gates"}

    # -----------------------------------------------------------------------
    # MANDATORY GATES (7-GATE CHECK)
    # -----------------------------------------------------------------------
    # 1. Slope Inflection
    if not (s_now > 0 and s_prev <= 0):
        return False, 0, {"reason": f"Slope no inflection: {s_prev:.4f} -> {s_now:.4f}"}

    # 2. Accel Momentum
    if not (a_now > a_prev):
        return False, 0, {"reason": f"Accel not rising: {a_prev:.4f} -> {a_now:.4f}"}

    # 3. Accel Conviction
    if not (a_now > at):
        return False, 0, {"reason": f"Accel {a_now:.4f} <= Threshold {at:.4f}"}

    # 4. Price Pivot
    if not (cfg.psz_min < psz_now <= cfg.psz_max):
        return False, 0, {"reason": f"PSZ {psz_now:.4f} not in ({cfg.psz_min}, {cfg.psz_max}]"}

    # 5. Institutional Guard
    if not (cfg.cts_min < cts_now <= cfg.cts_max):
        return False, 0, {"reason": f"CTS {cts_now:.4f} not in ({cfg.cts_min}, {cfg.cts_max}]"}

    # 6. Distance Guard (ADANIENT Guard)
    if not (cwvap_dist <= cfg.cwvap_dist_max):
        return False, 0, {"reason": f"CWVAP dist {cwvap_dist:.1f}% > max {cfg.cwvap_dist_max}%"}

    # 7. Institutional Alignment
    if not (cwc_slope > cfg.cwc_slope_min):
        return False, 0, {"reason": f"CWC slope {cwc_slope:.4f} <= min {cfg.cwc_slope_min}"}

    # -----------------------------------------------------------------------
    # MULTI-FACTOR SCORING (Ported from Study)
    # -----------------------------------------------------------------------
    # 1. Accel Scoring (3 bars)
    a_1, a_2, a_3 = records[idx-1].get("cts_accel", 0), records[idx-2].get("cts_accel", 0), records[idx-3].get("cts_accel", 0)
    accel_score = 0
    if a_now > a_1: 
        accel_score += 1
        delta = a_now - a_1
        if delta < at: 
            accel_score += 2 # Tight/Stable
        elif delta > (2 * at):
            accel_score -= 5 # CLIMAX PENALTY
    if a_1 > a_2:   
        accel_score += 2
        if (a_1 - a_2) < at: accel_score += 1
    if a_2 > a_3: accel_score += 3
    
    # 2. Slope Scoring (3 bars)
    s_1, s_2, s_3 = records[idx-1].get("cts_slope", 0), records[idx-2].get("cts_slope", 0), records[idx-3].get("cts_slope", 0)
    slope_score = 0
    if s_now > s_1: 
        slope_score += 1
        if (s_now - s_1) < 0.1: slope_score += 2
    if s_1 > s_2:   
        slope_score += 2
        if (s_1 - s_2) < 0.1: slope_score += 1
    if s_2 > s_3: slope_score += 3
    
    # 3. PSZ Velocity Trend (3 bars)
    v_1, v_2 = records[idx-1].get("psz_v", 0), records[idx-2].get("psz_v", 0)
    v_score = 0
    if v_now > v_1: 
        v_score += 1
        if (v_now - v_1) < 0.05: v_score += 1 # Tight turn
    if v_1 > v_2: v_score += 2
    
    total_score = accel_score + slope_score + v_score
    
    # 4. Accumulation Bonuses
    tight_range_slope, tight_range_accel = 0.05, (at if at > 0 else 0.01)
    flat_bars = 0
    for j in range(1, 6):
        if idx-j < 0: break
        if abs(records[idx-j].get("cts_slope", 0)) <= tight_range_slope and abs(records[idx-j].get("cts_accel", 0)) <= tight_range_accel:
            flat_bars += 1
        else: break
    total_score += (min(flat_bars, cfg.flat_bars_boom) * 2)
    
    # Early Stage Institutional Bonus
    if 0.0 < cts_now <= 0.25: 
        total_score += 4
        
    # 5. Momentum Chain Length
    chain_len = 1
    for j in range(1, 10):
        if idx-j-1 < 0: break
        if records[idx-j].get("cts_accel", 0) > records[idx-j-1].get("cts_accel", 0) and records[idx-j].get("psz_v", 0) > records[idx-j-1].get("psz_v", 0):
            chain_len += 1
        else: break

    # -----------------------------------------------------------------------
    # PRODUCTION GATE
    # -----------------------------------------------------------------------
    is_boom = (total_score >= cfg.score_min and flat_bars >= cfg.flat_bars_boom)
    is_trend = (total_score >= cfg.score_min and flat_bars < cfg.flat_bars_boom and chain_len >= cfg.chain_len_trend)
    
    if not (is_boom or is_trend):
        return False, 0, {"reason": f"Production gate failed (Score: {total_score}, Flat: {flat_bars}, Chain: {chain_len})"}
    if total_score > cfg.score_max:
        return False, 0, {"reason": f"Score {total_score} > max {cfg.score_max} (Climax exhaustion)"}

    # -----------------------------------------------------------------------
    # ML GUARD
    # -----------------------------------------------------------------------
    if getattr(cfg, "ml_guard_enabled", False):
        prob = MLGuard.get_instance().score_setup(row)
        if prob is None:
            return False, 0, {"reason": "ML Guard model not available"}
        
        prob_pct = prob * 100.0
        if prob_pct < cfg.min_ml_score:
            return False, 0, {"reason": f"ML Guard Failed: Score {prob_pct:.1f}% < {cfg.min_ml_score}%"}
        
        intensity_int, meta = compute_intensity(
            row, prev, EntryTag.ACCEL, 
            extra_parts=[f"ml={prob_pct:.1f}%", f"score={total_score}"], 
            override_score=prob_pct
        )
        meta["ml_score"] = prob_pct
        meta["score"] = float(intensity_int)
        return True, intensity_int, meta

    # -----------------------------------------------------------------------
    # INTENSITY MAPPING
    # -----------------------------------------------------------------------
    # Mapping study score (20-29) to core intensity (80-89)
    intensity_pts = 80 + (total_score - cfg.score_min)
    
    extra_parts = [
        f"score={total_score}",
        f"flat={flat_bars}",
        f"chain={chain_len}",
        f"dist={cwvap_dist:.1f}%"
    ]
    
    intensity_int, meta = compute_intensity(
        row, prev, EntryTag.ACCEL, 
        extra_parts=extra_parts, 
        override_score=float(intensity_pts)
    )
    meta["score"] = float(intensity_pts)
    
    return True, intensity_int, meta
