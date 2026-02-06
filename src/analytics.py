
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

from src.database import get_db_connection

def calculate_squeeze_score():
    """
    Calculates Squeeze Metrics:
    1. 'shock_score': Daily % change (The instant event)
    2. 'pressure_index': Sustained stress (Current Margin vs 30-Day Baseline)
    """
    conn = get_db_connection()
    
    # Fetch data sorted by timestamp
    query = """
    SELECT 
        l.timestamp, 
        l.instrument_id, 
        i.symbol, 
        i.asset_class,
        e.name as exchange,
        l.margin_percent, 
        l.contract_price 
    FROM margin_logs l
    JOIN instruments i ON l.instrument_id = i.id
    JOIN exchanges e ON i.exchange_id = e.id
    ORDER BY l.timestamp ASC
    """
    
    df = pd.read_sql(query, conn)
    conn.close()
    
    if df.empty: return pd.DataFrame()
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    results = []
    
    for instrument_id, group in df.groupby('instrument_id'):
        group = group.sort_values('timestamp')
        
        # 1. The Shock (Instantaneous Change)
        group['shock_score'] = group['margin_percent'].pct_change().fillna(0)
        
        # 2. The Pressure (Sustained Load)
        rolling_base = group['margin_percent'].rolling(window=30, min_periods=1).min()
        rolling_base = rolling_base.replace(0, 0.01) 
        
        group['pressure_index'] = group['margin_percent'] / rolling_base
        group['pressure_index'] = group['pressure_index'].fillna(1.0)
        
        results.append(group)
        
    if not results:
        return pd.DataFrame()

    final_df = pd.concat(results)
    
    # Rename for dashboard compatibility
    final_df['instrument'] = final_df['symbol']
    # margin_now and price_now are kept for compatibility with current Visual 1 refactor
    final_df.rename(columns={'margin_percent': 'margin_now', 'contract_price': 'price_now'}, inplace=True)
    
    return final_df

def get_carry_trade_data():
    """
    Fetches aligned data for Carry Trade Visuals:
    - USDJPY (DEXJPUS)
    - US 10Y Yield (DGS10)
    - Japan 10Y Yield (IRLTLT01JPM156N)
    
    Returns: DataFrame with timestamp, us_yield, jp_yield, usdjpy, spread
    """
    conn = get_db_connection()
    
    # Fetch all yields
    df = pd.read_sql("SELECT timestamp, currency, tenor, rate FROM yield_logs", conn)
    conn.close()
    
    if df.empty:
        return pd.DataFrame()
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Pivot to get columns
    # Create a composite key for pivoting
    df['key'] = df.apply(lambda x: f"{x['currency']}_{x['tenor']}", axis=1)
    
    pivot_df = df.pivot_table(index='timestamp', columns='key', values='rate')
    
    # Columns expected: 'USD_10Y', 'JPY_Spot' (USDJPY), 'JPY_10Y'
    # Rename to friendly names
    col_map = {
        'USD_10Y': 'us_10y',
        'JPY_Spot': 'usdjpy', 
        'JPY_10Y': 'jp_10y'
    }
    
    # Filter columns that exist in pivot_df
    available_cols = [c for c in pivot_df.columns if c in col_map]
    pivot_df = pivot_df[available_cols].rename(columns=col_map)
    
    # Forward fill missing data (especially JPY 10Y which is monthly)
    pivot_df = pivot_df.resample('D').last().ffill()
    
    # Calculate Spread
    if 'us_10y' in pivot_df.columns and 'jp_10y' in pivot_df.columns:
        pivot_df['yield_spread'] = pivot_df['us_10y'] - pivot_df['jp_10y']
        
    return pivot_df.reset_index()


def calculate_fair_value(df):
    """
    Calculate USD/JPY Fair Value based on yield spread.
    
    Heuristic Model: Fair Value = 80 + (20 * Yield_Spread)
    This creates a "rubber band" effect showing divergence from fundamentals.
    
    Returns: DataFrame with fair_value and deviation columns added
    """
    if df.empty:
        return df
    
    if 'yield_spread' not in df.columns:
        return df
        
    df = df.copy()
    df['fair_value'] = 80 + (20 * df['yield_spread'])
    
    if 'usdjpy' in df.columns:
        df['deviation'] = df['usdjpy'] - df['fair_value']
    
    return df


