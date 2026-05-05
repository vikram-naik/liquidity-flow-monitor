from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import CtsFloorReversionEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend, is_flattish_line_adaptive


def check_cts_floor_reversion(
    row: dict,
    prev_row: dict,
    cfg: CtsFloorReversionEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for CTS Floor Reversion path.
    
    Rules:
    - CTS and BT stuck at floor (-1) for 5 days.
    - Basic momentum turn (psz_v > prev_psz_v).
    - Structural exhaustion (price_slope_z <= -0.15).
    - Balanced multi-factor scoring (Score >= min_score).
    """
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    if idx < 10 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    # 1. GATE 1: 5-bar floor condition
    was_deep_floor = True
    for j in range(5):
        if records[idx - j].get("cts", 0) > -0.999 or records[idx - j].get("cts_buy_threshold", 0) > -0.999:
            was_deep_floor = False
            break

    if not was_deep_floor:
        return False, 0, {"reason": "CTS and BT not at floor for 5 bars"}
    else:
        # if we passed the floor test, check prior to 5 cts bars in last 5 bars cts was not above cts_sell_threshold. If it was we reject because it means we are coming down from a failed breakout and not a true floor reversion
        for j in range(5, 10):
            if records[idx - j].get("cts", 0) >= records[idx - j].get("cts_sell_threshold", 0):
                return False, 0, {"reason": "CTS was above sell threshold in lookback, likely coming down from failed breakout"}

    # 2. GATE 2: Basic momentum direction
    psz_v = row.get("psz_v", np.nan)
    prev_psz_v = records[idx - 1].get("psz_v", np.nan)
    if not np.isnan(psz_v) and not np.isnan(prev_psz_v) and psz_v <= prev_psz_v:
        return False, 0, {"reason": "psz_v not rising"}

    

    # 4. SCORING SYSTEM (Base Score 10.0)
    score = 10.0

    # MOMENTUM VIOLENCE (psz_v)
    # 3. GATE 3: Structural Exhaustion Guard, we move it to soft guard and penalize if psz_v is below the threshold.
    psz_now = row.get("price_slope_z", np.nan)
    if not np.isnan(psz_now) and psz_now >= -0.15:
        score -= 3.0  # Lack of structural exhaustion signal

    if not np.isnan(psz_v) and psz_v <= 0:
        score -= 5.0  # Weak momentum signal

    # 4. Momentum Quality (Adaptive psz_v)
    prev2_psz_v = records[idx-2].get("psz_v", np.nan)
    prev3_psz_v = records[idx-3].get("psz_v", np.nan)
    flat_lookback_size = 10
    spearman_lookback_size = 5
    if not any(np.isnan(x) for x in [psz_v, prev_psz_v, prev2_psz_v, prev3_psz_v]):
        psz_v_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(flat_lookback_size, 0, -1)
        ])
        
        flat_check_psz_v = is_flattish_line_adaptive(
            psz_v, prev_psz_v, prev2_psz_v, 
            psz_v_lookback, sensitivity=0.15
        )
        if flat_check_psz_v["is_valid"]:
            return False, 0, {"reason": "psz_v is flat"}

        psz_v_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(spearman_lookback_size, 0, -1)
        ])
        spearman_coeff = evaluate_spearman_trend(psz_v_lookback)
        if abs(spearman_coeff) < 0.50:  # If the Spearman correlation is very low, it indicates a flat momentum trend
            score -= 7.0  # Penalize for flat momentum trend
        
        # check if the spike was just after a flat period of low momentum, if so we give it a boost because that can be a sign of a strong reversal off the floor
        psz_v_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(spearman_lookback_size + 1, 1, -1)
        ])
        spearman_coeff = evaluate_spearman_trend(psz_v_lookback)
        if abs(spearman_coeff) < 0.50:  # If the Spearman correlation is very low, it indicates a flat momentum trend
            score -= 7.0  # Penalize for flat momentum trend

    # INSTITUTIONAL THRUST (cts_accel)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_thr = row.get("cts_accel_threshold", np.nan)
    prev_accel = records[idx - 1].get("cts_accel", np.nan)
    prev_accel_1 = records[idx - 2].get("cts_accel", np.nan)

    cts_accel_lookback = np.array([
        records[idx - n].get("cts_accel", np.nan) 
        for n in range(flat_lookback_size, 0, -1)
    ])
    flat_check_cts_accel = is_flattish_line_adaptive(
        cts_accel, prev_accel, prev_accel_1, 
        cts_accel_lookback, sensitivity=0.15
    )

    if not np.isnan(cts_accel) and not np.isnan(cts_accel_thr):
        if cts_accel < cts_accel_thr:
            score -= 7.0  # Weak institutional response
        else:
            score += 5.0  # Strong institutional thrust off the floor            

    if not any(np.isnan(x) for x in [cts_accel, prev_accel, prev_accel_1]):
        if cts_accel < prev_accel:
            score -= 7.0  # Penalize if there isn't a clear acceleration pattern

    if flat_check_cts_accel["is_valid"]:
        score -= 5.0  # Penalize for flat momentum, even if above threshold 

    cts_accel_lookback = np.array([
        records[idx - n].get("cts_accel", np.nan) 
        for n in range(spearman_lookback_size, 0, -1)
    ])
    spearman_coeff = evaluate_spearman_trend(cts_accel_lookback)
    if abs(spearman_coeff) < 0.50:  # If the Spearman correlation is very low, it indicates a flat momentum trend
        score -= 5.0  # Penalize for flat momentum trend

        

    # finally if cts_accel is above 0.05 we have already run up a bit, so we penalize.
    if not np.isnan(cts_accel) and cts_accel > 0.05:
        score -= 7.0

    # price range check
    if getattr(cfg, "range_guard_enabled", True):
        pw = row.get("range_pos_10", np.nan) #weekly range position
        pm = row.get("range_pos_22", np.nan) #monthly range position
        pq = row.get("range_pos_63", np.nan) #quarterly range position
        py = row.get("range_pos_252", np.nan) #yearly range position
        if not any(np.isnan(x) for x in [pw, pm, pq, py]):
            if pw > 0.45 and py > 0.40:
                score -= 7.0  # Penalize if price is above the midpoint of any major range, as it may indicate less room to run
            if pw > 0.60 or py > 0.60:
                score -= 7.0  # Heavily penalize if price is above the upper third of weekly.

    # Shallow Dead Cat & Strict Momentum Guards
    if getattr(cfg, "shallow_drop_guard_enabled", True):
        dist_high_10 = row.get("dist_high_10", np.nan)
        psz_now = row.get("price_slope_z", np.nan)
        
        # Guard 1: Fake Floor (Shallow Pullback without Structural Exhaustion)
        if not np.isnan(dist_high_10) and not np.isnan(psz_now):
            if dist_high_10 > -5.0 and psz_now > -0.15:
                score -= 11.0 # Heavy penalty for catching a high-level chop trade
                
        # Guard 2: Falling Knife (Strict Negative Momentum)
        if not np.isnan(psz_v) and psz_v <= 0:
            score -= 6.0 # Combined with the base -5.0 penalty above, this creates an -11.0 total rejection penalty

    # 5. GATE 4: Final Score Gate
    if score < cfg.min_score:
        return False, 0, {"reason": f"Score {score} < {cfg.min_score}"}

    # Intensity calculation
    intensity, meta = compute_intensity(row, prev_row, EntryTag.CTS_FLOOR_REVERSION, override_score=score)
    meta["score"] = float(intensity)

    return True, intensity, meta
