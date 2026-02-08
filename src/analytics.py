
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


def calculate_projected_coupons(schedule_df, avg_rates_df, start_date, end_date):
    """
    Estimates future coupon payments based on the maturity schedule and historical rates.
    Logic: Notes/Bonds/TIPS pay semi-annually. We project backwards from maturity.
    """
    if schedule_df.empty or avg_rates_df.empty:
        return pd.DataFrame()

    coupon_bearing = ['Notes', 'Bonds', 'Inflation-Protected Securities']
    mapping = {
        'Notes': 'Treasury Notes',
        'Bonds': 'Treasury Bonds',
        'Inflation-Protected Securities': 'Treasury Inflation-Protected Securities (TIPS)'
    }

    projections = []
    
    # Pre-fetch latest rates as fallback if vintage rate is missing
    latest_avg_rates = avg_rates_df.sort_values('record_date').groupby('security_desc')['avg_interest_rate_amt'].last().to_dict()

    # Filter for coupon-paying instruments
    debt_df = schedule_df.copy()
    if 'security_class' in debt_df.columns:
        debt_df['security_class'] = debt_df['security_class'].astype(str).str.strip()
    
    debt_df = debt_df[debt_df['security_class'].isin(coupon_bearing)].copy()
    debt_df['maturity_date'] = pd.to_datetime(debt_df['maturity_date'])
    debt_df['issue_date'] = pd.to_datetime(debt_df['issue_date'])
    
    # Strip avg_rates_df too
    avg_rates_df = avg_rates_df.copy()
    if 'security_desc' in avg_rates_df.columns:
        avg_rates_df['security_desc'] = avg_rates_df['security_desc'].astype(str).str.strip()

    for _, row in debt_df.iterrows():
        s_class = row['security_class']
        target_desc = mapping[s_class]
        i_month = row['issue_date'].strftime('%Y-%m')
        
        # Find historical rate
        rate_row = avg_rates_df[
            (avg_rates_df['security_desc'] == target_desc) & 
            (avg_rates_df['record_date'].str.startswith(i_month))
        ]
        
        rate = None
        if not rate_row.empty:
            rate = rate_row.iloc[0]['avg_interest_rate_amt']
        else:
            # Fallback to latest known average for that class
            rate = latest_avg_rates.get(target_desc)
            
        if rate is not None:
            # Calculate semi-annual payment dates backwards from maturity
            # We only care about payments in the [start_date, end_date] window
            curr_pay = row['maturity_date']
            while curr_pay >= start_date:
                if curr_pay <= end_date:
                    # Half of annual rate
                    coupon_amt = (row['amount_mil'] * 1e6) * (rate / 100) / 2
                    projections.append({
                        'date': curr_pay,
                        'coupon_amt': coupon_amt
                    })
                # Go back 6 months
                # Simple approximation: 182 days
                curr_pay = curr_pay - pd.Timedelta(days=182)

    if not projections:
        return pd.DataFrame(columns=['date', 'coupon_amt'])
        
    proj_df = pd.DataFrame(projections)
    # Aggregate by date
    return proj_df.groupby('date')['coupon_amt'].sum().reset_index()

