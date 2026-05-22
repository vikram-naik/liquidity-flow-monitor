#!/usr/bin/env python3
import sys
import re
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from src.divergence_engine.engine import DivergenceEngine

def parse_log_trades():
    log_path = Path("/home/vn/.gemini/antigravity-cli/brain/0d447a65-956a-46d9-a29a-e3c2ae2e93bd/.system_generated/tasks/task-1212.log")
    if not log_path.exists():
        raise FileNotFoundError(f"Could not find baseline log at {log_path}")
        
    trades = []
    with open(log_path, "r") as f:
        lines = f.readlines()
        
    # Example format:
    # 9 | ITC          | 2024-06-27 |    9.80 |   17.06 |  -0.390 | yes |     no |
    # 0.0250 |   no |   16 |   +70 |  35.1 | 0.38 | 0.59 | 0.53 | 0.26 | ExitReason.PR
    # T_ST_CROSS
    
    # We parse pairs of lines
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or not re.match(r'^\d+\s*\|', line):
            i += 1
            continue
            
        # Parse first line
        parts1 = [p.strip() for p in line.split("|")]
        if len(parts1) < 8:
            i += 1
            continue
            
        symbol = parts1[1]
        sig_date = parts1[2]
        pnl = float(parts1[3])
        mfe = float(parts1[4])
        cts_signal = float(parts1[5])
        cts_buy = parts1[6]
        accel_above_bt = parts1[7]
        
        # Parse second line (which has the rest)
        i += 1
        if i >= len(lines):
            break
        line2 = lines[i].strip()
        parts2 = [p.strip() for p in line2.split("|")]
        if len(parts2) < 10:
            continue
            
        psz_v = float(parts2[0])
        above_va_high = parts2[1]
        duration = int(parts2[2])
        score = int(parts2[3])
        ml_pct = parts2[4]
        rp10 = float(parts2[5]) if parts2[5] != 'nan' else np.nan
        rp22 = float(parts2[6]) if parts2[6] != 'nan' else np.nan
        rp63 = float(parts2[7]) if parts2[7] != 'nan' else np.nan
        rp252 = float(parts2[8]) if parts2[8] != 'nan' else np.nan
        
        # exit reason might span to next line
        exit_reason = parts2[9]
        if i + 1 < len(lines) and not re.match(r'^\d+\s*\|', lines[i+1].strip()) and "|" not in lines[i+1]:
            exit_reason += " " + lines[i+1].strip()
            i += 1
            
        trades.append({
            'symbol': symbol,
            'sig_date': sig_date,
            'pnl': pnl,
            'mfe': mfe,
            'cts': cts_signal,
            'cts_buy': cts_buy,
            'accel_above_bt': accel_above_bt,
            'psz_v': psz_v,
            'above_va_high': above_va_high,
            'duration': duration,
            'score': score,
            'exit_reason': exit_reason,
            'rp22': rp22,
            'rp63': rp63,
            'rp252': rp252
        })
        i += 1
        
    return trades

