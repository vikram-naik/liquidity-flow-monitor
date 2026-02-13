import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.database import get_db_connection


def load_stock_list():
    """Load list of available stocks from DB"""
    conn = get_db_connection()
    df = pd.read_sql("SELECT DISTINCT symbol FROM nse_delivery_log ORDER BY symbol", conn)
    conn.close()
    return df['symbol'].tolist()


def get_stock_history(symbol):
    """Fetch historical data for a stock"""
    conn = get_db_connection()
    df = pd.read_sql("""
        SELECT * FROM nse_delivery_log 
        WHERE symbol = ? 
        ORDER BY record_date ASC
    """, conn, params=(symbol,))
    conn.close()
    return df


def get_available_dates():
    """Get the list of distinct dates available in the DB"""
    conn = get_db_connection()
    df = pd.read_sql("SELECT DISTINCT record_date FROM nse_delivery_log ORDER BY record_date DESC", conn)
    conn.close()
    return df['record_date'].tolist()


def get_report_data(report_type, timeframe='daily', min_turnover_cr=5):
    """
    Generate market reports using DELTA-based logic.
    
    Timeframes:
      - daily: Compare latest date vs previous date
      - weekly: Average deltas over last 5 trading days
      - monthly: Average deltas over last 22 trading days
    """
    conn = get_db_connection()
    
    # Get available dates
    dates = pd.read_sql("SELECT DISTINCT record_date FROM nse_delivery_log ORDER BY record_date DESC", conn)['record_date'].tolist()
    
    if not dates:
        conn.close()
        return pd.DataFrame(), ""

    latest_date = dates[0]

    if timeframe == 'daily':
        # Single day: use latest date's deltas directly
        base_query = f"""
            SELECT symbol, price_close, delivery_pct, volume_total, delivery_qty,
                   price_change_pct, volume_change_pct, delivery_change_pct
            FROM nse_delivery_log 
            WHERE record_date = '{latest_date}'
        """
        label = f"Date: {latest_date}"
        
    elif timeframe == 'weekly':
        # Last 5 trading days: average deltas
        lookback_dates = dates[:5]
        if len(lookback_dates) < 2:
            conn.close()
            return pd.DataFrame(), "Not enough data for weekly view"
        
        date_list = "','".join(lookback_dates)
        base_query = f"""
            SELECT symbol, 
                   ROUND(AVG(price_close), 2) as price_close,
                   ROUND(AVG(delivery_pct), 2) as delivery_pct,
                   ROUND(AVG(volume_total), 0) as volume_total,
                   ROUND(AVG(delivery_qty), 0) as delivery_qty,
                   ROUND(AVG(price_change_pct), 2) as price_change_pct,
                   ROUND(AVG(volume_change_pct), 2) as volume_change_pct,
                   ROUND(AVG(delivery_change_pct), 2) as delivery_change_pct
            FROM nse_delivery_log 
            WHERE record_date IN ('{date_list}')
            GROUP BY symbol
        """
        label = f"Weekly Avg ({lookback_dates[-1]} to {lookback_dates[0]})"
        
    else:  # monthly
        lookback_dates = dates[:22]
        if len(lookback_dates) < 5:
            conn.close()
            return pd.DataFrame(), "Not enough data for monthly view"
        
        date_list = "','".join(lookback_dates)
        base_query = f"""
            SELECT symbol, 
                   ROUND(AVG(price_close), 2) as price_close,
                   ROUND(AVG(delivery_pct), 2) as delivery_pct,
                   ROUND(AVG(volume_total), 0) as volume_total,
                   ROUND(AVG(delivery_qty), 0) as delivery_qty,
                   ROUND(AVG(price_change_pct), 2) as price_change_pct,
                   ROUND(AVG(volume_change_pct), 2) as volume_change_pct,
                   ROUND(AVG(delivery_change_pct), 2) as delivery_change_pct
            FROM nse_delivery_log 
            WHERE record_date IN ('{date_list}')
            GROUP BY symbol
        """
        label = f"Monthly Avg ({lookback_dates[-1]} to {lookback_dates[0]})"

    all_df = pd.read_sql(base_query, conn)
    conn.close()

    if all_df.empty:
        return pd.DataFrame(), label

    # --- Compute Turnover & Apply Min Filter ---
    all_df['turnover_cr'] = (all_df['price_close'] * all_df['volume_total']) / 1e7  # ₹ Crores
    all_df = all_df[all_df['turnover_cr'] >= min_turnover_cr]
    
    if all_df.empty:
        return pd.DataFrame(), label

    # --- Apply Report Filters ---
    
    if report_type == 'high_conviction':
        # High Conviction = Delivery % INCREASING + Volume INCREASING
        # Both deltas positive — this filters out trade-to-trade stocks
        # which have high absolute delivery but no change.
        result = all_df[
            (all_df['delivery_change_pct'] > 0) & 
            (all_df['volume_change_pct'] > 0)
        ].sort_values('delivery_change_pct', ascending=False).head(25)
        
    elif report_type == 'accumulation':
        # Accumulation = Smart money buying gradually without moving price.
        # Price flat-to-slightly-up (-0.5% to +2%), delivery rising, volume rising.
        # Excludes stocks where price is clearly falling (those go to Distribution).
        result = all_df[
            (all_df['delivery_change_pct'] > 0) &
            (all_df['volume_change_pct'] > 0) &
            (all_df['price_change_pct'] >= -0.5) &
            (all_df['price_change_pct'] <= 2)
        ].sort_values('delivery_change_pct', ascending=False).head(25)
        
    elif report_type == 'distribution':
        # Distribution = Informed selling with conviction.
        # Price meaningfully falling (< -1%), yet delivery AND volume both rising.
        # This signals large holders are offloading — not panic (which has low delivery).
        result = all_df[
            (all_df['price_change_pct'] < -1) &
            (all_df['delivery_change_pct'] > 0) &
            (all_df['volume_change_pct'] > 0)
        ].sort_values('delivery_change_pct', ascending=False).head(25)
        
    elif report_type == 'speculative':
        # Speculative = High Volume + LOW Delivery % (intraday churn)
        # volume up but delivery % going down
        result = all_df[
            (all_df['volume_change_pct'] > 10) &
            (all_df['delivery_change_pct'] < 0)
        ].sort_values('volume_change_pct', ascending=False).head(25)
    else:
        result = all_df.head(25)

    # Rename columns for display
    display_cols = {
        'symbol': 'Symbol',
        'price_close': 'Close (₹)',
        'price_change_pct': 'Price Δ%',
        'delivery_pct': 'Deliv %',
        'delivery_change_pct': 'Deliv Δ (pts)',
        'volume_total': 'Volume',
        'volume_change_pct': 'Vol Δ%',
        'delivery_qty': 'Deliv Qty',
        'turnover_cr': 'Turnover (₹Cr)',
    }
    result['turnover_cr'] = result['turnover_cr'].round(1)
    result = result.rename(columns=display_cols)
    col_order = [c for c in display_cols.values() if c in result.columns]
    
    return result[col_order], label