def calculate_refinancing_shift(schedule_df, issuance_plan_df, avg_rates_df, latest_yields):
    """
    Calculates the 'Shift' in debt profile for specific event dates.
    Matches Maturing Debt (Out) vs. New Issuance (In), now including Coupons.
    Aggregates by ACTUAL DATE for precise daily view.
    
    Returns DataFrame with `issuance_breakdown` and `maturity_breakdown` columns 
    containing lists of dicts for drill-down display.
    """
    shift_data = []

    # 1. Prepare Dataframes - normalize dates only (no weekly bucketing)
    issuance_plan_df = issuance_plan_df.copy()
    issuance_plan_df['date'] = pd.to_datetime(issuance_plan_df['auction_date']).dt.normalize()
    
    schedule_df = schedule_df.copy()
    schedule_df['date'] = pd.to_datetime(schedule_df['maturity_date']).dt.normalize()

    # 2. Project Coupons
    today = pd.Timestamp.now().normalize()
    start_viz = today - pd.Timedelta(days=60)
    end_viz = today + pd.Timedelta(days=270) # 9 month window match
    coupon_df = calculate_projected_coupons(schedule_df, avg_rates_df, start_viz, end_viz)
    
    if not coupon_df.empty:
        coupon_df['date'] = pd.to_datetime(coupon_df['date']).dt.normalize()

    # 3. Iterate by Unique DATE in UNION of Issuance, Schedule and Coupons
    issuance_dates = set(issuance_plan_df['date'].unique())
    maturity_dates = set(schedule_df['date'].unique())
    coupon_dates = set(coupon_df['date'].unique()) if not coupon_df.empty else set()
    all_dates = sorted(list(issuance_dates.union(maturity_dates).union(coupon_dates)))
    
    today_date = pd.Timestamp.now().normalize()
    
    for event_date in all_dates:
        # A. Aggregate Issuance for this Date + Build Breakdown
        date_issuance = issuance_plan_df[issuance_plan_df['date'] == event_date]
        issuing_amt = 0
        issuing_rate = 0
        term_label = "TBD"
        is_estimate = False
        issuance_breakdown = []
        
        if not date_issuance.empty:
            issuing_amt = date_issuance['offering_amount'].sum()
            # Determine dominant term for rate estimation
            dominant_issue = date_issuance.loc[date_issuance['offering_amount'].idxmax()]
            term = dominant_issue['security_term']
            term_label = term
            
            # Rate determination
            if 'Bill' in term: term_key = 'Bill'
            elif 'Note' in term: term_key = 'Note'
            else: term_key = 'Bond'
            issuing_rate = latest_yields.get(term_key, 4.0)
            
            # Check if this is a future estimate
            if event_date > today_date:
                is_estimate = True
            
            # Build issuance breakdown for drill-down
            for _, iss_row in date_issuance.iterrows():
                iss_term = iss_row['security_term']
                if 'Bill' in iss_term: iss_rate = latest_yields.get('Bill', 4.0)
                elif 'Note' in iss_term: iss_rate = latest_yields.get('Note', 4.0)
                else: iss_rate = latest_yields.get('Bond', 4.0)
                issuance_breakdown.append({
                    'term': iss_term,
                    'amount': iss_row['offering_amount'],
                    'rate': iss_rate
                })
                
        # B. Aggregate Maturities for same Date + Build Breakdown
        date_maturities = schedule_df[schedule_df['date'] == event_date]
        maturing_amt = date_maturities['amount_mil'].sum() * 1_000_000
        maturity_breakdown = []
        
        maturing_rate = 0
        if maturing_amt > 0:
            weighted_rate_sum = 0
            for _, mat_row in date_maturities.iterrows():
                s_class = mat_row['security_class']
                i_date = mat_row['issue_date']
                
                hist_rate = 2.0 
                mapping = {
                    'Notes': 'Treasury Notes', 
                    'Bonds': 'Treasury Bonds',
                    'Inflation-Protected Securities': 'Treasury Inflation-Protected Securities (TIPS)',
                    'Floating Rate Notes': 'Treasury Floating Rate Notes (FRN)',
                    'Bills Maturity Value': 'Treasury Bills'
                }
                if s_class in mapping and i_date:
                    target = mapping[s_class]
                    prefix = str(i_date)[:7]
                    rate_row = avg_rates_df[
                        (avg_rates_df['security_desc'] == target) & 
                        (avg_rates_df['record_date'].str.startswith(prefix))
                    ]
                    if not rate_row.empty:
                        hist_rate = rate_row.iloc[0]['avg_interest_rate_amt']
                
                weighted_rate_sum += (mat_row['amount_mil'] * 1_000_000) * hist_rate
                
                # Build maturity breakdown for drill-down
                maturity_breakdown.append({
                    'type': s_class,
                    'amount': mat_row['amount_mil'] * 1_000_000,
                    'rate': hist_rate,
                    'issue_date': str(i_date)[:10] if i_date else 'N/A'
                })
            
            maturing_rate = weighted_rate_sum / maturing_amt
            
        # C. Aggregate Coupons for this date
        coupon_amt = 0
        if not coupon_df.empty:
            date_coupons = coupon_df[coupon_df['date'] == event_date]
            coupon_amt = date_coupons['coupon_amt'].sum()

        if maturing_amt > 0 or issuing_amt > 0 or coupon_amt > 0:
            shift_data.append({
                'date': event_date,
                'maturing_amt': maturing_amt,
                'maturing_rate': maturing_rate,
                'coupon_amt': float(coupon_amt),
                'issuing_amt': issuing_amt,
                'issuing_rate': issuing_rate,
                'net_principal': float(issuing_amt - maturing_amt - coupon_amt),
                'rate_delta': issuing_rate - maturing_rate if issuing_amt > 0 and maturing_amt > 0 else 0,
                'term_label': term_label,
                'is_estimate': is_estimate,
                'issuance_breakdown': issuance_breakdown,
                'maturity_breakdown': maturity_breakdown
            })
            
    return pd.DataFrame(shift_data)

