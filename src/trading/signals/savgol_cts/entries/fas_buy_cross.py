from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import FasBuyCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.entries.prt_slope_zero_cross import is_flattish_line_adaptive


def check_fas_buy_cross(
    row: dict,
    prev_row: dict,
    cfg: FasBuyCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for FAS Buy Cross path."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    if idx < 10 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    fas = row.get("fas", np.nan)
    prev_fas = prev_row.get("fas", np.nan)
    fas_bt = row.get("fas_buy_threshold", np.nan)
    prev_fas_bt = prev_row.get("fas_buy_threshold", np.nan)
    
    cts = row.get("cts", np.nan)
    cts_bt = row.get("cts_buy_threshold", np.nan)
    
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev2_cts_accel = records[idx-2].get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    
    low_px = row.get("low", np.nan)
    prev_high = prev_row.get("high", np.nan)
    
    if any(np.isnan(x) for x in [fas, prev_fas, fas_bt, prev_fas_bt, cts, cts_bt, cts_accel, prev_cts_accel, prev2_cts_accel, cts_accel_threshold, low_px, prev_high]):
        return False, 0, {"reason": "Missing data for FAS Buy Cross"}

    # 1. fas crosses fas_bt
    if not (prev_fas <= prev_fas_bt and fas > fas_bt):
        return False, 0, {"reason": f"fas did not cross fas_bt ({prev_fas:.3f} -> {fas:.3f})"}

    # 2. cts <= cfg.cts_max and cts <= cts_bt
    # if not (cts <= cfg.cts_max and cts_bt <= cfg.cts_bt_max and cts <= cts_bt):
    if not (cts <= cfg.cts_max and cts <= cts_bt):
        return False, 0, {"reason": f"cts ({cts:.3f}) or cts_bt ({cts_bt:.3f}) condition failed"}

    # 3. cts_accel should be rising and not flat
    if not (cts_accel > prev_cts_accel > prev2_cts_accel):
        return False, 0, {"reason": f"cts_accel not 3-bar rising"}
        
    lookback_size = 10
    cts_accel_lookback = np.array([
        records[idx - n].get("cts_accel", np.nan) 
        for n in range(lookback_size, 0, -1)
    ])
    
    flat_check = is_flattish_line_adaptive(
        cts_accel, prev_cts_accel, prev2_cts_accel, 
        cts_accel_lookback, sensitivity=0.05
    )
    if flat_check["is_valid"]:
        return False, 0, {"reason": "cts_accel is flat"}

    # 4. cts_accel > cts_accel_threshold
    if cts_accel <= cts_accel_threshold:
        return False, 0, {"reason": f"cts_accel ({cts_accel:.3f}) <= threshold ({cts_accel_threshold:.3f})"}

    # 5. no gap ups between current and prev candle (True Gap: Low > Prev High)
    if low_px > prev_high:
        return False, 0, {"reason": f"True Gap up detected (low {low_px:.2f} > prev_high {prev_high:.2f})"}

    base_score = 20.0
    if cts_bt <= cfg.cts_bt_max:
        base_score += 5.0
    elif cts_bt > cfg.cts_bt_max:
        base_score -= 5.0

    delta = cts_accel - cts_accel_threshold
    if delta < 0.001:
        base_score -= 15.0
    elif delta < 0.002:
        base_score -= 5.0

    psz_v = row.get("psz_v", np.nan)
    prev_psz_v = prev_row.get("psz_v", np.nan)
    prev2_psz_v = records[idx-2].get("psz_v", np.nan)
    prev3_psz_v = records[idx-3].get("psz_v", np.nan)

    if not any(np.isnan(x) for x in [psz_v, prev_psz_v, prev2_psz_v, prev3_psz_v]):
        psz_v_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(lookback_size, 0, -1)
        ])
        
        flat_check_psz_v = is_flattish_line_adaptive(
            psz_v, prev_psz_v, prev2_psz_v, 
            psz_v_lookback, sensitivity=0.15
        )
        if flat_check_psz_v["is_valid"]:
            return False, 0, {"reason": "psz_v is flat"}
            
        # Check if it spiked from a dead/flat base
        psz_v_prev_lookback = np.array([
            records[idx - n].get("psz_v", np.nan) 
            for n in range(lookback_size + 1, 1, -1)
        ])
        
        velocity_was_flat_results = is_flattish_line_adaptive(
            y1=prev_psz_v, y2=prev2_psz_v, y3=prev3_psz_v, 
            lookback_window_data=psz_v_prev_lookback, 
            sensitivity=0.15
        )
        if velocity_was_flat_results["is_valid"]:
            return False, 0, {"reason": "psz_v spiked from flat base"}
            
        if psz_v < 0:
            base_score += 5.0

    min_score = getattr(cfg, "min_score", 15.0)
    if base_score < min_score:
        return False, 0, {"reason": f"Score {base_score} < min {min_score} (delta={delta:.4f})"}

    intensity, meta = compute_intensity(row, prev_row, EntryTag.FAS_BUY_CROSS, override_score=base_score)
    meta["score"] = float(intensity)

    return True, intensity, meta
