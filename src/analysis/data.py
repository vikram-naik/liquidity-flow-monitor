import pandas as pd
import sqlite3
import os
import numpy as np
from src.database import get_db_connection, DB_PATH
from src.cache import get_cache

cache = get_cache()

def load_stock_list() -> list[str]:
    """Return sorted list of distinct symbols in the DB."""
    conn = get_db_connection()
    df = pd.read_sql("SELECT DISTINCT symbol FROM nse_delivery_log ORDER BY symbol", conn)
    conn.close()
    return df["symbol"].tolist()

from src.analysis.ledger import (
    calculate_mfm, calculate_dvl, calculate_cumulative_dvl, 
    calculate_davwap, get_or_create_anchor, calculate_mcs, calculate_volume_profile
)

def get_stock_data(symbol: str, agg_period: str = "daily", lookback_days: int = 365) -> tuple[pd.DataFrame, dict, list]:
    """
    Fetch OHLCV + delivery data for *symbol* with optional aggregation.
    Returns: (DataFrame, anchor_metadata)
    """
    # Try cache first
    cache_key = f"lfm:raw_data:{symbol.upper()}"
    df = cache.get(cache_key)
    
    if df is not None:
        print(f"DEBUG: Cache Hit for {symbol.upper()}")
    else:
        print(f"DEBUG: Cache Miss for {symbol.upper()}")
        conn = get_db_connection()
        df = pd.read_sql(
            "SELECT * FROM nse_delivery_log WHERE symbol = ? ORDER BY record_date ASC",
            conn, params=(symbol.upper(),)
        )
        
        if not df.empty:
            # --- APPLY CORPORATE ACTIONS (SPLITS) ---
            cas = pd.read_sql(
                "SELECT ex_date, ratio_factor FROM corporate_actions WHERE symbol = ? AND ca_type = 'SPLIT' ORDER BY ex_date DESC",
                conn, params=(symbol.upper(),)
            )
            
            for _, ca in cas.iterrows():
                ex_date = pd.to_datetime(ca['ex_date'])
                factor = float(ca['ratio_factor'])
                
                # Mask dates strictly BEFORE the ex_date
                # Ensure record_date is datetime for comparison
                temp_dates = pd.to_datetime(df['record_date'])
                mask = temp_dates < ex_date
                
                # Adjust Prices (Divide by factor)
                price_cols = ['price_open', 'price_high', 'price_low', 'price_close']
                for col in price_cols:
                    if col in df.columns:
                        df.loc[mask, col] = df.loc[mask, col] / factor
                
                # Adjust Volumes (Multiply by factor)
                vol_cols = ['volume_total', 'delivery_qty']
                for col in vol_cols:
                    if col in df.columns:
                        df.loc[mask, col] = (df.loc[mask, col] * factor).round(0)
            
            # --- CACHE THE ADJUSTED DATAFRAME ---
            cache.set(cache_key, df, ttl=86400) # Cache for 24h by default
        
        conn.close()

    if df.empty:
        return df, None, []

    df["record_date"] = pd.to_datetime(df["record_date"])
    
    # Ensure OHLC columns exist
    for col in ("price_open", "price_high", "price_low"):
        if col not in df.columns or df[col].isnull().all():
            df[col] = df["price_close"]
        else:
            df[col] = df[col].fillna(df["price_close"])

    df = df.set_index("record_date")

    # --- Ledger Calculations (Done on Daily Granularity) ---
    df['mfm'] = calculate_mfm(df)
    df['daily_flow'] = df['mfm'] * df['delivery_qty']
    
    # Find Anchor (persisted)
    anchor = get_or_create_anchor(symbol, df)
    anchor_date = pd.to_datetime(anchor['anchor_date']) if anchor else None
    
    # Calculate Anchored Volume Profile from Day Zero
    volume_profile = calculate_volume_profile(df, bins=50, anchor_date=anchor_date)
    
    # Calculate DVL and DAVWAP on daily data
    df['dvl'] = calculate_dvl(df, anchor_date)
    df['davwap'] = calculate_davwap(df, anchor_date)
    df['dvl_cumulative'] = calculate_cumulative_dvl(df)
    df['mcs'] = calculate_mcs(df, window=30)

    # --- Smart Money Signals ---
    # 1. Extract POC Price
    poc_price = 0.0
    if volume_profile:
        poc_bin = next((b for b in volume_profile if b.get('is_poc')), None)
        if poc_bin:
            poc_price = (poc_bin['price_start'] + poc_bin['price_end']) / 2.0

    # 2. Rolling Averages & Slopes
    df['deliv_sma_10'] = df['delivery_qty'].rolling(10).mean()
    df['prev_high'] = df['price_high'].shift(1)
    
    # Calculate 5-day slope (rate of change) to confirm sustained rising trends
    # Gracefully degrade to cumulative slope for the first 4 days post-anchor to avoid NaNs disabling filters
    
    # Find the index of the first non-NaN DVL (which is the anchor point)
    first_valid_idx = df['dvl'].first_valid_index()
    if first_valid_idx is not None:
        anchor_row_pos = df.index.get_loc(first_valid_idx)
        # days_since_anchor: 1 on anchor day, 2 on next day, etc. 0 or negative before anchor.
        df['days_since_anchor'] = range(-anchor_row_pos + 1, len(df) - anchor_row_pos + 1)
        
        dvl_diff_5 = df['dvl'].diff(5) / 5.0
        mcs_diff_5 = df['mcs'].diff(5) / 5.0
        
        # Calculate cumulative slope from anchor row specifically
        anchor_dvl = df.at[first_valid_idx, 'dvl']
        anchor_mcs = df.at[first_valid_idx, 'mcs']
        
        # Only calculate for rows at or after anchor (days_since_anchor >= 1)
        # Avoid division by zero on anchor day (day 1)
        cum_dvl_slope_anchor = (df['dvl'] - anchor_dvl) / df['days_since_anchor']
        cum_mcs_slope_anchor = (df['mcs'] - anchor_mcs) / df['days_since_anchor']
        
        # For the first 4 days (days 1-4), use cumulative. Day 5+ uses diff(5)
        # We use where/mask to be precise
        df['dvl_slope_5'] = dvl_diff_5.where(df['days_since_anchor'] >= 5, cum_dvl_slope_anchor)
        df['mcs_slope_5'] = mcs_diff_5.where(df['days_since_anchor'] >= 5, cum_mcs_slope_anchor)
    else:
        # Fallback if no anchor (shouldn't happen with get_stock_data)
        df['dvl_slope_5'] = np.nan
        df['mcs_slope_5'] = np.nan
        df['days_since_anchor'] = 0

    # 3. Boolean Signal Rules
    # Context Rule: Price holds above Delivery AVWAP
    ctx_pass = df['price_close'] > df['davwap']

    # Coil (Compression): Indecision candle, low delivery, neutral MCS, holding context, close to DAVWAP, and Ledger is NOT actively dumping
    # Coil Strength Multi-Pillar Scoring (0-100 pts)
    # Pillar 1: Geometry (30 pts) - Rewards tight bodies (Indecision)
    # 1.0 = perfect doji, 0.7 = our 30% body limit.
    range_total = df['price_high'] - df['price_low']
    body_top = df[['price_open', 'price_close']].max(axis=1)
    body_bot = df[['price_open', 'price_close']].min(axis=1)
    body_size = body_top - body_bot
    upper_wick = df['price_high'] - body_top
    lower_wick = body_bot - df['price_low']

    # Coil Strength Multi-Pillar Scoring (0-100 pts)
    # Pillar 1: Ledger (30 pts) - Rewards rising institutional flow (Dominant)
    # Rising (>10% SMA): 30, Stable (>-10% SMA): 15, Diving: 0
    score_ledger = pd.Series(0.0, index=df.index, dtype=float)
    score_ledger[df['dvl_slope_5'] > (df['deliv_sma_10'] * 0.1)] = 30
    score_ledger[(df['dvl_slope_5'] <= (df['deliv_sma_10'] * 0.1)) & (df['dvl_slope_5'] > -(df['deliv_sma_10'] * 0.1))] = 15
    
    # Pillar 2: Momentum (20 pts) - Rewards rising MCS (Spark)
    # Rising (>0.01): 20, Flat (>-0.01): 10, Falling: 0
    score_momentum = pd.Series(0.0, index=df.index, dtype=float)
    score_momentum[df['mcs_slope_5'] > 0.01] = 20
    score_momentum[(df['mcs_slope_5'] <= 0.01) & (df['mcs_slope_5'] > -0.01)] = 10
    
    # Pillar 3: Geometry (20 pts) - Rewards tight bodies (Indecision)
    score_geometry = 20 * (1.0 - (body_size / range_total)).clip(0, 1)
    
    # Pillar 4: Dryness (20 pts) - Rewards low delivery vs SMA 10
    score_dryness = 20 * (1.0 - (df['delivery_qty'] / df['deliv_sma_10'])).clip(0, 1)
    
    # Pillar 5: Proximity (10 pts) - Rewards proximity to DAVWAP (Symmetrical 3% limit)
    dist_pct = ((df['price_close'] - df['davwap']) / df['davwap']).abs()
    score_proximity = 10 * (1.0 - (dist_pct / 0.03)).clip(0, 1)
    
    df['coil_score'] = (score_geometry + score_dryness + score_ledger + score_momentum + score_proximity).round(0)
    
    # Filter: Indecisive wick geometry is still a fundamental requirement (must have 2-sided wicks)
    wick_check = (range_total > 0) & \
                 (upper_wick >= range_total * 0.1) & \
                 (lower_wick >= range_total * 0.1)

    # Decision: Trigger Coil if Score >= 50 and it's a structural 'wicky' candle AND within 3% of DAVWAP.
    # Note: ctx_pass removed to allow coils to trigger slightly below DAVWAP (Accumulation trap).
    df['is_coil'] = wick_check & (df['coil_score'] >= 50) & (dist_pct <= 0.03)
    df['is_coil'] = df['is_coil'].fillna(False)

    # Ignition Strength Multi-Pillar Scoring (0-100 pts)
    # Pillar 1: Value Proximity (30 pts) - Symmetric 3%, tiered by direction
    # Above: 30 pts, Below: 15 pts. Linear decay within 3% bracket.
    dist_raw = (df['price_close'] - df['davwap']) / df['davwap']
    score_prox_ign = pd.Series(0.0, index=df.index, dtype=float)
    
    mask_above = dist_raw >= 0
    mask_below = dist_raw < 0
    
    score_prox_ign[mask_above] = 30 * (1.0 - (dist_raw[mask_above] / 0.03)).clip(0, 1)
    score_prox_ign[mask_below] = 15 * (1.0 - (abs(dist_raw[mask_below]) / 0.03)).clip(0, 1)

    # Pillar 2: Vol Conviction (25 pts) - Rewards delivery surge
    # Scales from 1.0x SMA (0 pts) to 1.5x SMA (25 pts)
    score_vol_ign = 25 * ((df['delivery_qty'] / df['deliv_sma_10']) - 1.0).clip(0, 0.5) / 0.5
    
    # Pillar 3: Ledger Intensity (20 pts) - Rewards 5-day DVL acceleration
    # Scales from 0.2x SMA (0 pts) to 0.7x SMA (20 pts)
    score_ledger_ign = 20 * ((df['dvl_slope_5'] / df['deliv_sma_10']) - 0.2).clip(0, 0.5) / 0.5
    
    # Pillar 4: Momentum Velocity (15 pts) - Rewards sharp MCS acceleration
    # Scales from 0.01 (0 pts) to 0.05 (15 pts)
    score_mom_ign = 15 * (df['mcs_slope_5'] - 0.01).clip(0, 0.04) / 0.04
    
    # Pillar 5: Base Score (10 pts) -baseline for clearing the breakout candle check
    score_base_ign = 10
    
    df['ignition_score'] = (score_prox_ign + score_vol_ign + score_ledger_ign + score_mom_ign + score_base_ign).round(0)
    
    # Decision: Trigger Ignition if Score >= 50 and it passes hard breakout candle requirements AND within 3% of DAVWAP.
    is_expansion_candle = (df['price_close'] > df['price_open']) & \
                          (df['price_close'] > df['prev_high'])
                          
    df['is_ignition'] = is_expansion_candle & (df['ignition_score'] >= 50) & (abs(dist_raw) <= 0.03)
    df['is_ignition'] = df['is_ignition'].fillna(False)

    # POC Bounce: Touching POC zone (<2.0% from low) and closing above it, supported by rising DVL/MCS, holding context
    if poc_price > 0:
        dist_to_poc = abs(df['price_low'] - poc_price) / poc_price
        df['is_poc_bounce'] = ctx_pass & (dist_to_poc < 0.02) & (df['price_close'] > poc_price) & \
                              (df['mcs_slope_5'] > 0) & (df['dvl_slope_5'] > 0)
    else:
        df['is_poc_bounce'] = False
    df['is_poc_bounce'] = df['is_poc_bounce'].fillna(False)

    # POC Breakout: Opening below POC, surging through with high delivery and positive structural momentum
    if poc_price > 0:
        df['is_poc_breakout'] = (df['price_close'] > poc_price) & \
                                (df['price_open'] < poc_price) & \
                                (df['delivery_qty'] > df['deliv_sma_10']) & \
                                (df['mcs_slope_5'] > 0) & (df['dvl_slope_5'] > 0)
    else:
        df['is_poc_breakout'] = False
    df['is_poc_breakout'] = df['is_poc_breakout'].fillna(False)

    # 4. Quiet Period Requirement
    # Per user request: Suppress all signals for the first 3 days after an anchor (Day 0 kickstart)
    # This ensure slopes have enough history (min 3 dots) to reflect meaningful conviction.
    quiet_mask = df['days_since_anchor'] <= 3
    for col in ['is_coil', 'is_ignition', 'is_poc_bounce', 'is_poc_breakout']:
        df.loc[quiet_mask, col] = False

    # 5. Trend Intensity (0-90°) & Velocity Status
    # Ledger Intensity: Normalized vs GLS (Global Ledger Slope from Anchor)
    # GLS is calculated at line 94 as cum_dvl_slope_anchor
    gls = df['dvl_slope_5'].where(df['days_since_anchor'] <= 5, cum_dvl_slope_anchor) 
    # ^ Fallback to local if very near anchor, otherwise use global baseline
    
    # We use GLS specifically as the baseline for 'Angle'
    # Current pace / Trend Average pace
    l_ratio = (df['dvl_slope_5'] / gls.abs().replace(0, np.nan)).fillna(0)
    df['ledger_angle'] = np.degrees(np.arctan(l_ratio.clip(0))).round(1)
    
    # Velocity Status Logic
    # Accelerating: >1.0 ratio (>45 deg)
    # Steady: 0.7-1.0 ratio (35-45 deg)
    # Weakening: 0-0.7 ratio (0-35 deg)
    # Reversing: <0 ratio (0 deg)
    df['ledger_velocity'] = "Steady"
    df.loc[l_ratio > 1.0, 'ledger_velocity'] = "Accelerating"
    df.loc[(l_ratio <= 1.0) & (l_ratio >= 0.7), 'ledger_velocity'] = "Steady"
    df.loc[(l_ratio < 0.7) & (l_ratio >= 0), 'ledger_velocity'] = "Weakening"
    df.loc[l_ratio < 0, 'ledger_velocity'] = "Reversing"

    # MCS Intensity: Normalized vs 0.1 shift/day (Keep local for high-sensitivity spark)
    m_intensity = (df['mcs_slope_5'] / 0.1).fillna(0)
    df['mcs_angle'] = np.degrees(np.arctan(m_intensity.clip(0))).round(1)

    # Aggregation
    if agg_period == "weekly":
        df = df.resample("W-FRI").agg({
            "price_open": "first", "price_high": "max",
            "price_low": "min", "price_close": "last",
            "volume_total": "sum", "delivery_qty": "sum",
            "daily_flow": "sum", # Preserves daily granularity for Ledger
            "dvl": "last",       # Latest state of ledger
            "dvl_cumulative": "last",
            "davwap": "last",    # Final AVWAP value for the week
            "mcs": "last",
            "is_coil": "any",
            "is_ignition": "any",
            "is_poc_bounce": "any",
            "is_poc_breakout": "any",
            "ledger_angle": "last",
            "mcs_angle": "last",
            "ledger_velocity": "last",
            "symbol": "last",
        })
        # Only drop rows where price_close is NaN (no trading data for that week)
        df = df.dropna(subset=["price_close"])
        df["delivery_pct"] = (df["delivery_qty"] / df["volume_total"] * 100).fillna(0)
        # Recalculate MFM for the weekly candle just for color-coding bars if needed, 
        # but the Ledger relies on 'daily_flow'
        df['mfm_agg'] = calculate_mfm(df) 
        
    elif agg_period == "monthly":
        df = df.resample("ME").agg({
            "price_open": "first", "price_high": "max",
            "price_low": "min", "price_close": "last",
            "volume_total": "sum", "delivery_qty": "sum",
            "daily_flow": "sum",
            "dvl": "last",
            "dvl_cumulative": "last",
            "davwap": "last",
            "mcs": "last",
            "is_coil": "any",
            "is_ignition": "any",
            "is_poc_bounce": "any",
            "is_poc_breakout": "any",
            "ledger_angle": "last",
            "mcs_angle": "last",
            "ledger_velocity": "last",
            "symbol": "last",
        })
        df = df.dropna(subset=["price_close"])
        df["delivery_pct"] = (df["delivery_qty"] / df["volume_total"] * 100).fillna(0)
        df['mfm_agg'] = calculate_mfm(df)
    else:
        df['mfm_agg'] = df['mfm']

    # Slice lookback (only after calculation so we don't break cumulative series)
    if lookback_days > 0:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)
        df = df[df.index >= cutoff]

    df = df.reset_index()
    df["display_date_iso"] = df["record_date"].dt.strftime("%Y-%m-%d")
    return df, anchor, volume_profile
