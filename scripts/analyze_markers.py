#!/usr/bin/env python3
import sys
import os
import pandas as pd
from tabulate import tabulate

# Add the project root to the python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.analysis.data import get_stock_data

def analyze_stock(symbol, start_date, end_date=None):
    print(f"Fetching data for {symbol}...")
    df, _, _ = get_stock_data(symbol, lookback_days=300)
    
    if df.empty:
        print(f"No data found for {symbol}")
        return
        
    df = df.set_index('record_date')
    
    # Apply date filters
    if end_date:
        mask = (df.index >= start_date) & (df.index <= end_date)
    else:
        mask = (df.index >= start_date)
    
    sub_df = df[mask]
    
    if sub_df.empty:
        print(f"No data found for {symbol} in the specified date range.")
        return
        
    print(f"\n{'='*80}")
    print(f"--- GENERAL DATA: {symbol} ---")
    print(f"{'='*80}")
    cols = ['price_open', 'price_high', 'price_low', 'price_close', 'volume_total', 'delivery_qty', 'deliv_sma_10', 'davwap', 'atr_50']
    
    # Using tabulate for general data
    gen_data = sub_df[cols].reset_index()
    gen_data['record_date'] = gen_data['record_date'].dt.strftime('%Y-%m-%d')
    print(tabulate(gen_data, headers='keys', tablefmt='psql', showindex=False))

    print(f"\n{'='*80}")
    print(f"--- COIL ANALYSIS ---")
    print(f"{'='*80}")
    
    coil_table = []
    for date, row in sub_df.iterrows():
        range_total = row['price_high'] - row['price_low']
        body_size = abs(row['price_open'] - row['price_close'])
        dist_atr = abs(row['price_close'] - row['davwap']) / row['atr_50']
        dvl_slope = df.loc[date]['dvl_slope_5']
        
        req_rng = 0.8 * row['atr_50']
        req_bdy = 0.4 * row['atr_50']
        
        coil_range_pass = range_total < req_rng
        coil_body_pass = body_size < req_bdy
        coil_prox_pass = dist_atr <= 1.0
        
        score = row.get('coil_score', 0)
        is_coil = row.get('is_coil', False)
        
        coil_table.append([
            date.strftime('%Y-%m-%d'),
            f"{range_total:.1f} (< {req_rng:.1f}: {coil_range_pass})",
            f"{body_size:.1f} (< {req_bdy:.1f}: {coil_body_pass})",
            f"{dist_atr:.1f} (<= 1.0: {coil_prox_pass})",
            f"{dvl_slope:.1E}",
            score,
            is_coil
        ])
        
    headers = ["Date", "Range", "Body", "ProxATR", "DSlope", "Score", "COIL"]
    print(tabulate(coil_table, headers=headers, tablefmt='psql'))


    print(f"\n{'='*80}")
    print(f"--- IGNITION ANALYSIS ---")
    print(f"{'='*80}")
    
    ign_table = []
    for date, row in sub_df.iterrows():
        prev_close = df.loc[:date]['price_close'].shift(1).get(date, row['price_open'])
        
        is_up = (row['price_close'] > row['price_open'])
        origin = min(row['price_open'], prev_close)
        ignition_expansion = row['price_close'] - origin
        dist_origin = abs(origin - row['davwap']) / row['atr_50']
        
        req_exp = 0.8 * row['atr_50']
        ign_up_pass = is_up
        ign_exp_pass = ignition_expansion >= req_exp
        ign_prox_pass = dist_origin <= 1.0
        
        score = row.get('ignition_score', 0)
        is_ign = row.get('is_ignition', False)
        grind = row.get('grind_level', 0)
        mcs = row.get('mcs', 0)
        
        ign_table.append([
            date.strftime('%Y-%m-%d'),
            ign_up_pass,
            f"{ignition_expansion:.1f} (>= {req_exp:.1f}: {ign_exp_pass})",
            f"{dist_origin:.1f} (<= 1.0: {ign_prox_pass})",
            score,
            is_ign,
            grind,
            f"{mcs:.2f}" if pd.notna(mcs) else "---"
        ])
        
    headers = ["Date", "Up?", "Expansion", "ProxOrigATR", "Score", "IGN", "Grind", "MCS"]
    print(tabulate(ign_table, headers=headers, tablefmt='psql'))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 analyze_markers.py <SYMBOL> <START_DATE_YYYY-MM-DD> [END_DATE_YYYY-MM-DD]")
        sys.exit(1)
        
    symbol = sys.argv[1].upper()
    start_date = sys.argv[2]
    end_date = sys.argv[3] if len(sys.argv) > 3 else None
    
    analyze_stock(symbol, start_date, end_date)
