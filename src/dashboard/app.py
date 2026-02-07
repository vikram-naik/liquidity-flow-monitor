import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys
import os
import time
from datetime import timedelta, datetime
import pytz
from streamlit_autorefresh import st_autorefresh

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.analytics import (
    calculate_squeeze_score as _calculate_squeeze_score, 
    check_systemic_alert as _check_systemic_alert, 
    get_db_connection, 
    get_carry_trade_data as _get_carry_trade_data, 
    get_credit_stress_data as _get_credit_stress_data, 
    check_credit_stress as _check_credit_stress, 
    calculate_fair_value, 
    get_liquidity_valve_data as _get_liquidity_valve_data, 
    get_usdjpy_vs_asset_data as _get_usdjpy_vs_asset_data,
    get_treasury_fiscal_stress_data as _get_treasury_fiscal_stress_data
)

# --- CACHED ANALYTICS WRAPPERS (15 min TTL) ---
@st.cache_data(ttl=900)
def get_carry_trade_data(): return _get_carry_trade_data()

@st.cache_data(ttl=900)
def calculate_squeeze_score(): return _calculate_squeeze_score()

@st.cache_data(ttl=900)
def get_credit_stress_data(days=90): return _get_credit_stress_data(days)

@st.cache_data(ttl=900)
def check_credit_stress(): return _check_credit_stress()

@st.cache_data(ttl=900)
def check_systemic_alert(): return _check_systemic_alert()

@st.cache_data(ttl=900)
def get_liquidity_valve_data(): return _get_liquidity_valve_data()

@st.cache_data(ttl=900)
def get_usdjpy_vs_asset_data(asset='SILVER'): return _get_usdjpy_vs_asset_data(asset)

@st.cache_data(ttl=900)
def get_treasury_fiscal_stress_data(): return _get_treasury_fiscal_stress_data()

from src.agents.silver_agent import fetch_nse_price as _fetch_nse_price, fetch_nippon_inav as _fetch_nippon_inav
from src.database import init_db
from src.utils.market_hours import is_nse_market_open as _is_nse_market_open

# --- SILVERBEES MONITOR HELPERS (wrappers for UI feedback) ---


@st.cache_data(ttl=60)
def get_silver_live_data():
    """Cached fetch for Silver metrics to avoid Rapid Refresh UI flashing."""
    from src.agents.silver_agent import fetch_global_parity_metrics as _fetch_silver_parity
    from src.agents.silver_agent import fetch_nse_price as _fetch_nse_silver
    from src.agents.silver_agent import fetch_nippon_inav as _fetch_nippon_silver_inav
    
    with st.spinner("Fetching Silver data..."):
        price_data = _fetch_nse_silver("SILVERBEES")
        price = price_data['price'] if price_data else None
        inav_data = _fetch_nippon_silver_inav("Nippon India Silver ETF")
        inav, inav_time = (inav_data['inav'], inav_data['updated_at']) if inav_data else (None, None)
        parity_data = _fetch_silver_parity()
    
    if price and inav:
        # Save to DB
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO silver_intraday_log 
                (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
                VALUES (?, ?, ?, ?, ?)
            """, (
                now_ist.strftime('%Y-%m-%d %H:%M:%S'), 
                price, inav, 
                parity_data['spot_usd'] if parity_data else None, 
                parity_data['usdinr'] if parity_data else None
            ))
            conn.commit()
        except: pass
        finally: conn.close()
            
    return {'price': price, 'inav': inav, 'inav_time': inav_time, 'parity_data': parity_data}

@st.cache_data(ttl=60)
def get_gold_live_data():
    """Cached fetch for Gold metrics to avoid Rapid Refresh UI flashing."""
    from src.agents.gold_agent import fetch_global_parity_metrics as _fetch_gold_parity
    from src.agents.gold_agent import fetch_nse_price as _fetch_nse_gold
    from src.agents.gold_agent import fetch_nippon_inav as _fetch_nippon_gold_inav
    
    with st.spinner("Fetching Gold data..."):
        price_data = _fetch_nse_gold("GOLDBEES")
        price = price_data['price'] if price_data else None
        inav_data = _fetch_nippon_gold_inav("Nippon India ETF Gold BeES")
        inav, inav_time = (inav_data['inav'], inav_data['updated_at']) if inav_data else (None, None)
        parity_data = _fetch_gold_parity()
    
    if price and inav:
        # Save to DB
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO gold_intraday_log 
                (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
                VALUES (?, ?, ?, ?, ?)
            """, (
                now_ist.strftime('%Y-%m-%d %H:%M:%S'), 
                price, inav, 
                parity_data['spot_usd'] if parity_data else None, 
                parity_data['usdinr'] if parity_data else None
            ))
            conn.commit()
        except: pass
        finally: conn.close()
            
    return {'price': price, 'inav': inav, 'inav_time': inav_time, 'parity_data': parity_data}


@st.cache_data(ttl=3600)
def get_dashboard_holidays():
    """Fetch list of holiday dates to exclude from charts"""
    conn = get_db_connection()
    try:
        # Get purely the dates
        holidays = pd.read_sql("SELECT holiday_date FROM trading_holidays", conn)
        return holidays['holiday_date'].tolist() if not holidays.empty else []
    except:
        return []
    finally:
        conn.close()

# --- LAYMAN TRANSLATION ENGINE (THE GENERAL'S VIEW) ---
def generate_sitrep(credit_status, valve_df, alert_status):
    """
    Translates complex metrics into layman English.
    Returns: (Status Color, Title, Narrative List, Action Command)
    """
    narrative = []
    
    # 1. Check The Fuel (Liquidity)
    rrp_val = credit_status.get('rrp_current')
    fuel_status = "NORMAL"
    if rrp_val is not None:
        if rrp_val < 50:
            narrative.append("🪫 **Fuel Tank Empty:** Banks are out of cash (RRP < $50B). The system is running on fumes.")
            fuel_status = "CRITICAL"
        elif credit_status.get('liquidity_injection'):
            narrative.append("⛽ **Refueling:** The Fed is pumping cash back into the system.")
            fuel_status = "STABLE"
        else:
            narrative.append("📉 **Fuel Leaking:** Cash is leaving the system, but the tank isn't empty yet.")

    # 2. Check The Valve (Correlation)
    valve_status = "NORMAL"
    if not valve_df.empty and 'correlation' in valve_df.columns:
        corr = valve_df['correlation'].iloc[-1] if not pd.isna(valve_df['correlation'].iloc[-1]) else 0
        if corr < 0 and corr > -0.5:
            narrative.append("🔧 **Valve Broken:** Silver prices are disconnected from reality. You are seeing 'Forced Selling', not real value.")
            valve_status = "BROKEN"
        elif corr < -0.5:
            narrative.append("🛡️ **Safe Haven Mode:** Silver has bottomed and is acting as a shield against the Dollar.")
            valve_status = "SAFE_HAVEN"
        elif corr > 0.5:
            narrative.append("💧 **Valve Open:** Money is flowing normally into Silver.")
    
    # 3. Check The Stress (Margins)
    stress_status = "NORMAL"
    if alert_status['alert']:
        narrative.append(f"⚠️ **Margin Call:** Big players are being forced to sell {', '.join(alert_status['hikes'])} to pay debts.")
        stress_status = "HIGH"

    # --- SYNTHESIZE ORDERS ---
    if fuel_status == "CRITICAL" or valve_status == "BROKEN":
        color = "error" # Red
        title = "🚨 DEFCON 1: LIQUIDITY TRAP"
        action = "🛑 STAND DOWN. Do not buy yet. Wait for the Fed to print money."
    elif valve_status == "SAFE_HAVEN":
        color = "success" # Green
        title = "✅ DEFCON 4: PHYSICAL ACCUMULATION"
        action = "🛒 BUY PHYSICAL. The bottom is in."
    elif stress_status == "HIGH":
        color = "warning" # Orange
        title = "⚠️ DEFCON 3: SHAKEOUT IN PROGRESS"
        action = "✋ HOLD FIRE. Let the forced selling finish."
    else:
        color = "success" # Green
        title = "✅ DEFCON 5: ALL CLEAR"
        action = "🎯 ENGAGE. Markets are functioning normally."
        
    return color, title, narrative, action

# ---------------------------------

# --- REFRESH UTILITIES ---
def get_refresh_countdown(interval_seconds, key_prefix):
    """
    Manages a countdown timer and returns True when it's time to refresh data.
    Displays the countdown in the sidebar.
    """
    import time
    now = time.time()
    last_refresh_key = f"{key_prefix}_last_refresh"
    
    if last_refresh_key not in st.session_state:
        st.session_state[last_refresh_key] = now
    
    elapsed = now - st.session_state[last_refresh_key]
    remaining = max(0, interval_seconds - elapsed)
    
    # Display countdown in sidebar
    mins, secs = divmod(int(remaining), 60)
    timer_text = f"{mins:02d}:{secs:02d}"
    
    with st.sidebar:
        st.divider()
        st.subheader("⏲️ Refresh Status")
        st.metric("Next Logic Sync", timer_text)
        if remaining <= 0:
            st.caption("🔄 Refreshing now...")
            st.session_state[last_refresh_key] = now
            # Clear cache for the relevant scope
            st.cache_data.clear() 
            return True
    return False

# --- SIDEBAR NAVIGATION ---
if st.runtime.exists():
    st.set_page_config(page_title="Global Liquidity Flow Monitor", layout="wide")
    st.markdown("""
        <style>
               .block-container {
                    padding-top: 1rem;
                    padding-bottom: 0rem;
                }
        </style>
        """, unsafe_allow_html=True)

    # Use a small background ticker to update the countdown text without heavy reload
    # 5 seconds is a good balance between 'ticking' feel and performance
    st_autorefresh(interval=5000, key="global_timer_tick")

    st.sidebar.header("🧭 Navigation")
    dashboard_mode = st.sidebar.radio(
        "Select View:",
        ["Main Dashboard", "US Treasury Monitor", "Precious Metal ETF Monitor"],
        index=0
    )
    
    # Global Refresh Logic (Unified)
    # We use the countdown to decide when to 'heavy' reload data
    if dashboard_mode == "Main Dashboard":
        needs_refresh = get_refresh_countdown(900, "main") # 15 mins
    else:
        needs_refresh = get_refresh_countdown(60, "monitor") # 1 min
else:
    # Not running with Streamlit (e.g., bootloader mode)
    dashboard_mode = None


# --- SILVER MONITOR VIEW ---
def is_market_open():
    """Wrapper for shared market hours logic."""
    return _is_nse_market_open()

