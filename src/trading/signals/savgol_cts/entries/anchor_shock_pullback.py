import numpy as np
from src.trading.signals.enums import EntryTag

def entry_anchor_shock_pullback(row: dict, prev_row: dict, cfg, records: list[dict], idx: int) -> tuple[bool, int, dict]:
    """
    Path: Anchor Shock Pullback (ANCHOR_SHOCK_PULLBACK)
    Captures clean institutional swing turnarounds right at anchored support.
    """
    path_cfg = getattr(cfg, "anchor_shock_pullback", None)
    if path_cfg is None or not path_cfg.enabled:
        return False, 0, {"reason": "Anchor Shock Pullback entry path is disabled"}

    # 1. Price-DVL Divergence (PDD) safety gates
    pdd_120 = row.get("pdd_120", 0.0)
    if np.isnan(pdd_120) or pdd_120 < path_cfg.pdd_120_min:
        return False, 0, {"reason": f"pdd_120 ({pdd_120:.2f}) below secular threshold of {path_cfg.pdd_120_min}"}

    pdd_30 = row.get("pdd_30", 0.0)
    if np.isnan(pdd_30) or pdd_30 < path_cfg.pdd_30_min:
        return False, 0, {"reason": f"pdd_30 ({pdd_30:.2f}) below short-term crash threshold of {path_cfg.pdd_30_min} (falling knife protection)"}

    # 2. Strict discount price under Swing-Anchored DVWAP (S-DVWAP) cost basis
    sdvwap = row.get("sdvwap", np.nan)
    close = row.get("close", np.nan)
    if np.isnan(sdvwap) or np.isnan(close):
        return False, 0, {"reason": "Missing S-DVWAP / close price data"}
        
    if close >= sdvwap:
        return False, 0, {"reason": f"Close price ({close:.2f}) is above or at S-DVWAP support ({sdvwap:.2f})"}

    proximity = (sdvwap - close) / sdvwap
    if proximity > path_cfg.dist_thresh:
        return False, 0, {"reason": f"Price is too far below S-DVWAP support (proximity {proximity*100.0:.2f}% > {path_cfg.dist_thresh*100.0:.1f}%)"}

    # 3. Delivery Volume Z-Score dry-up (No institutional dumping)
    dv_shock = row.get("dv_shock", 0.0)
    if np.isnan(dv_shock) or dv_shock > path_cfg.shock_max:
        return False, 0, {"reason": f"dv_shock ({dv_shock:.2f}) above maximum shock dry-up limit of {path_cfg.shock_max}"}

    # 4. Relative Volume Spread Efficiency Ceiling (Consolidation/Supply Absorption)
    esr = row.get("esr", 0.0)
    if np.isnan(esr) or esr > path_cfg.esr_max:
        return False, 0, {"reason": f"esr ({esr:.4f}) above spread efficiency ceiling of {path_cfg.esr_max} (excessive spread/volatility)"}

    # 5. Range Position (avoid high-altitude peaks near 1-year high)
    rp_252 = row.get("range_pos_252", 0.0)
    if not np.isnan(rp_252) and getattr(path_cfg, "rp_252_max", None) is not None:
        if rp_252 > path_cfg.rp_252_max:
            return False, 0, {"reason": f"range_pos_252 ({rp_252:.3f}) above high-altitude ceiling of {path_cfg.rp_252_max} (distribution risk)"}

    # 6. Price Slope Z-score Floor (Momentum protection)
    psz = row.get("price_slope_z", 0.0)
    if not np.isnan(psz) and getattr(path_cfg, "psz_min", None) is not None:
        if psz < path_cfg.psz_min:
            return False, 0, {"reason": f"price_slope_z ({psz:.2f}) below floor of {path_cfg.psz_min} (excessive downward momentum)"}

    # 7. Annual Volatility Range Width Floor (Requires rebound energy)
    rw_252 = row.get("range_width_252", 0.0)
    if not np.isnan(rw_252) and getattr(path_cfg, "rw_252_min", None) is not None:
        if rw_252 < path_cfg.rw_252_min:
            return False, 0, {"reason": f"range_width_252 ({rw_252:.1f}%) below minimum requirement of {path_cfg.rw_252_min}% (flat stock, low rebound energy)"}

    # Accepted!
    
    # Bayesian Adaptive Scorer Post-Filter
    if getattr(path_cfg, "bayesian_mode", False):
        proximity = (sdvwap - close) / sdvwap if sdvwap > 0 else np.nan
        feat_vals = {
            "pdd_120": pdd_120,
            "pdd_30": pdd_30,
            "proximity": proximity,
            "dv_shock": dv_shock,
            "esr": esr,
            "price_slope_z": psz,
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
            return False, 0, {"reason": f"Anchor Shock Pullback rejected: Bayesian score ({bayesian_score:.4f}) < {path_cfg.score_threshold:.4f}"}

    score = path_cfg.score
    details = {
        "reason": "Anchor Shock Pullback accepted",
        "entry_tag": EntryTag.ANCHOR_SHOCK_PULLBACK.value,
        "score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0
    }
    return True, score, details
