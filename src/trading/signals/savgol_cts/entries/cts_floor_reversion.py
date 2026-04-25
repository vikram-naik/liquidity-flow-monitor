from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import CtsFloorReversionEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_cts_floor_reversion(
    row: dict,
    prev_row: dict,
    cfg: CtsFloorReversionEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for CTS Floor Reversion path.
    
    Rules:
    - CTS and BT stuck at floor (-1) for 5 days.
    - Day 6 must not meet this condition.
    - psz_v must be turning positive (momentum rising).
    - Multi-factor scoring must hit min_score.
    - Price structural exhaustion guard (price_slope_z <= -0.15).
    """
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    if idx < 5 or records is None:
        return False, 0, {"reason": "Insufficient history"}

    # 1. GATE 1 & 2: 5-bar floor condition
    was_deep_floor = True
    for j in range(5):
        # Allow slight floating point variations
        if records[idx - j].get("cts", 0) > -0.999 or records[idx - j].get("cts_buy_threshold", 0) > -0.999:
            was_deep_floor = False
            break

    if not was_deep_floor:
        return False, 0, {"reason": "CTS and BT not at floor for 5 bars"}

    # Gate 2: The 6th bar back must not have met this condition
    if idx >= 5:
        if records[idx - 5].get("cts", 0) <= -0.999 and records[idx - 5].get("cts_buy_threshold", 0) <= -0.999:
            return False, 0, {"reason": "Already at floor on 6th day (only enter on exactly 5th day)"}

    # 3. SCORING SYSTEM (Base Score 10.0)
    score = 10.0
    
    # Structural Depth (PRT)
    prt_now = row.get("prt", np.nan)
    if not np.isnan(prt_now):
        if prt_now <= -0.6: score += 4.0
        elif prt_now <= -0.5: score += 3.0
        elif prt_now <= -0.4: score += 2.0
        elif prt_now <= -0.2: score += 0.0
        else: score -= 5.0  # Shallow PRT penalty

    # Momentum Violence (PSZ_V over last 3 days)
    psz_v_0 = row.get("psz_v", np.nan)
    psz_v_1 = records[idx - 1].get("psz_v", np.nan)
    
    if idx >= 3:
        psz_v_2 = records[idx - 2].get("psz_v", np.nan)
        psz_v_3 = records[idx - 3].get("psz_v", np.nan)
    else:
        psz_v_2 = np.nan
        psz_v_3 = np.nan

    # Gate 3: Basic guard that momentum is turning positive today
    if not np.isnan(psz_v_0) and not np.isnan(psz_v_1) and psz_v_0 <= psz_v_1:
        return False, 0, {"reason": "psz_v not rising"}

    if not any(np.isnan(x) for x in [psz_v_1, psz_v_2, psz_v_3]):
        min_psz_v_3d = min(psz_v_1, psz_v_2, psz_v_3)
        max_abs_psz_v_3d = max(abs(psz_v_1), abs(psz_v_2), abs(psz_v_3))
        psz_v_std = np.std([psz_v_1, psz_v_2, psz_v_3])
        
        if min_psz_v_3d <= -0.04: score += 4.0
        elif min_psz_v_3d <= -0.02: score += 2.0
        elif min_psz_v_3d <= -0.01: score += 0.0
        else: score -= 4.0  # Weak momentum penalty
        
        # Miniscule/Flat Penalty (Filter dead-cat/noise floors)
        if max_abs_psz_v_3d < 0.025 and psz_v_std < 0.008:
            score -= 5.0

    # Gate 4: Soft Gate for Score
    if score < cfg.min_score:
        return False, 0, {"reason": f"Score {score} < {cfg.min_score}"}

    # Gate 5: Structural Exhaustion Guard
    psz_now = row.get("price_slope_z", np.nan)
    if not np.isnan(psz_now) and psz_now > -0.15:
        return False, 0, {"reason": f"price_slope_z {psz_now:.3f} > -0.15"}

    # Intensity calculation
    intensity, meta = compute_intensity(row, prev_row, EntryTag.CTS_FLOOR_REVERSION, override_score=score)
    meta["score"] = float(intensity)

    return True, intensity, meta
