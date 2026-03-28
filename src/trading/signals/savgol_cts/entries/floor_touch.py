"""Path 0 — Floor Touch entry.

Enter while CTS and BT are both pinned at floor and PSZ is deeply oversold.
Captures entries earlier than Floor-Leave.
Empirically (NIFTY 500): 64.8% WR, +2.50% avg, >10% winner rate 30%.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_floor_touch(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 0 entry conditions.

    Requires:
    - CTS and BT both at or below floor zone (cts_floor + floor_zone_tolerance).
    - PSZ below ``cfg.floor_touch.psz_max``.
    - CWVAP distance above ``cfg.cwvap_max_dist`` (if enabled).
    - DVWAP bear-stack gate not active (if enabled).
    """
    if not cfg.floor_touch.enabled:
        return False, 0, {"reason": "Floor Touch disabled"}

    cts = row.get("cts", np.nan)
    bt = row.get("cts_buy_threshold", np.nan)
    if np.isnan(cts) or np.isnan(bt):
        return False, 0, {"reason": "Missing CTS/BT data"}

    floor = cfg.cts_floor + cfg.floor_zone_tolerance
    if not (cts <= floor and bt <= floor):
        return False, 0, {"reason": f"CTS/BT not pinned: CTS={cts:.3f}, BT={bt:.3f}"}

    psz_raw = row.get("price_slope_z", np.nan)
    if np.isnan(psz_raw) or psz_raw >= cfg.floor_touch.psz_max:
        return False, 0, {"reason": f"PSZ [{psz_raw:.3f}] >= {cfg.floor_touch.psz_max}"}

    # CWVAP distance guard (shared with Floor-Leave)
    if cfg.cwvap_max_dist < 0.0:
        cwvap = row.get("cwvap", np.nan)
        close = row.get("close", np.nan)
        if not np.isnan(cwvap) and not np.isnan(close) and cwvap > 0:
            cwvap_dist_pct = (close - cwvap) / cwvap * 100.0
            if cwvap_dist_pct < cfg.cwvap_max_dist:
                return False, 0, {
                    "reason": f"CWVAP dist {cwvap_dist_pct:.1f}% < {cfg.cwvap_max_dist:.1f}%",
                }

    # DVWAP bear stack gate: reject when all anchors in bearish alignment
    if cfg.dvwap_bear_stack_gate_enabled and row.get("dvwap_bear_stack", False):
        return False, 0, {"reason": "DVWAP bear stack gate"}

    coh = row.get("coherence", np.nan)
    pdd = row.get("pdd_120", np.nan)
    regime = row.get("regime", "")
    intensity_int, meta = compute_intensity(
        cts, coh, pdd, regime, EntryTag.CTS_FLOOR_TOUCH, [f"bt={bt:.3f}"],
    )
    return True, intensity_int, meta
