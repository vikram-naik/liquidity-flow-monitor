"""Path 3 -- Institutional Floor entry.

High-conviction mean-reversion targeting the "Institutional Floor" of 
stocks. Identifies setups using a 9-gate filtering process combining 
price exhaustion (PSZ), institutional trend (CTS), and cohort alignment (CWC).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity


def check_institutional_floor(
    row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    records: list[dict] | None = None, idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check Path 3 entry conditions (9-gate process)."""
    ifcfg = cfg.institutional_floor
    if not ifcfg.enabled:
        return False, 0, {"reason": "Institutional Floor disabled"}

    # Basic data presence check
    psz = row.get("price_slope_z", np.nan)
    psz_prev = prev_row.get("price_slope_z", np.nan)
    rsz = row.get("rdv_slope_z", np.nan)
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    psz_v = row.get("psz_v", np.nan)
    cts = row.get("cts", np.nan)
    cts_bt = row.get("cts_buy_threshold", np.nan)
    cts_s = row.get("cts_slope", np.nan)
    cts_s_prev = prev_row.get("cts_slope", np.nan)
    cwc_s = row.get("cwc_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_prev = prev_row.get("cts_accel", np.nan)

    if any(np.isnan(v) for v in [psz, psz_prev, close, cwvap, psz_v, cts, cts_bt, cts_s, cts_s_prev, cwc_s, cts_accel_prev]):
        return False, 0, {"reason": "Missing data for Institutional Floor gates"}

    # GATE 1 & 2: PSZ Inflection from Sustained Exhaustion
    # 1. Previous N bars were ALL <= threshold
    conv_score = 0
    conv_parts = []

    if records is not None and idx >= ifcfg.psz_lookback:
        was_deep = all(
            records[idx - j].get("price_slope_z", 0) <= ifcfg.psz_threshold 
            for j in range(1, ifcfg.psz_lookback + 1)
        )
        if not was_deep:
            return False, 0, {"reason": f"PSZ not sustained <= {ifcfg.psz_threshold} for {ifcfg.psz_lookback} bars"}
    else:
        return False, 0, {"reason": "Insufficient history for PSZ lookback"}

    # 2. Signal bar crosses above (threshold + delta)
    is_inflection = psz >= (ifcfg.psz_threshold + ifcfg.psz_delta) and psz_prev <= ifcfg.psz_threshold
    if not is_inflection:
        return False, 0, {"reason": f"PSZ {psz:.2f} did not inflect from {psz_prev:.2f}"}

    # GATE 3: Price Displacement (Must be below CWVAP)
    cwvap_dist = (close - cwvap) / cwvap * 100.0
    if cwvap_dist > ifcfg.cwvap_dist_max:
        return False, 0, {"reason": f"Price too high ({cwvap_dist:.2f}%) above CWVAP threshold"}

    # GATE 4 & 5: Momentum Acceleration (PSZ Velocity)
    if records is not None and idx >= ifcfg.psz_v_lookback:
        psz_v_vals = [records[idx - j].get("psz_v", 0) for j in range(ifcfg.psz_v_lookback + 1)]
        # Velocity must be strictly increasing over lookback with minimum delta
        is_accelerating = all(
            (psz_v_vals[j] - psz_v_vals[j+1]) >= ifcfg.psz_v_delta 
            for j in range(ifcfg.psz_v_lookback)
        )
        if not is_accelerating:
            return False, 0, {"reason": "PSZ velocity not accelerating over lookback"}
    else:
        return False, 0, {"reason": "Insufficient history for PSZ velocity lookback"}

    # GATE 6: Institutional Dislocation (CTS <= buy threshold)
    if ifcfg.cts_buy_guard and cts > cts_bt:
        return False, 0, {"reason": f"CTS {cts:.2f} > BT {cts_bt:.2f}"}

    # GATE 7: Institutional Improvement (CTS slope accelerating)
    if ifcfg.cts_slope_accel_guard and cts_s <= cts_s_prev:
        return False, 0, {"reason": f"CTS slope not accelerating: {cts_s_prev:.4f} -> {cts_s:.4f}"}

    # GATE 7.5: Acceleration Rising Guard (avoid fading pops)
    if getattr(ifcfg, "accel_rising_guard", False) and cts_accel <= cts_accel_prev:
        return False, 0, {"reason": f"CTS accel not rising: {cts_accel_prev:.4f} -> {cts_accel:.4f}"}

    # GATE 8: Contrarian Guard (CTS slope must be negative)
    if ifcfg.cts_slope_neg_guard and cts_s >= 0:
        return False, 0, {"reason": f"CTS slope {cts_s:.4f} >= 0 (not contrarian)"}

    # GATE 9: Institutional Alignment (CWC slope must be positive)
    if ifcfg.cwc_slope_rising_guard and cwc_s <= 0:
        return False, 0, {"reason": f"CWC slope {cwc_s:.4f} <= 0 (no alignment)"}

    # GATE 10: Conviction score — multi-factor soft gate.
    if getattr(ifcfg, "conviction_enabled", False):
        # (a) CTS depth vs dynamic buy threshold (0/1/2)
        if not np.isnan(cts_bt):
            if cts <= cts_bt:
                conv_score += 2; conv_parts.append("cts_dep=2")
            elif cts <= cts_bt + 0.1:
                conv_score += 1; conv_parts.append("cts_dep=1")

        # (b) CTS accel vs dynamic threshold (0/2)
        cts_at = row.get("cts_accel_threshold", np.nan)
        if not np.isnan(cts_at) and cts_accel > cts_at:
            conv_score += 2; conv_parts.append("accel=2")

        # (c) CWVAP stretch (0/1/2)
        if cwvap_dist <= ifcfg.conv_cwvap_deep:
            conv_score += 2; conv_parts.append("cwvap=2")
        elif cwvap_dist <= ifcfg.conv_cwvap_mid:
            conv_score += 1; conv_parts.append("cwvap=1")

        # (d) PSZ momentum (0/1/2)
        psz_delta_val = psz - psz_prev if not np.isnan(psz_prev) else 0.0
        if psz_delta_val >= ifcfg.conv_psz_delta_strong:
            conv_score += 2; conv_parts.append("psz_m=2")
        elif psz_delta_val >= ifcfg.conv_psz_delta_mid:
            conv_score += 1; conv_parts.append("psz_m=1")

        # (e) Divergence spread strength (0/1/2)
        if not np.isnan(rsz) and not np.isnan(psz):
            spread = rsz - psz
            if spread >= ifcfg.conv_spread_strong:
                conv_score += 2; conv_parts.append("divg=2")
            elif spread >= ifcfg.conv_spread_mid:
                conv_score += 1; conv_parts.append("divg=1")

        # (f) PSZ rising 3-bar (0/1)
        if records is not None and idx >= 3:
            p0 = records[idx].get("price_slope_z", np.nan)
            p1 = records[idx - 1].get("price_slope_z", np.nan)
            p2 = records[idx - 2].get("price_slope_z", np.nan)
            p3 = records[idx - 3].get("price_slope_z", np.nan)
            if not any(np.isnan(v) for v in [p0, p1, p2, p3]) and p0 > p1 > p2 > p3:
                conv_score += 1; conv_parts.append("psz3=1")

        if conv_score < ifcfg.conviction_min_score:
            return False, 0, {
                "reason": f"Conviction too low ({conv_score}/{ifcfg.conviction_min_score}, {'+'.join(conv_parts) or 'none'})"
            }

    # All gates passed
    intensity_int, meta = compute_intensity(
        row, prev_row, EntryTag.INSTITUTIONAL_FLOOR,
        [f"psz={psz:.2f}", f"cwvap_dist={cwvap_dist:.1f}%", f"cts={cts:.2f}"],
    )
    meta["conv_score"] = conv_score
    meta["score"] = float(intensity_int)
    meta["conv_parts"] = "+".join(conv_parts)
    return True, intensity_int, meta
