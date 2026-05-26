import numpy as np
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.entries.utils import check_basing, check_gap_down



def do_path_1(row: dict, prev_row: dict, cfg, records: list[dict], idx: int) -> tuple[bool, int, dict]:
    """Example entry path function that checks for a recent gap down and a strong CTS signal."""

    # 1. cwc_slope is negative check
    cwc_slope = row.get("cwc_slope", np.nan)
    if not(cwc_slope < 0):
        return False, 0, {"reason": "cwc_slope does not turn negative"}
    
    # 2. rsz_v turning negative check
    rsz_v = row.get("rsz_v", np.nan)
    rsz_v_prev = prev_row.get("rsz_v", np.nan) if prev_row else np.nan
    if np.isnan(rsz_v) or np.isnan(rsz_v_prev):
        return False, 0, {"reason": "Missing rsz_v data"}
    if not (rsz_v_prev >= 0 and rsz_v < 0):
        return False, 0, {"reason": "rsz_v does not turn negative"}
    
    # 3. psz_v turning negative check and is less than -0.001 check
    psz_v = row.get("psz_v", np.nan)
    psz_v_prev = prev_row.get("psz_v", np.nan) if prev_row else np.nan
    if np.isnan(psz_v) or np.isnan(psz_v_prev):
        return False, 0, {"reason": "Missing psz_v data"}
    if not (psz_v_prev >= 0 and psz_v < 0):
        return False, 0, {"reason": "psz_v does not turn negative"} 
    if not (psz_v < -0.001):
        return False, 0, {"reason": "psz_v is not less than -0.001"}
    
    # 4. prt_slope turning negative check
    prt_slope = row.get("prt_slope", np.nan)
    prt_slope_prev = prev_row.get("prt_slope", np.nan) if   prev_row else np.nan    
    if np.isnan(prt_slope) or np.isnan(prt_slope_prev):
        return False, 0, {"reason": "Missing prt_slope data"}
    if not (prt_slope_prev >= 0 and prt_slope < 0):
        return False, 0, {"reason": "prt_slope does not turn negative"}
    
    # 5. cts < 0.2 check    
    cts = row.get("cts", np.nan)
    if np.isnan(cts):
        return False, 0, {"reason": "Missing cts data"} 
    if not (cts < 0.2):
        return False, 0, {"reason": "cts not less than 0.2"}

    # 6. range_pos_252 > 0.05 atleast - Lame check !!
    range_pos_252 = row.get("range_pos_252", np.nan)
    if np.isnan(range_pos_252):
        return False, 0, {"reason": "Missing range_pos_252 data"}
    if not (range_pos_252 > 0.05):
        return False, 0, {"reason": "range_pos_252 not greater than 0.05"}
    
    # 7. range_pos_22 > 0.05 check
    range_pos_22 = row.get("range_pos_22", np.nan)
    if np.isnan(range_pos_22):  
        return False, 0, {"reason": "Missing range_pos_22 data"}
    if not (range_pos_22 > 0.04):   
        return False, 0, {"reason": "range_pos_22 not greater than 0.05"}
    
    return True, 80, {"reason": "All conditions met for Flow Momentum entry path"}

