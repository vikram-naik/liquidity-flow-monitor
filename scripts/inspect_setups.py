#!/usr/bin/env python3
import sys
import os
import pandas as pd
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def inspect_ticker_date(symbol, date_str):
    engine = DivergenceEngine(symbol)
    result = engine.run()
    df = result.ledger
    df['date'] = pd.to_datetime(df['date'])
    df_lookup = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
    if df_lookup.empty:
        print(f"No data found for {symbol} on {date_str}")
        return None
        
    idx = df[df['date'].dt.strftime('%Y-%m-%d') == date_str].index[0]
    records = df.to_dict('records')
    row = records[idx]
    
    # Calculate Spearman price trend over 10 bars on this index
    tps_10 = [(r['high'] + r['low'] + r['close']) / 3.0 for r in records[max(0, idx-9) : idx+1]]
    spearman_10 = evaluate_spearman_trend(tps_10) if len(tps_10) >= 5 else 0.0
    
    tps_5 = [(r['high'] + r['low'] + r['close']) / 3.0 for r in records[max(0, idx-4) : idx+1]]
    spearman_5 = evaluate_spearman_trend(tps_5) if len(tps_5) >= 5 else 0.0
    
    close = row.get("close", 0.0)
    atr = row.get("atr_20", 0.0)
    bt = row.get("base_tightness", 1.0)
    rw10 = row.get("range_width_10", 0.0)
    rw10_abs = (rw10 * close) / 100.0
    rw_atrs = rw10_abs / atr if atr > 0 else 10.0
    
    is_tight = bt < 0.35
    is_narrow = rw_atrs < 1.5
    is_flat = abs(spearman_5) < 0.6
    
    print(f"\n--- BASING DIAGNOSTIC FOR {symbol} on {date_str} ---")
    print(f"Close: {close:.2f}")
    print(f"ATR: {atr:.2f}")
    print(f"Base Tightness: {bt:.4f} (is_tight [bt < 0.35]: {is_tight})")
    print(f"Range Width 10: {rw10:.4f}%")
    print(f"Range Width in ATRs: {rw_atrs:.4f} (is_narrow [rw_atrs < 1.5]: {is_narrow})")
    print(f"Spearman 5 (TP): {spearman_5:.4f} (is_flat [|spearman| < 0.6]: {is_flat})")
    print(f"Basing score: {int(is_tight) + int(is_narrow) + int(is_flat)} / 3 (Rejected if >= 2)")
    
    row['price_spearman_10'] = spearman_10
    row['price_spearman_5'] = spearman_5
    return row

def main():
    print("INSPECTING BAJAJFINSV SIGNAL ON 2022-04-21 (Entry: 2022-04-22)...")
    row_b = inspect_ticker_date("BAJAJFINSV", "2022-04-21")
    
    print("\nINSPECTING INFY SIGNAL ON 2025-03-07 (Entry: 2025-03-10)...")
    row_i = inspect_ticker_date("INFY", "2025-03-07")
    
    features = [
        'close', 'cts', 'cts_slope', 'cts_accel', 'cwc', 'cwc_slope', 'fas', 
        'psz_v', 'price_slope_z', 'base_tightness', 'range_width_10', 'rdv', 
        'pdd_30', 'pdd_120', 'coherence', 'price_spearman_10', 'price_spearman_5'
    ]
    
    comparison = []
    for f in features:
        val_b = row_b.get(f, 0.0) if row_b else 0.0
        val_i = row_i.get(f, 0.0) if row_i else 0.0
        comparison.append([f, val_b, val_i])
        
    print("\n" + "="*50)
    print("         COMPARATIVE FEATURE SIGNATURES")
    print("="*50)
    print(tabulate(comparison, headers=["Feature", "BAJAJFINSV (Falling Knife)", "INFY (Basing Whipsaw)"], tablefmt="grid"))
    print("="*50)

if __name__ == "__main__":
    main()
