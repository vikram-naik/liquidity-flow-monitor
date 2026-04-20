"""Path 6 -- Range Reversion entry.

Mean-reversion strategy using price range position features.
Identifies setups where price is near 52-week lows with institutional 
capitulation confirmed, basing formed, and momentum velocity improving.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_range_reversion(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check Path 6 entry conditions (10-gate process)."""
    rr_cfg = cfg.range_reversion
    if not rr_cfg.enabled:
        return False, 0, {"reason": "Range Reversion disabled"}

    # Basic data presence check
    rp252 = row.get("range_pos_252", np.nan)
    rp63 = row.get("range_pos_63", np.nan)
    rp10 = row.get("range_pos_10", np.nan)
    rp10_prev = prev_row.get("range_pos_10", np.nan)
    close = row.get("close", np.nan)
    prev_close = prev_row.get("close", np.nan)
    bars_at_base = row.get("bars_at_base", 0)
    rw10 = row.get("range_width_10", np.nan)
    atr = row.get("atr_20", np.nan)
    cts = row.get("cts", np.nan)
    cts_s = row.get("cts_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_prev = prev_row.get("cts_accel", np.nan)
    psz_v = row.get("psz_v", np.nan)

    if any(np.isnan(v) for v in [rp252, rp63, rp10, rp10_prev, close, prev_close, rw10, atr, cts, cts_s, cts_accel, psz_v]):
        return False, 0, {"reason": "Missing data for Range Reversion gates"}

    # 1. Annual Oversold
    if rp252 >= rr_cfg.rp252_max:
        return False, 0, {"reason": f"rp_252 {rp252:.2f} >= {rr_cfg.rp252_max}"}

    # 2. Quarterly Oversold
    if rp63 >= rr_cfg.rp63_max:
        return False, 0, {"reason": f"rp_63 {rp63:.2f} >= {rr_cfg.rp63_max}"}

    # 3. Short-term inflecting
    if rp10 <= rp10_prev:
        return False, 0, {"reason": f"rp_10 {rp10:.2f} <= prev {rp10_prev:.2f}"}

    # 4. Green candle
    if close <= prev_close:
        return False, 0, {"reason": "Not a green candle (close <= prev_close)"}

    # 5. Base formed
    if bars_at_base < rr_cfg.bars_at_base_min:
        return False, 0, {"reason": f"bars_at_base {bars_at_base} < {rr_cfg.bars_at_base_min}"}

    # 6. ATR-relative range tight
    rw10_in_atrs = (rw10 / 100 * close) / atr if atr > 0 else 99
    if rw10_in_atrs >= rr_cfg.rw10_atrs_max:
        return False, 0, {"reason": f"rw10_atrs {rw10_in_atrs:.2f} >= {rr_cfg.rw10_atrs_max}"}

    # 7. Institutional capitulation
    if cts > rr_cfg.cts_max:
        return False, 0, {"reason": f"cts {cts:.2f} > {rr_cfg.cts_max}"}

    # 8. Momentum velocity improving
    if psz_v <= 0:
        return False, 0, {"reason": f"psz_v {psz_v:.6f} <= 0"}

    # 9. Institutions not in freefall
    if cts_s < rr_cfg.cts_slope_min:
        return False, 0, {"reason": f"cts_slope {cts_s:.4f} < {rr_cfg.cts_slope_min}"}

    # 10. Selling not accelerating
    is_falling = close < prev_close # redundant here as we checked is_green, but following study logic
    accel_deteriorating = cts_accel < 0 and cts_accel < cts_accel_prev
    if accel_deteriorating and is_falling:
        return False, 0, {"reason": "Selling accelerating while falling"}

    # Conviction scoring (matches study logic)
    score = 10
    if rp252 < 0.15: score += 5
    if rp63 < 0.15: score += 5
    if rp10 < 0.30: score += 3
    if bars_at_base >= 10: score += 3
    if rw10 < 3.0: score += 2
    if cts < -0.50: score += 2
    if cts_s > prev_row.get("cts_slope", 0): score += 3
    psz_v_prev = prev_row.get("psz_v", 0)
    if psz_v > psz_v_prev: score += 2

    # All gates passed
    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.RANGE_REVERSION,
        [f"rp252={rp252:.2f}", f"rw10_atrs={rw10_in_atrs:.2f}", f"cts={cts:.2f}"],
    )
    meta["conv_score"] = score
    meta["score"] = float(intensity_int)
    return True, intensity_int, meta