def check_systemic_alert():
    """
    Cross-Asset Alert System
    """
    conn = get_db_connection()
    
    # 1. Check Yield Trend
    yield_df = pd.read_sql("SELECT * FROM yield_logs WHERE tenor='10Y' AND currency='USD' ORDER BY timestamp DESC LIMIT 5", conn)
    yield_rising = False
    if len(yield_df) >= 2:
        if yield_df.iloc[0]['rate'] > yield_df.iloc[1]['rate']:
            yield_rising = True
            
    # 2. Check Margin Hikes
    details = []
    hits = 0
    
    for sym in ['SILVER', 'COPPER', 'GOLD', 'ES', 'NQ']:
        q = f"""
        SELECT margin_percent FROM margin_logs l
        JOIN instruments i ON l.instrument_id = i.id
        WHERE i.symbol = '{sym}' ORDER BY l.timestamp DESC LIMIT 2
        """
        cur = conn.execute(q)
        rows = cur.fetchall()
        if len(rows) >= 2:
            latest = rows[0][0]
            prev = rows[1][0]
            if prev > 0:
                pct = (latest - prev) / prev
                if pct > 0.05:
                    hits += 1
                    details.append(f"{sym} (+{pct*100:.1f}%)")
        
    conn.close()
    
    alert = False
    message = "Normal"
    if hits >= 3 and yield_rising:
        alert = True
        message = f"SYSTEMIC INDUCED SHAKEOUT! Yields Rising + Margin Hikes in {', '.join(details)}"
    elif hits >= 3:
        message = f"High Margin Stress: {', '.join(details)} (Yields not rising)"
        
    # Phase 6 Logic
    cross_asset_msg = check_cross_asset_flush()
    carry_trade_msg = check_carry_trade()
    
    if cross_asset_msg:
        alert = True
        message = f"{message} | {cross_asset_msg}"
    if carry_trade_msg:
        alert = True
        message = f"{message} | {carry_trade_msg}"
        
    return {
        'alert': alert,
        'message': message,
        'yield_rising': yield_rising,
        'hikes': details,
        'cross_asset': cross_asset_msg,
        'carry_trade': carry_trade_msg
    }

def check_cross_asset_flush(conn=None):
    """
    Flag if Copper Margins Up AND Silver/Gold Price Down
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
        
    query = """
    SELECT i.symbol, i.asset_class, l.timestamp, l.margin_percent, l.contract_price
    FROM margin_logs l JOIN instruments i ON l.instrument_id = i.id
    WHERE i.symbol IN ('SILVER', 'GOLD', 'COPPER')
    ORDER BY l.timestamp ASC
    """
    df = pd.read_sql(query, conn)
    if close_conn: conn.close()
    
    if df.empty: return None
    
    copper_hike = False
    metals_dump = False
    
    # Check Copper (as proxy for industrial/inflation)
    sdf = df[df['symbol'] == 'COPPER']
    if len(sdf) >= 2:
        hike = (sdf.iloc[-1]['margin_percent'] - sdf.iloc[-2]['margin_percent']) / sdf.iloc[-2]['margin_percent']
        if hike > 0.05:
            copper_hike = True
                
    # Check Silver/Gold (Prices)
    for sym in ['SILVER', 'GOLD']:
        sdf = df[df['symbol'] == sym]
        if len(sdf) >= 2:
            p_curr = sdf.iloc[-1]['contract_price']
            p_prev = sdf.iloc[-2]['contract_price']
            if p_prev > 0:
                d_price = (p_curr - p_prev) / p_prev
                if d_price < -0.005:
                    metals_dump = True
                    
    if copper_hike and metals_dump:
        return "CROSS-METALS MARGIN FLUSH DETECTED! (Copper Margin Up + Silver/Gold Sold)"
    return None

def check_carry_trade(conn=None):
    """
    Checks for JPY strengthening + Gold/Silver Margin hikes
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    
    # Check JPY Spot (USDJPY)
    query_jpy = "SELECT rate FROM yield_logs WHERE currency='JPY' AND tenor='Spot' ORDER BY timestamp DESC LIMIT 2"
    
    # Check Margin Hikes on any metals
    query_metals = """
    SELECT margin_percent FROM margin_logs l JOIN instruments i ON l.instrument_id = i.id
    WHERE i.symbol IN ('GOLD', 'SILVER') ORDER BY l.timestamp DESC LIMIT 2
    """
    
    j_rows = conn.execute(query_jpy).fetchall()
    m_rows = conn.execute(query_metals).fetchall()
    if close_conn: conn.close()
    
    if len(j_rows) < 2 or len(m_rows) < 2:
        return None
        
    # JPY Strengthening (Rate dropping)
    jpy_strong = j_rows[0][0] < j_rows[1][0]
    
    # Metal Margin Hike
    if m_rows[1][0] == 0: return None
    metal_hike = (m_rows[0][0] - m_rows[1][0]) / m_rows[1][0] > 0.05
    
    if jpy_strong and metal_hike:
        return "GLOBAL DELEVERAGING SIGNAL (JPY Strong + Metal Margins Up)"
        
    return None

