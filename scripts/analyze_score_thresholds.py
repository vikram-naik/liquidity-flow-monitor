"""
Script to analyze the PnL of PRT-Zero-Cross trades from the backtest
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
    # Ensure threshold is 80 to get a wide range of trades
    entry_cfg.prt_zero_cross.min_ml_score = 80.0
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()

    print("Running TEST period to generate trades...", flush=True)
    all_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)

    # Filter for PRT_ZERO_CROSS
    prt_trades = [t for t in all_trades if t.entry_tag == EntryTag.PRT_ZERO_CROSS.value]

    if not prt_trades:
        print("No PRT-Zero-Cross trades found.")
        return

    print(f"\nTotal PRT-Zero-Cross trades: {len(prt_trades)}")

    # We will analyze the impact of different min score thresholds.
    # Because PRT_ZERO_CROSS override_score maps ML score exactly to conviction_score
    thresholds = [80.0, 85.0, 90.0, 92.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0]
    
    print("\n--- PnL Distribution by Minimum ML Score Threshold ---")
    print(f"{'Threshold':>10} | {'Trades':>6} | {'Win Rate':>8} | {'Avg PnL%':>9} | {'Max PnL%':>9} | {'Min PnL%':>9}")
    print("-" * 65)

    for thresh in thresholds:
        filtered = [t for t in prt_trades if float(t.conviction_score) >= thresh]
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
