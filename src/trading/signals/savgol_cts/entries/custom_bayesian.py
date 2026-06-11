import math
import numpy as np
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

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


def check_bayesian_triggers(row, prev_row, records, idx):
    """
    Check if a bar qualifies for Custom Bayesian triggers (inflection + CTS rising).
    """
    # 1. Structural triggers (inflection check)
    prev_cs = prev_row.get("cts_slope", 0)
    cs = row.get("cts_slope", 0)
    trigger_cs = (prev_cs <= 0 and cs > 0)

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = (prev_fas <= fas_bt and fas > fas_bt)

    prt = row.get("prt_slope", 0)
    prev_prt = prev_row.get("prt_slope", 0)
    trigger_prt = (prev_prt <= 0 and prt > 0)

    trigger_cwc = False
    if idx >= 3 and records is not None:
        cwc_vals = [records[k].get("cwc", 0.0) for k in range(idx - 3, idx)]
        cwc_curr = row.get("cwc", 0.0)
        cwc_avg = sum(cwc_vals) / len(cwc_vals)
        cwc_disp = max(cwc_vals) - min(cwc_vals)
        regime = row.get("regime", "notrend")
        if 0.80 <= cwc_avg <= 1.00:
            if cwc_disp <= 0.10:
                if cwc_curr > cwc_avg and (cwc_curr - cwc_avg) >= 0.01:
                    if regime != "downtrend":
                        trigger_cwc = True

    trigger_5 = check_trigger_5(row, records, idx)

    # Basic safety filter: CTS must not be declining
    prev_cts = prev_row.get("cts", 0)
    cts = row.get("cts", 0)
    if cts < prev_cts:
        return False

    return any([trigger_cs, trigger_fas, trigger_prt, trigger_cwc, trigger_5])


def entry_custom_bayesian(row, prev_row, cfg, records, idx):
    """
    Path: Custom Bayesian Inflection Entry.
    Evaluates standard structural triggers, then scores 12 features using
    symbol-specific log-likelihood weights.
    """
    if not getattr(cfg.custom_bayesian, "enabled", False):
        return False, 0, {"reason": "Custom Bayesian path disabled"}

    # 1. Structural triggers (inflection check) & safety filter
    if not check_bayesian_triggers(row, prev_row, records, idx):
        prev_cts = prev_row.get("cts", 0)
        cts = row.get("cts", 0)
        if cts < prev_cts:
            return False, 0, {"reason": "CTS not rising"}
        return False, 0, {"reason": "No structural inflection"}

    # 2. Extract Bayesian features
    feat_vals = {
        "cwc": row.get("cwc", 0.0),
        "psz_v": row.get("psz_v", 0.0),
        "fas": row.get("fas", 0.0),
        "cts_accel": row.get("cts_accel", 0.0),
        "pdd_30": row.get("pdd_30", 0.0),
        "pdd_120": row.get("pdd_120", 0.0),
        "range_pos_10": row.get("range_pos_10", 0.0),
        "range_pos_63": row.get("range_pos_63", 0.0),
        "range_pos_252": row.get("range_pos_252", 0.0),
        "dv_shock": row.get("dv_shock", 0.0),
        "esr": row.get("esr", 0.0),
        "base_tightness": row.get("base_tightness", 1.0),
        "cts": row.get("cts", 0.0),
        "cwc_slope": row.get("cwc_slope", 0.0),
        "price_slope_z": row.get("price_slope_z", 0.0),
        "rdv_slope_z": row.get("rdv_slope_z", 0.0),
        "psz_decel_3b": row.get("psz_decel_3b", 0.0),
    }

    # 3. Regime sub-model routing
    regime = row.get("regime", "notrend")
    cb_cfg = cfg.custom_bayesian
    
    # Check if sub-models are defined and have weights trained
    has_accum = hasattr(cb_cfg, "accumulation") and getattr(cb_cfg.accumulation, "feature_weights", None)
    has_mom = hasattr(cb_cfg, "momentum") and getattr(cb_cfg.momentum, "feature_weights", None)
    
    if has_accum and has_mom:
        if regime in ["downtrend", "notrend"]:
            active_sub_cfg = cb_cfg.accumulation
            sub_name = "accumulation"
        else:
            active_sub_cfg = cb_cfg.momentum
            sub_name = "momentum"
    else:
        active_sub_cfg = None
        sub_name = "unified"

    if active_sub_cfg is not None:
        score_threshold = active_sub_cfg.score_threshold
        feature_weights = active_sub_cfg.feature_weights
    else:
        score_threshold = cb_cfg.score_threshold
        feature_weights = getattr(cb_cfg, "feature_weights", {})

    bayesian_score = 0.0
    for feat, val in feat_vals.items():
        if val is None or (isinstance(val, float) and math.isnan(val)):
            val = 0.0
        weights = feature_weights.get(feat, [])
        for left, right, w in weights:
            if left < val <= right:
                bayesian_score += w
                break

    # Check 1: Symbol-specific optimized threshold check.
    # Note: If this threshold is optimized to be < 0.0, Check 2 below acts as a global floor,
    # meaning any score below 0.0 will still be rejected in practice.
    if bayesian_score < score_threshold:
        return False, 0, {
            "reason": f"Custom Bayesian rejected: score ({bayesian_score:.4f}) < {score_threshold:.4f} (model={sub_name})",
            "bayesian_score": bayesian_score,
            "score_threshold": score_threshold,
            "regime": regime,
        }

    # Convert log-odds score to probability-like 0-100 metric
    try:
        prob = 1.0 / (1.0 + math.exp(-bayesian_score))
    except OverflowError:
        prob = 1.0 if bayesian_score > 0 else 0.0
    score = int(prob * 100)

    # Check 2: Minimum Probability Gate / Global Floor
    # Reject entries where log-odds are negative (prob < 50%) — the model
    # itself has lower confidence than a coin flip, even if the raw score
    # clears the symbol-specific threshold (which can be calibrated < 0).
    # This acts as a hard safety floor of 0.0.
    if bayesian_score < 0:
        return False, 0, {
            "reason": f"Custom Bayesian rejected: prob < 50% (bayesian_score={bayesian_score:.4f}, model={sub_name})",
            "bayesian_score": bayesian_score,
            "score_threshold": score_threshold,
            "regime": regime,
        }

    details = {
        "reason": f"Custom Bayesian accepted (score={bayesian_score:.4f}, model={sub_name})",
        "entry_tag": EntryTag.CUSTOM_BAYESIAN.value,
        "score": score,
        "conv_score": score,
        "bayesian_score": bayesian_score,
        "score_threshold": score_threshold,
        "regime": regime,
    }

    return True, score, details
