import sys
import numpy as np
import pandas as pd
from pathlib import Path
import copy

sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scratch.train_symbol_weights import label_candidate_bars_sim, train_bayesian_model

FALLBACKS = ["HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    for sym in FALLBACKS:
        print(f"\n==================== SWEEPING {sym} ====================")
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            candidates, labels = label_candidate_bars_sim(sym, ledger, signal, exit_cfg)
            feature_bins, feature_weights = train_bayesian_model(ledger, candidates, labels)
            
            cfg = SavgolCTSEntryConfig()
            cfg.cooldown_enabled = False
            cfg.cdvl_cts.enabled = False
            cfg.universal_cross.enabled = False
            cfg.trend_pullback_enabled = False
            cfg.flow_momentum.enabled = False
            cfg.coherent_pullback.enabled = False
            cfg.anchor_shock_pullback.enabled = False
            cfg.springboard.enabled = False
            cfg.oversold_decel.enabled = False
            
            cfg.custom_bayesian.enabled = True
            cfg.custom_bayesian.feature_bins = feature_bins
            cfg.custom_bayesian.feature_weights = feature_weights
            
            for th in np.arange(-4.0, 8.01, 0.2):
                cfg.custom_bayesian.score_threshold = float(th)
                trades = simulate_trades(sym, ledger, cfg, exit_cfg, signal)
                trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
                if trades_2019:
                    pnls = [t.pnl_pct for t in trades_2019]
                    avg_pnl = np.mean(pnls)
                    sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
                    print(f"Threshold: {th:5.1f} | Trades: {len(trades_2019):3d} | Avg P&L: {avg_pnl:6.2f}% | SL Hits: {sl_hits}")
        except Exception as e:
            print(f"Error sweeping {sym}: {e}")

if __name__ == "__main__":
    main()