def do_path_2(row: dict, prev_row: dict, cfg, records: list[dict], idx: int) -> tuple[bool, int, dict]:
    """Example entry path function that checks for a recent gap down and a strong CTS signal."""
    # 1. cts crosing cts_buy_threshold check
    cts = row.get("cts", np.nan)
    cts_prev = prev_row.get("cts", np.nan) if prev_row else np.nan
    cts_buy_threshold = row.get("cts_buy_threshold", np.nan)
    if np.isnan(cts) or np.isnan(cts_prev) or np.isnan(cts_buy_threshold):
        return False, 0, {"reason": "Missing data for cts crossing check"}
    if not (cts_prev <= cts_buy_threshold and cts > cts_buy_threshold):
        return False, 0, {"reason": f"cts does not cross above buy threshold of {cts_buy_threshold}"}

    # 2. price_slope_z is rising for atleast 3 bars check
    price_slope_z = row.get("price_slope_z", np.nan)
    if np.isnan(price_slope_z):
        return False, 0, {"reason": "Missing price_slope_z data"}
    if idx < 3:
        return False, 0, {"reason": "Not enough data points for price_slope_z check"}   
    
    price_slope_z_1 = records[idx-1].get("price_slope_z", np.nan)
    price_slope_z_2 = records[idx-2].get("price_slope_z", np.nan)
    price_slope_z_3 = records[idx-3].get("price_slope_z", np.nan)
    if np.isnan(price_slope_z_1) or np.isnan(price_slope_z_2) or np.isnan(price_slope_z_3):
        return False, 0, {"reason": "Missing price_slope_z data for previous bars"}
    if not (price_slope_z > price_slope_z_1 > price_slope_z_2 and price_slope_z < 0):
        return False, 0, {"reason": "price_slope_z is not rising for at least 3 bars"}  

    # 3. cts_accel > cts_accel_threshold check
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    if np.isnan(cts_accel) or np.isnan(cts_accel_threshold):
        return False, 0, {"reason": "Missing data for cts_accel check"}
    if not (cts_accel > cts_accel_threshold):
        return False, 0, {"reason": f"cts_accel not greater than threshold of {cts_accel_threshold}"}
    
    # 4. cts_accel is rising for atleast 3 bars check
    if idx < 3:
        return False, 0, {"reason": "Not enough data points for cts_accel check"}
    cts_accel_1 = records[idx-1].get("cts_accel", np.nan)
    # cts_accel_2 = records[idx-2].get("cts_accel", np.nan)
    # cts_accel_3 = records[idx-3].get("cts_accel", np.nan)
    # if np.isnan(cts_accel_1) or np.isnan(cts_accel_2) or np.isnan(cts_accel_3):
    #     return False, 0, {"reason": "Missing cts_accel data for previous bars"}
    if not (cts_accel > cts_accel_1): # > cts_accel_2 > cts_accel_3 ):
        return False, 0, {"reason": "cts_accel is not rising for at least 3 bars"}  

    # 5. cwc > 0.4 check
    cwc = row.get("cwc", np.nan)
    if np.isnan(cwc):   
        return False, 0, {"reason": "Missing cwc data"}
    if not (cwc > 0.4):
        return False, 0, {"reason": "cwc not greater than 0.4"}
    
    # 6. psz_v is rising for atleast one bar check & is above required minimum
    psz_v = row.get("psz_v", np.nan)
    psz_v_prev = prev_row.get("psz_v", np.nan) if prev_row else np.nan
    if np.isnan(psz_v) or np.isnan(psz_v_prev):
        return False, 0, {"reason": "Missing psz_v data"}
    if not (psz_v > psz_v_prev):
        return False, 0, {"reason": "psz_v is not rising compared to previous bar"}
    psz_v_min = getattr(cfg, "psz_v_min", 0.04)
    if psz_v < psz_v_min:
        return False, 0, {"reason": f"psz_v ({psz_v:.4f}) is below required minimum threshold of {psz_v_min}"}
    
    # 7. check basing in last 5 bars
    if check_basing(records, idx):
        return False, 0, {"reason": "Price is basing in the last 5 bars"}   
    
    # 8. mcs_composite is above required minimum
    mcs_composite = row.get("mcs_composite", np.nan)
    mcs_min = getattr(cfg, "mcs_composite_min", 0.05)
    if np.isnan(mcs_composite) or mcs_composite < mcs_min:
        return False, 0, {"reason": f"mcs_composite ({mcs_composite:.4f}) is below required minimum threshold of {mcs_min}"}
    
    # 9. check for gap down in last 10 bars
    if check_gap_down(records, idx, lookback=10):
        return False, 0, {"reason": "Significant gap down detected in the last 10 bars"}    
    
    # 10. range_pos_63 > 0.25 check
    range_pos_63 = row.get("range_pos_63", np.nan)
    if np.isnan(range_pos_63):
        return False, 0, {"reason": "Missing range_pos_63 data"}
    if not (range_pos_63 > 0.25):
        return False, 0, {"reason": "range_pos_63 not greater than 0.25"}

    # 11. Bullish regime check
    if getattr(cfg, "only_bullish_regime", True):
        regime = row.get("regime", "")
        if "uptrend" not in regime.lower():
            return False, 0, {"reason": f"Market regime is not bullish (uptrend): {regime}"}

    return True, 81, {"reason": "All conditions met for Flow Momentum entry path"}



def entry_flow_momentum(row: dict, prev_row: dict, cfg, records: list[dict], idx: int) -> tuple[bool, int, dict]:
    """
    Path: Flow Momentum (FLOW_MOMENTUM)
    Designed using optimal parameter configurations discovered in watchlist PnL study.
    Fires on high-conviction momentum breakout setups when institutional flow actively accelerates
    and price velocity is extremely strong.
    """
    # 1. Path Enable check
    # Check if flow_momentum config exists and is enabled
    path_cfg = getattr(cfg, "flow_momentum", None)
    if path_cfg is None or not path_cfg.enabled:
        return False, 0, {"reason": "Flow Momentum entry path is disabled"}

    is_path_1_valid, score, details_1 = do_path_1(row, prev_row, path_cfg, records, idx)
    if not is_path_1_valid:
        is_path_2_valid, score, details_2 = do_path_2(row, prev_row, path_cfg, records, idx)
        if not is_path_2_valid:
            return False, 0, {"reason": details_1["reason"] + " AND " + details_2["reason"]}
        # return False, 0, details_1
    
    details = {
        "reason": "Flow Momentum Breakout accepted",
        "entry_tag": EntryTag.FLOW_MOMENTUM.value,
        "score": score,
        "raw_ml_score": 0,
        "ml_guard_threshold": 0
    }
    return True, score, details
