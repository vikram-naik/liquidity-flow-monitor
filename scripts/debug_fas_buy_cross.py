import argparse
import sys
import os
import pandas as pd
import numpy as np

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import FasBuyCrossEntryConfig
from src.trading.signals.savgol_cts.entries.fas_buy_cross import check_fas_buy_cross
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive

def main():
    parser = argparse.ArgumentParser(description="Debug FAS Buy Cross Scoring")
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--date", type=str, required=True)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    target_date = args.date

    print(f"Loading DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    
    df = result.ledger
    df['date'] = pd.to_datetime(df['date'])
    target_dt = pd.to_datetime(target_date)
    
    matches = df[df['date'] == target_dt]
    if matches.empty:
        print(f"Error: Date {target_date} not found for {symbol}.")
        return

    idx = matches.index[0]
    records = df.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]

    cfg = FasBuyCrossEntryConfig()
    
    print(f"\nEvaluating FAS Buy Cross Entry for {symbol} on {target_date}...")
    
    # -----------------------------------------------------------------------
    # Multi-Factor Scoring (STRENGTH & WEAKNESS) - Replicated for Debug
    # -----------------------------------------------------------------------
    lookback_size = 10
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    prev2_cts_accel = records[idx-2].get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    cwc_slope = row.get("cwc_slope", np.nan)
    psz_v = row.get("psz_v", np.nan)
    prev_psz_v = prev_row.get("psz_v", np.nan)
    prev2_psz_v = records[idx-2].get("psz_v", np.nan)

    cts_accel_lookback = np.array([records[idx - n].get("cts_accel", np.nan) for n in range(lookback_size, 0, -1)])
    flat_check = is_flattish_line_adaptive(cts_accel, prev_cts_accel, prev2_cts_accel, cts_accel_lookback, sensitivity=0.05)
    
    psz_v_lookback = np.array([records[idx - n].get("psz_v", np.nan) for n in range(lookback_size, 0, -1)])
    flat_check_psz_v = is_flattish_line_adaptive(psz_v, prev_psz_v, prev2_psz_v, psz_v_lookback, sensitivity=0.15)

    base_score = 15.0
    print(f"Initial Base Score: {base_score}")

    # 1. Accel
    accel_delta = cts_accel - prev_cts_accel
    dynamic_tol_accel = flat_check.get("dynamic_tolerance_used", 0.005)
    if cts_accel > prev_cts_accel:
        if accel_delta > dynamic_tol_accel:
            base_score += 10.0
            print(f"Accel: Strong Thrust (+10) | delta={accel_delta:.4f}, tol={dynamic_tol_accel:.4f}")
        else:
            base_score -= 5.0
            print(f"Accel: Anemic Rise (-5) | delta={accel_delta:.4f}, tol={dynamic_tol_accel:.4f}")
    else:
        base_score -= 10.0
        print(f"Accel: Falling (-10) | delta={accel_delta:.4f}")

    # 2. CWC
    if not np.isnan(cwc_slope):
        if cwc_slope > 0.05:
            base_score += 10.0
            print(f"CWC: Strong Alignment (+10) | slope={cwc_slope:.4f}")
        elif cwc_slope < 0.01:
            base_score -= 5.0
            print(f"CWC: Anemic (-5) | slope={cwc_slope:.4f}")

    # 3. Accel Threshold
    delta_at = cts_accel - cts_accel_threshold
    if delta_at >= 0:
        base_score += 5.0
        print(f"Accel Threshold: Above (+5) | delta={delta_at:.4f}")
    else:
        if delta_at < -0.05:
            base_score -= 21.0
            print(f"Accel Threshold: Deep Below (-21) | delta={delta_at:.4f}")
        elif delta_at < -0.01:
            base_score -= 11.0
            print(f"Accel Threshold: Clear Below (-11) | delta={delta_at:.4f}")
        else:
            base_score -= 5.0
            print(f"Accel Threshold: Borderline Below (-5) | delta={delta_at:.4f}")

    # 4. PSZ_V
    psz_v_delta = psz_v - prev_psz_v
    dynamic_tol_psz = flat_check_psz_v.get("dynamic_tolerance_used", 0.01)
    if psz_v > prev_psz_v:
        if psz_v_delta > dynamic_tol_psz:
            base_score += 5.0
            print(f"PSZ_V: meaningful turn (+5) | delta={psz_v_delta:.4f}, tol={dynamic_tol_psz:.4f}")
        else:
            base_score -= 5.0
            print(f"PSZ_V: anemic turn (-5) | delta={psz_v_delta:.4f}, tol={dynamic_tol_psz:.4f}")
    else:
        print(f"PSZ_V: Falling (0) | delta={psz_v_delta:.4f}")

    print(f"Final Debug Score: {base_score}")
    
    passed, intensity, meta = check_fas_buy_cross(
        row=row,
        prev_row=prev_row,
        cfg=cfg,
        records=records,
        idx=idx
    )

    print("-" * 60)
    print(f"PASSED: {passed}")
    print(f"SCORE:  {intensity}")
    if not passed:
        print(f"REASON: {meta.get('reason')}")
    else:
        print(f"META:   {meta}")

if __name__ == "__main__":
    main()
