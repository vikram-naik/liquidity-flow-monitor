#!/usr/bin/env python3
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from scripts.test_exit_options import simulate_trades_custom, get_watchlist_symbols

def main():
    watchlist_name = "NIFTY 50"
    start_date = "2025-12-01"
    symbols = get_watchlist_symbols(watchlist_name)
    signal = SignalFactory.get_signal("savgol_cts")
    
    baseline_trades = []
    rule3_trades = []
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            entry_cfg = get_symbol_entry_config(sym)
            exit_cfg = get_symbol_exit_config(sym)
            
            # Run baseline
            trades_base = simulate_trades_custom(sym, result.ledger, entry_cfg, exit_cfg, signal, "baseline")
            baseline_trades.extend([t for t in trades_base if start_date <= t["entry_date"]])
            
            # Run Rule 3 (2.0 * ATR Trail)
            trades_r3 = simulate_trades_custom(sym, result.ledger, entry_cfg, exit_cfg, signal, "rule_mod_atr")
            rule3_trades.extend([t for t in trades_r3 if start_date <= t["entry_date"]])
        except Exception:
            pass
            
    # Match trades by symbol and entry date
    df_base = pd.DataFrame(baseline_trades)
    df_r3 = pd.DataFrame(rule3_trades)
    
    merged = pd.merge(df_base, df_r3, on=["symbol", "entry_date"], suffixes=("_base", "_r3"))
    merged["pnl_diff"] = merged["pnl_pct_r3"] - merged["pnl_pct_base"]
    
    # Sort by pnl_diff to see where Rule 3 improved or degraded baseline
    print("=" * 100)
    print("                 TRADE-BY-TRADE DIFFERENCES (Rule 3 vs Baseline)")
    print("=" * 100)
    
    differences = merged[merged["pnl_diff"] != 0].copy()
    differences = differences.sort_values(by="pnl_diff", ascending=False)
    
    print(tabulate(differences[["symbol", "entry_date", "mfe_pct_base", "pnl_pct_base", "pnl_pct_r3", "pnl_diff", "exit_reason_base", "exit_reason_r3"]], 
                   headers=["Symbol", "Entry Date", "MFE%", "PnL% (Base)", "PnL% (R3)", "Diff%", "Exit (Base)", "Exit (R3)"], 
                   tablefmt="simple", floatfmt=".2f"))
    print("=" * 100)

if __name__ == "__main__":
    main()
