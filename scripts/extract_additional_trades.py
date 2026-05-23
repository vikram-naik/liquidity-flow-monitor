#!/usr/bin/env python3
"""
Extracts the exact list of additional trades captured under the Proposed (5-trigger)
configuration compared to the Baseline (4-trigger) configuration.
Includes Trigger Type, 'Both Fired' status, and columns for all other entry triggers:
  - CTS Slope Fired
  - FAS Fired
  - PRT Slope Fired
  - CWC Fired

Saves the results to:
  - output/additional_trades.md
  - output/additional_trades.csv
"""

import sys
import os
import pandas as pd
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.study_accel_trigger import get_nifty50_symbols
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.universal_cross import (
    check_long_term_range, check_gap_down, check_slope_flatness, check_basing
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def evaluate_custom_entry_with_type(row, prev_row, records, idx, use_accel_trigger=True):
    """Replicates entry_universal_cross but returns the trigger type, both-fired status, and other active triggers."""
    # 1. Identify Triggers
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

    # 4th trigger: CWC Consolidation + Rise
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

    # 5th trigger (Proposed Acceleration inflection - crossing threshold)
    trigger_accel = 0
    
    prev_accel = prev_row.get("cts_accel", 0.0)
    accel = row.get("cts_accel", 0.0)
    accel_bt = row.get("cts_accel_threshold", 0.0)
    
    if use_accel_trigger:
        trigger_accel = 1 if (prev_accel <= accel_bt and accel > accel_bt) else 0

    if not any([trigger_cs, trigger_fas, trigger_prt, trigger_cwc, trigger_accel]):
        return False, "None", "No", {}

    # Identify trigger type and check if both fired
    both_fired = "No"
    if trigger_accel:
        trigger_type = "accel_cross_thr"
    elif trigger_cs:
        trigger_type = "cts_slope_cross"
    elif trigger_fas:
        trigger_type = "fas_cross"
    elif trigger_prt:
        trigger_type = "prt_slope_cross"
    elif trigger_cwc:
        trigger_type = "cwc_consolidation"
    else:
        trigger_type = "other"

    # Capture other triggers' active state
    other_triggers = {
        'CTS Slope Fired': 'Yes' if trigger_cs else 'No',
        'FAS Fired': 'Yes' if trigger_fas else 'No',
        'PRT Slope Fired': 'Yes' if trigger_prt else 'No',
        'CWC Fired': 'Yes' if trigger_cwc else 'No'
    }

    # Filters:
    # 1. Acceleration Trend
    psz_v = row.get("psz_v", 0.0)
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)
    
    if not strong_institutional_turn:
        if accel <= accel_bt:
            return False, "None", "No", {}

        if idx >= 2:
            a2 = records[idx-1].get("cts_accel", 0.0)
            a3 = records[idx].get("cts_accel", 0.0)
            if a3 < a2:
                return False, "None", "No", {}
                
    # 2. PSZ Velocity Trend
    if psz_v <= 0:
        return False, "None", "No", {}
    if idx >= 2:
        v2 = records[idx-1].get("psz_v", 0.0)
        v3 = records[idx].get("psz_v", 0.0)
        if v3 < v2:
            return False, "None", "No", {}

    # 3. FAS Trend
    if idx >= 2:
        f1 = records[idx-2].get("fas", 0.0)
        if fas < f1 and fas > fas_bt:
            return False, "None", "No", {}
        fas_threshold = 0.1
        if fas > fas_threshold:
            return False, "None", "No", {}
        if fas_bt > -0.3:
            return False, "None", "No", {}

    # 4. Price location
    range_pos_10 = row.get("range_pos_10", 0.0)
    cwc = row.get("cwc", 0.0)
    pdd_30 = row.get("pdd_30", 0.0)
    momentum_bypass = (cwc >= 0.40 and pdd_30 < -3.5)
    
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs) and not trigger_cwc and not momentum_bypass:
        return False, "None", "No", {}

    # 4b. Dynamic Long-term Range Gate
    if check_long_term_range(row, cwc_threshold=0.50) and not trigger_cwc:
        return False, "None", "No", {}

    # 5. Recent gap downs
    if check_gap_down(records, idx, lookback=10) or check_gap_down(records, idx, lookback=11):
        return False, "None", "No", {}

    # 6. Slope flatness
    if check_slope_flatness(records, idx) and trigger_cs:
        return False, "None", "No", {}

    # 7. Basing patterns
    if check_basing(records, idx):
        return False, "None", "No", {}

    # 7b. Choppy Flat Basing Check using CWC
    cwc_slope = row.get("cwc_slope", 0.0)
    if cwc < 0.10 and cwc_slope < -0.02:
        return False, "None", "No", {}

    # 8. Anti-Trap
    base_tightness = row.get("base_tightness", 1.0)
    if pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46:
        return False, "None", "No", {}

    return True, trigger_type, both_fired, other_triggers


