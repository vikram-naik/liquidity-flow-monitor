from src.trading.signals.enums import EntryTag
from src.database import get_user_setting
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive, evaluate_spearman_trend

def check_gap_down(records, idx, lookback=10):
    """
    Checks if a significant gap down occurred in the recent lookback window.
    Gap down: prev_low > current_high AND gap > 0.3 * ATR.
    """
    # To cover T and T-1, we check the last lookback potential gaps
    start = max(1, idx - lookback)
    for i in range(start, idx + 1):
        prev_low = records[i-1].get("low", 0)
        curr_high = records[i].get("high", 0)
        atr = records[i].get("atr_20", 0)
        if prev_low > curr_high:
            gap_size = prev_low - curr_high
            if atr > 0 and gap_size > (0.3 * atr):
                return True
    return False

def check_slope_flatness(records, idx):
    """
    Evaluates CTS slope flatness for either T or T-1 using Spearman correlation.
    Flat if |Spearman| < 0.6.
    """
    for i in [idx, idx - 1]:
        if i < 4: continue
        slopes = [records[k].get("cts_slope", 0) for k in range(i-4, i+1)]
        if abs(evaluate_spearman_trend(slopes)) < 0.6:
            return True
    return False

def check_basing(records, idx):
    """
    Evaluates typical price basing for either T or T-1 over a 10-bar window.
    Uses precomputed engine features (base_tightness, range_width_10).
    If it detects the basing condition (score >= 2/3), returns True (reject entry).
    """
    for i in [idx, idx - 1]:
        if i < 4: continue
        row = records[i]

        close = row.get("close", 0.0)
        if close <= 0.0: continue

        atr = row.get("atr_20", 0.0)

        # 1. Tightness
        bt = row.get("base_tightness", 1.0)
        is_tight = bt < 0.35

        # 2. Narrowness
        rw10 = row.get("range_width_10", 0.0)
        rw10_abs = (rw10 * close) / 100.0
        rw_atrs = rw10_abs / atr if atr > 0 else 10.0
        is_narrow = rw_atrs < 1.5

        # 3. Flatness (Typical Price Spearman over 5 bars)
        tps = []
        for k in range(i - 4, i + 1):
            r = records[k]
            tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
            tps.append(tp)
        is_flat = abs(evaluate_spearman_trend(tps)) < 0.6

        if (int(is_tight) + int(is_narrow) + int(is_flat)) >= 2:
            return True
    return False


def check_long_term_range(row, cwc_threshold=0.50):
    """
    Checks if the price is in the upper portion of long-term ranges without strong trend coherence.
    Returns True if rejected, False if passed.
    """
    range_pos_22 = row.get("range_pos_22", 0.0)
    range_pos_63 = row.get("range_pos_63", 0.0)
    range_pos_252 = row.get("range_pos_252", 0.0)
    cwc = row.get("cwc", 0.0)

    in_high_range = (range_pos_22 > 0.50 or range_pos_63 > 0.60 or range_pos_252 > 0.55)
    strong_coherent_trend = (cwc > cwc_threshold)

    return in_high_range and not strong_coherent_trend


