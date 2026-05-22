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
BACKUP_PATH = UNIVERSAL_CROSS_PATH.with_suffix(".py.rangebak")

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

def modify_entry_file(rp22, rp63, rp252):
    with open(BACKUP_PATH, "r") as f:
        content = f.read()

    target_block = """    # 4. price location should be lower half of range_pos_10 
    # We should ignore price location check if both fas and cts_slope trigger together.
    range_pos_10 = row.get("range_pos_10", 0)
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs):
        return False, 0, {"reason": "price not in lower half of weekly range"}"""

    replacement_block = f"""    # 4. price location should be lower half of range_pos_10 
    # We should ignore price location check if both fas and cts_slope trigger together.
    range_pos_10 = row.get("range_pos_10", 0)
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs):
        return False, 0, {{"reason": "price not in lower half of weekly range"}}
        
    # 4b. Long-term range position constraints to avoid buying near quarterly/yearly highs
    range_pos_22 = row.get("range_pos_22", 0)
    range_pos_63 = row.get("range_pos_63", 0)
    range_pos_252 = row.get("range_pos_252", 0)
    if range_pos_22 > {rp22} or range_pos_63 > {rp63} or range_pos_252 > {rp252}:
        return False, 0, {{"reason": "price in upper portion of long-term ranges"}}"""

    if target_block in content:
        new_content = content.replace(target_block, replacement_block)
        with open(UNIVERSAL_CROSS_PATH, "w") as f:
            f.write(new_content)
        print(f"Injected long-term range constraints: rp22 <= {rp22}, rp63 <= {rp63}, rp252 <= {rp252}")
    else:
        raise ValueError("Could not locate range_pos_10 block in universal_cross.py!")

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
    print("Starting Long-Term Range Position Constraints Study...")
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    
    # We sweep different long-term range position bounds
    configs = [
        ("Current State-Bypass (Baseline)", None),
        ("Constraint: rp_63<=0.60 & rp_252<=0.55", (0.99, 0.60, 0.55)), # rp_22 disabled (0.99)
        ("Constraint: rp_22<=0.50 & rp_63<=0.60 & rp_252<=0.55", (0.50, 0.60, 0.55)),
        ("Constraint: rp_22<=0.50 & rp_63<=0.55 & rp_252<=0.50", (0.50, 0.55, 0.50)),
    ]
    
    results = []
    backup_file()
    try:
        for name, params in configs:
            cache = get_cache()
            cache.clear()
            print("\n------------------------------------------------------------")
            print(f"RUNNING CONFIGURATION: {name}")
            if params is None:
                shutil.copy2(BACKUP_PATH, UNIVERSAL_CROSS_PATH)
            else:
                modify_entry_file(*params)
            
            # Reload modules
            import importlib
            import src.trading.signals.savgol_cts.entries.universal_cross as uc
            import src.trading.signals.savgol_cts.signal as ucsig
            import src.trading.signals.savgol_cts as uc_pkg
            import src.trading.signals.factory as ucfact
            import src.trading.signals as uc_signals
            
            importlib.reload(uc)
            importlib.reload(ucsig)
            importlib.reload(uc_pkg)
            importlib.reload(ucfact)
            importlib.reload(uc_signals)
            
            active_signal = uc_signals.SignalFactory.get_signal("savgol_cts")
            
            # Run Train
            print("Running TRAIN period...")
            train_trades = run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", active_signal)
            train_uni = [t for t in train_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
            df_train = format_trades(train_uni)
            train_stats = calc_stats(df_train, train_uni)
            
            # Run Test
            print("Running TEST period...")
            test_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", active_signal)
            test_uni = [t for t in test_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
            df_test = format_trades(test_uni)
            test_stats = calc_stats(df_test, test_uni)
            
            results.append({
                "cfg": name,
                "train_trades": train_stats["trades"],
                "train_wr": train_stats["win_rate"],
                "train_pnl": train_stats["avg_pnl"],
                "train_pf": train_stats["profit_factor"],
                "train_mae": train_stats["avg_mae"],
                "test_trades": test_stats["trades"],
                "test_wr": test_stats["win_rate"],
                "test_pnl": test_stats["avg_pnl"],
                "test_pf": test_stats["profit_factor"],
                "test_mae": test_stats["avg_mae"]
            })
            
    finally:
        restore_file()
        cache = get_cache()
        cache.clear()
        
    df_results = pd.DataFrame(results)
    print("\n\n=== RANGE POSITION SWEEP COMPLETE ===")
    print(df_results.to_string(index=False))

if __name__ == "__main__":
    main()
