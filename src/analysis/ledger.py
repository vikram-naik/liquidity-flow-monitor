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
    Calculate True Money Flow Multiplier (Gap-Adjusted MFM):
    Anchors High and Low to the Previous Close to factor in overnight gaps.
    True High = max(High, Prev_Close)
    True Low = min(Low, Prev_Close)
    True Range = True High - True Low
    True MFM = [(Close - True Low) - (True High - Close)] / True Range
    """
    if 'prev_close' not in df.columns:
        df['prev_close'] = df['price_close'].shift(1)
        
    # Fallback for the very first row
    prev_close = df['prev_close'].fillna(df['price_open'])
    
    true_high = np.maximum(df['price_high'], prev_close)
    true_low = np.minimum(df['price_low'], prev_close)
    true_range = true_high - true_low
    
    # Avoid division by zero for Doji-like bars with zero range
    mfm = np.where(
        true_range == 0,
        0,
        ((df['price_close'] - true_low) - (true_high - df['price_close'])) / true_range
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

def find_day_zero_anchor(df: pd.DataFrame, manual_date: str = None) -> dict:
    """
    Identify the Day Zero anchor date.
    - If manual_date is provided, it uses that exact date.
    - Otherwise, it uses a Volume-Confirmed Volatility Pivot (ATR + Delivery Volume) 
      over the last 104 weeks (2 years).
    """
    if df.empty:
        return None

    df = df.sort_index()
    
    # --- Manual Override Logic ---
    if manual_date:
        try:
            day_zero_date = pd.to_datetime(manual_date)
            # Find the actual closest trading day in the index to avoid alignment issues
            if day_zero_date not in df.index:
                idx = df.index.get_indexer([day_zero_date], method='nearest')[0]
                day_zero_date = df.index[idx]
            
            day_zero_low = float(df.loc[day_zero_date]['price_low'])
            
            return {
                'anchor_date': day_zero_date.strftime('%Y-%m-%d'),
                'anchor_type': 'MANUAL',
                'meta': json.dumps({
                    'low': day_zero_low,
                    'is_manual': True
                })
            }
        except Exception as e:
            logger.error(f"Invalid manual_date: {manual_date} - {e}")
            return None

    # --- Automatic Logic: Volume-Confirmed Volatility Pivot ---
    
    # 1. Isolate the last 2 years (approx 500 trading days)
    recent_df = df.tail(500).copy()
    if len(recent_df) < 50:
        # Fallback to absolute low if not enough data
        day_zero_date = df['price_low'].idxmin()
        return {
            'anchor_date': day_zero_date.strftime('%Y-%m-%d'),
            'anchor_type': 'FALLBACK_MIN',
            'meta': json.dumps({'low': float(df.loc[day_zero_date]['price_low'])})
        }

    # 2. Resample to Weekly to smooth out noise
    weekly = recent_df.resample('W-FRI').agg({
        'price_open': 'first',
        'price_high': 'max',
        'price_low': 'min',
        'price_close': 'last',
        'delivery_qty': 'sum'
    }).dropna()

    if len(weekly) < 20:
        day_zero_date = recent_df['price_low'].idxmin()
        return {
            'anchor_date': day_zero_date.strftime('%Y-%m-%d'),
            'anchor_type': 'FALLBACK_MIN',
            'meta': json.dumps({'low': float(recent_df.loc[day_zero_date]['price_low'])})
        }

    # 3. Calculate True Range and 10-Week ATR
    weekly['prev_close'] = weekly['price_close'].shift(1)
    weekly['tr'] = weekly[['price_high', 'prev_close']].max(axis=1) - weekly[['price_low', 'prev_close']].min(axis=1)
    weekly['atr_10'] = weekly['tr'].rolling(window=10).mean()

    # 4. Calculate 10-Week Delivery Volume SMA
    weekly['deliv_sma_10'] = weekly['delivery_qty'].rolling(window=10).mean()
    weekly = weekly.dropna()

    # 5. Scan backwards for "Structural Ignition"
    valid_pivots = []
    
    # Iterate from latest down to 10th week
    for i in range(len(weekly) - 1, 9, -1):
        current_week = weekly.iloc[i]
        
        # 10-week base preceding this breakout week
        base_window = weekly.iloc[i-10 : i]
        base_low = base_window['price_low'].min()
        
        # Resistance Band: Base Low + (3 * ATR)
        breakout_threshold = base_low + (3 * current_week['atr_10'])
        
        # Condition 1: Price break above resistance
        is_price_breakout = current_week['price_close'] > breakout_threshold
        
        # Condition 2: Institutional volume confirm (TIGHTENED to 1.5x SMA)
        is_vol_confirmed = current_week['delivery_qty'] > (current_week['deliv_sma_10'] * 1.5)
        
        if is_price_breakout and is_vol_confirmed:
            # Pivot confirmed. Find the exact daily low in that 10-week base.
            window_start = base_window.index[0] - pd.Timedelta(days=6)
            window_end = base_window.index[-1]
            
            daily_base = recent_df.loc[window_start:window_end]
            if not daily_base.empty:
                daily_low_date = daily_base['price_low'].idxmin()
                
                # Calculate Anchor Score
                # 1. Volume Expansion Multiple (e.g., 2.5x SMA = 2.5 score)
                vol_expansion = current_week['delivery_qty'] / current_week['deliv_sma_10'] if current_week['deliv_sma_10'] > 0 else 0
                
                # 2. Base Tightness (Inverse of standard deviation of closes)
                base_closes = base_window['price_close']
                base_std = base_closes.std()
                # Normalize tightness: lower std = higher score. Prevent div by zero.
                tightness_score = 1.0 / (base_std / base_closes.mean() + 1e-6) 
                
                # Total Score - heavily weight the volume expansion
                total_score = (vol_expansion * 2.0) + tightness_score

                valid_pivots.append({
                    'date': daily_low_date,
                    'score': total_score,
                    'vol_expansion': vol_expansion
                })
    
    # Select the best pivot
    day_zero_date = None
    anchor_type = 'FALLBACK_MIN'
    
    if valid_pivots:
        # Sort by score descending and take the best
        valid_pivots.sort(key=lambda x: x['score'], reverse=True)
        best_pivot = valid_pivots[0]
        day_zero_date = best_pivot['date']
        anchor_type = 'VOL_PIVOT'
        logger.info(f"Selected Anchor {day_zero_date} with score {best_pivot['score']:.2f} (Vol Expansion {best_pivot['vol_expansion']:.2f}x)")
    
    # Final Fallback: use absolute low of 2-year window if no pivot found
    if day_zero_date is None:
        day_zero_date = recent_df['price_low'].idxmin()

    day_zero_low = float(df.loc[day_zero_date]['price_low'])
    return {
        'anchor_date': day_zero_date.strftime('%Y-%m-%d'),
        'anchor_type': anchor_type,
        'meta': json.dumps({
            'low': day_zero_low,
            'is_manual': False
        })
    }

def calculate_mcs(df: pd.DataFrame, window: int = 30) -> pd.Series:
    """
    Calculates a rolling 30-day Pearson correlation between the Typical Price
    (High+Low+Close)/3 and the Relative Delivery Volume (RDV).
    RDV is delivery_qty divided by its 30-day SMA.
    """
    typical_price = (df['price_high'] + df['price_low'] + df['price_close']) / 3
    
    # Relative Delivery Volume (RDV)
    sma_delivery = df['delivery_qty'].rolling(window=window).mean()
    rdv = df['delivery_qty'] / sma_delivery
    
    # Pearson correlation over a rolling window
    mcs = typical_price.rolling(window=window).corr(rdv)
    
    # Fill NaNs with 0
    return mcs.fillna(0)

def calculate_volume_profile(df: pd.DataFrame, bins: int = 50, anchor_date=None) -> list[dict]:
    """
    Calculates the volume profile across the dataframe, filtered from anchor_date if provided.
    Groups delivery_qty by price bins based on Typical Price.
    Returns: [{"price_start": float, "price_end": float, "volume": float, "is_poc": bool}, ...]
    """
    if anchor_date:
        df = df[df.index >= pd.to_datetime(anchor_date)]
        
    if df.empty or 'delivery_qty' not in df.columns:
        return []

    typical_price = (df['price_high'] + df['price_low'] + df['price_close']) / 3
    
    df_temp = pd.DataFrame({
        'typical_price': typical_price,
        'delivery_qty': df['delivery_qty']
    }).dropna()
    
    if df_temp.empty:
        return []

    # Use pd.cut to create bins
    df_temp['bin'] = pd.cut(df_temp['typical_price'], bins=bins)
    
    # Group by bins and aggregate volume
    profile = df_temp.groupby('bin', observed=False)['delivery_qty'].sum().reset_index()
    
    # Find POC (Point of Control)
    poc_index = profile['delivery_qty'].idxmax()
    
    result = []
    for idx, row in profile.iterrows():
        bin_interval = row['bin']
        if pd.isna(bin_interval):
            continue
            
        result.append({
            "price_start": float(bin_interval.left),
            "price_end": float(bin_interval.right),
            "volume": float(row['delivery_qty']),
            "is_poc": bool(idx == poc_index)
        })
        
    return result

def get_or_create_anchor(symbol: str, df: pd.DataFrame, force_new: bool = False, manual_date: str = None) -> dict:
    """Check DB for stored anchor, else calculate and save. force_new=True avoids DB read."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        if not force_new and not manual_date:
            cursor.execute("SELECT anchor_date, anchor_type, meta FROM symbol_anchors WHERE symbol = ?", (symbol.upper(),))
            row = cursor.fetchone()
            
            if row:
                return {
                    'anchor_date': row[0],
                    'anchor_type': row[1],
                    'meta': row[2]
                }
        
        # Not in DB or Forced, find it
        anchor = find_day_zero_anchor(df, manual_date=manual_date)
        if anchor:
            cursor.execute(
                "INSERT OR REPLACE INTO symbol_anchors (symbol, anchor_date, anchor_type, meta) VALUES (?, ?, ?, ?)",
                (symbol.upper(), anchor['anchor_date'], anchor['anchor_type'], anchor['meta'])
            )
            conn.commit()
        return anchor
    finally:
        conn.close()
