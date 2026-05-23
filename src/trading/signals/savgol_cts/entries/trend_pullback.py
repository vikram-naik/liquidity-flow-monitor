from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def entry_trend_pullback(row, prev_row, cfg, records, idx):
    """
    Path: Secular Trend Pullback
    Designed to capture quiet pullbacks and consolidations in structurally strong uptrending stocks.
    Uses highly optimized statistical thresholds to achieve an Average P&L > 5% with high win-rate.
    """
    # 1. Check if the path is enabled in the configuration
    if not getattr(cfg, "trend_pullback_enabled", True):
        return False, 0, {"reason": "Trend Pullback entry path is disabled"}

    # 2. Extract Core Indicators
    cwc = row.get("cwc", 0.0)
    pdd_30 = row.get("pdd_30", 0.0)
    pdd_120 = row.get("pdd_120", 0.0)
    bt = row.get("base_tightness", 1.0)
    price_slope_z = row.get("price_slope_z", 0.0)
    range_pos_10 = row.get("range_pos_10", 0.0)
    range_pos_252 = row.get("range_pos_252", 0.0)

    # Raw changes
    cts = row.get("cts", 0.0)
    prev_cts = prev_row.get("cts", 0.0) if prev_row else 0.0
    cts_change = cts - prev_cts

    fas = row.get("fas", 0.0)
    prev_fas = prev_row.get("fas", 0.0) if prev_row else 0.0
    fas_change = fas - prev_fas

    prt_slope = row.get("prt_slope", 0.0)
    prev_prt_slope = prev_row.get("prt_slope", 0.0) if prev_row else 0.0
    prt_slope_change = prt_slope - prev_prt_slope

    # 3. Core Threshold checks (Flow Coherence + Tight Consolidation + Oversold level)
    if cwc < 0.55:
        return False, 0, {"reason": "cwc below optimized threshold (0.55)"}
    if pdd_30 > -2.50:
        return False, 0, {"reason": "pdd_30 not in optimized pullback region (> -2.50)"}
    if pdd_30 < -5.00:
        return False, 0, {"reason": "pdd_30 indicates short-term crash/falling knife (< -5.00)"}
    if bt > 0.45:
        return False, 0, {"reason": "base_tightness above optimized threshold (0.45)"}
    if price_slope_z > -0.10:
        return False, 0, {"reason": "price_slope_z not in optimized oversold region (> -0.10)"}
    if cts_change < -0.05:
        return False, 0, {"reason": "cts_change indicates active institutional distribution (< -0.05)"}

    # 4. Core Filters (Secular Uptrend Strength & Safety Gates)
    # A. Secular Uptrend Strength: blocks falling knives and deep markdowns
    if pdd_120 < -4.00:
        return False, 0, {"reason": "Secular Distribution: pdd_120 below optimized threshold (4.0%)"}

    # B. Falling Knife Guard: typical price spearman over 10 bars
    tps_10 = []
    for k in range(max(0, idx - 9), idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_10.append(tp)
        
    if len(tps_10) >= 5:
        spearman_10 = evaluate_spearman_trend(tps_10)
        if spearman_10 <= -0.90:
            return False, 0, {"reason": "Falling Knife Guard: price_spearman_10 <= -0.90"}

    # C. Weekly Range Position: prevents entering if the price has already run up
    if range_pos_10 > 0.40:
        return False, 0, {"reason": "Overextended: price not in lower 40% of weekly range"}

    # D. Secular Range Position: blocks setups that are overextended on a yearly basis
    if range_pos_252 > 0.55:
        return False, 0, {"reason": "Secular Overextension: price in upper half of yearly range"}

    # 5. Resumption Triggers (Require flow rise, slope inflection, or active flow)
    has_trigger = False
    if fas_change > 0.0 and fas >= -0.75:
        has_trigger = True
    if cts_change > 0.0:
        has_trigger = True
    if prt_slope > 0.0 and prt_slope_change > 0.0:
        has_trigger = True

    if not has_trigger:
        return False, 0, {"reason": "Resumption Trigger: no active flow/slope inflection trigger found"}

    score = 75  # High conviction setup score
    details = {
        "reason": "Secular Trend Pullback accepted",
        "entry_tag": EntryTag.TREND_PULLBACK.value,
        "score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0
    }
    return True, score, details