def check_trigger_5(row, records, idx):
    """
    5th Trigger: CWC Accumulation Exhaustion
    Fires on deep flow drawdown under active accumulation and acceleration.
    """
    cwc = row.get("cwc", 0.0)
    cts_accel = row.get("cts_accel", 0.0)
    pdd_120 = row.get("pdd_120", 0.0)
    bt = row.get("base_tightness", 1.0)
    price_slope_z = row.get("price_slope_z", 0.0)

    # Core thresholds
    if cwc < 0.40:
        return False
    if cts_accel < 0.01:
        return False
    if pdd_120 > -4.0:
        return False
    if bt > 0.45:
        return False
    if price_slope_z > -0.15:
        return False

    # Falling Knife Guard: typical price spearman over 10 bars
    tps_10 = []
    for k in range(max(0, idx - 9), idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_10.append(tp)
        
    if len(tps_10) >= 5:
        spearman_10 = evaluate_spearman_trend(tps_10)
        if spearman_10 <= -0.90:
            return False
    else:
        return False

    return True


def entry_universal_cross(row, prev_row, cfg, records, idx):
    """
    Path: Universal Cross for CTS Slope.
    """
    if not getattr(cfg.universal_cross, "enabled", False):
        return False, 0, {}

    # 1. Identify Triggers
    prev_cs = prev_row.get("cts_slope", 0)
    cs = row.get("cts_slope", 0)
    trigger_cs = 1 if (prev_cs <= 0 and cs > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    prt = row.get("prt_slope", 0)
    prev_prt = prev_row.get("prt_slope", 0)
    prt_bt = row.get("prt_buy_threshold", 0.0)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0

    # 4th trigger: CWC Consolidation + Rise (Top Alpha configuration)
    trigger_cwc = 0
    if idx >= 3:
        cwc_vals = [records[k].get("cwc", 0.0) for k in range(idx - 3, idx)]
        cwc_curr = row.get("cwc", 0.0)
        cwc_avg = sum(cwc_vals) / len(cwc_vals)
        cwc_disp = max(cwc_vals) - min(cwc_vals)
        regime = row.get("regime", "notrend")
        if 0.80 <= cwc_avg <= 1.00:
            if cwc_disp <= 0.10:
                if cwc_curr > cwc_avg and (cwc_curr - cwc_avg) >= 0.01:
                    if regime != "downtrend":
                        trigger_cwc = 1

    cts_bt = row.get("cts_buy_threshold", -0.8)
    prev_cts = prev_row.get("cts", 0)
    cts = row.get("cts", 0)
    # trigger_cts = 1 if (prev_cts <= cts_bt and cts > cts_bt) else 0

    accel_bt = row.get("cts_accel_threshold", 0.0)
    accel = row.get("cts_accel", 0)
    psz_v = row.get("psz_v", 0)

    # 5th trigger: CWC Accumulation Exhaustion
    trigger_5 = check_trigger_5(row, records, idx)

    if not any([trigger_cs, trigger_fas, trigger_prt, trigger_cwc, trigger_5]):
        return False, 0, {"reason": "No structural inflection"}
    

    if cts < prev_cts:
        return False, 0, {"reason": "CTS not rising"}
       
    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is actively positive, rising, and price velocity is strong
    prev_fas = prev_row.get("fas", 0.0)
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)
    
    if not strong_institutional_turn:
        if accel <= accel_bt:
            return False, 0, {"reason": "cts_accel below threshold"}

        if idx >= 2:
            a1 = records[idx-2].get("cts_accel", 0)
            a2 = records[idx-1].get("cts_accel", 0)
            a3 = records[idx].get("cts_accel", 0)
            if a3 < a2:
                return False, 0, {"reason": "cts_accel not rising"}
        
    # 2. PSZ Velocity Trend should be positive and rising.
    psz_v = row.get("psz_v", 0)
    if psz_v <= 0:
        return False, 0, {"reason": "psz_v not positive"}
    if idx >= 2:
        v1 = records[idx-2].get("psz_v", 0)
        v2 = records[idx-1].get("psz_v", 0)
        v3 = records[idx].get("psz_v", 0)
        if v3 < v2:
            return False, 0, {"reason": "psz_v not rising"}

    # 3. FAS Trend - fas should be above fas_bt and rising, but not above a high threshold (e.g. 0.1) to avoid overextended conditions
    # fas should be rising if fas is above fas_bt
    if idx >= 2:
        f1 = records[idx-2].get("fas", 0)
        if fas < f1 and fas > fas_bt:
            return False, 0, {"reason": "fas not rising"}
        fas_threshold = 0.1
        if fas > fas_threshold:
            return False, 0, {"reason": "fas above high threshold"}
        # fas is falling and has just crossed 0 and fas_bt is also near zero - falling knife
        if fas_bt > -0.3:
            return False, 0, {"reason": "fas_bt not in expected range"}

    # 4. price location should be lower half of range_pos_10 
    # We should ignore price location check if both fas and cts_slope trigger together, or if CWC trigger is active.
    # OR if we meet the breakout/momentum bypass condition: CWC >= 0.40 AND pdd_30 < -3.5 (high trend coherence and peak-overextension safety)
    range_pos_10 = row.get("range_pos_10", 0)
    cwc = row.get("cwc", 0.0)
    pdd_30 = row.get("pdd_30", 0.0)
    
    momentum_bypass = (cwc >= 0.40 and pdd_30 < -3.5)
    
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs) and not trigger_cwc and not momentum_bypass:
        return False, 0, {"reason": "price not in lower half of weekly range"}

    # 4b. Dynamic Long-term Range Gate with Cross-Window Coherence (CWC) Bypass
    # We ignore this range gate if CWC trigger is active.
    if check_long_term_range(row, cwc_threshold=0.50) and not trigger_cwc:
        return False, 0, {"reason": "price in upper portion of long-term ranges without trend coherence"}
    
    # 5. Check for recent gap downs which could indicate a strong downtrend and invalidate the signal
    lookback = getattr(cfg.universal_cross, "gap_down_lookback", 10)
    if check_gap_down(records, idx, lookback=lookback) or check_gap_down(records, idx, lookback=lookback+1):
        return False, 0, {"reason": "recent gap down detected"}
    
    # 6. Check for slope flatness - if the slope has been very flat, a cross might be more significant
    # Check for slope flatness only when we have trigger from cts_slope.
    if check_slope_flatness(records, idx) and trigger_cs:
        return False, 0, {"reason": "slope has been flat recently, cross less reliable"}    
    
    # 7. Check for basing patterns - if the price has been basing, a cross might be more significant
    if check_basing(records, idx):
        return False, 0, {"reason": "price has been basing recently, cross less reliable"}

    # 7b. Choppy Flat Basing Check using CWC (Cross-Window Coherence)
    if getattr(cfg.universal_cross, "cwc_basing_filter_enabled", True):
        cwc = row.get("cwc", 0.0)
        cwc_slope = row.get("cwc_slope", 0.0)
        cwc_thr = getattr(cfg.universal_cross, "cwc_basing_cwc_threshold", 0.10)
        slope_thr = getattr(cfg.universal_cross, "cwc_basing_slope_threshold", -0.02)
        if cwc < cwc_thr and cwc_slope < slope_thr:
            return False, 0, {"reason": "low and degrading trend coherence (choppy flat basing)"}


    
    # 8. Anti-Trap: Distribution Trap Gate
    # Rejects loose, choppy bases under strong distribution (avoiding WIPRO, TRENT, CIPLA failed breakouts)
    pdd_30 = row.get("pdd_30", 0.0)
    base_tightness = row.get("base_tightness", 1.0)
    if pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46:
        return False, 0, {"reason": "Distribution Trap: choppy base under heavy distribution"}

    # Accepted!
    
    # Bayesian Adaptive Scorer Post-Filter
    uc_cfg = getattr(cfg, "universal_cross", None)
    if uc_cfg is not None and getattr(uc_cfg, "bayesian_mode", False):
        feat_vals = {
            "cwc": cwc,
            "psz_v": psz_v,
            "fas": fas,
            "cts_accel": accel,
            "pdd_30": pdd_30,
            "range_pos_10": range_pos_10,
        }
        
        bayesian_score = 0.0
        feature_weights = getattr(uc_cfg, "feature_weights", {})
        
        for feat, val in feat_vals.items():
            weights = feature_weights.get(feat, [])
            for left, right, w in weights:
                if left < val <= right:
                    bayesian_score += w
                    break
                    
        if bayesian_score < uc_cfg.score_threshold:
            return False, 0, {"reason": f"Universal Cross rejected: Bayesian score ({bayesian_score:.4f}) < {uc_cfg.score_threshold:.4f}"}

    score = 70
    if getattr(uc_cfg, "bayesian_mode", False):
        import math
        prob = 1.0 / (1.0 + math.exp(-bayesian_score))
        score = int(prob * 100)

    details = {
        "reason": "Universal Cross accepted",
        "entry_tag": EntryTag.UNIVERSAL_CROSS.value,
        "score": score,
        "conv_score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0,
    }

    return True, score, details
