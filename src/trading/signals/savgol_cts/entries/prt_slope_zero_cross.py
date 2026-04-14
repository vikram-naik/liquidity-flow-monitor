from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import PrtSlopeZeroCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_prt_slope_zero_cross(
    row: dict,
    prev_row: dict,
    cfg: PrtSlopeZeroCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for PRT Slope Zero Cross path."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    # Extract required fields
    prt_slope = row.get("prt_slope", np.nan)
    prev_prt_slope = prev_row.get("prt_slope", np.nan)
    prt_accel = row.get("prt_accel", np.nan)
    prt = row.get("prt",np.nan)
    prev_prt = prev_row.get("prt",np.nan)
    prev_prt_1 = records[idx-2].get("prt",np.nan)
    
    fas = row.get("fas", np.nan)
    
    psz = row.get("price_slope_z", np.nan)
    psz_v = row.get("psz_v", np.nan)
    psz_v_1 = prev_row.get("psz_v", np.nan)
    psz_v_2 = records[idx-2].get("psz_v", np.nan)
    
    cts = row.get("cts", np.nan)
    cts_slope = row.get("cts_slope", np.nan)
    prev_cts_slope = prev_row.get("cts_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    
    rdv_slope_z = row.get("rdv_slope_z", np.nan)

    if any(np.isnan(x) for x in [prt, prev_prt_1, prt_slope, prev_prt_slope, prt_accel, fas, psz, psz_v, psz_v_1, psz_v_2, cts, cts_slope, prev_cts_slope, cts_accel, cts_accel_threshold, rdv_slope_z]):
        return False, 0, {"reason": "Missing data for PRT Slope Zero Cross"}

    # 1. PRT Slope crosses above zero
    if not (prev_prt_slope < 0 and prt_slope > 0):
        return False, 0, {"reason": f"prt_slope did not cross above zero ({prev_prt_slope:.3f} -> {prt_slope:.3f})"}
    
    # 2. PRT Trend rising and above threshold
    if not (prt_slope >= cfg.prt_slope_min):
        return False, 0, {"reason": f"prt_slope ({prt_slope:.3f}) < min ({cfg.prt_slope_min:.3f})"}
    
    # 2.1 PRT is not flat.
    if not(prt - prev_prt_1 > 0.05):
        return False, 0, {"reason": f"prt - prev_prt_1 ({(prt - prev_prt_1):.4f}) > 0.05."}

    # 3. FAS alignment
    if not (cfg.fas_min <= fas < cfg.fas_max):
        return False, 0, {"reason": f"fas ({fas:.3f}) outside range ({cfg.fas_min:.3f}, {cfg.fas_max:.3f})"}
    
    # 4. PSZ oversold and rising
    if not (psz < cfg.psz_threshold):
        return False, 0, {"reason": f"psz ({psz:.3f}) >= threshold ({cfg.psz_threshold:.3f})"}

    
    # 5. CTS Alignment
    if not (cts < 0):
        return False, 0, {"reason": f"cts ({cts:.3f}) >= 0"}
    
    # 6. Guards
    if prt_accel > cfg.prt_accel_max:
        return False, 0, {"reason": f"prt_accel ({prt_accel:.3f}) > max ({cfg.prt_accel_max:.3f})"}

    # --- SCORING LOGIC (0 to 50 path points) ---
    path_score = 10.0

    # A. CTS Slope Confluence (Max 1)
    if cts_slope < -0.05:
        path_score += 1.0  
        if cts_slope > prev_cts_slope and abs(cts_slope - prev_cts_slope) > 0.01:
            path_score += 1.0  # Ideal: Decelerating selling
    else:
        path_score += -3.0  # We have already run up a bit.
    
    # B. CTS Acceleration State (Max 2)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev2_cts_accel = np.nan
    if records and idx >= 2:
        prev2_cts_accel = records[idx - 2].get("cts_accel", np.nan)

    if not np.isnan(cts_accel) and not np.isnan(cts_accel_threshold) and not np.isnan(prev_cts_accel):
        if cts_accel >= 0:
            if cts_accel <= cts_accel_threshold:
                path_score += -4.0
            else:
                path_score += 1.0
                if abs(cts_accel - cts_accel_threshold) >= 0.01 and abs(cts_accel - prev_cts_accel) >= 0.01:
                    path_score += 2.0
                else:
                    path_score += -2.0
                # if cts_accel > prev_cts_accel:
                #     path_score += 1.0
                # else:
                #     path_score += -1.0

            # C. CTS Acceleration Momentum Bonus (Max 1)
            if not np.isnan(prev2_cts_accel):
                if cts_accel > prev_cts_accel and prev_cts_accel > prev2_cts_accel:
                    path_score += 1.0
        else:
            # penalize if acceleration is negative.
            path_score += -4.0

    if fas < -0.5:
        # good if the price is deep.
        path_score += 1.0
        if fas <= -0.8:
            # below -0.8 and -1.0 price almost bottom'd out so bonus    
            path_score += 2.0
    else:
        # penalize, if the price is already higher up.
        path_score += -1.0

    # Price momentum, below threshold
    if not (psz_v > cfg.psz_v_min):
        path_score += 2.0
    else:
        path_score += -2.0

    # D. RDV Slope Z Bonus/Penalty (+1 / -1)
    # if rdv_slope_z > 0:
    #     path_score += 1.0
    # else:
    #     path_score -= 1.0

    # E. PSZ Velocity Rising Bonus/Penalty (+1 / -1)
    if psz_v > psz_v_1:
        path_score += 1.0
        if abs(psz_v - psz_v_1) > 0.01:
            path_score += 1.0
        else:
            # take it back, no negative marking, but no +ve as well.
            path_score += -1.0

        sum_total = abs(psz_v + psz_v_1 + psz_v_2)
        if (sum_total/3) > 0.01:
            path_score += 1.0
        else:
            path_score += -2.0            
    else:
        path_score -= 1.0

    # if prt_slope crosses with momentum, the trade fizzles out early, so penalize.
    # WIRPO: dt: 25-Mar-2025
    if prt_slope > 0.1:
        path_score -= 1.0

    if path_score <= cfg.min_score:
        return False, 0, {"reason": f"path_score ({path_score}) <= min ({cfg.min_score})"}

    base_score = 0.0

    override_score = base_score + path_score

    intensity, meta = compute_intensity(row, prev_row, EntryTag.PRT_SLOPE_ZERO_CROSS, override_score=override_score)
    meta["path_score"] = path_score
    meta["score"] = override_score

    return True, intensity, meta
