#!/usr/bin/env python3
"""
Study script to scan the NIFTY 50 watchlist starting from 2019-01-01
for 'Silent Setups'—bars where all 4 entry triggers are inactive,
but all mandatory setup filters pass. 
Simulates performance using the actual platform exit trailing mechanics.

Saves results to:
  - output/silent_setups.md
  - output/silent_setups.csv
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.universal_cross import (
    check_long_term_range, check_gap_down, check_slope_flatness, check_basing
)
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.database import get_db_connection

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_nifty50_symbols():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol FROM watchlist_items 
        WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = 'NIFTY 50')
        ORDER BY display_order
    """)
    symbols = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not symbols:
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "SBIN", "LICI", "ITC", "HINDUNILVR"]
    return symbols


def evaluate_silent_setup(row, prev_row, records, idx):
    """
    Returns True if none of the triggers are active, but all mandatory filters pass.
    """
    # 1. Identify Triggers (Must be completely inactive)
    prev_cs = prev_row.get("cts_slope", 0.0)
    cs = row.get("cts_slope", 0.0)
    trigger_cs = 1 if (prev_cs <= 0 and cs > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0.0)
    fas = row.get("fas", 0.0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    prt = row.get("prt_slope", 0.0)
    prev_prt = prev_row.get("prt_slope", 0.0)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0

    # 4th trigger: CWC Consolidation + Rise (Top Alpha configuration)
    trigger_cwc = 0
    if idx >= 3:
        cwc_vals = [records[k].get("cwc", 0.0) for k in range(idx - 3, idx)]
        cwc_curr = row.get("cwc", 0.0)
        cwc_avg = sum(cwc_vals) / len(cwc_vals)
        cwc_disp = max(cwc_vals) - min(cwc_vals)
        regime = row.get("regime", "notrend")
        if 0.80 <= cwc_avg <= 1.00:
            if cwc_disp <= 0.10:
                if cwc_curr > cwc_avg and (cwc_curr - cwc_avg) >= 0.01:
                    if regime != "downtrend":
                        trigger_cwc = 1

    # SILENT SETUP: No triggers are active
    if any([trigger_cs, trigger_fas, trigger_prt, trigger_cwc]):
        return False

    # Filters:
    accel_bt = row.get("cts_accel_threshold", 0.0)
    accel = row.get("cts_accel", 0.0)
    psz_v = row.get("psz_v", 0.0)
    
    # 1. Acceleration Trend
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)
    if not strong_institutional_turn:
        if accel <= accel_bt:
            return False
        if idx >= 2:
            a2 = records[idx-1].get("cts_accel", 0.0)
            a3 = records[idx].get("cts_accel", 0.0)
            if a3 < a2:
                return False
                
    # 2. PSZ Velocity Trend
    if psz_v <= 0:
        return False
    if idx >= 2:
        v2 = records[idx-1].get("psz_v", 0.0)
        v3 = records[idx].get("psz_v", 0.0)
        if v3 < v2:
            return False

    # 3. FAS Trend
    if idx >= 2:
        f1 = records[idx-2].get("fas", 0.0)
        if fas < f1 and fas > fas_bt:
            return False
        fas_threshold = 0.1
        if fas > fas_threshold:
            return False
        if fas_bt > -0.3:
            return False

    # 4. Price location
    range_pos_10 = row.get("range_pos_10", 0.0)
    cwc = row.get("cwc", 0.0)
    pdd_30 = row.get("pdd_30", 0.0)
    momentum_bypass = (cwc >= 0.40 and pdd_30 < -3.5)
    
    if range_pos_10 > 0.5 and not momentum_bypass:
        return False

    # 4b. Dynamic Long-term Range Gate
    if check_long_term_range(row, cwc_threshold=0.50):
        return False

    # 5. Recent gap downs
    if check_gap_down(records, idx, lookback=10) or check_gap_down(records, idx, lookback=11):
        return False

    # 6. Slope flatness (Bypassed since trigger_cs = 0)

    # 7. Basing patterns
    if check_basing(records, idx):
        return False

    # 7b. Choppy Flat Basing Check using CWC
    cwc_slope = row.get("cwc_slope", 0.0)
    if cwc < 0.10 and cwc_slope < -0.02:
        return False

    # 8. Anti-Trap
    base_tightness = row.get("base_tightness", 1.0)
    if pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46:
        return False

    return True


