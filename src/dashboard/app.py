
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys
import os
import time
from datetime import timedelta

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.analytics import calculate_squeeze_score, check_systemic_alert, get_db_connection, get_carry_trade_data

st.set_page_config(page_title="Global Liquidity Flow Monitor", layout="wide")

st.title("🌊 Global Liquidity Flow Monitor")
st.caption("Identifying Induced Shakeouts via Margin/Yield Correlation")

# --- Macro Traffic Light ---
macro_df = get_carry_trade_data()
if not macro_df.empty:
    latest = macro_df.iloc[-1]
    
    # Eval conditions
    usdjpy_drop = 0
    if len(macro_df) > 1:
        prev = macro_df.iloc[-2]
        if 'usdjpy' in latest and 'usdjpy' in prev and prev['usdjpy'] > 0:
            usdjpy_drop = (latest['usdjpy'] - prev['usdjpy']) / prev['usdjpy']
    
    jp_yield = latest.get('jp_10y', 0)
    us_yield = latest.get('us_10y', 0)
    
    if jp_yield > 1.2 or usdjpy_drop < -0.01:
        st.error("🚨 **CARRY TRADE COLLAPSE**: Yen Surge or Japan Yield Spike detected. Liquidity unwinding risk is CRITICAL.")
    elif us_yield > 4.5:
        st.warning("⚠️ **US TREASURY SQUEEZE**: US 10Y Yields above 4.5%. Debt service pressure is RISING.")
    else:
        st.success("✅ **GLOBAL LIQUIDITY STABLE**: Benchmark yields and FX within safe parameters.")
else:
    st.info("⌛ Synchronizing Macro Flow data...")

# Metric Row
alert_status = check_systemic_alert()

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
            
        color_status = "normal"
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

# Analytics Data
scores_df = calculate_squeeze_score()

# Normalize instrument names to avoid duplicates (e.g., 'Silver' vs 'SILVER')
if not scores_df.empty:
    scores_df['instrument'] = scores_df['instrument'].str.upper()

# Filter for CME instruments (Metals + Equities)
if not scores_df.empty:
    cme_instruments = ['SILVER', 'GOLD', 'COPPER', 'ES', 'NQ']
    scores_df = scores_df[scores_df['instrument'].isin(cme_instruments)]
    # Ensure exchange is CME for these instruments if not already filtered by calculate_squeeze_score
    scores_df = scores_df[scores_df['exchange'] == 'CME']

# --- Global Controls ---
# Get list of unique normalized instruments
instrument_list = sorted(scores_df['instrument'].unique().tolist()) if not scores_df.empty else []
default_ix = 0
if 'SILVER' in instrument_list:
    default_ix = instrument_list.index('SILVER')

# Sidebar Controls
st.sidebar.header("Controls")