def main():
    print("Parsing 82 baseline trades from log file...")
    trades = parse_log_trades()
    print(f"Loaded {len(trades)} trades successfully.")
    
    filtered_trades = []
    
    # We will compute FAS and prev_FAS for each trade by running the DivergenceEngine
    # for the corresponding symbol and matching dates.
    for i, t in enumerate(trades, 1):
        sym = t['symbol']
        sig_dt = t['sig_date']
        
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                print(f"Warning: No ledger for {sym}")
                continue
                
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            matches = ledger[ledger['date_str'] == sig_dt]
            if matches.empty:
                print(f"Warning: No sig date match for {sym} on {sig_dt}")
                continue
                
            sig_idx = matches.index[0]
            sig_row = ledger.iloc[sig_idx]
            prev_sig_row = ledger.iloc[sig_idx - 1] if sig_idx > 0 else None
            
            fas = sig_row.get("fas", 0.0)
            fas_bt = sig_row.get("fas_buy_threshold", -0.8)
            prev_fas = prev_sig_row.get("fas", 0.0) if prev_sig_row is not None else 0.0
            psz_v = sig_row.get("psz_v", 0.0)
            
            accel = sig_row.get("cts_accel", 0.0)
            accel_bt = sig_row.get("cts_accel_threshold", 0.0)
            
            # Check standard acceleration check
            accel_ok = True
            if accel <= accel_bt:
                accel_ok = False
            elif sig_idx >= 2:
                a2 = ledger.iloc[sig_idx - 1].get("cts_accel", 0.0)
                a3 = sig_row.get("cts_accel", 0.0)
                if a3 < a2:
                    accel_ok = False
            
            # Check if this trade relied on the bypass:
            strong_institutional_turn_old = (fas > fas_bt and psz_v > 0.02)
            strong_institutional_turn_new = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)
            
            # A trade is filtered if:
            # 1. Standard acceleration checks fail (accel_ok is False)
            # 2. It bypassed in baseline (strong_institutional_turn_old is True)
            # 3. It fails bypass under new rule (strong_institutional_turn_new is False, i.e. fas < prev_fas)
            is_filtered = (not accel_ok) and strong_institutional_turn_old and (not strong_institutional_turn_new)
            
            if is_filtered:
                filtered_trades.append({
                    'symbol': sym,
                    'sig_date': sig_dt,
                    'pnl': t['pnl'],
                    'fas': fas,
                    'prev_fas': prev_fas,
                    'psz_v': psz_v,
                    'accel': accel,
                    'accel_bt': accel_bt,
                    'exit_reason': t['exit_reason']
                })
                
        except Exception as e:
            print(f"Error processing {sym} on {sig_dt}: {e}")
            
    print("\n==============================================")
    print(f"IDENTIFIED {len(filtered_trades)} FILTERED TRADES POST-CHANGE")
    print("==============================================")
    
    good_trades = [t for t in filtered_trades if t['pnl'] > 0]
    bad_trades = [t for t in filtered_trades if t['pnl'] <= 0]
    
    print(f"\n--- GOOD TRADES FILTERED OUT ({len(good_trades)}) ---")
    for t in sorted(good_trades, key=lambda x: x['sig_date']):
        print(f"  * {t['symbol']:<12} | Sig Date: {t['sig_date']} | PnL: {t['pnl']:+.2f}% | FAS: {t['fas']:.3f} | Prev FAS: {t['prev_fas']:.3f} | Exit: {t['exit_reason']}")
        
    print(f"\n--- BAD TRADES FILTERED OUT ({len(bad_trades)}) ---")
    for t in sorted(bad_trades, key=lambda x: x['sig_date']):
        print(f"  * {t['symbol']:<12} | Sig Date: {t['sig_date']} | PnL: {t['pnl']:+.2f}% | FAS: {t['fas']:.3f} | Prev FAS: {t['prev_fas']:.3f} | Exit: {t['exit_reason']}")
        
    print("\n==============================================")
    print("SUMMARY IMPACT STATISTICS")
    print("==============================================")
    print(f"Total baseline trades     : {len(trades)}")
    print(f"Total post-change trades  : {len(trades) - len(filtered_trades)}")
    print(f"Good trades filtered (PnL > 0) : {len(good_trades)}")
    print(f"Bad trades filtered (PnL <= 0) : {len(bad_trades)}")
    
    # Calculate P&L shifts
    baseline_pnl = [t['pnl'] for t in trades]
    filtered_pnl = [t['pnl'] for t in filtered_trades]
    remaining_trades = [t for t in trades if not any(f['symbol'] == t['symbol'] and f['sig_date'] == t['sig_date'] for f in filtered_trades)]
    remaining_pnl = [t['pnl'] for t in remaining_trades]
    
    print(f"Baseline Avg PnL          : {sum(baseline_pnl)/len(baseline_pnl):.2f}%" if baseline_pnl else "N/A")
    print(f"Post-Change Avg PnL       : {sum(remaining_pnl)/len(remaining_pnl):.2f}%" if remaining_pnl else "N/A")
    print(f"Net PnL Improvement       : {(sum(remaining_pnl)/len(remaining_pnl) - sum(baseline_pnl)/len(baseline_pnl)):+.2f}%" if baseline_pnl and remaining_pnl else "N/A")

if __name__ == "__main__":
    main()
