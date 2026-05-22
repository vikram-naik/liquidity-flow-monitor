import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    trades = run_period(["ITC", "NESTLEIND"], "2024-01-01", "2026-05-22", entry_cfg, exit_cfg, "TEST", signal)
    for t in trades:
        print(f"{t.symbol} Trade: Entry={t.entry_date}, Exit={t.exit_date}, PnL={t.pnl_pct:.2f}%, Reason={t.exit_reason}")

if __name__ == "__main__":
    main()