def render_nse_monitor():
    st.title("🇮🇳 NSE Delivery & Volume Monitor")
    
    # Load stocks (needed for both tabs)
    stocks = load_stock_list()
    if not stocks:
        st.warning("No data found in database. Run the backfill agent: `python src/agents/nse_agent.py --backfill 30`")
        return

    # Tabs
    tab1, tab2 = st.tabs(["📈 Single Stock Analysis", "📊 Market Reports"])
    
    # ---- TAB 1: Single Stock Analysis ----
    with tab1:
        # Stock selector INSIDE the tab
        selected_stock = st.selectbox("🔍 Select Stock", stocks, 
                                       index=stocks.index('RELIANCE') if 'RELIANCE' in stocks else 0,
                                       key="nse_stock_selector")
        
        if selected_stock:
            df = get_stock_history(selected_stock)
            
            if not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                
                # Metrics Row
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    price_change = latest['price_close'] - prev['price_close']
                    st.metric("💰 Price", f"₹{latest['price_close']:.2f}", 
                              f"{latest.get('price_change_pct', 0):.2f}%")
                with c2:
                    st.metric("📦 Delivery %", f"{latest['delivery_pct']:.1f}%", 
                              f"{latest['delivery_change_pct']:+.1f} pts")
                with c3:
                    st.metric("📊 Volume", f"{latest['volume_total']:,}", 
                              f"{latest['volume_change_pct']:+.1f}%")
                with c4:
                    st.metric("🚚 Deliv Qty", f"{latest['delivery_qty']:,}")
                with c5:
                    # Signal indicator
                    price_up = latest.get('price_change_pct', 0) >= 0
                    deliv_up = latest['delivery_change_pct'] > 0
                    vol_up = latest['volume_change_pct'] > 0
                    
                    if deliv_up and vol_up and price_up:
                        signal = "🟢 Bullish"
                    elif deliv_up and vol_up and not price_up:
                        signal = "🔴 Distribution"
                    elif not deliv_up and vol_up:
                        signal = "🟡 Speculative"
                    else:
                        signal = "⚪ Neutral"
                    st.metric("📡 Signal", signal)
                
                st.divider()
                
                # --- Combined Chart: Price + Delivery (top) / Volume (bottom), shared x-axis ---
                st.subheader("Price, Delivery & Volume")
                
                fig = make_subplots(
                    rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                    row_heights=[0.65, 0.35],
                    specs=[[{"secondary_y": True}], [{}]]
                )
                
                # Row 1: Price line (left y) + Delivery % line (right y)
                fig.add_trace(go.Scatter(
                    x=df['record_date'], y=df['price_close'], 
                    name='Close Price', line=dict(color='#636EFA', width=2)),
                    row=1, col=1, secondary_y=False)
                fig.add_trace(go.Scatter(
                    x=df['record_date'], y=df['delivery_pct'], 
                    name='Delivery %',
                    line=dict(color='#00CC96', width=2, dash='dot'), 
                    fill='tozeroy', fillcolor='rgba(0, 204, 150, 0.08)'),
                    row=1, col=1, secondary_y=True)
                
                # Row 2: Volume + Delivery Qty bars
                fig.add_trace(go.Bar(
                    x=df['record_date'], y=df['volume_total'], 
                    name='Total Volume', marker_color='rgba(239, 85, 59, 0.5)'),
                    row=2, col=1)
                fig.add_trace(go.Bar(
                    x=df['record_date'], y=df['delivery_qty'], 
                    name='Delivery Qty', marker_color='#FFA15A'),
                    row=2, col=1)
                
                # Axis labels
                fig.update_yaxes(title_text='Price (₹)', row=1, col=1, secondary_y=False)
                fig.update_yaxes(title_text='Delivery %', row=1, col=1, secondary_y=True, range=[0, 100])
                fig.update_yaxes(title_text='Volume', row=2, col=1)
                fig.update_xaxes(type='category', row=2, col=1)
                
                # Enable crosshair spike lines on both subplots
                fig.update_xaxes(showspikes=True, spikemode='across', spikethickness=1,
                                 spikecolor='grey', spikesnap='cursor', spikedash='dot')
                fig.update_yaxes(showspikes=True, spikethickness=1,
                                 spikecolor='grey', spikesnap='cursor', spikedash='dot')
                
                fig.update_layout(
                    barmode='overlay',
                    hovermode='x unified',
                    legend=dict(orientation='h', y=1.03, x=0.5, xanchor='center'),
                    height=620,
                    margin=dict(t=30, b=20)
                )
                st.plotly_chart(fig, use_container_width=True)
                
            else:
                st.info("No data for this stock.")
                
    # ---- TAB 2: Market Reports ----
    with tab2:
        # Controls row
        tf_col, rpt_col, filter_col = st.columns([1, 3, 1])
        with tf_col:
            timeframe = st.radio("⏱️ Timeframe", ["Daily", "Weekly", "Monthly"], index=0)
        
        with rpt_col:
            report_mode = st.radio("📋 Report Type", [
                "🎯 High Conviction (Deliv↑ + Vol↑)",
                "🟢 Accumulation (Range + Deliv↑ + Vol↑)",
                "🔴 Distribution (Price↓ + Deliv↑ + Vol↑)", 
                "🟡 Speculative Activity (Vol↑ + Deliv↓)"
            ])
        
        with filter_col:
            min_turnover = st.slider("💰 Min Turnover (₹Cr)", min_value=1, max_value=1000, value=5, step=5,
                                      help="Filter out low-value stocks. Only show stocks with daily turnover above this threshold.")
        
        tf_map = {"Daily": "daily", "Weekly": "weekly", "Monthly": "monthly"}
        tf_key = tf_map[timeframe]
        
        st.divider()
        
        if "High Conviction" in report_mode:
            report_df, label = get_report_data('high_conviction', tf_key, min_turnover)
            st.subheader(f"🎯 High Conviction Delivery")
            st.caption(f"Stocks where both Delivery % **and** Volume are **increasing** (delta > 0). | {label}")
            with st.expander("💡 How to read this?"):
                st.markdown("""
                **What it means:** When delivery % **increases** along with volume, it signals that participants are **taking delivery** (holding), not just day-trading.
                
                - **High Deliv Δ + High Vol Δ** = Strong institutional/HNI activity
                - Filters out trade-to-trade stocks (high absolute delivery but no change)
                """)
                
        elif "Accumulation" in report_mode:
            report_df, label = get_report_data('accumulation', tf_key, min_turnover)
            st.subheader(f"🟢 Accumulation Candidates")
            st.caption(f"Delivery↑ + Volume↑ + Price flat (-0.5% to +2%). | {label}")
            with st.expander("💡 How to read this?"):
                st.markdown("""
                **What it means:** Smart money accumulates gradually — they buy without moving the price much.
                
                - **Price stable** (-2% to +2% change) = No panic, no euphoria
                - **Delivery increasing** = Buying and holding
                - **Volume increasing** = More participants entering
                
                This is the classic accumulation pattern before a breakout.
                """)
                
        elif "Distribution" in report_mode:
            report_df, label = get_report_data('distribution', tf_key, min_turnover)
            st.subheader(f"🔴 Distribution / Selling Pressure")
            st.caption(f"Price↓ (< -1%) + Delivery↑ + Volume↑ — conviction selling. | {label}")
            with st.expander("💡 How to read this?"):
                st.markdown("""
                **What it means:** When price is **falling** but delivery % and volume are **rising**, it signals that large players are **selling with conviction** (taking delivery to deliver to buyers).
                
                - This is NOT panic selling (which would have low delivery %)
                - This is **informed distribution** — institutions offloading
                """)
                
        else:  # Speculative
            report_df, label = get_report_data('speculative', tf_key, min_turnover)
            st.subheader(f"🟡 Speculative Activity")
            st.caption(f"Volume↑ > 10% + Delivery↓ — intraday churn. | {label}")
            with st.expander("💡 How to read this?"):
                st.markdown("""
                **What it means:** High volume but **falling** delivery % means the stock is being churned by day-traders and speculators.
                
                - **Caution:** Moves driven by speculation often reverse sharply
                - No holding conviction — positions are squared off same day
                """)
        
        if not report_df.empty:
            st.dataframe(report_df, use_container_width=True, hide_index=True)
        else:
            st.info("No stocks matched the criteria for the selected timeframe.")