def render_silver_monitor():
    """Renders the SILVERBEES Tracking Error Monitor with auto-refresh and DB persistence."""
    from src.agents.silver_agent import fetch_global_parity_metrics as _fetch_global_parity_metrics
    
    st.title("🥈 SILVERBEES Live Tracking Error Monitor")
    
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    today_date_str = now_ist.strftime('%Y-%m-%d')
    
    # Check market hours
    market_open, market_status = is_market_open()
    
    # --- DB Helper Functions ---
    def load_history_from_db():
        """Smart history loading: Today's data or fallback to last 100."""
        conn = get_db_connection()
        # Try to get today's data first
        today_df = pd.read_sql(f"""
            SELECT * FROM silver_intraday_log 
            WHERE DATE(timestamp) = '{today_date_str}' 
            ORDER BY timestamp ASC
        """, conn)
        
        if not today_df.empty:
            conn.close()
            return today_df, True  # is_live=True
        
        # Fallback: last 100 records
        fallback_df = pd.read_sql("""
            SELECT * FROM silver_intraday_log 
            ORDER BY timestamp DESC LIMIT 100
        """, conn)
        conn.close()
        return fallback_df.iloc[::-1] if not fallback_df.empty else pd.DataFrame(), False
    
    def save_tick_to_db(ts, price_nse, inav_nippon, spot_usd, usdinr):
        """Persists a single tick to DB. Ignores duplicates."""
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO silver_intraday_log 
                (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
                VALUES (?, ?, ?, ?, ?)
            """, (ts.strftime('%Y-%m-%d %H:%M:%S'), price_nse, inav_nippon, spot_usd, usdinr))
            conn.commit()
        except Exception as e:
            print(f"[silver_monitor] DB write error: {e}")
        finally:
            conn.close()
    
    # --- Data Handling ---
    price, inav, inav_time, spread, spread_pct = None, None, None, None, None
    parity_data = None
    
    history_df, is_live_data = load_history_from_db()
    
    if market_open:
        # Use cached fetcher to avoid Rapid Refresh UI flashing
        live_data = get_silver_live_data()
        price = live_data['price']
        inav = live_data['inav']
        inav_time = live_data['inav_time']
        parity_data = live_data['parity_data']
        
        if price and inav:
            spread = inav - price
            spread_pct = (spread / price) * 100
    else:
        # Market closed - use last row from history for metrics
        if not history_df.empty:
            last_row = history_df.iloc[-1]
            price = last_row['price_nse']
            inav = last_row['inav_nippon']
            if price and inav:
                spread = inav - price
                spread_pct = (spread / price) * 100
            inav_time = f"Last captured: {pd.to_datetime(last_row['timestamp']).strftime('%I:%M %p')}"
            if last_row.get('spot_usd') and last_row.get('usdinr'):
                parity_data = {
                    'spot_usd': last_row['spot_usd'],
                    'usdinr': last_row['usdinr'],
                    'parity_inr': (last_row['spot_usd'] * last_row['usdinr']) / 31.1035
                }
    
    # Calculate Parity Spread
    parity_spread = None
    parity_spread_pct = None
    if price and parity_data:
        p_inr = parity_data.get('parity_inr') or (parity_data['spot_usd'] * parity_data['usdinr']) / 31.1035
        parity_spread = p_inr - price
        parity_spread_pct = (parity_spread / price) * 100

    # --- Data Status Banner (Updated with Timestamp) ---
    status_suffix = f" | {inav_time}" if inav_time else ""
    if is_live_data:
        st.success(f"🟢 **Live Intraday Data** | {market_status} | Auto-refresh Active{status_suffix}")
    else:
        if not history_df.empty:
            last_date = pd.to_datetime(history_df['timestamp'].iloc[-1]).strftime('%d %b %Y')
            st.warning(f"⚠️ **Historical Data (Market Closed)** | Showing data from: **{last_date}**{status_suffix}")
        else:
            st.info("📊 No historical data available. Live tracking will begin when market opens.")

    # --- Unified Metrics Row (7 columns) ---
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    with m1:
        st.metric("🏷️ LTP (NSE)", f"₹{price:.2f}" if price else "--")
    with m2:
        st.metric("📊 iNAV", f"₹{inav:.4f}" if inav else "--")
    with m3:
        if spread is not None:
            delta_color = "normal" if spread >= 0 else "inverse"
            s_label = " (discount)" if spread > 0 else " (premium)" if spread < 0 else ""
            st.metric("📏 Spread", f"₹{spread:.4f}", delta=f"{spread_pct:+.2f}%{s_label}", delta_color=delta_color)
        else:
            st.metric("📏 Spread", "--")

    with m7:
        if parity_spread is not None:
            p_delta_color = "normal" if parity_spread >= 0 else "inverse"
            p_label = " (discount)" if parity_spread > 0 else " (premium)" if parity_spread < 0 else ""
            st.metric("🎯 Parity Spread", f"₹{parity_spread:.4f}", delta=f"{parity_spread_pct:+.2f}%{p_label}", delta_color=p_delta_color)
        else:
            st.metric("🎯 Parity Spread", "--")
            
    if parity_data:
        with m4:
            st.metric("🥈 Spot ($)", f"${parity_data['spot_usd']:.2f}")
        with m5:
            st.metric("💵 USD/INR", f"₹{parity_data['usdinr']:.2f}")
        with m6:
            parity_inr = parity_data.get('parity_inr') or (parity_data['spot_usd'] * parity_data['usdinr']) / 31.1035
            st.metric("🌍 Parity (₹)", f"₹{parity_inr:.4f}")
    
    st.divider()

    # --- Charts ---
    if not history_df.empty and len(history_df) > 1:
        chart_df = history_df.copy()
        chart_df['Timestamp'] = pd.to_datetime(chart_df['timestamp'])
        chart_df['Price'] = chart_df['price_nse']
        chart_df['iNAV'] = chart_df['inav_nippon']
        chart_df['Spread'] = chart_df['iNAV'] - chart_df['Price']
        chart_df['Parity_INR'] = (chart_df['spot_usd'] * chart_df['usdinr']) / 31.1035
        chart_df['Parity_Spread'] = chart_df['Parity_INR'] - chart_df['Price']
        
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Price vs iNAV vs Parity")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Price'], name='LTP (NSE)', line=dict(color='#636EFA', width=2)))
            fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['iNAV'], name='iNAV (Nippon)', line=dict(color='#00CC96', width=2, dash='dash')))
            if chart_df['Parity_INR'].notna().any():
                fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Parity_INR'], name='Global Parity', line=dict(color='#FFA500', width=2, dash='dot')))
            
            fig1.update_layout(yaxis=dict(title="Price (₹)"), legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"), margin=dict(t=40, l=0, r=0, b=0), hovermode="x unified", height=350)
            st.plotly_chart(fig1, width="stretch", key="silver_price_chart")
            
        with c2:
            st.subheader("Tracking Error (Buyer Margin)")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Spread'], name='iNAV Margin (₹)', line=dict(color='#00CC96', width=2), fill='tozeroy', fillcolor='rgba(0, 204, 150, 0.2)'))
            fig2.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Parity_Spread'], name='Parity Margin (₹)', line=dict(color='#FFA500', width=2, dash='dot')))
            fig2.add_hline(y=0, line_dash="solid", line_color="gray")
            fig2.update_layout(yaxis=dict(title="Margin (₹)", zeroline=True), margin=dict(t=40, l=0, r=0, b=0), hovermode="x unified", height=350)
            st.plotly_chart(fig2, width="stretch", key="silver_spread_chart")
    elif market_open:
        st.info("Collecting data... Chart will appear after 2+ data points.")

def render_gold_monitor():
    """Renders the GOLDBEES Tracking Error Monitor with auto-refresh and DB persistence."""
    from src.agents.gold_agent import fetch_global_parity_metrics as _fetch_gold_parity
    from src.agents.gold_agent import fetch_nse_price as _fetch_nse_gold
    from src.agents.gold_agent import fetch_nippon_inav as _fetch_nippon_gold_inav

    # Local wrappers for UI feedback
    def fetch_gold_price(symbol="GOLDBEES"):
        data = _fetch_nse_gold(symbol)
        if data: return data['price']
        st.toast("Gold Price Fetch Error", icon="⚠️")
        return None

    def fetch_gold_inav(scheme="Nippon India ETF Gold BeES"):
        data = _fetch_nippon_gold_inav(scheme)
        if data: return data['inav'], data['updated_at']
        st.toast("Gold iNAV Fetch Error", icon="⚠️")
        return None, None

    st.title("🥇 GOLDBEES Live Tracking Error Monitor")
    
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    today_date_str = now_ist.strftime('%Y-%m-%d')
    
    
    market_open, market_status = is_market_open()
    
    # --- DB Helper Functions ---
    def load_history_from_db():
        conn = get_db_connection()
        today_df = pd.read_sql(f"SELECT * FROM gold_intraday_log WHERE DATE(timestamp) = '{today_date_str}' ORDER BY timestamp ASC", conn)
        
        if not today_df.empty:
            conn.close()
            return today_df, True
        
        fallback_df = pd.read_sql("SELECT * FROM gold_intraday_log ORDER BY timestamp DESC LIMIT 100", conn)
        conn.close()
        return fallback_df.iloc[::-1] if not fallback_df.empty else pd.DataFrame(), False
    
    def save_tick_to_db(ts, price, inav, spot, usdinr):
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO gold_intraday_log 
                (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
                VALUES (?, ?, ?, ?, ?)
            """, (ts.strftime('%Y-%m-%d %H:%M:%S'), price, inav, spot, usdinr))
            conn.commit()
        except Exception as e:
            print(f"[gold_monitor] DB write error: {e}")
        finally:
            conn.close()

    # --- Data Handling ---
    price, inav, inav_time, spread, spread_pct = None, None, None, None, None
    parity_data = None
    
    history_df, is_live_data = load_history_from_db()
    
    if market_open:
        # Use cached fetcher to avoid Rapid Refresh UI flashing
        live_data = get_gold_live_data()
        price = live_data['price']
        inav = live_data['inav']
        inav_time = live_data['inav_time']
        parity_data = live_data['parity_data']
        
        if price and inav:
            spread = inav - price
            spread_pct = (spread / price) * 100
    else:
        if not history_df.empty:
            last_row = history_df.iloc[-1]
            price = last_row['price_nse']
            inav = last_row['inav_nippon']
            if price and inav:
                spread = inav - price
                spread_pct = (spread / price) * 100
            inav_time = f"Last captured: {pd.to_datetime(last_row['timestamp']).strftime('%I:%M %p')}"
            if last_row.get('spot_usd') and last_row.get('usdinr'):
                spot = last_row['spot_usd']
                uid = last_row['usdinr']
                p_inr = (spot * uid / 31.1035) * 0.01
                parity_data = {'spot_usd': spot, 'usdinr': uid, 'parity_inr': p_inr}
    
    # Calculate Parity Spread
    parity_spread = None
    parity_spread_pct = None
    if price and parity_data:
        p_inr = parity_data.get('parity_inr') or (parity_data['spot_usd'] * parity_data['usdinr'] / 31.1035) * 0.01
        parity_spread = p_inr - price
        parity_spread_pct = (parity_spread / price) * 100

    # --- Status Banner ---
    status_suffix = f" | {inav_time}" if inav_time else ""
    if is_live_data:
        st.success(f"🟢 **Live Intraday Data** | {market_status} | Auto-refresh{status_suffix}")
    else:
        if not history_df.empty:
            last_date = pd.to_datetime(history_df['timestamp'].iloc[-1]).strftime('%d %b %Y')
            st.warning(f"⚠️ **Historical Data (Market Closed)** | Showing data from: **{last_date}**{status_suffix}")
        else:
            st.info("📊 No historical data available.")

    # --- Unified Metrics Row (7 columns) ---
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    with m1: st.metric("🏷️ LTP (NSE)", f"₹{price:.2f}" if price else "--")
    with m2: st.metric("📊 iNAV", f"₹{inav:.2f}" if inav else "--")
    with m3:
        if spread is not None:
            delta_color = "normal" if spread >= 0 else "inverse"
            s_label = " (discount)" if spread > 0 else " (premium)" if spread < 0 else ""
            st.metric("📏 Spread", f"₹{spread:.2f}", delta=f"{spread_pct:+.2f}%{s_label}", delta_color=delta_color)
        else: st.metric("📏 Spread", "--")

    with m7:
        if parity_spread is not None:
            p_delta_color = "normal" if parity_spread >= 0 else "inverse"
            p_label = " (discount)" if parity_spread > 0 else " (premium)" if parity_spread < 0 else ""
            st.metric("🎯 Parity Spread", f"₹{parity_spread:.4f}", delta=f"{parity_spread_pct:+.2f}%{p_label}", delta_color=p_delta_color)
        else:
            st.metric("🎯 Parity Spread", "--")
    
    if parity_data:
        with m4: st.metric("🥇 Spot ($)", f"${parity_data['spot_usd']:.2f}")
        with m5: st.metric("💵 USD/INR", f"₹{parity_data['usdinr']:.2f}")
        with m6:
            p_val = parity_data.get('parity_inr')
            st.metric("🌍 Parity (Unit)", f"₹{p_val:.2f}" if p_val else "--")

    st.divider()

    # --- Charts ---
    if not history_df.empty and len(history_df) > 1:
        chart_df = history_df.copy()
        chart_df['Timestamp'] = pd.to_datetime(chart_df['timestamp'])
        chart_df['Price'] = chart_df['price_nse']
        chart_df['iNAV'] = chart_df['inav_nippon']
        chart_df['Spread'] = chart_df['iNAV'] - chart_df['Price']
        chart_df['Parity_INR'] = (chart_df['spot_usd'] * chart_df['usdinr'] / 31.1035) * 0.01
        chart_df['Parity_Spread'] = chart_df['Parity_INR'] - chart_df['Price']
        
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Price vs iNAV vs Parity")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Price'], name='LTP (NSE)', line=dict(color='#FFD700', width=2))) # Gold color
            fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['iNAV'], name='iNAV', line=dict(color='#00CC96', width=2, dash='dash')))
            if chart_df['Parity_INR'].notna().any():
                fig1.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Parity_INR'], name='Global Parity', line=dict(color='#FFA500', width=2, dash='dot')))
            
            fig1.update_layout(yaxis=dict(title="Price (₹)"), legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"), margin=dict(t=40, l=0, r=0, b=0), hovermode="x unified", height=350)
            st.plotly_chart(fig1, width="stretch", key="gold_price_chart")
            
        with c2:
            st.subheader("Tracking Error (Buyer Margin)")
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Spread'], name='iNAV Margin (₹)', line=dict(color='#00CC96', width=2), fill='tozeroy', fillcolor='rgba(0, 204, 150, 0.2)'))
            fig2.add_trace(go.Scatter(x=chart_df['Timestamp'], y=chart_df['Parity_Spread'], name='Parity Margin (₹)', line=dict(color='#FFA500', width=2, dash='dot')))
            fig2.add_hline(y=0, line_dash="solid", line_color="gray")
            fig2.update_layout(yaxis=dict(title="Margin (₹)", zeroline=True), margin=dict(t=40, l=0, r=0, b=0), hovermode="x unified", height=350)
            st.plotly_chart(fig2, width="stretch", key="gold_spread_chart")
    elif market_open:
        st.info("Collecting data... Chart will appear after 2+ data points.")
    # When market is closed and no chart data, just don't show anything extra
def render_treasury_monitor():
    """Renders the 4-Quadrant US Treasury Fiscal Stress Monitor."""
    st.title("🏛️ US Treasury Fiscal Stress Monitor")
    
    data = get_treasury_fiscal_stress_data()
    if not data:
        st.warning("No Treasury data available. Please run the collector.")
        return
        
    auctions_df = data['auctions']
    stress_df = data['liquidity']
    stress_level = data['stress_level']
    stress_msg = data['stress_message']
    latest_btc = data['latest_btc']
    latest_cds = data['latest_cds']
    buyback_ratio = data['buyback_ratio']
    rrp_val = stress_df['rrp_balance'].iloc[-1] if not stress_df.empty else None
    
    # --- Consolidated Systemic Risk Header ---
    st.subheader("⚠️ Systemic Risk Monitor")
    
    # Collect all alerts
    alerts = []
    if latest_btc is not None and latest_btc < 2.3:
        alerts.append(f"📉 **Low Auction Demand:** Bid-to-Cover is {latest_btc:.2f} (Target > 2.3)")
    if latest_cds is not None and latest_cds > 40:
        alerts.append(f"🛡️ **Credit Stress:** US 5Y CDS is {latest_cds:.1f}bps (Threshold > 40)")
    if rrp_val is not None and rrp_val < 50:
        alerts.append(f"🪫 **Liquidity Drain:** Fed RRP is ${rrp_val:.0f}B (Critical < $50B)")
    if buyback_ratio is not None and buyback_ratio < 0.5:
        alerts.append(f"🔄 **Weak Buyback Support:** Acceptance Ratio is {buyback_ratio:.1%} (Target > 50%)")
        
    if alerts:
        with st.container():
            st.error("### 🚨 SYSTEMIC ALERTS DETECTED")
            for alert in alerts:
                st.write(alert)
    else:
        st.success("### ✅ Systemic Risk: Low")

    st.divider()

    # --- Pressure Gauge (Metrics Row) ---
    st.subheader("🚥 Pressure Gauge")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        st.metric("BTC (10Y/Proxy)", f"{latest_btc:.2f}" if latest_btc else "--", 
                  delta="-0.1" if latest_btc and latest_btc < 2.3 else None, delta_color="inverse")
    with g2:
        st.metric("CDS (Risk Premium)", f"{latest_cds:.1f} bps" if latest_cds else "--",
                  delta="+5" if latest_cds and latest_cds > 35 else None, delta_color="inverse")
    with g3:
        st.metric("RRP (Liquidity)", f"${rrp_val:.0f}B" if rrp_val else "--",
                  delta="-20B" if rrp_val and rrp_val < 100 else None, delta_color="inverse")
    with g4:
        st.metric("Buyback Ratio", f"{buyback_ratio:.1%}" if buyback_ratio else "--",
                  delta="-5%" if buyback_ratio and buyback_ratio < 0.5 else None, delta_color="inverse")

    st.divider()
    
    # Quadrants - One per row as requested
    
    st.subheader("Quadrant 1: Liquidity Vacuum")
    st.caption("TGA (Operating Cash) vs. Fed RRP Balances")
    with st.expander("💡 How to read this?"):
        st.markdown("""
        **What it means:** This tracks the two main 'buckets' of cash at the Federal Reserve.
        - **TGA (Blue) - The Treasury's Wallet:** This is the US Government's checking account. When the TGA rises, money is being pulled *out* of the economy (via taxes or debt sales). When it falls, the government is spending money *into* the economy.
        - **RRP (Red) - The Liquidity Buffer:** This is where **Money Market Funds** park excess cash. Think of it as a 'spare tank' of liquidity.
        
        ---
        ### 🧐 FAQ: Why is the RRP 'empty'? 
        **Q: From where does the RRP money come?**
        Mainly from **Money Market Funds (MMFs)**. When MMFs have too much cash and nowhere safe to put it, they lend it to the Fed (Reverse Repo) to earn interest.
        
        **Q: What happens when it drains?**
        For the last year, the Treasury has issued trillions in 'Bills'. MMFs took their money out of the RRP to buy these Bills. This was great for the market because the government was funded by 'idle cash'. 
        
        **Q: The Stress Signal:**
        Now that the RRP is nearly empty, the government can no longer rely on this 'spare tank'. New debt must now be bought using **Bank Reserves**. If both TGA is rising and RRP is empty, it's a 'Liquidity Vacuum'—the market's safety net is gone.
        """)
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(x=stress_df['record_date'], y=stress_df['tga_balance'], name='TGA Balance ($B)', line=dict(color='#636EFA', width=3), connectgaps=True))
    fig1.add_trace(go.Scatter(x=stress_df['record_date'], y=stress_df['rrp_balance'], name='RRP Balance ($B)', line=dict(color='#EF553B', width=2), yaxis='y2', connectgaps=True))
    fig1.update_layout(
        yaxis=dict(title="TGA ($B)"),
        yaxis2=dict(title="RRP ($B)", overlaying='y', side='right'),
        legend=dict(orientation="h", x=0.5, xanchor="center"),
        hovermode="x unified", height=400
    )
    st.plotly_chart(fig1, use_container_width=True)
    st.divider()
        
    st.subheader("Quadrant 2: Rollover Wall vs. Buyback Support")
    st.caption("Debt Maturing within 12 Months vs. Recent Buyback Operations")
    
    schedule_df = data.get('schedule')
    redemptions_df = data.get('redemptions')
    buybacks_df = data.get('buybacks')
    surcharge_df = data.get('surcharge')
    issuance_plan_df = data.get('issuance_plan')
    refi_shift_df = data.get('refi_shift')
    debt_spiral_df = data.get('debt_spiral')
    today = pd.Timestamp.now().normalize()
    # Calculate Rolling Window (User requested: -1 month to +2 months)
    start_viz = today - pd.Timedelta(days=30)
    end_viz = today + pd.Timedelta(days=60)
    
    # --- New Chart: The Interest Rate Surcharge ---
    # --- Chart 1: The Refinancing Shift (Daily Delta) ---
    if refi_shift_df is not None and not refi_shift_df.empty:
        st.subheader("Chart 1: The Refinancing Shift (Daily Event)")
        st.caption("Visualizing the 'Trade-Up' Event: Old Cheap Debt vs. New Expensive Debt")
        
        # Filter for recent/upcoming events AND meaningful volume
        shift_viz = refi_shift_df[
            (pd.to_datetime(refi_shift_df['date']) >= start_viz) & 
            (pd.to_datetime(refi_shift_df['date']) <= end_viz) &
            ((refi_shift_df['maturing_amt'] > 1e6) | (refi_shift_df['issuing_amt'] > 1e6))
        ].copy()
        
        if not shift_viz.empty:
            fig_shift = go.Figure()
            
            # Maturing Debt (Red)
            fig_shift.add_trace(go.Bar(
                x=shift_viz['date'],
                y=shift_viz['maturing_amt'] / 1e9,
                name='Maturing Debt (Out)',
                marker_color='#EF553B',
                text=shift_viz['maturing_rate'].apply(lambda x: f"@{x:.2f}%"),
                textposition='auto',
                customdata=shift_viz[['maturing_rate', 'term_label']],
                hovertemplate="<b>Maturing:</b> $%{y:.1f}B<br><b>Rate:</b> %{customdata[0]:.2f}%<br><b>Term:</b> %{customdata[1]}<extra></extra>"
            ))
            
            # New Issuance (Green)
            # Add logic for 'Est.' label
            def format_issuance_label(row):
                label = f"@{row['issuing_rate']:.2f}%"
                if row.get('is_estimate', False):
                    label = f"Est. {label}"
                return label

            fig_shift.add_trace(go.Bar(
                x=shift_viz['date'],
                y=shift_viz['issuing_amt'] / 1e9,
                name='New Issuance (In)',
                marker_color='#00CC96',
                text=shift_viz.apply(format_issuance_label, axis=1),
                textposition='auto',
                customdata=shift_viz[['issuing_rate', 'term_label']],
                hovertemplate="<b>Issuing:</b> $%{y:.1f}B<br><b>New Rate:</b> %{customdata[0]:.2f}%<br><b>Term:</b> %{customdata[1]}<extra></extra>"
            ))
            
            fig_shift.update_layout(
                title="The Shift: Maturing Volume vs. New Volume",
                yaxis_title="Volume ($B)",
                barmode='group',
                hovermode="x unified",
                height=400,
                legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center")
            )
            st.plotly_chart(fig_shift, use_container_width=True)
            
    # --- Chart 2: The Cumulative Debt Spiral ---
    if debt_spiral_df is not None and not debt_spiral_df.empty:
        st.subheader("Chart 2: The Debt Spiral (Cumulative Impact)")
        st.caption("Tracking the Acceleration of Debt Burden. Note how the **Avg Interest Rate (Blue)** rises as cheap legacy debt is replaced by expensive new issuance.")
        
        # Filter
        spiral_viz = debt_spiral_df[
            (pd.to_datetime(debt_spiral_df['date']) >= start_viz) & 
            (pd.to_datetime(debt_spiral_df['date']) <= end_viz)
        ].copy()
        
        if not spiral_viz.empty:
            fig_spiral = go.Figure()
            
            # Total Debt (Left Y - Red)
            fig_spiral.add_trace(go.Scatter(
                x=spiral_viz['date'],
                y=spiral_viz['total_debt_trillions'],
                name='Total Debt ($T)',
                line=dict(color='#EF553B', width=3),
                yaxis='y'
            ))
            
            # Avg Interest Rate (Right Y - Blue)
            fig_spiral.add_trace(go.Scatter(
                x=spiral_viz['date'],
                y=spiral_viz['avg_interest_rate'],
                name='Weighted Avg Rate (%)',
                line=dict(color='#636EFA', width=3, dash='dot'),
                yaxis='y2'
            ))
            
            fig_spiral.update_layout(
                title="The Spiral: Mounting Debt & Rising Rates",
                yaxis=dict(title="Total Debt ($T)", title_font=dict(color="#EF553B"), tickfont=dict(color="#EF553B")),
                yaxis2=dict(title="Avg Interest Rate (%)", title_font=dict(color="#636EFA"), tickfont=dict(color="#636EFA"), 
                            overlaying='y', side='right', showgrid=False),
                hovermode="x unified",
                height=400,
                legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center")
            )
            st.plotly_chart(fig_spiral, use_container_width=True)
            st.divider()

    fig2 = go.Figure()
    
    # Process Historical Redemptions (Past Wall)
    if redemptions_df is not None and not redemptions_df.empty:
        redemptions_df['record_date'] = pd.to_datetime(redemptions_df['record_date'])
        # Filter for Redemptions only (to avoid double counting with Issues)
        hist_mat = redemptions_df[
            (redemptions_df['transaction_type'] == 'Redemptions') & 
            (redemptions_df['record_date'] < today)
        ].copy()
        
        if not hist_mat.empty:
            hist_mat['week'] = hist_mat['record_date'].dt.to_period('W').apply(lambda r: r.start_time)
            weekly_hist = hist_mat.groupby('week')['total_mil'].sum().reset_index()
            
            fig2.add_bar(
                x=weekly_hist['week'],
                y=weekly_hist['total_mil']/1e3,
                name='Maturity (Settled) $B',
                marker_color='#AB63FA', # Purple for settled
                opacity=0.5,
                hovertemplate="<b>Week Starting:</b> %{x|%b %d, %Y}<br><b>Redemptions:</b> $%{y:.1f}B<extra></extra>"
            )

    # Process Future Maturity Schedule & Planned Issuance
    weekly_mat = pd.DataFrame()
    if schedule_df is not None and not schedule_df.empty:
        schedule_df['maturity_date'] = pd.to_datetime(schedule_df['maturity_date'])
        future_mat = schedule_df[schedule_df['maturity_date'] >= today].copy()
        if not future_mat.empty:
            future_mat['week'] = future_mat['maturity_date'].dt.to_period('W').apply(lambda r: r.start_time)
            weekly_mat = future_mat.groupby('week')['amount_mil'].sum().reset_index()

    weekly_iss = pd.DataFrame()
    if issuance_plan_df is not None and not issuance_plan_df.empty:
        issuance_plan_df['auction_date'] = pd.to_datetime(issuance_plan_df['auction_date'])
        upcoming_iss = issuance_plan_df[issuance_plan_df['auction_date'] >= today].copy()
        if not upcoming_iss.empty:
            upcoming_iss['week'] = upcoming_iss['auction_date'].dt.to_period('W').apply(lambda r: r.start_time)
            weekly_iss = upcoming_iss.groupby('week')['offering_amount'].sum().reset_index()

    # Merge for Tooltips
    if not weekly_mat.empty:
        merged_weekly = pd.merge(weekly_mat, weekly_iss, on='week', how='left').fillna(0)
        # Add surcharge delta info back to the week
        if surcharge_df is not None and not surcharge_df.empty:
            s_copy = surcharge_df.copy()
            s_copy['week'] = pd.to_datetime(s_copy['maturity_date']).dt.to_period('W').apply(lambda r: r.start_time)
            weekly_surcharge = s_copy.groupby('week')['delta_bps'].mean().reset_index()
            merged_weekly = pd.merge(merged_weekly, weekly_surcharge, on='week', how='left').fillna(0)
        else:
            merged_weekly['delta_bps'] = 0
            
        # Standardize customdata for tooltips (Issuance in $B, Delta in BPS)
        merged_weekly['issuance_bn'] = merged_weekly['offering_amount'] / 1e9
        stack_data = merged_weekly[['issuance_bn', 'delta_bps']].values

        # Current Wall (Blue)
        fig2.add_trace(go.Bar(
            x=merged_weekly['week'],
            y=merged_weekly['amount_mil']/1e3,
            name='Upcoming Maturity $B',
            marker_color='#636EFA',
            opacity=0.8,
            customdata=stack_data,
            hovertemplate="""<b>Week Starting:</b> %{x|%b %d, %Y}<br>
                             Maturing Amount: $%{y:.1f}B<br>
                             Replacement Issuance: $%{customdata[0]:.1f}B<br>
                             Cost Increase: +%{customdata[1]:.1f} bps<extra></extra>"""
        ))

        # Ghost Bars for Planned Issuance
        fig2.add_trace(go.Bar(
            x=merged_weekly['week'],
            y=merged_weekly['offering_amount']/1e9,
            name='Planned Issuance (Ghost Wall) $B',
            marker_color='rgba(150, 150, 150, 0.4)',
            offsetgroup=1, 
            hovertemplate="<b>Week Starting:</b> %{x|%b %d, %Y}<br><b>Planned Issuance:</b> $%{y:.1f}B<extra></extra>"
        ))

    # Process Historical Buybacks for overlay
    if buybacks_df is not None and not buybacks_df.empty:
        buybacks_df['record_date'] = pd.to_datetime(buybacks_df['record_date'])
        # Filter for recent (last 3 months) to show context
        lookback = today - pd.Timedelta(days=90)
        recent_bb = buybacks_df[buybacks_df['record_date'] >= lookback].copy()
        
        if not recent_bb.empty:
            bb_daily = recent_bb.groupby('record_date')['total_accepted'].sum().reset_index()
            fig2.add_trace(go.Scatter(
                x=bb_daily['record_date'],
                y=bb_daily['total_accepted']/1e9,
                name='Buyback Support $B',
                mode='markers+lines',
                marker=dict(size=8, color='#00CC96', symbol='diamond'),
                line=dict(width=2, color='#00CC96'),
                yaxis='y2', # Move to secondary Y to ensure visibility
                hovertemplate="<b>Date:</b> %{x}<br><b>Accepted:</b> $%{y:.1f}B<extra></extra>"
            ))
        

    fig2.update_layout(
        yaxis=dict(title="Maturity Wall ($B)", color='#636EFA'),
        yaxis2=dict(title="Buyback Support ($B)", color='#00CC96', overlaying='y', side='right', showgrid=False),
        xaxis=dict(range=[start_viz, end_viz]), # Force Rolling Window
        barmode='overlay',
        hovermode="x unified", height=450,
        legend=dict(orientation="h", y=-0.3, x=0.5, xanchor="center"),
        title="Rolling Refinancing Profile: The 'Maturity Wall' (9-Month Window)"
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.divider()

    # --- Section: Liquidity Support (Buybacks) ---
    st.subheader("Liquidity Support (Buybacks)")
    st.caption("Treasury Buyback Operations: Strategic liquidity injections via debt retirement")
    
    with st.expander("💡 How to read this?"):
        st.markdown("""
        **What it means:** This tracks how much liquidity the Treasury is *actually* injecting into the market by buying back older debt.
        - **Offered ($B):** The Treasury's maximum budget for that operation. It represents 'Intent'.
        - **Accepted ($B):** The actual amount purchased. It represents 'Executed Support'.
        - **Acceptance Ratio (%):** A key efficiency metric. High ratios (>70%) mean the Treasury is aggressively supporting liquidity. Low ratios (<40%) mean the bids were too expensive or the market didn't need as much support as expected.
        
        **The Stress Signal:** 
        - **Price Mismatch:** If the Treasury is 'Offering' a lot but 'Accepting' very little, it means sellers (banks) are asking for a **higher price** than the Treasury is willing to pay. 
        - **Liquidity Failure:** A consistently low ratio implies that the 'Liquidity Injection' is failing because the Treasury won't pay the market's price for its own debt.
        """)
    
    if buybacks_df is not None and not buybacks_df.empty:
        # Ensure record_date is string for categorical axis & sort
        buybacks_df['record_date'] = pd.to_datetime(buybacks_df['record_date']).dt.strftime('%Y-%m-%d')
        sorted_dates = sorted(buybacks_df['record_date'].unique())
        
        # Handle cases where maturity_bucket might be missing due to cache
        current_cols = buybacks_df.columns
        has_bucket = 'maturity_bucket' in current_cols
        
        # Group by date for the ratio line
        bb_daily = buybacks_df.groupby('record_date').agg({
            'total_offered': 'sum', 
            'total_accepted': 'sum'
        }).reset_index().sort_values('record_date')
        bb_daily['ratio'] = (bb_daily['total_accepted'] / bb_daily['total_offered']) * 100
        
        fig_bb = go.Figure()
        
        # Binning Maturity Buckets for visual impact (reduces # of bars per date)
        def bin_maturity(b):
            b = str(b)
            if '1Mo' in b or '2Y' in b and '3Y' not in b: return 'Short Term (<2Y)'
            if '2Y to 3Y' in b or '3Y to 5Y' in b: return 'Short-Mid (2Y-5Y)'
            if '5Y' in b or '7Y' in b or '1Y to 10Y' in b: return 'Intermediate (5Y-10Y)'
            if '10Y to 20Y' in b: return 'Long Term (10Y-20Y)'
            if '20Y to 30Y' in b or '10Y to 30Y' in b: return 'Ultra Long (20Y-30Y)'
            return 'Other'

        buybacks_df['binned_bucket'] = buybacks_df['maturity_bucket'].apply(bin_maturity)
        
        # Color Map for Binned Durations
        bucket_colors = {
            'Short Term (<2Y)': 'rgba(99, 110, 250, 1)',   # Blue 
            'Short-Mid (2Y-5Y)': 'rgba(0, 204, 150, 1)',    # Green
            'Intermediate (5Y-10Y)': 'rgba(255, 161, 90, 1)',# Orange
            'Long Term (10Y-20Y)': 'rgba(171, 99, 250, 1)',  # Purple
            'Ultra Long (20Y-30Y)': 'rgba(239, 85, 59, 1)', # Red
        }
        
        # Group by Date and Binned Bucket (Ensuring chronological sort)
        bb_binned = buybacks_df.groupby(['record_date', 'binned_bucket']).agg({
            'total_offered': 'sum',
            'total_accepted': 'sum'
        }).reset_index().sort_values('record_date')

        fig_bb = go.Figure()
        
        # Iterate through binned buckets
        order = ['Short Term (<2Y)', 'Short-Mid (2Y-5Y)', 'Intermediate (5Y-10Y)', 'Long Term (10Y-20Y)', 'Ultra Long (20Y-30Y)', 'Other']
        unique_buckets = sorted(bb_binned['binned_bucket'].unique(), key=lambda x: order.index(x) if x in order else 99)
        
        for bucket in unique_buckets:
            bucket_df = bb_binned[bb_binned['binned_bucket'] == bucket]
            base_rgb = bucket_colors.get(bucket, 'rgba(128, 128, 128, 1)')
            
            # 1. Intent Bars (Offered) - Full Width Ghost
            ghost_color = base_rgb.replace(', 1)', ', 0.15)')
            fig_bb.add_trace(go.Bar(
                x=bucket_df['record_date'], 
                y=bucket_df['total_offered']/1e9, 
                name=f'{bucket} (Offered)', 
                marker_color=ghost_color,
                legendgroup=bucket,
                showlegend=True,
                hovertemplate="<b>%{x}</b><br>%{name}<br>Offered: $%{y:.1f}B<extra></extra>",
                width=0.6 # Constant width on categorical axis
            ))
            
            # 2. Execution Bars (Accepted) - Slightly narrower solid overlay
            solid_color = base_rgb.replace(', 1)', ', 0.8)')
            fig_bb.add_trace(go.Bar(
                x=bucket_df['record_date'], 
                y=bucket_df['total_accepted']/1e9, 
                name=f'{bucket} (Accepted)', 
                marker_color=solid_color,
                legendgroup=bucket,
                showlegend=False,
                hovertemplate="<b>%{x}</b><br>%{name}<br>Accepted: $%{y:.1f}B<extra></extra>",
                width=0.4 # Narrower to create 'nested' effect
            ))

        # 3. Efficiency Line (Acceptance Ratio) - Must be sorted for correct pathing
        bb_daily = buybacks_df.groupby('record_date').agg({'total_offered':'sum', 'total_accepted':'sum'}).reset_index().sort_values('record_date')
        bb_daily['ratio'] = (bb_daily['total_accepted'] / bb_daily['total_offered']) * 100
        
        fig_bb.add_trace(go.Scatter(
            x=bb_daily['record_date'], 
            y=bb_daily['ratio'], 
            name='Avg Acceptance Ratio %',
            line=dict(color='#FFA15A', width=3, dash='dot'),
            yaxis='y2',
            hovertemplate="Avg Acceptance: %{y:.1f}%<extra></extra>"
        ))
        
        fig_bb.update_layout(
            barmode='overlay', # Draw Reality over Intent
            height=500,
            xaxis=dict(
                type='category', 
                categoryorder='array',
                categoryarray=sorted_dates,
                tickangle=-45,
                title="Operation Date"
            ),
            yaxis=dict(title="Liquidity Volume ($B)", gridcolor='rgba(128,128,128,0.1)'),
            yaxis2=dict(
                title="Acceptance Efficiency (%)",
                overlaying='y',
                side='right',
                range=[0, 105],
                showgrid=False
            ),
            legend=dict(
                orientation="v", 
                x=1.15, 
                y=0.5, 
                xanchor="left", 
                yanchor="middle",
                bgcolor='rgba(0,0,0,0)',
                bordercolor='rgba(128,128,128,0.2)',
                borderwidth=1
            ),
            hovermode="x unified",
            margin=dict(t=30, b=80, r=180) # Increased right margin for legend
        )
        st.plotly_chart(fig_bb, use_container_width=True)
    else:
        st.info("No buyback data available.")
        
    st.divider()
        
    st.subheader("Quadrant 3: Auction Health")
    st.caption("Auction Demand (BTC) & Pricing (Tail) - Last 6 Months")
    with st.expander("💡 How to read this?"):
        st.markdown("""
        **What it means:** This measures how 'hungry' investors are for US debt.
        - **Bid-to-Cover (BTC):** A ratio of demand. 2.5x means for every $1 offered, investors bid $2.5. A falling ratio indicates weak demand.
        - **Allotment Breakdown:** Who is actually buying the debt?
            - **Indirect (Green):** Primarily Foreign Central Banks and International Investors. A high share is a sign of global confidence.
            - **Direct (Blue):** Domestic Institutional Investors (Hedge Funds, Pensions).
            - **Primary Dealers (Red):** The 'Buyers of Last Resort'. A high share here means 'real' investors stayed away, forcing Market Makers to soak up the supply.
            - **SOMA (Purple):** The Federal Reserve's purchases (Quantitative Easing).
        """)
    
    # Categorize maturities for cleaner visualization
    bills_list = ['4-Week', '8-Week', '13-Week', '17-Week', '26-Week', '52-Week', '6-Week']
    coupons_list = ['2-Year', '3-Year', '5-Year', '7-Year', '10-Year', '20-Year', '30-Year']
    
    # --- New Aggregate Investor Profile Section ---
    st.markdown("**Global Investor Profile: Liquidity vs. Duration**")
    
    # Prepare Aggregates with robust categorization
    def categorize_auction(row):
        stype = str(row['security_type']).lower()
        if 'week' in stype or 'day' in stype or 'month' in stype and 'year' not in stype:
            return "Liquidity (Bills)"
        return "Duration (Notes/Bonds)"
    
    auc_summary = auctions_df.copy()
    auc_summary['Category'] = auc_summary.apply(categorize_auction, axis=1)
    
    allot_cols = ['indirect_bidder_accepted', 'direct_bidder_accepted', 'primary_dealer_accepted', 'soma_accepted', 'noncomp_accepted']
    col_labels = {
        'indirect_bidder_accepted': 'Indirect (Foreign)',
        'direct_bidder_accepted': 'Direct (Hedge Funds)',
        'primary_dealer_accepted': 'Primary Dealers',
        'soma_accepted': 'SOMA (Fed)',
        'noncomp_accepted': 'Non-comp (Retail)'
    }
    
    profile_data = []
    for cat_name in auc_summary['Category'].unique():
        df = auc_summary[auc_summary['Category'] == cat_name]
        if not df.empty:
            total = df['total_accepted'].sum()
            if total > 0:
                for col in allot_cols:
                    share = (df[col].sum() / total) * 100
                    profile_data.append({
                        "Category": cat_name,
                        "Investor Class": col_labels[col],
                        "Share (%)": round(share, 1)
                    })
    
    if profile_data:
        profile_df = pd.DataFrame(profile_data)
        fig_profile = px.bar(profile_df, x="Category", y="Share (%)", color="Investor Class",
                             barmode="group", text="Share (%)",
                             color_discrete_map={
                                 'Indirect (Foreign)': '#00CC96',
                                 'Direct (Hedge Funds)': '#636EFA',
                                 'Primary Dealers': '#EF553B',
                                 'SOMA (Fed)': '#AB63FA',
                                 'Non-comp (Retail)': '#FFA15A'
                             })
        fig_profile.update_layout(height=400, yaxis_title="Market Share (%)", legend=dict(orientation="h", y=-0.2))
        fig_profile.update_traces(textposition='outside')
        st.plotly_chart(fig_profile, use_container_width=True)
    
    with st.expander("🏛️ Guide to Auction Participants & The Fed's Role"):
        st.markdown("""
        ### Who is buying US Debt?
        | Investor Class | Typical Participants | Why it matters |
        | :--- | :--- | :--- |
        | **Indirect (Foreign)** | **Foreign Central Banks** (e.g. Bank of Japan, PBoC), IMF, International Authorities. | Measures global trust in the US Dollar and reliability as a reserve currency. |
        | **Direct (Domestic)** | **Hedge Funds**, Pension Funds, Insurance Companies, Mutual Funds. | Represents 'real' domestic investment demand. |
        | **Primary Dealers** | **Wall Street Banks** (e.g. JPMorgan, Goldman Sachs). | The **Buyers of Last Resort**. They are *obligated* to bid. High PD share signal weak 'real' demand. |
        | **SOMA (Fed)** | **The Federal Reserve's** portfolio. | The Fed typically 'rolls over' maturing debt. High SOMA share usually implies QE (Money Printing). |
        | **Non-comp (Retail)** | **Individual Investors**, small institutions. | Shows grass-roots retail demand. |

        ---
        ### 🧐 FAQ: Does the Fed 'Print Money' to buy debt?
        **Q: Where does the Fed's money come from?**
        Essentially, yes. When the Fed (SOMA) buys Treasuries, it does not use existing tax revenue. It creates **electronic reserves** (new money) out of thin air and credits it to the commercial banks' accounts. 
        
        **Q: Isn't the Fed the Buyer of Last Resort?**
        Technically, **No**. In a Treasury Auction, the **Primary Dealers** are the regulatory buyers of last resort. They are *required* to bid for their pro-rata share to ensure no auction ever 'fails'. The Fed typically buys in the **Secondary Market** (after the auction) or rolls over maturing debt to avoid a sudden liquidity drain. If the Fed has to step in directly to fund the government (QE), it's a sign of significant market stress.
        """)

    st.write("---")
    
    tab1, tab2 = st.tabs(["Duration Risk (Notes/Bonds)", "Liquidity Backbone (Bills)"])
    
    with tab1:
        # Notes and Bonds are more sensitive to duration risk
        # Notes and Bonds are more sensitive to duration risk
        coupon_auctions = auctions_df[auctions_df['security_type'].isin(coupons_list) | auctions_df['security_type'].str.contains('Year')].copy()
        if not coupon_auctions.empty:
            fig3a = go.Figure()
            # BTC Line
            for sec in coupon_auctions['security_type'].unique():
                df_sec = coupon_auctions[coupon_auctions['security_type'] == sec]
                fig3a.add_trace(go.Scatter(x=df_sec['record_date'], y=df_sec['bid_to_cover'], 
                                         name=f"{sec} BTC", mode='lines+markers', connectgaps=True))
            
            fig3a.add_hline(y=2.3, line_dash="dash", line_color="red", annotation_text="Weak Demand")
            fig3a.update_layout(title="Bid-to-Cover (BTC) - Notes & Bonds", height=400, 
                              yaxis_title="BTC Ratio", hovermode="x unified")
            st.plotly_chart(fig3a, use_container_width=True)
            
            st.write("---")
            st.markdown("**Allotment Breakdown (Notes & Bonds)**")
            # Allotment Stacked Bar
            fig3a_allot = go.Figure()
            # Wong color-blind friendly palette
            cols = {
                'indirect_bidder_accepted': ('Indirect (Foreign CBs)', '#009E73'), # Bluish Green
                'direct_bidder_accepted': ('Direct (Hedge Funds/Dom)', '#0072B2'), # Blue
                'primary_dealer_accepted': ('Primary Dealers', '#D55E00'),        # Vermillion
                'soma_accepted': ('SOMA (Fed)', '#CC79A7'),                       # Reddish Purple
                'noncomp_accepted': ('Non-comp (Retail)', '#E69F00')              # Orange
            }
            for col, (name, color) in cols.items():
                fig3a_allot.add_trace(go.Bar(
                    x=coupon_auctions['record_date'] + " " + coupon_auctions['security_type'],
                    y=coupon_auctions[col] / 1e9,
                    name=name, marker_color=color
                ))
            
            # Add Total Volume Line on Secondary Axis
            coupon_auctions['total_accepted'] = coupon_auctions[[c for c in cols.keys()]].sum(axis=1)
            fig3a_allot.add_trace(go.Scatter(
                x=coupon_auctions['record_date'] + " " + coupon_auctions['security_type'],
                y=coupon_auctions['total_accepted'] / 1e9,
                name='Total Volume ($B)',
                line=dict(color='white', width=2, dash='dot'),
                yaxis='y2',
                hovertemplate="Total Volume: $%{y:.1f}B<extra></extra>"
            ))
            
            fig3a_allot.update_layout(
                barmode='stack', 
                barnorm='percent', # 100% stacked
                height=450, 
                title="Allotment Share by Investor Class (%)",
                yaxis=dict(title="Allotment Share (%)", ticksuffix="%"),
                yaxis2=dict(
                    title="Total Volume ($B)",
                    overlaying='y',
                    side='right',
                    showgrid=False,
                    rangemode='tozero'
                ),
                legend=dict(
                    orientation="v", 
                    x=1.1, 
                    y=0.5, 
                    xanchor="left", 
                    yanchor="middle",
                    bgcolor='rgba(0,0,0,0)'
                ),
                hovermode="x unified",
                margin=dict(r=150)
            )
            st.plotly_chart(fig3a_allot, use_container_width=True)
        else:
            st.info("No Note/Bond auction data found for this period.")

    with tab2:
        # Bills are high frequency
        # Bills are high frequency
        bill_auctions = auctions_df[auctions_df['security_type'].isin(bills_list) | auctions_df['security_type'].str.contains('Week')].copy()
        if not bill_auctions.empty:
            fig3b = go.Figure()
            for sec in bill_auctions['security_type'].unique():
                df_sec = bill_auctions[bill_auctions['security_type'] == sec]
                fig3b.add_trace(go.Scatter(x=df_sec['record_date'], y=df_sec['bid_to_cover'], 
                                         name=f"{sec} BTC", mode='lines+markers', connectgaps=True))
            
            fig3b.update_layout(title="Bid-to-Cover (BTC) - Treasury Bills", height=400, 
                              yaxis_title="BTC Ratio", hovermode="x unified")
            st.plotly_chart(fig3b, use_container_width=True)

            st.write("---")
            st.markdown("**Allotment Breakdown (Recent Bills)**")
            # For bills, let's just show top 15 most recent to avoid clutter
            bill_recent = bill_auctions.head(20).copy()
            fig3b_allot = go.Figure()
            for col, (name, color) in cols.items():
                fig3b_allot.add_trace(go.Bar(
                    x=bill_recent['record_date'] + " " + bill_recent['security_type'],
                    y=bill_recent[col] / 1e9,
                    name=name, marker_color=color
                ))
            # Add Total Volume Line on Secondary Axis
            bill_recent['total_accepted'] = bill_recent[[c for c in cols.keys()]].sum(axis=1)
            fig3b_allot.add_trace(go.Scatter(
                x=bill_recent['record_date'] + " " + bill_recent['security_type'],
                y=bill_recent['total_accepted'] / 1e9,
                name='Total Volume ($B)',
                line=dict(color='white', width=2, dash='dot'),
                yaxis='y2',
                hovertemplate="Total Volume: $%{y:.1f}B<extra></extra>"
            ))

            fig3b_allot.update_layout(
                barmode='stack', 
                barnorm='percent', # 100% stacked
                height=450, 
                title="Allotment Share by Investor Class (%)",
                yaxis=dict(title="Allotment Share (%)", ticksuffix="%"),
                yaxis2=dict(
                    title="Total Volume ($B)",
                    overlaying='y',
                    side='right',
                    showgrid=False,
                    rangemode='tozero'
                ),
                legend=dict(
                    orientation="v", 
                    x=1.1, 
                    y=0.5, 
                    xanchor="left", 
                    yanchor="middle",
                    bgcolor='rgba(0,0,0,0)'
                ),
                hovermode="x unified",
                margin=dict(r=150)
            )
            st.plotly_chart(fig3b_allot, use_container_width=True)
        else:
            st.info("No Bill auction data found for this period.")

    st.divider()
        
    st.subheader("Quadrant 4: Risk Premium")
    st.caption("US 5Y CDS Spreads vs. 10Y Treasury Yield")
    with st.expander("💡 How to read this?"):
        st.markdown("""
        **What it means:** This compares the cost of 'Insurance' against default vs general interest rates.
        - **CDS Spread (Purple):** The cost to insure US debt. Higher = Higher perceived risk.
        - **10Y Yield (Green):** The standard interest rate.
        - **The Stress Signal:** If the Purple line spikes while the Green line stays flat, markets are worried specifically about **US Credit Quality**, not just inflation or growth.
        """)
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(x=stress_df['record_date'], y=stress_df['cds_spread'], name='US 5Y CDS (bps)', line=dict(color='#AB63FA', width=3), mode='lines+markers', connectgaps=True))
    fig4.add_trace(go.Scatter(x=stress_df['record_date'], y=stress_df['yield_10y'], name='10Y Yield (%)', line=dict(color='#00CC96', width=2), yaxis='y2', connectgaps=True))
    fig4.update_layout(
        yaxis=dict(title="CDS (bps)"),
        yaxis2=dict(title="10Y Yield (%)", overlaying='y', side='right'),
        legend=dict(orientation="h", x=0.5, xanchor="center"),
        hovermode="x unified", height=400
    )
    st.plotly_chart(fig4, use_container_width=True)


