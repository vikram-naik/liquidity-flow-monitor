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

from src.analytics import calculate_squeeze_score, check_systemic_alert, get_db_connection, get_carry_trade_data, get_credit_stress_data, check_credit_stress, calculate_fair_value, get_liquidity_valve_data, get_usdjpy_vs_asset_data

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

st.set_page_config(page_title="Global Liquidity Flow Monitor", layout="wide")

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
    cme_instruments = ['SILVER', 'GOLD', 'COPPER', 'CRUDE_OIL', 'ES', 'NQ']
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
    selected_instrument = st.sidebar.selectbox("Select Instrument for Deep Dive", sorted(instruments))
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
        st.plotly_chart(fig1, use_container_width=True)
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
        st.plotly_chart(fig2, use_container_width=True)

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
            st.plotly_chart(fig3, use_container_width=True)

    # --- Visual 4: Global Liquidity Map ---
    st.divider()
    st.subheader("Visual 4: Global Liquidity Map (Margin Hikes & Cuts)")
    latest_scores = scores_df.sort_values('timestamp').groupby('instrument').tail(1)
    
    if 'asset_class' in latest_scores.columns and not latest_scores.empty:
        min_score, max_score = latest_scores['shock_score'].min(), latest_scores['shock_score'].max()
        abs_max = max(abs(min_score), abs(max_score), 0.1)
        
        fig4 = px.bar(
            latest_scores, x='instrument', y='shock_score', color='asset_class',
            title="Margin Shift (24h)", text_auto='.1%', range_y=[-abs_max*1.1, abs_max*1.1]
        )
        fig4.update_layout(yaxis=dict(title="Shock Score (Hike/Cut %)", zeroline=True, zerolinewidth=3, zerolinecolor='black'))
        st.plotly_chart(fig4, use_container_width=True)
    
    # --- Row 3: Macro Plumbing ---
    st.divider()
    if not credit_df.empty:
        st.subheader("🔧 Macro Plumbing (The Engine Room)")
        st.caption("Tracking systemic risk signals.")
        
        mc1, mc2, mc3, mc4 = st.columns(4)
        
        # Query actual last data dates from DB (not forward-filled)
        conn = get_db_connection()
        macro_dates = pd.read_sql("""
            SELECT tenor, MAX(DATE(timestamp)) as last_date 
            FROM yield_logs 
            WHERE tenor IN ('DXY', 'HY_SPREAD', 'RRP', 'VIX')
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
                fig_c.update_layout(height=150, margin=dict(l=0,r=0,t=0,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                st.plotly_chart(fig_c, use_container_width=True)
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
                fig_r.update_layout(height=150, margin=dict(l=0,r=0,t=0,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                st.plotly_chart(fig_r, use_container_width=True)
                st.caption(f"📅 {macro_dates_dict.get('RRP', 'N/A')}")

        # Steamroller
        with mc3:
            st.markdown("### 💵 Steamroller")
            dxy_val = credit_status.get('dxy_current')
            if dxy_val:
                if dxy_val > 115: st.error(f"{dxy_val:.1f} (CRUSHING)")
                else: st.warning(f"{dxy_val:.1f}")
            else: st.info("--")
            
            if 'dxy' in credit_df.columns:
                fig_d = go.Figure(go.Scatter(x=credit_df['timestamp'], y=credit_df['dxy'], line=dict(color='#EF553B'), connectgaps=True))
                fig_d.add_hline(y=115, line_dash="dash", line_color="red", annotation_text="115")
                fig_d.update_layout(height=150, margin=dict(l=0,r=0,t=0,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                st.plotly_chart(fig_d, use_container_width=True)
                st.caption(f"📅 {macro_dates_dict.get('DXY', 'N/A')}")
                
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
                fig_v.update_layout(height=150, margin=dict(l=0,r=0,t=0,b=0), xaxis=dict(showticklabels=False), yaxis=dict(showticklabels=True, tickfont=dict(size=10)), showlegend=False)
                st.plotly_chart(fig_v, use_container_width=True)
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
        st.plotly_chart(fig5a, use_container_width=True)
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
            st.plotly_chart(fig5b, use_container_width=True)
            
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
        st.plotly_chart(fig6, use_container_width=True)
        
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
        st.plotly_chart(fig7, use_container_width=True)
        
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
        st.plotly_chart(fig7b, use_container_width=True)


            

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
        v8_asset = st.radio("Select Commodity Asset:", ('GOLD', 'COPPER', 'CRUDE_OIL'), index=0)
        
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
            st.plotly_chart(fig8, use_container_width=True)
            
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
            st.plotly_chart(fig8b, use_container_width=True)
            
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