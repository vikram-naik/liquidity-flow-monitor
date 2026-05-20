from src.trading.signals.savgol_cts.ml_guard import MLGuard
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


    prt = row.get("prt", 0)
    prev_prt = prev_row.get("prt", 0)
    prt_bt = row.get("prt_buy_threshold", 0.0)
    trigger_prt = 1 if (prev_prt <= prt_bt and prt > prt_bt) else 0

    cts_bt = row.get("cts_buy_threshold", -0.8)
    prev_cts = prev_row.get("cts", 0)
    cts = row.get("cts", 0)
    # trigger_cts = 1 if (prev_cts <= cts_bt and cts > cts_bt) else 0

    accel_bt = row.get("cts_accel_threshold", 0.0)
    accel = row.get("cts_accel", 0)

    if not any([trigger_cs, trigger_fas, trigger_prt]):
        return False, 0, {"reason": "No structural inflection"}

       
    # 1. Acceleration Trend
    # cts_accel should be above accel_bt
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
    # We should ignore price location check if both fas and cts_slope trigger together.
    range_pos_10 = row.get("range_pos_10", 0)
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs):
        return False, 0, {"reason": "price not in lower half of weekly range"}
    
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

    
    # 8. Anti-Trap: Distribution Trap Gate
    # Rejects loose, choppy bases under strong distribution (avoiding WIPRO, TRENT, CIPLA failed breakouts)
    pdd_30 = row.get("pdd_30", 0.0)
    base_tightness = row.get("base_tightness", 1.0)
    if pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46:
        return False, 0, {"reason": "Distribution Trap: choppy base under heavy distribution"}

    score = 70

    details = {
        "reason": "Universal Cross accepted",
        "entry_tag": EntryTag.UNIVERSAL_CROSS.value,
        "score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0,
    }

    return True, score, details
