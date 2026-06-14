#!/usr/bin/env python3
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason

def run_backtest_for_threshold(symbols, threshold, enabled, watchlist_name):
    entry_cfg = SavgolCTSEntryConfig()
    entry_cfg.anti_knife_filter_enabled = enabled
    entry_cfg.anti_knife_fas_slope_sum_threshold = threshold
    
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    test_end = today_str()
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, f"TEST_{threshold}", signal)
    
    if not trades:
        return {
            "threshold": threshold if enabled else "Disabled",
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "profit_factor": 0.0,
            "hard_stops": 0
        }
        
    df = pd.DataFrame([{
        "pnl": t.pnl_pct,
        "reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
    } for t in trades])
    
    total = len(df)
    winners = (df["pnl"] > 0).sum()
    win_rate = winners / total * 100
    avg_pnl = df["pnl"].mean()
    
    # Calculate Profit Factor
    gross_win = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
    profit_factor = gross_win / gross_loss if gross_loss != 0 else float("inf")
    
    # Count hard stops
    hard_stops = sum(1 for t in trades if "hard_stop" in (t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)).lower() or "hard stop" in (t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)).lower())
    
    return {
        "threshold": threshold if enabled else "Disabled",
        "trades": total,
        "win_rate": round(win_rate, 2),
        "avg_pnl": round(avg_pnl, 3),
        "profit_factor": round(profit_factor, 3),
        "hard_stops": hard_stops
    }

def main():
    test_watchlists = ["NIFTY 50", "NSE F&O"]
    thresholds = [
        (False, 0.0),      # Disabled (Baseline)
        (True, -2.0),
        (True, -2.5),
        (True, -3.0),
        (True, -3.5),
        (True, -4.0),
        (True, -4.5),
        (True, -5.0)
    ]
    
    for wl in test_watchlists:
        print(f"\n==================================================")
        print(f"  SWEEPING ANTI-KNIFE THRESHOLDS FOR {wl}")
        print(f"==================================================")
        symbols = get_watchlist_symbols(wl)
        results = []
        for enabled, thresh in thresholds:
            res = run_backtest_for_threshold(symbols, thresh, enabled, wl)
            results.append(res)
            
        print("\nSweep Results:")
        print(tabulate(results, headers="keys", tablefmt="grid"))

if __name__ == "__main__":
    main()
