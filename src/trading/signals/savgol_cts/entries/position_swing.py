"""Path 7 -- Position Swing entry.

Position trading strategy targeting deep value in a long-term structural uptrend.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_position_swing(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check Path 7 entry conditions (6-gate process)."""
    ps_cfg = cfg.position_swing
    if not ps_cfg.enabled:
        return False, 0, {"reason": "Position Swing disabled"}

    # Basic data presence check
    range_pos_63 = row.get("range_pos_63", np.nan)
    pdd_120 = row.get("pdd_120", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cwc_slope = row.get("cwc_slope", np.nan)
    coherence = row.get("coherence", np.nan)
    
    # Market PSZ is optional (might not be in standard ledger unless injected)
    # We default to 0.0 (neutral) if it's missing so the signal can run standalone
    mkt_psz = row.get("mkt_psz", np.nan)
    if np.isnan(mkt_psz):
        mkt_psz = 0.0

    if any(np.isnan(v) for v in [range_pos_63, pdd_120, cts_accel, cwc_slope, coherence]):
        return False, 0, {"reason": "Missing data for Position Swing gates"}

    # 1. Deep 3-month pullback
    if range_pos_63 >= ps_cfg.rp63_max:
        return False, 0, {"reason": f"rp63 {range_pos_63:.2f} >= {ps_cfg.rp63_max}"}

    # 2. Asset is structurally trending up long-term
    if pdd_120 <= ps_cfg.pdd_min:
        return False, 0, {"reason": f"pdd120 {pdd_120:.2f} <= {ps_cfg.pdd_min}"}

    # 3. Sharp momentum return
    if cts_accel <= ps_cfg.cts_accel_min:
        return False, 0, {"reason": f"cts_accel {cts_accel:.4f} <= {ps_cfg.cts_accel_min}"}

    # 4. Institutional backing
    if cwc_slope <= ps_cfg.cwc_slope_min:
        return False, 0, {"reason": f"cwc_slope {cwc_slope:.4f} <= {ps_cfg.cwc_slope_min}"}

    # 5. Market not in a crash
    if mkt_psz <= ps_cfg.mkt_psz_min:
        return False, 0, {"reason": f"mkt_psz {mkt_psz:.2f} <= {ps_cfg.mkt_psz_min}"}

    # 6. Trend is coherent
    if coherence <= ps_cfg.coherence_min:
        return False, 0, {"reason": f"coherence {coherence:.2f} <= {ps_cfg.coherence_min}"}

    # Conviction scoring
    score = 10
    if range_pos_63 < 0.2: score += 5
    if pdd_120 > 0.05: score += 5
    if cts_accel > 0.05: score += 5
    if coherence > 0.5: score += 5

    # All gates passed
    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.POSITION_SWING,
        [f"rp63={range_pos_63:.2f}", f"pdd120={pdd_120:.2f}", f"cts_accel={cts_accel:.3f}"],
    )
    meta["conv_score"] = score
    return True, intensity_int, meta
