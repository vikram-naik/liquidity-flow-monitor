from src.trading.signals.savgol_cts.ml_guard import MLGuard
from src.trading.signals.enums import EntryTag

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
    prev_prt = prev_row.get("prt_slope", 0)
    prt = row.get("prt_slope", 0)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    cts_bt = row.get("cts_buy_threshold", -0.8)
    prev_cts = prev_row.get("cts", 0)
    cts = row.get("cts", 0)
    trigger_cts = 1 if (prev_cts <= cts_bt and cts > cts_bt) else 0

    accel_bt = row.get("cts_accel_threshold", 0.0)
    prev_accel = prev_row.get("cts_accel", 0)
    accel = row.get("cts_accel", 0)
    trigger_accel = 1 if (prev_accel <= accel_bt and accel > accel_bt) else 0

    if not any([trigger_prt, trigger_fas, trigger_cts, trigger_accel]):
        return False, 0, {"reason": "No structural inflection"}

    # 2. Score with ML Guard
    guard = MLGuard.get_instance()
    
    # We must explicitly add the trigger columns to the row dictionary 
    # so the ML Guard can map them to the feature vector correctly.
    row_dict = row.to_dict() if hasattr(row, 'to_dict') else dict(row)
    row_dict.update({
        "trigger_prt": trigger_prt,
        "trigger_fas": trigger_fas,
        "trigger_cts": trigger_cts,
        "trigger_accel": trigger_accel
    })

    ml_score_pct = guard.score_setup(row_dict)
    if ml_score_pct is None:
        return False, 0, {"reason": "ML Guard unavailable"}

    ml_score_pct *= 100.0

    if ml_score_pct < cfg.universal_cross.min_ml_score:
        return False, 0, {"reason": f"Universal ML Guard rejected ({ml_score_pct:.1f}%)"}

    # 3. Accept Setup
    details = {
        "reason": "Universal Cross accepted",
        "entry_tag": EntryTag.UNIVERSAL_CROSS.value, # Use standardized Universal exit logic
        "override_score": ml_score_pct,
        "score": int(ml_score_pct),
        "ml_score": ml_score_pct,
        "trigger_prt": bool(trigger_prt),
        "trigger_fas": bool(trigger_fas),
        "trigger_cts": bool(trigger_cts),
        "trigger_accel": bool(trigger_accel)
    }

    return True, int(ml_score_pct), details
