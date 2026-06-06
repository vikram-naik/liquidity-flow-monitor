import re
import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason
from scripts.walk_forward import get_watchlist_symbols, today_str
from scratch.analyze_trends import parse_trends, clean_date_str

def simulate_causal_exit(records, entry_idx, signal, exit_cfg):
    """
    Simulates standard exit logic starting from entry_idx.
    Returns: exit_idx, exit_price, exit_reason
    """
    n = len(records)
    if entry_idx >= n:
        return n - 1, records[-1].get("close", 0.0), "End of Data"

    entry_row = records[entry_idx]
    entry_price = entry_row.get("close", 0.0)
    
    peak_close = entry_price
    delivery_bad_count = 0
    cwvap_values = [r.get("cwvap", np.nan) for r in records[:entry_idx + 1]]
    
    pending_exit_reason = None
    
    for i in range(entry_idx + 1, n):
        row = records[i]
        prev = records[i - 1]
        cwvap_values.append(row.get("cwvap", np.nan))
        
        # Execute pending exit at today's open (EOD-lag)
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else row.get("close", 0.0)
            return i, exit_price, pending_exit_reason
            
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue
            
        if close > peak_close:
            peak_close = close
            
        bars_held = i - entry_idx
        
        # We need a dummy Trade object for check_exit
        trade = Trade(
            symbol="",
            entry_date="",
            entry_price=entry_price,
            entry_idx=entry_idx,
            atr_at_entry=entry_row.get("atr_20", entry_price * 0.02)
        )
        
        reason, delivery_bad_count = signal.check_exit(
            row, prev, trade, peak_close, bars_held,
            delivery_bad_count, cwvap_values, exit_cfg,
            records, i
        )
        
        if reason:
            pending_exit_reason = reason

    # If we hit the end of data
    last_row = records[-1]
    return n - 1, last_row.get("close", 0.0), pending_exit_reason or "End of Data (Open Trade)"

