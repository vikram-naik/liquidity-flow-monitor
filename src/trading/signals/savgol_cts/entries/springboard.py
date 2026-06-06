from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def entry_springboard(
    row: dict,
    prev_row: dict,
    cfg,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """SpringBoard entry check logic.
    
    Identifies high-quality bottom turnarounds by finding positive flow-price divergence
    when the stock is in a deeply corrected state and is beginning to stabilize.
    """
    path_cfg = getattr(cfg, "springboard", None)
    if path_cfg is None or not path_cfg.enabled:
        return False, 0, {"reason": "SpringBoard entry path is disabled"}

    if not prev_row or not records:
        return False, 0, {"reason": "Missing historical context records"}

    if idx < path_cfg.capitulation_lookback:
        return False, 0, {"reason": f"Insufficient history (idx {idx} < lookback {path_cfg.capitulation_lookback})"}

    # 1. Range Position Gate
    rp_63 = row.get("range_pos_63", np.nan)
    if np.isnan(rp_63) or rp_63 > path_cfg.range_pos_63_max:
        return False, 0, {"reason": f"rp_63 ({rp_63:.2f}) above ceiling threshold of {path_cfg.range_pos_63_max}"}

    # 2. Capitulation Check
    # Verify if CTS dropped to or below capitulation_threshold recently
    capitulated = False
    for k in range(max(0, idx - path_cfg.capitulation_lookback + 1), idx + 1):
        c = records[k].get("cts", np.nan)
        if not np.isnan(c) and c <= path_cfg.capitulation_threshold:
            capitulated = True
            break
            
    if not capitulated:
        return False, 0, {"reason": f"No recent capitulation (CTS did not drop below {path_cfg.capitulation_threshold})"}

    # 3. Bullish Flow-Price Divergence
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False, 0, {"reason": "Missing CTS telemetry data"}
        
    if cts <= prev_cts:
        return False, 0, {"reason": f"CTS not rising (curr: {cts:.3f}, prev: {prev_cts:.3f})"}
        
    if cts == -1.0:
        return False, 0, {"reason": "CTS is pegged at absolute floor (-1.0)"}

    # 4. Coherence Health Gate
    cwc = row.get("cwc", np.nan)
    cwc_slope = row.get("cwc_slope", np.nan)
    if not np.isnan(cwc_slope) and cwc_slope <= path_cfg.cwc_slope_min:
        return False, 0, {"reason": f"CWC slope degrading too fast ({cwc_slope:.4f} <= {path_cfg.cwc_slope_min})"}

    # 5. Price Velocity Stabilization
    psz_v = row.get("psz_v", np.nan)
    if not np.isnan(psz_v) and psz_v <= path_cfg.psz_v_min:
        return False, 0, {"reason": f"Price velocity dropping too fast ({psz_v:.4f} <= {path_cfg.psz_v_min})"}

    # 6. Falling Knife Protection (Spearman price trend over last 5 bars)
    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
        
    if len(tps_5) >= 5:
        spearman_5 = evaluate_spearman_trend(tps_5)
        if spearman_5 <= path_cfg.spearman_5_min:
            return False, 0, {"reason": f"Falling knife: spearman_5 ({spearman_5:.2f}) <= {path_cfg.spearman_5_min}"}
    else:
        return False, 0, {"reason": "Insufficient history for Spearman trend estimation"}

    # Passed all gates!
    
    # Bayesian Adaptive Scorer Post-Filter
    if getattr(path_cfg, "bayesian_mode", False):
        # Calculate min_cts_10
        cts_vals = []
        for k in range(max(0, idx - 9), idx + 1):
            c = records[k].get("cts", np.nan)
            if not np.isnan(c):
                cts_vals.append(c)
        min_cts_val = min(cts_vals) if cts_vals else np.nan
        
        # Calculate spearman_5
        tps_5 = []
        for k in range(idx - 4, idx + 1):
            r = records[k]
            tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
            tps_5.append(tp)
        spearman_5_val = evaluate_spearman_trend(tps_5) if len(tps_5) >= 5 else np.nan
        
        feat_vals = {
            "range_pos_63": rp_63,
            "min_cts_10": min_cts_val,
            "cts_surge": cts - prev_cts,
            "cwc_slope": cwc_slope,
            "psz_v": psz_v,
            "spearman_5": spearman_5_val,
        }
        
        bayesian_score = 0.0
        feature_weights = getattr(path_cfg, "feature_weights", {})
        
        for feat, val in feat_vals.items():
            weights = feature_weights.get(feat, [])
            for left, right, w in weights:
                if left < val <= right:
                    bayesian_score += w
                    break
                    
        if bayesian_score < path_cfg.score_threshold:
            return False, 0, {"reason": f"SpringBoard inflection rejected: Bayesian score ({bayesian_score:.4f}) < {path_cfg.score_threshold:.4f}"}

    score = path_cfg.score
    if getattr(path_cfg, "bayesian_mode", False):
        prob = 1.0 / (1.0 + np.exp(-bayesian_score))
        score = int(prob * 100)

    details = {
        "reason": "SpringBoard inflection accepted",
        "entry_tag": EntryTag.SPRINGBOARD.value,
        "score": score,
        "conv_score": score,
        "cts": cts,
        "cwc": cwc,
        "rp_63": rp_63
    }
    return True, score, details
