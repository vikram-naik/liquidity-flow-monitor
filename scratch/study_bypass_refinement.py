#!/usr/bin/env python3
import sys
import shutil
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str, compute_profit_factor
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag
from src.cache.factory import get_cache

UNIVERSAL_CROSS_PATH = Path("/home/vn/python-projects/liquidity-flow-monitor/src/trading/signals/savgol_cts/entries/universal_cross.py")
BACKUP_PATH = UNIVERSAL_CROSS_PATH.with_suffix(".py.bypassbak")

def backup_file():
    if UNIVERSAL_CROSS_PATH.exists():
        shutil.copy2(UNIVERSAL_CROSS_PATH, BACKUP_PATH)
        print(f"Backed up universal_cross.py to {BACKUP_PATH.name}")
    else:
        raise FileNotFoundError(f"Could not find {UNIVERSAL_CROSS_PATH}")

def restore_file():
    if BACKUP_PATH.exists():
        shutil.move(BACKUP_PATH, UNIVERSAL_CROSS_PATH)
        print("Restored universal_cross.py from backup.")
    else:
        print("Warning: Backup file not found to restore!")

def modify_entry_file_with_bypass_variant(variant_name):
    with open(BACKUP_PATH, "r") as f:
        content = f.read()

    target_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is actively positive AND price velocity is strong
    strong_institutional_turn = (fas > fas_bt and psz_v > 0.02)"""

    if variant_name == "baseline":
        replacement_block = target_block
    elif variant_name == "rising_fas":
        replacement_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is actively positive and rising AND price velocity is strong
    prev_fas = prev_row.get("fas", 0.0)
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)"""
    elif variant_name == "positive_fas":
        replacement_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is positive AND price velocity is strong
    strong_institutional_turn = (fas > 0.0 and psz_v > 0.02)"""
    elif variant_name == "positive_and_rising_fas":
        replacement_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is positive and rising AND price velocity is strong
    prev_fas = prev_row.get("fas", 0.0)
    strong_institutional_turn = (fas > 0.0 and fas >= prev_fas and psz_v > 0.02)"""
    else:
        raise ValueError(f"Unknown variant: {variant_name}")

    if target_block in content:
        new_content = content.replace(target_block, replacement_block)
        with open(UNIVERSAL_CROSS_PATH, "w") as f:
            f.write(new_content)
        print(f"Injected bypass variant: {variant_name}")
    else:
        # Let's inspect the file to see if we can find it
        raise ValueError("Could not locate strong_institutional_turn block in universal_cross.py!")

def format_trades(trades_list):
    records_list = []
    for t in trades_list:
        records_list.append({
            'symbol': t.symbol,
            'entry_date': t.entry_date,
            'pnl': t.pnl_pct,
            'mae': getattr(t, "mae_pct", 0.0),
            'duration': t.duration
        })
    return pd.DataFrame(records_list)

def calc_stats(df, raw_trades):
    if df.empty:
        return {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0, "profit_factor": 0.0, "avg_mae": 0.0}
    winners = df[df['pnl'] > 0]
    win_rate = (len(winners) / len(df)) * 100.0
    pf = compute_profit_factor(raw_trades)
    return {
        "trades": len(df),
        "win_rate": win_rate,
        "avg_pnl": df['pnl'].mean(),
        "profit_factor": pf,
        "avg_mae": df['mae'].mean()
    }

def main():
    print("Starting Institutional Turn Bypass Refinement Study...")
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    
    # Flush cache first to be absolutely clean
    cache = get_cache()
    cache.clear()
    print("Redis Cache Flushed.")
    
    backup_file()
    
    variants = ["baseline", "rising_fas", "positive_fas", "positive_and_rising_fas"]
    results = {}
    
    try:
        for var in variants:
            print(f"\n==================================================")
            print(f"RUNNING BACKTEST FOR VARIANT: {var}")
            print(f"==================================================")
            modify_entry_file_with_bypass_variant(var)
            
            # Flush redis cache between variants
            cache.clear()
            
            entry_cfg = SavgolCTSEntryConfig()
            exit_cfg = SavgolCTSExitConfig()
            signal = SignalFactory.get_signal("savgol_cts")
            
            # Run test period
            trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "test", signal)
            
            df = format_trades(trades)
            stats = calc_stats(df, trades)
            results[var] = (stats, df)
            
            print(f"Stats for {var}:")
            print(f"  Trades        : {stats['trades']}")
            print(f"  Win Rate      : {stats['win_rate']:.2f}%")
            print(f"  Avg PnL       : {stats['avg_pnl']:.2f}%")
            print(f"  Profit Factor : {stats['profit_factor']:.2f}")
            print(f"  Avg MAE       : {stats['avg_mae']:.2f}%")
            
            # Check if ITC trade is still present
            itc_trades = df[df['symbol'] == "ITC"]
            if not itc_trades.empty:
                print("  ITC Trades in this variant:")
                for _, r in itc_trades.iterrows():
                    print(f"    - Date: {r['entry_date']}, PnL: {r['pnl']:.2f}%, Duration: {r['duration']}")
            else:
                print("  ITC Trades: NONE (Avoided successfully!)")
                
    finally:
        restore_file()
        cache.clear()
        
    print("\n=================== FINAL SUMMARY ===================")
    print("Variant                  | Trades | WinRate | AvgPnL | ProfitFactor | AvgMAE")
    print("-" * 75)
    for var in variants:
        stats, _ = results[var]
        print(f"{var:24} | {stats['trades']:6d} | {stats['win_rate']:6.2f}% | {stats['avg_pnl']:5.2f}% | {stats['profit_factor']:12.2f} | {stats['avg_mae']:5.2f}%")

if __name__ == "__main__":
    main()
