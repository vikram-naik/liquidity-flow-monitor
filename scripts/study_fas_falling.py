#!/usr/bin/env python3
import pandas as pd
import numpy as np
import sys
import os
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.scanner import get_watchlist_symbols
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.enums import EntryTag

class MockUniversalCrossCfg:
    enabled = True
    min_basing_expansion = 0.5

class MockCfg:
    universal_cross = MockUniversalCrossCfg()

def run_study(apply_penalty: bool, symbol_list: list):
    trades = []
    cfg = MockCfg()
    
    for symbol in symbol_list:
        try:
            engine = DivergenceEngine(symbol)
            df = engine.run().ledger
            if df is None or df.empty: continue
            
            df['date'] = pd.to_datetime(df['date'])
            records = df.to_dict('records')
            
            in_trade = False
            entry_row = None
            pending_entry = False
            entry_idx = 0
            
            for i in range(1, len(records)):
                row = records[i]
                prev = records[i-1]
                
                if row['date'] < pd.to_datetime("2020-01-01"):
                    continue
                    
                if in_trade:
                    # pure CTS Trailing exit logic
                    cts = row.get('cts', 0.0)
                    prev_cts = prev.get('cts', 0.0)
                    cts_st = row.get('cts_sell_threshold', 0.0)
                    prev_cts_st = prev.get('cts_sell_threshold', 0.0)
                    
                    if cts < cts_st and prev_cts >= prev_cts_st:
                        pnl = (row['close'] / entry_row['close']) - 1
                        mfe = (df.iloc[entry_idx : i+1]['high'].max() / entry_row['close']) - 1
                        mae = (df.iloc[entry_idx : i+1]['low'].min() / entry_row['close']) - 1
                        trades.append({
                            'pnl': pnl, 'mfe': mfe, 'mae': mae, 'duration': i - entry_idx,
                            'symbol': symbol, 'entry_date': entry_row['date'], 'exit_date': row['date']
                        })
                        in_trade = False
                    continue
                    
                if pending_entry:
                    entry_row = row
                    entry_idx = i
                    in_trade = True
                    pending_entry = False
                    continue
                    
                if i < 30: continue
                
                # Use Pandas Series to simulate rows since entry_universal_cross expects row to have .to_dict() and .get()
                row_series = df.iloc[i]
                prev_series = df.iloc[i-1]
                
                is_entry, score, details = entry_universal_cross(row_series, prev_series, cfg, records, i)
                
                if is_entry and apply_penalty:
                    # apply penalty if FAS is falling over last 3 bars
                    fas_0 = row.get('fas', 0)
                    fas_3 = records[i-3].get('fas', 0)
                    if fas_0 < fas_3:
                        # penalize by removing 0.465 (which is ml_guard_threshold * 0.3)
                        # default ml_guard_threshold is 1.55 in db
                        threshold = details.get('ml_guard_threshold', 1.55)
                        penalty = threshold * 0.3
                        score_after_penalty = score - penalty
                        if score_after_penalty < threshold:
                            is_entry = False
                
                if is_entry:
                    pending_entry = True
                    
        except Exception as e:
            pass

    return pd.DataFrame(trades)

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Running 'Before' (Original Master Path)...")
    df_before = run_study(False, symbols)
    
    print("Running 'After' (With FAS Falling Penalty)...")
    df_after = run_study(True, symbols)
    
    def get_metrics(df):
        if df.empty: return {}
        wins = df[df['pnl'] > 0]
        losses = df[df['pnl'] <= 0]
        expectancy = (wins['pnl'].mean() * len(wins) + losses['pnl'].mean() * len(losses)) / len(df) * 100 if len(df) else 0
        return {
            'Trades': len(df),
            'Win Rate %': (len(wins) / len(df) * 100) if len(df) else 0,
            'Avg PnL %': df['pnl'].mean() * 100,
            'Profit Factor': (wins['pnl'].sum() / abs(losses['pnl'].sum())) if len(losses) and losses['pnl'].sum() != 0 else float('inf'),
            'Expectancy %': expectancy,
            'Avg MFE %': df['mfe'].mean() * 100,
            'Avg MAE %': df['mae'].mean() * 100,
            'Avg Duration': df['duration'].mean()
        }
        
    res = [
        {"Model": "Before", **get_metrics(df_before)},
        {"Model": "After", **get_metrics(df_after)}
    ]
    print("\n--- RESULTS ---\n")
    print(tabulate(res, headers="keys", floatfmt=".2f"))

if __name__ == "__main__":
    main()