def get_credit_stress_data(days: int = 90):
    """
    Fetches DXY, HY_SPREAD, and RRP data from yield_logs for macro plumbing analysis.
    
    Returns: DataFrame with timestamp, dxy, hy_spread, rrp, and trend indicators
    """
    conn = get_db_connection()
    
    df = pd.read_sql("""
        SELECT timestamp, tenor, rate 
        FROM yield_logs 
        WHERE tenor IN ('DXY', 'HY_SPREAD', 'RRP', 'VIX', 'DXY_ICE')
        ORDER BY timestamp ASC
    """, conn)
    conn.close()
    
    if df.empty:
        return pd.DataFrame()
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Pivot to get DXY, HY_SPREAD, and RRP as columns
    pivot_df = df.pivot_table(index='timestamp', columns='tenor', values='rate')
    
    # Rename columns to lowercase for consistency
    col_map = {'DXY': 'dxy', 'HY_SPREAD': 'hy_spread', 'RRP': 'rrp', 'VIX': 'vix', 'DXY_ICE': 'dxy_ice'}
    available_cols = [c for c in pivot_df.columns if c in col_map]
    pivot_df = pivot_df[available_cols].rename(columns=col_map)
    
    # Forward fill and resample to daily
    pivot_df = pivot_df.resample('D').last().ffill()
    
    # Calculate trend indicators
    if 'dxy' in pivot_df.columns:
        pivot_df['dxy_pct_5d'] = pivot_df['dxy'].pct_change(periods=5)
    if 'dxy_ice' in pivot_df.columns:
        pivot_df['dxy_ice_pct_5d'] = pivot_df['dxy_ice'].pct_change(periods=5)
    if 'hy_spread' in pivot_df.columns:
        pivot_df['hy_spread_pct_5d'] = pivot_df['hy_spread'].pct_change(periods=5)
    if 'rrp' in pivot_df.columns:
        # RRP change in absolute billions over 7 days
        pivot_df['rrp_delta_7d'] = pivot_df['rrp'].diff(periods=7)
    
    # Filter to requested days
    if days > 0:
        cutoff = pivot_df.index.max() - timedelta(days=days)
        pivot_df = pivot_df[pivot_df.index >= cutoff]
        
    return pivot_df.reset_index()


