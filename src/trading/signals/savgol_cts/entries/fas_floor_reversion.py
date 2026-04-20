from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import FasFloorReversionEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_fas_floor_reversion(
    row: dict,
    prev_row: dict,
    cfg: FasFloorReversionEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for FAS Floor Reversion path.
    
    Rules:
    - fas < -1.10
    - prt_slope > 0
    - cts == -1
    """
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    fas = row.get("fas", np.nan)
    prt_slope = row.get("prt_slope", np.nan)
    cts = row.get("cts", np.nan)
    cts_slope = row.get("cts_slope", np.nan)

    if any(np.isnan(x) for x in [fas, prt_slope, cts, cts_slope]):
        return False, 0, {"reason": "Missing data for FAS Floor Reversion"}

    # Gate 1: FAS deep floor
    if fas >= cfg.fas_max:
        return False, 0, {"reason": f"fas {fas:.3f} >= {cfg.fas_max:.3f}"}

    # Gate 2: PRT Slope inflection (positive)
    if prt_slope <= cfg.prt_slope_min:
        return False, 0, {"reason": f"prt_slope {prt_slope:.3f} <= {cfg.prt_slope_min:.3f}"}

    # Gate 3: CTS extreme floor
    if cts > cfg.cts_max:
        return False, 0, {"reason": f"cts {cts:.3f} > {cfg.cts_max:.3f}"}

    # Gate 4: CTS slope guard (must be negative)
    if cts_slope >= cfg.cts_slope_max:
        return False, 0, {"reason": f"cts_slope {cts_slope:.3f} >= {cfg.cts_slope_max:.3f}"}

    # All gates passed, calculate intensity
    intensity, meta = compute_intensity(row, prev_row, EntryTag.FAS_FLOOR_REVERSION)
    meta["score"] = float(intensity)

    return True, intensity, meta
