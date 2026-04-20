from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import AccelZeroCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_accel_zero_cross(
    row: dict,
    prev_row: dict,
    cfg: AccelZeroCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for Accel Zero Cross path."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    # Extract required fields
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    cts_slope = row.get("cts_slope", np.nan)
    cs_1 = records[idx -1].get("cts_slope",np.nan)
    psz_v = row.get("psz_v", np.nan)
    v_1 = records[idx-1].get("psz_v", np.nan)
    v_2 = records[idx-2].get("psz_v", np.nan)
    psz = row.get("price_slope_z", np.nan)


    cts = row.get("cts", np.nan)
    cts_buy_threshold = row.get("cts_buy_threshold", np.nan)
    fas_threshold = -0.1
    fas = row.get("fas", np.nan)
    prev_fas = prev_row.get("fas", np.nan)

    if any(np.isnan(x) for x in [cts_accel, prev_cts_accel, cts_slope,cs_1, cts, cts_buy_threshold, fas, prev_fas, psz, psz_v, cts_accel_threshold]):
        return False, 0, {"reason": "Missing data for Accel Zero Cross"}

    # Gate 1: cts_accel crosses zero from negative to positive
    if not (prev_cts_accel < cts_accel_threshold and cts_accel > cts_accel_threshold and abs(cts_accel-cts_accel_threshold) > 0.01):
        return False, 0, {"reason": f"cts_accel did not cross threshold ({prev_cts_accel:.3f} -> {cts_accel:.3f})"}

    # Gate 2: cts_slope is negative
    if cts_slope >= 0:
        return False, 0, {"reason": f"cts_slope is not negative ({cts_slope:.3f})"}
    

    # Gate 3: cts <= cts_buy_threshold
    # if cts > cts_buy_threshold:
    #     return False, 0, {"reason": f"cts ({cts:.3f}) not <= buy threshold ({cts_buy_threshold:.3f})"}
    
    # Gate 4: fas <= fas_threshold
    if not(fas <= fas_threshold):
        return  False, 0, {"reason": f"fas ({fas:.3f}) not <= ({fas_threshold:.3f})"}
    
    # Gate 4.1: fas > prev_fas
    if fas < prev_fas:
        return  False, 0, {"reason": f"fas ({fas:.3f}) not rising > ({prev_fas:.3f})"}

    # Gate 5: psz_v > 0.01
    if psz_v < 0.01 :
        return  False, 0, {"reason": f"psz_v ({psz_v:.3f}) not >= 0.01 and is not rising "}
    
    # Gate 5.1: psz_v should be rising.
    if not( psz_v > v_1 > v_2):
        return  False, 0, {"reason": f"psz_v [{psz_v:.3f}, {v_1:.3f}, {v_2:.3f}] is not rising "}

    psz_threshold = -0.30
    # Gate 5.1: psz < -0.25
    if psz > psz_threshold:
        return  False, 0, {"reason": f"psz ({psz:.3f}) not < {psz_threshold:.3f} "}

    # All gates passed, calculate intensity
    intensity, meta = compute_intensity(row, prev_row, EntryTag.ACCEL_ZERO_CROSS)
    meta["score"] = float(intensity)

    return True, intensity, meta
