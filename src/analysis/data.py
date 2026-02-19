import pandas as pd
import sqlite3
import os
from src.database import get_db_connection, DB_PATH

def load_stock_list() -> list[str]:
    """Return sorted list of distinct symbols in the DB."""
    conn = get_db_connection()
    df = pd.read_sql("SELECT DISTINCT symbol FROM nse_delivery_log ORDER BY symbol", conn)
    conn.close()
    return df["symbol"].tolist()

from src.analysis.ledger import calculate_mfm, calculate_dvl, calculate_cumulative_dvl, calculate_davwap, get_or_create_anchor

def get_stock_data(symbol: str, agg_period: str = "daily", lookback_days: int = 365) -> tuple[pd.DataFrame, dict]:
    """
    Fetch OHLCV + delivery data for *symbol* with optional aggregation.
    Returns: (DataFrame, anchor_metadata)
    """
    conn = get_db_connection()
    # Fetch all data for calculations (we need history for DVL/AVWAP)
    df = pd.read_sql(
        "SELECT * FROM nse_delivery_log WHERE symbol = ? ORDER BY record_date ASC",
        conn, params=(symbol.upper(),)
    )
    conn.close()

    if df.empty:
        return df, None

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
    
    # Calculate DVL and DAVWAP on daily data
    df['dvl'] = calculate_dvl(df, anchor_date)
    df['davwap'] = calculate_davwap(df, anchor_date)
    df['dvl_cumulative'] = calculate_cumulative_dvl(df)

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
    return df, anchor