def main():
    watchlist = "NIFTY 50"
    start_date_limit = pd.to_datetime("2025-12-01")
    
    symbols = get_watchlist_symbols(watchlist)
    print(f"Loaded {len(symbols)} symbols from {watchlist}.")
    
    trends = parse_trends()
    # Filter trends to >= 2025-12-01
    trends_filtered = []
    for sym, dt_str in trends:
        dt = clean_date_str(dt_str)
        if dt >= start_date_limit:
            # Map ASIANPAINTS to ASIANPAINT
            sym_clean = "ASIANPAINT" if sym == "ASIANPAINTS" else sym
            trends_filtered.append((sym_clean, dt))
            
    print(f"Filtered {len(trends_filtered)} trends from trends.txt starting on/after 2025-12-01.")
    
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    
    # Cache all ledgers
    ledgers = {}
    print("Running DivergenceEngine for all symbols...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df['date_dt'] = pd.to_datetime(df['date'])
            ledgers[sym] = df
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    # --- 1. Analyze Trends from trends.txt ---
    trend_results = []
    for sym, dt in trends_filtered:
        if sym not in ledgers:
            continue
        df = ledgers[sym]
        records = df.to_dict('records')
        
        # Find index of target date
        match_df = df[df['date_dt'] <= dt]
        if match_df.empty:
            continue
        t_idx = match_df.index[-1]
        actual_date = df.loc[t_idx, 'date_dt']
        
        # Check if any entry signal fired in [t_idx - 5, t_idx + 5]
        start_check = max(0, t_idx - 5)
        end_check = min(len(df) - 1, t_idx + 5)
        
        fired_signals = []
        for i in range(start_check, end_check + 1):
            if df.loc[i, 'entry_signal'] > 0:
                fired_signals.append({
                    "date": df.loc[i, 'date_dt'].strftime('%Y-%m-%d'),
                    "tag": df.loc[i, 'entry_tag'],
                    "intensity": df.loc[i, 'entry_signal']
                })
                
        is_missed = len(fired_signals) == 0
        
        # Simulate trade if missed
        pnl_causal = np.nan
        pnl_oracle = np.nan
        duration_causal = 0
        duration_oracle = 0
        exit_date_causal = ""
        exit_date_oracle = ""
        exit_reason_causal = ""
        
        entry_idx = t_idx + 1 # Enter on bar after signal
        if entry_idx < len(df):
            # Causal Exit Simulation
            ex_idx, ex_price, ex_reason = simulate_causal_exit(records, entry_idx, signal, exit_cfg)
            entry_price = df.loc[entry_idx, 'close']
            pnl_causal = (ex_price / entry_price - 1.0) * 100.0
            duration_causal = ex_idx - entry_idx
            exit_date_causal = df.loc[ex_idx, 'date_dt'].strftime('%Y-%m-%d')
            exit_reason_causal = ex_reason.value if hasattr(ex_reason, "value") else str(ex_reason)
            
            # Oracle Exit Simulation
            # Find next oracle peak
            peaks = df[(df.index > t_idx) & (df['oracle_peak'] == 1.0)]
            if not peaks.empty:
                op_idx = peaks.index[0]
                op_exit_idx = min(op_idx + 1, len(df) - 1)
                op_exit_price = df.loc[op_exit_idx, 'close']
                pnl_oracle = (op_exit_price / entry_price - 1.0) * 100.0
                duration_oracle = op_exit_idx - entry_idx
                exit_date_oracle = df.loc[op_exit_idx, 'date_dt'].strftime('%Y-%m-%d')
                
        trend_results.append({
            "symbol": sym,
            "target_date": dt.strftime('%Y-%m-%d'),
            "actual_date": actual_date.strftime('%Y-%m-%d'),
            "is_missed": is_missed,
            "fired_signals": fired_signals,
            "pnl_causal": pnl_causal,
            "duration_causal": duration_causal,
            "exit_date_causal": exit_date_causal,
            "exit_reason_causal": exit_reason_causal,
            "pnl_oracle": pnl_oracle,
            "duration_oracle": duration_oracle,
            "exit_date_oracle": exit_date_oracle
        })
        
    df_trends = pd.DataFrame(trend_results)
    df_trends.to_csv("scratch/trends_analysis.csv", index=False)
    
    # --- 2. Analyze ALL Oracle Troughs since 2025-12-01 ---
    oracle_trough_results = []
    for sym, df in ledgers.items():
        records = df.to_dict('records')
        trough_indices = df[(df['date_dt'] >= start_date_limit) & (df['oracle_trough'] == 1.0)].index.tolist()
        
        for t_idx in trough_indices:
            actual_date = df.loc[t_idx, 'date_dt']
            
            # Check if any entry signal fired in [t_idx - 5, t_idx + 5]
            start_check = max(0, t_idx - 5)
            end_check = min(len(df) - 1, t_idx + 5)
            
            fired_signals = []
            for i in range(start_check, end_check + 1):
                if df.loc[i, 'entry_signal'] > 0:
                    fired_signals.append({
                        "date": df.loc[i, 'date_dt'].strftime('%Y-%m-%d'),
                        "tag": df.loc[i, 'entry_tag'],
                        "intensity": df.loc[i, 'entry_signal']
                    })
                    
            is_missed = len(fired_signals) == 0
            
            # Simulate trade
            pnl_causal = np.nan
            pnl_oracle = np.nan
            duration_causal = 0
            duration_oracle = 0
            exit_date_causal = ""
            exit_date_oracle = ""
            exit_reason_causal = ""
            
            entry_idx = t_idx + 1
            if entry_idx < len(df):
                # Causal Exit
                ex_idx, ex_price, ex_reason = simulate_causal_exit(records, entry_idx, signal, exit_cfg)
                entry_price = df.loc[entry_idx, 'close']
                pnl_causal = (ex_price / entry_price - 1.0) * 100.0
                duration_causal = ex_idx - entry_idx
                exit_date_causal = df.loc[ex_idx, 'date_dt'].strftime('%Y-%m-%d')
                exit_reason_causal = ex_reason.value if hasattr(ex_reason, "value") else str(ex_reason)
                
                # Oracle Exit
                peaks = df[(df.index > t_idx) & (df['oracle_peak'] == 1.0)]
                if not peaks.empty:
                    op_idx = peaks.index[0]
                    op_exit_idx = min(op_idx + 1, len(df) - 1)
                    op_exit_price = df.loc[op_exit_idx, 'close']
                    pnl_oracle = (op_exit_price / entry_price - 1.0) * 100.0
                    duration_oracle = op_exit_idx - entry_idx
                    exit_date_oracle = df.loc[op_exit_idx, 'date_dt'].strftime('%Y-%m-%d')
                    
            oracle_trough_results.append({
                "symbol": sym,
                "trough_date": actual_date.strftime('%Y-%m-%d'),
                "is_missed": is_missed,
                "fired_signals": fired_signals,
                "pnl_causal": pnl_causal,
                "duration_causal": duration_causal,
                "exit_date_causal": exit_date_causal,
                "exit_reason_causal": exit_reason_causal,
                "pnl_oracle": pnl_oracle,
                "duration_oracle": duration_oracle,
                "exit_date_oracle": exit_date_oracle
            })
            
    df_troughs = pd.DataFrame(oracle_trough_results)
    df_troughs.to_csv("scratch/troughs_analysis.csv", index=False)
    
    print("\n--- RESULTS FOR TRENDS.TXT SETUPS ---")
    missed_trends = df_trends[df_trends['is_missed']]
    print(f"Total trends analyzed: {len(df_trends)}")
    print(f"Missed trends: {len(missed_trends)}")
    print(tabulate(missed_trends[["symbol", "target_date", "pnl_causal", "exit_reason_causal", "pnl_oracle"]], headers='keys', tablefmt='psql', showindex=False))
    
    print("\n--- RESULTS FOR ALL ORACLE TROUGHS ---")
    missed_troughs = df_troughs[df_troughs['is_missed']]
    print(f"Total troughs analyzed: {len(df_troughs)}")
    print(f"Missed troughs: {len(missed_troughs)}")
    print(tabulate(missed_troughs[["symbol", "trough_date", "pnl_causal", "exit_reason_causal", "pnl_oracle"]].sort_values(by="pnl_causal", ascending=False), headers='keys', tablefmt='psql', showindex=False))

if __name__ == "__main__":
    main()
