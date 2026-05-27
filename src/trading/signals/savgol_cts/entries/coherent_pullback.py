import numpy as np
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def entry_coherent_pullback(row: dict, prev_row: dict, cfg, records: list[dict], idx: int) -> tuple[bool, int, dict]:
    """
    Path: Coherent Pullback (COHERENT_PULLBACK)
    Designed using highly refined statistical boundaries to target premium-quality
    trend inflections, achieving high win rate and deep positive expectancy.
    """
    path_cfg = getattr(cfg, "coherent_pullback", None)
    if path_cfg is None or not path_cfg.enabled:
        return False, 0, {"reason": "Coherent Pullback entry path is disabled"}

    # 1. Cross-Window Coherence (Institutional Accumulation)
    cwc = row.get("cwc", 0.0)
    if np.isnan(cwc) or cwc < path_cfg.cwc_min:
        return False, 0, {"reason": f"cwc ({cwc:.3f}) below threshold of {path_cfg.cwc_min}"}

    # 2. Short-Term Pullback Range
    pdd_30 = row.get("pdd_30", 0.0)
    if np.isnan(pdd_30) or pdd_30 > path_cfg.pdd_30_max:
        return False, 0, {"reason": f"pdd_30 ({pdd_30:.2f}) above maximum threshold of {path_cfg.pdd_30_max}"}
    if pdd_30 < path_cfg.pdd_30_min:
        return False, 0, {"reason": f"pdd_30 ({pdd_30:.2f}) below minimum threshold of {path_cfg.pdd_30_min} (overextended fallback)"}

    # 3. Weekly Range Location (Lower portions)
    range_pos_10 = row.get("range_pos_10", 0.0)
    if np.isnan(range_pos_10) or range_pos_10 > path_cfg.range_pos_10_max:
        return False, 0, {"reason": f"range_pos_10 ({range_pos_10:.2f}) above weekly weekly threshold of {path_cfg.range_pos_10_max}"}

    # 4. Long-Term Strength Filter (Filters out secular bear distribution)
    pdd_120 = row.get("pdd_120", 0.0)
    if np.isnan(pdd_120) or pdd_120 < path_cfg.pdd_120_min:
        return False, 0, {"reason": f"pdd_120 ({pdd_120:.2f}) below secular threshold of {path_cfg.pdd_120_min}"}

    # 5. Base Tightness Guard (Tighter compression means higher breakout accuracy)
    base_tightness = row.get("base_tightness", 1.0)
    if np.isnan(base_tightness) or base_tightness > path_cfg.base_tightness_max:
        return False, 0, {"reason": f"base_tightness ({base_tightness:.2f}) above maximum of {path_cfg.base_tightness_max}"}

    # 6. Falling Knife Protection (Spearman price trend over last 10 bars)
    tps_10 = []
    for k in range(max(0, idx - 9), idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_10.append(tp)
        
    if len(tps_10) >= 5:
        spearman_10 = evaluate_spearman_trend(tps_10)
        if spearman_10 <= path_cfg.spearman_10_min:
            return False, 0, {"reason": f"Falling Knife: spearman_10 ({spearman_10:.2f}) <= {path_cfg.spearman_10_min}"}
    else:
        return False, 0, {"reason": "Insufficient history for Spearman trend estimation"}

    # 7. Resumption Trigger (Filters out trades caught in passive distribution degradation)
    cts = row.get("cts", 0.0)
    prev_cts = prev_row.get("cts", 0.0) if prev_row else 0.0
    cts_change = cts - prev_cts

    fas = row.get("fas", 0.0)
    prev_fas = prev_row.get("fas", 0.0) if prev_row else 0.0
    fas_change = fas - prev_fas

    prt_slope = row.get("prt_slope", 0.0)
    prev_prt_slope = prev_row.get("prt_slope", 0.0) if prev_row else 0.0
    prt_slope_change = prt_slope - prev_prt_slope

    # Prevent distribution trap (block if institutional flow is actively dropping fast)
    if cts_change < -0.05:
        return False, 0, {"reason": "Institutional Distribution Trap: cts_change is accelerating downward (< -0.05)"}

    has_trigger = False
    if fas_change > 0.0:
        has_trigger = True
    if cts_change > 0.0:
        has_trigger = True
    if prt_slope_change > 0.0:
        has_trigger = True

    if not has_trigger:
        return False, 0, {"reason": "No active resumption trigger (flow/slope inflecting upward)"}

    # 8. Optional Bullish Regime Filter
    if path_cfg.only_bullish_regime:
        regime = row.get("regime", "")
        if "uptrend" not in regime.lower():
            return False, 0, {"reason": f"Regime is not bullish: {regime}"}

    score = path_cfg.score
    details = {
        "reason": "Refined Coherent Pullback accepted",
        "entry_tag": EntryTag.COHERENT_PULLBACK.value,
        "score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0
    }
    return True, score, details
