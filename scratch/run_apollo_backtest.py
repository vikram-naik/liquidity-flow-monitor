import sys
import os
import pandas as pd
from pathlib import Path

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag
from scratch.study_state_based_bypass import modify_entry_file, backup_file, restore_file, format_trades

# Set up NIFTY 50
symbols = get_watchlist_symbols("NIFTY 50")
entry_cfg = SavgolCTSEntryConfig()
exit_cfg = SavgolCTSExitConfig()
signal = SignalFactory.get_signal("savgol_cts")

backup_file()
try:
    modify_entry_file(0.02)
    print("\n--- Running TEST period backtest on APOLLOHOSP under State-Bypass ---")
    # Let's run just APOLLOHOSP to see what trades it generated
    trades = run_period(["APOLLOHOSP"], "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    df = format_trades(trades)
    print(df.to_string())
finally:
    restore_file()
