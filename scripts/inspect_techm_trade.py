import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    trades = run_period(["TECHM"], "2026-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    
    print(f"Total trades found: {len(trades)}")
    for i, t in enumerate(trades, 1):
        print(f"Trade #{i}: {t.symbol} | Entry: {t.entry_date} | Exit: {t.exit_date} | PnL: {t.pnl_pct:+.2f}% | Duration: {t.duration} | Reason: {t.exit_reason}")

if __name__ == "__main__":
    main()
