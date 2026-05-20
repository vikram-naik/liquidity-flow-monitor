from src.trading.signals.savgol_cts.ml_guard import MLGuard
from src.trading.signals.enums import EntryTag
from src.database import get_user_setting
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive

def entry_universal_cross(row, prev_row, cfg, records, idx):
    """
    Path: Universal Cross (ML Master Path)
    
    A single funnel that catches any structural inflection point (PRT, FAS, CTS, or Accel).
    If an inflection occurs, it feeds the state and the trigger context to the ML Guard.
    If the ML Guard scores it above the minimum threshold, it is accepted.
    """
    if not getattr(cfg.universal_cross, "enabled", False):
        return False, 0, {}

    # 1. Identify Triggers
    # prev_prt = prev_row.get("prt_slope", 0)
    # prt = row.get("prt_slope", 0)
    # trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    cts_bt = row.get("cts_buy_threshold", -0.8)
    prev_cts = prev_row.get("cts", 0)
    cts = row.get("cts", 0)
    trigger_cts = 1 if (prev_cts <= cts_bt and cts > cts_bt) else 0

    accel_bt = row.get("cts_accel_threshold", 0.0)
    accel = row.get("cts_accel", 0)
    prev_accel = prev_row.get("cts_accel", 0)
    trigger_accel = 1 if (prev_accel <= accel_bt and accel > accel_bt) else 0

    # if not any([trigger_prt, trigger_fas, trigger_cts, trigger_accel]):
    if not any([trigger_fas, trigger_cts, trigger_accel]):
        return False, 0, {"reason": "No structural inflection"}

    # 2. Score with ML Guard
    guard = MLGuard.get_instance()
    
    # We must explicitly add the trigger columns to the row dictionary 
    # so the ML Guard can map them to the feature vector correctly.
    row_dict = row.to_dict() if hasattr(row, 'to_dict') else dict(row)
    row_dict.update({
        "trigger_fas": trigger_fas,
        "trigger_cts": trigger_cts,
        # "trigger_accel": trigger_accel
    })

    ml_score_pct = guard.score_setup(row_dict)
    if ml_score_pct is None:
        return False, 0, {"reason": "ML Guard unavailable"}

    # Handle float precision issues
    raw_ml_score = round(ml_score_pct, 4)

    # Using configured threshold from database
    ml_guard_threshold = float(get_user_setting("ml_guard_threshold", "1.55"))
        
    # Adjustments (these do not cause hard rejections, but can be used to fine-tune the score for borderline cases)
    adjustments = {}
    # award = ml_guard_threshold * 0.3  # Award for positive trends
    
    # 1. Acceleration Trend
    # if idx >= 2:
    #     r1 = records[idx-2].get("cts_accel", 0)
    #     r2 = records[idx-1].get("cts_accel", 0)
    #     r3 = records[idx].get("cts_accel", 0)
        
    #     accel_adj = 0
    #     if not (r3 > r2 > r1):
    #         accel_adj -= award
    #     if r3 < r2:
    #         accel_adj -= award
            
    #     if accel_adj != 0:
    #         adjustments["accel_trend"] = round(accel_adj, 4)

    # 2. PSZ Velocity Trend
    # if idx >= 2:
    #     v1 = records[idx-2].get("psz_v", 0)
    #     v2 = records[idx-1].get("psz_v", 0)
    #     v3 = records[idx].get("psz_v", 0)
        
    #     psz_v_adj = 0
    #     if not (v3 > v2 > v1):
    #         psz_v_adj -= award
            
    #     if psz_v_adj != 0:
    #         adjustments["psz_v_trend"] = round(psz_v_adj, 4)

    # 3. psz_v prior to signal is flat.
    # if idx >= 3:
    #     v1 = records[idx-3].get("psz_v", 0)
    #     v2 = records[idx-2].get("psz_v", 0)
    #     v3 = records[idx-1].get("psz_v", 0)
    #     lookback_data = [records[i].get("psz_v", 0) for i in range(max(0, idx-30), idx)]
    #     result = is_flattish_line_adaptive(v1, v2, v3, lookback_window_data=lookback_data, sensitivity=0.05)
    #     if result["is_valid"] and ml_score_pct > 0.9:
    #         # Anti-Trap Gates: Ensure we aren't catching a flat-but-fading move in a sideways base
    #         psz_v_now = row.get("psz_v", 0)
    #         psz_v_prev = prev_row.get("psz_v", 0)
            
    #         # 1. Momentum Direction (Don't reward declining momentum)
    #         is_rising = psz_v_now >= psz_v_prev
            
    #         # 2. Typical Price Expansion (Ensure the stock is "alive", not basing)
    #         atr = row.get("atr_20", 0)
    #         tp_range = 0
    #         if idx >= 5:
    #             tp_window = []
    #             for j in range(idx-5, idx+1):
    #                 r = records[j]
    #                 tp_val = (r.get("high", 0) + r.get("low", 0) + r.get("close", 0)) / 3.0
    #                 tp_window.append(tp_val)
    #             tp_range = max(tp_window) - min(tp_window)
            
    #         min_exp = getattr(cfg.universal_cross, "min_basing_expansion", 0.5)
    #         is_expanded = tp_range >= (atr * min_exp) if atr > 0 else True
            
    #         if is_rising and is_expanded:
    #             # I would like to promote flatness prior to the signal, as it can indicate a buildup before a breakout
    #             adjustments["flatness_award"] = round(award, 4)

    # 4. psz_v turned positive (if it was negative in the prior bar)
    # prev_psz_v = prev_row.get("psz_v", 0)
    # psz_v = row.get("psz_v", 0)
    # if (prev_psz_v < 0 and psz_v > 0) and ml_score_pct > 0.9:
    #     # I would like to promote a positive turn in velocity, as it can indicate a shift in momentum
    #     adjustments["turn_positive_award"] = round(award, 4)

    # 5. Negative PSZ Velocity Penalty
    # if psz_v < 0:
    #     adjustments["negative_psz_v_penalty"] = round(-award, 4)

    # 6. FAS Flatness Penalty
    # if idx >= 2:
    #     f1 = records[idx-2].get("fas", 0)
    #     f2 = records[idx-1].get("fas", 0)
    #     f3 = records[idx].get("fas", 0)
    #     fas_lookback = [records[i].get("fas", 0) for i in range(max(0, idx-30), idx + 1)]
    #     res_fas_flat = is_flattish_line_adaptive(f1, f2, f3, lookback_window_data=fas_lookback, sensitivity=0.05)
    #     if res_fas_flat["is_valid"]:
    #         adjustments["fas_flat_penalty"] = round(-award, 4)

    # 7. Positive FAS Penalty
    # if fas > 0:
    #     adjustments["positive_fas_penalty"] = round(-award, 4)

    # 8. FAS Falling Penalty
    # if idx >= 3:
    #     fas_0 = row.get("fas", 0)
    #     fas_3 = records[idx-3].get("fas", 0)
    #     if fas_0 < fas_3:
    #         adjustments["fas_falling_penalty"] = round(-award, 4)

    # Apply all adjustments
    # total_adjustment = sum(adjustments.values())
    # ml_score_pct += total_adjustment
 
    # ml_score_pct should between 1.8251, 2.0601
    # if ml_score_pct < 1.8251or ml_score_pct > 2.0601:
    if ml_score_pct < ml_guard_threshold:
        return False, 0, {
            "reason": f"Universal ML Guard rejected (Confidence: {ml_score_pct:.2f})",
            "score": ml_score_pct,
            "raw_ml_score": raw_ml_score,
            "ml_guard_threshold": ml_guard_threshold,
            "adjustments": adjustments
        }


    details = {
        "reason": "Universal Cross accepted",
        "entry_tag": EntryTag.UNIVERSAL_CROSS.value,
        "score": ml_score_pct,
        "raw_ml_score": raw_ml_score,
        "ml_guard_threshold": ml_guard_threshold,
        "trigger_fas": bool(trigger_fas),
        "trigger_cts": bool(trigger_cts),
        "adjustments": adjustments
    }

    return True, ml_score_pct, details
