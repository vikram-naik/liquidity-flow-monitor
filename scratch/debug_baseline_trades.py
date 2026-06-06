import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSExitConfig
from scratch.optimize_springboard_bayesian import simulate_chronological_trades, check_relaxed_baseline

def main():
    sym = "ASIANPAINT"
    engine = DivergenceEngine(sym)
    res = engine.run()
    records = res.ledger.to_dict('records')
    
    exit_cfg = SavgolCTSExitConfig()
    exit_cfg.springboard.enabled = True
    exit_cfg.springboard.prt_st_cross_enabled = False
    exit_cfg.springboard.hard_stop_enabled = False
    exit_cfg.springboard.max_hold_bars = 50
    exit_cfg.springboard.time_decay_enabled = True
    
    signal = SignalFactory.get_signal("savgol_cts")
    
    trades = simulate_chronological_trades(sym, records, exit_cfg, signal, check_relaxed_baseline)
    
    print(f"Total trades for {sym}: {len(trades)}")
    for t in trades[:20]:
        print(f"Entry: {t.entry_date} (idx {t.entry_idx}) @ {t.entry_price:.2f} | Exit: {t.exit_date} @ {t.exit_price:.2f} | PnL: {t.pnl_pct:+.2f}% | Duration: {t.duration} bars | Reason: {t.exit_reason}")

if __name__ == "__main__":
    main()