def get_liquidity_valve_data():
    """
    Fetches USD/JPY and Silver price data for the Liquidity Valve visualization.
    Calculates 30-day rolling correlation to quantify the currency-commodity link.
    
    Returns: DataFrame with timestamp, usdjpy, silver_price, and correlation
    """
    conn = get_db_connection()

    # Fetch USD/JPY (stored as JPY Spot)
    jpy_df = pd.read_sql("""
        SELECT timestamp, rate as usdjpy 
        FROM yield_logs 
        WHERE currency='JPY' AND tenor='Spot'
    """, conn)
    
    if jpy_df.empty:
        conn.close()
        return pd.DataFrame()
        
    jpy_df['timestamp'] = pd.to_datetime(jpy_df['timestamp']).dt.date
    # Deduplicate: take last value per date
    jpy_df = jpy_df.groupby('timestamp', as_index=False).last()

    # Fetch Silver Price
    silver_df = pd.read_sql("""
        SELECT l.timestamp, l.contract_price as silver_price 
        FROM margin_logs l 
        JOIN instruments i ON l.instrument_id = i.id 
        WHERE i.symbol='SILVER'
    """, conn)
    conn.close()
    
    if silver_df.empty:
        return pd.DataFrame()
        
    silver_df['timestamp'] = pd.to_datetime(silver_df['timestamp']).dt.date
    # Deduplicate: take last value per date
    silver_df = silver_df.groupby('timestamp', as_index=False).last()

    # Merge using outer join and forward-fill to handle date mismatches
    df = pd.merge(jpy_df, silver_df, on='timestamp', how='outer').sort_values('timestamp')
    df = df.ffill()  # Forward-fill missing values
    df = df.dropna()  # Drop any remaining NaN at the start
    
    if len(df) < 2:
        return pd.DataFrame()

    # Calculate Rolling Correlation (30D) to quantify the "Liquidity Link"
    df['correlation'] = df['usdjpy'].rolling(30, min_periods=5).corr(df['silver_price'])

    return df


def get_usdjpy_vs_asset_data(asset_symbol: str = 'SILVER'):
    """
    Fetches USD/JPY and Asset price data for the Liquidity Valve visualization.
    Calculates 30-day rolling correlation.
    
    Returns: DataFrame with timestamp, usdjpy, price, correlation
    """
    conn = get_db_connection()

    # Fetch USD/JPY (stored as JPY Spot)
    jpy_df = pd.read_sql("""
        SELECT timestamp, rate as usdjpy 
        FROM yield_logs 
        WHERE currency='JPY' AND tenor='Spot'
    """, conn)
    
    if jpy_df.empty:
        conn.close()
        return pd.DataFrame()
        
    jpy_df['timestamp'] = pd.to_datetime(jpy_df['timestamp']).dt.date
    # Deduplicate: take last value per date
    jpy_df = jpy_df.groupby('timestamp', as_index=False).last()

    # Fetch Asset Price
    asset_df = pd.read_sql(f"""
        SELECT l.timestamp, l.contract_price as price 
        FROM margin_logs l 
        JOIN instruments i ON l.instrument_id = i.id 
        WHERE i.symbol='{asset_symbol}'
    """, conn)
    conn.close()
    
    if asset_df.empty:
        return pd.DataFrame()
        
    asset_df['timestamp'] = pd.to_datetime(asset_df['timestamp']).dt.date
    # Deduplicate: take last value per date
    asset_df = asset_df.groupby('timestamp', as_index=False).last()

    # Merge using outer join and forward-fill to handle date mismatches
    df = pd.merge(jpy_df, asset_df, on='timestamp', how='outer').sort_values('timestamp')
    df = df.ffill()  # Forward-fill missing values
    df = df.dropna()  # Drop any remaining NaN at the start
    
    if len(df) < 2:
        return pd.DataFrame()

    # Calculate Rolling Correlation (30D) to quantify the "Liquidity Link"
    df['correlation'] = df['usdjpy'].rolling(30, min_periods=5).corr(df['price'])

    return df




