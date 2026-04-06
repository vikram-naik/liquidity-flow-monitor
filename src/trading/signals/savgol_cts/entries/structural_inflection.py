"""Path 4 -- Structural Inflection (Diamond) entry.

High-conviction mean-reversion targeting structural inflections with
strong institutional acceleration and price velocity. Identifies setups
using an 8-gate filtering process.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_structural_inflection(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check Path 4 entry conditions (8-gate process)."""
    dicfg = cfg.structural_inflection
    if not dicfg.enabled:
        return False, 0, {"reason": "Structural Inflection disabled"}

    if records is None or idx < max(dicfg.v_lookback, dicfg.s_lookback):
        return False, 0, {"reason": "Insufficient history"}

    # Basic data presence check
    psz_now = row.get("price_slope_z", np.nan)
    psz_1 = records[idx-1].get("price_slope_z", np.nan)
    
    cts_slope_now = row.get("cts_slope", np.nan)
    cts_slope_1 = records[idx-1].get("cts_slope", np.nan)
    
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    cwc_slope_now = row.get("cwc_slope", np.nan)
    cts_now = row.get("cts", np.nan)

    if any(np.isnan(v) for v in [psz_now, psz_1, cts_slope_now, cts_slope_1, close, cwvap, cwc_slope_now, cts_now]):
        return False, 0, {"reason": "Missing data for Structural Inflection gates"}

    # GATE 1: PSZ Zero-Cross
    if not (psz_now > 0 and psz_1 <= 0):
        return False, 0, {"reason": "No PSZ zero-cross"}

    # GATE 2: CTS Slope Zero-Cross
    if not (cts_slope_now > 0 and cts_slope_1 <= 0):
        return False, 0, {"reason": "No CTS slope zero-cross"}

    # GATE 3: Velocity Surge (PSZ velocity rising)
    psz_v_vals = [records[idx - j].get("psz_v", np.nan) for j in range(dicfg.v_lookback + 1)]
    if any(np.isnan(v) for v in psz_v_vals):
        return False, 0, {"reason": "Missing PSZ velocity data"}
    
    v_rising = all(psz_v_vals[j] > psz_v_vals[j+1] for j in range(dicfg.v_lookback))
    if not v_rising:
        return False, 0, {"reason": "PSZ velocity not strictly rising"}

    # GATE 4: Accumulation Surge (CTS slope rising)
    cts_slope_vals = [records[idx - j].get("cts_slope", np.nan) for j in range(dicfg.s_lookback + 1)]
    if any(np.isnan(s) for s in cts_slope_vals):
        return False, 0, {"reason": "Missing CTS slope data for lookback"}
        
    s_rising = all(cts_slope_vals[j] > cts_slope_vals[j+1] for j in range(dicfg.s_lookback))
    if not s_rising:
        return False, 0, {"reason": "CTS slope not strictly rising"}

    # GATE 5: CWC Alignment
    if cwc_slope_now <= 0:
        return False, 0, {"reason": "CWC slope not positive"}

    # GATE 6: Displacement
    cwvap_dist = (close - cwvap) / cwvap * 100.0 if cwvap > 0 else 0
    if cwvap_dist >= dicfg.cwvap_dist_max:
        return False, 0, {"reason": f"Price too far above CWVAP ({cwvap_dist:.2f}%)"}

    # GATE 7: Institutional Floor
    if cts_now <= dicfg.cts_floor:
        return False, 0, {"reason": f"CTS {cts_now:.2f} <= {dicfg.cts_floor}"}

    # GATE 8: Total Momentum Delta
    cts_slope_old = cts_slope_vals[dicfg.s_lookback]
    total_slope_delta = cts_slope_now - cts_slope_old
    if total_slope_delta <= dicfg.total_slope_delta_min:
        return False, 0, {"reason": f"Total slope delta {total_slope_delta:.4f} <= {dicfg.total_slope_delta_min}"}

    # Base conviction score calculation modeled from study script
    score = 10
    if psz_v_vals[0] > 0.1: score += 5
    if cts_now > 0: score += 5
    if cwc_slope_now > 0.05: score += 5

    # All gates passed
    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.STRUCTURAL_INFLECTION,
        [f"psz={psz_now:.2f}", f"cwvap_dist={cwvap_dist:.1f}%", f"cts={cts_now:.2f}", f"delta={total_slope_delta:.3f}"],
    )
    meta["conv_score"] = score
    meta["entry_tag"] = EntryTag.STRUCTURAL_INFLECTION.value
    meta["cts_slope_total_delta"] = total_slope_delta
    
    return True, intensity_int, meta
