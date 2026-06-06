from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag

def entry_cdvl_cts(
    row: dict,
    prev_row: dict,
    cfg,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """CDVL-CTS entry check logic.
    
    Triggers when:
    1. CDVL turns positive (prev_cdvl <= 0.0 and cdvl > 0.0)
    2. CTS negative and rising or flat bottom:
       cts < 0.0 and (cts > prev_cts or (cts == -1.0 and prev_cts == -1.0))
    3. CTS <= dynamic buy threshold (with absolute floor gate: if cts == -1.0, cts_buy_threshold must be exactly -1.0)
    4. CWC coherence filter: cwc > cwc_min (default 0.5)
    5. CTS acceleration rising: cts_accel > prev_cts_accel (1-bar check)
    6. CTS acceleration dynamic threshold gate: cts_accel > cts_accel_threshold
    """
    # 1. Config Check
    cdvl_cts_cfg = getattr(cfg, "cdvl_cts", None)
    if cdvl_cts_cfg is not None:
        if not cdvl_cts_cfg.enabled:
            return False, 0, {"reason": "CDVL_CTS path disabled"}
        cwc_min = getattr(cdvl_cts_cfg, "cwc_min", 0.50)
    else:
        cwc_min = 0.50

    if not prev_row:
        return False, 0, {"reason": "Missing historical prev_row context"}

    # --- Feature Extraction ---
    cdvl = row.get("cdvl", np.nan)
    prev_cdvl = prev_row.get("cdvl", np.nan)
    
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_buy_threshold = row.get("cts_buy_threshold", np.nan)
    
    cwc = row.get("cwc", np.nan)
    
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    # --- Check for NaNs ---
    if any(np.isnan(x) for x in [cdvl, prev_cdvl, cts, prev_cts, cts_buy_threshold, cwc, cts_accel, prev_cts_accel, cts_accel_threshold]):
        return False, 0, {"reason": "Missing CDVL/CTS telemetry signals (NaN values)"}

    # 1. CDVL Cross positive
    if not (prev_cdvl <= 0.0 and cdvl > 0.0):
        return False, 0, {"reason": f"CDVL did not turn positive (prev: {prev_cdvl:.4f}, curr: {cdvl:.4f})"}

    # 2. CTS structure gate: negative and rising or flat bottom
    cts_rising_or_flat = (cts > prev_cts) or (cts == -1.0 and prev_cts == -1.0)
    if not (cts < 0.0 and cts_rising_or_flat):
        return False, 0, {"reason": f"CTS not negative/rising or flat bottom (curr: {cts:.2f}, prev: {prev_cts:.2f})"}

    # 3. CTS <= dynamic buy threshold with absolute floor gate
    if cts == -1.0:
        cts_threshold_ok = (cts_buy_threshold == -1.0)
    else:
        cts_threshold_ok = (cts <= cts_buy_threshold)
        
    if not cts_threshold_ok:
        return False, 0, {"reason": f"CTS dynamic buy threshold gate failed (cts: {cts:.2f}, buy_threshold: {cts_buy_threshold:.2f})"}

    # 4. CWC Coherence Filter
    if not (cwc > cwc_min):
        return False, 0, {"reason": f"CWC coherence filter failed (cwc: {cwc:.4f} <= {cwc_min:.2f})"}

    # 5. CTS acceleration rising (1-bar check)
    if not (cts_accel > prev_cts_accel):
        return False, 0, {"reason": f"CTS acceleration not rising (prev: {prev_cts_accel:.4f}, curr: {cts_accel:.4f})"}

    # 6. CTS acceleration dynamic threshold gate
    if not (cts_accel > cts_accel_threshold):
        return False, 0, {"reason": f"CTS acceleration below dynamic threshold (accel: {cts_accel:.4f} <= threshold: {cts_accel_threshold:.4f})"}

    # Accepted!
    
    # Bayesian Adaptive Scorer Post-Filter
    if getattr(cdvl_cts_cfg, "bayesian_mode", False):
        cdvl_surge = cdvl - prev_cdvl
        cts_accel_surge = cts_accel - prev_cts_accel
        
        feat_vals = {
            "cdvl": cdvl,
            "cdvl_surge": cdvl_surge,
            "cts": cts,
            "cts_accel": cts_accel,
            "cwc": cwc,
            "cts_accel_surge": cts_accel_surge,
        }
        
        bayesian_score = 0.0
        feature_weights = getattr(cdvl_cts_cfg, "feature_weights", {})
        
        for feat, val in feat_vals.items():
            weights = feature_weights.get(feat, [])
            for left, right, w in weights:
                if left < val <= right:
                    bayesian_score += w
                    break
                    
        if bayesian_score < cdvl_cts_cfg.score_threshold:
            return False, 0, {"reason": f"CDVL_CTS rejected: Bayesian score ({bayesian_score:.4f}) < {cdvl_cts_cfg.score_threshold:.4f}"}

    score = 85  # CDVL-CTS custom score fallback
    if getattr(cdvl_cts_cfg, "bayesian_mode", False):
        prob = 1.0 / (1.0 + np.exp(-bayesian_score))
        score = int(prob * 100)

    details = {
        "reason": "CDVL_CTS entry path accepted",
        "entry_tag": EntryTag.CDVL_CTS.value,
        "score": score,
        "conv_score": score,
        "cdvl": cdvl,
        "cts": cts,
        "cwc": cwc,
        "cts_accel": cts_accel,
    }
    return True, score, details
