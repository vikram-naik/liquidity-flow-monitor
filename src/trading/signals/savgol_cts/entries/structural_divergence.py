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
    records: list[dict] | None = None, idx: int = 0,
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

    # Condition 6: PSZ rising guard — require PSZ bending up (_| pattern).
    # Best setups have price slope still deeply negative but now rising
    # bar-over-bar (moving towards zero). Reject when PSZ is flat or falling.
    prev_psz = prev_row.get("price_slope_z", np.nan)
    if sdcfg.psz_rising_guard and not np.isnan(prev_psz):
        psz_delta = psz - prev_psz
        if psz_delta < sdcfg.psz_delta_min:
            return False, 0, {"reason": f"PSZ not rising (delta={psz_delta:.4f})"}

    # Condition 7: CWC slope rising — reject when institutional coherence is
    # intensifying (positive CWC with rising slope = unified selling building).
    cwc_slope = row.get("cwc_slope", np.nan)
    if (sdcfg.cwc_slope_guard and cwc >= 0
            and not np.isnan(cwc_slope) and cwc_slope >= sdcfg.cwc_slope_max):
        return False, 0, {"reason": f"CWC coherence intensifying (cwc={cwc:.2f}, cwc_slope={cwc_slope:.4f})"}

    # Condition 8: PSZ velocity (psz_v) rising over last 3 bars.
    # Confirms sustained acceleration of recovery, not a one-bar blip.
    # Requires psz_v(t-1) > psz_v(t-2) > psz_v(t-3) where t is signal day.
    if sdcfg.psz_v_rising_guard and records is not None and idx >= 3:
        pv0 = records[idx - 1].get("psz_v", np.nan)
        pv1 = records[idx - 2].get("psz_v", np.nan)
        pv2 = records[idx - 3].get("psz_v", np.nan)
        if not any(np.isnan(v) for v in [pv0, pv1, pv2]):
            if not (pv0 > pv1 > pv2):
                return False, 0, {
                    "reason": f"psz_v not rising 3-bar (t-1={pv0:.4f}, t-2={pv1:.4f}, t-3={pv2:.4f})"
                }

    # Condition 9: Conviction score — multi-factor soft gate.
    # No single feature separates winners from losers, but the combination does.
    if sdcfg.conviction_enabled:
        conv_score = 0
        conv_parts = []

        # (a) CTS depth vs dynamic buy threshold (0/1/2)
        cts_bt = row.get("cts_buy_threshold", np.nan)
        if not np.isnan(cts_bt):
            if cts <= cts_bt:
                conv_score += 2; conv_parts.append("cts_dep=2")
            elif cts <= cts_bt + 0.1:
                conv_score += 1; conv_parts.append("cts_dep=1")

        # (b) CTS accel vs dynamic threshold (0/2)
        cts_at = row.get("cts_accel_threshold", np.nan)
        if not np.isnan(cts_at) and cts_accel > cts_at:
            conv_score += 2; conv_parts.append("accel=2")

        # (c) CWVAP stretch — deeper below = more rubber-band tension (0/1/2)
        if cwvap_dist <= sdcfg.conv_cwvap_deep:
            conv_score += 2; conv_parts.append("cwvap=2")
        elif cwvap_dist <= sdcfg.conv_cwvap_mid:
            conv_score += 1; conv_parts.append("cwvap=1")

        # (d) PSZ momentum — strength of the _| bend (0/1/2)
        psz_delta = psz - prev_psz if not np.isnan(prev_psz) else 0.0
        if psz_delta >= sdcfg.conv_psz_delta_strong:
            conv_score += 2; conv_parts.append("psz_m=2")
        elif psz_delta >= sdcfg.conv_psz_delta_mid:
            conv_score += 1; conv_parts.append("psz_m=1")

        # (e) Divergence spread strength (0/1/2)
        if spread >= sdcfg.conv_spread_strong:
            conv_score += 2; conv_parts.append("divg=2")
        elif spread >= sdcfg.conv_spread_mid:
            conv_score += 1; conv_parts.append("divg=1")

        # (f) PSZ rising 3-bar — sustained recovery confirmation (0/1)
        if records is not None and idx >= 3:
            p0 = records[idx].get("price_slope_z", np.nan)
            p1 = records[idx - 1].get("price_slope_z", np.nan)
            p2 = records[idx - 2].get("price_slope_z", np.nan)
            p3 = records[idx - 3].get("price_slope_z", np.nan)
            if not any(np.isnan(v) for v in [p0, p1, p2, p3]) and p0 > p1 > p2 > p3:
                conv_score += 1; conv_parts.append("psz3=1")

        if conv_score < sdcfg.conviction_min_score:
            return False, 0, {
                "reason": f"Conviction too low ({conv_score}/{sdcfg.conviction_min_score}, {'+'.join(conv_parts) or 'none'})"
            }

    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.STRUCTURAL_DIVERGENCE,
        [f"spread={spread:.2f}", f"accum={accum_div:.4f}", f"cwc={cwc:.2f}"],
    )
    return True, intensity_int, meta


