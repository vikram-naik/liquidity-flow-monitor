import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

def main():
    sym = "ITC"
    engine = DivergenceEngine(sym)
    df = engine.run().ledger
    
    df['date_dt'] = pd.to_datetime(df['date'])
    idx = df[df['date_dt'] == '2025-11-17'].index[0]
    
    records = df.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]
    
    print("--- ITC Telemetry on 2025-11-17 ---")
    print(f"Date: {row['date']}")
    print(f"Close: {row.get('close')}, CWVAP: {row.get('cwvap')}")
    print(f"prev_cts_slope: {prev_row.get('cts_slope')}, cts_slope: {row.get('cts_slope')}")
    print(f"prev_fas: {prev_row.get('fas')}, fas: {row.get('fas')}, fas_buy_threshold: {row.get('fas_buy_threshold')}")
    print(f"prev_prt_slope: {prev_row.get('prt_slope')}, prt_slope: {row.get('prt_slope')}")
    print(f"cts: {row.get('cts')}, cts_buy_threshold: {row.get('cts_buy_threshold')}")
    print(f"psz_v: {row.get('psz_v')}, prev_psz_v: {prev_row.get('psz_v')}")
    print(f"cts_accel: {row.get('cts_accel')}, cts_accel_threshold: {row.get('cts_accel_threshold')}")
    
    # Evaluate triggers
    prev_cs = prev_row.get("cts_slope", 0)
    cs = row.get("cts_slope", 0)
    trigger_cs = 1 if (prev_cs <= 0 and cs > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    prt = row.get("prt_slope", 0)
    prev_prt = prev_row.get("prt_slope", 0)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0
    
    print(f"trigger_cs: {trigger_cs}, trigger_fas: {trigger_fas}, trigger_prt: {trigger_prt}")
    
    # Evaluated Gates
    strong_institutional_turn = (fas > fas_bt and row.get('psz_v', 0) > 0.02)
    print(f"strong_institutional_turn: {strong_institutional_turn}")
    
    accel = row.get("cts_accel", 0)
    accel_bt = row.get("cts_accel_threshold", 0.0)
    print(f"cts_accel: {accel} > threshold {accel_bt}? {accel > accel_bt}")
    
    a2 = prev_row.get("cts_accel", 0)
    a3 = row.get("cts_accel", 0)
    print(f"accel rising: a3 ({a3}) >= a2 ({a2})? {a3 >= a2}")
    
    psz_v = row.get("psz_v", 0)
    print(f"psz_v: {psz_v} > 0? {psz_v > 0}")
    
    v2 = prev_row.get("psz_v", 0)
    v3 = row.get("psz_v", 0)
    print(f"psz_v rising: v3 ({v3}) >= v2 ({v2})? {v3 >= v2}")
    
    f1 = prev_row.get("fas", 0)
    print(f"fas rising or valid: fas ({fas}) >= f1 ({f1})? {fas >= f1}")
    
    range_pos_10 = row.get("range_pos_10", 0)
    print(f"range_pos_10: {range_pos_10} <= 0.5? {range_pos_10 <= 0.5}")
    
    range_pos_22 = row.get("range_pos_22", 0)
    range_pos_63 = row.get("range_pos_63", 0)
    range_pos_252 = row.get("range_pos_252", 0)
    cwc = row.get("cwc", 0)
    print(f"range_pos_22: {range_pos_22}, range_pos_63: {range_pos_63}, range_pos_252: {range_pos_252}, cwc: {cwc}")
    
if __name__ == "__main__":
    main()
