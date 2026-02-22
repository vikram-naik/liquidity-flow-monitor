import sys
import os
import pandas as pd
import numpy as np

# Add the project root to sys.path
sys.path.append('/home/vn/python-projects/liquidity-flow-monitor')

from src.analysis.data import get_stock_data

def investigate_distribution():
    symbol = "NESTLEIND"
    agg_period = "daily"
    df, anchor, vol_prof = get_stock_data(symbol, agg_period, lookback_days=120)
    
    if df.empty:
        return

    # Calculate Churn: Volume / Price Range
    # High Churn = High Volume, Small Range
    df['range'] = (df['price_high'] - df['price_low']) / df['price_close']
    df['churn'] = df['delivery_qty'] / (df['range'] + 1e-9)
    
    # Delivery Percentage
    df['deliv_pct'] = (df['delivery_qty'] / df['volume_total']) * 100
    
    # Recent 10 days
    recent = df.tail(10)
    
    print(f"--- Distribution Check: {symbol} ---")
    print(recent[['display_date_iso', 'price_close', 'mfm', 'delivery_qty', 'deliv_pct', 'churn', 'dvl']])
    
    print("\n--- Summary Statistics (Recent 10) ---")
    print(f"Avg Delivery %: {recent['deliv_pct'].mean():.2f}%")
    print(f"Avg MFM: {recent['mfm'].mean():.4f}")
    print(f"Positive MFM Days: {(recent['mfm'] > 0).sum()} / 10")
    
    # Check for "High Volume, Low MFM" days (Distribution Signature)
    dist_days = recent[(recent['delivery_qty'] > recent['delivery_qty'].mean()) & (recent['mfm'] < 0)]
    print("\n--- Potential Distribution Days (High Vol, Neg MFM) ---")
    if dist_days.empty:
        print("None found in recent 10 days.")
    else:
        print(dist_days[['display_date_iso', 'delivery_qty', 'mfm', 'price_close']])

if __name__ == "__main__":
    investigate_distribution()
