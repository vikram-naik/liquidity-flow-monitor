import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
import src.trading.signals.savgol_cts.exits.cwvap_guard as cwvap_guard

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    
    orig_apply = cwvap_guard.apply_cwvap_guard
    
    def patched_apply_old(row, trade, res, state_val, cfg, records, idx, tag=""):
        row_copy = row.copy()
        if "cts_sell_threshold" in row_copy:
            del row_copy["cts_sell_threshold"]
        return orig_apply(row_copy, trade, res, state_val, cfg, records, idx, tag)
        
    cwvap_guard.apply_cwvap_guard = patched_apply_old
    
    trades = run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal)
    
    cwvap_guard.apply_cwvap_guard = orig_apply
    
    early_release_trades = []
    for t in trades:
        reason_str = str(t.exit_reason.value if hasattr(t.exit_reason, "value") else t.exit_reason)
        if "CWC slope early release" in reason_str or "CWC_SLOPE_EARLY_RELEASE" in reason_str:
            early_release_trades.append(t)
            
    print(f"Found {len(early_release_trades)} early release trades under OLD logic:")
    for t in early_release_trades:
        sym = t.symbol
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            exit_matches = ledger[ledger['date_str'] == t.exit_date]
            if exit_matches.empty:
                continue
            exit_idx = exit_matches.index[0]
            sig_idx = exit_idx - 1
            sig_row = ledger.iloc[sig_idx]
            sig_cts = sig_row.get("cts", np.nan)
            sig_st = sig_row.get("cts_sell_threshold", np.nan)
            print(f"{sym:<12} | Entry={t.entry_date} | Exit={t.exit_date} | sig_cts={sig_cts:.3f} | sig_st={sig_st:.3f} | cts > st? {sig_cts > sig_st}")
        except Exception as e:
            print(f"Error {sym}: {e}")

if __name__ == "__main__":
    main()
