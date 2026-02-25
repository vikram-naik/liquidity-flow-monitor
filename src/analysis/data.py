"""
Stock data pipeline — fetch, adjust, compute ledger, run markers, aggregate.

The heavy-lifting marker evaluation is delegated to the Marker Factory
(``src.analysis.markers``).  This module remains responsible for:
  1. DB/cache fetch + corporate action adjustment
  2. Ledger calculations (MFM, DVL, DAVWAP, MCS)
  3. Shared prerequisites (ATR, slopes, rolling averages)
  4. Marker registry loop — ``for m in registry.get_all(): df = m.evaluate(df)``
  5. Quiet period enforcement
  6. Aggregation (weekly / monthly)
"""

import pandas as pd
import sqlite3
import os
import numpy as np
from src.database import get_db_connection, DB_PATH
from src.cache import get_cache
from src.analysis.markers import MarkerRegistry

cache = get_cache()
_registry = MarkerRegistry()


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
    Returns: (DataFrame, anchor_metadata, volume_profile)
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

    # ─── Ledger Calculations (Done on Daily Granularity) ───────────────
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

    # ─── Shared Prerequisites (consumed by marker classes) ─────────────
    # POC Price
    poc_price = 0.0
    if volume_profile:
        poc_bin = next((b for b in volume_profile if b.get('is_poc')), None)
        if poc_bin:
            poc_price = (poc_bin['price_start'] + poc_bin['price_end']) / 2.0

    # Rolling averages
    df['deliv_sma_10'] = df['delivery_qty'].rolling(10).mean()
    df['prev_high'] = df['price_high'].shift(1)
    df['prev_close'] = df['price_close'].shift(1)

    # True Range and 50-period ATR
    df['tr'] = np.maximum(
        df['price_high'] - df['price_low'],
        np.maximum(
            abs(df['price_high'] - df['prev_close']),
            abs(df['price_low'] - df['prev_close'])
        )
    )
    df['atr_50'] = df['tr'].rolling(window=50, min_periods=1).mean()

    # 5-day slope (rate of change) with graceful degradation near anchor
    first_valid_idx = df['dvl'].first_valid_index()
    cum_dvl_slope_anchor = pd.Series(np.nan, index=df.index)

    if first_valid_idx is not None:
        anchor_row_pos = df.index.get_loc(first_valid_idx)
        df['days_since_anchor'] = range(-anchor_row_pos + 1, len(df) - anchor_row_pos + 1)

        dvl_diff_5 = df['dvl'].diff(5) / 5.0
        mcs_diff_5 = df['mcs'].diff(5) / 5.0

        # Cumulative slope from anchor
        anchor_dvl = df.at[first_valid_idx, 'dvl']
        anchor_mcs = df.at[first_valid_idx, 'mcs']

        cum_dvl_slope_anchor = (df['dvl'] - anchor_dvl) / df['days_since_anchor']
        cum_mcs_slope_anchor = (df['mcs'] - anchor_mcs) / df['days_since_anchor']

        # First 4 days use cumulative, day 5+ uses diff(5)
        df['dvl_slope_5'] = dvl_diff_5.where(df['days_since_anchor'] >= 5, cum_dvl_slope_anchor)
        df['mcs_slope_5'] = mcs_diff_5.where(df['days_since_anchor'] >= 5, cum_mcs_slope_anchor)
    else:
        df['dvl_slope_5'] = np.nan
        df['mcs_slope_5'] = np.nan
        df['days_since_anchor'] = 0

    # Expose cumulative DVL slope for IntensityMarker via a private column
    df['_cum_dvl_slope'] = cum_dvl_slope_anchor

    # ─── Marker Evaluation Loop ────────────────────────────────────────
    for marker in _registry.get_all():
        df = marker.evaluate(df)

    # ─── Quiet Period ──────────────────────────────────────────────────
    # Suppress boolean flag markers for the first 3 days after anchor
    quiet_mask = df['days_since_anchor'] <= 3
    for marker in _registry.get_all():
        meta = marker.metadata()
        if not meta.get('is_chart_marker'):
            continue
        flag_key = meta.get('flag_key')
        if flag_key and flag_key in df.columns:
            # Only suppress boolean flags, not numeric columns like grind_level
            if df[flag_key].dtype == bool:
                df.loc[quiet_mask, flag_key] = False

    # Clean up private column
    df.drop(columns=['_cum_dvl_slope'], inplace=True, errors='ignore')

    # ─── Aggregation ───────────────────────────────────────────────────
    if agg_period in ("weekly", "monthly"):
        # Base columns shared by all aggregation periods
        base_agg = {
            "price_open": "first", "price_high": "max",
            "price_low": "min", "price_close": "last",
            "volume_total": "sum", "delivery_qty": "sum",
            "daily_flow": "sum",
            "dvl": "last",
            "dvl_cumulative": "last",
            "davwap": "last",
            "mcs": "last",
            "symbol": "last",
        }

        # Dynamically add marker aggregation rules
        for marker in _registry.get_all():
            for col, func in marker.agg_rules().items():
                if col in df.columns:
                    base_agg[col] = func

        rule = "W-FRI" if agg_period == "weekly" else "ME"
        df = df.resample(rule).agg(base_agg)
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
