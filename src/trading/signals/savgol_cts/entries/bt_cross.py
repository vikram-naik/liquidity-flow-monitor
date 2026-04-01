"""Path 3 — BT-Cross entry.

CTS crosses BT from below while in oversold territory.  Catches V-bottoms
where CTS hits floor but BT hasn't caught up.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_bt_crossover(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check Path 3 entry conditions.

    Requires:
    - CTS crossed above BT from below.
    - CTS still in oversold zone.
    - PSZ still negative (not a late entry).
    - psz_v not flat for N consecutive bars (flat gate).
    - DVWAP bear-stack gate not active.
    """
    if not cfg.bt_cross.enabled:
        return False, 0, {"reason": "BT cross disabled"}

    # cts_slope positive
    # cs = row.get("cts_slope", np.nan)
    # if np.isnan(cs) or cs <= 0:
    #     return False, 0, {"reason": "cts_slope not positive"}        

    cts = row.get("cts", np.nan)
    bt = row.get("cts_buy_threshold", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    prev_bt = prev_row.get("cts_buy_threshold", np.nan)

    if np.isnan(prev_cts) or np.isnan(prev_bt) or np.isnan(bt):
        return False, 0, {"reason": "Missing CTS/BT data"}

    # Must cross BT from below
    if not (prev_cts <= prev_bt and cts > bt):
        return False, 0, {"reason": "No BT crossover"}

    # BT must be above floor — unless prev_bt was also at floor.
    # If BT was already pinned at -1.0 (both CTS and BT at floor together),
    # CTS leaving first IS a genuine recovery signal.
    # Only block when BT just dropped to floor (prev_bt was above floor).
    if bt <= cfg.cts_floor and prev_bt > cfg.cts_floor:
        return False, 0, {"reason": f"BT at floor ({bt:.3f}), not a genuine crossover"}

    # Must still be in oversold territory
    if cts > cfg.bt_cross.oversold_threshold:
        return False, 0, {
            "reason": f"CTS {cts:.3f} above oversold threshold {cfg.bt_cross.oversold_threshold}",
        }

    # PSZ must still be negative — if already positive we're entering late
    psz_raw = row.get("price_slope_z", np.nan)
    if not np.isnan(psz_raw) and (psz_raw >= 0 or psz_raw >= cfg.psz_min_threshold):
        return False, 0, {"reason": f"PSZ: ({psz_raw:.3f}), late entry"}

    # Flat psz_v gate: reject when psz_v has been quiescent (straight-line fall)
    bc = cfg.bt_cross
    if bc.flat_gate_enabled and records and idx >= bc.flat_gate_lookback:
        all_flat = True
        for j in range(idx - bc.flat_gate_lookback, idx):
            v = records[j].get("psz_v", np.nan)
            if np.isnan(v) or abs(v) >= bc.flat_gate_threshold:
                all_flat = False
                break
        if all_flat:
            return False, 0, {
                "reason": f"BT-cross flat psz_v gate ({bc.flat_gate_lookback} bars)",
            }

    # DVWAP bear stack gate: reject when all anchors in bearish alignment
    if cfg.dvwap_bear_stack_gate_enabled and row.get("dvwap_bear_stack", False):
        return False, 0, {"reason": "DVWAP bear stack gate"}

    # CWVAP distance guard: reject when price is too far below CWVAP
    cwvap = row.get("cwvap", np.nan)
    close = row.get("close", np.nan)
    cwvap_dist_pct = np.nan
    if not np.isnan(cwvap) and not np.isnan(close) and cwvap > 0:
        cwvap_dist_pct = (close - cwvap) / cwvap * 100.0
        if cfg.cwvap_max_dist < 0.0 and cwvap_dist_pct < cfg.cwvap_max_dist:
            return False, 0, {
                "reason": f"CWVAP dist {cwvap_dist_pct:.1f}% < {cfg.cwvap_max_dist:.1f}%",
            }

    # Dead-cat bounce gate: price bouncing (psz_v high) but deep under CWVAP
    bc = cfg.bt_cross
    if bc.dead_cat_gate_enabled and not np.isnan(cwvap_dist_pct):
        psz_v = row.get("psz_v", np.nan)
        if (
            not np.isnan(psz_v)
            and psz_v >= bc.dead_cat_pszv_min
            and cwvap_dist_pct < bc.dead_cat_cwvap_max
        ):
            return False, 0, {
                "reason": f"Dead-cat bounce: psz_v={psz_v:.3f}, cwvap={cwvap_dist_pct:.1f}%",
            }

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.BT_CROSS,
        [f"bt={bt:.3f}", f"prev_cts={prev_cts:.3f}"],
    )
    return True, intensity_int, meta