def simulate_silent_trades(symbol, df, start_date="2019-01-01"):
    """Simulates trading using the exact trailing CTS exit logic."""
    records = df.to_dict('records')
    trades = []
    in_trade = False
    entry_row = None
    pending_entry = False
    entry_idx = 0
    trade_obj = None
    peak_close = 0.0
    state_val = 0  # Initial SavgolCTSExitState
    
    exit_cfg = SavgolCTSExitConfig().universal_cross
    
    for i in range(1, len(records)):
        row = records[i]
        prev = records[i-1]
        curr_date = row['date']
        
        # Skip before start date
        if str(curr_date)[:10] < start_date:
            continue

        if in_trade:
            bars_held = i - entry_idx
            close_now = row.get("close", np.nan)
            if np.isnan(close_now):
                continue
                
            if close_now > peak_close:
                peak_close = close_now
                
            # Standard Exit Check
            reason, state_val = exit_universal_cross(
                row=row,
                prev_row=prev,
                trade=trade_obj,
                peak_close=peak_close,
                bars_held=bars_held,
                state_val=state_val,
                cfg=exit_cfg,
                records=records,
                idx=i
            )
            
            if reason is not None:
                exit_price = row.get('open', close_now)
                if np.isnan(exit_price):
                    exit_price = close_now
                pnl = (exit_price / entry_row['close']) - 1
                
                trades.append({
                    'Symbol': symbol,
                    'Setup Date': str(entry_row['date'])[:10],
                    'Exit Date': str(curr_date)[:10],
                    'PnL %': round(pnl * 100, 2),
                    'Duration (bars)': bars_held,
                    'Exit Reason': reason.value if hasattr(reason, 'value') else str(reason)
                })
                in_trade = False
                entry_row = None
                trade_obj = None
            continue

        if pending_entry:
            # EOD-Lag
            entry_row = row
            entry_idx = i
            in_trade = True
            pending_entry = False
            peak_close = row.get('close', 0.0)
            state_val = 0
            
            atr = row.get("atr_20", 0.0)
            psz = row.get("price_slope_z", 0.0)
            trade_obj = Trade(
                symbol=symbol,
                entry_date=str(row['date'])[:10],
                entry_price=row.get('close', 0.0),
                entry_idx=i,
                atr_at_entry=atr if not np.isnan(atr) else 0.0,
                conviction_score=70,
                regime_at_entry=row.get('regime', '-'),
                entry_tag=EntryTag.UNIVERSAL_CROSS.value,
                psz_at_entry=psz if not np.isnan(psz) else 0.0,
                psz_peak=psz if not np.isnan(psz) else 0.0
            )
            continue

        if i < 5:
            continue
            
        is_silent = evaluate_silent_setup(row, prev, records, i)
        if is_silent:
            pending_entry = True
            
    # Force close open trade at the end of data
    if in_trade and entry_row:
        last = records[-1]
        pnl = (last['close'] / entry_row['close']) - 1
        trades.append({
            'Symbol': symbol,
            'Setup Date': str(entry_row['date'])[:10],
            'Exit Date': str(last['date'])[:10],
            'PnL %': round(pnl * 100, 2),
            'Duration (bars)': len(records) - 1 - entry_idx,
            'Exit Reason': ExitReason.END_OF_DATA.value
        })
        
    return trades


def calculate_metrics(trades):
    if not trades:
        return {
            'count': 0, 'win_rate': 0.0, 'avg_pnl': 0.0, 
            'profit_factor': 0.0, 'drawdown': 0.0, 'avg_duration': 0.0
        }
    df = pd.DataFrame(trades)
    count = len(df)
    winners = df[df['PnL %'] > 0]
    losers = df[df['PnL %'] <= 0]
    
    win_rate = (len(winners) / count) * 100 if count > 0 else 0.0
    avg_pnl = df['PnL %'].mean()
    
    gross_profits = winners['PnL %'].sum()
    gross_losses = abs(losers['PnL %'].sum())
    profit_factor = gross_profits / gross_losses if gross_losses > 0 else (float('inf') if gross_profits > 0 else 1.0)
    
    avg_duration = df['Duration (bars)'].mean()
    
    df = df.sort_values('Setup Date')
    equity = (1.0 + df['PnL %'] / 100).cumprod()
    peak = equity.cummax()
    drawdowns = (equity - peak) / peak
    max_dd = drawdowns.min() * 100 if len(drawdowns) > 0 else 0.0
    
    return {
        'count': count,
        'win_rate': win_rate,
        'avg_pnl': avg_pnl,
        'profit_factor': profit_factor,
        'drawdown': max_dd,
        'avg_duration': avg_duration
    }


