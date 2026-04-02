"""Path 2 -- Structural Divergence entry.

Captures volume exhaustion and delivery divergence during sharp price drops.
Requires a flattening of the downward cycle and prevents falling knife entries 
when institutional distribution is heavily unified (CWC guard).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_structural_divergence(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
) -> tuple[bool, int, dict]:
    """Check Path 2 entry conditions."""
    sdcfg = cfg.structural_divergence
    if not sdcfg.enabled:
        return False, 0, {"reason": "Structural divergence disabled"}

    # Get data
    psz = row.get("price_slope_z", np.nan)
    rsz = row.get("rdv_slope_z", np.nan)
    cts = row.get("cts", np.nan)
    cts_slope = row.get("cts_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    accum_div = row.get("accum_div", np.nan)
    cwc = row.get("cwc", np.nan)
    cwvap = row.get("cwvap", np.nan)
    close = row.get("close", np.nan)

    if any(np.isnan(v) for v in [psz, rsz, cts, cts_slope, cts_accel, accum_div, cwc, cwvap, close]):
        missing = [k for k, v in {"psz": psz, "rsz": rsz, "cts": cts, "cts_slope": cts_slope, "cts_accel": cts_accel, "accum": accum_div, "cwc": cwc, "cwvap": cwvap, "close": close}.items() if np.isnan(v)]
        return False, 0, {"reason": f"Missing data: {missing}"}

    # Condition 1: Exhaustion
    if psz > sdcfg.psz_max or cts > sdcfg.cts_max:
        return False, 0, {"reason": f"Not exhausted (psz={psz:.2f}, cts={cts:.2f})"}

    # Condition 2: Divergence
    spread = rsz - psz
    if spread < sdcfg.spread_min and accum_div <= sdcfg.accum_div_min:
        return False, 0, {"reason": f"No divergence (spread={spread:.2f}, accum={accum_div:.4f})"}

    # Condition 3: Inflection
    if cts_slope >= 0 or cts_accel <= sdcfg.accel_min:
        return False, 0, {"reason": f"Not inflecting (slope={cts_slope:.4f}, accel={cts_accel:.4f})"}

    # Condition 4: Anti-capitulation
    if cwc > sdcfg.cwc_max:
        return False, 0, {"reason": f"Unified distribution (cwc={cwc:.2f})"}

    # Condition 5: CWVAP Context
    cwvap_dist = (close - cwvap) / cwvap * 100.0
    if cwvap_dist > sdcfg.cwvap_dist_max:
        return False, 0, {"reason": f"Price too high above CWVAP ({cwvap_dist:.2f}%)"}

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.STRUCTURAL_DIVERGENCE,
        [f"spread={spread:.2f}", f"accum={accum_div:.4f}", f"cwc={cwc:.2f}"],
    )
    return True, intensity_int, meta
