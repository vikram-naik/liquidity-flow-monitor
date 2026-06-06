import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config
from scripts.walk_forward import simulate_trades

def debug_symbol(sym):
    print(f"=== Debugging {sym} ===")
    engine = DivergenceEngine(sym, start_date=None, end_date=None)
    result = engine.run()
    
    # 1. Fresh signal instance
    signal_fresh = SignalFactory.get_signal("savgol_cts")
    entry_cfg = get_symbol_entry_config(sym)
    exit_cfg = get_symbol_exit_config(sym)
    
    trades_fresh = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg, signal_fresh)
    trades_fresh_2019 = [t for t in trades_fresh if t.entry_date >= "2019-01-01"]
    
    print(f"Fresh signal: {len(trades_fresh_2019)} trades, avg P&L = {np.mean([t.pnl_pct for t in trades_fresh_2019]):.4f}%")
    for t in trades_fresh_2019:
        print(f"  Entry: {t.entry_date} @ {t.entry_price:.2f} | Exit: {t.exit_date} @ {t.exit_price:.2f} | P&L: {t.pnl_pct:.2f}% | MAE: {t.mae_pct:.2f}% | Reason: {t.exit_reason}")

debug_symbol("KOTAKBANK")
debug_symbol("TATACONSUM")