def main():
    print("="*60)
    print("      LFM SILENT SETUPS COMPARATIVE SCAN STUDY")
    print("="*60)
    
    symbols = get_nifty50_symbols()
    print(f"Loading {len(symbols)} symbols from the NIFTY 50 watchlist (scanning from 2019-01-01)...")
    
    all_trades = []
    
    for idx, symbol in enumerate(symbols, 1):
        try:
            engine = DivergenceEngine(symbol)
            result = engine.run()
            df = result.ledger
            if df is None or df.empty:
                continue
                
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            
            # Simulate trades starting from 2019-01-01
            trades = simulate_silent_trades(symbol, df, start_date="2019-01-01")
            all_trades.extend(trades)
            
            if trades:
                print(f"[{idx}/{len(symbols)}] {symbol:<12} | Found {len(trades):>2} silent setups")
            else:
                print(f"[{idx}/{len(symbols)}] {symbol:<12} | No setups found")
        except Exception as e:
            print(f"[{idx}/{len(symbols)}] {symbol:<12} | Failed: {e}")

    # Compute metrics
    metrics = calculate_metrics(all_trades)
    
    # Save CSV
    df_trades = pd.DataFrame(all_trades) if all_trades else pd.DataFrame(columns=['Symbol', 'Setup Date', 'Exit Date', 'PnL %', 'Duration (bars)', 'Exit Reason'])
    df_trades = df_trades.sort_values(by=['Symbol', 'Setup Date'])
    csv_path = OUTPUT_DIR / "silent_setups.csv"
    df_trades.to_csv(csv_path, index=False)
    
    # Format markdown report
    md_path = OUTPUT_DIR / "silent_setups.md"
    with open(md_path, "w") as f:
        f.write("# Silent Setups Scan Report\n\n")
        f.write(f"*Scanned across the NIFTY 50 universe from 2019-01-01 to present.*\n\n")
        f.write("## Overview\n")
        f.write("A **Silent Setup** represents a bar where the baseline inflection triggers do *not* fire, ")
        f.write("but all core structural, flow, and volume filters in `entry_universal_cross.py` are fully satisfied. ")
        f.write("This scan simulates these setups using the **actual platform trailing exits** to measure their PnL viability.\n\n")
        
        f.write("## Summary Statistics\n\n")
        f.write(f"* **Total Silent Setups Discovered**: {metrics['count']}\n")
        f.write(f"* **Win Rate**: {metrics['win_rate']:.2f}%\n")
        f.write(f"* **Average PnL per trade**: {metrics['avg_pnl']:.2f}%\n")
        f.write(f"* **Profit Factor**: {metrics['profit_factor']:.2f}\n")
        f.write(f"* **Max Simulated Drawdown**: {metrics['drawdown']:.2f}%\n")
        f.write(f"* **Average Trade Duration**: {metrics['avg_duration']:.1f} bars\n\n")
        
        f.write("## Discovered Silent Setup Trades\n\n")
        if not df_trades.empty:
            md_table = tabulate(df_trades, headers='keys', tablefmt='github', showindex=False)
            f.write(md_table + "\n")
        else:
            f.write("*No silent setups found during the study period.*\n")
            
    print("\n" + "="*60)
    print("                     SCAN RESULTS SUMMARY")
    print("="*60)
    print(f"Total Silent Setups: {metrics['count']}")
    print(f"Win Rate:            {metrics['win_rate']:.2f}%")
    print(f"Avg P&L:             {metrics['avg_pnl']:.2f}%")
    print(f"Profit Factor:       {metrics['profit_factor']:.2f}")
    print(f"Max Drawdown:        {metrics['drawdown']:.2f}%")
    print(f"Avg Duration:        {metrics['avg_duration']:.1f} bars")
    print("="*60 + "\n")
    
    print(f"Reports successfully generated:")
    print(f"  - {csv_path.absolute()}")
    print(f"  - {md_path.absolute()}\n")


if __name__ == "__main__":
    main()
