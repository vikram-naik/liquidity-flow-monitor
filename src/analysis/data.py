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
        try:
            df = pd.read_sql(
                "SELECT * FROM nse_delivery_log WHERE symbol = ? ORDER BY record_date ASC",
                conn, params=(symbol.upper(),)
            )
            
            if not df.empty:
                # --- APPLY CORPORATE ACTIONS (SPLITS) ---
                cas = pd.read_sql(
                    "SELECT ex_date, ratio_factor FROM corporate_actions WHERE symbol = ? ORDER BY ex_date DESC",
                    conn, params=(symbol.upper(),)
                )
                
                for _, ca in cas.iterrows():
                    ex_date = pd.to_datetime(ca['ex_date'])
                    factor = float(ca['ratio_factor'])
                    
                    # Mask dates strictly BEFORE the ex_date
                    temp_dates = pd.to_datetime(df['record_date'])
                    mask = temp_dates < ex_date
                    
                    # Adjust Prices
                    price_cols = ['price_open', 'price_high', 'price_low', 'price_close']
                    for col in price_cols:
                        if col in df.columns:
                            df.loc[mask, col] = df.loc[mask, col] / factor
                    
                    # Adjust Volumes
                    vol_cols = ['volume_total', 'delivery_qty']
                    for col in vol_cols:
                        if col in df.columns:
                            df.loc[mask, col] = (df.loc[mask, col] * factor).round(0)
                
                # --- CACHE THE ADJUSTED DATAFRAME ---
                cache.set(cache_key, df, ttl=86400)
        finally:
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
    df['prev_close'] = df['price_close'].shift(1)
    
    # Calculate True Range and 50-period ATR (sniper scope)
    df['tr'] = np.maximum(
        df['price_high'] - df['price_low'],
        np.maximum(
            abs(df['price_high'] - df['prev_close']),
            abs(df['price_low'] - df['prev_close'])
        )
    )
    # min_periods=1 ensures we have an ATR value early in the chart
    df['atr_50'] = df['tr'].rolling(window=50, min_periods=1).mean()
    
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

    # 3. Smart Money Signal Rules (ATR-Based Sniper Scope)
    
    # ---------------------------------------------------------
    # COIL (Volatility Compression & Accumulation)
    # ---------------------------------------------------------
    range_total = df['price_high'] - df['price_low']
    body_top = df[['price_open', 'price_close']].max(axis=1)
    body_bot = df[['price_open', 'price_close']].min(axis=1)
    body_size = body_top - body_bot

    # Hard Triggers (Sniper Scope) - Filter out noise before scoring
    # 1. Total range must be < 0.8 * ATR
    # 2. Body must be tight < 0.4 * ATR
    # 3. Distance from DAVWAP must be <= 1.0 * ATR
    dist_raw_davwap = df['price_close'] - df['davwap']
    dist_atr_davwap = dist_raw_davwap.abs() / df['atr_50']
    
    coil_hard_trigger = (range_total < (0.8 * df['atr_50'])) & \
                        (body_size < (0.4 * df['atr_50'])) & \
                        (dist_atr_davwap <= 1.0)
    
    # Coil Scoring Pillars (0-100 pts)
    # Pillar 1: Geometry (30 pts) - Rewards extreme compression vs normal ATR
    score_geom_coil = 30 * (1.0 - (range_total / df['atr_50'])).clip(0, 1)
    
    # Pillar 2: Dryness (30 pts) - Rewards extreme volume exhaustion
    score_dry_coil = 30 * (1.0 - (df['delivery_qty'] / df['deliv_sma_10'])).clip(0, 1)
    
    # Pillar 3: Ledger Divergence (20 pts) - Flow must be non-negative
    score_ledger_coil = pd.Series(0.0, index=df.index, dtype=float)
    score_ledger_coil[df['dvl_slope_5'] > 0] = 20
    score_ledger_coil[(df['dvl_slope_5'] <= 0) & (df['dvl_slope_5'] > -(df['deliv_sma_10'] * 0.1))] = 10
    
    # Pillar 4: Value Proximity (20 pts) - Rewards proximity to DAVWAP in ATR units
    # Baseline 10 pts for being strictly inside the 1.0 ATR zone
    score_prox_coil = 10 + 10 * (1.0 - dist_atr_davwap).clip(0, 1)

    df['coil_score'] = (score_geom_coil + score_dry_coil + score_ledger_coil + score_prox_coil).round(0)
    df['is_coil'] = coil_hard_trigger & (df['coil_score'] >= 50)
    df['is_coil'] = df['is_coil'].fillna(False)

    # ---------------------------------------------------------
    # IGNITION (Markup Initiation / Expansion)
    # ---------------------------------------------------------
    # Hard Triggers (Sniper Scope)
    # 1. Structural Expansion (strong up candle)
    # 2. Body expansion >= 0.8 * ATR
    # 3. Move originated near DAVWAP (origin within 1.0 ATR)
    
    is_up_candle = (df['price_close'] > df['price_open'])
    origin_price = df[['price_open', 'prev_close']].min(axis=1)
    dist_origin_davwap = (origin_price - df['davwap']) / df['atr_50']
    
    ignition_expansion = df['price_close'] - origin_price
    
    ignition_hard_trigger = is_up_candle & \
                            (ignition_expansion >= (0.8 * df['atr_50'])) & \
                            (dist_origin_davwap.abs() <= 1.0)
                            
    # Ignition Scoring Pillars (0-100 pts)
    # Pillar 1: Expansion Geometry (30 pts)
    body_expansion_atr = ignition_expansion / df['atr_50']
    score_geom_ign = 30 * ((body_expansion_atr - 0.8).clip(0, 1.2) / 1.2)
    
    # Pillar 2: Volume Conviction (30 pts) - Uncapped massive surges
    vol_ratio = df['delivery_qty'] / df['deliv_sma_10']
    score_vol_ign = 30 * ((vol_ratio - 1.0).clip(0, 1.5) / 1.5)
    
    # Pillar 3: Value Origin Proximity (20 pts) - Reward moves starting right at value
    score_prox_ign = 20 * (1.0 - dist_origin_davwap.abs()).clip(0, 1)
    
    # Pillar 4: Ledger Acceleration (20 pts) - Rewards 5-day DVL acceleration
    score_ledger_ign = 20 * ((df['dvl_slope_5'] / df['deliv_sma_10']) - 0.2).clip(0, 0.5) / 0.5
    
    # Pillar 5: Structural Baseline (10 pts) - Rewards passing the strict 0.8 ATR expansion filter
    score_base_ign = 10
    
    df['ignition_score'] = (score_geom_ign + score_vol_ign + score_prox_ign + score_ledger_ign + score_base_ign).round(0)
    df['is_ignition'] = ignition_hard_trigger & (df['ignition_score'] >= 50)
    df['is_ignition'] = df['is_ignition'].fillna(False)

    # ---------------------------------------------------------
    # COMPOSITE GRIND MARKER (3-Day Progressive Buildup)
    # ---------------------------------------------------------
    # Day 1: Expansion >= 0.5 ATR, originating within 1.0 ATR
    is_grind_day1 = (df['price_close'] - origin_price >= 0.5 * df['atr_50']) & (dist_origin_davwap.abs() <= 1.0)
    
    price_close_prev1 = df['price_close'].shift(1)
    origin_prev1 = origin_price.shift(1)
    atr_prev1 = df['atr_50'].shift(1)
    
    # Day 2: Close > Day 1, Cumulative Expansion >= 0.8 ATR
    is_grind_day2 = is_grind_day1.shift(1).fillna(False) & \
                    (df['price_close'] > price_close_prev1) & \
                    (df['price_close'] - origin_prev1 >= 0.8 * atr_prev1)
                    
    origin_prev2 = origin_price.shift(2)
    atr_prev2 = df['atr_50'].shift(2)
    
    # Day 3: Close > Day 2, Cumulative Expansion >= 1.2 ATR, Positive 5-day slope, MCS Confirmation
    mcs_prev3 = df['mcs'].shift(3)
    is_grind_day3 = is_grind_day2.shift(1).fillna(False) & \
                    (df['price_close'] > price_close_prev1) & \
                    (df['price_close'] - origin_prev2 >= 1.2 * atr_prev2) & \
                    (df['dvl_slope_5'] > 0) & \
                    (df['mcs'] > mcs_prev3)
                    
    df['grind_level'] = 0
    
    # Retroactive valid assignment
    df.loc[is_grind_day3, 'grind_level'] = 3
    df.loc[is_grind_day3.shift(-1).fillna(False), 'grind_level'] = 2
    df.loc[is_grind_day3.shift(-2).fillna(False), 'grind_level'] = 1
    
    # Progressive (Ghosting) Handle Live Edge (last 2 rows max)
    if len(df) > 0:
        last_idx = df.index[-1]
        
        if is_grind_day2.at[last_idx] and df.at[last_idx, 'grind_level'] == 0:
            df.at[last_idx, 'grind_level'] = 2
            if len(df) > 1:
                prev_idx = df.index[-2]
                df.at[prev_idx, 'grind_level'] = 1
                
        elif is_grind_day1.at[last_idx] and df.at[last_idx, 'grind_level'] == 0:
            df.at[last_idx, 'grind_level'] = 1

    # 4. Quiet Period Requirement
    # Per user request: Suppress all signals for the first 3 days after an anchor (Day 0 kickstart)
    # This ensure slopes have enough history (min 3 dots) to reflect meaningful conviction.
    quiet_mask = df['days_since_anchor'] <= 3
    for col in ['is_coil', 'is_ignition']:
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