def simulate_trades_with_type(symbol, df, use_accel_trigger, start_date="2024-01-01"):
    """Simulates trading using EOD-Lag entry and standard CTS sell-threshold exits."""
    records = df.to_dict('records')
    trades = []
    in_trade = False
    entry_row = None
    pending_entry = False
    entry_idx = 0
    pending_trigger = "None"
    pending_both = "No"
    pending_others = {}
    
    for i in range(1, len(records)):
        row = records[i]
        prev = records[i-1]
        curr_date = row['date']
        
        # Skip before start date
        if str(curr_date)[:10] < start_date:
            continue

        if in_trade:
            # Standard Exit Logic
            cts = row.get('cts', 0.0)
            prev_cts = prev.get('cts', 0.0)
            cts_st = row.get('cts_sell_threshold', 0.0)
            prev_cts_st = prev.get('cts_sell_threshold', 0.0)
            
            if cts < cts_st and prev_cts >= prev_cts_st:
                exit_price = row['close']
                pnl = (exit_price / entry_row['close']) - 1
                duration = i - entry_idx
                
                trade_dict = {
                    'symbol': symbol,
                    'entry_date': entry_row['date'],
                    'exit_date': curr_date,
                    'pnl': pnl,
                    'duration': duration,
                    'trigger_type': pending_trigger,
                    'both_fired': pending_both
                }
                trade_dict.update(pending_others)
                trades.append(trade_dict)
                in_trade = False
                entry_row = None
            continue

        if pending_entry:
            # EOD-Lag
            entry_row = row
            entry_idx = i
            in_trade = True
            pending_entry = False
            continue

        if i < 5:
            continue
            
        passed, trigger_type, both_fired, other_triggers = evaluate_custom_entry_with_type(row, prev, records, i, use_accel_trigger)
        if passed:
            pending_entry = True
            pending_trigger = trigger_type
            pending_both = both_fired
            pending_others = other_triggers
            
    # Force close open trades
    if in_trade and entry_row:
        last = records[-1]
        pnl = (last['close'] / entry_row['close']) - 1
        trade_dict = {
            'symbol': symbol,
            'entry_date': entry_row['date'],
            'exit_date': last['date'],
            'pnl': pnl,
            'duration': len(records) - 1 - entry_idx,
            'trigger_type': pending_trigger,
            'both_fired': pending_both
        }
        trade_dict.update(pending_others)
        trades.append(trade_dict)
        
    return trades


def main():
    symbols = get_nifty50_symbols()
    print(f"Extracting trades for {len(symbols)} symbols to identify additional triggers...")
    
    baseline_trades = []
    proposed_trades = []
    
    for idx, symbol in enumerate(symbols, 1):
        try:
            engine = DivergenceEngine(symbol)
            result = engine.run()
            df = result.ledger
            if df is None or df.empty:
                continue
                
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            
            b_trades = simulate_trades_with_type(symbol, df, use_accel_trigger=False)
            p_trades = simulate_trades_with_type(symbol, df, use_accel_trigger=True)
            
            baseline_trades.extend(b_trades)
            proposed_trades.extend(p_trades)
        except Exception as e:
            pass

    # Convert to DataFrames
    cols = ['symbol', 'entry_date', 'exit_date', 'pnl', 'duration', 'trigger_type', 'both_fired', 'CTS Slope Fired', 'FAS Fired', 'PRT Slope Fired', 'CWC Fired']
    df_b = pd.DataFrame(baseline_trades) if baseline_trades else pd.DataFrame(columns=cols)
    df_p = pd.DataFrame(proposed_trades) if proposed_trades else pd.DataFrame(columns=cols)
    
    additional_rows = []
    
    for _, p_row in df_p.iterrows():
        p_sym = p_row['symbol']
        p_entry = pd.to_datetime(p_row['entry_date'])
        
        # Check if baseline has a trade in the same symbol within +/- 3 days of proposed entry
        df_b_sym = df_b[df_b['symbol'] == p_sym]
        match_found = False
        
        for _, b_row in df_b_sym.iterrows():
            b_entry = pd.to_datetime(b_row['entry_date'])
            day_diff = abs((p_entry - b_entry).days)
            if day_diff <= 3:
                match_found = True
                break
                
        if not match_found:
            additional_rows.append({
                'Symbol': p_sym,
                'Entry Date': str(p_row['entry_date'])[:10],
                'Exit Date': str(p_row['exit_date'])[:10],
                'PnL %': round(p_row['pnl'] * 100, 2),
                'Duration (bars)': int(p_row['duration']),
                'Trigger Type': p_row['trigger_type'],
                'Both Fired': p_row['both_fired'],
                'CTS Slope Fired': p_row.get('CTS Slope Fired', 'No'),
                'FAS Fired': p_row.get('FAS Fired', 'No'),
                'PRT Fired': p_row.get('PRT Slope Fired', 'No'),
                'CWC Fired': p_row.get('CWC Fired', 'No')
            })
            
    df_add = pd.DataFrame(additional_rows)
    df_add = df_add.sort_values(by=['Symbol', 'Entry Date'])
    
    # Save CSV
    csv_path = OUTPUT_DIR / "additional_trades.csv"
    df_add.to_csv(csv_path, index=False)
    
    # Save Markdown
    md_path = OUTPUT_DIR / "additional_trades.md"
    with open(md_path, "w") as f:
        f.write("# Extract: Additional Trades with Detailed Trigger States\n\n")
        f.write("This table details the exact additional trades generated, showing all other active triggers at entry:\n\n")
        f.write(tabulate(df_add, headers='keys', tablefmt='github', showindex=False) + "\n")
        
    print(f"\nSuccessfully identified {len(df_add)} additional trades!")
    print(f"Saved CSV extract to: {csv_path.absolute()}")
    print(f"Saved Markdown extract to: {md_path.absolute()}\n")
    
    # Print the table
    print(tabulate(df_add, headers='keys', tablefmt='grid', showindex=False))

if __name__ == "__main__":
    main()
