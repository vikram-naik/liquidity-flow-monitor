from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag

def entry_oversold_decel(
    row: dict,
    prev_row: dict,
    cfg,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Oversold Deceleration Path (ODP) entry check logic.
    
    Identifies high-expectancy bottom turnarounds by finding volatility-normalized
    overextensions (Distance-to-ATR Stretch) coupled with price rate-of-fall deceleration.
    """
    path_cfg = getattr(cfg, "oversold_decel", None)
    if path_cfg is None or not path_cfg.enabled:
        return False, 0, {"reason": "Oversold-Decel entry path is disabled"}

    if not prev_row or not records:
        return False, 0, {"reason": "Missing historical context records"}

    # Extract indicators
    das = row.get("das", np.nan)
    psz_v = row.get("psz_v", np.nan)
    psz_decel = row.get("psz_decel_3b", np.nan)
    rp_22 = row.get("range_pos_22", np.nan)
    cwc = row.get("cwc", np.nan)
    pdd_30 = row.get("pdd_30", np.nan)
    bt = row.get("base_tightness", np.nan)
    fas = row.get("fas", np.nan)
    regime = row.get("regime", "notrend")
    dv_shock = row.get("dv_shock", np.nan)
    rdv = row.get("rdv", np.nan)

    # Validate that required data is present
    if any(np.isnan(x) for x in [das, psz_v, psz_decel, rp_22, cwc, pdd_30, bt, fas]):
        return False, 0, {"reason": "Missing required indicators for Oversold-Decel"}

    # 1. Base ODP shape criteria
    # Volatility stretch or range position stretch + price velocity active + deceleration floor
    has_stretch = (das < path_cfg.das_thresh) or (rp_22 < 0.15)
    if not has_stretch:
        return False, 0, {"reason": f"No oversold stretch (DAS: {das:.2f} >= {path_cfg.das_thresh} and RP_22: {rp_22:.2f} >= 0.15)"}

    if psz_v <= 0.01:
        return False, 0, {"reason": f"Price velocity too low (psz_v: {psz_v:.3f} <= 0.01)"}

    if psz_decel <= path_cfg.decel_thresh:
        return False, 0, {"reason": f"Price deceleration too low (psz_decel_3b: {psz_decel:.3f} <= {path_cfg.decel_thresh})"}

    # 2. Safety and volume climax filters (if enabled)
    if (path_cfg.dv_shock_min > 0.0 or path_cfg.rdv_min > 0.0):
        if np.isnan(dv_shock) or np.isnan(rdv):
            return False, 0, {"reason": "Missing volume climax indicators"}
        if dv_shock < path_cfg.dv_shock_min or rdv < path_cfg.rdv_min:
            return False, 0, {"reason": f"Volume climax criteria not met (DV Shock: {dv_shock:.2f} < {path_cfg.dv_shock_min} or RDV: {rdv:.2f} < {path_cfg.rdv_min})"}

    # 3. Soft and hard CWC, FAS, BT, Regime Filters
    if path_cfg.filter_regime and regime == "downtrend":
        return False, 0, {"reason": "Regime is downtrend and regime filter is active"}

    if fas < path_cfg.fas_min:
        return False, 0, {"reason": f"FAS flow score too low (FAS: {fas:.3f} < {path_cfg.fas_min})"}

    if cwc < path_cfg.cwc_min:
        return False, 0, {"reason": f"CWC coherence score too low (CWC: {cwc:.3f} < {path_cfg.cwc_min})"}

    if bt > path_cfg.bt_max:
        return False, 0, {"reason": f"Base tightness too loose (BT: {bt:.3f} > {path_cfg.bt_max})"}

    # 4. Falling Knife / Gap Down & Structural bottom checks
    # Gap down check
    def check_gap_down(records, idx, lookback=10):
        start = max(1, idx - lookback)
        for i in range(start, idx + 1):
            prev_low = records[i-1].get("low", 0)
            curr_high = records[i].get("high", 0)
            atr = records[i].get("atr_20", 0)
            if prev_low > curr_high:
                gap_size = prev_low - curr_high
                if atr > 0 and gap_size > (0.3 * atr):
                    return True
        return False

    has_gap_down = check_gap_down(records, idx, 10)
    if has_gap_down:
        return False, 0, {"reason": "Recent gap down detected (active falling knife)"}

    if pdd_30 >= -2.50:
        return False, 0, {"reason": f"PDD_30 above bottom limit (PDD_30: {pdd_30:.2f} >= -2.50)"}

    # Passed all gates!
    details = {
        "reason": "Oversold Decel acceptance",
        "entry_tag": EntryTag.OVERSOLD_DECEL.value,
        "score": 80,
        "conv_score": 80,
        "das": das,
        "cwc": cwc,
        "bt": bt
    }
    return True, 80, details
