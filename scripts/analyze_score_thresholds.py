"""
Script to analyze the PnL of Universal-Cross trades from the backtest
across different ML score thresholds to find the optimal min_ml_score.
"""

from __future__ import annotations

import sys
from pathlib import Path
import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    # Ensure threshold is low to get a wide range of trades
    entry_cfg.universal_cross.min_ml_score = 40.0
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()

    print("Running TRAIN period to generate trades...", flush=True)
    all_trades = run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal)

    # Filter for UNIVERSAL_CROSS
    universal_trades = [t for t in all_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]

    if not universal_trades:
        print("No Universal-Cross trades found.")
        return

    print(f"\nTotal Universal-Cross trades: {len(universal_trades)}")

    # We will analyze the impact of different min score thresholds.
    # Because UNIVERSAL_CROSS override_score maps ML score exactly to conviction_score
    thresholds = [40.0, 50.0, 60.0, 70.0, 80.0, 85.0, 90.0, 95.0]
    
    print("\n--- PnL Distribution by Minimum ML Score Threshold ---")
    print(f"{'Threshold':>10} | {'Trades':>6} | {'Win Rate':>8} | {'Avg PnL%':>9} | {'Max PnL%':>9} | {'Min PnL%':>9}")
    print("-" * 65)

    for thresh in thresholds:
        filtered = [t for t in universal_trades if float(t.conviction_score) >= thresh]
        if not filtered:
            continue
        
        count = len(filtered)
        wins = sum(1 for t in filtered if t.pnl_pct > 0)
        win_rate = (wins / count) * 100
        avg_pnl = np.mean([t.pnl_pct for t in filtered])
        max_pnl = max([t.pnl_pct for t in filtered])
        min_pnl = min([t.pnl_pct for t in filtered])

        print(f"{thresh:>10.1f} | {count:>6} | {win_rate:>7.1f}% | {avg_pnl:>8.2f}% | {max_pnl:>8.2f}% | {min_pnl:>8.2f}%")


if __name__ == "__main__":
    main()
