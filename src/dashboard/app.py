
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

from src.analytics import calculate_squeeze_score, check_systemic_alert, get_db_connection, get_carry_trade_data, get_credit_stress_data, check_credit_stress, calculate_fair_value

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
    cme_instruments = ['SILVER', 'GOLD', 'COPPER', 'CRUDE_OIL', 'ES', 'NQ']
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
        

        # Get holidays once
        holidays = get_dashboard_holidays()

        fig1.update_layout(
            title=f"{selected_instrument} Price vs Leverage Requirements ({selected_exchange})",
            xaxis=dict(
                title="Time",
                rangebreaks=[
                    dict(bounds=["sat", "mon"]), # hide weekends
                    dict(values=holidays) # hide holidays
                ]
            ),
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
    st.divider()
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
        
        # Get holidays if not already fetched
        if 'holidays' not in locals():
            holidays = get_dashboard_holidays()

        fig2.update_layout(
            yaxis=dict(ticksuffix="x"),
            xaxis=dict(
                rangebreaks=[
                    dict(bounds=["sat", "mon"]), 
                    dict(values=holidays)
                ]
            ),
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0.5, xanchor="center")
        )
        
        # Enhanced Tooltip logic is native to px.line + hovermode unified
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No data available for Global Stress Monitor.")

    # --- Visual 3: Correlation Matrix ---
    st.divider()
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
    st.divider()
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
    
    # --- Row 3: Macro Plumbing (The Engine Room) ---
    st.divider()
    
    # Fetch credit stress data
    credit_df = get_credit_stress_data(days=90)
    credit_status = check_credit_stress()
    
    if not credit_df.empty:
        # Section Title
        st.subheader("🔧 Macro Plumbing (The Engine Room)")
        st.caption("Tracking systemic risk signals from Credit Markets, Liquidity Reservoir, and Dollar Strength.")
        
        # Engine Room Status Box (centered, between title and charts)
        status_msg = credit_status['message']
        if credit_status.get('buffer_depleted') or credit_status.get('credit_freeze'):
            st.error(f"**🔧 Engine Room Status:** {status_msg}")
        elif credit_status.get('dollar_squeeze') or credit_status.get('risk_off'):
            st.warning(f"**🔧 Engine Room Status:** {status_msg}")
        elif credit_status.get('liquidity_injection'):
            st.success(f"**🔧 Engine Room Status:** {status_msg}")
        else:
            st.info(f"**🔧 Engine Room Status:** {status_msg}")
        
        # 4-column metric layout
        metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
        
        # --- Metric 1: The Credit Canary (HY Spread) ---
        with metric_col1:
            st.markdown("### 🐤 The Credit Canary")
            st.caption("High Yield Spread - Early warning for credit stress")
            
            hy_val = credit_status.get('hy_spread_current')
            hy_trend = credit_status.get('hy_spread_trend', 'Stable')
            
            if hy_val is not None:
                # Color based on level
                if hy_val > 4.0:
                    st.error(f"**{hy_val:.2f}%**")
                    st.caption("🚨 BANKING FREEZE")
                elif hy_val > 3.5:
                    st.warning(f"**{hy_val:.2f}%**")
                    st.caption(f"⚠️ Watch Zone ({hy_trend})")
                else:
                    st.success(f"**{hy_val:.2f}%**")
                    st.caption(f"✅ Healthy ({hy_trend})")
            else:
                st.info("--")
                st.caption("No data")
            
            # Mini area chart
            if 'hy_spread' in credit_df.columns:
                fig_credit = go.Figure()
                fig_credit.add_trace(go.Scatter(
                    x=credit_df['timestamp'],
                    y=credit_df['hy_spread'],
                    fill='tozeroy',
                    line=dict(color='#AB63FA', width=2),
                    fillcolor='rgba(171, 99, 250, 0.3)'
                ))
                fig_credit.add_hline(y=4.0, line_dash="dash", line_color="red")
                fig_credit.update_layout(
                    height=200,
                    margin=dict(l=20, r=20, t=20, b=20),
                    yaxis=dict(title="", ticksuffix="%"),
                    xaxis=dict(title="", showticklabels=False),
                    showlegend=False
                )
                st.plotly_chart(fig_credit, use_container_width=True)
            
            # Help tooltip
            with st.expander("ℹ️ How to Read"):
                st.markdown("""
**Measures the fear in the banking system.**

- 🟢 **< 3.5%**: Banks are calm. Credit is flowing.
- 🟠 **3.5% - 4.5%**: Watch zone. Stress building.
- 🔴 **> 4.5%**: Credit Freeze. Banks stopped lending. Stocks usually crash 3-5 days later.
                """)
        
        # --- Metric 2: The Liquidity Reservoir (RRP) ---
        with metric_col2:
            st.markdown("### 🏦 Liquidity Reservoir")
            st.caption("Overnight Reverse Repo - Where banks park cash")
            
            rrp_val = credit_status.get('rrp_current')
            rrp_delta = credit_status.get('rrp_delta_7d', 0) or 0
            rrp_trend = credit_status.get('rrp_trend', 'Stable')
            
            if rrp_val is not None:
                # Format in billions
                rrp_display = f"${rrp_val:.0f}B" if rrp_val < 1000 else f"${rrp_val/1000:.2f}T"
                delta_display = f"{rrp_delta:+.0f}B (7d)"
                
                # RRP < 50B = Buffer Depleted (BUY signal)
                if rrp_val < 50:
                    st.error(f"**{rrp_display}**")
                    st.caption("🚨 BUFFER DEPLETED - QE IMMINENT")
                elif rrp_delta < -10:
                    st.success(f"**{rrp_display}**")
                    st.caption(f"💧 {delta_display} - INJECTION")
                elif rrp_delta > 50:
                    st.warning(f"**{rrp_display}**")
                    st.caption(f"🏦 {delta_display} - HOARDING")
                else:
                    st.info(f"**{rrp_display}**")
                    st.caption(f"➡️ {delta_display} - {rrp_trend}")
            else:
                st.info("--")
                st.caption("No data - Run backfill")
            
            # Mini line chart
            if 'rrp' in credit_df.columns:
                fig_rrp = go.Figure()
                # Color based on delta
                line_color = '#00CC96' if rrp_delta < 0 else '#EF553B'
                fig_rrp.add_trace(go.Scatter(
                    x=credit_df['timestamp'],
                    y=credit_df['rrp'],
                    line=dict(color=line_color, width=2),
                    mode='lines'
                ))
                fig_rrp.update_layout(
                    height=200,
                    margin=dict(l=20, r=20, t=20, b=20),
                    yaxis=dict(title="", tickprefix="$", ticksuffix="B"),
                    xaxis=dict(title="", showticklabels=False),
                    showlegend=False
                )
                st.plotly_chart(fig_rrp, use_container_width=True)
            
            # Help tooltip
            with st.expander("ℹ️ How to Read"):
                st.markdown("""
**The system's spare cash buffer.**

- 🟢 **Falling**: Cash is entering the market (Bullish).
- 🟠 **Near Zero (~$10B)**: Buffer Depleted. The system is running on fumes.
- 🚨 **< $50B**: Any shock now will force the Fed to print money (QE). Major BUY signal for metals.
                """)
        
        # --- Metric 3: The Steamroller (DXY) ---
        with metric_col3:
            st.markdown("### 💵 The Steamroller")
            st.caption("Dollar Index - When it rises, everything else suffers")
            
            dxy_val = credit_status.get('dxy_current')
            dxy_trend = credit_status.get('dxy_trend', 'Stable')
            
            if dxy_val is not None:
                if dxy_val > 115:
                    st.error(f"**{dxy_val:.1f}**")
                    st.caption("💵 WRECKING BALL (>115)")
                elif dxy_val < 110:
                    st.success(f"**{dxy_val:.1f}**")
                    st.caption(f"✅ Safe (<110)")
                else:
                    st.warning(f"**{dxy_val:.1f}**")
                    st.caption(f"⚠️ Watch Zone ({dxy_trend})")
            else:
                st.info("--")
                st.caption("No data")
            
            # Mini line chart
            if 'dxy' in credit_df.columns:
                fig_dxy = go.Figure()
                line_color = '#00CC96' if dxy_trend == 'Rising' else '#EF553B'
                fig_dxy.add_trace(go.Scatter(
                    x=credit_df['timestamp'],
                    y=credit_df['dxy'],
                    line=dict(color=line_color, width=2),
                    mode='lines'
                ))
                fig_dxy.add_hline(y=115, line_dash="dash", line_color="orange")
                fig_dxy.update_layout(
                    height=200,
                    margin=dict(l=20, r=20, t=20, b=20),
                    yaxis=dict(title=""),
                    xaxis=dict(title="", showticklabels=False),
                    showlegend=False
                )
                st.plotly_chart(fig_dxy, use_container_width=True)
            
            # Help tooltip
            with st.expander("ℹ️ How to Read"):
                st.markdown("""
**The global cost of paying USD debts.**

- 🔴 **Rising / > 115**: The Dollar is getting stronger, crushing Silver and Emerging Markets.
- 🟢 **Falling / < 110**: The Dollar is weakening. Global liquidity is expanding (Bullish for Silver).
                """)
        
        # --- Metric 4: The Fear Gauge (VIX) ---
        with metric_col4:
            st.markdown("### 😱 The Fear Gauge")
            st.caption("CBOE Volatility Index - Market panic meter")
            
            vix_val = credit_status.get('vix_current')
            
            if vix_val is not None:
                if vix_val > 30:
                    st.error(f"**{vix_val:.1f}**")
                    st.caption("🚨 PANIC (Forced Liquidation)")
                elif vix_val > 20:
                    st.warning(f"**{vix_val:.1f}**")
                    st.caption("⚠️ Anxiety")
                else:
                    st.success(f"**{vix_val:.1f}**")
                    st.caption("✅ Complacent (<20)")
            else:
                st.info("--")
                st.caption("No data - Run backfill")
            
            # Mini line chart for VIX
            if 'vix' in credit_df.columns:
                fig_vix = go.Figure()
                line_color = '#EF553B' if (vix_val or 0) > 20 else '#00CC96'
                fig_vix.add_trace(go.Scatter(
                    x=credit_df['timestamp'],
                    y=credit_df['vix'],
                    line=dict(color=line_color, width=2),
                    fill='tozeroy',
                    fillcolor='rgba(239, 85, 59, 0.2)' if (vix_val or 0) > 20 else 'rgba(0, 204, 150, 0.2)',
                    mode='lines'
                ))
                fig_vix.add_hline(y=20, line_dash="dot", line_color="orange")
                fig_vix.add_hline(y=30, line_dash="dash", line_color="red")
                fig_vix.update_layout(
                    height=200,
                    margin=dict(l=20, r=20, t=20, b=20),
                    yaxis=dict(title=""),
                    xaxis=dict(title="", showticklabels=False),
                    showlegend=False
                )
                st.plotly_chart(fig_vix, use_container_width=True)
            
            # Help tooltip
            with st.expander("ℹ️ How to Read"):
                st.markdown("""
**Market fear and volatility meter.**

- 🟢 **< 20**: Complacent. Markets calm (buy signals often fail).
- 🟠 **20-30**: Anxiety. Hedging activity rising.
- 🔴 **> 30**: PANIC. Forced liquidations. Margin calls flying.
                """)
    else:
        st.warning("⏳ No Macro Plumbing data available. Run: `python -m src.backfill_history`")
        
    # --- Visual 5: Carry Trade Monitor ---
    st.divider()
    st.subheader("Visual 5: Yen Carry Trade Monitor")
    st.caption("Monitoring the unwinding of the Yen Carry Trade via Yield Spreads and Currency Strength.")
    
    carry_df = get_carry_trade_data()
    
    if not carry_df.empty:
        # Calculate fair value
        carry_df = calculate_fair_value(carry_df)
        
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
        
        # --- Fair Value Divergence Chart ---
        st.markdown("**⚠️ The Trap: Price vs. Fair Value Divergence**")
        
        if 'fair_value' in carry_df.columns and 'usdjpy' in carry_df.columns:
            fig5c = go.Figure()
            
            # Trace 1: Fair Value (Grey Dash - plotted first as base for fill)
            fig5c.add_trace(go.Scatter(
                x=carry_df['timestamp'],
                y=carry_df['fair_value'],
                name="Fair Value",
                line=dict(color='grey', width=2, dash='dash'),
                mode='lines'
            ))
            
            # Trace 2: Spot Price (Orange) with fill to Fair Value
            fig5c.add_trace(go.Scatter(
                x=carry_df['timestamp'],
                y=carry_df['usdjpy'],
                name="Spot Price",
                line=dict(color='#FFA15A', width=3),
                fill='tonexty',
                fillcolor='rgba(255, 0, 0, 0.2)',
                mode='lines'
            ))
            
            # Get current deviation
            latest_dev = carry_df['deviation'].iloc[-1] if 'deviation' in carry_df.columns else 0
            
            fig5c.update_layout(
                title=f"USD/JPY Spot vs Fair Value (Current Gap: {latest_dev:+.1f} ¥)",
                yaxis=dict(title="USD/JPY"),
                xaxis=dict(title=""),
                legend=dict(orientation="h", x=0.5, xanchor="center"),
                hovermode="x unified"
            )
            st.plotly_chart(fig5c, use_container_width=True)
            
            # Insight box
            if latest_dev > 5:
                st.error(f"🚨 **CRASH RISK**: Yen is {latest_dev:.1f}¥ OVERVALUED vs fundamentals. Rubber band stretched!")
            elif latest_dev > 2:
                st.warning(f"⚠️ **Divergence Alert**: Spot is {latest_dev:.1f}¥ above fair value.")
            elif latest_dev < -5:
                st.success(f"🟢 **Undervalued**: Yen is {abs(latest_dev):.1f}¥ below fair value. Potential bounce.")
            else:
                st.info(f"➡️ **Near Fair Value**: Deviation is {latest_dev:+.1f}¥ (within normal range).")
        else:
            st.info("Fair Value calculation requires yield spread data.")
    else:
        st.warning("Insufficient Carry Trade data.")
    
    # --- Visual 6: The Inflation Vector (Oil vs Silver) ---
    st.divider()
    st.subheader("Visual 6: The Inflation Vector (Oil vs Silver)")
    st.caption("Comparing Energy (Inflation) vs Precious Metals (Deflation Hedge) to diagnose crash types.")
    
    # Fetch Oil and Silver price data
    conn = get_db_connection()
    oil_silver_df = pd.read_sql("""
        SELECT l.timestamp, i.symbol, l.contract_price
        FROM margin_logs l
        JOIN instruments i ON l.instrument_id = i.id
        WHERE i.symbol IN ('CRUDE_OIL', 'SILVER')
        ORDER BY l.timestamp ASC
    """, conn)
    conn.close()
    
    if not oil_silver_df.empty:
        # Pivot to get Oil and Silver as columns
        pivot_os = oil_silver_df.pivot_table(index='timestamp', columns='symbol', values='contract_price')
        pivot_os = pivot_os.reset_index()
        
        if 'CRUDE_OIL' in pivot_os.columns or 'SILVER' in pivot_os.columns:
            fig6 = go.Figure()
            
            # Trace 1: Crude Oil (Left Axis - Black)
            if 'CRUDE_OIL' in pivot_os.columns:
                fig6.add_trace(go.Scatter(
                    x=pivot_os['timestamp'],
                    y=pivot_os['CRUDE_OIL'],
                    name="Crude Oil (WTI)",
                    line=dict(color='black', width=3),
                    mode='lines',
                    yaxis='y'
                ))
            
            # Trace 2: Silver (Right Axis - Grey)
            if 'SILVER' in pivot_os.columns:
                fig6.add_trace(go.Scatter(
                    x=pivot_os['timestamp'],
                    y=pivot_os['SILVER'],
                    name="Silver",
                    line=dict(color='grey', width=3),
                    mode='lines',
                    yaxis='y2'
                ))
            
            fig6.update_layout(
                title="Oil vs Silver: Inflation Vector",
                yaxis=dict(
                    title="Oil Price ($)", 
                    title_font=dict(color='black'),
                    side='left'
                ),
                yaxis2=dict(
                    title="Silver Price ($)", 
                    title_font=dict(color='grey'),
                    overlaying='y',
                    side='right'
                ),
                legend=dict(orientation="h", x=0.5, xanchor="center"),
                hovermode="x unified"
            )
            st.plotly_chart(fig6, use_container_width=True)
            
            # The Cheat Sheet
            with st.expander("ℹ️ CHEAT SHEET: How to Interpret the Crash"):
                st.markdown("""
| Pattern | Oil Price | Silver Price | Diagnosis | Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Correlation** | 📉 DOWN | 📉 DOWN | **Liquidity Crisis (Deflation)** | Cash is King. Wait for RRP to hit $0. |
| **Divergence** | 📈 UP | 📉 DOWN | **War / Supply Shock (Stagflation)** | Accumulate. Silver is being sold to cover Oil margins. |
| **Reflation** | 📈 UP | 📈 UP | **Currency Collapse** | All In. The Dollar is losing control. |
                """)
        else:
            st.info("Waiting for Oil and Silver price data. Run CME agent to fetch.")
    else:
        st.warning("No Oil/Silver data available. Run: `python -m src.agents.global_margin_agent` or `python -m src.agents.cme_historical_agent`")

else:
    st.sidebar.warning("No data available. Please run backfill.")
    st.warning("Not enough data to calculate analytics yet (Need 2+ data points).")

# Raw Data
with st.expander("Raw Logs"):
    conn = get_db_connection()
    st.dataframe(pd.read_sql("SELECT * FROM margin_logs ORDER BY timestamp DESC", conn))
    st.dataframe(pd.read_sql("SELECT * FROM yield_logs ORDER BY timestamp DESC", conn))
    conn.close()