def calculate_debt_spiral(initial_debt, initial_rate, shift_df):
    """
    Project the Cumulative Debt Spiral based on the daily shifts.
    initial_debt: Total Marketable Debt in Trillions (e.g., 28.0)
    initial_rate: Weighted Avg Rate (e.g., 3.35)
    shift_df: DataFrame from calculate_refinancing_shift
    """
    if shift_df.empty:
        return pd.DataFrame()
    
    spiral_data = []
    
    current_debt = initial_debt * 1e12 # Convert T to Actual
    current_rate = initial_rate
    
    # Sort by date
    shift_df = shift_df.sort_values('date')
    
    for _, row in shift_df.iterrows():
        # 1. Calculate Interest Cost Component before shift
        # Not needed for simple rate/principal projection, but good for context
        
        # 2. Apply The Shift
        # Old Interest Burden = Current_Debt * Current_Rate
        old_burden = current_debt * (current_rate / 100)
        
        # Removing Maturing Debt Burden
        mat_burden = row['maturing_amt'] * (row['maturing_rate'] / 100)
        
        # Adding New Debt Burden
        new_burden = row['issuing_amt'] * (row['issuing_rate'] / 100)
        
        # New Mechanics
        next_debt = current_debt - row['maturing_amt'] + row['issuing_amt']
        
        # New Weighted Rate = Total New Burden / Total New Debt
        next_burden = old_burden - mat_burden + new_burden
        next_rate = (next_burden / next_debt) * 100 if next_debt > 0 else 0
        
        spiral_data.append({
            'date': row['date'],
            'total_debt_trillions': next_debt / 1e12,
            'avg_interest_rate': next_rate,
            'annual_interest_cost_B': next_burden / 1e9
        })
        
        # Update state for next iteration
        current_debt = next_debt
        current_rate = next_rate
        
    return pd.DataFrame(spiral_data)

