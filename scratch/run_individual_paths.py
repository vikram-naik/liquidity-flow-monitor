import os
import sys
import io
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import (
    get_watchlist_symbols, run_period, summarize, today_str,
    TRAIN_START, TRAIN_END, TEST_START
)
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig

def main():
    watchlist = "NIFTY 50"
    symbols = get_watchlist_symbols(watchlist)
    print(f"Loaded {len(symbols)} symbols from watchlist {watchlist}")
    
    test_end = today_str()
    signal = SignalFactory.get_signal("savgol_cts")
    
    paths = [
        "cdvl_cts",
        "universal_cross",
        "trend_pullback",
        "flow_momentum",
        "coherent_pullback",
        "anchor_shock_pullback",
        "springboard"
    ]
    
    results = []
    
    for path in paths:
        print(f"\n========================================================")
        print(f"Running Singular Backtest for Entry Path: {path.upper()}")
        print(f"========================================================")
        
        # Configure entry cfg to enable ONLY this path
        entry_cfg = SavgolCTSEntryConfig()
        entry_cfg.cdvl_cts.enabled = (path == "cdvl_cts")
        entry_cfg.universal_cross.enabled = (path == "universal_cross")
        entry_cfg.trend_pullback_enabled = (path == "trend_pullback")
        entry_cfg.flow_momentum.enabled = (path == "flow_momentum")
        entry_cfg.coherent_pullback.enabled = (path == "coherent_pullback")
        entry_cfg.anchor_shock_pullback.enabled = (path == "anchor_shock_pullback")
        entry_cfg.springboard.enabled = (path == "springboard")
        
        exit_cfg = SavgolCTSExitConfig()
        
        # Run periods
        print(f"Running Train period ({TRAIN_START} to {TRAIN_END})...")
        train_trades = run_period(symbols, TRAIN_START, TRAIN_END, entry_cfg, exit_cfg, "TRAIN", signal)
        
        print(f"Running Test period ({TEST_START} to {test_end})...")
        test_trades = run_period(symbols, TEST_START, test_end, entry_cfg, exit_cfg, "TEST", signal)
        
        # Summarize (pass dummy StringIO to suppress printing detailed table to stdout)
        dummy_out_train = io.StringIO()
        train_stats = summarize(train_trades, "TRAIN", TRAIN_START, TRAIN_END, dummy_out_train)
        
        dummy_out_test = io.StringIO()
        test_stats = summarize(test_trades, "TEST", TEST_START, test_end, dummy_out_test)
        
        results.append({
            "Path": path,
            "Train Trades": train_stats.get("trades", 0),
            "Train Win Rate%": train_stats.get("win_rate", 0.0),
            "Train Avg P&L%": train_stats.get("avg_pnl", 0.0),
            "Train Expectancy%": train_stats.get("expectancy", 0.0),
            "Test Trades": test_stats.get("trades", 0),
            "Test Win Rate%": test_stats.get("win_rate", 0.0),
            "Test Avg P&L%": test_stats.get("avg_pnl", 0.0),
            "Test Expectancy%": test_stats.get("expectancy", 0.0),
        })
        
        print(f"Result for {path}:")
        print(f"  Train: {train_stats.get('trades', 0)} trades, Avg P&L: {train_stats.get('avg_pnl', 0.0):+.2f}%, Win Rate: {train_stats.get('win_rate', 0.0):.1f}%")
        print(f"  Test:  {test_stats.get('trades', 0)} trades, Avg P&L: {test_stats.get('avg_pnl', 0.0):+.2f}%, Win Rate: {test_stats.get('win_rate', 0.0):.1f}%")

    df_res = pd.DataFrame(results)
    
    print("\n" + "="*80)
    print("FINAL COMPARATIVE REPORT FOR SINGULAR ENTRY PATHS")
    print("="*80)
    print(df_res.to_string(index=False))
    
    output_path = Path(__file__).resolve().parent.parent / "output" / "singular_paths_backtest.txt"
    df_res.to_csv(output_path, index=False)
    print(f"\nSaved singular paths report to {output_path}")

if __name__ == "__main__":
    main()