def check_credit_stress():
    """
    Interprets Macro Plumbing signals: Credit Stress, Dollar Strength, and Liquidity (RRP).

    
    Rules:
    - HY_SPREAD > 4.0%: CRITICAL - CREDIT FREEZE (Systemic banking risk)
    - HY_SPREAD rising > 5% in 5 days: WARNING - RISK OFF
    - DXY > 115: DOLLAR WRECKING BALL (Broad Index - Metals headwind)
    - RRP < 50B: CRITICAL - BUFFER DEPLETED (QE imminent - BUY signal for metals)
    - RRP increases > $50B in 1 week: LIQUIDITY HOARDING (Banks pulling cash)
    - RRP drops: LIQUIDITY INJECTION (Cash entering system)
    
    Returns: Dict with flags and messages
    """
    df = get_credit_stress_data(days=30)
    
    if df.empty:
        return {
            'credit_freeze': False,
            'risk_off': False,
            'dollar_squeeze': False,
            'liquidity_hoarding': False,
            'liquidity_injection': False,
            'hy_spread_current': None,
            'dxy_current': None,
            'rrp_current': None,
            'rrp_delta_7d': None,
            'vix_current': None,
            'message': 'No macro data available'
        }
    
    latest = df.iloc[-1]
    
    # Extract current values
    hy_spread = latest.get('hy_spread', None)
    dxy = latest.get('dxy', None)
    rrp = latest.get('rrp', None)
    vix = latest.get('vix', None)
    hy_spread_pct_5d = latest.get('hy_spread_pct_5d', 0) or 0
    dxy_pct_5d = latest.get('dxy_pct_5d', 0) or 0
    rrp_delta_7d = latest.get('rrp_delta_7d', 0) or 0
    
    # Rule 1: Credit Freeze
    credit_freeze = hy_spread is not None and hy_spread > 4.0
    
    # Rule 2: Risk Off (Credit rising >5% in 5 days)
    risk_off = hy_spread_pct_5d > 0.05
    
    # Rule 3: Dollar Wrecking Ball (Broad DXY > 115)
    dollar_squeeze = dxy is not None and dxy > 115
    
    # Rule 4: RRP Buffer Depleted (< $50B = QE imminent)
    buffer_depleted = rrp is not None and rrp < 50
    
    # Rule 5: Liquidity Hoarding (RRP up > $50B in 7 days)
    liquidity_hoarding = rrp_delta_7d > 50
    
    # Rule 6: Liquidity Injection (RRP dropping)
    liquidity_injection = rrp_delta_7d < -10  # significant drop
    
    # Build message
    messages = []
    if credit_freeze:
        messages.append("🚨 CRITICAL: BANKING FREEZE (HY Spread > 4%)")
    if buffer_depleted:
        messages.append("🚨 CRITICAL: BUFFER DEPLETED (QE IMMINENT - BUY METALS)")
    if risk_off:
        messages.append("⚠️ WARNING: RISK OFF (Credit Stress Rising)")
    if dollar_squeeze:
        messages.append("💵 DOLLAR WRECKING BALL (DXY > 115)")
    if liquidity_hoarding:
        messages.append("🏦 LIQUIDITY HOARDING (Banks pulling cash)")
    if liquidity_injection:
        messages.append("💧 LIQUIDITY INJECTION (Cash entering system)")
    
    if not messages:
        # Determine healthy status
        if hy_spread is not None and hy_spread < 3.5:
            messages.append("✅ Macro Plumbing Healthy")
        else:
            messages.append("➡️ Macro Plumbing Stable")
            
    return {
        'credit_freeze': credit_freeze,
        'risk_off': risk_off,
        'dollar_squeeze': dollar_squeeze,
        'buffer_depleted': buffer_depleted,
        'liquidity_hoarding': liquidity_hoarding,
        'liquidity_injection': liquidity_injection,
        'hy_spread_current': hy_spread,
        'dxy_current': dxy,
        'rrp_current': rrp,
        'rrp_delta_7d': rrp_delta_7d,
        'hy_spread_trend': 'Rising' if hy_spread_pct_5d > 0.02 else ('Falling' if hy_spread_pct_5d < -0.02 else 'Stable'),
        'dxy_trend': 'Rising' if dxy_pct_5d > 0 else 'Falling',
        'rrp_trend': 'Rising' if rrp_delta_7d > 10 else ('Falling' if rrp_delta_7d < -10 else 'Stable'),
        'vix_current': vix,
        'message': ' | '.join(messages)
    }

if __name__ == "__main__":
    print("Calculating Squeeze Scores...")
    scores = calculate_squeeze_score()
    print(scores.head() if not scores.empty else "No scores")
    
    print("\nChecking Alerts...")
    try:
        alert_status = check_systemic_alert()
        print(alert_status)
    except Exception as e:
        print(f"Alert check failed: {e}")
