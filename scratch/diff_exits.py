import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
import src.trading.signals.savgol_cts.exits.cwvap_guard as cwvap_guard

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    
    # 1. Run with NEW logic (bypass enabled when cts > st)
    print("Running with NEW logic (bypass enabled when cts > st)...", flush=True)
    new_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    
    # 2. Run with OLD logic (bypass disabled)
    print("\nRunning with OLD logic (bypass disabled)...", flush=True)
    import src.trading.signals.savgol_cts.signal as savgol_signal
    orig_apply = savgol_signal.apply_cwvap_guard
    
    def patched_apply_old(row, trade, res, state_val, cfg, records, idx, tag=""):
        row_copy = row.copy()
        # Delete cts_sell_threshold to force cts_above_st = False
        if "cts_sell_threshold" in row_copy:
            del row_copy["cts_sell_threshold"]
        return orig_apply(row_copy, trade, res, state_val, cfg, records, idx, tag)
        
    savgol_signal.apply_cwvap_guard = patched_apply_old
    
    old_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    
    # Restore original function
    savgol_signal.apply_cwvap_guard = orig_apply
    
    # 3. Compare trades by entry date and symbol
    old_map = {(t.symbol, t.entry_date): t for t in old_trades}
    new_map = {(t.symbol, t.entry_date): t for t in new_trades}
    
    print("\n=== COMPARISON OF CHANGED TRADES ===")
    header = f"{'Symbol':<12} | {'Entry Date':<10} | {'Old Exit':<10} | {'Old PnL%':>8} | {'Old Reason':<30} | {'New Exit':<10} | {'New PnL%':>8} | {'New Reason':<30}"
    print(header)
    print("-" * len(header))
    
    changed_count = 0
    for key, old_t in old_map.items():
        if key in new_map:
            new_t = new_map[key]
            if old_t.exit_date != new_t.exit_date or old_t.exit_reason != new_t.exit_reason:
                changed_count += 1
                old_reason = old_t.exit_reason.value if hasattr(old_t.exit_reason, "value") else str(old_t.exit_reason)
                new_reason = new_t.exit_reason.value if hasattr(new_t.exit_reason, "value") else str(new_t.exit_reason)
                print(f"{key[0]:<12} | {key[1]:<10} | {old_t.exit_date:<10} | {old_t.pnl_pct:>8.2f}% | {old_reason:<30} | {new_t.exit_date:<10} | {new_t.pnl_pct:>8.2f}% | {new_reason:<30}")
                
    print(f"\nTotal changed trades: {changed_count}")

if __name__ == "__main__":
    main()
