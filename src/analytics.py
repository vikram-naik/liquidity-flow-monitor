
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

DB_PATH = "liquidity_monitor.db"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

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
