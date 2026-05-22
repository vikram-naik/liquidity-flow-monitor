#!/usr/bin/env python3
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
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
        if UNIVERSAL_CROSS_PATH.exists():
            UNIVERSAL_CROSS_PATH.unlink()
        shutil.copy2(BACKUP_PATH, UNIVERSAL_CROSS_PATH)
        BACKUP_PATH.unlink()
        print("Restored universal_cross.py from backup.")
    else:
        print("Warning: Backup file not found to restore!")

def inject_variant(variant):
    with open(BACKUP_PATH, "r") as f:
        content = f.read()

    target_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is actively positive AND price velocity is strong
    strong_institutional_turn = (fas > fas_bt and psz_v > 0.02)"""

    if variant == "baseline":
        replacement_block = target_block
    elif variant == "rising_fas":
        replacement_block = """    # 1. Acceleration Trend
    # State-based Flow-Velocity Bypass: Bypass acceleration checks if institutional flow is actively positive and rising AND price velocity is strong
    prev_fas = prev_row.get("fas", 0.0)
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)"""
    else:
        raise ValueError(f"Unknown variant {variant}")

    if target_block in content:
        new_content = content.replace(target_block, replacement_block)
        with open(UNIVERSAL_CROSS_PATH, "w") as f:
            f.write(new_content)
        print(f"Injected bypass variant: {variant}")
    else:
        raise ValueError("Could not locate target block in universal_cross.py")

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    cache = get_cache()
    
    backup_file()
    try:
        # 1. Run Baseline
        print("\nRunning Baseline backtest...", flush=True)
        inject_variant("baseline")
        cache.clear()
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()
        signal = SignalFactory.get_signal("savgol_cts")
        baseline_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "test", signal)
        
        # 2. Run Proposed (rising_fas)
        print("\nRunning Rising FAS backtest...", flush=True)
        inject_variant("rising_fas")
        cache.clear()
        rising_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "test", signal)
        
        # Identify filtered trades
        baseline_keys = {(t.symbol, t.entry_date) for t in baseline_trades}
        rising_keys = {(t.symbol, t.entry_date) for t in rising_trades}
        
        filtered_keys = baseline_keys - rising_keys
        
        filtered_trades = [t for t in baseline_trades if (t.symbol, t.entry_date) in filtered_keys]
        
        print("\n==============================================")
        print(f"IDENTIFIED {len(filtered_trades)} FILTERED TRADES POST-CHANGE")
        print("==============================================")
        
        good_trades = [t for t in filtered_trades if t.pnl_pct > 0]
        bad_trades = [t for t in filtered_trades if t.pnl_pct <= 0]
        
        print(f"\n--- GOOD TRADES FILTERED OUT ({len(good_trades)}) ---")
        for t in sorted(good_trades, key=lambda x: x.entry_date):
            reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
            print(f"  * {t.symbol:<12} | Entry Date: {t.entry_date} | PnL: {t.pnl_pct:+.2f}% | Exit Reason: {reason}")
            
        print(f"\n--- BAD TRADES FILTERED OUT ({len(bad_trades)}) ---")
        for t in sorted(bad_trades, key=lambda x: x.entry_date):
            reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
            print(f"  * {t.symbol:<12} | Entry Date: {t.entry_date} | PnL: {t.pnl_pct:+.2f}% | Exit Reason: {reason}")
            
        print("\n==============================================")
        print("SUMMARY IMPACT STATISTICS")
        print("==============================================")
        print(f"Total baseline trades     : {len(baseline_trades)}")
        print(f"Total post-change trades  : {len(rising_trades)}")
        print(f"Good trades filtered (PnL > 0) : {len(good_trades)}")
        print(f"Bad trades filtered (PnL <= 0) : {len(bad_trades)}")
        
        # Calculate metric change
        base_pnl = [t.pnl_pct for t in baseline_trades]
        rising_pnl = [t.pnl_pct for t in rising_trades]
        
        print(f"Baseline Avg PnL          : {sum(base_pnl)/len(base_pnl):.2f}%" if base_pnl else "N/A")
        print(f"Post-Change Avg PnL       : {sum(rising_pnl)/len(rising_pnl):.2f}%" if rising_pnl else "N/A")
        
    finally:
        restore_file()
        cache.clear()

if __name__ == "__main__":
    main()
