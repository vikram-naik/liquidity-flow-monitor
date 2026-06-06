import sys
import numpy as np
import copy
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scratch.train_symbol_weights_parallel import label_candidate_bars_sim, train_bayesian_model, evaluate_config

def main():
    sym = "KOTAKBANK"
    engine = DivergenceEngine(sym, start_date=None, end_date=None)
    res = engine.run()
    ledger = res.ledger
    
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    candidates, labels = label_candidate_bars_sim(sym, ledger, signal, exit_cfg)
    feature_bins, feature_weights = train_bayesian_model(ledger, candidates, labels, num_bins=3)
    
    passed, threshold, trades, avg_pnl, sl_hits = evaluate_config(
        sym, ledger, feature_bins, feature_weights, signal, exit_cfg
    )
    
    print(f"Training search returned: passed={passed}, threshold={threshold}, trades={trades}, avg_pnl={avg_pnl:.4f}%, sl_hits={sl_hits}")
    
    # Let's run simulation with threshold = 3.8
    test_cfg = SavgolCTSEntryConfig()
    test_cfg.cooldown_enabled = False
    test_cfg.cdvl_cts.enabled = False
    test_cfg.universal_cross.enabled = False
    test_cfg.trend_pullback_enabled = False
    test_cfg.flow_momentum.enabled = False
    test_cfg.coherent_pullback.enabled = False
    test_cfg.anchor_shock_pullback.enabled = False
    test_cfg.springboard.enabled = False
    test_cfg.oversold_decel.enabled = False
    test_cfg.custom_bayesian.enabled = True
    test_cfg.custom_bayesian.feature_bins = feature_bins
    test_cfg.custom_bayesian.feature_weights = feature_weights
    test_cfg.custom_bayesian.score_threshold = 3.8
    
    trades_sim = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
    trades_2019 = [t for t in trades_sim if t.entry_date >= "2019-01-01"]
    print(f"\nSimulation with freshly trained params at threshold 3.8: {len(trades_2019)} trades, avg P&L = {np.mean([t.pnl_pct for t in trades_2019]):.4f}%")
    for t in trades_2019:
         print(f"  Entry: {t.entry_date} @ {t.entry_price:.2f} | Exit: {t.exit_date} @ {t.exit_price:.2f} | P&L: {t.pnl_pct:.2f}% | MAE: {t.mae_pct:.2f}% | Reason: {t.exit_reason}")

if __name__ == "__main__":
    main()
