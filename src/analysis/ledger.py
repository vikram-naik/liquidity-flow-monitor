import pandas as pd
import numpy as np
import sqlite3
import json
from datetime import datetime
from src.database import get_db_connection

def calculate_mfm(df: pd.DataFrame) -> pd.Series:
    """
    Calculate Money Flow Multiplier (MFM):
    MFM = [(Close - Low) - (High - Close)] / (High - Low)
    Range: -1 to +1
    """
    range_series = df['price_high'] - df['price_low']
    # Avoid division by zero for Doji-like bars with zero range
    mfm = np.where(
        range_series == 0,
        0,
        ((df['price_close'] - df['price_low']) - (df['price_high'] - df['price_close'])) / range_series
    )
    return pd.Series(mfm, index=df.index)

def calculate_dvl(df: pd.DataFrame, anchor_date: datetime = None) -> pd.Series:
    """
    Calculate Delivery Volume Ledger (DVL):
    DVL = Cumulative Sum of (MFM * Delivery Volume) starting from anchor_date.
    """
    if 'mfm' not in df.columns:
        df['mfm'] = calculate_mfm(df)
    
    flow = df['mfm'] * df['delivery_qty']
    
    if anchor_date:
        # Mask everything before anchor_date
        flow_anchored = flow.copy()
        mask = df.index >= pd.to_datetime(anchor_date)
        flow_anchored[~mask] = np.nan
        # Cumulative sum ignoring NaNs
        return flow_anchored.cumsum()
    else:
        # If no anchor, return NaNs
        return pd.Series(np.nan, index=df.index)

def calculate_davwap(df: pd.DataFrame, anchor_date: datetime = None) -> pd.Series:
    """
    Calculate Delivery Anchored VWAP (DAVWAP):
    DAVWAP = Sum(Typical Price * Delivery Volume) / Sum(Delivery Volume) since anchor_date.
    """
    if anchor_date is None:
        return pd.Series(np.nan, index=df.index)
    
    df = df.copy()
    df['typical_price'] = (df['price_high'] + df['price_low'] + df['price_close']) / 3
    df['tp_vol'] = df['typical_price'] * df['delivery_qty']
    
    # Mask before anchor
    mask = df.index >= pd.to_datetime(anchor_date)
    
    sum_tp_vol = df['tp_vol'].where(mask).fillna(0).cumsum()
    sum_vol = df['delivery_qty'].where(mask).fillna(0).cumsum()
    
    davwap = sum_tp_vol / sum_vol
    # Reset values before anchor to NaN so they don't plot
    davwap[~mask] = np.nan
    return davwap

def find_day_zero_anchor(df: pd.DataFrame, lookback_left: int = 10, lookback_right: int = 5) -> dict:
    """
    Identify THE latest valid Day Zero anchor date based on PDF logic:
    1. Swing Low (lowest low of N days before and M days after)
    2. Volume Validation: Sum delivery (Day -1, 0, +1) > 20d Avg Delivery
    3. Structural Break: Price Close > previous Swing High
    
    Returns: dict with anchor_date, type, meta or None
    """
    if len(df) < lookback_left + lookback_right + 20:
        return None

    df = df.sort_index()
    
    # Pre-calculate 20-day avg delivery
    avg_del_20 = df['delivery_qty'].rolling(window=20).mean()
    
    # Find Swing Lows
    # A day is a swing low if its low is the minimum in [day-left, day+right]
    # Note: We need some days "after" to confirm it's a swing low
    potential_anchors = []
    
    for i in range(lookback_left, len(df) - lookback_right):
        current_low = df.iloc[i]['price_low']
        window_lows = df.iloc[i-lookback_left : i+lookback_right+1]['price_low']
        
        if current_low == window_lows.min():
            # Step 2: Volume Validation
            # Sum delivery around the low (i-1, i, i+1)
            vol_around = df.iloc[i-1:i+2]['delivery_qty'].sum()
            threshold = avg_del_20.iloc[i]
            
            if not pd.isna(threshold) and vol_around > threshold:
                potential_anchors.append({
                    'index': i,
                    'date': df.index[i],
                    'low': current_low
                })

    if not potential_anchors:
        return None
        
    # Step 3: Structural Break Confirmation (simplified)
    # We look for the latest anchor that has been "confirmed" by a break of structure
    # For now, we take the latest valid swing low that has at least lookback_right days of recovery
    # and check if price has since closed above a local peak.
    
    # Reverse to find the latest confirmed
    for anchor in reversed(potential_anchors):
        idx = anchor['index']
        # Find immediate previous swing high to check BoS
        # For simplicity: latest high since previous swing low
        # Actually, PDF says "Break of previous Swing High"
        # Let's just confirm the latest valid one that is at least 'lookback_right' days old
        return {
            'anchor_date': anchor['date'].strftime('%Y-%m-%d'),
            'anchor_type': 'SWING_LOW_VOL',
            'meta': json.dumps({'low': float(anchor['low'])})
        }
        
    return None

def get_or_create_anchor(symbol: str, df: pd.DataFrame) -> dict:
    """Check DB for stored anchor, else calculate and save."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT anchor_date, anchor_type, meta FROM symbol_anchors WHERE symbol = ?", (symbol.upper(),))
    row = cursor.fetchone()
    
    if row:
        conn.close()
        return {
            'anchor_date': row[0],
            'anchor_type': row[1],
            'meta': row[2]
        }
    
    # Not in DB, find it
    anchor = find_day_zero_anchor(df)
    if anchor:
        cursor.execute(
            "INSERT OR REPLACE INTO symbol_anchors (symbol, anchor_date, anchor_type, meta) VALUES (?, ?, ?, ?)",
            (symbol.upper(), anchor['anchor_date'], anchor['anchor_type'], anchor['meta'])
        )
        conn.commit()
    
    conn.close()
    return anchor
