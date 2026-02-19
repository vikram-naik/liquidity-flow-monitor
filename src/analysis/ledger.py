import pandas as pd
import numpy as np
import sqlite3
import json
from datetime import datetime
from src.database import get_db_connection
import logging

logger = logging.getLogger(__name__)

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

def calculate_cumulative_dvl(df: pd.DataFrame) -> pd.Series:
    """
    Calculate Total Cumulative DVL from the start of available data.
    Used for the 'Ghost Line' to show historical context.
    """
    if 'mfm' not in df.columns:
        df['mfm'] = calculate_mfm(df)
    
    flow = df['mfm'] * df['delivery_qty']
    return flow.fillna(0).cumsum()

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

def find_day_zero_anchor(df: pd.DataFrame, lookback_left: int = 10, lookback_right: int = 5, target_month_date: str = None) -> dict:
    """
    Identify THE latest valid Day Zero anchor date based on PDF logic:
    If target_month_date is provided (YYYY-MM-DD), it forces the anchor to be the lowest low within that month.
    """
    if len(df) < lookback_left + lookback_right + 20:
        return None

    df = df.sort_index()
    
    # --- Manual Override Logic ---
    if target_month_date:
        try:
            t_date = pd.to_datetime(target_month_date)
            # Find the month containing this date
            start_of_month = t_date.replace(day=1)
            # End of month
            next_month = start_of_month + pd.DateOffset(months=1)
            end_of_month = next_month - pd.DateOffset(days=1)
            
            min_month_date = end_of_month # Alignment for code below
            min_month_val = 0 # Placeholder
        except Exception as e:
            logger.error(f"Invalid target_month_date: {target_month_date}")
            return None
    else:
        # --- Hybrid Logic: Monthly Context ---
        
        # 1. Resample to Monthly to find major structural lows
        # distinct months
        df_monthly = df.resample('ME').agg({
            'price_low': 'min',
            'price_close': 'last',
            'delivery_qty': 'sum'
        }).dropna()

        if len(df_monthly) < 3:
            # Not enough monthly data, fallback to simple daily min
            daily_min_idx = df['price_low'].idxmin()
            return {
                'anchor_date': daily_min_idx.strftime('%Y-%m-%d'),
                'anchor_type': 'ABS_MIN',
                'meta': json.dumps({'low': float(df.loc[daily_min_idx]['price_low'])})
            }

        # 2. Find the lowest low in the last 5 years
        min_month_date = df_monthly['price_low'].idxmin()
        min_month_val = df_monthly.loc[min_month_date]['price_low']
    
    # 3. Drill down to Daily to find specific date
    if not target_month_date:
        start_of_month = min_month_date.replace(day=1)
        end_of_month = min_month_date
    
    
    # 3. Drill down to Daily to find specific date
    # Define range for that month
    start_of_month = min_month_date.replace(day=1)
    end_of_month = min_month_date  # 'ME' index is end of month
    
    mask_month = (df.index >= start_of_month) & (df.index <= end_of_month)
    df_target_month = df[mask_month]
    
    if df_target_month.empty:
        # Fallback
        return None
        
    # The specific day with the lowest low
    day_zero_date = df_target_month['price_low'].idxmin()
    day_zero_low = df_target_month.loc[day_zero_date]['price_low']
    
    # 4. Light Validation (Volume)
    # Check if this low has "stopping volume" or accumulated volume.
    # We won't strictly REJECT it if volume is low, because Price Structure is King for Day Zero,
    # but we will tag it.
    
    # Check 3-day volume around the low vs 20-day average
    idx_loc = df.index.get_loc(day_zero_date)
    start_loc = max(0, idx_loc - 1)
    end_loc = min(len(df), idx_loc + 2)
    
    vol_3d = df.iloc[start_loc:end_loc]['delivery_qty'].sum()
    avg_vol = df['delivery_qty'].rolling(20).mean().iloc[idx_loc]
    
    is_climax = False
    if not pd.isna(avg_vol) and avg_vol > 0:
        if vol_3d > avg_vol * 1.5: # 1.5x avg volume
            is_climax = True
            
    return {
        'anchor_date': day_zero_date.strftime('%Y-%m-%d'),
        'anchor_type': 'MANUAL_MONTHLY_MIN' if target_month_date else 'HYBRID_MONTHLY_MIN',
        'meta': json.dumps({
            'low': float(day_zero_low),
            'monthly_context': start_of_month.strftime('%Y-%m'), # Use start_of_month for consistency
            'vol_climax': is_climax,
            'is_manual': bool(target_month_date)
        })
    }

def get_or_create_anchor(symbol: str, df: pd.DataFrame, force_new: bool = False, manual_date: str = None) -> dict:
    """Check DB for stored anchor, else calculate and save. force_new=True avoids DB read."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if not force_new and not manual_date:
        cursor.execute("SELECT anchor_date, anchor_type, meta FROM symbol_anchors WHERE symbol = ?", (symbol.upper(),))
        row = cursor.fetchone()
        
        if row:
            conn.close()
            return {
                'anchor_date': row[0],
                'anchor_type': row[1],
                'meta': row[2]
            }
    
    # Not in DB or Forced, find it
    anchor = find_day_zero_anchor(df, target_month_date=manual_date)
    if anchor:
        cursor.execute(
            "INSERT OR REPLACE INTO symbol_anchors (symbol, anchor_date, anchor_type, meta) VALUES (?, ?, ?, ?)",
            (symbol.upper(), anchor['anchor_date'], anchor['anchor_type'], anchor['meta'])
        )
        conn.commit()
    
    conn.close()
    return anchor