def get_treasury_fiscal_stress_data():
    """
    Computes US Treasury Fiscal Stress metrics:
    1. Auction Tail (Proxy): Calculated here as 0 for now until reliable WI data is sourced.
    2. Fiscal Stress Indicator: 
        - CRITICAL if bid_to_cover < 2.3 OR cds_spread > 40bps
    """
    conn = get_db_connection()
    
    # Fetch Auctions
    auctions_df = pd.read_sql("""
        SELECT record_date, auction_date, security_type, maturity, bid_to_cover, tail_bps, high_yield, offering_amount, total_accepted,
               primary_dealer_accepted, direct_bidder_accepted, indirect_bidder_accepted, soma_accepted, noncomp_accepted, is_new_issuance
        FROM treasury_auctions
        ORDER BY record_date ASC
    """, conn)
    
    # Fetch Liquidity/Stress data
    liquidity_df = pd.read_sql("""
        SELECT record_date, tga_balance, rrp_balance, cds_spread
        FROM treasury_liquidity
        ORDER BY record_date ASC
    """, conn)
    
    # Fetch Maturity Wall (1yr)
    maturity_df = pd.read_sql("""
        SELECT record_date, maturing_1yr, total_debt
        FROM treasury_debt_profile
        ORDER BY record_date ASC
    """, conn)

    # Fetch Buyback data
    buybacks_df = pd.read_sql("""
        SELECT record_date, total_offered, total_accepted, security_type, maturity_bucket
        FROM treasury_buybacks
        ORDER BY record_date ASC
    """, conn)
    
    # Fetch Daily Flows (DTS Table II) for synthetic wall extension
    flows_df = pd.read_sql("""
        SELECT record_date, transaction_type, SUM(amount_mil) as total_mil
        FROM treasury_daily_debt_flows
        WHERE security_type = 'Bills'
        GROUP BY record_date, transaction_type
    """, conn)
    
    # Fetch Average Interest Rates
    avg_rates_df = pd.read_sql("""
        SELECT record_date, security_desc, avg_interest_rate_amt
        FROM treasury_avg_interest_rates
        ORDER BY record_date ASC
    """, conn)
    
    # Fetch Issuance Plan
    issuance_plan_df = pd.read_sql("""
        SELECT auction_date, security_term, offering_amount, is_new_issuance
        FROM treasury_issuance_plan
        ORDER BY auction_date ASC
    """, conn)

    # Fetch 10Y Yield & RRP for Quadrant 4 / Q1 Fallback
    yield_df = pd.read_sql("""
        SELECT timestamp, tenor, rate 
        FROM yield_logs 
        WHERE currency='USD' AND tenor IN ('10Y', 'RRP')
    """, conn)
    
    # Fetch Future Maturity Schedule
    schedule_df = pd.read_sql("""
        SELECT maturity_date, issue_date, security_class, amount_mil
        FROM treasury_maturity_schedule
        ORDER BY maturity_date ASC
    """, conn)
    
    # Fetch Debt Profile for Total Debt Baseline
    debt_profile_df = pd.read_sql("""
        SELECT record_date, total_debt
        FROM treasury_debt_profile
        ORDER BY record_date DESC
        LIMIT 1
    """, conn)
    
    conn.close()
    
    if auctions_df.empty or liquidity_df.empty:
        return None
        
    # --- Pivot Logic: Refinancing Shift & Debt Spiral ---
    
    # 1. Calculate Refinancing Shift (Event-driven)
    # We need efficient lookup for avg rates
    # Convert avg_rates_df to a more usable lookup if needed, but the loop does it row-by-row for now
    
    latest_yields = auctions_df.sort_values('record_date').groupby('maturity')['high_yield'].last().to_dict()
    # Fallback to key mapping if needed (Bill/Note/Bond)
    # The new function expects 'Bill', 'Note', 'Bond' keys
    # Let's standardize latest_yields keys
    std_yields = {}
    for k, v in latest_yields.items():
        if 'Bill' in k: std_yields['Bill'] = v
        elif 'Note' in k: std_yields['Note'] = v
        elif 'Bond' in k: std_yields['Bond'] = v
    
    refi_shift_df = calculate_refinancing_shift(schedule_df, issuance_plan_df, avg_rates_df, std_yields)
    
    # 2. Calculate Debt Spiral (Cumulative)
    # Get Baselines
    baseline_debt = 36.0 # Fallback Trillions
    if not debt_profile_df.empty:
        baseline_debt = debt_profile_df.iloc[0]['total_debt'] / 1e6 # debt_profile is in Millions usually? 
        # Check agent: total_mil -> yes, Millions.
        # So Divide by 1e6 to get Trillions for the function input? 
        # Function expects Trillions. 
        # Let's check function: "initial_debt: Total Marketable Debt in Trillions (e.g., 28.0)"
        # table value = 36,000,000 (Mil) = 36 Trillion. 
        # So 36,000,000 / 1,000,000 = 36.
    
    # Get Net Weighted Average Rate Baseline
    # We can approximate this from the latest avg_rates_df entry for "Total Marketable"
    baseline_rate = 3.35 # Fallback
    total_rate_row = avg_rates_df[avg_rates_df['security_desc'] == 'Total Marketable'].sort_values('record_date')
    if not total_rate_row.empty:
        baseline_rate = total_rate_row.iloc[-1]['avg_interest_rate_amt']
        
    debt_spiral_df = calculate_debt_spiral(baseline_debt, baseline_rate, refi_shift_df)
    
    if auctions_df.empty or liquidity_df.empty:
        return None
    
    # Calculate Synthetic Maturity Wall (Daily Updates)
    if not maturity_df.empty and not flows_df.empty:
        # Get last official baseline
        last_mspd = maturity_df.iloc[-1]
        baseline_date = last_mspd['record_date']
        baseline_val = last_mspd['maturing_1yr']
        
        # Filter flows for dates after baseline
        recent_flows = flows_df[flows_df['record_date'] > baseline_date].copy()
        
        if not recent_flows.empty:
            # Pivot to get Issues and Redemptions on one line
            recent_flows = recent_flows.pivot(index='record_date', columns='transaction_type', values='total_mil').fillna(0)
            if 'Issues' not in recent_flows: recent_flows['Issues'] = 0
            if 'Redemptions' not in recent_flows: recent_flows['Redemptions'] = 0
            
            # Net Flow = Issues - Redemptions
            recent_flows['net_flow'] = recent_flows['Issues'] - recent_flows['Redemptions']
            recent_flows['cum_net_flow'] = recent_flows['net_flow'].cumsum()
            
            # Create synthetic rows
            synthetic_rows = []
            for date, row in recent_flows.iterrows():
                synthetic_rows.append({
                    'record_date': date,
                    'maturing_1yr': baseline_val + row['cum_net_flow'],
                    'total_debt': last_mspd['total_debt'] # Static proxy for total debt
                })
            
            # Combine with official data
            maturity_df = pd.concat([maturity_df, pd.DataFrame(synthetic_rows)], ignore_index=True)
        
    liquidity_df['record_date'] = pd.to_datetime(liquidity_df['record_date'])
    yield_df['timestamp'] = pd.to_datetime(yield_df['timestamp'])
    
    # Split yields and RRP for cleaner merging
    y10_df = yield_df[yield_df['tenor'] == '10Y'].rename(columns={'rate': 'yield_10y'})
    rrp_fallback_df = yield_df[yield_df['tenor'] == 'RRP'].rename(columns={'rate': 'rrp_fred'})
    
    # Merge Liquidity with Yields
    stress_df = pd.merge_asof(
        liquidity_df.sort_values('record_date'), 
        y10_df.sort_values('timestamp'), 
        left_on='record_date', 
        right_on='timestamp', 
        direction='backward'
    )

    # Merge with RRP Fallback (if treasury_liquidity.rrp_balance is NULL)
    stress_df = pd.merge_asof(
        stress_df,
        rrp_fallback_df.sort_values('timestamp'),
        left_on='record_date',
        right_on='timestamp',
        direction='backward',
        suffixes=('', '_fallback')
    )
    
    # Fill RRP gaps: prioritize rrp_balance (table) then rrp_fred (yield_logs)
    stress_df['rrp_balance'] = stress_df['rrp_balance'].fillna(stress_df['rrp_fred'])
    
    # Alert Trigger Logic
    # Filter for completed auctions (BTC not null)
    completed_auctions = auctions_df.dropna(subset=['bid_to_cover'])
    latest_liquidity = liquidity_df.iloc[-1]
    
    if not completed_auctions.empty:
        latest_auction = completed_auctions.iloc[-1]
        # Get latest BTC for 10Y específicamente, fallback to latest overall
        ten_year_auctions = completed_auctions[completed_auctions['security_type'].str.contains('10-Year', na=False)]
        btc_10y = ten_year_auctions['bid_to_cover'].iloc[-1] if not ten_year_auctions.empty else latest_auction['bid_to_cover']
    else:
        btc_10y = None

    cds_spread = latest_liquidity['cds_spread']
    
    # Buyback Acceptance Ratio Logic
    latest_buyback_ratio = None
    if not buybacks_df.empty:
        # Group by date to get aggregate ratio if multiple operations on same day
        daily_buybacks = buybacks_df.groupby('record_date').agg({'total_offered': 'sum', 'total_accepted': 'sum'}).reset_index()
        latest_bb = daily_buybacks.iloc[-1]
        if latest_bb['total_offered'] > 0:
            latest_buyback_ratio = latest_bb['total_accepted'] / latest_bb['total_offered']

    stress_level_num = 0
    stress_msg = "Fiscal Conditions Stable"
    
    if (btc_10y is not None and btc_10y < 2.3) or (cds_spread is not None and cds_spread > 40) or (latest_buyback_ratio is not None and latest_buyback_ratio < 0.4):
        stress_level_num = 2
        stress_msg = "CRITICAL FISCAL STRESS DETECTED"
    elif (btc_10y is not None and btc_10y < 2.4) or (cds_spread is not None and cds_spread > 35) or (latest_buyback_ratio is not None and latest_buyback_ratio < 0.5):
        stress_level_num = 1
        stress_msg = "Elevated Fiscal Risk"
        
    # --- Interest Rate Surcharge (Debt Recycling) ---
    surcharge_df = pd.DataFrame()
    if not schedule_df.empty and not avg_rates_df.empty and not auctions_df.empty:
        mapping = {
            'Bills Maturity Value': 'Treasury Bills',
            'Notes': 'Treasury Notes',
            'Bonds': 'Treasury Bonds',
            'Floating Rate Notes': 'Treasury Floating Rate Notes (FRN)',
            'Inflation-Protected Securities': 'Treasury Inflation-Protected Securities (TIPS)'
        }
        
        # Get latest yields per broad class (Bill/Note/Bond)
        # Note: mapping maturity types to auction types
        latest_yields = auctions_df.sort_values('record_date').groupby('maturity')['high_yield'].last().to_dict()
        
        surcharge_rows = []
        for idx, row in schedule_df.iterrows():
            s_class = row['security_class']
            i_date = row['issue_date']
            
            if s_class in mapping and i_date:
                target_desc = mapping[s_class]
                i_month_prefix = str(i_date)[:7] # YYYY-MM
                
                # Find historical average rate for that issue month
                hist_rate_row = avg_rates_df[
                    (avg_rates_df['security_desc'] == target_desc) & 
                    (avg_rates_df['record_date'].str.startswith(i_month_prefix))
                ]
                
                if not hist_rate_row.empty:
                    hist_rate = hist_rate_row.iloc[0]['avg_interest_rate_amt']
                    # Map to auction category
                    auc_cat = 'Bill' if 'Bill' in s_class else ('Note' if 'Note' in s_class else 'Bond')
                    new_rate = latest_yields.get(auc_cat)
                    
                    if hist_rate is not None and new_rate is not None:
                        delta = (new_rate - hist_rate) * 100
                        amount = row['amount_mil']
                        annual_impact = amount * (delta / 10000)
                        surcharge_rows.append({
                            'maturity_date': row['maturity_date'],
                            'security_class': s_class,
                            'amount_mil': amount,
                            'historical_rate': hist_rate,
                            'new_rate': new_rate,
                            'delta_bps': delta,
                            'annual_impact_mil': annual_impact
                        })
        
        surcharge_df = pd.DataFrame(surcharge_rows)
        
        # Trigger Refinancing Shock Alert
        if not surcharge_df.empty and surcharge_df['delta_bps'].max() > 200:
            stress_level_num = max(stress_level_num, 2)
            stress_msg = "REFINANCING SHOCK: Interest rate delta exceeds 200bps on upcoming rolled debt."

    stress_level_map = {0: "GREEN", 1: "YELLOW", 2: "RED"}
    stress_level = stress_level_map.get(stress_level_num, "GREEN")

    return {
        'auctions': auctions_df,
        'liquidity': stress_df,
        'maturity': maturity_df,
        'schedule': schedule_df,
        'redemptions': flows_df,
        'buybacks': buybacks_df,
        'surcharge': surcharge_df,
        'issuance_plan': issuance_plan_df,
        'refi_shift': refi_shift_df,
        'debt_spiral': debt_spiral_df,
        'stress_level': stress_level,
        'stress_message': stress_msg,
        'latest_btc': btc_10y,
        'latest_cds': cds_spread,
        'buyback_ratio': latest_buyback_ratio
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