# --- MAIN DASHBOARD VIEW (Conditional) ---
if dashboard_mode == "Main Dashboard":

    # --- DATA LOADING ---
    macro_df = get_carry_trade_data()
    credit_status = check_credit_stress()
    valve_df = get_liquidity_valve_data()
    alert_status = check_systemic_alert()
    scores_df = calculate_squeeze_score()
    credit_df = get_credit_stress_data(days=90)

    # --- HEADQUARTERS (SITREP) ---
    st.title("🌊 Global Liquidity Flow Monitor")

    # Generate the Layman Narrative
    status_color, status_title, sitrep_points, action_command = generate_sitrep(credit_status, valve_df, alert_status)


    # Display the "General's View"
    with st.container():
        st.markdown(f"### {status_title}")
    
        c1, c2 = st.columns([2, 1])
        with c1:
            st.info(f"**📝 SITUATION REPORT:**\n\n" + "\n".join([f"- {pt}" for pt in sitrep_points]))
        with c2:
            if status_color == "error":
                st.error(f"**YOUR ORDERS:**\n\n### {action_command}")
            elif status_color == "warning":
                st.warning(f"**YOUR ORDERS:**\n\n### {action_command}")
            else:
                st.success(f"**YOUR ORDERS:**\n\n### {action_command}")

    st.divider()

    # --- ORIGINAL DASHBOARD BELOW ---

    # Metric Row
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.subheader("System Status")
        if alert_status['alert']:
            st.error(f"🚨 {alert_status['message']}")
        else:
            st.success(f"✅ {alert_status['message']}")

    with col2:
        st.subheader("Carry Trade Spread")
        if not macro_df.empty:
            latest_spread = macro_df.iloc[-1].get('yield_spread', 0)
            # 30-day delta
            delta_val = 0
            target_date = macro_df.iloc[-1]['timestamp'] - pd.Timedelta(days=30)
            past_data = macro_df[macro_df['timestamp'] <= target_date]
            if not past_data.empty:
                past_spread = past_data.iloc[-1].get('yield_spread', 0)
                delta_val = latest_spread - past_spread
            
            if latest_spread < 2.5:
                st.error(f"Low: {latest_spread:.2f}%")
            elif latest_spread > 3.0:
                st.success(f"Healthy: {latest_spread:.2f}%")
            else:
                st.metric("Spread", f"{latest_spread:.2f}%", delta=f"{delta_val:+.2f}% 30d")
        else:
            st.write("No data")
        
    with col3:
        st.subheader("Yield Trend (10Y)")
        if alert_status['yield_rising']:
            st.write("📈 Rising")
        else:
             st.write("➡️ Stable/Falling")

    with col4:
        st.subheader("Margin Hikes (24h)")
        if alert_status['hikes']:
            st.warning("\n".join(alert_status['hikes']))
        else:
            st.info("None > 5%")

    st.divider()

    # Normalize instrument names
    if not scores_df.empty:
        scores_df['instrument'] = scores_df['instrument'].str.upper()

    # Filter for CME instruments
    if not scores_df.empty:
        # Desired order: SILVER, GOLD, CRUDE_OIL, NATURAL_GAS, COPPER, NQ, ES
        cme_instruments = ['SILVER', 'GOLD', 'CRUDE_OIL', 'NATURAL_GAS', 'COPPER', 'NQ', 'ES']
        scores_df = scores_df[scores_df['instrument'].isin(cme_instruments)]
        scores_df = scores_df[scores_df['exchange'] == 'CME']

    # --- Sidebar Controls ---
    st.sidebar.header("Controls")

    if not scores_df.empty:
        # Timeline Filter
        max_date = scores_df['timestamp'].max().date()
        min_date = scores_df['timestamp'].min().date()
        default_start = max(min_date, max_date - timedelta(days=30))
    
        st.sidebar.subheader("Filter Timeline")
        date_range = st.sidebar.date_input(
            "Select Range",
            value=(default_start, max_date),
            min_value=min_date,
            max_value=max_date
        )
    
        if len(date_range) == 2:
            start_filter, end_filter = date_range
            scores_df = scores_df[
                (scores_df['timestamp'].dt.date >= start_filter) & 
                (scores_df['timestamp'].dt.date <= end_filter)
            ]
    
        instruments = scores_df['instrument'].unique()
        # Use the order from cme_instruments filtered by available data in scores_df
        ordered_instruments = [inst for inst in cme_instruments if inst in instruments]
        selected_instrument = st.sidebar.selectbox("Select Instrument for Deep Dive", ordered_instruments)
        inst_df = scores_df[scores_df['instrument'] == selected_instrument].sort_values('timestamp')
        selected_exchange = 'CME'
    
        # --- Visual 1: Deep Dive ---
        st.subheader(f"Visual 1: {selected_instrument} ({selected_exchange}) - Price vs. Margin Requirements %")
    
        if not inst_df.empty:
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=inst_df['timestamp'], y=inst_df['price_now'], name=f"Price", line=dict(color='#636EFA', width=3), yaxis='y1'))
            fig1.add_trace(go.Bar(x=inst_df['timestamp'], y=inst_df['margin_now'], name="Margin %", marker_color='#EF553B', opacity=0.6, yaxis='y2'))
        
            holidays = get_dashboard_holidays()
            fig1.update_layout(
                title=f"{selected_instrument} Price vs Leverage Requirements",
                xaxis=dict(title="Time", rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=holidays)]),
                yaxis=dict(title="Price", title_font=dict(color='#636EFA')),
                yaxis2=dict(title="Margin %", title_font=dict(color='#EF553B'), overlaying='y', side='right', ticksuffix="%"),
                hovermode="x unified",
                legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center")
            )
            st.plotly_chart(fig1, width="stretch", key="visual_1_deep_dive")
        else:
            st.info(f"No data for {selected_instrument}")

        # --- Visual 2: Global Stress Monitor ---
        st.divider()
        st.subheader("Visual 2: Global Stress Monitor (All-Asset Multiplier)")
        st.caption("Comparing Sustained Margin Pressure across Metals and Equities.")
    
        if not scores_df.empty:
            fig2 = px.line(
                scores_df, x='timestamp', y='pressure_index', color='instrument',
                title="Margin Pressure Multiplier", labels={'pressure_index': 'Multiplier'},
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig2.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="Baseline")
            fig2.add_hline(y=1.5, line_dash="dash", line_color="orange", annotation_text="Stress")
            fig2.update_layout(xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=holidays)]), hovermode="x unified")
            st.plotly_chart(fig2, width="stretch", key="visual_2_stress_monitor")

        # --- Visual 3: Correlation Matrix ---
        st.divider()
        st.subheader("Visual 3: Global Margin Correlation")
    
        with st.expander("ℹ️ How to read this matrix?"):
            st.markdown("""
            * 🟦 **Blue (1.0)**: **Lockstep Move**. High Systemic Risk.
            * ⬜ **White (0.0)**: **Uncorrelated**. Moving independently.
            * 🧧 **Red (-1.0)**: **Inverse**. Safe Haven behavior.
            """)

        conn = get_db_connection()
        margins_df = pd.read_sql("""
            SELECT l.timestamp, i.symbol || ' (' || e.name || ')' as asset, l.margin_percent
            FROM margin_logs l
            JOIN instruments i ON l.instrument_id = i.id
            JOIN exchanges e ON i.exchange_id = e.id
        """, conn)
        conn.close()
    
        if not margins_df.empty:
            pivot_df = margins_df.pivot_table(index='timestamp', columns='asset', values='margin_percent')
            pct_change_df = pivot_df.pct_change().dropna(how='all')
            if not pct_change_df.empty:
                fig3 = px.imshow(pct_change_df.corr(), text_auto='.2f', color_continuous_scale="RdBu", zmin=-1, zmax=1, aspect="auto")
                st.plotly_chart(fig3, width="stretch", key="visual_3_correlation")

        # --- Visual 4: Global Liquidity Map ---
        st.divider()
        st.subheader("Visual 4: Global Liquidity Map (Margin Hikes & Cuts)")
        latest_scores = scores_df.sort_values('timestamp').groupby('instrument').tail(1)
    
        if 'asset_class' in latest_scores.columns and not latest_scores.empty:
            min_score, max_score = latest_scores['shock_score'].min(), latest_scores['shock_score'].max()
            abs_max = max(abs(min_score), abs(max_score), 0.1)
        
            # Get last row for detailed metrics
            df_last_row = credit_df.iloc[-1]
            latest = df_last_row
            
            fig4 = px.bar(
                latest_scores, x='instrument', y='shock_score', color='asset_class',
                title="Margin Shift (24h)", text_auto='.1%', range_y=[-abs_max*1.1, abs_max*1.1]
            )
            fig4.update_layout(yaxis=dict(title="Shock Score (Hike/Cut %)", zeroline=True, zerolinewidth=3, zerolinecolor='black'))
            st.plotly_chart(fig4, width="stretch", key="visual_4_liquidity_map")
    
        # --- Row 3: Macro Plumbing ---
        st.divider()
        if not credit_df.empty:
            st.subheader("🔧 Macro Plumbing (The Engine Room)")
            st.caption("Tracking systemic risk signals.")
        
            # Row 1: Credit Canary & Reservoir
            mc1, mc2 = st.columns(2)
        
            # Query actual last data dates from DB (not forward-filled)
            conn = get_db_connection()
            macro_dates = pd.read_sql("""
                SELECT tenor, MAX(DATE(timestamp)) as last_date 
                FROM yield_logs 
                WHERE tenor IN ('DXY', 'HY_SPREAD', 'RRP', 'VIX', 'DXY_ICE')
                GROUP BY tenor
            """, conn)
            conn.close()
            macro_dates_dict = dict(zip(macro_dates['tenor'], macro_dates['last_date']))
        
            # Credit Canary
            with mc1:
                st.markdown("### 🐤 Credit Canary")
                hy_val = credit_status.get('hy_spread_current')
                if hy_val:
                    if hy_val > 4.0: st.error(f"{hy_val:.2f}% (FREEZE)")
                    elif hy_val > 3.5: st.warning(f"{hy_val:.2f}% (Stress)")
                    else: st.success(f"{hy_val:.2f}% (Healthy)")
                else: st.info("--")
            
                if 'hy_spread' in credit_df.columns:
                    fig_c = go.Figure(go.Scatter(x=credit_df['timestamp'], y=credit_df['hy_spread'], fill='tozeroy', line=dict(color='#AB63FA'), connectgaps=True))
                    fig_c.add_hline(y=4.0, line_dash="dash", line_color="red", annotation_text="4%")
                    fig_c.update_layout(height=200, margin=dict(l=0,r=0,t=10,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                    st.plotly_chart(fig_c, width="stretch", key="canary_sparkline")
                    st.caption(f"📅 {macro_dates_dict.get('HY_SPREAD', 'N/A')}")

            # Liquidity Reservoir
            with mc2:
                st.markdown("### 🏦 Reservoir")
                rrp_val = credit_status.get('rrp_current')
                if rrp_val:
                    disp = f"${rrp_val:.0f}B" if rrp_val < 1000 else f"${rrp_val/1000:.2f}T"
                    if rrp_val < 50: st.error(f"{disp} (EMPTY)")
                    else: st.info(f"{disp}")
                else: st.info("--")
            
                if 'rrp' in credit_df.columns:
                    fig_r = go.Figure(go.Scatter(x=credit_df['timestamp'], y=credit_df['rrp'], line=dict(color='#00CC96'), connectgaps=True))
                    fig_r.add_hline(y=50, line_dash="dash", line_color="red", annotation_text="$50B")
                    fig_r.update_layout(height=200, margin=dict(l=0,r=0,t=10,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                    st.plotly_chart(fig_r, width="stretch", key="reservoir_sparkline")
                    st.caption(f"📅 {macro_dates_dict.get('RRP', 'N/A')}")

            st.divider()

            # Row 2: Steamroller & Fear Gauge
            mc3, mc4 = st.columns(2)

            # Steamroller
            with mc3:
                st.markdown("### 💵 Steamroller")
                dxy_val = credit_status.get('dxy_current')
                dxy_ice_val = df_last_row.get('dxy_ice') if 'dxy_ice' in df_last_row else None

                # Metric Logic (Prioritize ICE if available, else Broad)
                if dxy_val and dxy_ice_val:
                    # Show both
                     if dxy_ice_val > 106: st.error(f"ICE: {dxy_ice_val:.2f} (STRONG) | Broad: {dxy_val:.1f}")
                     else: st.warning(f"ICE: {dxy_ice_val:.2f} | Broad: {dxy_val:.1f}")
                elif dxy_val:
                    if dxy_val > 115: st.error(f"{dxy_val:.1f} (CRUSHING)")
                    else: st.warning(f"{dxy_val:.1f}")
                else: st.info("--")
            
                fig_d = go.Figure()
                
                # Add Broad DXY (FRED)
                if 'dxy' in credit_df.columns:
                    fig_d.add_trace(go.Scatter(x=credit_df['timestamp'], y=credit_df['dxy'], name='Broad DXY (FRED)', line=dict(color='#EF553B', width=2), connectgaps=True, yaxis='y1'))
                    
                # Add ICE DXY (Live) on Secondary Axis
                if 'dxy_ice' in credit_df.columns:
                     fig_d.add_trace(go.Scatter(x=credit_df['timestamp'], y=credit_df['dxy_ice'], name='ICE DXY (Live)', line=dict(color='#0077BB', width=2, dash='dot'), connectgaps=True, yaxis='y2'))
                     fig_d.add_hline(y=97, line_dash="dot", line_color="#0077BB", annotation_text="ICE 97", yref='y2')

                fig_d.add_hline(y=115, line_dash="dash", line_color='#EF553B', annotation_text="Broad 115", yref='y1')
                
                fig_d.update_layout(
                    height=250, 
                    margin=dict(l=0,r=0,t=10,b=0), 
                    xaxis=dict(showticklabels=True), 
                    yaxis=dict(title="Broad", showticklabels=True, tickfont=dict(size=10, color='#EF553B'), title_font=dict(size=10, color='#EF553B'), automargin=True),
                    yaxis2=dict(title="ICE", showticklabels=True, tickfont=dict(size=10, color='#0077BB'), title_font=dict(size=10, color='#0077BB'), overlaying='y', side='right', automargin=True),
                    showlegend=True,
                    legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center", font=dict(size=9))
                )
                st.plotly_chart(fig_d, width="stretch", key="steamroller_chart")
                
                last_date_broad = macro_dates_dict.get('DXY', 'N/A')
                last_date_ice = macro_dates_dict.get('DXY_ICE', 'N/A')
                st.caption(f"📅 ICE: {last_date_ice} | Broad: {last_date_broad}")
                
            # Fear Gauge
            with mc4:
                st.markdown("### 😱 Fear Gauge")
                vix_val = credit_status.get('vix_current')
                if vix_val:
                    if vix_val > 30: st.error(f"{vix_val:.1f} (PANIC)")
                    elif vix_val > 20: st.warning(f"{vix_val:.1f} (Anxiety)")
                    else: st.success(f"{vix_val:.1f} (Calm)")
                else: st.info("--")
            
                if 'vix' in credit_df.columns:
                    fig_v = go.Figure(go.Scatter(x=credit_df['timestamp'], y=credit_df['vix'], fill='tozeroy', line=dict(color='#FFA15A'), connectgaps=True))
                    fig_v.add_hline(y=30, line_dash="dash", line_color="red", annotation_text="30")
                    fig_v.update_layout(height=250, margin=dict(l=0,r=0,t=10,b=0), xaxis=dict(showticklabels=True), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                    st.plotly_chart(fig_v, width="stretch", key="fear_gauge_sparkline")
                    st.caption(f"📅 {macro_dates_dict.get('VIX', 'N/A')}")

        # --- Visual 5: Carry Trade ---
        st.divider()
        st.subheader("Visual 5: Yen Carry Trade Monitor")
        st.caption("Tracking the unwinding of the Yen Carry Trade via Yield Spreads and Currency Strength.")
        carry_df = get_carry_trade_data()
    
        if not carry_df.empty:
            carry_df = calculate_fair_value(carry_df)
        
            # Chart 5A: Carry Trade Incentives (USD/JPY vs Yield Spread)
            st.markdown("**📊 Carry Trade Incentives: USD/JPY vs Yield Spread**")
            fig5a = go.Figure()
            if 'usdjpy' in carry_df.columns: 
                fig5a.add_trace(go.Scatter(x=carry_df['timestamp'], y=carry_df['usdjpy'], name="USD/JPY (Price)", line=dict(color='#FFA15A', width=2), yaxis='y1'))
            if 'yield_spread' in carry_df.columns: 
                fig5a.add_trace(go.Bar(x=carry_df['timestamp'], y=carry_df['yield_spread'], name="Yield Spread (US-JP) %", marker_color='#19D3F3', opacity=0.4, yaxis='y2'))
            fig5a.update_layout(
                yaxis=dict(title="USD/JPY", title_font=dict(color='#FFA15A')),
                yaxis2=dict(title="Spread %", title_font=dict(color='#19D3F3'), overlaying='y', side='right'),
                legend=dict(orientation="h", x=0.5, xanchor="center"),
                hovermode="x unified"
            )
            st.plotly_chart(fig5a, width="stretch", key="carry_trade_incentives")
            st.caption("ℹ️ When the spread narrows (bars drop) and USD/JPY falls (line drops), the Carry Trade is UNWINDING.")
        
            # Chart 5B: Fair Value Divergence (The Trap)
            st.markdown("**⚠️ The Trap: Spot Price vs Fair Value Divergence**")
            if 'fair_value' in carry_df.columns:
                latest_dev = carry_df['deviation'].iloc[-1] if 'deviation' in carry_df.columns else 0
                fig5b = go.Figure()
                fig5b.add_trace(go.Scatter(x=carry_df['timestamp'], y=carry_df['fair_value'], name="Fair Value", line=dict(color='grey', width=2, dash='dash')))
                fig5b.add_trace(go.Scatter(x=carry_df['timestamp'], y=carry_df['usdjpy'], name="Spot Price", line=dict(color='#FFA15A', width=2), fill='tonexty', fillcolor='rgba(255,0,0,0.15)'))
                fig5b.update_layout(
                    title=f"USD/JPY Spot vs Fair Value (Current Gap: {latest_dev:+.1f} ¥)",
                    yaxis=dict(title="USD/JPY"),
                    legend=dict(orientation="h", x=0.5, xanchor="center"),
                    hovermode="x unified"
                )
                st.plotly_chart(fig5b, width="stretch", key="carry_trade_trap")
            
                # Insight box
                if latest_dev > 5:
                    st.error(f"🚨 **CRASH RISK**: Yen is {latest_dev:.1f}¥ OVERVALUED vs fundamentals. Rubber band stretched!")
                elif latest_dev > 2:
                    st.warning(f"⚠️ **Divergence Alert**: Spot is {latest_dev:.1f}¥ above fair value.")
                elif latest_dev < -5:
                    st.success(f"🟢 **Undervalued**: Yen is {abs(latest_dev):.1f}¥ below fair value. Potential bounce.")
                else:
                    st.info(f"➡️ **Near Fair Value**: Deviation is {latest_dev:+.1f}¥ (within normal range).")

        # --- Visual 6: Inflation Vector ---
        st.divider()
        st.subheader("Visual 6: The Inflation Vector (Oil vs Silver)")
        st.caption("Comparing Energy (Inflation) vs Precious Metals (Deflation Hedge) to diagnose crash types.")
    
        conn = get_db_connection()
        oil_silver_df = pd.read_sql("SELECT l.timestamp, i.symbol, l.contract_price FROM margin_logs l JOIN instruments i ON l.instrument_id = i.id WHERE i.symbol IN ('CRUDE_OIL', 'SILVER')", conn)
        conn.close()
    
        if not oil_silver_df.empty:
            p_os = oil_silver_df.pivot_table(index='timestamp', columns='symbol', values='contract_price').reset_index()
            fig6 = go.Figure()
            if 'CRUDE_OIL' in p_os.columns: 
                fig6.add_trace(go.Scatter(x=p_os['timestamp'], y=p_os['CRUDE_OIL'], name="Crude Oil (WTI)", line=dict(color='black', width=2), yaxis='y'))
            if 'SILVER' in p_os.columns: 
                fig6.add_trace(go.Scatter(x=p_os['timestamp'], y=p_os['SILVER'], name="Silver", line=dict(color='grey', width=2), yaxis='y2'))
            fig6.update_layout(
                title="Oil vs Silver: Inflation Vector",
                yaxis=dict(title="Oil Price ($)", title_font=dict(color='black')),
                yaxis2=dict(title="Silver Price ($)", title_font=dict(color='grey'), overlaying='y', side='right'),
                legend=dict(orientation="h", x=0.5, xanchor="center"),
                hovermode="x unified"
            )
            st.plotly_chart(fig6, width="stretch", key="inflation_vector")
        
            with st.expander("ℹ️ CHEAT SHEET: How to Interpret the Crash"):
                st.markdown("""
    | Pattern | Oil Price | Silver Price | Diagnosis | Strategy |
    | :--- | :--- | :--- | :--- | :--- |
    | **Correlation** | 📉 DOWN | 📉 DOWN | **Liquidity Crisis (Deflation)** | Cash is King. Wait for RRP to hit $0. |
    | **Divergence** | 📈 UP | 📉 DOWN | **War / Supply Shock (Stagflation)** | Accumulate. Silver is being sold to cover Oil margins. |
    | **Reflation** | 📈 UP | 📈 UP | **Currency Collapse** | All In. The Dollar is losing control. |
                """)

        # --- Visual 7: The Liquidity Valve ---
        st.divider()
        st.subheader("Visual 7: The Liquidity Valve (USD/JPY vs Silver)")
        st.caption("Currency-Commodity link revealing global liquidity flow. 30-day rolling correlation quantifies the relationship.")
    
        valve_df = get_liquidity_valve_data()
    
        if not valve_df.empty and 'usdjpy' in valve_df.columns and 'silver_price' in valve_df.columns:
            # 1. Ensure Correlation Exists
            if 'correlation' not in valve_df.columns:
                valve_df['correlation'] = valve_df['usdjpy'].rolling(30).corr(valve_df['silver_price'])
            
            current_corr = valve_df['correlation'].iloc[-1] if not pd.isna(valve_df['correlation'].iloc[-1]) else 0
        
            # 2. Calculate Slope (The Hook)
            valve_df['corr_slope'] = valve_df['correlation'].diff(3) # 3-day change
            slope = valve_df['corr_slope'].iloc[-1] if not pd.isna(valve_df['corr_slope'].iloc[-1]) else 0
        
            # 3. Define Signal Logic
            signal_color = "gray"
            signal_msg = "WAIT"
            sub_msg = "No clear signal."
        
            if current_corr < -0.4:
                if slope > 0.05:
                    signal_color = "green"
                    signal_msg = "🟢 SNIPER BUY (The Hook)"
                    sub_msg = "Correlation bottomed and turning up. Safe Haven mode active."
                else:
                    signal_color = "red"
                    signal_msg = "🛑 FALLING KNIFE (Wait)"
                    sub_msg = "Correlation diving deep negative. Let it bottom out."
            elif current_corr > -0.1 and current_corr < 0.1 and slope > 0:
                 signal_color = "blue"
                 signal_msg = "🔵 ZERO CROSS (Reconnection)"
                 sub_msg = "Valve repairing. Add to winners."
            elif slope < -0.05:
                 signal_color = "orange"
                 signal_msg = "⚠️ DECOUPLING"
                 sub_msg = "Link breaking. Stand aside."
            else:
                 signal_msg = "⏸️ HOLD"
                 sub_msg = "No clear signal."

            # 4. Display Signal Banner (New UI Element)
            if signal_color == "green": st.success(f"**SIGNAL: {signal_msg}**\n\n{sub_msg}")
            elif signal_color == "red": st.error(f"**SIGNAL: {signal_msg}**\n\n{sub_msg}")
            elif signal_color == "blue": st.info(f"**SIGNAL: {signal_msg}**\n\n{sub_msg}")
            elif signal_color == "orange": st.warning(f"**SIGNAL: {signal_msg}**\n\n{sub_msg}")
            else: st.info(f"**SIGNAL: {signal_msg}**\n\n{sub_msg}")

            # 5. Render Charts
            # Chart 7A: Main Dual-Axis (Full Width)
            fig7 = go.Figure()
            fig7.add_trace(go.Scatter(x=valve_df['timestamp'], y=valve_df['usdjpy'], name='USD/JPY', line=dict(color='orange', width=2), yaxis='y'))
            fig7.add_trace(go.Scatter(x=valve_df['timestamp'], y=valve_df['silver_price'], name='Silver Price ($)', line=dict(color='purple', width=2), yaxis='y2'))
            fig7.update_layout(
                title="The Liquidity Valve: Currency vs Commodity",
                yaxis=dict(title="USD/JPY", title_font=dict(color='orange')),
                yaxis2=dict(title="Silver Price ($)", title_font=dict(color='purple'), overlaying='y', side='right'),
                legend=dict(orientation="h", x=0.5, xanchor="center"),
                hovermode="x unified",
                height=400
            )
            st.plotly_chart(fig7, width="stretch", key="liquidity_valve_main")
        
            # Metrics Row (Horizontal, above correlation chart)
            st.markdown("**📊 30-Day Rolling Correlation Trend**")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Correlation", f"{current_corr:.2f}")
            with m2:
                # DYNAMIC SLOPE INTERPRETATION
                slope_color = "off"
                slope_status = "FLAT"
                if slope > 0.05:
                    slope_status = "📈 RECOVERING"
                    slope_color = "normal"
                elif slope < -0.05:
                    slope_status = "📉 DIVING"
                    slope_color = "inverse"
                else:
                    slope_status = "➡️ FLATTENING"
                    slope_color = "off"
            
                st.metric("Slope (3d)", f"{slope:.3f}", delta=slope_status, delta_color=slope_color)
            with m3:
                # Explicit Explanation in a styled box
                if slope < -0.05:
                    st.error("⚠️ Selling pressure increasing")
                elif slope > 0.05:
                    st.success("✅ Buyers stepping in")
                else:
                    st.info("⏸️ Bottoming out / No momentum")
        

            # Chart 7B: Correlation (Full Width)
            fig7b = go.Figure()
            fig7b.add_trace(go.Scatter(x=valve_df['timestamp'], y=valve_df['correlation'], name='Correlation', line=dict(color='#636EFA', width=2), fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.2)'))
            # Reference lines with annotations positioned inside the chart (not at far right)
            fig7b.add_hline(y=0.5, line_dash="dash", line_color="green", 
                            annotation_text="Valve Open (+0.5)", annotation_position="top left")
            fig7b.add_hline(y=0, line_dash="solid", line_color="grey")
            fig7b.add_hline(y=-0.5, line_dash="dash", line_color="red", 
                            annotation_text="Safe Haven (-0.5)", annotation_position="bottom left")
            fig7b.update_layout(
                yaxis=dict(title="Correlation", range=[-1, 1]),
                hovermode="x unified",
                height=280,
                margin=dict(t=20, r=50)  # Reduced right margin for symmetry
            )
            st.plotly_chart(fig7b, width="stretch", key="liquidity_valve_corr")


            

            # Interpretation Guide
            with st.expander("ℹ️ How to Interpret the Liquidity Valve (Regime Analysis)"):
                st.markdown("""
    This chart reveals **WHERE** the money is coming from. 

    ### **Regime 1: The Bubble (Normal)**
    * **Signal:** Correlation **> +0.5**
    * **What it means:** **"The Valve is Open."**
    * **Mechanism:** Speculators are borrowing cheap Yen (Orange Line Rising) to buy Silver (Purple Line Rising). Silver is essentially "Leveraged Yen."
    * **Action:** ✅ **Ride the Wave.**

    ### **Regime 2: The Trap (Current)**
    * **Signal:** Correlation **+0.2 to -0.3**
    * **What it means:** **"The Valve is Broken."**
    * **Mechanism:** The Yen is still cheap (Orange High), but Silver is crashing. Why? Because **Margin Hikes have cut the pipe.** The money exists, but it cannot reach the asset.
    * **Action:** 🛑 **STAND DOWN.** Do not buy the dip. The price is being manipulated by clearinghouses, not the currency market.

    ### **Regime 3: The Bottom (Safe Haven)**
    * **Signal:** Correlation **< -0.5**
    * **What it means:** **"Real Money takes over."**
    * **Mechanism:** The Yen strengthens (Orange Falls) as loans are repaid, but Silver starts **RISING** (Purple Rises). This means Silver has decoupled from the "Casino" and is now acting as a hard asset shield.
    * **Action:** 🛒 **BUY PHYSICAL.** This is the true bottom.
                """)

        # --- Visual 8: Cross-Asset Correlations ---
        st.divider()
        st.subheader("Visual 8: Cross-Asset Correlations (USD/JPY vs Commodities)")
        st.caption("Analyze the relationship between Currency Flows and Major Commodities.")

        col_v8_1, col_v8_2 = st.columns([1, 3])
    
        with col_v8_1:
            v8_asset = st.radio("Select Commodity Asset:", ('GOLD', 'COPPER', 'CRUDE_OIL', 'NATURAL_GAS'), index=0)
        
            # Domain Commentary Dictionary
            commentary = {
                'GOLD': {
                    'title': "Gold (Anti-Fiat)",
                    'desc': "**Debasement Trade:** If USD/JPY rises (Yen value falls) and Gold rises, the market is fleeing fiat currencies.",
                    'bull': "Gold rising faster than USD/JPY indicates pure flight to safety.",
                    'bear': "Gold falling while USD/JPY rises suggests standard Dollar Strength (Rates Up)."
                },
                'COPPER': {
                    'title': "Copper (Dr. Copper)",
                    'desc': "**Growth Proxy:** Copper measures real economic demand.",
                    'bull': "Copper rising with USD/JPY suggests 'Reflation' (Growth + Easy Money).",
                    'bear': "Copper falling while USD/JPY rises suggests 'Stagflation' (Cost Push Inflation but no Growth)."
                },
                'CRUDE_OIL': {
                    'title': "Crude Oil (Energy)",
                    'desc': "**The Inflation Engine:** Oil drives costs.",
                    'bull': "Oil rising with USD/JPY is the 'Pain Trade' (Imported Inflation for Japan).",
                    'bear': "Oil falling while USD/JPY rises is Deflationary/Recessionary signal."
                },
                'NATURAL_GAS': {
                    'title': "Natural Gas (The Volatility Widow)",
                    'desc': "**Industrial Energy:** Primary heating and industrial source. Highly distinct from Oil due to transport (LNG).",
                    'bull': "NG Rising + USD/JPY Rising = **Industrial Reflation**. Japan (major LNG importer) pays more, weakening Yen further.",
                    'bear': "NG Falling + USD/JPY Rising = **Deflationary Help**. Lower energy costs provide relief to Yen carry trade."
                }
            }
        
            c_data = commentary.get(v8_asset, {})
            st.info(f"**{c_data.get('title')}**\n\n{c_data.get('desc')}")
            st.markdown(f"📈 **Bull Case:** {c_data.get('bull')}")
            st.markdown(f"📉 **Bear Case:** {c_data.get('bear')}")

        with col_v8_2:
            v8_df = get_usdjpy_vs_asset_data(v8_asset)
            holidays = get_dashboard_holidays()
        
            if not v8_df.empty:
                # Stats & Slope
                v8_df['slope'] = v8_df['correlation'].diff(3).fillna(0)
                cur_corr = v8_df['correlation'].iloc[-1] if not pd.isna(v8_df['correlation'].iloc[-1]) else 0
                cur_slope = v8_df['slope'].iloc[-1] if not pd.isna(v8_df['slope'].iloc[-1]) else 0
            
                # --- SIGNAL LOGIC ---
                sig_color = "gray"
                sig_title = "NEUTRAL"
                sig_msg = "No dominant regime detected."
            
                # GOLD
                if v8_asset == 'GOLD':
                    if cur_corr > 0.5:
                        sig_color, sig_title, sig_msg = "green", "🚨 DEBASEMENT DETECTED (LONG GOLD)", "Gold rising WITH USD/JPY. Market fleeing Fiat."
                    elif cur_corr < -0.5:
                         sig_color, sig_title, sig_msg = "red", "📉 DOLLAR HEADWIND (WAIT)", "Gold suppressed by Strong Dollar. Standard behavior."
                    elif cur_slope > 0.1:
                        sig_color, sig_title, sig_msg = "blue", "👀 COUPLING DETECTED", "Correlation turning positive. Watch for breakout."

                # COPPER
                elif v8_asset == 'COPPER':
                    if cur_corr > 0.5:
                        sig_color, sig_title, sig_msg = "green", "🔥 REFLATION TRADE (LONG COPPER)", "Copper & USD/JPY rising. Growth + Liquidity = Bullish."
                    elif cur_corr < -0.5:
                         sig_color, sig_title, sig_msg = "red", "⚠️ STAGFLATION RISK (SHORT COPPER)", "Dollar Up, Copper Down. Growth is choking."
                    elif cur_slope < -0.1:
                         sig_color, sig_title, sig_msg = "orange", "⚠️ GROWTH SCARE", "Correlation breaking down fast."

                # CRUDE OIL
                elif v8_asset == 'CRUDE_OIL':
                    if cur_corr > 0.5:
                        sig_color, sig_title, sig_msg = "green", "🛢️ IMPORTED INFLATION (HEDGE)", "Oil rising with Dollar. Nightmare for importers (Japan)."
                    elif cur_corr < -0.5:
                         sig_color, sig_title, sig_msg = "red", "📉 DEFLATION SIGNAL (SHORT OIL)", "Strong Dollar crushing Energy demand."
            
                # NATURAL GAS
                elif v8_asset == 'NATURAL_GAS':
                    if cur_corr > 0.5:
                        sig_color, sig_title, sig_msg = "green", "🔥 INDUSTRIAL REFLATION (LONG NG)", "NG Rising with Dollar. High demand + strong currency = Overheating."
                    elif cur_corr < -0.5:
                        sig_color, sig_title, sig_msg = "red", "📉 ENERGY GLUT (SHORT NG)", "Strong Dollar crushing global NG demand."
            
                # Display Signal
                if sig_color == 'green': st.success(f"**{sig_title}**\n\n{sig_msg}")
                elif sig_color == 'red': st.error(f"**{sig_title}**\n\n{sig_msg}")
                elif sig_color == 'blue': st.info(f"**{sig_title}**\n\n{sig_msg}")
                elif sig_color == 'orange': st.warning(f"**{sig_title}**\n\n{sig_msg}")
                else: st.info(f"**{sig_title}**\n\n{sig_msg}")

                # Chart 8A (Price)
                fig8 = go.Figure()
                fig8.add_trace(go.Scatter(x=v8_df['timestamp'], y=v8_df['usdjpy'], name='USD/JPY', line=dict(color='orange', width=2), yaxis='y', connectgaps=True))
                fig8.add_trace(go.Scatter(x=v8_df['timestamp'], y=v8_df['price'], name=f'{v8_asset}', line=dict(color='#00CC96', width=2), yaxis='y2', connectgaps=True))
                fig8.update_layout(
                    title=f"USD/JPY vs {v8_asset} Price",
                    yaxis=dict(title="USD/JPY", title_font=dict(color='orange')),
                    yaxis2=dict(title=f"{v8_asset}", title_font=dict(color='#00CC96'), overlaying='y', side='right'),
                    legend=dict(orientation="h", x=0.5, xanchor="center"),
                    hovermode="x unified",
                    xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=holidays)]),
                    height=350, margin=dict(t=30, b=0)
                )
                st.plotly_chart(fig8, width="stretch", key="cross_asset_price")
            
                # Metrics Row (Slope)
                st.markdown(f"**📊 Correlation Dynamics (30D Rolling)**")
                m1, m2, m3 = st.columns(3)
                with m1: st.metric("Correlation", f"{cur_corr:.2f}")
                with m2: 
                    slope_delta_color = "normal" if abs(cur_slope) > 0.02 else "off"
                    delta_str = "Improving" if cur_slope > 0 else "Weakening"
                    st.metric("Slope (3d)", f"{cur_slope:.3f}", delta=delta_str, delta_color=slope_delta_color)
                with m3: st.caption(f"Slope measures how fast the relationship is changing.")

                # Chart 8B (Correlation Area)
                fig8b = go.Figure()
                fig8b.add_trace(go.Scatter(x=v8_df['timestamp'], y=v8_df['correlation'], name='30D Correlation', line=dict(color='#636EFA', width=2), fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.2)', connectgaps=True))
                fig8b.add_hline(y=0.5, line_dash="dash", line_color="green", annotation_text="Positive Lockstep")
                fig8b.add_hline(y=-0.5, line_dash="dash", line_color="red", annotation_text="Negative Inverse")
                fig8b.update_layout(
                    yaxis=dict(title="Correlation", range=[-1, 1], zeroline=True),
                    xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=holidays)]),
                    height=250, margin=dict(t=10, b=0),
                    showlegend=False
                )
                st.plotly_chart(fig8b, width="stretch", key="cross_asset_corr")
            
            else:
                st.warning(f"No data available for {v8_asset}")

        # --- DATA FRESHNESS FOOTER ---
        st.divider()
        st.subheader("📊 Data Freshness")
    
        # Read last sync timestamp from file in data mount (same dir as DB)
        last_sync = "Never"
        db_path = os.getenv("DB_PATH", "liquidity_monitor.db")
        data_dir = os.path.dirname(db_path)
        sync_file = os.path.join(data_dir, ".last_sync.txt") if data_dir else ".last_sync.txt"
    
        if os.path.exists(sync_file):
            with open(sync_file, 'r') as f:
                utc_sync = f.read().strip()
                # Convert UTC to IST (UTC+5:30) for Indian users
                try:
                    from datetime import datetime, timedelta
                    if 'UTC' in utc_sync:
                        utc_dt = datetime.strptime(utc_sync.replace(' UTC', ''), '%Y-%m-%d %H:%M:%S')
                        ist_dt = utc_dt + timedelta(hours=5, minutes=30)
                        last_sync = ist_dt.strftime('%Y-%m-%d %H:%M:%S IST')
                    else:
                        last_sync = utc_sync
                except:
                    last_sync = utc_sync
    
        # Get latest data dates per instrument/series
        conn = get_db_connection()
        margin_dates = pd.read_sql("""
            SELECT i.symbol as instrument, MAX(DATE(l.timestamp)) as latest_date, 'Margin' as type
            FROM margin_logs l 
            JOIN instruments i ON l.instrument_id = i.id 
            GROUP BY i.symbol
        """, conn)
    
        yield_dates = pd.read_sql("""
            SELECT currency || ' ' || tenor as instrument, MAX(DATE(timestamp)) as latest_date, 'Yield' as type
            FROM yield_logs 
            GROUP BY currency, tenor
        """, conn)
        conn.close()
    
        # Combine all data
        all_data = pd.concat([margin_dates, yield_dates], ignore_index=True)
    
        if not all_data.empty:
            # Get unique dates and sort (most recent first)
            all_data['latest_date'] = pd.to_datetime(all_data['latest_date']).dt.date
            unique_dates = sorted(all_data['latest_date'].unique(), reverse=True)
        
            # Calculate staleness (days from today)
            from datetime import date
            today = date.today()
        
            # Build simple HTML table with emojis for accessibility
            html = f'<div style="margin-bottom: 8px; font-size: 13px;"><strong>Last Sync:</strong> {last_sync}</div>'
            html += '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
            html += '<tr>'
        
            # Header row with dates and emoji indicators
            for d in unique_dates[:7]:
                days_old = (today - d).days
                if days_old == 0:
                    emoji = '🟢'
                    status_text = 'Today'
                elif days_old == 1:
                    emoji = '🟢'
                    status_text = '1 day'
                elif days_old <= 3:
                    emoji = '🟡'
                    status_text = f'{days_old} days'
                elif days_old <= 7:
                    emoji = '🟠'
                    status_text = f'{days_old} days'
                else:
                    emoji = '🔴'
                    status_text = f'{days_old} days'
            
                html += f'<th style="padding: 8px; text-align: center; border-bottom: 1px solid #555;">{d}<br>{emoji} {status_text}</th>'
        
            html += '</tr><tr>'
        
            # Data row with instruments
            for d in unique_dates[:7]:
                instruments = all_data[all_data['latest_date'] == d]['instrument'].tolist()
                instruments_html = '<br>'.join(instruments) if instruments else '-'
                html += f'<td style="padding: 8px; text-align: center; vertical-align: top;">{instruments_html}</td>'
        
            html += '</tr></table>'
        
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.caption(f"**Last Sync:** {last_sync}")
            st.text("No data available")

    else:
        st.warning("Please run data backfill.")

elif dashboard_mode == "US Treasury Monitor":
    render_treasury_monitor()

elif dashboard_mode == "Precious Metal ETF Monitor":
    st.sidebar.markdown("---")
    st.sidebar.title("🛡️ PM ETF Monitor")
    st.sidebar.caption("Real-time tracking of NSE-traded Gold and Silver ETFs against their fair value and global spot parity.")
    st.sidebar.caption("Comparing ETF Traded Price (NSE) vs Indicative NAV (iNAV).")
    
    # Create Tabs
    tab1, tab2 = st.tabs(["🥈 SilverBees (Silver)", "🥇 GoldBees (Gold)"])
    
    with tab1:
        render_silver_monitor()
        
    with tab2:
        render_gold_monitor()

if __name__ == "__main__":
    # Standard Streamlit entry point when run via 'streamlit run'
    pass