if not scores_df.empty:
    # 1. Timeline Filter
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
        # Filter the main dataframe
        scores_df = scores_df[
            (scores_df['timestamp'].dt.date >= start_filter) & 
            (scores_df['timestamp'].dt.date <= end_filter)
        ]
    
    # Standardize names for display
    instruments = scores_df['instrument'].unique()
    selected_instrument = st.sidebar.selectbox("Select Instrument for Deep Dive", sorted(instruments))
    
    # Filter data
    inst_df = scores_df[scores_df['instrument'] == selected_instrument].sort_values('timestamp')
    selected_exchange = 'CME'
    
    # --- Visual 1: Deep Dive (Dual Axis) ---
    st.subheader(f"Visual 1: {selected_instrument} ({selected_exchange}) - Price vs. Margin Requirements %")
    
    if not inst_df.empty:
        fig1 = go.Figure()
        
        # Trace 1: Price (Left Axis)
        fig1.add_trace(go.Scatter(
            x=inst_df['timestamp'],
            y=inst_df['price_now'],
            name=f"Price ({selected_exchange})",
            line=dict(color='#636EFA', width=3),
            yaxis='y1'
        ))
        
        # Trace 2: Margin % (Right Axis)
        fig1.add_trace(go.Bar(
            x=inst_df['timestamp'],
            y=inst_df['margin_now'],
            name="Margin Requirement %",
            marker_color='#EF553B',
            opacity=0.6,
            yaxis='y2'
        ))
        
        fig1.update_layout(
            title=f"{selected_instrument} Price vs Leverage Requirements ({selected_exchange})",
            xaxis=dict(title="Time"),
            yaxis=dict(title="Price", title_font=dict(color='#636EFA'), tickfont=dict(color='#636EFA')),
            yaxis2=dict(
                title="Margin %",
                title_font=dict(color='#EF553B'),
                tickfont=dict(color='#EF553B'),
                overlaying='y',
                side='right',
                ticksuffix="%"
            ),
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0.5, xanchor="center")
        )
        st.plotly_chart(fig1, use_container_width=True)
    else:
        st.info(f"No data for {selected_instrument} on {selected_exchange}")

    # --- Visual 2: Global Stress Monitor ---
    st.subheader("Visual 2: Global Stress Monitor (All-Asset Multiplier)")
    st.caption("Comparing Sustained Margin Pressure across Metals and Equities to identify liquidity flows.")
    
    if not scores_df.empty:
        # We use scores_df directly as it already filtered by timeline
        fig2 = px.line(
            scores_df,
            x='timestamp',
            y='pressure_index',
            color='instrument',
            title="Margin Pressure Multiplier (Current vs 30D Rolling Min)",
            labels={'pressure_index': 'Margin Pressure Multiplier', 'timestamp': 'Time'},
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        
        # Add Reference Lines
        fig2.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="Baseline (1.0x)")
        fig2.add_hline(y=1.5, line_dash="dash", line_color="orange", annotation_text="Stress Threshold (1.5x)")
        
        fig2.update_layout(
            yaxis=dict(ticksuffix="x"),
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0.5, xanchor="center")
        )
        
        # Enhanced Tooltip logic is native to px.line + hovermode unified
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No data available for Global Stress Monitor.")

    # --- Visual 3: Correlation Matrix ---
    st.subheader("Visual 3: Global Margin Correlation")
    
    with st.expander("ℹ️ How to read this matrix?"):
        st.markdown("""
        **What this shows:** How margin requirements move together across assets.
        *   🟦 **Blue (1.0)**: **Lockstep Move**. If one goes up, ours goes up. (High Systemic Risk).
        *   ⬜ **White (0.0)**: **Uncorrelated**. They move independently.
        *   🧧 **Red (-1.0)**: **Inverse**. If one goes up, other goes down.
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
        pct_change_df = pivot_df.pct_change()
        # Drop rows with all NaN (first row after pct_change)
        pct_change_df = pct_change_df.dropna(how='all')
        if not pct_change_df.empty:
            corr = pct_change_df.corr()
            
            fig3 = px.imshow(
                corr, 
                text_auto='.2f', 
                color_continuous_scale="RdBu",
                zmin=-1,
                zmax=1,
                aspect="auto"
            )
            st.plotly_chart(fig3, use_container_width=True)

    # --- Visual 4: Global Liquidity Map ---
    st.subheader("Visual 4: Global Liquidity Map (Margin Stress)")
    
    # Calculate "Stress" as % Increase in Margin (last 24h) - Using latest from score_df
    latest_scores = scores_df.sort_values('timestamp').groupby('instrument').tail(1)
    
    if 'asset_class' in latest_scores.columns:
        fig4 = px.bar(
            latest_scores, 
            x='instrument', 
            y='shock_score',
            color='asset_class',
            title="Instant Margin Hikes (24h) by Asset Class",
            text_auto='.1%',
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        fig4.update_layout(yaxis=dict(title="Shock Score (Hike %)"))
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.write("Asset Class data missing for Liquidity Map.")
        
    # --- Visual 5: Carry Trade Monitor ---
    st.subheader("Visual 5: Yen Carry Trade Monitor")
    st.caption("Monitoring the unwinding of the Yen Carry Trade via Yield Spreads and Currency Strength.")
    
    carry_df = get_carry_trade_data()
    
    if not carry_df.empty:
        c1, c2 = st.columns(2)
        
        with c1:
            st.markdown("**Yield Spread (US 10Y - JP 10Y)**")
            fig5a = go.Figure()
            
            if 'usdjpy' in carry_df.columns:
                fig5a.add_trace(go.Scatter(
                    x=carry_df['timestamp'],
                    y=carry_df['usdjpy'],
                    name="USD/JPY Price",
                    line=dict(color='#FFA15A', width=2),
                    yaxis='y1'
                ))
            
            if 'yield_spread' in carry_df.columns:
                fig5a.add_trace(go.Bar(
                    x=carry_df['timestamp'],
                    y=carry_df['yield_spread'],
                    name="Yield Spread %",
                    marker_color='#19D3F3',
                    opacity=0.3,
                    yaxis='y2'
                ))
            
            fig5a.update_layout(
                title="Carry Trade Incentives",
                yaxis=dict(title="USD/JPY", title_font=dict(color='#FFA15A')),
                yaxis2=dict(
                    title="Spread (US-JP) %", 
                    title_font=dict(color='#19D3F3'),
                    overlaying='y',
                    side='right'
                ),
                legend=dict(orientation="h", x=0.5, xanchor="center")
            )
            st.plotly_chart(fig5a, use_container_width=True)
            
        with c2:
            st.markdown("**Benchmark Yields Overlay**")
            fig5b = go.Figure()
             
            if 'us_10y' in carry_df.columns:
                fig5b.add_trace(go.Scatter(
                    x=carry_df['timestamp'],
                    y=carry_df['us_10y'],
                    name="US 10Y",
                    line=dict(color='#00CC96', width=2)
                ))
            
            if 'jp_10y' in carry_df.columns:
                fig5b.add_trace(go.Scatter(
                    x=carry_df['timestamp'],
                    y=carry_df['jp_10y'],
                    name="JP 10Y",
                    line=dict(color='#AB63FA', width=2) 
                ))
            
            fig5b.update_layout(
                title="US vs JP 10Y Yields",
                yaxis=dict(title="Yield %"),
                legend=dict(orientation="h", x=0.5, xanchor="center")
            )
            st.plotly_chart(fig5b, use_container_width=True)
            
        st.info("ℹ️ **Interpretation**: If Spread narrows (Bars drop) and JPY Strengthens (Line drops), the Carry Trade is **UNWINDING**.")
    else:
        st.warning("Insufficient Carry Trade data.")

else:
    st.sidebar.warning("No data available. Please run backfill.")
    st.warning("Not enough data to calculate analytics yet (Need 2+ data points).")

# Raw Data
with st.expander("Raw Logs"):
    conn = get_db_connection()
    st.dataframe(pd.read_sql("SELECT * FROM margin_logs ORDER BY timestamp DESC", conn))
    st.dataframe(pd.read_sql("SELECT * FROM yield_logs ORDER BY timestamp DESC", conn))
    conn.close()
