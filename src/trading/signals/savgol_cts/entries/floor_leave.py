"""Path 1 — CTS Floor-Leave entry.

CTS rises above floor after being pinned.  Fires on the lift bar, not
the touch bar — confirms floor held before entry.
Empirically (NIFTY 500): 63.9% WR, +2.52% avg, 73.1% ceiling exits.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_floor_leave(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 1 entry conditions.

    Requires:
    - prev_cts at or below floor zone, current cts above it.
    - PSZ still negative (not a late entry).
    - CWVAP distance above trap floor.
    - CTS not jumped too far (vertical jump guard).
    - |PSZV| above conviction minimum.
    """

    if not cfg.floor_leave.enabled:
        return False, 0, {"reason": "Floor Leave disabled"}

    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False, 0, {"reason": "Missing CTS data"}

    psz_raw = row.get("price_slope_z", np.nan)
    if not np.isnan(psz_raw) and (psz_raw >= 0 or psz_raw >= cfg.psz_min_threshold):
        return False, 0, {"reason": f"PSZ: ({psz_raw:.3f}), late entry"}

    floor = cfg.cts_floor + cfg.floor_zone_tolerance
    if not (prev_cts <= floor and cts > floor):
        return False, 0, {"reason": "CTS not leaving floor"}

    # 1. Data Validation Gate
    cwvap = row.get("cwvap", np.nan)
    close = row.get("close", np.nan)
    psz_v = row.get("psz_v", np.nan)
    if np.isnan(cwvap) or np.isnan(close) or np.isnan(psz_v) or cwvap <= 0:
        return False, 0, {"reason": "Insufficient data"}

    # 2. Signal-Day Trap Floor: hard rejection for entries deeper than threshold
    dist_pct = ((close - cwvap) / cwvap) * 100.0
    if dist_pct <= cfg.floor_leave.cwvap_trap_hi:
        return False, 0, {"reason": f"Signal-Day Trap: {dist_pct:.1f}%"}

    # 3. Vertical Jump Ceiling: reject spikes already in exhaustion cliff
    if cts > cfg.floor_leave.cts_max:
        return False, 0, {"reason": f"Vertical Jump Ceiling: CTS {cts:.3f}"}

    # 4. Conviction Gate: reject "Dead Momentum" signals (|PSZV| too low)
    if abs(psz_v) < cfg.floor_leave.pszv_min:
        return False, 0, {"reason": f"Dead Momentum: PSZV {psz_v:.4f}"}

    # 5. Direction Gate: reject entries where momentum is still falling
    if cfg.floor_leave.pszv_direction_gate and psz_v <= 0:
        return False, 0, {"reason": f"Falling Momentum: PSZV {psz_v:.4f}"}

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.CTS_FLOOR_LEAVE,
        [f"prev_cts={prev_cts:.3f}"],
    )
    return True, intensity_int, meta
