import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import get_watchlist_symbols, today_str, run_period
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    watchlist = "NIFTY 50"
    symbols = get_watchlist_symbols(watchlist)
    test_end = today_str()

    print(f"Running NIFTY 50 simulation to find CWC_SLOPE_EARLY_RELEASE exits...", flush=True)
    all_trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)

    # Filter for CWC_SLOPE_EARLY_RELEASE exits
    # Exit reason can be Enum or String
    early_release_trades = []
    for t in all_trades:
        reason_str = str(t.exit_reason.value if hasattr(t.exit_reason, "value") else t.exit_reason)
        if "CWC slope early release" in reason_str or "CWC_SLOPE_EARLY_RELEASE" in reason_str:
            early_release_trades.append(t)

    print(f"\nFound {len(early_release_trades)} trades that exited with CWC_SLOPE_EARLY_RELEASE:\n")
    
    header = f"{'Symbol':<12} | {'Entry Date':<10} | {'Exit Date':<10} | {'PnL%':>7} | {'Sig CTS':>7} | {'Sig ST':>7} | {'Exit CTS':>7} | {'Exit ST':>7}"
    print(header)
    print("-" * len(header))

    for t in early_release_trades:
        sym = t.symbol
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            
            # Find the row on the exit day
            exit_matches = ledger[ledger['date_str'] == t.exit_date]
            if exit_matches.empty:
                continue
            exit_idx = exit_matches.index[0]
            
            # The signal day is exit_idx - 1 (EOD-lag: exit happens on the next day's open)
            sig_idx = exit_idx - 1
            sig_row = ledger.iloc[sig_idx]
            exit_row = ledger.iloc[exit_idx]
            
            sig_cts = sig_row.get("cts", np.nan)
            sig_st = sig_row.get("cts_sell_threshold", np.nan)
            exit_cts = exit_row.get("cts", np.nan)
            exit_st = exit_row.get("cts_sell_threshold", np.nan)
            
            print(f"{sym:<12} | {t.entry_date:<10} | {t.exit_date:<10} | {t.pnl_pct:>7.2f} | {sig_cts:>7.3f} | {sig_st:>7.3f} | {exit_cts:>7.3f} | {exit_st:>7.3f}")
            
        except Exception as e:
            print(f"Error processing {sym}: {e}")

if __name__ == "__main__":
    main()
